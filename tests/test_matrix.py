from cthulhu.matrix import (
    LOW_N, build_matrices, context_tables, defence_tables, delta, session_table,
)
from cthulhu.results import SessionRecord, TurnRecord


def _turn(i, outcome, vector="v1", category="c1"):
    return TurnRecord(i, category, vector, "", outcome, False, "not_judged")


def _rec(turns, *, mode="isolated", guardrail="none", target="mock:t", strategy="corpus",
         attacker_model="", needs_review=False, run_id="r"):
    """turns: (outcome, vector, category) triples or (outcome, vector) pairs."""
    built = [_turn(i, *t) for i, t in enumerate(turns, 1)]
    won = next((t.turn for t in built if t.outcome == "objective_met"), None)
    return SessionRecord(run_id, "baseline" if guardrail == "none" else "guarded", target, "mock:j",
                         strategy, attacker_model, guardrail, "leak_canary", won is not None, won,
                         "success" if won else "turn_budget", needs_review, built, mode)


def _iso(outcome, vector="v1", category="c1", **kw):
    return _rec([(outcome, vector, category)], **kw)


def _many(n, k, vector="v1", category="c1", **kw):
    """n isolated one-turn sessions of one vector, k of them proven."""
    return [_iso("objective_met" if i < k else "held", vector, category, **kw) for i in range(n)]


def _row(table, label):
    return next(r for r in table.rows if r.label == label)


def test_only_guardrail_modes_with_data_get_a_column_and_new_ones_open_one():
    recs = _many(3, 1) + _many(3, 0, guardrail="input")
    (table,) = defence_tables(recs)
    assert table.guardrails == ["none", "input"]
    (more,) = defence_tables(recs + _many(3, 0, guardrail="output"))
    assert more.guardrails == ["none", "input", "output"]


def test_a_cell_is_counts_from_the_asr_table_and_the_all_row_totals_them():
    recs = (_many(4, 1, "v1", "c1") + _many(6, 3, "v2", "c1") + _many(5, 0, "v3", "c2"))
    (table,) = defence_tables(recs)
    c1, c2, total = _row(table, "c1"), _row(table, "c2"), _row(table, "all")
    assert (c1.cells["none"].proven, c1.cells["none"].n) == (4, 10)
    assert (c2.cells["none"].proven, c2.cells["none"].n) == (0, 5)
    assert (total.cells["none"].proven, total.cells["none"].n) == (4, 15)
    assert [r.label for r in table.rows][-1] == "all"


def test_the_defence_matrix_reads_only_isolated_records():
    recs = _many(3, 1) + [_rec([("objective_met", "v1", "c1")], mode="campaign")]
    (table,) = defence_tables(recs)
    assert _row(table, "all").cells["none"].n == 3


def test_a_cell_nothing_ran_in_is_none_and_one_with_only_errors_has_no_n():
    recs = _many(3, 1, target="a") + _many(3, 0, target="a", guardrail="input") + _many(3, 1, target="b")
    tables = {t.target: t for t in defence_tables(recs)}
    assert tables["b"].guardrails == ["none"]
    only_errors = _many(3, 1) + [_iso("error", guardrail="input")]
    (table,) = defence_tables(only_errors)
    cell = _row(table, "c1").cells["input"]
    assert cell is not None and cell.n == 0 and cell.error == 1


def test_judge_only_stays_out_of_proven():
    recs = _many(4, 1) + [_iso("objective_met", needs_review=True)]
    cell = _row(defence_tables(recs)[0], "c1").cells["none"]
    assert (cell.proven, cell.judge_only, cell.n) == (1, 1, 5)


def test_delta_is_in_points_and_says_whether_the_intervals_touch():
    (table,) = defence_tables(_many(30, 15) + _many(30, 0, guardrail="input"))
    d = _row(table, "all").deltas["input"]
    assert d.points == -50.0 and d.apart is True

    (close,) = defence_tables(_many(3, 1) + _many(3, 2, guardrail="input"))
    d = _row(close, "all").deltas["input"]
    assert d.points == 33.3 and d.apart is False          # 1/3 against 2/3 is unclear, not equal


def test_there_is_no_delta_without_an_unguarded_column_or_without_results():
    (table,) = defence_tables(_many(3, 1, guardrail="input") + _many(3, 0, guardrail="output"))
    assert table.rows[0].deltas == {"input": None, "output": None}
    assert delta(None, None) is None


