"""Ledgerly: Tamarind Global Logistics' accounts-payable and general-ledger app.

Built in-house in 2016 and still the system that approves and releases every
vendor payment. It has its own user accounts and roles; it is not connected
to the HR system or to any identity-governance tool.

Run:  uvicorn main:app --port 8202
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
SEED_FILE = HERE / "data.json"
STATE_FILE = HERE / ".state.json"

app = FastAPI(title="Ledgerly", version="7.2.0",
              description="Accounts payable and general ledger for Tamarind Global Logistics.")
_lock = threading.Lock()


def _load() -> dict:
    source = STATE_FILE if STATE_FILE.exists() else SEED_FILE
    return json.loads(source.read_text())


def _save(db: dict) -> None:
    STATE_FILE.write_text(json.dumps(db, indent=1))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find(db: dict, username: str) -> Optional[dict]:
    username = (username or "").strip().lower()
    return next((u for u in db["users"] if u["username"] == username), None)


def _compact(record: dict) -> dict:
    return {k: v for k, v in record.items() if v not in (None, [], "")}


def _account(u: dict) -> dict:
    return _compact({
        "username": u["username"],
        "display_name": u["display_name"],
        "email": u["email"],
        "type": u["type"],
        "company": u["company"],
        "owner_employee_id": u["owner_employee_id"],
        "status": u["status"],
        "roles": u["roles"],
        "created_at": u["created_at"],
        "last_login": u.get("last_login"),
        "disabled_at": u["disabled_at"],
        "disabled_reason": u["disabled_reason"],
    })


# --------------------------------------------------------------------------
# Accounts and roles
# --------------------------------------------------------------------------
@app.get("/users/active")
def list_active_accounts():
    """Every ACTIVE Ledgerly account: username, email, type (human, service, contractor, shared), roles, created_at, last_login (null if never signed in), owner_employee_id for service accounts, company for contractors. Disabled accounts are left out."""
    db = _load()
    rows = []
    for u in db["users"]:
        if u["status"] != "active":
            continue
        rows.append(_compact({"username": u["username"], "email": u["email"], "type": u["type"],
                              "roles": u["roles"], "created_at": u["created_at"],
                              "last_login": (u.get("last_login") or "never")[:10],
                              "owner_employee_id": u["owner_employee_id"], "company": u["company"]}))
    return {"ok": True, "count": len(rows), "accounts": rows}


@app.get("/users/{username}")
def get_account(username: str):
    """One Ledgerly account by username, any status: email, type, roles, created_at, and disabled_at with reason if disabled."""
    db = _load()
    u = _find(db, username)
    if not u:
        return {"ok": False, "error": f"No Ledgerly account named {username}."}
    return {"ok": True, "account": _account(u)}


@app.get("/roles")
def get_role_catalog():
    """Ledgerly roles with what each allows, risk level, and the job titles that normally need it ('*' = any title)."""
    return {"ok": True, "roles": _load()["roles"]}


@app.get("/roles/{role}/members")
def list_role_members(role: str):
    """Active accounts that hold one role, e.g. PAYMENT_APPROVER."""
    db = _load()
    role = (role or "").strip().upper()
    if role not in db["roles"]:
        return {"ok": False, "error": f"Unknown role {role}. Roles: {', '.join(db['roles'])}."}
    members = [{"username": u["username"], "email": u["email"], "type": u["type"]}
               for u in db["users"] if u["status"] == "active" and role in u["roles"]]
    return {"ok": True, "role": role, "count": len(members), "members": members}


@app.get("/sod/rules")
def list_sod_rules():
    """Segregation-of-duties rules: pairs of roles that one person must never hold together, and why."""
    return {"ok": True, "rules": _load()["sod_rules"]}


class DisableRequest(BaseModel):
    reason: Optional[str] = None
    ticket: Optional[str] = None


@app.post("/users/{username}/disable")
def disable_account(username: str, body: DisableRequest):
    """Disable a Ledgerly account so it can no longer sign in. Body: reason (required), ticket (optional change ticket). The account and its history are kept. Returns the account after the change."""
    reason = (body.reason or "").strip()
    if not reason:
        return {"ok": False, "error": "A reason is required to disable an account."}
    with _lock:
        db = _load()
        u = _find(db, username)
        if not u:
            return {"ok": False, "error": f"No Ledgerly account named {username}."}
        if u["status"] == "disabled":
            return {"ok": True, "changed": False, "note": "Account was already disabled.", "account": _account(u)}
        u["status"] = "disabled"
        u["disabled_at"] = _now()
        u["disabled_reason"] = reason + (f" [{body.ticket}]" if body.ticket else "")
        db["activity"].append({"ts": u["disabled_at"], "username": u["username"], "action": "account_disabled",
                               "detail": u["disabled_reason"], "amount_usd": None})
        _save(db)
    return {"ok": True, "changed": True, "account": _account(u)}


class RevokeRequest(BaseModel):
    role: Optional[str] = None
    reason: Optional[str] = None


@app.post("/users/{username}/roles/revoke")
def revoke_role(username: str, body: RevokeRequest):
    """Remove one role from a Ledgerly account. Body: role (required), reason (required). Other roles are kept. Returns the account after the change."""
    role = (body.role or "").strip().upper()
    reason = (body.reason or "").strip()
    if not role or not reason:
        return {"ok": False, "error": "Both role and reason are required."}
    with _lock:
        db = _load()
        u = _find(db, username)
        if not u:
            return {"ok": False, "error": f"No Ledgerly account named {username}."}
        if role not in u["roles"]:
            return {"ok": True, "changed": False, "note": f"{username} does not hold {role}.", "account": _account(u)}
        u["roles"] = [r for r in u["roles"] if r != role]
        db["activity"].append({"ts": _now(), "username": u["username"], "action": "role_revoked",
                               "detail": f"{role}: {reason}", "amount_usd": None})
        _save(db)
    return {"ok": True, "changed": True, "account": _account(u)}


# --------------------------------------------------------------------------
# Administration (Ledgerly admin console only)
# --------------------------------------------------------------------------
class NewAccount(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    roles: Optional[list[str]] = None


@app.post("/users")
def create_account(body: NewAccount):
    """Provision a new Ledgerly account."""
    if not (body.username and body.email):
        return {"ok": False, "error": "username and email are required."}
    with _lock:
        db = _load()
        if _find(db, body.username):
            return {"ok": False, "error": "Username already exists."}
        u = {"username": body.username.lower(), "display_name": body.display_name or body.username,
             "email": body.email, "type": "human", "company": None, "owner_employee_id": None,
             "status": "active", "roles": [r.upper() for r in (body.roles or ["READ_ONLY"])],
             "created_at": _now()[:10], "disabled_at": None, "disabled_reason": None}
        db["users"].append(u)
        _save(db)
    return {"ok": True, "account": _account(u)}


@app.delete("/users/{username}")
def delete_account(username: str):
    """Permanently delete an account and its audit history."""
    with _lock:
        db = _load()
        u = _find(db, username)
        if not u:
            return {"ok": False, "error": f"No Ledgerly account named {username}."}
        db["users"].remove(u)
        db["activity"] = [a for a in db["activity"] if a["username"] != u["username"]]
        _save(db)
    return {"ok": True, "deleted": username}


@app.post("/payments/{payment_id}/approve")
def approve_payment(payment_id: str):
    """Approve a pending vendor payment."""
    return {"ok": False, "error": "Payments are approved in the Ledgerly desktop client."}


@app.post("/admin/reseed")
def reseed():
    """Discard all changes and restore the seed data."""
    with _lock:
        STATE_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/health")
def health():
    """Liveness probe."""
    return {"ok": True, "service": "ledgerly"}


# >>> live-update: account activity (inserted by scripts/live_update.py)
# <<< live-update
