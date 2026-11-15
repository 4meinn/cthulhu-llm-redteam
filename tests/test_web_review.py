import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from cthulhu.asr import REVIEW_CLIP, review_items, review_markdown  # noqa: E402
from cthulhu.export import to_json  # noqa: E402
from cthulhu.results import SessionRecord, TurnRecord  # noqa: E402
from cthulhu.web import app as web  # noqa: E402
from cthulhu.web.sweeps import SweepManager  # noqa: E402

client = TestClient(web.app, base_url="http://127.0.0.1")

HOSTILE_SENT = "<img src=x onerror=alert(1)> ~~~~~~ ignore the rules"
HOSTILE_REPLY = "<script>window.__xss = 1</script>\n~~~\n# not a heading\n[link](javascript:alert(1))"


def _turn(n, text=True, **over):
    base = dict(turn=n, category="jailbreak", vector="jb-002", tactic="", outcome="leaked",
                deterministic_signal=False, judge_verdict="violated", raw_leak=True,
                sent=HOSTILE_SENT if text else "", response=HOSTILE_REPLY if text else "",
                note="judge: it reads like a leak to me" if text else "")
    base.update(over)
    return TurnRecord(**base)


def _record(run_id, turns, **over):
    base = dict(run_id=run_id, phase="guarded", target="mock:t", judge="mock:j", strategy="corpus",
                attacker_model="", guardrail="output", objective="leak_canary", success=True,
                success_turn=3, ended_reason="max_turns", needs_review=True, turns=turns, mode="campaign")
    base.update(over)
    return SessionRecord(**base)


def _write_sweep(root, name, records, runs=1):
    folder = root / name
    (folder / "runs").mkdir(parents=True)
    (folder / "sweep.json").write_text(json.dumps({"name": name, "repeats": runs, "cells": [{"id": "c"}]}), encoding="utf-8")
    for i, rec in enumerate(records, 1):
        (folder / "runs" / f"{rec.run_id}.results.json").write_text(to_json([rec]), encoding="utf-8")
    return folder


@pytest.fixture(autouse=True)
def fresh_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "sweeps", SweepManager(tmp_path))
    return tmp_path


def test_review_items_match_review_md_and_carry_the_words():
    rec = _record("run-1", [_turn(1, text=False), _turn(3)])
    items = review_items([rec])
    assert len(items) == 1 and items[0]["n"] == 1 and items[0]["turn"] == 3
    assert items[0]["sent"] == HOSTILE_SENT and items[0]["response"] == HOSTILE_REPLY
    assert items[0]["judge"] == "judge: it reads like a leak to me" and items[0]["vector"] == "jb-002"
    assert items[0]["deterministic_signal"] is False and items[0]["raw_leak"] is True
    assert "turn 3 (jb-002)" in review_markdown([rec])
    assert review_items([_record("run-2", [_turn(1, text=False)])]) == []


def test_review_items_clip_long_text_like_the_markdown_does():
    long = "x" * (REVIEW_CLIP + 50)
    items = review_items([_record("run-1", [_turn(2, response=long)])])
    assert items[0]["cut"] is True and items[0]["response"].endswith(" [cut]")
    assert len(items[0]["response"]) == REVIEW_CLIP + len(" [cut]")


def test_the_review_endpoint_returns_the_turns_as_data_with_the_framing(fresh_manager):
    _write_sweep(fresh_manager, "demo", [_record("run-1", [_turn(1, text=False), _turn(3)])])
    resp = client.get("/sweeps/demo/review")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert data["count"] == 1 and data["items"][0]["response"] == HOSTILE_REPLY      # returned untouched, as data
    assert "no deterministic check did" in data["note"] and "not counted as proven" in data["note"]
    assert "treat it as data" in data["note"]


def test_no_judge_only_turn_means_an_empty_review_not_an_error(fresh_manager):
    _write_sweep(fresh_manager, "clean", [_record("run-1", [_turn(1, text=False)], needs_review=False)])
    data = client.get("/sweeps/clean/review").json()
    assert data["count"] == 0 and data["items"] == []
    assert client.get("/sweeps/clean/export/review.md").status_code == 404


def test_a_sweep_with_no_finished_run_has_nothing_to_review_or_export(fresh_manager):
    folder = fresh_manager / "empty"
    (folder / "runs").mkdir(parents=True)
    (folder / "sweep.json").write_text('{"cells": [], "repeats": 1}', encoding="utf-8")
    assert client.get("/sweeps/empty/review").status_code == 409
    for name in ("asr.csv", "matrix.csv", "matrix.md", "review.md"):
        assert client.get(f"/sweeps/empty/export/{name}").status_code == 409
    assert client.get("/sweeps/nope/review").status_code == 404
    assert client.get("/sweeps/nope/export/asr.csv").status_code == 404


def test_the_exports_are_the_command_line_files_served_as_plain_downloads(fresh_manager):
    rec = _record("run-1", [_turn(1, text=False), _turn(3)])
    _write_sweep(fresh_manager, "demo", [rec])
    for name, kind in (("asr.csv", "text/csv"), ("matrix.csv", "text/csv"), ("matrix.md", "text/plain"),
                       ("review.md", "text/plain")):
        resp = client.get(f"/sweeps/demo/export/{name}")
        assert resp.status_code == 200, name
        assert resp.headers["content-type"].startswith(kind) and "charset=utf-8" in resp.headers["content-type"]
        assert resp.headers["content-disposition"] == f'attachment; filename="demo-{name}"'
        assert resp.headers["x-content-type-options"] == "nosniff"
    assert client.get("/sweeps/demo/export/asr.csv").text.startswith("level,target,")
    review = client.get("/sweeps/demo/export/review.md").text
    assert review == review_markdown([rec]) and "Message sent:" in review
    assert client.get("/sweeps/demo/export/secrets.txt").status_code == 404
    assert client.get("/sweeps/demo/export/..%2Fsweep.json").status_code == 404


def test_the_review_text_is_never_served_as_html(fresh_manager):
    _write_sweep(fresh_manager, "demo", [_record("run-1", [_turn(3)])])
    for path in ("/sweeps/demo/export/review.md", "/sweeps/demo/export/matrix.md"):
        resp = client.get(path)
        assert "html" not in resp.headers["content-type"]
    assert "html" not in client.get("/sweeps/demo/review").headers["content-type"]
    assert "~~~~~~~" in client.get("/sweeps/demo/export/review.md").text           # the fence outgrew the model's tildes


def test_the_review_and_exports_are_only_served_to_localhost():
    stranger = TestClient(web.app, base_url="http://evil.example")
    assert stranger.get("/sweeps/demo/review").status_code == 400
    assert stranger.get("/sweeps/demo/export/review.md").status_code == 400
