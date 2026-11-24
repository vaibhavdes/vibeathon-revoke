"""The two legacy systems behave the way the agent relies on."""


def test_lookup_matches_past_email_and_ignores_case(peoplehub):
    r = peoplehub.post("/employees/lookup", json={"emails": [
        "priya.nair@tamarind.example", "Arjun.Shah@TAMARIND.example", "nobody@elsewhere.example"]}).json()
    by_email = {m["email"]: m for m in r["matches"]}
    assert by_email["priya.nair@tamarind.example"]["name"] == "Priya Menon"
    assert by_email["priya.nair@tamarind.example"]["matched_on"] == "past_email"
    assert by_email["Arjun.Shah@TAMARIND.example"]["status"] == "terminated"
    assert r["unknown_emails"] == ["nobody@elsewhere.example"]


def test_lookup_accepts_null_and_comma_string(peoplehub):
    # Synapse's generated tools send missing body fields as null.
    assert peoplehub.post("/employees/lookup", json={"emails": None}).json()["ok"] is False
    r = peoplehub.post("/employees/lookup", json={"emails": "sneha.kulkarni@tamarind.example"}).json()
    assert r["matches"][0]["rehire_date"] == "2026-02-02"


def test_leavers_include_rehire_and_notice_period_with_current_status(peoplehub):
    rows = peoplehub.get("/employees/leavers/since/2025-01-01").json()["leavers"]
    status = {r["name"]: r["status"] for r in rows}
    assert status["Sneha Kulkarni"] == "active"      # rehired
    assert status["Kavita Joshi"] == "active"        # notice period, last day in December
    assert status["Rahul Patel"] == "terminated"


def test_bad_dates_are_reported_not_raised(peoplehub):
    assert peoplehub.get("/employees/leavers/since/yesterday").json()["ok"] is False


def test_disable_requires_reason_and_keeps_history(ledgerly):
    assert ledgerly.post("/users/miyer/disable", json={"reason": None, "ticket": None}).json()["ok"] is False
    r = ledgerly.post("/users/miyer/disable", json={"reason": "Leaver", "ticket": "CHG-1"}).json()
    assert r["changed"] is True and r["account"]["status"] == "disabled"
    again = ledgerly.post("/users/miyer/disable", json={"reason": "Leaver", "ticket": None}).json()
    assert again["changed"] is False
    assert ledgerly.get("/users/miyer").json()["account"]["disabled_reason"] == "Leaver [CHG-1]"


def test_revoke_role_removes_only_that_role(ledgerly):
    r = ledgerly.post("/users/rgupta/roles/revoke", json={"role": "payment_approver", "reason": "Mover"}).json()
    assert r["account"]["roles"] == ["AP_CLERK"]
    missing = ledgerly.post("/users/rgupta/roles/revoke", json={"role": "GL_ADMIN", "reason": "x"}).json()
    assert missing["changed"] is False


def test_active_accounts_leave_out_disabled(ledgerly):
    names = {a["username"] for a in ledgerly.get("/users/active").json()["accounts"]}
    assert "rpatel" in names and "grangan" not in names
