from agent.grounding import Facts
from agent.policy import review_write

ACCOUNTS = {
    "rpatel": {"username": "rpatel", "type": "human", "roles": ["PAYMENT_APPROVER", "AP_CLERK"]},
    "svc_batch_payments": {"username": "svc_batch_payments", "type": "service", "roles": ["PAYMENT_APPROVER"]},
}


def test_service_accounts_are_never_queued():
    proposal, msg = review_write("ledgerly__disable_account", {"username": "svc_batch_payments", "reason": "x"}, ACCOUNTS)
    assert proposal is None and "service account" in msg


def test_reason_and_existing_account_and_held_role_are_required():
    assert review_write("ledgerly__disable_account", {"username": "rpatel"}, ACCOUNTS)[0] is None
    assert review_write("ledgerly__disable_account", {"username": "ghost", "reason": "x"}, ACCOUNTS)[0] is None
    assert review_write("ledgerly__revoke_role", {"username": "rpatel", "role": "GL_ADMIN", "reason": "x"}, ACCOUNTS)[0] is None
    p, _ = review_write("ledgerly__revoke_role", {"username": "RPatel", "role": "ap_clerk", "reason": "Mover"}, ACCOUNTS)
    assert p.arguments == {"username": "rpatel", "role": "AP_CLERK", "reason": "Mover"}


def _facts():
    f = Facts()
    f.absorb("ledgerly__list_active_accounts", {}, {"accounts": [
        {"username": "pnair", "email": "priya.nair@t.example", "type": "human", "roles": ["VENDOR_ADMIN"]},
        {"username": "svc_gl_import", "type": "service", "roles": ["GL_ADMIN"], "owner_employee_id": "E1014"},
        {"username": "ext_x", "email": "x@vendor.example", "type": "contractor", "roles": ["AP_CLERK"]}]})
    f.absorb("hr__lookup_employees_by_email", {}, {"matches": [
        {"email": "priya.nair@t.example", "matched_on": "past_email", "employee_id": "E1013", "name": "Priya Menon",
         "status": "terminated", "termination_date": "2026-10-17", "manager": "Suresh Menon (E1005)"}],
        "unknown_emails": ["x@vendor.example"]})
    f.absorb("hr__get_employee", {}, {"employee": {"employee_id": "E1014", "name": "Arjun Shah", "status": "terminated",
                                                   "termination_date": "2026-09-30"}})
    return f


def test_joined_view_puts_hr_record_next_to_each_account():
    lines = _facts().joined_view([])["accounts"]
    assert any(l.startswith("pnair") and "Priya Menon" in l and "PAST email" in l for l in lines)
    assert any(l.startswith("svc_gl_import") and "Arjun Shah (terminated" in l for l in lines)
    assert any(l.startswith("ext_x") and "NO HR RECORD" in l for l in lines)


def test_findings_get_verified_facts_policy_severity_and_hr_certifier():
    f = _facts()
    finding = f.ground({"category": "leaver_active_access", "username": "pnair", "severity": "low",
                        "certifier": "The CFO", "evidence": "..."})
    assert finding["facts"]["verified"] and finding["facts"]["termination_date"] == "2026-10-17"
    assert finding["severity"] == "high"            # policy, not the model's "low"
    assert finding["certifier"] == "Suresh Menon"   # HR, not the model's guess
    ghost = f.ground({"category": "leaver_active_access", "username": "nobody", "severity": "high"})
    assert ghost["facts"]["verified"] is False


def test_activity_after_the_last_day_is_attached():
    f = _facts()
    f.absorb("ledgerly__get_account_activity", {"username": "pnair"}, {"username": "pnair", "events": [
        {"ts": "2026-10-29T22:47:00Z", "action": "login"}, {"ts": "2026-10-14T12:00:00Z", "action": "login"}]})
    g = f.ground({"category": "post_termination_activity", "username": "pnair"})
    assert [e["ts"] for e in g["facts"]["used_after_leaving"]] == ["2026-10-29T22:47:00Z"]
    assert g["severity"] == "critical"


def test_claims_the_data_refutes_are_rejected():
    f = _facts()
    f.absorb("ledgerly__list_sod_rules", {}, {"rules": [{"rule_id": "SOD-01", "roles": ["VENDOR_ADMIN", "PAYMENT_APPROVER"]}]})
    f.absorb("ledgerly__get_role_catalog", {}, {"roles": {"VENDOR_ADMIN": {"typical_titles": ["Vendor Master Analyst"]}}})
    # pnair holds only VENDOR_ADMIN: no SoD rule is broken
    sod = f.ground({"category": "sod_conflict", "username": "pnair", "rule_id": "SOD-01"})
    assert "does not hold both roles" in f.check(sod, "2026-11-24")
    # ext_x is a contractor with no HR record: not a leaver
    leaver = f.ground({"category": "leaver_active_access", "username": "ext_x"})
    assert f.check(leaver, "2026-11-24")
    # but a real leaver passes
    real = f.ground({"category": "leaver_active_access", "username": "pnair"})
    assert f.check(real, "2026-11-24") is None


def test_sod_holders_are_computed_from_the_accounts_read():
    f = _facts()
    f.absorb("ledgerly__list_active_accounts", {}, {"accounts": [
        {"username": "dnair", "email": "d@t.example", "type": "human", "roles": ["VENDOR_ADMIN", "PAYMENT_APPROVER"]}]})
    f.absorb("ledgerly__list_sod_rules", {}, {"rules": [{"rule_id": "SOD-01", "roles": ["VENDOR_ADMIN", "PAYMENT_APPROVER"]}]})
    assert f.sod_holders()[0]["active_accounts_holding_both"] == ["dnair"]
