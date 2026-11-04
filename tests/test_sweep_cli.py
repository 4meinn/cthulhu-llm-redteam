import csv
import io
import json

import yaml

from cthulhu.cli import main

EXAMPLE = "examples/sweep.yaml"


def _sweep(tmp_path, out, **over):
    data = {"base": str(tmp_path / "base.yaml"), "out": str(out), "repeats": 1,
            "grid": {"guardrail": ["none"], "mode": ["isolated"]}}
    data.update(over)
    (tmp_path / "base.yaml").write_text(yaml.safe_dump({
        "target": {"provider": "mock"}, "system_prompt_text": "code is {{CANARY}}",
        "canary": "NW-test-CANARY",
        "attacker": {"strategy": "corpus", "max_turns": 40}}), encoding="utf-8")
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_plan_shows_the_cost_and_runs_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--no-color", "sweep", "--config", EXAMPLE, "--plan", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "4 cell(s) x 3 repeat(s) = 12 run(s)" in text and "isolated" in text
    assert not out.exists()


def test_the_shipped_example_runs_end_to_end_and_writes_the_tables(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--no-color", "sweep", "--config", EXAMPLE, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "12 run(s) finished, 0 already done, 0 failed" in text
    assert len(list((out / "runs").glob("*.results.json"))) == 12
    assert (out / "sweep.json").is_file() and (out / "asr.csv").is_file() and (out / "asr.md").is_file()
    assert (out / "matrix.md").is_file() and (out / "matrix.csv").is_file()
    assert "Defence matrix by category" in (out / "matrix.md").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO((out / "asr.csv").read_text(encoding="utf-8"))))
    config = [r for r in rows if r["level"] == "config"]
    assert {(r["guardrail"], r["mode"]) for r in config} == {
        ("none", "isolated"), ("none", "campaign"), ("input", "isolated"), ("input", "campaign")}


def test_running_the_same_sweep_again_picks_up_where_it_left_off(tmp_path, capsys):
    path = _sweep(tmp_path, tmp_path / "out", repeats=2)
    assert main(["--no-color", "sweep", "--config", str(path)]) == 0
    capsys.readouterr()
    assert main(["--no-color", "sweep", "--config", str(path)]) == 0
    assert "0 run(s) finished, 2 already done" in capsys.readouterr().out


def test_workers_can_be_set_on_the_command_line(tmp_path, capsys):
    path = _sweep(tmp_path, tmp_path / "out", repeats=3)
    assert main(["--no-color", "sweep", "--config", str(path), "--workers", "3"]) == 0
    assert json.loads((tmp_path / "out" / "sweep.json").read_text(encoding="utf-8"))["workers"] == 3


def test_a_changed_plan_is_an_error_and_a_bad_grid_is_too(tmp_path, capsys):
    path = _sweep(tmp_path, tmp_path / "out")
    assert main(["--no-color", "sweep", "--config", str(path)]) == 0
    capsys.readouterr()
    changed = _sweep(tmp_path, tmp_path / "out", grid={"guardrail": ["none", "input"], "mode": ["isolated"]})
    assert main(["--no-color", "sweep", "--config", str(changed)]) == 2
    assert "different plan" in capsys.readouterr().err

    bad = _sweep(tmp_path, tmp_path / "other", grid={"strategy": ["adaptive"], "mode": ["isolated"]})
    assert main(["--no-color", "sweep", "--config", str(bad)]) == 2
    assert "cannot run" in capsys.readouterr().err
    assert main(["--no-color", "sweep", "--config", str(tmp_path / "missing.yaml")]) == 2


def test_aggregate_combines_folders_and_files(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    a = _sweep(tmp_path / "a", tmp_path / "a" / "out", repeats=2)
    assert main(["--no-color", "sweep", "--config", str(a)]) == 0
    capsys.readouterr()
    out = tmp_path / "agg"
    one = next((tmp_path / "a" / "out" / "runs").glob("*r001.results.json"))
    assert main(["--no-color", "aggregate", str(tmp_path / "a" / "out"), str(one), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "skipped" in captured.err and "already counted" in captured.err
    assert "wrote" in captured.out
    rows = list(csv.DictReader(io.StringIO((out / "asr.csv").read_text(encoding="utf-8"))))
    config = next(r for r in rows if r["level"] == "config")
    assert int(config["n"]) == 2 * 29          # two runs, the repeated file not counted again


def test_aggregate_defaults_to_the_folder_it_was_given(tmp_path, capsys):
    path = _sweep(tmp_path, tmp_path / "out")
    main(["--no-color", "sweep", "--config", str(path)])
    (tmp_path / "out" / "asr.md").unlink()
    assert main(["--no-color", "aggregate", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "asr.md").is_file()


def test_aggregate_writes_the_matrix_and_all_keeps_the_empty_vector_rows(tmp_path, capsys):
    fixture = "tests/fixtures/smoke"
    default, everything = tmp_path / "default", tmp_path / "everything"
    assert main(["--no-color", "aggregate", fixture, "--out", str(default)]) == 0
    assert main(["--no-color", "aggregate", fixture, "--out", str(everything), "--all"]) == 0
    short = (default / "matrix.md").read_text(encoding="utf-8")
    full = (everything / "matrix.md").read_text(encoding="utf-8")
    assert (default / "matrix.csv").is_file()
    assert "enc-001" in full and "enc-001" not in short       # nothing leaked and nothing blocked
    assert "jb-002" in short


def test_aggregate_with_nothing_to_read_is_an_error(tmp_path, capsys):
    assert main(["--no-color", "aggregate", str(tmp_path / "nope")]) == 2
    assert "no such file" in capsys.readouterr().err
