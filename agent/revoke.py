"""Revoke: an access-review agent for Ledgerly, joined to PeopleHub.

The agent only sees the two systems through the MCP servers Synapse
generated. Reads run immediately; changes are queued for a human
(agent/policy.py). Every question produces an evidence pack (agent/evidence.py).

    python -m agent.revoke "Who has left but still has Ledgerly access?"
    python -m agent.revoke                 # interactive session
    python -m agent.revoke --tools         # list the tools the agent can use
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from agent.config import APP, APP_KEY, HR, REVIEW_DATE, ROOT
from agent.evidence import CATEGORY_TITLES, Run, facts_text, now_iso
from agent.grounding import Facts
from agent.hub import McpHub
from agent.llm import LLM, LLMUnavailable
from agent.policy import Proposal, review_write

MAX_STEPS = 16
ACTION_WORDS = re.compile(r"\b(disable|deactivate|revoke|remove|offboard|clean ?up|fix)\b", re.I)
MAX_RESULT_CHARS = 14_000
CATEGORIES = ["leaver_active_access", "post_termination_activity", "upcoming_leaver", "unknown_identity",
              "service_account_owner", "mover_retained_privilege", "sod_conflict", "dormant_account",
              "shared_account"]

# Rules code can apply on its own from the joined data (movers and SoD have their own code views).
SIMPLE_RULES = {"leaver_active_access", "upcoming_leaver", "unknown_identity", "service_account_owner",
                "dormant_account", "shared_account"}

FINDINGS_TOOL = {"type": "function", "function": {
    "name": "submit_findings",
    "description": "Record the findings of this review for the audit evidence pack. Call once, before answering.",
    "parameters": {"type": "object", "required": ["findings"], "properties": {"findings": {
        "type": "array", "items": {"type": "object",
                                   "required": ["category", "username", "severity", "evidence"],
                                   "properties": {
                                       "category": {"type": "string", "enum": CATEGORIES},
                                       "username": {"type": "string", "description": "Ledgerly username"},
                                       "person": {"type": "string"},
                                       "role": {"type": "string", "description": "for mover findings"},
                                       "rule_id": {"type": "string", "description": "for sod findings"},
                                       "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                                       "evidence": {"type": "string", "description": "facts with dates and source system"},
                                       "recommended_action": {"type": "string"},
                                       "certifier": {"type": "string"}}}}}}}}

PROPOSE_TOOL = {"type": "function", "function": {
    "name": "propose_changes",
    "description": ("File change requests for a human to approve. You hold no write access: each approved change "
                    "is then made by Ledgerly's disable_account or revoke_role tool with exactly these arguments. "
                    "Put every change in ONE call."),
    "parameters": {"type": "object", "required": ["changes"], "properties": {"changes": {
        "type": "array", "items": {"type": "object", "required": ["action", "username", "reason"], "properties": {
            "action": {"type": "string", "enum": ["disable_account", "revoke_role"]},
            "username": {"type": "string"},
            "role": {"type": "string", "description": "only for revoke_role"},
            "reason": {"type": "string", "description": "cite the finding, e.g. 'Leaver: terminated 2026-11-03 per PeopleHub'"}}}}}}}}

ACTIVITY_RULE = (f"- **post_termination_activity**: for every leaver, read {APP}__get_account_activity. Any event "
                 "dated after the termination_date (ignore account_disabled) means the access was USED after the "
                 "person left. Severity critical. Quote the timestamp, action and amount. Recommend disabling now "
                 "and opening a security incident.")


def system_prompt(has_activity: bool) -> str:
    template = (ROOT / "agent" / "system_prompt.md").read_text()
    since = (date.fromisoformat(REVIEW_DATE) - timedelta(days=180)).isoformat()
    return template.format(review_date=REVIEW_DATE, mover_since=since, app=APP, hr=HR,
                           activity_rule=ACTIVITY_RULE if has_activity else "")


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class ReviewAgent:
    def __init__(self, on_event=None):
        self._sink = on_event or (lambda e: None)
        self._current: Run | None = None
        self.llm = LLM(on_event=self.on_event)
        self.memory: list[dict] = []
        self.proposals: dict[str, Proposal] = {}
        self.proposal_run: dict[str, Run] = {}

    def on_event(self, event: dict, run: "Run | None" = None) -> None:
        """Send an event to the console and record it with the run, so the run can be replayed."""
        run = run or self._current
        if run is not None:
            run.record(event)
        self._sink(event)

    async def ask(self, question: str) -> Run:
        run = Run(question=question)
        self._current = run
        self.on_event({"type": "start", "run_id": run.run_id, "question": question})
        async with McpHub() as hub:
            run.tools_available = sorted(hub.tools)
            has_activity = f"{APP}__get_account_activity" in hub.tools
            self.on_event({"type": "tools", "tools": run.tools_available})
            # The model gets read tools only. Changes go through propose_changes and a human.
            tools = [t.openai_schema() for t in hub.tools.values() if not hub.is_write(t.qualified)]
            tools += [FINDINGS_TOOL, PROPOSE_TOOL]
            messages = [{"role": "system", "content": system_prompt(has_activity)},
                        *self.memory[-6:], {"role": "user", "content": question}]
            facts = Facts()
            accounts_msg = None
            nudged = action_nudged = submitted = coverage_nudged = False
            wants_change = run.wants_change = bool(ACTION_WORDS.search(question))
            for _ in range(MAX_STEPS):
                reply = await asyncio.to_thread(self.llm.complete, messages, tools)
                run.models.append(reply.model)
                messages.append(reply.raw_message)
                if not reply.tool_calls:
                    if not submitted and not run.proposals and not nudged:
                        nudged = True
                        messages.append({"role": "user", "content":
                                         "Before answering, call submit_findings with the findings for this "
                                         "question (an empty list if there are none), then answer."})
                        continue
                    if wants_change and not run.proposals and not run.blocked and not action_nudged:
                        action_nudged = True
                        messages.append({"role": "user", "content":
                                         "I asked for changes, but you have not called propose_changes, so nothing "
                                         "is queued. Call it now with every change the policy allows. If no change "
                                         "is allowed, say so plainly instead."})
                        continue
                    missing = _named_without_finding(reply.content, facts, run)
                    if missing and not coverage_nudged:
                        coverage_nudged = True
                        messages.append({"role": "user", "content":
                                         f"Your answer names {', '.join(missing)}, but no finding was recorded "
                                         "for them. Call submit_findings for each one that is a finding (one per "
                                         "role), or leave them out of the answer. Then give the answer again."})
                        continue
                    run.answer = reply.content.strip()
                    break
                for tc in reply.tool_calls:
                    name = tc.function.name
                    submitted = submitted or name == "submit_findings"
                    try:
                        args = json.loads(tc.function.arguments or "{}") or {}
                    except ValueError:
                        args = {}
                    content = await self._handle(hub, run, name, args, facts)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                    if name == f"{APP}__list_active_accounts":
                        accounts_msg = messages[-1]
                    if name == f"{HR}__lookup_employees_by_email" and '"joined"' in content:
                        _compact_history(messages, accounts_msg, tc.id, len(args.get("emails") or []))
            else:
                run.answer = ("I stopped after the maximum number of steps. Partial findings are in the evidence "
                              "pack; ask a narrower question.")
        self.memory += [{"role": "user", "content": question},
                        {"role": "assistant", "content": _short(run.answer, 3000)}]
        self.on_event({"type": "final", "run_id": run.run_id, "answer": run.answer,
                       "findings": run.findings, "proposals": [p.to_dict() for p in run.proposals],
                       "evidence": os.path.relpath(run.folder, ROOT)})
        self._current = None
        run.write()
        return run

    async def _handle(self, hub: McpHub, run: Run, name: str, args: dict, facts: Facts) -> str:
        if name == "submit_findings":
            findings = _split_multi(f for f in (args.get("findings") or []) if isinstance(f, dict))
            kept, rejected = [], []
            seen = {(f["category"], f["username"], f.get("role"), f.get("rule_id")) for f in run.findings}
            for f in findings:
                f["username"] = str(f.get("username", "")).strip().lower()
                facts.ground(f)
                why = facts.check(f, REVIEW_DATE)
                key = (f.get("category"), f["username"], f.get("role"), f.get("rule_id"))
                if why:
                    rejected.append({**f, "rejected_because": why})
                elif key not in seen:
                    seen.add(key)
                    kept.append(f)
            run.findings.extend(kept)
            run.rejected.extend(rejected)
            # Completeness: for each simple rule the model applied, code lists every account that
            # matches it in the data. Anything not yet recorded is pointed out once.
            recorded = {(f["category"], f["username"]) for f in run.findings}
            missed = {}
            for cat in sorted({f.get("category") for f in kept + rejected} & SIMPLE_RULES):
                if cat in run.completeness_asked or not facts.accounts or not facts.people_by_email:
                    continue
                run.completeness_asked.add(cat)
                gap = sorted(u for u in facts.candidates(cat) if (cat, u) not in recorded)
                if gap:
                    missed[cat] = gap
            self.on_event({"type": "findings", "findings": kept, "rejected": rejected})
            reply = {"ok": True, "recorded": len(kept),
                     # Severity and certifier are set by policy code; the answer must use these.
                     "as_recorded": [{"username": f["username"], "category": f.get("category"),
                                      **({"role": f["role"]} if f.get("role") else {}),
                                      "severity": f.get("severity"), "certifier": f.get("certifier")}
                                     for f in kept],
                     "note_severity": "Use the severity and certifier in as_recorded in your answer."}
            if rejected:
                reply["rejected"] = [{"username": r["username"], "category": r.get("category"),
                                      "why": r["rejected_because"]} for r in rejected]
                reply["note"] = "Rejected findings do not hold against the data. Do not report them."
            if missed:
                reply["possibly_missed"] = missed
                reply["note_missed"] = ("These accounts match the same rule in the data you read but have no finding. "
                                        "Check each and call submit_findings again for those that hold.")
                self.on_event({"type": "completeness", "missed": missed})
            return json.dumps(reply)
        if name == "propose_changes":
            return await self._propose(hub, run, args.get("changes") or [])
        tool = hub.tools.get(name)
        if tool is None:
            return json.dumps({"ok": False, "error": f"No tool named {name}. Use one of: {', '.join(hub.tools)}"})
        if hub.is_write(name):
            return json.dumps({"ok": False, "error": "You have no write access. Use propose_changes."})
        if tool.name == "lookup_employees_by_email" and facts.accounts:
            # Make the join complete: every listed human or contractor account is looked up,
            # even if the model left one out.
            given = args.get("emails") or []
            if isinstance(given, str):
                given = given.split(",")
            wanted = {e.strip().lower(): e.strip() for e in given if e and e.strip()}
            for acc in facts.accounts.values():
                if acc.get("type") != "service" and acc.get("email") and acc.get("status") == "active":
                    wanted.setdefault(acc["email"].lower(), acc["email"])
            args = {**args, "emails": list(wanted.values())}
        self.on_event({"type": "tool_call", "system": tool.system, "tool": name, "arguments": args})
        started = time.monotonic()
        result, is_error = await hub.call(name, args)
        ms = int((time.monotonic() - started) * 1000)
        run.log_call(tool.system, name, args, result, is_error, ms)
        facts.absorb(name, args, result)
        self.on_event({"type": "tool_result", "system": tool.system, "tool": name, "ok": not is_error, "ms": ms,
                       "summary": _summarise(result)})
        if tool.name == "list_job_changes_since" and not is_error:
            # Movers need the accounts and the role catalog too; read them now if the model has not.
            if not facts.accounts:
                await self._code_read(hub, run, facts, f"{APP}__list_active_accounts", {})
            if not facts.catalog:
                await self._code_read(hub, run, facts, f"{APP}__get_role_catalog", {})
            result = {**result, "mover_review": facts.mover_review(),
                      "note": "mover_review was computed from the job changes, the account list and the role "
                              "catalog. A role not typical for the new title is a retained privilege: one "
                              "finding per role."}
        if tool.name == "list_sod_rules" and not is_error and facts.accounts:
            result = {"ok": True, "rules": facts.sod_holders(),
                      "note": "active_accounts_holding_both was computed from the account list you read"}
        if tool.name == "lookup_employees_by_email" and not is_error and facts.accounts:
            result = facts.joined_view(result.get("unknown_emails", []))
            self.on_event({"type": "join", "rows": len(result["accounts"])})
        text = json.dumps(result, separators=(",", ":"), default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + '..."(truncated)"'
        return text

    async def _code_read(self, hub: McpHub, run: Run, facts: Facts, name: str, args: dict) -> None:
        """A read made by Revoke's code (not the model), shown in the trace like any other call."""
        tool = hub.tools.get(name)
        if tool is None:
            return
        self.on_event({"type": "tool_call", "system": tool.system, "tool": name, "arguments": args, "by": "code"})
        started = time.monotonic()
        result, is_error = await hub.call(name, args)
        ms = int((time.monotonic() - started) * 1000)
        run.log_call(tool.system, name, args, result, is_error, ms)
        facts.absorb(name, args, result)
        self.on_event({"type": "tool_result", "system": tool.system, "tool": name, "ok": not is_error, "ms": ms,
                       "summary": _summarise(result)})

    async def _propose(self, hub: McpHub, run: Run, changes: list) -> str:
        if not run.wants_change:
            return json.dumps({"ok": False, "queued_for_approval": 0, "message":
                               "The user did not ask for changes. Recommend them in your answer; the user can "
                               "ask you to queue them."})
        listing, _ = await hub.call(f"{APP}__list_active_accounts")
        accounts = {a["username"]: a for a in listing.get("accounts", [])}
        outcome = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            action = str(change.get("action", "")).strip()
            tool = f"{APP}__{action}"
            if action not in ("disable_account", "revoke_role") or tool not in hub.tools:
                outcome.append({"change": change, "queued": False, "message": "Unknown action."})
                continue
            args = {k: change[k] for k in ("username", "reason", "role") if change.get(k)}
            if action == "disable_account":
                args.pop("role", None)
            proposal, message = review_write(tool, args, accounts)
            if proposal is None:
                run.blocked.append({"tool": tool, "arguments": args, "message": message})
                self.on_event({"type": "blocked", "tool": tool, "arguments": args, "message": message})
                outcome.append({"username": args.get("username"), "queued": False, "message": message})
                continue
            run.proposals.append(proposal)
            self.proposals[proposal.id] = proposal
            self.proposal_run[proposal.id] = run
            self.on_event({"type": "proposal", "proposal": proposal.to_dict(), "message": message})
            outcome.append({"id": proposal.id, "queued": True, "change": proposal.summary})
        queued = sum(1 for o in outcome if o.get("queued"))
        return json.dumps({"ok": True, "queued_for_approval": queued, "results": outcome,
                           "note": "Nothing has been executed. Report exactly what was queued and what was blocked."})

    def pending(self) -> list[Proposal]:
        return [p for p in self.proposals.values() if p.status == "pending"]

    async def decide(self, ids: list[str], approve: bool, approver: str) -> list[Proposal]:
        """Run (or reject) queued changes exactly as proposed, then re-read each account."""
        chosen = [self.proposals[i] for i in ids if i in self.proposals and self.proposals[i].status == "pending"]
        if not chosen:
            return []
        touched_runs = {}
        async with McpHub() as hub:
            for p in chosen:
                p.decided_by, p.decided_at = approver, now_iso()
                run = self.proposal_run[p.id]
                touched_runs[run.run_id] = run
                if not approve:
                    p.status = "rejected"
                    self.on_event({"type": "decision", "proposal": p.to_dict()}, run=self.proposal_run.get(p.id))
                    continue
                started = time.monotonic()
                result, is_error = await hub.call(p.tool, p.arguments)
                run.log_call(hub.tools[p.tool].system, p.tool, p.arguments, result, is_error,
                             int((time.monotonic() - started) * 1000))
                p.result = result if isinstance(result, dict) else {"result": result}
                check, _ = await hub.call(f"{APP}__get_account", {"username": p.arguments["username"]})
                run.log_call(APP_KEY, f"{APP}__get_account", {"username": p.arguments["username"]},
                             check, False, 0)
                account = check.get("account", {}) if isinstance(check, dict) else {}
                if p.tool.endswith("disable_account"):
                    ok = account.get("status") == "disabled"
                    summary = f"status={account.get('status')} at {account.get('disabled_at', '?')}"
                else:
                    ok = p.arguments["role"] not in account.get("roles", [])
                    summary = f"roles now {account.get('roles')}"
                p.verification = {"ok": ok, "summary": ("✓ " if ok else "✗ ") + summary}
                p.status = "executed" if (not is_error and ok) else "failed"
                self.on_event({"type": "decision", "proposal": p.to_dict()}, run=self.proposal_run.get(p.id))
        for run in touched_runs.values():
            run.write()
        return chosen


