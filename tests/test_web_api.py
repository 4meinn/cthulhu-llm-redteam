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
    assert client.get(f"/runs/{rid}/report").json()["asr"]["config"][0]["n"] >= 1
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


def _start(**over):
    return client.post("/runs", json=_body(**over)).json()["run_id"]


def test_runs_are_listed_with_labels_and_a_plan():
    rid = _start(label="Mira")
    _drain_events(rid)
    entry = [r for r in client.get("/runs").json()["runs"] if r["id"] == rid][0]
    assert entry["label"] == "Mira" and entry["status"] == "done"
    assert entry["plan"] == {"objectives": 1, "phases": 2, "max_turns": 6}   # guardrail on means two phases
    assert entry["summary"]["baseline"]["met"] == 1


def test_an_unlabelled_run_gets_a_default_name():
    rid = _start()
    _drain_events(rid)
    label = client.get(f"/runs/{rid}").json()["label"]
    assert label.startswith("qwen3:8b #")


def test_the_list_keeps_start_order():
    first, second = _start(label="one"), _start(label="two")
    ids = [r["id"] for r in client.get("/runs").json()["runs"]]
    assert ids.index(first) < ids.index(second)
    _drain_events(first)
    _drain_events(second)


def test_cancel_all_leaves_finished_runs_alone():
    finished = _start()
    _drain_events(finished)
    reply = client.post("/runs/cancel-all").json()
    assert isinstance(reply["cancelled"], int)
    assert client.get(f"/runs/{finished}").json()["status"] == "done"


def test_results_endpoints_serve_json_and_csv():
    rid = client.post("/runs", json=_body()).json()["run_id"]
    _drain_events(rid)

    js = client.get(f"/runs/{rid}/results.json")
    assert js.status_code == 200 and js.headers["content-type"].startswith("application/json")
    sessions = js.json()["sessions"]
    assert [s["phase"] for s in sessions] == ["baseline", "guarded"]
    assert all(s["run_id"] == rid for s in sessions)

    cs = client.get(f"/runs/{rid}/results.csv")
    assert cs.status_code == 200 and cs.headers["content-type"].startswith("text/csv")
    lines = cs.text.strip().splitlines()
    assert lines[0].startswith("run_id,phase,target") and len(lines) == 1 + sum(
        len(s["turns"]) for s in sessions)


def test_results_endpoints_need_a_known_run_and_a_ready_report():
    assert client.get("/runs/nope/results.json").status_code == 404
    assert client.get("/runs/nope/results.csv").status_code == 404


def _wait_done(rid, timeout=30):
    import time
    end = time.time() + timeout
    while time.time() < end:
        info = client.get(f"/runs/{rid}").json()
        if info["status"] != "running":
            return info
        time.sleep(0.02)
    raise AssertionError("the run did not finish")


def test_runs_do_not_read_files_named_by_the_client(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read this", encoding="utf-8")
    only_file = _body(system_prompt_file=str(secret))
    del only_file["system_prompt_text"]
    resp = client.post("/runs", json=only_file)
    assert resp.status_code == 400 and "system prompt as text" in resp.json()["detail"]

    body = _body(system_prompt_file=str(secret), corpus_dir=str(tmp_path))
    body["attacker"] = {**body["attacker"], "strategy": "corpus", "max_turns": 3}
    rid = client.post("/runs", json=body).json()["run_id"]       # an empty corpus folder would fail a run
    info = _wait_done(rid)
    assert info["status"] == "done" and info["error"] is None
    events = _drain_events(rid)
    assert any(e["kind"] == "turn" for e in events)
    everything = json.dumps(events) + json.dumps(client.get(f"/runs/{rid}/report").json())
    assert "do not read this" not in everything and str(tmp_path) not in everything


def test_the_web_still_runs_a_normal_inline_config():
    rid = client.post("/runs", json=_body()).json()["run_id"]
    assert _wait_done(rid)["status"] == "done"
