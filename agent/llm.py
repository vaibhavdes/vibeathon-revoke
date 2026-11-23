"""Chat-completions client that keeps working on free tiers.

Free tiers limit tokens per minute per model (Groq: 8K). Instead of waiting,
the client moves to the next (provider, model) pair on a 429 and comes back
when the limit resets. Any OpenAI-compatible endpoint works.

When every model is out of quota the client does not hang: it raises
LimitReached with the time the first model frees up, and remembers daily
limits in runtime/llm_limits.json so the next question fails fast too.

Configure in .env (only the keys you have; Groq alone is enough):
    GROQ_API_KEY, GROQ_API_KEY_2 ...         free; each extra key (another account) adds its own quota
    SARVAM_API_KEY                           Sarvam Token Factory, pay per token (cheap), no daily cap
    CEREBRAS_API_KEY, GEMINI_API_KEY         optional, free
    ANTHROPIC_API_KEY                        optional, paid; add "anthropic" to LLM_PROVIDERS
    LLM_PROVIDERS=groq,sarvam,cerebras,gemini   order to try
    GROQ_MODELS=openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b
    MAX_WAIT_SECONDS=90                      longest wait for a per-minute limit before giving up
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import openai

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY",
             "models": "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"},
    "sarvam": {"base_url": "https://api.sarvam.ai/v2", "key": "SARVAM_API_KEY",
               "models": "sarvam-105b,deepseekv4.1-flash-1017,glm5.3-flash"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key": "CEREBRAS_API_KEY",
                 "models": "gpt-oss-120b"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key": "GEMINI_API_KEY",
               "models": "gemini-2.5-flash"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1/", "key": "ANTHROPIC_API_KEY",
                  "models": "claude-sonnet-5-5"},
}


LIMITS_FILE = Path(__file__).resolve().parent.parent / "runtime" / "llm_limits.json"


class LLMUnavailable(RuntimeError):
    pass


class LimitReached(LLMUnavailable):
    """Every configured model is out of free quota. `until` is when the first frees up."""

    def __init__(self, until: float, daily: bool):
        self.until, self.daily = until, daily
        mins = max(1, round((until - time.time()) / 60))
        when = time.strftime("%H:%M", time.localtime(until))
        kind = "daily token limit" if daily else "per-minute limit"
        super().__init__(f"Free model limit reached ({kind} on every configured model). "
                         f"The first model is available again in about {mins} min, at {when}. "
                         f"Please try again then.")


@dataclass
class Target:
    provider: str
    model: str
    client: openai.OpenAI
    cool_until: float = 0.0
    daily: bool = False          # cooling down because of a daily limit

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class Reply:
    content: str
    tool_calls: list            # openai ChatCompletionMessageToolCall objects
    model: str
    usage: dict = field(default_factory=dict)
    raw_message: dict = field(default_factory=dict)
    finish_reason: str = ""


def _retry_after(exc: openai.APIStatusError, default: float) -> float:
    """Seconds until the limit resets: the retry-after header, else Groq's
    "Please try again in 7m12.5s" in the message, else the default."""
    try:
        value = exc.response.headers.get("retry-after")
        if value:
            return min(max(float(value), 1.0), 86400.0)
    except (TypeError, ValueError, AttributeError):
        pass
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", str(exc))
    if m and any(m.groups()):
        h, mi, se = (float(g) if g else 0.0 for g in m.groups())
        return max(h * 3600 + mi * 60 + se, 1.0)
    return default


def _keys(var: str) -> list[str]:
    """VAR, then VAR_2 ... VAR_5, skipping empty and repeated values."""
    out = []
    for name in [var] + [f"{var}_{i}" for i in range(2, 6)]:
        value = (os.getenv(name) or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _load_limits() -> dict:
    try:
        return json.loads(LIMITS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_limit(label: str, until: float) -> None:
    data = {k: v for k, v in _load_limits().items() if v > time.time()}
    data[label] = until
    try:
        LIMITS_FILE.parent.mkdir(exist_ok=True)
        LIMITS_FILE.write_text(json.dumps(data))
    except OSError:
        pass


def limit_status() -> dict:
    """Models currently known to be out of daily quota: {label: unix time it resets}."""
    return {k: v for k, v in _load_limits().items() if v > time.time()}


def _max_output(target: "Target") -> int:
    """Groq's free tier allows qwen only 1,000 output tokens per minute; a larger
    max_tokens is refused outright. Paid providers have no per-minute cap, and
    their reasoning models need room to think before they call a tool."""
    override = os.getenv("MAX_OUTPUT_TOKENS")
    if override:
        return int(override)
    if not target.provider.startswith("groq"):
        return 8000
    return 900 if "qwen" in target.model else 2000


class LLM:
    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda *_: None)
        self.targets: list[Target] = []
        order = os.getenv("LLM_PROVIDERS", "groq,sarvam,cerebras,gemini").split(",")
        for name in [o.strip() for o in order if o.strip()]:
            spec = PROVIDERS.get(name)
            if not spec:
                continue
            models = os.getenv(f"{name.upper()}_MODELS", spec["models"])
            # Several keys per provider (e.g. GROQ_API_KEY, GROQ_API_KEY_2): each has its own quota.
            for n, key in enumerate(_keys(spec["key"]), start=1):
                label = name if n == 1 else f"{name}#{n}"
                client = openai.OpenAI(api_key=key, base_url=spec["base_url"], max_retries=0, timeout=120)
                for model in [m.strip() for m in models.split(",") if m.strip()]:
                    self.targets.append(Target(label, model, client))
        known = limit_status()
        for t in self.targets:
            if t.label in known:
                t.cool_until, t.daily = known[t.label], True
        if not self.targets:
            raise LLMUnavailable("No model configured. Put GROQ_API_KEY (free) in .env; see .env.example.")

    def complete(self, messages: list[dict], tools: list[dict], max_wait: float = 300) -> Reply:
        bad_requests: dict[str, int] = {}
        prefer_wait = float(os.getenv("PREFER_WAIT_SECONDS", "20"))
        max_wait = min(max_wait, float(os.getenv("MAX_WAIT_SECONDS", "90")))
        deadline = time.time() + max_wait
        while True:
            now = time.time()
            best = self.targets[0]
            # The first model is the strongest. If it frees up soon, wait for it
            # rather than hand the decision to a weaker model.
            if best.cool_until > now and best.cool_until - now <= prefer_wait \
                    and best.cool_until != float("inf") and best.cool_until <= deadline:
                self.on_event({"type": "rate_limit_wait", "seconds": round(best.cool_until - now, 1)})
                time.sleep(best.cool_until - now)
                continue
            ready = [t for t in self.targets if t.cool_until <= now]
            if not ready:
                first = min(self.targets, key=lambda t: t.cool_until)
                wait = first.cool_until - now
                if now + wait > deadline:
                    raise LimitReached(first.cool_until, all(t.daily or t.cool_until == float("inf") for t in self.targets))
                self.on_event({"type": "rate_limit_wait", "seconds": round(wait, 1)})
                time.sleep(wait)
                continue
            target = ready[0]
            try:
                extra = {}
                if "gpt-oss" in target.model or target.model.startswith("sarvam"):
                    # Reasoning tokens count against the per-minute budget; keep them short.
                    extra["reasoning_effort"] = os.getenv("REASONING_EFFORT", "low")
                resp = target.client.chat.completions.create(
                    model=target.model, messages=messages, tools=tools or None,
                    tool_choice="auto" if tools else None, temperature=0.1,
                    max_tokens=_max_output(target), extra_body=extra or None)
            except openai.RateLimitError as exc:
                text = str(exc)
                daily = "per day" in text or "(TPD)" in text or "(RPD)" in text
                target.cool_until = time.time() + _retry_after(exc, 3600 if daily else 60 if "too large" in text else 30)
                target.daily = daily
                if daily:
                    _save_limit(target.label, target.cool_until)
                reason = "daily token limit reached" if daily else "rate limit"
                self.on_event({"type": "model_switch", "from": target.label, "reason": reason})
                continue
            except openai.BadRequestError as exc:
                # Groq answers 400 when a model emits a malformed tool call. Retry
                # once, then let the next model try.
                n = bad_requests.get(target.label, 0) + 1
                bad_requests[target.label] = n
                if n >= 2:
                    target.cool_until = time.time() + max(prefer_wait + 10, 30)
                    self.on_event({"type": "model_switch", "from": target.label,
                                   "reason": f"bad request: {str(exc)[:160]}"})
                continue
            except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                target.cool_until = time.time() + 15
                self.on_event({"type": "model_switch", "from": target.label, "reason": type(exc).__name__})
                continue
            except openai.AuthenticationError:
                target.cool_until = float("inf")
                self.on_event({"type": "model_switch", "from": target.label, "reason": "invalid API key"})
                if all(t.cool_until == float("inf") for t in self.targets):
                    raise LLMUnavailable("Every configured API key was rejected.")
                continue
            except openai.APIStatusError as exc:
                if exc.status_code != 413:
                    raise
                # Groq: "request too large" for this model's per-minute budget.
                target.cool_until = time.time() + 60
                self.on_event({"type": "model_switch", "from": target.label, "reason": "request too large"})
                continue
            target.daily = False
            msg = resp.choices[0].message
            usage = resp.usage.model_dump() if resp.usage else {}
            raw = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                raw["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.function.name,
                                                   "arguments": tc.function.arguments or "{}"}}
                                     for tc in msg.tool_calls]
            reply = Reply(content=msg.content or "", tool_calls=list(msg.tool_calls or []),
                          model=target.label, usage=usage, raw_message=raw,
                          finish_reason=resp.choices[0].finish_reason or "")
            self.on_event({"type": "llm_call", "model": target.label, "finish": reply.finish_reason,
                           "tokens": usage.get("total_tokens"), "tool_calls": len(reply.tool_calls)})
            if not reply.tool_calls and not reply.content.strip():
                # Out of tokens while reasoning, or an empty turn: let another model try.
                # Longer than PREFER_WAIT_SECONDS, so the client moves on instead of waiting for it.
                target.cool_until = time.time() + max(prefer_wait + 10, 30)
                self.on_event({"type": "model_switch", "from": target.label,
                               "reason": f"empty reply ({reply.finish_reason})"})
                if time.time() > deadline:
                    raise LLMUnavailable("The models returned empty replies. Try again.")
                continue
            return reply
