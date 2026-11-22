"""The live update in the demo: a Ledgerly developer exposes the activity log.

Ledgerly has always recorded sign-ins and payment approvals but had no API
for them. `apply` adds GET /users/{username}/activity to systems/ledgerly/main.py
(the running service reloads by itself); `remove` takes it out again.

    python -m scripts.live_update apply
    python -m scripts.build_mcp --system ledgerly --discover --include-live
    python -m scripts.live_update remove
"""
from __future__ import annotations

import sys

from agent.config import ROOT

TARGET = ROOT / "systems" / "ledgerly" / "main.py"
SNIPPET = ROOT / "demo" / "activity_endpoint.py"
START = "# >>> live-update: account activity (inserted by scripts/live_update.py)"
END = "# <<< live-update"


def _split(text: str) -> tuple[str, str, str]:
    head, rest = text.split(START, 1)
    body, tail = rest.split(END, 1)
    return head, body, tail


def is_applied() -> bool:
    return "def get_account_activity" in _split(TARGET.read_text())[1]


def apply() -> None:
    if is_applied():
        print("Already applied: GET /users/{username}/activity is live.")
        return
    head, _, tail = _split(TARGET.read_text())
    TARGET.write_text(f"{head}{START}\n{SNIPPET.read_text().rstrip()}\n{END}{tail}")
    print("Added GET /users/{username}/activity to systems/ledgerly/main.py")


def remove() -> None:
    head, _, tail = _split(TARGET.read_text())
    TARGET.write_text(f"{head}{START}\n{END}{tail}")
    print("Removed the activity endpoint from systems/ledgerly/main.py")


def main(argv=None) -> int:
    action = (argv or sys.argv[1:] or ["status"])[0]
    if action == "apply":
        apply()
    elif action == "remove":
        remove()
    elif action == "status":
        print("applied" if is_applied() else "not applied")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
