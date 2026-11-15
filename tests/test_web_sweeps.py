import json
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from cthulhu.web import app as web  # noqa: E402
from cthulhu.web.sweeps import SweepManager  # noqa: E402

client = TestClient(web.app, base_url="http://127.0.0.1")

BASE = {
    "target": {"provider": "mock", "model": "qwen3:8b"}, "judge": {"provider": "mock"},
    "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
    "attacker": {"strategy": "corpus", "endpoint": {"provider": "mock"},
                 "objectives": ["leak_canary"], "max_turns": 40},
}


@pytest.fixture(autouse=True)
def fresh_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "sweeps", SweepManager(tmp_path))
    return tmp_path


def _body(name="demo", **over):
    body = {"name": name, "base": BASE, "repeats": 1, "workers": 1,
            "grid": {"guardrail": ["none", "output"], "mode": ["isolated", "campaign"]}}
    body.update(over)
    return body


def _finish(sweep_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        info = client.get(f"/sweeps/{sweep_id}").json()
        if info["status"] != "running":
            return info
        time.sleep(0.02)
    raise AssertionError("the sweep did not finish")


def test_plan_then_start_then_read_the_results():
    plan = client.post("/sweeps/plan", json=_body()).json()
    assert plan["runs"] == 4 and plan["pending"] == 4 and plan["state"] == "new" and plan["max_target_calls"] > 0
    assert client.get("/sweeps").json() == {"sweeps": []}                 # planning started nothing

    sweep_id = client.post("/sweeps", json=_body()).json()["sweep_id"]
    assert sweep_id == "demo"
    info = _finish(sweep_id)
    assert info["status"] == "done" and info["done"] == 4 and info["total"] == 4

    listed = client.get("/sweeps").json()["sweeps"]
    assert [s["id"] for s in listed] == ["demo"]
    data = client.get("/sweeps/demo/matrix").json()
    assert data["runs"] == 4 and data["matrix"]["output_stage"] is True
    rows = {(r["key"]["guardrail"], r["key"]["mode"]): r for r in data["asr"]["config"]}
    assert rows[("output", "isolated")]["proven"] == 0 and rows[("output", "isolated")]["caught"] > 0


def test_the_event_stream_replays_and_ends_when_the_sweep_does():
    sweep_id = client.post("/sweeps", json=_body(grid={"guardrail": ["none"], "mode": ["campaign"]})).json()["sweep_id"]
    with client.stream("GET", f"/sweeps/{sweep_id}/events") as resp:
        assert resp.status_code == 200
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    assert [e["kind"] for e in events] == ["run_started", "run_done", "sweep_finished"]
    assert events[-1]["status"] == "done"
    with client.stream("GET", f"/sweeps/{sweep_id}/events") as again:    # a late reader gets it all
        replay = [json.loads(line[6:]) for line in again.iter_lines() if line.startswith("data: ")]
    assert replay == events


def test_a_second_sweep_while_one_runs_is_refused():
    first = client.post("/sweeps", json=_body("first", repeats=40, grid={"guardrail": ["none"], "mode": ["campaign"]}))
    assert first.status_code == 200
    second = client.post("/sweeps", json=_body("second"))
    assert second.status_code == 409 and "already running" in second.json()["detail"]
    assert client.post("/sweeps/first/cancel").json() == {"cancelled": True}
    assert _finish("first")["status"] == "cancelled"
    assert client.post("/sweeps", json=_body("second")).status_code == 200
    _finish("second")


def test_a_different_plan_under_the_same_name_is_a_409():
    client.post("/sweeps", json=_body())
    _finish("demo")
    other = {**BASE, "attacker": {**BASE["attacker"], "max_turns": 5}}
    resp = client.post("/sweeps", json=_body(base=other))
    assert resp.status_code == 409 and "different plan" in resp.json()["detail"]
    assert client.post("/sweeps/plan", json=_body(base=other)).json()["state"] == "different"


def test_bad_requests_are_400_or_422_and_unknown_sweeps_404():
    assert client.post("/sweeps", json=_body(grid={"guardrail": ["bogus"]})).status_code == 422
    assert client.post("/sweeps", json=_body(grid={"target": ["no-colon"]})).status_code == 400
    assert client.post("/sweeps", json=_body(workers=9)).status_code == 422
    assert client.post("/sweeps", json=_body(repeats=0)).status_code == 422
    assert client.post("/sweeps", json=_body(name="///")).status_code == 400
    assert client.post("/sweeps/plan", json=_body(name="///")).status_code == 400
    for path in ("/sweeps/nope", "/sweeps/nope/matrix", "/sweeps/nope/events"):
        assert client.get(path).status_code == 404
    assert client.post("/sweeps/nope/cancel").status_code == 404


def test_the_matrix_is_not_ready_before_any_run_has_finished(fresh_manager):
    (fresh_manager / "empty" / "runs").mkdir(parents=True)
    (fresh_manager / "empty" / "sweep.json").write_text('{"cells": [], "repeats": 1}', encoding="utf-8")
    assert client.get("/sweeps/empty/matrix").status_code == 409
    assert client.get("/sweeps/empty/events").status_code == 409          # on disk only, not running here


def test_a_sweep_on_disk_shows_up_after_a_restart(fresh_manager):
    client.post("/sweeps", json=_body())
    _finish("demo")
    web.sweeps = SweepManager(fresh_manager)                              # a new server session
    info = client.get("/sweeps/demo").json()
    assert info["status"] == "idle" and info["live"] is False and info["done"] == 4
    assert client.get("/sweeps/demo/matrix").json()["runs"] == 4
    resumed = client.post("/sweeps", json=_body()).json()["sweep_id"]
    assert _finish(resumed)["skipped"] == 4


def test_paths_in_the_config_are_ignored_over_http(fresh_manager):
    secret = fresh_manager / "secret.txt"
    secret.write_text("do not read", encoding="utf-8")
    only_file = {k: v for k, v in BASE.items() if k != "system_prompt_text"}
    resp = client.post("/sweeps", json=_body(base={**only_file, "system_prompt_file": str(secret)}))
    assert resp.status_code == 400 and "as text" in resp.json()["detail"]
    custom = {**BASE, "corpus_dir": str(fresh_manager), "system_prompt_file": str(secret)}
    client.post("/sweeps", json=_body(base=custom, grid={"guardrail": ["none"], "mode": ["isolated"]}))
    info = _finish("demo")
    assert info["status"] == "done"
    saved = "".join(p.read_text(encoding="utf-8") for p in (fresh_manager / "demo").rglob("*.json"))
    assert "do not read" not in saved and str(fresh_manager) not in saved


def test_the_folder_is_the_slug_of_the_name_and_stays_under_the_root(fresh_manager):
    sweep_id = client.post("/sweeps", json=_body("../../My Sweep")).json()["sweep_id"]
    assert sweep_id == "my-sweep"
    _finish(sweep_id)
    assert (fresh_manager / "my-sweep" / "sweep.json").is_file()
    assert not (fresh_manager.parent / "My Sweep").exists()


def test_sweeps_are_only_served_to_localhost():
    stranger = TestClient(web.app, base_url="http://evil.example")
    assert stranger.get("/sweeps").status_code == 400
    assert stranger.post("/sweeps/plan", json=_body()).status_code == 400
    assert stranger.get("/sweeps/demo/matrix").status_code == 400
    assert "access-control-allow-origin" not in client.get("/sweeps").headers
