"""Ground truth for the access review, computed straight from the seed files.

This never goes through MCP or a model. It is the answer key that the
scripted check (MCP tools, no model) and the agent eval (MCP tools + model)
are both scored against.

A finding is a tuple key:
    ("leaver_active_access", username)
    ("post_termination_activity", username)     only once activity is exposed
    ("upcoming_leaver", username)
    ("unknown_identity", username)
    ("service_account_owner", username)
    ("mover_retained_privilege", username, role)
    ("sod_conflict", username, rule_id)
    ("dormant_account", username)               no sign-in for 90 days, or never used after 30
    ("shared_account", username)                a team login: nobody is accountable
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from agent.config import REVIEW_DATE, ROOT
from agent.grounding import DORMANT_DAYS, NEW_ACCOUNT_GRACE_DAYS

MOVER_WINDOW_DAYS = 180

# Written by hand from scripts/seed.py. compute() must agree with it.
PLANTED = {
    ("leaver_active_access", "rpatel"), ("leaver_active_access", "pnair"),
    ("leaver_active_access", "ashah"), ("leaver_active_access", "miyer"),
    ("leaver_active_access", "vrao"),
    ("upcoming_leaver", "kjoshi"),
    ("unknown_identity", "ext_dkozlov"),
    ("service_account_owner", "svc_batch_payments"), ("service_account_owner", "svc_gl_import"),
    ("mover_retained_privilege", "rgupta", "AP_CLERK"), ("mover_retained_privilege", "rgupta", "PAYMENT_APPROVER"),
    ("mover_retained_privilege", "fsheikh", "TREASURY_RELEASE"), ("mover_retained_privilege", "nverma", "GL_ADMIN"),
    ("sod_conflict", "dnair", "SOD-01"), ("sod_conflict", "abose", "SOD-02"),
    ("sod_conflict", "rgupta", "SOD-02"), ("sod_conflict", "rpatel", "SOD-02"),
    ("dormant_account", "fpillai"), ("dormant_account", "fmishra"), ("dormant_account", "aagarwal"),
    ("shared_account", "finops_shared"),
}
PLANTED_ACTIVITY = {("post_termination_activity", "rpatel"), ("post_termination_activity", "pnair")}

# People and accounts a careless review would flag. None of them may appear.
# adeshmukh is also inactive for months, but on approved leave; mkhanna's account is 14 days old.
MUST_NOT_FLAG = {"skulkarni", "adeshmukh", "sreddy", "svc_fx_rates", "mkhanna"}


def load():
    hub = json.loads((ROOT / "systems/peoplehub/data.json").read_text())
    led = json.loads((ROOT / "systems/ledgerly/data.json").read_text())
    return hub, led


def compute(include_activity: bool = False, review_date: str = REVIEW_DATE) -> set[tuple]:
    hub, led = load()
    today = date.fromisoformat(review_date)
    employees = hub["employees"]
    by_email = {}
    for e in employees:
        by_email[e["work_email"].lower()] = e
        for old in e["past_emails"]:
            by_email.setdefault(old.lower(), e)
    by_id = {e["employee_id"]: e for e in employees}
    roles = led["roles"]
    out: set[tuple] = set()

    active = [u for u in led["users"] if u["status"] == "active"]
    for u in active:
        if u["type"] == "service":
            owner = by_id.get(u["owner_employee_id"] or "")
            if owner is None or owner["status"] != "active":
                out.add(("service_account_owner", u["username"]))
            continue
        if u["type"] == "shared":
            out.add(("shared_account", u["username"]))
            continue
        emp = by_email.get((u["email"] or "").lower())
        if emp is None:
            out.add(("unknown_identity", u["username"]))
            continue
        ended = date.fromisoformat(emp["termination_date"]) if emp["termination_date"] else None
        if emp["status"] == "terminated" and ended and ended <= today:
            out.add(("leaver_active_access", u["username"]))
            if include_activity:
                after = [a for a in led["activity"]
                         if a["username"] == u["username"] and a["ts"][:10] > emp["termination_date"]]
                if after:
                    out.add(("post_termination_activity", u["username"]))
        elif emp["status"] == "active" and ended and ended > today:
            out.add(("upcoming_leaver", u["username"]))
        if emp["status"] == "active":
            last = u.get("last_login")
            created = date.fromisoformat(u["created_at"])
            if (last and date.fromisoformat(last[:10]) < today - timedelta(days=DORMANT_DAYS)) or \
                    (not last and created < today - timedelta(days=NEW_ACCOUNT_GRACE_DAYS)):
                out.add(("dormant_account", u["username"]))
        for move in emp["job_history"]:
            when = date.fromisoformat(move["effective_date"])
            if today - timedelta(days=MOVER_WINDOW_DAYS) <= when <= today and emp["status"] != "terminated":
                for role in u["roles"]:
                    typical = roles[role]["typical_titles"]
                    if "*" not in typical and emp["title"] not in typical:
                        out.add(("mover_retained_privilege", u["username"], role))

    for rule in led["sod_rules"]:
        a, b = rule["roles"]
        for u in active:
            if a in u["roles"] and b in u["roles"]:
                out.add(("sod_conflict", u["username"], rule["rule_id"]))
    return out


def certifier(username: str) -> str | None:
    """Name of the manager who must certify this account's access."""
    hub, led = load()
    user = next((u for u in led["users"] if u["username"] == username), None)
    if not user:
        return None
    by_id = {e["employee_id"]: e for e in hub["employees"]}
    emp = None
    if user["type"] == "service":
        emp = by_id.get(user["owner_employee_id"] or "")
    else:
        email = (user["email"] or "").lower()
        emp = next((e for e in hub["employees"]
                    if e["work_email"].lower() == email or email in [p.lower() for p in e["past_emails"]]), None)
    if not emp or not emp["manager_id"]:
        return None
    mgr = by_id[emp["manager_id"]]
    return f"{mgr['first_name']} {mgr['last_name']}"


if __name__ == "__main__":
    for include in (False, True):
        got = compute(include)
        want = PLANTED | (PLANTED_ACTIVITY if include else set())
        status = "OK" if got == want else "MISMATCH"
        print(f"activity={'on ' if include else 'off'} {status}: {len(got)} findings")
        for key in sorted(got ^ want):
            print("   ", "extra" if key in got else "missing", key)