def _named_without_finding(answer: str, facts: Facts, run: Run) -> list[str]:
    """Accounts the answer presents as findings (before its "not flagged" line) with no recorded finding."""
    body = re.split(r"(?i)checked and not flagged|not flagged", answer or "")[0]
    recorded = ({f["username"] for f in run.findings} | {p.arguments.get("username") for p in run.proposals}
                | {(b.get("arguments") or {}).get("username") for b in run.blocked})
    named = [u for u in facts.accounts if re.search(rf"(?<![\w.@]){re.escape(u)}(?![\w@])", body)]
    return sorted(u for u in named if u not in recorded)


def _split_multi(findings) -> list[dict]:
    """Policy is one finding per role (movers) and per rule (SoD). Models sometimes
    pack several into one, e.g. role "AP_CLERK, PAYMENT_APPROVER"; split them."""
    out = []
    for f in findings:
        key = "role" if f.get("category") == "mover_retained_privilege" else \
              "rule_id" if f.get("category") == "sod_conflict" else None
        values = [v.strip().upper() for v in re.split(r"[,/;+&]| and ", str(f.get(key) or "")) if v.strip()] \
            if key else []
        if len(values) > 1:
            out += [{**f, key: v} for v in values]
        else:
            if key and values:
                f[key] = values[0]
            out.append(f)
    return out


