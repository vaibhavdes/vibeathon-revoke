"""Start both legacy systems and keep them running (Ctrl-C stops them).

They run with auto-reload, so the live update (a new endpoint added to
Ledgerly) is served without restarting anything.

    python -m scripts.run_systems
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
import urllib.request

from agent.config import systems


def wait_healthy(url: str, timeout: float = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> int:
    procs = []
    for spec in systems().values():
        cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
               "--port", str(spec.port), "--reload", "--log-level", "warning"]
        procs.append((spec, subprocess.Popen(cmd, cwd=spec.path)))
    try:
        for spec, _ in procs:
            ok = wait_healthy(spec.base_url)
            print(f"{spec.key:<10} {spec.base_url}  {'up' if ok else 'NOT RESPONDING'}")
        print("Both systems are running. Ctrl-C to stop.")
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in procs:
            p.terminate()
        for _, p in procs:
            p.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
