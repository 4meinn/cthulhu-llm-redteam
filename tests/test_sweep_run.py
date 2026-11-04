import json

import pytest

from cthulhu import campaign
from cthulhu.asr import aggregate, merge_results, records_from_json, result_files, write_aggregate
from cthulhu.config import RunConfig
from cthulhu.corpus import load_corpus
from cthulhu.sweep import SweepConfig, SweepMismatch, expand, pending_runs, prepare, run_sweep
from cthulhu.web.manager import RunManager

N_ATTACKS = len(load_corpus([]))


def _base(max_turns=40):
    return RunConfig.model_validate({
        "target": {"provider": "mock", "model": "qwen3:8b"}, "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
        "attacker": {"strategy": "corpus", "endpoint": {"provider": "mock"},
                     "objectives": ["leak_canary"], "max_turns": max_turns},
    })


def _sweep(tmp_path, repeats=2, workers=1, **grid):
    grid = grid or {"guardrail": ["none", "input"], "mode": ["isolated", "campaign"]}
    return SweepConfig.model_validate({"base": "x", "out": str(tmp_path / "sweep"),
                                       "repeats": repeats, "workers": workers, "grid": grid})


class _CountingManager(RunManager):
    def __init__(self):
        super().__init__()
        self.started = []

    def start(self, cfg, runner=None):
        self.started.append(cfg.label)
        return super().start(cfg, runner)


def _run(sweep, base=None, **kw):
    base = base or _base()
    manager = _CountingManager()
    result = run_sweep(sweep, base, expand(sweep, base), manager=manager, poll=0.005, **kw)
    return result, manager


def _files(sweep):
    return sorted(p.name for p in (sweep.out / "runs").glob("*.results.json"))


def test_every_run_is_written_to_its_own_file(tmp_path):
    sweep = _sweep(tmp_path)
    result, manager = _run(sweep)
    assert len(result.done) == 8 and result.skipped == 0 and not result.failed
    assert len(_files(sweep)) == 8 and len(manager.started) == 8
    assert "ollama" not in "".join(_files(sweep))
    name = "mock-qwen3-8b-input-corpus-leak_canary-isolated-r001.results.json"
    recs = records_from_json((sweep.out / "runs" / name).read_text(encoding="utf-8"))
    assert len(recs) == N_ATTACKS and {r.phase for r in recs} == {"guarded"}
    assert {r.guardrail for r in recs} == {"input"} and {r.mode for r in recs} == {"isolated"}
    assert {r.run_id for r in recs} == {name[: -len(".results.json")]}


def test_the_plan_is_stored_with_a_fingerprint_and_the_sampling(tmp_path):
    sweep = _sweep(tmp_path)
    _run(sweep)
    plan = json.loads((sweep.out / "sweep.json").read_text(encoding="utf-8"))
    assert len(plan["cells"]) == 4 and plan["repeats"] == 2 and plan["fingerprint"]
    assert plan["cells"][0]["sampling"]["target"]["temperature"] == "endpoint/model default, unspecified"
    assert plan["created"]


def test_running_again_does_nothing_because_every_run_is_on_disk(tmp_path):
    sweep = _sweep(tmp_path)
    _run(sweep)
    result, manager = _run(sweep)
    assert result.done == [] and result.skipped == 8 and manager.started == []


def test_only_the_missing_run_is_redone_and_a_cut_off_file_is_ignored(tmp_path):
    sweep = _sweep(tmp_path)
    _run(sweep)
    gone = (sweep.out / "runs").glob("*campaign-r002.results.json")
    victim = next(gone)
    victim.unlink()
    stale = sweep.out / "runs" / "half-written-r001.results.json.tmp"
    stale.write_text('{"sessions": [', encoding="utf-8")
    result, manager = _run(sweep)
    assert result.done == [victim.name[: -len(".results.json")]] and result.skipped == 7
    assert len(_files(sweep)) == 8 and not stale.exists()


def test_a_sweep_can_be_resumed_with_more_repeats(tmp_path):
    sweep = _sweep(tmp_path, repeats=2)
    _run(sweep)
    more = _sweep(tmp_path, repeats=3)
    result, manager = _run(more)
    assert len(result.done) == 4 and result.skipped == 8
    assert all(label.endswith("-r003") for label in manager.started)


def test_a_changed_plan_is_refused_and_runs_nothing(tmp_path):
    sweep = _sweep(tmp_path)
    _run(sweep)
    manager = _CountingManager()
    with pytest.raises(SweepMismatch, match="different plan"):
        run_sweep(sweep, _base(max_turns=10), expand(sweep, _base(max_turns=10)), manager=manager)
    assert manager.started == []


def test_the_run_order_keeps_a_target_together_and_spreads_the_repeats(tmp_path):
    sweep = _sweep(tmp_path, repeats=2, target=["mock:a", "mock:b"], guardrail=["none", "input"])
    cells = expand(sweep, _base())
    runs_dir = prepare({"fingerprint": "f"}, sweep.out)
    todo, _ = pending_runs(cells, 2, runs_dir)
    order = [(s.cell.target.model, s.repeat, s.cell.guardrail) for s in todo]
    assert [o[0] for o in order] == ["a"] * 4 + ["b"] * 4
    assert [o[1] for o in order] == [1, 1, 2, 2, 1, 1, 2, 2]


