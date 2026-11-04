import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from cthulhu import campaign  # noqa: E402
from cthulhu.campaign import run_attack_run, run_phase  # noqa: E402
from cthulhu.cli import main  # noqa: E402
from cthulhu.config import RunConfig  # noqa: E402
from cthulhu.report import build_phase_report, build_session_report, run_meta  # noqa: E402
from cthulhu.results import records_from_report  # noqa: E402
from cthulhu.sweep import SweepConfig, expand, run_sweep  # noqa: E402
from cthulhu.web.app import app  # noqa: E402
from cthulhu.web.manager import RunManager  # noqa: E402

http = TestClient(app, base_url="http://127.0.0.1")


def _cfg(strategy="corpus", **over):
    body = {
        "target": {"provider": "mock"}, "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
        "attacker": {"strategy": strategy, "endpoint": {"provider": "mock"},
                     "objectives": ["leak_canary"], "max_turns": 40},
    }
    body.update(over)
    return RunConfig.model_validate(body)


def _turns(sessions):
    return [t for s in sessions for t in s.turns]


def test_the_defaults_leave_every_existing_config_as_it_was():
    cfg = _cfg()
    assert cfg.guardrail is False and cfg.guardrail_mode == "input" and cfg.output_secrets == []


def test_each_mode_runs_the_stages_it_names():
    none = _turns(run_phase(_cfg(), guardrail="none", mode="isolated"))
    inp = _turns(run_phase(_cfg(), guardrail="input", mode="isolated"))
    out = _turns(run_phase(_cfg(), guardrail="output", mode="isolated"))
    both = _turns(run_phase(_cfg(), guardrail="both", mode="isolated"))

    assert not any(t.blocked or t.filtered for t in none) and any(t.leaked for t in none)
    assert any(t.blocked for t in inp) and not any(t.filtered for t in inp)
    assert not any(t.blocked for t in out) and any(t.filtered for t in out)
    assert any(t.blocked for t in both) and any(t.filtered for t in both)


def test_the_output_stage_leaves_no_deterministic_leak_and_catches_every_raw_one():
    turns = _turns(run_phase(_cfg(), guardrail="output", mode="isolated"))
    assert not any(t.leaked for t in turns)
    assert sum(t.raw_leaked for t in turns) == sum(t.filtered for t in turns) > 0
    assert all(t.filtered == t.raw_leaked for t in turns)        # here the two coincide by construction


def test_true_and_false_still_mean_the_configs_own_guarded_mode_and_none():
    cfg = _cfg(guardrail_mode="output")
    assert any(t.filtered for t in _turns(run_phase(cfg, guardrail=True, mode="isolated")))
    assert not any(t.filtered or t.blocked for t in _turns(run_phase(cfg, guardrail=False, mode="isolated")))


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode must be one of"):
        run_phase(_cfg(), guardrail="sideways")


def test_registered_secrets_reach_the_output_filter(monkeypatch):
    seen = {}
    real = campaign.build_guardrail

    def spy(mode, canary, secrets=()):
        seen.update(mode=mode, canary=canary, secrets=list(secrets))
        return real(mode, canary, secrets)

    monkeypatch.setattr(campaign, "build_guardrail", spy)
    run_phase(_cfg(output_secrets=["tok-123"]), guardrail="output", mode="isolated")
    assert seen == {"mode": "output", "canary": "NW-test-CANARY", "secrets": ["tok-123"]}


def test_the_attack_run_uses_the_configured_guarded_mode_for_the_guarded_phase_only():
    cfg = _cfg(strategy="adaptive", guardrail=True, guardrail_mode="output")
    baseline, guarded = run_attack_run(cfg)
    assert not any(t.filtered for t in _turns(baseline))
    assert any(t.filtered for t in _turns(guarded))
    report = build_session_report(baseline, guarded, run_meta(cfg))
    assert report["meta"]["guardrail_mode"] == "output"
    assert [r.guardrail for r in records_from_report(report)] == ["none", "output"]


def test_records_name_the_mode_and_an_older_report_still_means_input():
    for mode in ("input", "output", "both"):
        report = build_phase_report(run_phase(_cfg(), guardrail=mode, mode="isolated"), "guarded",
                                    {**run_meta(_cfg(guardrail_mode=mode)), "mode": "isolated"})
        assert {r.guardrail for r in records_from_report(report)} == {mode}
    report = build_session_report(run_phase(_cfg(), guardrail="none"),
                                  run_phase(_cfg(), guardrail="input"), {"target": "mock"})
    assert "guardrail_mode" not in report["meta"]
    assert [r.guardrail for r in records_from_report(report)] == ["none", "input"]


def test_the_cli_takes_a_guardrail_mode_and_the_results_carry_it(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["--no-color", "attack", "--config", "examples/config.yaml",
                 "--guardrail-mode", "output", "--out", str(out)])
    assert code == 0 and "guardrail output" in capsys.readouterr().out
    sessions = json.loads((out / "results.json").read_text(encoding="utf-8"))["sessions"]
    assert [s["guardrail"] for s in sessions] == ["none", "output"]
    assert sessions[1]["turns"][0]["outcome"] in ("filtered", "held", "blocked", "objective_met", "leaked")
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["meta"]["guardrail_mode"] == "output"


def test_a_sweep_runs_every_guardrail_mode(tmp_path):
    sweep = SweepConfig.model_validate({
        "base": "x", "out": str(tmp_path / "s"), "repeats": 1,
        "grid": {"guardrail": ["none", "input", "output", "both"], "mode": ["isolated"]}})
    base = _cfg()
    cells = expand(sweep, base)
    assert [(c.run_config(base).guardrail, c.run_config(base).guardrail_mode) for c in cells] == [
        (False, "input"), (True, "input"), (True, "output"), (True, "both")]
    result = run_sweep(sweep, base, cells, manager=RunManager(), poll=0.005)
    assert len(result.done) == 4 and not result.failed
    labels = {}
    for path in sorted((tmp_path / "s" / "runs").glob("*.results.json")):
        sessions = json.loads(path.read_text(encoding="utf-8"))["sessions"]
        labels[sessions[0]["guardrail"]] = {s["phase"] for s in sessions}
    assert labels == {"none": {"baseline"}, "input": {"guarded"}, "output": {"guarded"}, "both": {"guarded"}}


def test_the_web_api_accepts_a_guardrail_mode():
    body = {
        "target": {"provider": "mock"}, "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
        "guardrail": True, "guardrail_mode": "output",
        "attacker": {"strategy": "adaptive", "endpoint": {"provider": "mock"},
                     "objectives": ["leak_canary"], "max_turns": 6},
    }
    rid = http.post("/runs", json=body).json()["run_id"]
    with http.stream("GET", f"/runs/{rid}/events") as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    assert events[-1]["status"] == "done"
    filtered = [e for e in events if e["kind"] == "turn" and e["phase"] == "guarded" and e.get("filtered")]
    assert filtered and all(e["raw_leaked"] and not e["leaked"] for e in filtered)
    assert http.get(f"/runs/{rid}").json()["meta"]["guardrail_mode"] == "output"
    assert http.get(f"/runs/{rid}/report").json()["meta"]["guardrail_mode"] == "output"
