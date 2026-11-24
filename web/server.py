"""Revoke console: a browser UI over the same agent the CLI uses.

    python -m web.server            # http://127.0.0.1:8700

The two legacy systems must be running (python -m scripts.run_systems).
Events from the agent (tool calls, findings, proposals) are streamed to the
page with Server-Sent Events.
"""
from __future__ import annotations

import asyncio
import time
import html
import json
import os
import subprocess
import sys
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.config import EVIDENCE_DIR, REVIEW_DATE, ROOT, load_exposure, systems
from agent.hub import McpHub
from agent.llm import LimitReached, LLMUnavailable, limit_status
from agent.revoke import ReviewAgent
from scripts import live_update

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bus.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Revoke console", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Broadcaster:
    """Send events to open pages. Each page (browser tab) has a client id; an event for a client goes
    only to that tab, so two people on a shared instance never see each other's runs. Thread-safe."""

    def __init__(self):
        self.queues: dict[asyncio.Queue, str] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: dict, client: str | None = None) -> None:
        if self.loop is None:
            return
        for q, owner in list(self.queues.items()):
            if client is None or owner == client:
                self.loop.call_soon_threadsafe(q.put_nowait, event)


bus = Broadcaster()
state = {"agent": None, "busy": False, "asker": None, "replaying": set()}


def get_agent() -> ReviewAgent:
    if state["agent"] is None:
        # A live question's events go to the tab that asked it (all tabs if it gave no id).
        state["agent"] = ReviewAgent(on_event=lambda event: bus.publish(event, state["asker"]))
    return state["agent"]


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text()


def _health(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


@app.get("/api/state")
async def api_state():
    exposure = load_exposure()["systems"]
    out_systems = []
    tools = []
    try:
        async with McpHub() as hub:
            tools = [{"name": t.qualified, "system": t.system, "access": t.access, "endpoint": t.endpoint,
                      "description": t.description} for t in hub.tools.values()]
    except Exception as exc:  # systems down or servers not built
        tools = []
        tools_error = str(exc)
    else:
        tools_error = ""
    for key, spec in systems().items():
        raw = exposure[key]
        out_systems.append({
            "key": key, "url": spec.base_url, "up": await asyncio.to_thread(_health, spec.base_url),
            "server_file": str(spec.server_file().relative_to(ROOT)) if spec.server_file() else None,
            "withheld": raw["withheld"], "live_demo": raw.get("live_demo", []),
            "expose": raw["expose"],
        })
    agent = state["agent"] or _try_agent()
    return {
        "review_date": REVIEW_DATE,
        "can_ask": agent is not None,
        "hosted": os.getenv("REVOKE_HOSTED") == "1",
        "systems": out_systems,
        "tools": tools,
        "tools_error": tools_error,
        "live_update_applied": live_update.is_applied(),
        "busy": state["busy"],
        "pending": [p.to_dict() for p in agent.pending()] if agent else [],
        "model_limits": model_limits(agent),
    }


def _try_agent():
    try:
        return get_agent()
    except LLMUnavailable:
        return None


def model_limits(agent) -> dict:
    """Which models are out of free quota, and when each is back (unix time)."""
    out = dict(limit_status())
    if agent:
        now = time.time()
        for t in agent.llm.targets:
            if t.daily and t.cool_until > now:
                out[t.label] = t.cool_until
    configured = [t.label for t in agent.llm.targets] if agent else []
    return {"limited": out, "configured": configured}


@app.get("/api/events")
async def api_events(request: Request, client: str = ""):
    queue: asyncio.Queue = asyncio.Queue()
    bus.queues[queue] = client

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.queues.pop(queue, None)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Ask(BaseModel):
    question: str


@app.post("/api/ask")
async def api_ask(body: Ask, client: str = ""):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question.")
    if state["busy"]:
        raise HTTPException(409, "Revoke is still working on the previous question.")
    try:
        agent = get_agent()
    except LLMUnavailable as exc:
        raise HTTPException(503, str(exc))
    now = time.time()
    if all(t.cool_until > now and t.daily for t in agent.llm.targets):
        first = min(t.cool_until for t in agent.llm.targets)
        raise HTTPException(429, str(LimitReached(first, True)))
    state["busy"] = True
    state["asker"] = client or None

    async def run():
        try:
            await agent.ask(question)
        except LimitReached as exc:
            bus.publish({"type": "limit", "message": str(exc), "until": exc.until}, state["asker"])
        except Exception as exc:
            bus.publish({"type": "error", "message": f"{type(exc).__name__}: {exc}"}, state["asker"])
        finally:
            state["busy"] = False
            bus.publish({"type": "idle"}, state["asker"])

    asyncio.create_task(run())
    return {"ok": True}


class Decision(BaseModel):
    ids: list[str]
    approve: bool
    approver: str = "console user"


@app.post("/api/decide")
async def api_decide(body: Decision):
    agent = state["agent"]
    if agent is None:
        raise HTTPException(400, "Nothing to decide.")
    done = await agent.decide(body.ids, body.approve, body.approver.strip() or "console user")
    return {"ok": True, "decided": [p.to_dict() for p in done]}


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@app.post("/api/live-update/{action}")
async def api_live_update(action: str):
    """Apply or remove the Ledgerly activity endpoint, then rebuild with Synapse."""
    if state["busy"]:
        raise HTTPException(409, "Wait for the current question to finish.")
    if action == "apply":
        live_update.apply()
        code, out = await asyncio.to_thread(_run, [sys.executable, "-m", "scripts.build_mcp", "--system",
                                                   "ledgerly", "--discover", "--include-live"])
    elif action == "remove":
        code, out = await asyncio.to_thread(_run, [sys.executable, "-m", "scripts.reset_demo"])
    else:
        raise HTTPException(404, "Use apply or remove.")
    await asyncio.sleep(1.0)   # let Ledgerly's auto-reload pick up the change
    return {"ok": code == 0, "output": out, "applied": live_update.is_applied()}


@app.post("/api/connectors/rebuild")
async def api_connectors_rebuild():
    """Regenerate both MCP servers from the systems' code with Synapse."""
    if state["busy"]:
        raise HTTPException(409, "Wait for the current question to finish.")
    cmd = [sys.executable, "-m", "scripts.build_mcp", "--discover"]
    if live_update.is_applied():
        cmd.append("--include-live")
    code, out = await asyncio.to_thread(_run, cmd)
    return {"ok": code == 0, "output": out}


@app.post("/api/reset")
async def api_reset():
    if state["busy"]:
        raise HTTPException(409, "Wait for the current question to finish.")
    code, out = await asyncio.to_thread(_run, [sys.executable, "-m", "scripts.reset_demo"])
    state["agent"] = None
    return {"ok": code == 0, "output": out}


@app.post("/api/new-conversation")
async def api_new_conversation():
    state["agent"] = None
    return {"ok": True}


SAMPLES_DIR = ROOT / "evidence" / "samples"
EVIDENCE_FILES = ("report.md", "certifications.md", "findings.csv", "trace.jsonl", "actions.json",
                  "events.jsonl", "manifest.json")


def _run_folder(run_id: str) -> Path | None:
    """A run's evidence folder: this machine's runs first, then the committed samples."""
    for base in (EVIDENCE_DIR, SAMPLES_DIR):
        folder = (base / run_id).resolve()
        if base.resolve() in folder.parents and (folder / "report.md").is_file():
            return folder
    return None


def recordings() -> list[dict]:
    """Runs that can be replayed in the console without a model: committed samples, then local runs."""
    out = []
    for base, source in ((SAMPLES_DIR, "sample"), (EVIDENCE_DIR, "local")):
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir(), reverse=True):
            events = folder / "events.jsonl"
            if not events.is_file():
                continue
            first = json.loads(events.read_text().splitlines()[0])["event"]
            out.append({"id": folder.name, "question": first.get("question", ""), "source": source})
    return out[:30]


