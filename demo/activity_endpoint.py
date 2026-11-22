@app.get("/users/{username}/activity")
def get_account_activity(username: str):
    """Sign-ins and actions recorded for one account, newest first (latest 25): ts, action (login, approve_payment, view_vendor_list, ...), detail, amount_usd. Also returns last_login."""
    db = _load()
    u = _find(db, username)
    if not u:
        return {"ok": False, "error": f"No Ledgerly account named {username}."}
    events = sorted((a for a in db["activity"] if a["username"] == u["username"]),
                    key=lambda a: a["ts"], reverse=True)
    last_login = next((a["ts"] for a in events if a["action"] == "login"), None)
    return {"ok": True, "username": u["username"], "status": u["status"], "last_login": last_login,
            "count": len(events), "events": [_compact(dict(a, username=None)) for a in events[:25]]}