def _compact_history(messages: list[dict], accounts_msg: dict | None, call_id: str, n_emails: int) -> None:
    """The joined view repeats the account list, so drop the raw copy and the long
    email list from the conversation. Keeps every request under free-tier limits."""
    if accounts_msg is not None:
        accounts_msg["content"] = ('{"ok":true,"note":"account list superseded by the joined view returned by '
                                   f'{HR}__lookup_employees_by_email"}}')
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc["id"] == call_id:
                tc["function"]["arguments"] = json.dumps({"emails": [f"<{n_emails} account emails>"]})


def _summarise(result) -> str:
    if not isinstance(result, dict):
        return _short(result, 120)
    if result.get("ok") is False:
        return "error: " + _short(result.get("error", ""), 120)
    for key in ("accounts", "matches", "leavers", "job_changes", "members", "events", "rules", "reports"):
        if isinstance(result.get(key), list):
            extra = f", {len(result['unknown_emails'])} unknown" if key == "matches" else ""
            return f"{len(result[key])} {key}{extra}"
    if "roles" in result and isinstance(result["roles"], dict):
        return f"{len(result['roles'])} roles"
    if "account" in result:
        a = result["account"]
        return f"{a.get('username')}: {a.get('status')}, roles {a.get('roles')}"
    if "employee" in result:
        e = result["employee"]
        return f"{e.get('name')}: {e.get('status')}"
    return _short(json.dumps(result), 120)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def _print_event(e: dict) -> None:
    kind = e["type"]
    if kind == "tool_call":
        print(f"  → {e['tool']} {json.dumps(e['arguments'])[:120]}")
    elif kind == "tool_result":
        print(f"    {'✓' if e['ok'] else '✗'} {e['summary']} ({e['ms']} ms)")
    elif kind == "proposal":
        print(f"  ⏸ {e['message']}")
    elif kind == "blocked":
        print(f"  ⛔ {e['message']}")
    elif kind == "findings":
        print(f"  ✎ {len(e['findings'])} verified findings recorded")
        for r in e.get("rejected") or []:
            print(f"  ✗ rejected {r.get('category')} for {r.get('username')}: {r['rejected_because']}")
    elif kind == "completeness":
        print(f"  ? also matching the rule: {e['missed']}")
    elif kind == "model_switch":
        print(f"  ~ {e['from']} unavailable ({e['reason']}), switching model")
    elif kind == "llm_call" and os.getenv("REVOKE_DEBUG"):
        print(f"  · {e['model']} finish={e['finish']} tokens={e['tokens']} tool_calls={e['tool_calls']}")
    elif kind == "rate_limit_wait":
        print(f"  ~ all models rate-limited, waiting {e['seconds']} s")


