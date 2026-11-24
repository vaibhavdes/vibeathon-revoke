"""The console's no-model paths: recorded runs, replay, evidence files (what a container without a key serves)."""
from __future__ import annotations


from fastapi.testclient import TestClient

from web import server

client = TestClient(server.app)


def test_recordings_cover_every_demo_question():
    recs = client.get("/api/recordings").json()["recordings"]
    questions = " ".join(r["question"] for r in recs if r["source"] == "sample")
    for words in ("left the company", "changed jobs", "conflicting roles", "dormant or shared",
                  "after their last day", "Disable the Ledgerly accounts"):
        assert words in questions, words


def test_evidence_pack_is_served_and_hashes_match():
    import hashlib
    run_id = client.get("/api/recordings").json()["recordings"][0]["id"]
    files = client.get(f"/evidence/{run_id}/manifest.json").json()["files"]
    assert "report.md" in files
    for name, digest in files.items():
        body = client.get(f"/evidence/{run_id}/{name}").content
        assert hashlib.sha256(body).hexdigest() == digest, name


def test_evidence_route_refuses_other_files():
    run_id = client.get("/api/recordings").json()["recordings"][0]["id"]
    assert client.get(f"/evidence/{run_id}/..%2F..%2F.env").status_code == 404
    assert client.get("/evidence/..%2F..%2Fagent/report.md").status_code == 404
    assert client.get(f"/evidence/{run_id}/notes.txt").status_code == 404


def test_replay_unknown_run_is_404():
    assert client.post("/api/replay/does-not-exist").status_code == 404


def test_events_reach_only_the_tab_that_started_them():
    import asyncio

    async def scenario():
        bus = server.Broadcaster()
        bus.loop = asyncio.get_running_loop()
        a, b, any_tab = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        bus.queues.update({a: "tab-a", b: "tab-b", any_tab: ""})
        bus.publish({"type": "final"}, "tab-a")
        bus.publish({"type": "state"})              # no client: every tab
        await asyncio.sleep(0)
        return a.qsize(), b.qsize(), any_tab.qsize()

    assert asyncio.run(scenario()) == (2, 1, 1)
