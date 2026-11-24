"""Start everything with one command: both legacy systems and the Revoke console.

    python -m scripts.start                         # http://127.0.0.1:8700
    python -m scripts.start --host 0.0.0.0 --port 7860 --fresh   # what the container runs

--fresh restores the seed data first, so every start tells the same story.
Ctrl-C stops all three processes.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

from agent.config import ROOT, systems
from scripts.run_systems import wait_healthy


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8700")))
    parser.add_argument("--fresh", action="store_true", help="restore seed data and remove the live update first")
    args = parser.parse_args(argv)

    if args.fresh:
        subprocess.run([sys.executable, "-m", "scripts.reset_demo"], cwd=ROOT, check=False)

    procs = []
    for spec in systems().values():
        cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(spec.port),
               "--reload", "--log-level", "warning"]
        procs.append(subprocess.Popen(cmd, cwd=spec.path))
    for spec in systems().values():
        print(f"{spec.key:<10} {spec.base_url}  {'up' if wait_healthy(spec.base_url, 40) else 'NOT RESPONDING'}",
              flush=True)

    web = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.server:app", "--host", args.host,
                            "--port", str(args.port), "--log-level", "warning"], cwd=ROOT)
    procs.append(web)
    print(f"Revoke console  http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}", flush=True)

    def stop(*_):
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # If any process dies, stop the rest so the container restarts cleanly.
    while True:
        for p in procs:
            if p.poll() is not None:
                print(f"process exited ({p.args[2:5]}), stopping", flush=True)
                stop()
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