def _print_findings(run: Run) -> None:
    if not run.findings:
        return
    print(f"\nFindings ({len(run.findings)}), facts verified against tool results:")
    for f in run._sorted_findings():
        title = CATEGORY_TITLES.get(f.get("category"), f.get("category"))
        extra = " ".join(x for x in (f.get("role"), f.get("rule_id")) if x)
        print(f"  [{f.get('severity', '?'):>8}] {f.get('username'):<20} {title}{' (' + extra + ')' if extra else ''}")
        print(f"             {facts_text(f)}")
        if f.get("certifier"):
            print(f"             certifier: {f['certifier']}  ·  action: {f.get('recommended_action', '')}")


async def _interactive(agent: ReviewAgent, first: str | None) -> None:
    question = first
    while True:
        if question is None:
            try:
                question = input("\nrevoke> ").strip()
            except EOFError:
                return
        if question in ("", "exit", "quit"):
            return
        try:
            run = await agent.ask(question)
        except LLMUnavailable as exc:
            print(f"\n{exc}")
            question = None
            continue
        _print_findings(run)
        print("\n" + run.answer)
        print(f"\nEvidence: {run.folder.relative_to(ROOT)}/report.md")
        pending = agent.pending()
        if pending:
            print("\nWaiting for your approval:")
            for p in pending:
                print(f"  {p.id}  {p.summary}  (reason: {p.arguments.get('reason')})")
            answer = input("Type 'approve' to run ALL of these exactly as listed, anything else to reject: ")
            done = await agent.decide([p.id for p in pending], answer.strip().lower() == "approve", "cli-user")
            for p in done:
                print(f"  {p.id} {p.status}: {p.summary} {p.verification.get('summary', '')}")
        question = None


async def _list_tools() -> None:
    async with McpHub() as hub:
        for t in hub.tools.values():
            print(f"{t.qualified:<40} {t.access:<5} {t.endpoint}")
        print(f"\n{len(hub.tools)} tools")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Revoke access-review agent")
    parser.add_argument("question", nargs="*", help="ask one question and exit")
    parser.add_argument("--tools", action="store_true", help="list the MCP tools and exit")
    args = parser.parse_args(argv)
    if args.tools:
        asyncio.run(_list_tools())
        return 0
    try:
        agent = ReviewAgent(on_event=_print_event)
    except LLMUnavailable as exc:
        print(exc)
        return 1
    question = " ".join(args.question) or None
    if question:
        try:
            run = asyncio.run(agent.ask(question))
        except LLMUnavailable as exc:
            print(f"\n{exc}")
            return 2
        _print_findings(run)
        print("\n" + run.answer)
        print(f"\nEvidence: {Path(run.folder).relative_to(ROOT)}/report.md")
        for p in agent.pending():
            print(f"Pending approval: {p.id} {p.summary}")
        return 0
    asyncio.run(_interactive(agent, None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