@app.get("/api/recordings")
def api_recordings():
    return {"recordings": recordings()}


@app.post("/api/replay/{run_id}")
async def api_replay(run_id: str, speed: float = 1.0, client: str = ""):
    """Play a recorded run to the tab that asked for it. Replays need no model and don't block other tabs."""
    folder = _run_folder(run_id)
    if not folder or not (folder / "events.jsonl").is_file():
        raise HTTPException(404, "No recording for that run.")
    key = client or "_all"
    if key in state["replaying"] or (not client and state["busy"]):
        raise HTTPException(409, "A run is still playing in this tab.")
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
    state["replaying"].add(key)

    async def play():
        try:
            last = 0.0
            for item in events:
                # Model calls took seconds to minutes; replay keeps the order and compresses the waits.
                await asyncio.sleep(min(max(item["t"] - last, 0.0), 1.2) * 0.6 / max(speed, 0.1))
                last = item["t"]
                event = dict(item["event"], replay=True)
                if event.get("type") in ("rate_limit_wait", "model_switch", "llm_call"):
                    continue
                bus.publish(event, client or None)
        finally:
            state["replaying"].discard(key)
            bus.publish({"type": "idle"}, client or None)

    asyncio.create_task(play())
    return {"ok": True, "events": len(events)}


@app.get("/evidence/{run_id}/{name}")
def evidence_file(run_id: str, name: str):
    folder = _run_folder(run_id)
    if not folder or name not in EVIDENCE_FILES or not (folder / name).is_file():
        raise HTTPException(404, "No such evidence file.")
    return FileResponse(folder / name)


@app.get("/evidence/{run_id}", response_class=HTMLResponse)
def evidence_page(run_id: str):
    folder = _run_folder(run_id)
    if not folder:
        raise HTTPException(404, "No such run.")
    report = folder / "report.md"
    md = json.dumps(report.read_text())
    files = "".join(f'<a href="/evidence/{html.escape(run_id)}/{n}" download>{n}</a>'
                    for n in EVIDENCE_FILES if (folder / n).is_file())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence {html.escape(run_id)} · Revoke</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="evidence-page"><header class="ev-head"><a href="/">← Revoke console</a>
<div class="ev-files">Download: {files}</div></header>
<main id="report" class="md"></main>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script>const md={md};
document.getElementById('report').innerHTML = window.marked ? marked.parse(md) : '<pre>'+md.replace(/</g,'&lt;')+'</pre>';
</script></body></html>"""


def main() -> None:
    import uvicorn
    uvicorn.run("web.server:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8700")),
                log_level="warning")


if __name__ == "__main__":
    main()
