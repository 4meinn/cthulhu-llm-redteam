import csv
import io

from cthulhu.matrix import LOW_N, build_matrices
from cthulhu.matrix_render import (
    CSV_COLUMNS, LOW_N_MARK, matrix_csv, matrix_markdown, matrix_rows, write_matrix,
)
from cthulhu.results import SessionRecord, TurnRecord


def _rec(turns, *, mode="isolated", guardrail="none", target="mock:t", strategy="corpus",
         attacker_model="", needs_review=False, run_id="r"):
    built = [TurnRecord(i, "c1", t[1], "", t[0], False, "not_judged") for i, t in enumerate(turns, 1)]
    won = next((t.turn for t in built if t.outcome == "objective_met"), None)
    return SessionRecord(run_id, "baseline" if guardrail == "none" else "guarded", target, "mock:j",
                         strategy, attacker_model, guardrail, "leak_canary", won is not None, won,
                         "success" if won else "turn_budget", needs_review, built, mode)


def _many(n, k, vector="v1", **kw):
    return [_rec([("objective_met" if i < k else "held", vector)], **kw) for i in range(n)]


def _records():
    recs = _many(30, 15) + _many(30, 0, guardrail="input")
    for i in range(3):
        recs.append(_rec([("held", f"x{j}") for j in range(18)] + [("objective_met", "jb-002")],
                         mode="campaign", guardrail="input", run_id=f"c{i}"))
    recs += _many(3, 0, "jb-002", guardrail="input")
    recs.append(_rec([("held", "")] * 3 + [("objective_met", "")], mode="campaign", strategy="adaptive",
                     attacker_model="mock:a", guardrail="none"))
    return recs


def _md(recs=None):
    return matrix_markdown(build_matrices(recs if recs is not None else _records()))


def test_the_defence_table_shows_counts_percentages_and_the_change_against_none():
    md = _md()
    assert "### Defence matrix by category: mock:t (isolated)" in md
    assert "| Category | none | input | input vs none |" in md
    assert "| all | 15/30 (50.0%) | 0/33 (0.0%) | -50.0 points, intervals apart |" in md


def test_overlapping_intervals_are_called_unclear_not_equal():
    md = _md(_many(3, 1) + _many(3, 2, guardrail="input"))
    assert "+33.3 points, intervals overlap, unclear" in md
    assert "does not mean there is no difference" in md
    assert "not a significance test" in md


def test_low_n_is_marked_and_a_larger_n_is_not():
    small = _md(_many(LOW_N - 1, 1))
    assert f"1/{LOW_N - 1} (11.1%) {LOW_N_MARK}" in small
    big = _md(_many(LOW_N, 1))
    assert f"1/{LOW_N} (10.0%)" in big and f"(10.0%) {LOW_N_MARK}" not in big


def test_not_run_and_no_result_read_differently_from_zero():
    recs = _many(12, 2, target="a") + [_rec([("error", "v1")], target="a", guardrail="input")]
    md = _md(recs)
    assert "no result (1 error(s))" in md
    md = _md(_many(12, 2, target="a") + _many(12, 2, target="b", guardrail="input"))
    assert "not run" in md


def test_judge_only_and_blocked_sit_beside_the_rate_not_in_it():
    recs = _many(12, 1) + [_rec([("objective_met", "v1")], needs_review=True),
                           _rec([("blocked", "v1")], guardrail="input")]
    md = _md(recs)
    assert "1/13 (7.7%) (+1 judge only)" in md
    assert "(1 blocked)" in md


def test_the_context_table_shows_where_it_only_won_in_context_and_the_turns():
    md = _md()
    assert "### Context effect: mock:t, guardrail input" in md
    assert "| jb-002 | 0/3 (0.0%) " in md and "3 (turn 19, 19, 19) | only in context |" in md
    assert "A campaign stops at its first success" in md


def test_the_session_matrix_names_the_attacker_and_the_break_turns():
    md = _md()
    assert "### Session matrix: attacker against defender (campaign sessions)" in md
    assert "| adaptive (mock:a) | 1/1 proven (100.0%) " in md and "broke at turn 4" in md
    assert "broke at turn 19, 19, 19" in md


