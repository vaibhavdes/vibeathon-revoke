"""Ground every finding in what the tools actually returned.

The model decides WHICH accounts are findings and WHY. The facts shown next to
each finding (roles, HR status, dates, manager, activity after leaving) are
copied here from the tool results of the same run, never from the model's
prose. A finding about an account no tool returned is marked unverified.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.config import HR

MONEY_ROLES = {"PAYMENT_APPROVER", "TREASURY_RELEASE"}
DORMANT_DAYS = 90             # no sign-in for this long: access is no longer needed (policy)
NEW_ACCOUNT_GRACE_DAYS = 30   # a new account that has never been used is not dormant yet
HIGH_RISK_ROLES = {"PAYMENT_APPROVER", "TREASURY_RELEASE", "VENDOR_ADMIN", "GL_ADMIN"}


def policy_severity(category: str, facts: dict, role: str | None = None) -> str | None:
    """Severity is company policy, so code sets it from verified facts."""
    roles = set(facts.get("roles") or [])
    if category == "post_termination_activity":
        return "critical"
    if category == "leaver_active_access":
        if facts.get("used_after_leaving") or facts.get("signed_in_after_leaving") or roles & MONEY_ROLES:
            return "critical"
        return "high"
    if category == "unknown_identity":
        return "high"
    if category == "service_account_owner":
        return "high" if roles & HIGH_RISK_ROLES else "medium"
    if category == "mover_retained_privilege":
        return "high" if (role or "").upper() in HIGH_RISK_ROLES else "medium"
    if category == "sod_conflict":
        return "high"
    if category == "upcoming_leaver":
        return "low"
    if category == "dormant_account":
        return "high" if roles & HIGH_RISK_ROLES else "medium"
    if category == "shared_account":
        return "high" if roles & HIGH_RISK_ROLES or "AP_CLERK" in roles else "medium"
    return None


def _review_date() -> str:
    from agent.config import REVIEW_DATE
    return REVIEW_DATE


def _is_dormant(last_login: str | None, created_at: str | None) -> bool:
    from datetime import date, timedelta
    from agent.config import REVIEW_DATE
    today = date.fromisoformat(REVIEW_DATE)
    if last_login:
        return date.fromisoformat(last_login[:10]) < today - timedelta(days=DORMANT_DAYS)
    if created_at:
        return date.fromisoformat(created_at[:10]) < today - timedelta(days=NEW_ACCOUNT_GRACE_DAYS)
    return False


@dataclass
class Facts:
    accounts: dict[str, dict] = field(default_factory=dict)      # username -> account row
    people_by_email: dict[str, dict] = field(default_factory=dict)
    people_by_id: dict[str, dict] = field(default_factory=dict)
    activity: dict[str, list] = field(default_factory=dict)       # username -> events
    unknown_emails: set[str] = field(default_factory=set)
    sod_rules: list[dict] = field(default_factory=list)
    catalog: dict[str, dict] = field(default_factory=dict)        # role -> catalog entry
    moves: dict[str, list] = field(default_factory=dict)          # employee_id -> job changes

    def absorb(self, tool: str, args: dict, result) -> None:
        if not isinstance(result, dict) or result.get("ok") is False:
            return
        name = tool.split("__", 1)[-1]
        if name == "list_active_accounts":
            for row in result.get("accounts", []):
                self.accounts[row["username"]] = {**self.accounts.get(row["username"], {}), **row,
                                                  "status": "active"}
        elif name in ("get_account", "disable_account", "revoke_role") and "account" in result:
            row = result["account"]
            self.accounts[row["username"]] = {**self.accounts.get(row["username"], {}), **row}
        elif name == "lookup_employees_by_email":
            for m in result.get("matches", []):
                self.people_by_email[m["email"].lower()] = m
                self.people_by_id[m["employee_id"]] = {**self.people_by_id.get(m["employee_id"], {}), **m}
            self.unknown_emails |= {e.lower() for e in result.get("unknown_emails", [])}
        elif name == "get_employee" and "employee" in result:
            emp = result["employee"]
            self.people_by_id[emp["employee_id"]] = {**self.people_by_id.get(emp["employee_id"], {}), **emp}
        elif name == "list_leavers_since":
            for p in result.get("leavers", []):
                self.people_by_id[p["employee_id"]] = {**self.people_by_id.get(p["employee_id"], {}), **p}
        elif name == "get_account_activity":
            self.activity[result.get("username", args.get("username", ""))] = result.get("events", [])
        elif name == "list_sod_rules":
            self.sod_rules = result.get("rules", [])
        elif name == "get_role_catalog":
            self.catalog = result.get("roles", {})
        elif name == "list_job_changes_since":
            for move in result.get("job_changes", []):
                self.moves.setdefault(move["employee_id"], []).append(move)

    def mover_review(self) -> list[dict]:
        """For each person who changed jobs, their active Ledgerly account and which of its roles
        are normal for the NEW title (from the role catalog). Done in code so no mover or role is
        skipped; the model still decides and explains."""
        by_email = {str(a.get("email", "")).lower(): a for a in self.accounts.values()
                    if a.get("status") == "active" and a.get("email")}
        out = []
        for emp_id, moves in sorted(self.moves.items()):
            move = max(moves, key=lambda m: m["effective_date"])
            acc = by_email.get(str(move.get("work_email", "")).lower())
            row = {"employee_id": emp_id, "name": move.get("name"), "effective_date": move["effective_date"],
                   "from_title": move.get("from_title"), "to_title": move.get("to_title"),
                   "username": acc["username"] if acc else None}
            if acc:
                kept, normal = [], []
                for role in acc.get("roles", []):
                    typical = (self.catalog.get(role) or {}).get("typical_titles") or []
                    (normal if ("*" in typical or move.get("to_title") in typical) else kept).append(role)
                row["roles_not_typical_for_new_title"] = kept
                row["roles_typical_for_new_title"] = normal
            else:
                row["note"] = "no active Ledgerly account"
            out.append(row)
        return out

    def candidates(self, category: str) -> set[str]:
        """Accounts that match a rule in the data read so far, computed by code. Used to tell the
        model what it may have skipped; each still goes through ground() and check()."""
        out = set()
        for username, acc in self.accounts.items():
            if acc.get("status") != "active":
                continue
            if category == "service_account_owner" and acc.get("type") == "service":
                owner = acc.get("owner_employee_id")
                if owner and owner not in self.people_by_id:
                    continue        # owner not looked up yet: unknown, not a finding
            finding = self.ground({"category": category, "username": username})
            if category == "unknown_identity" and (finding.get("facts") or {}).get("hr_status") != "no HR record":
                continue
            if self.check(finding, _review_date()) is None:
                out.add(username)
        return out

    def sod_holders(self) -> list[dict]:
        """For each SoD rule, the active accounts that hold both roles. Pure set logic,
        done in code and handed to the model next to the rules."""
        out = []
        for rule in self.sod_rules:
            a, b = rule["roles"]
            holders = sorted(u for u, acc in self.accounts.items()
                             if acc.get("status") == "active" and a in acc.get("roles", []) and b in acc.get("roles", []))
            out.append({**rule, "active_accounts_holding_both": holders})
        return out

    def check(self, finding: dict, review_date: str) -> str | None:
        """Return why a finding does NOT hold against the data read in this run, or None.
        Only claims the data can refute are rejected; anything unknown is let through."""
        cat = finding.get("category", "")
        g = finding.get("facts") or {}
        if not g.get("verified"):
            return "no tool returned this account"
        if g.get("account_status") not in (None, "active"):
            return f"the account is {g.get('account_status')}"
        roles = set(g.get("roles") or [])
        hr = g.get("hr_status")
        ended = g.get("termination_date")
        if cat == "leaver_active_access":
            if hr != "terminated" or not ended or ended > review_date:
                return f"PeopleHub says {hr or 'no record'}" + (f", last day {ended}" if ended else "")
        elif cat == "post_termination_activity":
            if not g.get("used_after_leaving"):
                return "no activity after the last day in the data read"
        elif cat == "upcoming_leaver":
            if hr != "active" or not ended or ended <= review_date:
                return "no future termination date in PeopleHub"
        elif cat == "unknown_identity":
            if g.get("account_type") == "shared":
                return "a shared team login: report it as shared_account"
            if g.get("account_type") == "service" or (hr and hr != "no HR record"):
                return "the account has an HR record or is a service account"
        elif cat == "service_account_owner":
            if g.get("account_type") != "service":
                return "not a service account"
            owner = str(g.get("owner", ""))
            if "(active)" in owner:
                return "its owner is active"
        elif cat == "shared_account":
            if g.get("account_type") != "shared":
                return "not a shared account"
        elif cat == "dormant_account":
            if hr != "active":
                return f"PeopleHub says {hr or 'no record'}" + (
                    ": approved leave is not dormancy" if hr == "on_leave" else "")
            if not g.get("dormant"):
                return f"last sign-in {g.get('last_login') or 'never'}, created {g.get('created_at')}: within policy"
        elif cat == "mover_retained_privilege":
            role = str(finding.get("role") or "").upper()
            if role not in roles:
                return f"the account does not hold {role or 'that role'}"
            typical = (self.catalog.get(role) or {}).get("typical_titles")
            if typical and ("*" in typical or g.get("title") in typical):
                return f"{role} is normal for the current title {g.get('title')}"
        elif cat == "sod_conflict":
            if self.sod_rules:
                broken = [r for r in self.sod_rules if set(r["roles"]) <= roles]
                if not broken:
                    return "the account does not hold both roles of any SoD rule"
                rule_ids = [r["rule_id"] for r in broken]
                if str(finding.get("rule_id") or "").upper() not in rule_ids:
                    finding["rule_id"] = rule_ids[0]
                finding.pop("role", None)
        return None

    def joined_view(self, unknown_emails: list[str]) -> dict:
        """One line per active Ledgerly account with its PeopleHub record next to it.

        Language models are unreliable at matching 40 rows to 40 rows by email.
        Code is not. The model gets the join done right and spends its effort
        on applying the policy. Lines keep it small enough for free-tier limits.
        """
        lines = []
        for username, acc in sorted(self.accounts.items()):
            if acc.get("status") != "active":
                continue
            head = (f"{username} [{acc.get('type')}] roles={','.join(acc.get('roles', []))} "
                    f"last_login={acc.get('last_login') or 'never'} created={acc.get('created_at')}")
            if acc.get("type") == "shared":
                hr = f"SHARED team login ({acc.get('email')}): no single person is accountable"
            elif acc.get("type") == "service":
                owner = acc.get("owner_employee_id")
                known = self.people_by_id.get(owner or "")
                if not owner:
                    hr = "service account, NO OWNER"
                elif known:
                    hr = (f"service account, owner {owner} {known.get('name')} ({known.get('status')}"
                          + (f", termination_date={known['termination_date']}" if known.get("termination_date") else "")
                          + ")")
                else:
                    hr = f"service account, owner_employee_id={owner} (not in this lookup: check with {HR}__get_employee)"
            else:
                person = self.person_for(acc)
                if person is None:
                    hr = f"NO HR RECORD for {acc.get('email')}" + (
                        f" (contractor, {acc['company']})" if acc.get("company") else "")
                else:
                    bits = [person.get("name"), person.get("employee_id"), person.get("status"),
                            person.get("title")]
                    if person.get("matched_on") == "past_email":
                        bits.append(f"matched on PAST email {acc.get('email')}")
                    for key in ("termination_date", "rehire_date", "leave_until"):
                        if person.get(key):
                            bits.append(f"{key}={person[key]}")
                    ended = person.get("termination_date")
                    last = acc.get("last_login")
                    if ended and last and last != "never" and last[:10] > ended:
                        bits.append(f"SIGNED IN AFTER LAST DAY ({last})")
                    if person.get("status") == "active" and _is_dormant(
                            None if last in (None, "never") else last, acc.get("created_at")):
                        bits.append(f"DORMANT: no sign-in for over {DORMANT_DAYS} days" if last and last != "never"
                                    else "DORMANT: never signed in")
                    if person.get("manager"):
                        bits.append(f"manager={person['manager']}")
                    hr = ", ".join(str(b) for b in bits if b)
            lines.append(f"{head} | {hr}")
        return {"ok": True, "joined": "PeopleHub record of each active Ledgerly account (matched by email, "
                                      "past emails included). Format: username [type] roles last_login created | HR record",
                "accounts": lines, "unknown_emails": unknown_emails}

    def person_for(self, account: dict) -> dict | None:
        if account.get("type") == "service":
            return self.people_by_id.get(account.get("owner_employee_id") or "")
        email = (account.get("email") or "").lower()
        return self.people_by_email.get(email)

    def ground(self, finding: dict) -> dict:
        username = finding.get("username", "")
        account = self.accounts.get(username)
        g: dict = {"verified": account is not None}
        if account:
            g["account_status"] = account.get("status")
            g["roles"] = account.get("roles", [])
            g["account_type"] = account.get("type")
            g["last_login"] = None if account.get("last_login") in (None, "never") else account.get("last_login")
            g["created_at"] = account.get("created_at")
            g["dormant"] = _is_dormant(g["last_login"], g["created_at"])
            person = self.person_for(account)
            if account.get("type") == "service":
                g["owner"] = (f"{person.get('name')} ({person.get('status')})" if person
                              else (account.get("owner_employee_id") or "none recorded"))
            elif person:
                g["hr_name"] = person.get("name")
                g["hr_status"] = person.get("status")
                g["title"] = person.get("title")
                for key in ("termination_date", "rehire_date", "leave_until"):
                    if person.get(key):
                        g[key] = person[key]
                manager = person.get("manager")
                if isinstance(manager, dict):
                    manager = manager.get("name")
                if manager:
                    g["manager"] = manager
            elif (account.get("email") or "").lower() in self.unknown_emails:
                g["hr_status"] = "no HR record"
            ended = g.get("termination_date")
            events = self.activity.get(username)
            if ended and g["last_login"] and g["last_login"][:10] > ended:
                g["signed_in_after_leaving"] = g["last_login"]
            if events is not None and ended:
                g["used_after_leaving"] = [
                    {k: e.get(k) for k in ("ts", "action", "detail", "amount_usd") if e.get(k) is not None}
                    for e in events if e.get("ts", "")[:10] > ended and e.get("action") != "account_disabled"]
        if not finding.get("person"):
            finding["person"] = g.get("hr_name") or (account or {}).get("display_name") or ""
        # The certifier is the person's manager in HR, the system of record. The
        # model's suggestion is used only when HR has no answer.
        if g.get("manager"):
            finding["certifier"] = str(g["manager"]).split(" (")[0]
        elif account and account.get("type") == "service":
            owner = self.people_by_id.get(account.get("owner_employee_id") or "") or {}
            manager = owner.get("manager")
            if isinstance(manager, dict):
                manager = manager.get("name")
            if manager and owner.get("status") == "active":
                finding["certifier"] = str(manager).split(" (")[0]
        severity = policy_severity(finding.get("category", ""), g, finding.get("role"))
        if severity and g.get("verified"):
            finding["severity"] = severity
        finding["facts"] = g
        return finding
