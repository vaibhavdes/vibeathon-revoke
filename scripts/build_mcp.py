"""Turn each legacy system into an MCP server with Synapse.

Runs one `synapse build --local --auto` per system, exposing only the
endpoints listed in mcp/exposure.json, then checks that the tools Synapse
generated are exactly the ones we asked for. The generated files are never
edited.

    python -m scripts.build_mcp                       # both systems
    python -m scripts.build_mcp --system ledgerly --discover --include-live
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from agent.config import ROOT, generated_tool_names, systems

SYNAPSE = ROOT / "node_modules" / ".bin" / "synapse"


def build(spec, discover: bool, include_live: bool) -> bool:
    endpoints = spec.endpoints(include_live)
    selection = ",".join(e["endpoint"] for e in endpoints)
    cmd = [str(SYNAPSE), "build", "--local", "--auto", "--yes",
           "--server-name", spec.server_name, "--endpoints", selection]
    if discover:
        cmd.append("--discover")
    shown = " ".join(f'"{c}"' if " " in c else c for c in cmd[1:])
    print(f"\n[{spec.key}] synapse {shown}")
    env = {**os.environ, "SYNAPSE_TELEMETRY": "0",
           # Auto mode without --smart-names calls no model, but the CLI still
           # asks for a provider key; Ollama needs none.
           "SYNAPSE_LLM_PROVIDER": os.getenv("SYNAPSE_LLM_PROVIDER", "ollama")}
    before = {p.name: p.stat().st_mtime for p in spec.path.glob("synapse_auto_server*.py")}
    proc = subprocess.run(cmd, cwd=spec.path, env=env, capture_output=True, text=True)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        print(output[-2500:])
        print(f"[{spec.key}] FAILED (exit {proc.returncode}). The previous server file is kept.")
        return False

    newest = spec.server_file()
    if newest is None or before.get(newest.name) == newest.stat().st_mtime:
        print(output[-1500:])
        print(f"[{spec.key}] FAILED: Synapse did not write a server file.")
        return False
    # Synapse 0.1.9 writes synapse_auto_server_2.py, _3.py... on rebuilds. Keep
    # one stable path so the MCP client config never changes.
    canonical = spec.path / "synapse_auto_server.py"
    if newest != canonical:
        shutil.move(newest, canonical)
    for extra in spec.path.glob("synapse_auto_server_*.py"):
        extra.unlink()

    got = generated_tool_names(canonical)
    want = [e["tool"] for e in endpoints]
    if sorted(got) != sorted(want):
        print(f"[{spec.key}] FAILED: tools {sorted(got)} != expected {sorted(want)}")
        return False
    print(f"[{spec.key}] OK: {len(got)} tools -> {canonical.relative_to(ROOT)}")
    for name in got:
        print(f"    - {spec.namespace}__{name}")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--system", choices=["all", *systems()], default="all")
    parser.add_argument("--discover", action="store_true", help="rescan the code (needed after adding an endpoint)")
    parser.add_argument("--include-live", action="store_true", help="also expose the live-demo endpoints")
    args = parser.parse_args(argv)
    if not SYNAPSE.exists():
        print("Synapse CLI not found. Run: npm install")
        return 1
    chosen = [s for k, s in systems().items() if args.system in ("all", k)]
    ok = all([build(s, args.discover, args.include_live) for s in chosen])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
