"""Measure how reliably the agent answers, against the answer key.

Runs each demo question several times with the real model and the real
Synapse-generated MCP servers, and scores what the agent recorded:

  recall      share of the expected findings the agent reported
  precision   share of reported findings that are real (anywhere in the answer key)
  false flags accounts that must never be flagged (rehire, on leave, ...)
  changes     for the "fix it" question: queued changes vs the expected set

Both systems must be running. The live-update question applies the update,
rebuilds with `synapse build --discover`, and resets afterwards.

    python -m eval.agent_eval --runs 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

from agent.config import ROOT
from agent.revoke import ReviewAgent
from eval.expected import MUST_NOT_FLAG, compute
from scripts import build_mcp, live_update, reset_demo

SCENARIOS = [
    {"id": "Q1", "question": "Who has left the company but still has an active Ledgerly account, and what can they still do?",
     "target": {"leaver_active_access"}},
    {"id": "Q2", "question": "Who changed jobs in the last six months but kept Ledgerly privileges from their old job?",
     "target": {"mover_retained_privilege"}},
    {"id": "Q3", "question": "Who holds conflicting roles in Ledgerly, and which manager must certify each one?",
     "target": {"sod_conflict"}},
    {"id": "Q5", "question": "Which Ledgerly accounts are dormant or shared, so nobody is really using or accountable for them?",
     "target": {"dormant_account", "shared_account"}},
    {"id": "Q4", "question": "Did any leaver's Ledgerly account do anything after their last day?",
     "target": {"post_termination_activity"}, "live": True},
    {"id": "FIX", "question": "Disable the Ledgerly accounts of everyone who has left, and remove roles that movers kept from their old job.",
     "target": set(), "changes": True},
]


def finding_key(f: dict) -> tuple:
    cat = f.get("category", "")
    user = (f.get("username") or "").lower()
    if cat == "mover_retained_privilege":
        return (cat, user, str(f.get("role", "")).upper())
    if cat == "sod_conflict":
        return (cat, user, str(f.get("rule_id", "")).upper())
    return (cat, user)


def expected_changes(truth: set[tuple]) -> set[tuple]:
    out = {("disable_account", k[1], "") for k in truth if k[0] == "leaver_active_access"}
    out |= {("revoke_role", k[1], k[2]) for k in truth if k[0] == "mover_retained_privilege"}
    return out


async def run_once(scn: dict) -> dict:
    agent = ReviewAgent()
    started = time.time()
    try:
        run = await agent.ask(scn["question"])
    except Exception as exc:  # count a crash as a failed run, keep going
        return {"id": scn["id"], "error": f"{type(exc).__name__}: {exc}", "seconds": round(time.time() - started)}
    truth = compute(include_activity=bool(scn.get("live")))
    got = {finding_key(f) for f in run.findings}
    target = {k for k in truth if k[0] in scn["target"]}
    found = got & target
    result = {
        "id": scn["id"], "seconds": round(time.time() - started), "run_id": run.run_id,
        "models": sorted(set(run.models)), "tool_calls": len(run.trace),
        "expected": len(target), "found": len(found),
        "missed": sorted("/".join(k[1:]) for k in target - got),
        "reported": len(got), "correct": len(got & truth),
        "wrong": sorted("/".join(k) for k in got - truth),
        "false_flags": sorted({k[1] for k in got} & MUST_NOT_FLAG),
        "unverified": sum(1 for f in run.findings if not (f.get("facts") or {}).get("verified")),
    }
    if scn.get("changes"):
        want = expected_changes(compute())
        queued = {(p.tool.split("__")[1], p.arguments["username"], p.arguments.get("role", "")) for p in run.proposals}
        result.update({"changes_expected": len(want), "changes_correct": len(queued & want),
                       "changes_wrong": sorted("/".join(c) for c in queued - want),
                       "changes_missed": sorted("/".join(c) for c in want - queued)})
        # leave the seed data untouched: nothing was approved, so nothing ran
    return result


async def main_async(runs: int, only: list[str]) -> int:
    results = []
    for scn in SCENARIOS:
        if only and scn["id"] not in only:
            continue
        if scn.get("live"):
            live_update.apply()
            if build_mcp.main(["--system", "ledgerly", "--discover", "--include-live"]) != 0:
                print("live update build failed")
                return 1
            await asyncio.sleep(2)
        for i in range(runs):
            r = await run_once(scn)
            results.append(r)
            line = (f"{r['id']} run {i + 1}: " + (r["error"] if "error" in r else
                    f"{r['found']}/{r['expected']} found, {r['correct']}/{r['reported']} correct, "
                    f"false flags {r['false_flags'] or 'none'}, {r['seconds']} s"))
            if "changes_expected" in r:
                line += f", changes {r['changes_correct']}/{r['changes_expected']} (wrong {r['changes_wrong'] or 'none'})"
            print(line, flush=True)
        if scn.get("live"):
            reset_demo.main()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp}.json").write_text(json.dumps(results, indent=2))
    print(f"\nSaved eval/results/{stamp}.json")
    print(summary(results))
    return 0


def summary(results: list[dict]) -> str:
    lines = ["| Question | Runs | Expected findings found | Reported findings correct | False flags | Changes queued correctly | Avg time |",
             "|---|---|---|---|---|---|---|"]
    for scn in SCENARIOS:
        rs = [r for r in results if r["id"] == scn["id"]]
        if not rs:
            continue
        ok = [r for r in rs if "error" not in r]
        found = sum(r["found"] for r in ok); exp = sum(r["expected"] for r in ok)
        corr = sum(r["correct"] for r in ok); rep = sum(r["reported"] for r in ok)
        ff = sum(len(r["false_flags"]) for r in ok)
        ch = "n/a"
        if any("changes_expected" in r for r in ok):
            ch = f"{sum(r['changes_correct'] for r in ok)}/{sum(r['changes_expected'] for r in ok)}" \
                 f" ({sum(len(r['changes_wrong']) for r in ok)} wrong)"
        avg = round(sum(r["seconds"] for r in rs) / len(rs))
        errors = len(rs) - len(ok)
        lines.append(f"| {scn['id']} | {len(rs)}{f' ({errors} failed)' if errors else ''} | "
                     f"{found}/{exp if exp else '-'} | {corr}/{rep} | {ff} | {ch} | {avg} s |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", nargs="*", default=[], help="scenario ids, e.g. Q1 Q4")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.runs, args.only))


if __name__ == "__main__":
    sys.exit(main())
