"""The approval gate. The model can PROPOSE a change; only a human can make it.

Any tool that is not marked "read" in mcp/exposure.json is intercepted here.
It is checked against policy, and if it passes it is queued as a Proposal.
Nothing is sent to Ledgerly until approve() is called with the proposal's id,
and then exactly the queued arguments are sent, never new ones from the model.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field

_ids = itertools.count(1)


@dataclass
class Proposal:
    id: str
    tool: str
    arguments: dict
    summary: str
    status: str = "pending"          # pending | approved | rejected | executed | failed
    result: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    decided_by: str = ""
    decided_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def describe(tool: str, args: dict) -> str:
    user = args.get("username", "?")
    if tool.endswith("disable_account"):
        return f"Disable Ledgerly account {user}"
    if tool.endswith("revoke_role"):
        return f"Remove role {str(args.get('role', '?')).upper()} from {user}"
    return f"{tool} {args}"


def review_write(tool: str, args: dict, accounts: dict[str, dict]) -> tuple[Proposal | None, str]:
    """Return (proposal, message for the model). accounts: username -> active account row."""
    user = str(args.get("username") or "").strip().lower()
    if not user:
        return None, "Blocked: username is required."
    account = accounts.get(user)
    if account is None:
        return None, f"Blocked: {user} is not an active Ledgerly account."
    if account.get("type") == "service":
        return None, (f"Blocked by policy: {user} is a service account. Disabling or changing it would stop a "
                      "business process. Recommend assigning an active owner instead.")
    if not str(args.get("reason") or "").strip():
        return None, "Blocked: give a specific reason that cites the finding."
    if tool.endswith("revoke_role"):
        role = str(args.get("role") or "").strip().upper()
        if role not in account.get("roles", []):
            return None, f"Blocked: {user} does not hold {role}."
        args = {**args, "role": role}
    args = {**args, "username": user}
    proposal = Proposal(id=f"A{next(_ids)}", tool=tool, arguments=args, summary=describe(tool, args))
    return proposal, (f"Queued as {proposal.id} for human approval: {proposal.summary}. "
                      "NOT executed. Tell the user it is waiting for approval.")
