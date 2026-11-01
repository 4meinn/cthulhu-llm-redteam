import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from cthulhu.web.app import app  # noqa: E402

CANARY = "NW-test-CANARY"
client = TestClient(app, base_url="http://127.0.0.1")


def _body(**over):
    body = {
        "target": {"provider": "mock"},
        "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}",
        "canary": CANARY,
        "guardrail": True,
        "attacker": {
            "strategy": "adaptive",
            "endpoint": {"provider": "mock"},
            "objectives": ["leak_canary"],
            "max_turns": 6,
        },
    }
    body.update(over)
    return body


def _drain_events(run_id):
    with client.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_full_run_flow():
    rid = client.post("/runs", json=_body()).json()["run_id"]
    events = _drain_events(rid)
    assert events[-1]["kind"] == "run_finished" and events[-1]["status"] == "done"
    assert any(e["kind"] == "turn" for e in events)

    status = client.get(f"/runs/{rid}").json()
    assert status["status"] == "done" and status["summary"]["baseline"]["met"] == 1

    assert client.get(f"/runs/{rid}/report").json()["baseline"][0]["success"] is True
    assert "Cthulhu multi-turn report" in client.get(f"/runs/{rid}/report.md").text


def test_missing_attacker_is_rejected():
    assert client.post("/runs", json=_body(attacker=None)).status_code == 400


def test_host_header_check_blocks_non_localhost():
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/").status_code == 200   # localhost is allowed


def test_unknown_run_is_404():
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/report").status_code == 404


def test_serves_the_ui():
    root = client.get("/")
    assert root.status_code == 200 and "<title>Cthulhu</title>" in root.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