def test_workers_do_not_change_the_results(tmp_path):
    one = _sweep(tmp_path / "one", workers=1)
    three = _sweep(tmp_path / "three", workers=3)
    _run(one)
    _run(three)
    assert _files(one) == _files(three)
    a, _ = merge_results(result_files([one.out]))
    b, _ = merge_results(result_files([three.out]))
    assert a == b


def test_a_failed_run_is_reported_not_written_and_retried_next_time(tmp_path, monkeypatch):
    sweep = _sweep(tmp_path, guardrail=["none", "input"], mode=["campaign"], repeats=1)
    real = campaign.run_phase

    def flaky(cfg, **kw):
        if kw["guardrail"] != "none":
            raise RuntimeError("target unreachable")
        return real(cfg, **kw)

    monkeypatch.setattr(campaign, "run_phase", flaky)
    result, _ = _run(sweep)
    assert len(result.done) == 1 and list(result.failed.values()) == ["target unreachable"]
    assert len(_files(sweep)) == 1

    monkeypatch.setattr(campaign, "run_phase", real)
    again, manager = _run(sweep)
    assert len(again.done) == 1 and again.skipped == 1 and len(_files(sweep)) == 2


def test_stopping_keeps_what_finished_and_the_rest_runs_on_resume(tmp_path):
    sweep = _sweep(tmp_path, repeats=3, guardrail=["none"], mode=["campaign"])
    seen = []
    result, _ = _run(sweep, should_stop=lambda: bool(seen),
                     on_progress=lambda rid, status, res, total: seen.append(rid))
    assert result.cancelled and len(result.done) >= 1
    assert len(_files(sweep)) == len(result.done) < 3
    rest, _ = _run(sweep)
    assert len(_files(sweep)) == 3 and not rest.cancelled


def test_progress_is_reported_per_finished_run(tmp_path):
    sweep = _sweep(tmp_path, repeats=2, guardrail=["none"], mode=["campaign"])
    events = []
    _run(sweep, on_progress=lambda rid, status, res, total: events.append((status, len(res.done), total)))
    assert events == [("done", 1, 2), ("done", 2, 2)]


def test_isolated_gives_every_vector_n_equal_to_the_repeats(tmp_path):
    sweep = _sweep(tmp_path, repeats=3, guardrail=["none"], mode=["isolated"])
    _run(sweep)
    recs, _ = merge_results(result_files([sweep.out]))
    rows = aggregate(recs, by=("config", "vector"))
    assert len(rows) == N_ATTACKS and {r.n for r in rows} == {3}


def test_modes_and_guardrails_aggregate_into_separate_rows(tmp_path):
    sweep = _sweep(tmp_path, repeats=1)
    _run(sweep)
    recs, skipped = merge_results(result_files([sweep.out]))
    rows = aggregate(recs)
    assert skipped == [] and len(rows) == 4
    assert {(r.key["guardrail"], r.key["mode"]) for r in rows} == {
        ("none", "isolated"), ("none", "campaign"), ("input", "isolated"), ("input", "campaign")}
    iso = next(r for r in rows if r.key == {**r.key, "guardrail": "none", "mode": "isolated"})
    assert iso.n == N_ATTACKS


def test_two_sweeps_merge_and_n_adds_up(tmp_path):
    a = _sweep(tmp_path / "a", repeats=1, guardrail=["none"], mode=["isolated"])
    b = _sweep(tmp_path / "b", repeats=1, guardrail=["none"], mode=["isolated"])
    _run(a)
    for p in (b.out / "runs",):
        p.mkdir(parents=True)
    src = next((a.out / "runs").glob("*.results.json"))
    text = src.read_text(encoding="utf-8").replace("-r001", "-r002")
    (b.out / "runs" / src.name.replace("-r001", "-r002")).write_text(text, encoding="utf-8")
    recs, skipped = merge_results(result_files([a.out, b.out]))
    assert skipped == [] and aggregate(recs)[0].n == 2 * N_ATTACKS


def test_the_same_run_given_twice_is_counted_once(tmp_path):
    sweep = _sweep(tmp_path, repeats=1, guardrail=["none"], mode=["campaign"])
    _run(sweep)
    file = next((sweep.out / "runs").glob("*.results.json"))
    once, _ = merge_results([file])
    twice, skipped = merge_results([file, file])
    assert len(twice) == len(once) and skipped == [file.name[: -len(".results.json")]]


def test_result_files_reads_folders_and_rejects_the_missing(tmp_path):
    sweep = _sweep(tmp_path, repeats=1, guardrail=["none"], mode=["campaign"])
    _run(sweep)
    assert len(result_files([sweep.out])) == 1
    assert len(result_files([sweep.out / "runs"])) == 1
    with pytest.raises(ValueError, match="no such file"):
        result_files([tmp_path / "nope"])
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no results files"):
        result_files([empty])


def test_write_aggregate_makes_the_csv_and_the_markdown(tmp_path):
    sweep = _sweep(tmp_path, repeats=1, guardrail=["none"], mode=["isolated"])
    _run(sweep)
    recs, _ = merge_results(result_files([sweep.out]))
    csv_path, md_path = write_aggregate(recs, tmp_path / "agg")
    assert csv_path.name == "asr.csv" and md_path.name == "asr.md"
    assert "## Attack success rate" in md_path.read_text(encoding="utf-8")
    assert "isolated" in md_path.read_text(encoding="utf-8")
