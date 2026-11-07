import importlib.util
import json
from pathlib import Path

from cthulhu.asr import merge_results, result_files
from cthulhu.corpus import load_corpus
from cthulhu.sweep import expand, load_sweep, make_plan

RESEARCH = Path("research")
N_ATTACKS = len(load_corpus([]))


def _script():
    spec = importlib.util.spec_from_file_location("make_probe_corpus", RESEARCH / "make_probe_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_part_a_is_the_planned_grid():
    sweep, base = load_sweep(RESEARCH / "sweep-a.yaml")
    cells = expand(sweep, base)
    assert (len(cells), sweep.repeats, sweep.workers) == (8, 10, 1)
    assert {c.guardrail for c in cells} == {"none", "input", "output", "both"}
    assert {c.mode for c in cells} == {"isolated", "campaign"}
    assert base.attacker.judge_mode == "on_suspicion" and base.attacker.max_turns >= N_ATTACKS
    assert base.target.temperature is None                       # sampling is left at the model default
    assert make_plan(sweep, base, cells).warnings == []


def test_part_c_judges_every_reply_and_only_the_output_stages():
    sweep, base = load_sweep(RESEARCH / "sweep-c.yaml")
    cells = expand(sweep, base)
    assert (len(cells), sweep.repeats) == (4, 3)
    assert {c.guardrail for c in cells} == {"output", "both"} and base.attacker.judge_mode == "per_turn"


def test_the_probe_is_unguarded_and_guarded_campaigns_on_the_subset_corpus():
    sweep, base = load_sweep(RESEARCH / "sweep-probe.yaml")
    cells = expand(sweep, base)
    assert (len(cells), sweep.repeats) == (4, 10) and {c.mode for c in cells} == {"campaign"}
    assert base.corpus_dir is not None and base.corpus_dir.name == "probe-corpus"
    assert base.attacker.judge_mode == "on_suspicion"


def test_the_three_parts_use_the_same_target_prompt_and_canary():
    loaded = [load_sweep(RESEARCH / name)[1] for name in ("sweep-a.yaml", "sweep-probe.yaml", "sweep-c.yaml")]
    assert len({b.canary for b in loaded}) == 1
    assert len({b._raw_system_prompt() for b in loaded}) == 1
    assert len({(b.target.model, b.judge.model) for b in loaded}) == 1


def test_the_probe_script_removes_the_unguarded_winners_and_ends_at_jb_002(tmp_path):
    script = _script()
    records, _ = merge_results(result_files([Path("tests/fixtures/smoke")]))
    order = script.corpus_order()
    won = script.winners(records, order)
    assert "ii-001" in won                                   # won an unguarded campaign in the fixture
    assert all(order.index(v) < order.index("jb-002") for v in won) and "jb-002" not in won
    keep = script.subset_order(order, set(won))
    assert keep[-1] == "jb-002" and not set(won) & set(keep)
    assert len(keep) == order.index("jb-002") + 1 - len(won)

    script.write_corpus(keep, tmp_path / "corpus")
    loaded = [a.id for a in load_corpus([], tmp_path / "corpus")]
    assert loaded == keep                                    # same order, nothing else left


def test_only_proven_unguarded_successes_count_as_winners():
    script = _script()
    records, _ = merge_results(result_files([Path("tests/fixtures/smoke")]))
    guarded_only = [r for r in records if r.guardrail != "none"]
    assert script.winners(guarded_only, script.corpus_order()) == {}


def test_the_probe_script_main_shows_a_plan_and_writes_a_manifest(tmp_path, capsys):
    script = _script()
    out = tmp_path / "probe-corpus"
    assert script.main(["tests/fixtures/smoke", "--out", str(out)]) == 0
    shown = capsys.readouterr().out
    assert "removed" in shown and "jb-002 is attack" in shown and "not written" in shown
    assert not out.exists()
    assert script.main(["tests/fixtures/smoke", "--out", str(out), "--write"]) == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus"][-1] == "jb-002" and manifest["window"] == 8
    assert "system_prompt_leak.yaml" not in [p.name for p in out.glob("*.yaml")]


def test_the_wrapper_asks_windows_to_stay_awake_and_gives_it_back():
    text = (RESEARCH / "run.ps1").read_text(encoding="utf-8")
    assert "SetThreadExecutionState" in text and "finally" in text and "exit $LASTEXITCODE" in text


def test_the_research_folder_commits_no_results():
    names = {p.name for p in RESEARCH.iterdir()}
    assert not any(n.endswith((".json", ".csv")) for n in names) and "out" not in names
