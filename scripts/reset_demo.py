"""Put everything back to the start of the demo.

Removes the live-update endpoint, rebuilds Ledgerly's MCP server without it,
and discards every change made to either system (disabled accounts, revoked
roles). Run it before each rehearsal.

    python -m scripts.reset_demo
"""
from __future__ import annotations

import sys

from agent.config import systems
from scripts import build_mcp, live_update


def main() -> int:
    if live_update.is_applied():
        live_update.remove()
        if build_mcp.main(["--system", "ledgerly", "--discover"]) != 0:
            return 1
    for spec in systems().values():
        state = spec.path / ".state.json"
        if state.exists():
            state.unlink()
            print(f"Restored {spec.key} seed data")
    print("Reset: 12 tools, seed data, no activity endpoint.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
