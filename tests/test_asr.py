import csv
import io

import pytest

from cthulhu.asr import (
    CSV_COLUMNS, MODEL_AUTHORED, aggregate, asr_section, records_from_json, section_csv,
    section_markdown, wilson,
)
from cthulhu.export import to_json
from cthulhu.results import SessionRecord, TurnRecord


def _rec(turns, *, run_id="r1", target="mock:t", guardrail="none", strategy="corpus",
         attacker_model="", objective="leak_canary", needs_review=False):
    """A session from (outcome, category, vector) triples, a bare outcome is a corpus turn."""
    built = []
    for i, t in enumerate(turns, 1):
        outcome, cat, vec = (t, "direct_injection", f"di-{i:03d}") if isinstance(t, str) else t
        built.append(TurnRecord(i, cat, vec, "", outcome, False, "not_judged"))
    won = any(t.outcome == "objective_met" for t in built)
    return SessionRecord(run_id, "baseline" if guardrail == "none" else "guarded", target, "mock:j",
                         strategy, attacker_model, guardrail, objective, won,
                         next((t.turn for t in built if t.outcome == "objective_met"), None),
                         "success" if won else "turn_budget", needs_review, built)


def _only(rows):
    assert len(rows) == 1
    return rows[0]


def test_counts_and_rate_from_a_known_set():
    row = _only(aggregate([_rec(["held", "blocked", "leaked", "error", "objective_met"])]))
    assert (row.held, row.blocked, row.leaked, row.error, row.proven) == (1, 1, 1, 1, 1)
    assert row.n == 4                      # the error turn is not in the denominator
    assert row.proven_rate == 0.25 and row.judge_only == 0
    assert (row.sessions, row.sessions_proven) == (1, 1)


def test_blocked_turns_stay_in_the_denominator():
    row = _only(aggregate([_rec(["blocked", "blocked", "blocked", "objective_met"])]))
    assert row.n == 4 and row.blocked == 3 and row.proven_rate == 0.25


def test_judge_only_success_is_not_added_to_the_proven_rate():
    row = _only(aggregate([_rec(["held", "objective_met"], needs_review=True)]))
    assert row.proven == 0 and row.proven_rate == 0.0
    assert row.judge_only == 1 and row.judge_only_rate == 0.5
    assert (row.sessions_proven, row.sessions_judge_only) == (0, 1)


def test_a_session_with_only_errors_has_no_rate():
    row = _only(aggregate([_rec(["error", "error"])]))
    assert row.n == 0 and row.error == 2
    assert row.proven_rate is None and row.proven_ci is None and row.judge_only_rate is None


def test_model_authored_turns_are_their_own_group():
    corpus = _rec([("held", "jailbreak", "jb-001"), ("objective_met", "jailbreak", "jb-002")])
    adaptive = _rec([("held", "", ""), ("held", "", ""), ("objective_met", "", "")],
                    strategy="adaptive", attacker_model="mock:a")
    rows = aggregate([corpus, adaptive], by=("config", "category"))
    by_cat = {(r.key["strategy"], r.key["category"]): r for r in rows}
    assert set(by_cat) == {("corpus", "jailbreak"), ("adaptive", MODEL_AUTHORED)}
    assert by_cat[("corpus", "jailbreak")].n == 2          # not polluted by the adaptive turns
    assert by_cat[("adaptive", MODEL_AUTHORED)].n == 3


def test_config_key_has_target_guardrail_strategy_and_attacker_model():
    rows = aggregate([
        _rec(["held"]), _rec(["held"], guardrail="input"), _rec(["held"], target="mock:other"),
        _rec(["held"], strategy="adaptive", attacker_model="mock:a"),
        _rec(["held"], strategy="adaptive", attacker_model="mock:b"),
    ])
    assert len(rows) == 5
    assert set(rows[0].key) == {"target", "guardrail", "strategy", "attacker_model"}


def test_runs_with_the_same_config_merge_and_n_adds_up():
    a = _rec(["held", "objective_met"], run_id="r1")
    b = _rec(["held", "held", "held"], run_id="r2")
    row = _only(aggregate([a, b]))
    assert row.n == 5 and row.proven == 1 and row.sessions == 2 and row.sessions_proven == 1
    assert row.proven_rate == 0.2


