"""The agent loop end to end, with a scripted model and the two systems in-process.

No network and no API key: the "model" is a fixed list of replies, and the MCP
hub is replaced by one that routes each tool to the same endpoint Synapse
exposes (from mcp/exposure.json), served by FastAPI's TestClient. What is under
test is everything Revoke does around the model: the join, verification of
findings, refusal of writes, the policy gate, approval, execution and the
verification read afterwards.
"""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

import agent.evidence as evidence
import agent.revoke as revoke
from agent.config import systems
from agent.hub import HubTool
from agent.llm import Reply


class FakeHub:
    """Same interface as McpHub; each tool calls its endpoint through TestClient."""

    def __init__(self, clients):
        self.clients = clients
        self.tools = {}
        for key, spec in systems().items():
            for e in spec.endpoints():
                q = f"{spec.namespace}__{e['tool']}"
                params = re.findall(r"{(\w+)}", e["endpoint"])
                props = {p: {"type": "string"} for p in params}
                self.tools[q] = HubTool(q, key, e["tool"], e["why"], {"type": "object", "properties": props},
                                        e["access"], e["endpoint"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def is_write(self, qualified):
        tool = self.tools.get(qualified)
        return tool is None or tool.access != "read"

    async def call(self, qualified, arguments=None):
        tool = self.tools.get(qualified)
        if tool is None:
            return {"error": f"Unknown tool {qualified}."}, True
        args = dict(arguments or {})
        method, path = tool.endpoint.split(" ", 1)
        for p in re.findall(r"{(\w+)}", path):
            path = path.replace("{" + p + "}", str(args.pop(p)))
        client = self.clients[tool.system]
        resp = client.request(method, path, json=args if method == "POST" else None)
        return resp.json(), resp.status_code >= 400


class ScriptedLLM:
    """Returns the given turns in order. A turn is a list of (tool, args) or a final text."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.targets = []

    def complete(self, messages, tools, max_wait=300):
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return Reply(content=turn, tool_calls=[], model="scripted", raw_message={"role": "assistant", "content": turn})
        calls = [SimpleNamespace(id=f"call_{len(self.turns)}_{i}",
                                 function=SimpleNamespace(name=n, arguments=json.dumps(a)))
                 for i, (n, a) in enumerate(turn)]
        raw = {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]}
        return Reply(content="", tool_calls=calls, model="scripted", raw_message=raw)


@pytest.fixture
def make_agent(peoplehub, ledgerly, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-used")
    monkeypatch.setattr(evidence, "EVIDENCE_DIR", tmp_path / "runs")
    monkeypatch.setattr(revoke, "McpHub", lambda: FakeHub({"peoplehub": peoplehub, "ledgerly": ledgerly}))

    def make(turns):
        events = []
        a = revoke.ReviewAgent(on_event=events.append)
        a.llm = ScriptedLLM(turns)
        a.events = events
        return a
    return make


READ_ACCOUNTS = [("ledgerly__list_active_accounts", {})]
LOOKUP = [("hr__lookup_employees_by_email", {"emails": ["rahul.patel@tamarind.example"]})]


def test_findings_are_verified_against_the_data(make_agent):
    agent = make_agent([
        READ_ACCOUNTS, LOOKUP,
        [("submit_findings", {"findings": [
            {"category": "leaver_active_access", "username": "rpatel", "severity": "low", "evidence": "left"},
            # Hallucinations the model has really made in testing:
            {"category": "leaver_active_access", "username": "skulkarni", "severity": "high", "evidence": "left"},
            {"category": "leaver_active_access", "username": "smenon", "severity": "high", "evidence": "left"},
            {"category": "mover_retained_privilege", "username": "pnair", "role": "TREASURY_RELEASE",
             "severity": "high", "evidence": "kept role"},
        ]})],
        "Rahul Patel left but can still approve payments.",
    ])
    run = asyncio.run(agent.ask("Who has left but still has an active Ledgerly account?"))

    assert [f["username"] for f in run.findings] == ["rpatel"]
    rahul = run.findings[0]
    assert rahul["facts"]["termination_date"] == "2026-11-03"       # copied from PeopleHub, not the model
    assert rahul["severity"] == "critical"                             # set by policy: payment rights
    assert rahul["certifier"]                                          # the manager in PeopleHub
    rejected = {r["username"]: r["rejected_because"] for r in run.rejected}
    assert set(rejected) == {"skulkarni", "smenon", "pnair"}
    assert "active" in rejected["skulkarni"]                           # the rehire is not a leaver
    # The evidence pack is complete and replayable.
    for name in ("report.md", "certifications.md", "findings.csv", "trace.jsonl", "events.jsonl", "manifest.json"):
        assert (run.folder / name).is_file()
    assert "rpatel" in (run.folder / "certifications.md").read_text()
    replay = [json.loads(line)["event"]["type"] for line in (run.folder / "events.jsonl").read_text().splitlines()]
    assert replay[0] == "start" and replay[-1] == "final"


def test_the_model_cannot_write_or_queue_changes_unasked(make_agent, ledgerly):
    agent = make_agent([
        READ_ACCOUNTS,
        [("ledgerly__disable_account", {"username": "rpatel", "reason": "left", "ticket": "X"}),
         ("propose_changes", {"changes": [{"action": "disable_account", "username": "rpatel", "reason": "left"}]})],
        [("submit_findings", {"findings": []})],
        "Done.",
    ])
    run = asyncio.run(agent.ask("Who has left the company?"))
    assert run.proposals == []
    assert ledgerly.get("/users/rpatel").json()["account"]["status"] == "active"


def test_policy_gate_then_human_approval_then_verified_change(make_agent, ledgerly):
    agent = make_agent([
        READ_ACCOUNTS, LOOKUP,
        [("propose_changes", {"changes": [
            {"action": "disable_account", "username": "rpatel", "reason": "Leaver: terminated 2026-11-03"},
            {"action": "disable_account", "username": "svc_batch_payments", "reason": "no owner"},
            {"action": "revoke_role", "username": "rgupta", "role": "GL_ADMIN", "reason": "mover"},
        ]})],
        [("submit_findings", {"findings": []})],
        "Queued one change for approval.",
    ])
    run = asyncio.run(agent.ask("Disable Rahul Patel and svc_batch_payments, and remove GL_ADMIN from rgupta."))

    assert [(p.tool.split("__")[1], p.arguments["username"]) for p in run.proposals] == [("disable_account", "rpatel")]
    blocked = " ".join(b["message"] for b in run.blocked)
    assert "svc_batch_payments" in blocked and "rgupta" in blocked     # service account; role not held
    assert ledgerly.get("/users/rpatel").json()["account"]["status"] == "active"   # nothing runs before approval

    done = asyncio.run(agent.decide([run.proposals[0].id], True, "Test Approver"))
    assert done[0].status == "executed" and done[0].verification["ok"]
    assert done[0].decided_by == "Test Approver"
    assert ledgerly.get("/users/rpatel").json()["account"]["status"] == "disabled"


def test_movers_are_computed_in_code_and_answers_match_findings(make_agent):
    agent = make_agent([
        [("hr__list_job_changes_since", {"since_date": "2026-05-28"})],
        # The model records two of the three movers, but its answer names all three:
        [("submit_findings", {"findings": [
            {"category": "mover_retained_privilege", "username": "rgupta", "role": "PAYMENT_APPROVER",
             "severity": "high", "evidence": "moved to Sales"},
            {"category": "mover_retained_privilege", "username": "fsheikh", "role": "TREASURY_RELEASE",
             "severity": "high", "evidence": "moved to HR"}]})],
        "rgupta, fsheikh and nverma kept old-job roles. Checked and not flagged: sreddy (promotion).",
        # After the coverage nudge it records the missing one:
        [("submit_findings", {"findings": [
            {"category": "mover_retained_privilege", "username": "nverma", "role": "GL_ADMIN",
             "severity": "high", "evidence": "moved to Procurement"}]})],
        "rgupta, fsheikh and nverma kept old-job roles.",
    ])
    run = asyncio.run(agent.ask("Who changed jobs but kept privileges from their old job?"))

    # The code read the accounts and role catalog itself, and built the mover view.
    tools_called = [t["tool"] for t in run.trace]
    assert "ledgerly__list_active_accounts" in tools_called and "ledgerly__get_role_catalog" in tools_called
    movers = {(f["username"], f["role"]) for f in run.findings}
    assert movers == {("rgupta", "PAYMENT_APPROVER"), ("fsheikh", "TREASURY_RELEASE"), ("nverma", "GL_ADMIN")}
    assert not any(f["username"] == "sreddy" for f in run.findings)


def test_dormant_and_shared_accounts_with_their_exceptions(make_agent):
    agent = make_agent([
        READ_ACCOUNTS, LOOKUP,
        [("submit_findings", {"findings": [
            {"category": "dormant_account", "username": "fmishra", "severity": "low", "evidence": "no sign-in since May"},
            {"category": "dormant_account", "username": "aagarwal", "severity": "low", "evidence": "never signed in"},
            {"category": "shared_account", "username": "finops_shared", "severity": "low", "evidence": "team login"},
            # Must be rejected: on approved leave; account 14 days old; a shared login called an unknown person.
            {"category": "dormant_account", "username": "adeshmukh", "severity": "high", "evidence": "inactive"},
            {"category": "dormant_account", "username": "mkhanna", "severity": "high", "evidence": "never used"},
            {"category": "unknown_identity", "username": "finops_shared", "severity": "high", "evidence": "no HR"},
        ]})],
        "Two dormant accounts and one shared login.",
    ])
    run = asyncio.run(agent.ask("Which accounts are dormant or shared?"))
    kept = {(f["category"], f["username"]): f for f in run.findings}
    assert set(kept) == {("dormant_account", "fmishra"), ("dormant_account", "aagarwal"),
                         ("shared_account", "finops_shared")}
    assert kept[("dormant_account", "fmishra")]["severity"] == "high"      # VENDOR_ADMIN: high-risk role
    rejected = {(r["category"], r["username"]): r["rejected_because"] for r in run.rejected}
    assert "leave" in rejected[("dormant_account", "adeshmukh")]
    assert "within policy" in rejected[("dormant_account", "mkhanna")]
    assert "shared" in rejected[("unknown_identity", "finops_shared")]


def test_code_points_out_a_skipped_leaver(make_agent):
    leavers = ["rpatel", "pnair", "ashah", "miyer"]          # the model forgets vrao
    agent = make_agent([
        READ_ACCOUNTS, LOOKUP,
        [("submit_findings", {"findings": [
            {"category": "leaver_active_access", "username": u, "severity": "high", "evidence": "left"}
            for u in leavers]})],
        [("submit_findings", {"findings": [
            {"category": "leaver_active_access", "username": "vrao", "severity": "high", "evidence": "left"}]})],
        "Five leavers still have access.",
    ])
    run = asyncio.run(agent.ask("Who has left but still has an active Ledgerly account?"))
    pointed = [e for e in agent.events if e["type"] == "completeness"]
    assert pointed and pointed[0]["missed"] == {"leaver_active_access": ["vrao"]}
    assert {f["username"] for f in run.findings} == set(leavers) | {"vrao"}
