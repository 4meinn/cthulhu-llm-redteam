import csv
import io
import json

from cthulhu.asr import aggregate, asr_section, records_from_json, section_csv, section_markdown
from cthulhu.export import to_json
from cthulhu.matrix import build_matrices, defence_tables
from cthulhu.matrix_render import matrix_csv, matrix_markdown
from cthulhu.results import SessionRecord, TurnRecord


def _turn(i, outcome, raw_leak=False, vector="v1"):
    return TurnRecord(i, "c1", vector, "", outcome, False, "not_judged", raw_leak)


def _rec(turns, *, guardrail="none", mode="isolated", needs_review=False, target="mock:t"):
    """turns: outcomes, or (outcome, raw_leak) pairs."""
    built = [_turn(i, *((t, False) if isinstance(t, str) else t)) for i, t in enumerate(turns, 1)]
    won = next((t.turn for t in built if t.outcome == "objective_met"), None)
    return SessionRecord("r", "baseline" if guardrail == "none" else "guarded", target, "mock:j",
                         "corpus", "", guardrail, "leak_canary", won is not None, won,
                         "success" if won else "turn_budget", needs_review, built, mode)


def _caught(n):
    return [_rec([("filtered", True)], guardrail="output") for _ in range(n)]


def test_filtered_turns_are_defended_so_they_stay_in_the_denominator():
    row = aggregate([_rec([("filtered", True), "filtered", "held", "blocked", "objective_met"])])[0]
    assert (row.filtered, row.caught, row.held, row.blocked, row.proven) == (2, 1, 1, 1, 1)
    assert row.n == 5 and row.proven_rate == 0.2


def test_caught_counts_only_filtered_turns_where_the_model_really_leaked():
    row = aggregate(_caught(3) + [_rec(["filtered"], guardrail="output")] * 2, by=("config",))[0]
    assert (row.filtered, row.caught) == (5, 3)


def test_a_raw_leak_that_still_met_the_objective_is_not_a_catch():
    row = aggregate([_rec([("objective_met", True)])])[0]
    assert row.caught == 0 and row.proven == 1


def test_the_unfiltered_rate_is_what_the_same_turns_would_have_scored_without_the_stage():
    row = aggregate(_caught(6) + [_rec(["held"], guardrail="output")] * 2
                    + [_rec(["objective_met"], guardrail="output")])[0]
    assert row.proven_rate == round(1 / 9, 4) and row.unfiltered_rate == round(7 / 9, 4)
    assert aggregate([_rec(["error"])])[0].unfiltered_rate is None


def test_judge_only_success_after_the_filter_is_still_kept_apart():
    row = aggregate(_caught(4) + [_rec(["objective_met"], guardrail="output", needs_review=True)])[0]
    assert (row.proven, row.judge_only, row.caught, row.n) == (0, 1, 4, 5)


def test_the_asr_table_and_csv_show_filtered_and_caught():
    section = asr_section(_caught(5) + [_rec(["held"], guardrail="output")] * 3)
    md = "\n".join(section_markdown(section))
    assert "| Filtered |" in md and "5 (5 caught)" in md
    row = next(csv.DictReader(io.StringIO(section_csv(section))))
    assert (row["filtered"], row["caught"], row["unfiltered_rate"], row["proven_rate"]) == (
        "5", "5", "0.625", "0.0")


def test_an_asr_section_from_before_the_output_stage_still_renders():
    section = asr_section([_rec(["held", "objective_met"])])
    for rows in section.values():
        for row in rows:
            for key in ("filtered", "caught", "unfiltered_rate"):
                del row[key]
    assert "| Filtered |" in "\n".join(section_markdown(section))


def test_a_results_file_from_before_the_output_stage_still_loads():
    recs = [_rec(["held", "objective_met"])]
    data = json.loads(to_json(recs))
    for session in data["sessions"]:
        for turn in session["turns"]:
            del turn["raw_leak"]
    back = records_from_json(json.dumps(data))
    assert back == recs and all(t.raw_leak is False for t in back[0].turns)


def test_output_and_both_columns_open_when_there_is_data_and_not_before():
    base = [_rec(["held"] * 10), _rec(["objective_met"] * 1)]
    (without,) = defence_tables(base)
    assert without.guardrails == ["none"]
    recs = base + [_rec([("filtered", True)] * 10, guardrail="output"),
                   _rec([("filtered", True)] * 5 + ["blocked"] * 5, guardrail="both")]
    (table,) = defence_tables(recs)
    assert table.guardrails == ["none", "output", "both"]
    assert table.rows[-1].label == "all"
    assert table.rows[-1].cells["output"].caught == 10 and table.rows[-1].cells["both"].caught == 5


def test_the_matrix_says_how_many_were_caught_and_explains_why_proven_is_near_zero():
    recs = [_rec(["held"] * 6 + ["objective_met"] * 4)] + [_rec([("filtered", True)] * 10, guardrail="output")]
    md = matrix_markdown(build_matrices(recs))
    assert "0/10 (0.0%) (10 caught)" in md and "output vs none" in md
    assert "Output stage:" in md and "by construction" in md
    assert "judge-only success on the cleaned text" in md


def test_the_output_stage_note_only_appears_when_there_is_an_output_stage():
    md = matrix_markdown(build_matrices([_rec(["held", "objective_met"])] + [_rec(["blocked"], guardrail="input")]))
    assert "Output stage:" not in md


def test_a_filtered_turn_without_a_raw_leak_is_called_out_apart_from_a_catch():
    recs = [_rec([("filtered", True)] * 3 + ["filtered"] * 2, guardrail="output")]
    md = matrix_markdown(build_matrices(recs))
    assert "3 caught" in md and "2 filtered, no raw leak" in md


def test_the_matrix_csv_carries_the_new_columns():
    recs = [_rec(["held"] * 5)] + [_rec([("filtered", True)] * 5, guardrail="output")]
    rows = list(csv.DictReader(io.StringIO(matrix_csv(build_matrices(recs)))))
    cell = next(r for r in rows if r["view"] == "defence_category" and r["guardrail"] == "output")
    assert (cell["filtered"], cell["caught"], cell["unfiltered_rate"], cell["proven_rate"]) == (
        "5", "5", "1.0", "0.0")


def test_a_vector_where_the_filter_acted_is_not_hidden_from_the_vector_view():
    recs = [_rec([("held", False)], guardrail="none")] * 3 + [_rec([("filtered", True)], guardrail="output")] * 3
    (table,) = defence_tables(recs, "vector")
    assert [r.label for r in table.rows] == ["v1 (c1)"]
