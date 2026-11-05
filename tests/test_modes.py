import pytest

from cthulhu.asr import aggregate
from cthulhu.campaign import run_attack_run, run_phase
from cthulhu.config import RunConfig
from cthulhu.corpus import load_corpus
from cthulhu.report import build_phase_report, build_session_report, run_meta
from cthulhu.results import records_from_report

N_ATTACKS = len(load_corpus([]))


def _cfg(strategy="corpus", objectives=("leak_canary",), max_turns=40, **over):
    body = {
        "target": {"provider": "mock"}, "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
        "attacker": {"strategy": strategy, "endpoint": {"provider": "mock"},
                     "objectives": list(objectives), "max_turns": max_turns},
    }
    body.update(over)
    return RunConfig.model_validate(body)


def test_isolated_runs_every_vector_in_its_own_one_turn_session():
    sessions = run_phase(_cfg(), guardrail=False, mode="isolated")
    assert len(sessions) == N_ATTACKS
    assert all(len(s.turns) == 1 for s in sessions)
    assert len({s.turns[0].move.vector for s in sessions}) == N_ATTACKS


def test_isolated_gives_every_vector_the_same_n_and_ignores_earlier_wins():
    meta = {**run_meta(_cfg()), "mode": "isolated"}
    report = build_phase_report(run_phase(_cfg(), guardrail=False, mode="isolated"), "baseline", meta)
    rows = aggregate(records_from_report(report), by=("config", "vector"))
    assert len(rows) == N_ATTACKS and {r.n for r in rows} == {1}
    assert sum(r.proven for r in rows) > 1          # several vectors win, none stops the others


def test_campaign_stops_at_its_first_success_so_later_vectors_are_not_tried():
    sessions = run_phase(_cfg(), guardrail=False, mode="campaign")
    assert len(sessions) == 1 and sessions[0].success
    assert len(sessions[0].turns) < N_ATTACKS


def test_isolated_needs_the_corpus_strategy():
    with pytest.raises(ValueError, match="corpus"):
        run_phase(_cfg(strategy="adaptive"), guardrail=False, mode="isolated")


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        run_phase(_cfg(), guardrail=False, mode="sideways")


def test_the_guardrail_applies_in_isolated_mode_and_blocked_turns_are_recorded():
    sessions = run_phase(_cfg(), guardrail=True, mode="isolated")
    assert any(s.turns[0].blocked for s in sessions)


def test_a_phase_report_labels_the_phase_and_the_mode():
    meta = {**run_meta(_cfg()), "mode": "isolated"}
    report = build_phase_report(run_phase(_cfg(), guardrail=True, mode="isolated"), "guarded", meta)
    assert report["baseline"] == [] and report["guarded"]
    recs = records_from_report(report, run_id="c-r001")
    assert {r.phase for r in recs} == {"guarded"} and {r.guardrail for r in recs} == {"input"}
    assert {r.mode for r in recs} == {"isolated"} and {r.run_id for r in recs} == {"c-r001"}


def test_isolated_and_campaign_land_in_different_rows():
    iso = records_from_report(build_phase_report(
        run_phase(_cfg(), guardrail=False, mode="isolated"), "baseline",
        {**run_meta(_cfg()), "mode": "isolated"}))
    camp = records_from_report(build_phase_report(
        run_phase(_cfg(), guardrail=False, mode="campaign"), "baseline", run_meta(_cfg())))
    rows = aggregate(iso + camp)
    assert {r.key["mode"] for r in rows} == {"isolated", "campaign"} and len(rows) == 2


def test_the_attack_run_still_runs_baseline_then_guarded():
    baseline, guarded = run_attack_run(_cfg(strategy="adaptive", guardrail=True, max_turns=6))
    assert len(baseline) == 1 and guarded is not None and len(guarded) == 1
    report = build_session_report(baseline, guarded, run_meta(_cfg(strategy="adaptive")))
    assert {r.mode for r in records_from_report(report)} == {"campaign"}


def test_a_corpus_run_names_no_attacker_model_even_when_the_config_has_one():
    assert run_meta(_cfg(strategy="corpus"))["attacker_model"] is None
    assert run_meta(_cfg(strategy="adaptive"))["attacker_model"] == "mock:qwen3:8b"
