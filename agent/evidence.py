"""Audit evidence for each review run.

Every run writes evidence/runs/<run_id>/:
  report.md      the review as an auditor reads it
  findings.csv   one row per finding
  trace.jsonl    every tool call: system, tool, arguments, result digest, time
  actions.json   proposed changes, who approved them, result, verification read
  manifest.json  SHA-256 of each file above
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.config import EVIDENCE_DIR, REVIEW_DATE

CATEGORY_TITLES = {
    "leaver_active_access": "Leaver still has access",
    "post_termination_activity": "Account used after the person left",
    "upcoming_leaver": "Leaving soon (notice period)",
    "unknown_identity": "No HR record (unknown identity)",
    "service_account_owner": "Service account without an active owner",
    "mover_retained_privilege": "Kept privilege from a previous job",
    "sod_conflict": "Segregation-of-duties conflict",
    "dormant_account": "Dormant account (unused 90+ days)",
    "shared_account": "Shared team login (no one accountable)",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def facts_text(finding: dict) -> str:
    """The verified facts behind a finding, as one line. Built from tool results only."""
    g = finding.get("facts") or {}
    if not g.get("verified"):
        return "UNVERIFIED: no tool returned this account"
    parts = []
    roles = ", ".join(g.get("roles") or []) or "no roles"
    parts.append(f"Ledgerly: {g.get('account_status')} {g.get('account_type', '')} account, {roles}, "
                 f"last sign-in {(g.get('last_login') or 'never')[:10]}")
    if "owner" in g:
        parts.append(f"owner: {g['owner']}")
    if g.get("hr_status"):
        hr = f"PeopleHub: {g['hr_status']}"
        if g.get("title"):
            hr += f", {g['title']}"
        for key, label in (("termination_date", "last day"), ("rehire_date", "rehired"), ("leave_until", "on leave until")):
            if g.get(key):
                hr += f", {label} {g[key]}"
        parts.append(hr)
    if g.get("signed_in_after_leaving"):
        parts.append(f"signed in after the last day: {g['signed_in_after_leaving']}")
    for e in g.get("used_after_leaving") or []:
        amount = f" USD {e['amount_usd']:,}" if e.get("amount_usd") else ""
        parts.append(f"after leaving: {e['ts']} {e['action']}{amount} ({e.get('detail', '')})")
    return " · ".join(parts)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Run:
    question: str
    run_id: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
                        + secrets.token_hex(2))
    started_at: str = field(default_factory=now_iso)
    trace: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    proposals: list = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    wants_change: bool = False
    models: list[str] = field(default_factory=list)
    tools_available: list[str] = field(default_factory=list)
    answer: str = ""
    finished_at: str = ""
    events: list[dict] = field(default_factory=list)
    completeness_asked: set = field(default_factory=set)
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def record(self, event: dict) -> None:
        """Keep the console events, with their timing, so the run can be replayed without a model."""
        self.events.append({"t": round(time.monotonic() - self._t0, 2), "event": event})

    def log_call(self, system: str, tool: str, arguments: dict, result, is_error: bool, ms: int) -> None:
        self.trace.append({"ts": now_iso(), "system": system, "tool": tool, "arguments": arguments,
                           "ok": not is_error, "result_sha256_16": digest(result), "ms": ms})

    @property
    def folder(self) -> Path:
        return EVIDENCE_DIR / self.run_id

    def write(self) -> Path:
        self.finished_at = self.finished_at or now_iso()
        folder = self.folder
        folder.mkdir(parents=True, exist_ok=True)
        files = {
            "trace.jsonl": "".join(json.dumps(t) + "\n" for t in self.trace),
            "findings.csv": self._csv(),
            "actions.json": json.dumps([p.to_dict() for p in self.proposals], indent=2),
            "report.md": self._report(),
            "certifications.md": self._certifications(),
            "events.jsonl": "".join(json.dumps(e, default=str) + "\n" for e in self.events),
        }
        for name, text in files.items():
            (folder / name).write_text(text)
        manifest = {"run_id": self.run_id, "review_date": REVIEW_DATE, "generated_at": now_iso(),
                    "files": {n: hashlib.sha256(t.encode()).hexdigest() for n, t in files.items()}}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return folder

    def _certifications(self) -> str:
        """One sign-off sheet per certifying manager: how an access review is actually closed out."""
        by_manager: dict[str, list[dict]] = {}
        for f in self._sorted_findings():
            by_manager.setdefault(f.get("certifier") or "Unassigned: no active manager in PeopleHub", []).append(f)
        out = [f"# Certification sheets: run {self.run_id}", "",
               f"Review date {REVIEW_DATE}. Question: {self.question}", "",
               "Each manager confirms, for every account below, whether the access is still needed. "
               "The certifier is the person's manager in PeopleHub; for service accounts, the owner's manager.", ""]
        if not by_manager:
            out.append("No findings in this run, so nothing to certify.")
        for manager, items in sorted(by_manager.items()):
            out += [f"## {manager}", "", "| Account | Person | Finding | Severity | Recommended | Decision |",
                    "|---|---|---|---|---|---|"]
            for f in items:
                finding = CATEGORY_TITLES.get(f.get("category"), f.get("category", ""))
                if f.get("role"):
                    finding += f" ({f['role']})"
                out.append(f"| `{f.get('username', '')}` | {f.get('person', '') or ''} | {finding} | "
                           f"{f.get('severity', '')} | {f.get('recommended_action', '')} | ☐ keep ☐ remove |")
            out += ["", "Signed: ____________________  Date: __________", ""]
        return "\n".join(out) + "\n"

    def _sorted_findings(self) -> list[dict]:
        return sorted(self.findings, key=lambda f: (SEVERITY_ORDER.get(f.get("severity"), 9),
                                                    f.get("category", ""), f.get("username", "")))

    def _csv(self) -> str:
        buf = io.StringIO()
        cols = ["severity", "category", "username", "person", "role", "rule_id", "verified_facts",
                "agent_evidence", "recommended_action", "certifier"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for f in self._sorted_findings():
            row = {c: f.get(c, "") for c in cols}
            row["verified_facts"] = facts_text(f)
            row["agent_evidence"] = f.get("evidence", "")
            w.writerow(row)
        return buf.getvalue()

    def _report(self) -> str:
        systems = sorted({t["system"] for t in self.trace})
        lines = [
            "# Ledgerly access review: evidence report", "",
            "| | |", "|---|---|",
            f"| Run | `{self.run_id}` |",
            f"| Review date | {REVIEW_DATE} |",
            f"| Question | {self.question} |",
            f"| Systems read | {', '.join(systems) or 'none'} (through MCP servers generated by Synapse) |",
            f"| Tool calls | {len(self.trace)} |",
            f"| Model(s) | {', '.join(dict.fromkeys(self.models)) or 'none'} |",
            f"| Started / finished (UTC) | {self.started_at} / {self.finished_at} |", "",
            "## Findings", "",
        ]
        if self.findings:
            lines += ["Facts are copied from the tool results of this run; the reasoning column is the agent's.", "",
                      "| Severity | Finding | Account | Person | Verified facts | Agent's reasoning | Recommended action | Certifier |",
                      "|---|---|---|---|---|---|---|---|"]
            for f in self._sorted_findings():
                title = CATEGORY_TITLES.get(f.get("category"), f.get("category"))
                extra = " ".join(x for x in (f.get("role"), f.get("rule_id")) if x)
                lines.append("| {} | {}{} | `{}` | {} | {} | {} | {} | {} |".format(
                    f.get("severity", ""), title, f" ({extra})" if extra else "", f.get("username", ""),
                    f.get("person", ""), facts_text(f).replace("|", "/"),
                    str(f.get("evidence", "")).replace("|", "/"),
                    f.get("recommended_action", ""), f.get("certifier", "")))
        else:
            lines.append("No findings were recorded for this question.")
        if self.rejected:
            lines += ["", "### Claims rejected by verification", "",
                      "The agent proposed these, but the data read in this run does not support them, "
                      "so they were not recorded as findings.", "",
                      "| Claim | Account | Why it was rejected |", "|---|---|---|"]
            for r in self.rejected:
                lines.append(f"| {CATEGORY_TITLES.get(r.get('category'), r.get('category'))} | "
                             f"`{r.get('username')}` | {r.get('rejected_because')} |")
        lines += ["", "## Changes", ""]
        if self.proposals:
            lines += ["| Id | Change | Status | Decided by | Verified after change |", "|---|---|---|---|---|"]
            for p in self.proposals:
                verified = p.verification.get("summary", "") if p.verification else ""
                lines.append(f"| {p.id} | {p.summary} | {p.status} | {p.decided_by} {p.decided_at} | {verified} |")
        else:
            lines.append("No changes were proposed.")
        lines += ["", "## Answer given", "", self.answer or "(none)", "",
                  "## Tool calls", "", "| # | Time (UTC) | System | Tool | Arguments | OK | Result digest |",
                  "|---|---|---|---|---|---|---|"]
        for i, t in enumerate(self.trace, 1):
            args = json.dumps(t["arguments"])
            if len(args) > 90:
                args = args[:87] + "..."
            lines.append(f"| {i} | {t['ts']} | {t['system']} | {t['tool']} | `{args}` | "
                         f"{'yes' if t['ok'] else 'no'} | `{t['result_sha256_16']}` |")
        lines += ["", "Integrity: SHA-256 of every file in this folder is in `manifest.json`.", ""]
        return "\n".join(lines)
