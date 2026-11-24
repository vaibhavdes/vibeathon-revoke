"""A new app with its own field names plugs in through mcp/exposure.json, not code.

Here an imaginary procurement app calls an account a "login", its roles "groups", and its active state
"enabled". The hub's mapping turns its replies into Revoke's fields, so grounding works unchanged.
"""
from __future__ import annotations

from agent.config import APP
from agent.grounding import Facts
from agent.hub import HubTool

ENTRY = {  # what the app's entries in exposure.json would add
    "as": "list_active_accounts",
    "fields": {"accounts": "users", "username": "login", "email": "mail", "roles": "groups",
               "last_login": "lastSeen", "status": "state"},
    "values": {"status": {"enabled": "active"}, "roles": {"approver": "PAYMENT_APPROVER"}},
}


def tool() -> HubTool:
    return HubTool(qualified=f"{APP}__list_active_accounts", system="procure", name="get_users_list",
                   description="", schema={}, access="read", fields=ENTRY["fields"], values=ENTRY["values"])


def test_reply_is_translated_to_revoke_fields_and_codes():
    raw = {"ok": True, "users": [{"login": "jdoe", "mail": "J.Doe@corp.example", "groups": ["approver", "viewer"],
                                  "lastSeen": "2026-11-20", "state": "enabled"}]}
    out = tool().to_revoke(raw)
    assert out == {"ok": True, "accounts": [{"username": "jdoe", "email": "J.Doe@corp.example",
                                             "roles": ["PAYMENT_APPROVER", "viewer"], "last_login": "2026-11-20",
                                             "status": "active"}]}


def test_grounding_reads_the_mapped_reply():
    raw = {"ok": True, "users": [{"login": "jdoe", "mail": "j.doe@corp.example", "groups": ["approver"],
                                  "lastSeen": "2026-11-20", "state": "enabled"}]}
    facts = Facts()
    facts.absorb(f"{APP}__list_active_accounts", {}, tool().to_revoke(raw))
    assert facts.accounts["jdoe"]["email"] == "j.doe@corp.example"
    assert facts.accounts["jdoe"]["roles"] == ["PAYMENT_APPROVER"]


def test_arguments_go_back_in_the_app_field_names():
    assert tool().to_app({"username": "jdoe", "reason": "left"}) == {"login": "jdoe", "reason": "left"}


def test_demo_apps_need_no_mapping():
    plain = HubTool(qualified="x", system="ledgerly", name="get_account", description="", schema={}, access="read")
    reply = {"ok": True, "account": {"username": "rpatel", "roles": ["AP_CLERK"]}}
    assert plain.to_revoke(reply) == reply and plain.to_app({"username": "rpatel"}) == {"username": "rpatel"}
