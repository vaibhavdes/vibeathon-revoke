"""Prove, without any model, that the Synapse-generated tools are enough.

Reaches every finding using ONLY the MCP tools the agent gets, joins the two
systems the way the agent must, and compares the result with the answer key
in eval/expected.py. Both systems must be running (python -m scripts.run_systems).

    python -m eval.scripted_check            # before the live update
    python -m eval.scripted_check --live     # after it: activity tool required
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta

from agent.config import REVIEW_DATE
from agent.grounding import DORMANT_DAYS, NEW_ACCOUNT_GRACE_DAYS
from agent.hub import McpHub
from eval.expected import MOVER_WINDOW_DAYS, MUST_NOT_FLAG, PLANTED, PLANTED_ACTIVITY, compute

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


async def review(hub: McpHub, live: bool) -> set[tuple]:
    today = date.fromisoformat(REVIEW_DATE)
    accounts = (await hub.call("ledgerly__list_active_accounts"))[0]["accounts"]
    catalog = (await hub.call("ledgerly__get_role_catalog"))[0]["roles"]
    rules = (await hub.call("ledgerly__list_sod_rules"))[0]["rules"]

    humans = [a for a in accounts if a["type"] != "service"]
    lookup, _ = await hub.call("hr__lookup_employees_by_email",
                               {"emails": [a["email"] for a in humans if a.get("email")]})
    by_email = {m["email"].lower(): m for m in lookup["matches"]}
    unknown = {e.lower() for e in lookup["unknown_emails"]}
    since = (today - timedelta(days=MOVER_WINDOW_DAYS)).isoformat()
    moves = (await hub.call("hr__list_job_changes_since", {"since_date": since}))[0]["job_changes"]
    moved = {m["employee_id"] for m in moves if m["effective_date"] <= REVIEW_DATE}

    out: set[tuple] = set()
    for acc in accounts:
        name = acc["username"]
        if acc["type"] == "service":
            owner_id = acc.get("owner_employee_id")
            owner_ok = False
            if owner_id:
                owner = (await hub.call("hr__get_employee", {"employee_id": owner_id}))[0]
                owner_ok = owner.get("ok") and owner["employee"]["status"] == "active"
            if not owner_ok:
                out.add(("service_account_owner", name))
            continue
        if acc["type"] == "shared":
            out.add(("shared_account", name))
            continue
        email = (acc.get("email") or "").lower()
        if email in unknown or email not in by_email:
            out.add(("unknown_identity", name))
            continue
        person = by_email[email]
        ended = person.get("termination_date")
        if person["status"] == "terminated" and ended and ended <= REVIEW_DATE:
            out.add(("leaver_active_access", name))
            if live:
                activity, _ = await hub.call("ledgerly__get_account_activity", {"username": name})
                if any(ev["ts"][:10] > ended for ev in activity["events"]
                       if ev["action"] != "account_disabled"):
                    out.add(("post_termination_activity", name))
        elif person["status"] == "active" and ended and ended > REVIEW_DATE:
            out.add(("upcoming_leaver", name))
        if person["status"] == "active":
            last = acc.get("last_login")
            last = None if last in (None, "never") else last
            if (last and date.fromisoformat(last[:10]) < today - timedelta(days=DORMANT_DAYS)) or \
                    (not last and date.fromisoformat(acc["created_at"]) < today - timedelta(days=NEW_ACCOUNT_GRACE_DAYS)):
                out.add(("dormant_account", name))
        if person["employee_id"] in moved and person["status"] != "terminated":
            for role in acc["roles"]:
                typical = catalog[role]["typical_titles"]
                if "*" not in typical and person["title"] not in typical:
                    out.add(("mover_retained_privilege", name, role))
    for rule in rules:
        a, b = rule["roles"]
        for acc in accounts:
            if a in acc["roles"] and b in acc["roles"]:
                out.add(("sod_conflict", acc["username"], rule["rule_id"]))
    return out


async def main_async(live: bool) -> int:
    async with McpHub() as hub:
        names = sorted(hub.tools)
        has_activity = "ledgerly__get_account_activity" in hub.tools
        check(len(names) == (13 if live else 12) and has_activity == live,
              f"MCP tools from Synapse: {len(names)} ({'with' if has_activity else 'without'} get_account_activity)")
        check(all(not hub.is_write(n) for n in names if "disable" not in n and "revoke" not in n)
              and hub.is_write("ledgerly__disable_account") and hub.is_write("ledgerly__revoke_role"),
              "only disable_account and revoke_role are write tools")
        got = await review(hub, live)

    truth = compute(include_activity=live)
    planted = PLANTED | (PLANTED_ACTIVITY if live else set())
    check(truth == planted, "answer key (computed from raw data) equals the planted cases")

    def by(cat):
        return {k for k in got if k[0] == cat}, {k for k in truth if k[0] == cat}

    labels = {
        "leaver_active_access": "Q1 leavers with active accounts (incl. maiden-name and case-mismatch emails)",
        "upcoming_leaver": "Q1 notice-period employee reported as upcoming, not as a leaver",
        "unknown_identity": "Q1 contractor with no HR record reported as unknown identity",
        "service_account_owner": "Q1 service accounts with no owner or an owner who left",
        "mover_retained_privilege": "Q2 movers keeping privileges of their old job",
        "sod_conflict": "Q3 segregation-of-duties conflicts",
        "dormant_account": "Q5 dormant accounts (no sign-in for 90 days, or never used)",
        "shared_account": "Q5 shared team login",
    }
    if live:
        labels["post_termination_activity"] = "Q4 leaver accounts USED after the last day"
    for cat, label in labels.items():
        g, t = by(cat)
        detail = ", ".join(sorted("/".join(k[1:]) for k in t))
        check(g == t, f"{label}: {detail}")
    flagged = {k[1] for k in got}
    check(not (flagged & MUST_NOT_FLAG),
          "not flagged: rehire skulkarni, on-leave adeshmukh, promoted sreddy, owned svc_fx_rates, new account mkhanna")
    if not live:
        check(not any(k[0] == "post_termination_activity" for k in got),
              "without the activity tool, what a leaver's account did cannot be established")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="expect the live-update activity tool")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.live))


if __name__ == "__main__":
    sys.exit(main())