def test_grouping_by_vector_and_objective():
    recs = [_rec([("held", "c1", "v1"), ("objective_met", "c1", "v2")]),
            _rec([("held", "c1", "v1")], objective="out_of_scope")]
    vec = {(r.key["vector"]): r for r in aggregate(recs, by=("vector",))}
    assert vec["v1"].n == 2 and vec["v2"].proven == 1
    obj = {r.key["objective"]: r for r in aggregate(recs, by=("objective",))}
    assert obj["leak_canary"].n == 2 and obj["out_of_scope"].n == 1


def test_a_session_counts_once_per_group_it_touched():
    rec = _rec([("held", "c1", "v1"), ("objective_met", "c2", "v2")])
    rows = {r.key["category"]: r for r in aggregate([rec], by=("category",))}
    assert rows["c1"].sessions == 1 and rows["c1"].sessions_proven == 0
    assert rows["c2"].sessions == 1 and rows["c2"].sessions_proven == 1


def test_wilson_handles_the_edges():
    assert wilson(0, 0) is None
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.7225, abs=1e-3)
    lo, hi = wilson(1, 1)
    assert lo == pytest.approx(0.2065, abs=1e-3) and hi == 1.0
    for n in range(1, 30):
        for k in range(n + 1):
            lo, hi = wilson(k, n)
            assert 0.0 <= lo <= k / n <= hi <= 1.0


def test_the_interval_is_on_the_proven_rate():
    row = _only(aggregate([_rec(["held", "held", "held", "objective_met"])]))
    assert row.proven_ci == wilson(1, 4)


def test_results_json_round_trips_into_the_same_aggregate():
    recs = [_rec(["held", "objective_met"]),
            _rec([("held", "", "")], strategy="adaptive", attacker_model="mock:a")]
    back = records_from_json(to_json(recs))
    assert back == recs
    assert asr_section(back) == asr_section(recs)


def test_a_file_that_is_not_a_results_file_is_rejected():
    for bad in ('{"sessions": [{"nope": 1}]}', "[]", "not json"):
        with pytest.raises(ValueError, match="results file"):
            records_from_json(bad)


def test_section_has_every_level_and_serializes():
    import json

    section = asr_section([_rec(["held", "objective_met"])])
    assert set(section) == {"config", "category", "vector", "objective"}
    json.dumps(section)
    assert section["category"][0]["key"]["category"] == "direct_injection"
    assert section["config"][0]["proven_ci"] == list(wilson(1, 2))


def test_csv_is_flat_with_a_level_column_and_empty_unused_keys():
    section = asr_section([_rec(["held", "objective_met"], attacker_model="")])
    rows = list(csv.DictReader(io.StringIO(section_csv(section))))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert {r["level"] for r in rows} == {"config", "category", "vector", "objective"}
    config = next(r for r in rows if r["level"] == "config")
    assert config["category"] == "" and config["n"] == "2" and config["proven_rate"] == "0.5"
    assert float(config["ci_low"]) < 0.5 < float(config["ci_high"])


def test_csv_escapes_a_formula_in_a_text_cell():
    section = asr_section([_rec(["held"], target="=SUM(1,1)")])
    row = next(csv.DictReader(io.StringIO(section_csv(section))))
    assert row["target"] == "'=SUM(1,1)"


def test_markdown_shows_counts_with_the_percentage_and_keeps_judge_only_apart():
    section = asr_section([_rec(["held", "objective_met"]),
                           _rec(["held", "held", "objective_met"], needs_review=True)])
    text = "\n".join(section_markdown(section))
    assert "## Attack success rate" in text and "### By category" in text
    assert "1/5 (20.0%)" in text                    # proven
    assert "1/5 (20.0%) | 0 | 0 |" in text         # judge only sits in its own column
    assert "1 of 2 proven, 1 judge only" in text
    assert not any(c in text for c in (chr(0x2013), chr(0x2014)))