def test_an_empty_set_says_so_and_no_dash_characters_are_written():
    assert "No results to compare." in _md([])
    assert not any(c in _md() for c in (chr(0x2013), chr(0x2014)))


def test_a_pipe_in_a_label_does_not_break_the_table():
    md = _md([_rec([("objective_met", "v1")], mode="campaign", target="a|b")])
    table_lines = [line for line in md.splitlines() if line.startswith("|")]
    assert table_lines and not any("a|b" in line for line in table_lines)
    assert any("a\\|b" in line for line in table_lines)


def test_the_csv_is_one_tidy_row_per_cell_with_a_fixed_header():
    text = matrix_csv(build_matrices(_records()))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert {r["view"] for r in rows} == {"defence_category", "defence_vector", "context", "session"}
    cat = next(r for r in rows if r["view"] == "defence_category" and r["row"] == "all"
               and r["guardrail"] == "input")
    assert (cat["n"], cat["proven"], cat["delta_points"], cat["intervals"]) == ("33", "0", "-50.0", "apart")
    ctx = [r for r in rows if r["view"] == "context" and r["row"] == "jb-002"]
    assert {r["mode"] for r in ctx} == {"isolated", "campaign"}
    camp = next(r for r in ctx if r["mode"] == "campaign")
    assert camp["break_turns"] == "19 19 19" and camp["note"] == "only in context"
    sess = next(r for r in rows if r["view"] == "session")
    assert sess["strategy"] == "adaptive" and sess["attacker_model"] == "mock:a" and sess["break_turns"] == "4"
    assert all(r["low_n"] in ("true", "false") for r in rows)


def test_the_csv_rows_match_the_cells_and_a_formula_in_a_label_is_escaped():
    recs = _many(3, 1, target="=SUM(1,1)")
    rows = matrix_rows(build_matrices(recs))
    assert rows and all(r["target"] == "=SUM(1,1)" for r in rows)
    parsed = list(csv.DictReader(io.StringIO(matrix_csv(build_matrices(recs)))))
    assert all(r["target"] == "'=SUM(1,1)" for r in parsed)


def test_write_matrix_writes_both_files_and_all_rows_can_be_asked_for(tmp_path):
    recs = _many(3, 2, "win") + _many(3, 0, "dull")
    md_path, csv_path = write_matrix(recs, tmp_path / "m")
    assert md_path.name == "matrix.md" and csv_path.name == "matrix.csv"
    assert "dull" not in md_path.read_text(encoding="utf-8")
    write_matrix(recs, tmp_path / "all", show_all=True)
    assert "dull" in (tmp_path / "all" / "matrix.md").read_text(encoding="utf-8")


def test_a_real_smoke_run_makes_the_matrices(tmp_path):
    """Four runs from a real qwen3:8b sweep: isolated and campaign, with and without the guardrail."""
    from pathlib import Path

    from cthulhu.asr import merge_results, result_files

    records, skipped = merge_results(result_files([Path("tests/fixtures/smoke")]))
    assert skipped == [] and len(records) == 29 + 1 + 29 + 1
    md_path, csv_path = write_matrix(records, tmp_path)
    md = md_path.read_text(encoding="utf-8")
    assert "Defence matrix by category: ollama:qwen3:8b (isolated)" in md
    assert "Context effect: ollama:qwen3:8b, guardrail input" in md
    # the story-framed jailbreak broke only inside the campaign, at turn 19
    assert "| jb-002 |" in md and "1 (turn 19) | only in context |" in md
    # the translation vector got through the input filter on its own
    matrices = build_matrices(records)
    vec = next(t for t in matrices.vectors if t.target == "ollama:qwen3:8b")
    spl = next(r for r in vec.rows if r.label.startswith("spl-005"))
    assert spl.cells["input"].proven == 1 and spl.cells["none"].proven == 1
    assert "broke at turn 13" in md and "broke at turn 19" in md
    assert len(list(csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8"))))) > 30
