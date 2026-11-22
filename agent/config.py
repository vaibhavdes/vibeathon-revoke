"""Shared settings: where things are, which systems exist, what day the review runs."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# .env wins over the shell, so only keys given to this project are used.
load_dotenv(ROOT / ".env", override=True)

EXPOSURE_FILE = ROOT / "mcp" / "exposure.json"
EVIDENCE_DIR = ROOT / "evidence" / "runs"

# The review is pinned to a date so the demo data always tells the same story.
REVIEW_DATE = os.getenv("REVIEW_DATE", "2026-11-24")


@dataclass(frozen=True)
class SystemSpec:
    key: str
    path: Path
    server_name: str
    namespace: str
    port: int
    raw: dict

    @property
    def base_url(self) -> str:
        return os.getenv(f"{self.key.upper()}_URL", f"http://127.0.0.1:{self.port}")

    def endpoints(self, include_live: bool = False) -> list[dict]:
        items = list(self.raw["expose"])
        if include_live:
            items += self.raw.get("live_demo", [])
        return items

    def write_tools(self) -> set[str]:
        items = self.raw["expose"] + self.raw.get("live_demo", [])
        return {e.get("as", e["tool"]) for e in items if e.get("access") == "write"}

    def server_file(self) -> Path | None:
        """The MCP server Synapse generated most recently for this system."""
        files = sorted(self.path.glob("synapse_auto_server*.py"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None


def load_exposure() -> dict:
    return json.loads(EXPOSURE_FILE.read_text())


def systems() -> dict[str, SystemSpec]:
    out = {}
    for key, raw in load_exposure()["systems"].items():
        out[key] = SystemSpec(key=key, path=ROOT / raw["path"], server_name=raw["server_name"],
                              namespace=raw["namespace"], port=raw["port"], raw=raw)
    return out


TOOL_RE = re.compile(r'@mcp\.tool\(name="([^"]+)"')


def generated_tool_names(server_file: Path) -> list[str]:
    return TOOL_RE.findall(server_file.read_text())


def _namespace(role: str, default: str) -> str:
    for raw in load_exposure()["systems"].values():
        if raw.get("role") == role:
            return raw["namespace"]
    return default


def _key(role: str, default: str) -> str:
    for key, raw in load_exposure()["systems"].items():
        if raw.get("role") == role:
            return key
    return default


# Tool-name prefixes of the HR system and of the app under review (set by "role" in exposure.json).
HR = _namespace("hr", "hr")
APP = _namespace("app", "ledgerly")
APP_KEY = _key("app", "ledgerly")
