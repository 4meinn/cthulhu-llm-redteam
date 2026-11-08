import json
from pathlib import Path

from cthulhu.asr import merge_results, result_files
from cthulhu.matrix import build_matrices
from cthulhu.matrix_render import LOW_N_MARK, matrix_json, matrix_markdown
from tests.test_sweep_run import _base, _run, _sweep

SMOKE = Path("tests/fixtures/smoke")


def _smoke():
    records, _ = merge_results(result_files([SMOKE]))
    return records, build_matrices(records)


def test_the_matrix_json_is_plain_data():
    _, matrices = _smoke()
    data = matrix_json(matrices)
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data
    assert data["empty"] is False and data["low_n"] == 10 and data["low_n_mark"] == LOW_N_MARK


def test_every_view_is_there_and_carries_the_markdown_text():
    records, matrices = _smoke()
    data = matrix_json(matrices)
    md = matrix_markdown(matrices)
    assert data["defence"] and data["context"] and data["session"]
    for table in data["defence"] + data["vectors"]:
        for row in table["rows"]:
            for cell in row["cells"].values():
                assert cell["text"] in md
    for table in data["context"]:
        for row in table["rows"]:
            assert row["isolated"]["text"] in md
    for cell in data["session"]["cells"]:
        assert cell["text"] in md


def test_the_marks_and_notes_are_the_ones_the_markdown_uses():
    records, matrices = _smoke()
    data = matrix_json(matrices)
    cells = [c for t in data["defence"] for r in t["rows"] for c in r["cells"].values() if c["row"]]
    assert any(c["low_n"] for c in cells) == any(LOW_N_MARK in c["text"] for c in cells)
    assert all(0 < c["row"]["n"] < 10 for c in cells if c["low_n"])
    assert any("not a significance test" in n for n in data["notes"])
    assert any("overlap" in n and "does not mean there is no difference" in n for n in data["notes"])
    deltas = [d for t in data["defence"] for r in t["rows"] for d in r["deltas"].values() if d]
    assert all(("intervals apart" in d["text"]) == d["apart"] for d in deltas)


def test_caught_and_judge_only_stay_apart_from_proven():
    _, matrices = _smoke()
    rows = [c["row"] for t in matrix_json(matrices)["defence"] for r in t["rows"]
            for c in r["cells"].values() if c["row"]]
    assert rows and all({"proven", "judge_only", "caught", "filtered", "blocked", "error"} <= set(r) for r in rows)
    assert all(r["proven_ci"] is None or len(r["proven_ci"]) == 2 for r in rows)


def test_a_missing_cell_says_not_run_not_zero(tmp_path):
    sweep = _sweep(tmp_path, repeats=1, guardrail=["none", "input"], mode=["isolated"])
    _run(sweep)
    records, _ = merge_results(result_files([sweep.out]))
    keep = [r for r in records if r.guardrail == "none"]
    data = matrix_json(build_matrices(keep))
    assert all("input" not in t["guardrails"] for t in data["defence"])       # no data, no column


def test_the_output_note_shows_only_with_an_output_stage(tmp_path):
    records, _ = _smoke()
    plain = [r for r in records if r.guardrail in ("none", "input")]
    assert matrix_json(build_matrices(plain))["output_stage"] is False
    assert not any(n.startswith("Output stage") for n in matrix_json(build_matrices(plain))["notes"])


def test_no_records_is_an_empty_matrix():
    data = matrix_json(build_matrices([]))
    assert data["empty"] is True and data["defence"] == [] and data["session"] is None