def test_the_vector_view_hides_rows_with_nothing_to_show_until_asked():
    recs = (_many(3, 2, "win") + _many(3, 0, "dull") + _many(3, 0, "walled")
            + [_iso("held", "dull", guardrail="input")] * 3
            + [_iso("blocked", "walled", guardrail="input")] * 3)
    (table,) = defence_tables(recs, "vector")
    labels = [r.label for r in table.rows]
    assert "win (c1)" in labels and "walled (c1)" in labels and "dull (c1)" not in labels
    (everything,) = defence_tables(recs, "vector", show_all=True)
    assert "dull (c1)" in [r.label for r in everything.rows]


def test_a_vector_that_wins_only_inside_a_campaign_is_marked_and_keeps_its_turns():
    recs = _many(3, 0, "jb-002", "jailbreak", guardrail="input")
    for n in range(3):
        recs.append(_rec([("held", f"x{i}") for i in range(18)] + [("objective_met", "jb-002", "jailbreak")],
                         mode="campaign", guardrail="input", run_id=f"c{n}"))
    recs += _many(3, 3, "spl-005", "system_prompt_leak", guardrail="input")
    (table,) = context_tables(recs)
    assert (table.target, table.guardrail) == ("mock:t", "input")
    jb = next(r for r in table.rows if r.vector == "jb-002")
    assert jb.note == "only in context" and jb.win_turns == [19, 19, 19]
    assert (jb.isolated.proven, jb.isolated.n, jb.campaign.proven, jb.campaign.n) == (0, 3, 3, 3)
    spl = next(r for r in table.rows if r.vector == "spl-005")
    assert spl.note == "not reached in campaign" and spl.campaign is None
    assert "x0" not in [r.vector for r in table.rows]        # pure zeros stay hidden


def test_a_vector_that_wins_both_ways_has_no_note():
    recs = _many(3, 2, "v1") + [_rec([("objective_met", "v1")], mode="campaign")]
    (table,) = context_tables(recs)
    assert table.rows[0].note == ""


def test_there_is_no_context_table_without_both_modes_for_the_same_defender():
    assert context_tables(_many(3, 1)) == []
    assert context_tables([_rec([("objective_met", "v1")], mode="campaign")]) == []
    other = _many(3, 1, guardrail="none") + [_rec([("objective_met", "v1")], mode="campaign", guardrail="input")]
    assert context_tables(other) == []


def test_adaptive_turns_stay_out_of_the_context_view():
    recs = _many(3, 1) + [_rec([("objective_met", "")], mode="campaign", strategy="adaptive",
                               attacker_model="mock:a")]
    assert context_tables(recs) == []


def test_session_matrix_pairs_attackers_with_defenders_and_lists_the_break_turns():
    recs = [
        _rec([("held", "")] * 3 + [("objective_met", "")], mode="campaign", strategy="adaptive",
             attacker_model="mock:strong", guardrail="none"),
        _rec([("held", "")] * 5, mode="campaign", strategy="adaptive", attacker_model="mock:strong",
             guardrail="none"),
        _rec([("held", "")] * 2 + [("objective_met", "")], mode="campaign", strategy="adaptive",
             attacker_model="mock:weak", guardrail="input"),
        _rec([("objective_met", "v1")], mode="campaign", strategy="corpus", guardrail="none"),
    ]
    table = session_table(recs)
    assert table.defenders == [("mock:t", "none"), ("mock:t", "input")]
    assert table.attackers == [("adaptive", "mock:strong"), ("adaptive", "mock:weak"), ("corpus", "")]
    strong = table.cells[(("adaptive", "mock:strong"), ("mock:t", "none"))]
    assert (strong.sessions, strong.proven, strong.break_turns) == (2, 1, [4])
    assert strong.rate == 0.5 and strong.ci[0] < 0.5 < strong.ci[1]
    assert (("adaptive", "mock:weak"), ("mock:t", "none")) not in table.cells    # never run there
    assert table.cells[(("corpus", ""), ("mock:t", "none"))].break_turns == [1]


def test_a_judge_only_session_is_counted_apart_and_has_no_break_turn():
    recs = [_rec([("held", ""), ("objective_met", "")], mode="campaign", strategy="adaptive",
                 attacker_model="m", needs_review=True)]
    cell = next(iter(session_table(recs).cells.values()))
    assert (cell.proven, cell.judge_only, cell.break_turns) == (0, 1, [])


def test_there_is_no_session_matrix_without_a_campaign():
    assert session_table(_many(3, 1)) is None


def test_build_matrices_is_empty_for_no_records_and_the_low_n_line_is_ten():
    assert build_matrices([]).empty()
    assert LOW_N == 10
    assert not build_matrices(_many(3, 1)).empty()
