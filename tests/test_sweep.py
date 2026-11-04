from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cthulhu.config import EndpointConfig, RunConfig
from cthulhu.corpus import load_corpus
from cthulhu.sweep import (
    SAMPLING_DEFAULT, SweepConfig, describe, expand, fingerprint, format_plan, load_sweep,
    make_plan, parse_target, run_id,
)

N_ATTACKS = len(load_corpus([]))


def _base(max_turns=40, **attacker):
    return RunConfig.model_validate({
        "target": {"provider": "mock", "model": "qwen3:8b"}, "judge": {"provider": "mock"},
        "system_prompt_text": "code is {{CANARY}}", "canary": "NW-test-CANARY",
        "attacker": {"strategy": "corpus", "endpoint": {"provider": "mock"},
                     "objectives": ["leak_canary"], "max_turns": max_turns, **attacker},
    })


def _sweep(**grid):
    return SweepConfig.model_validate({"base": "unused.yaml", "repeats": 3, "grid": grid})


def test_an_empty_grid_is_one_cell_from_the_base():
    (cell,) = expand(_sweep(), _base())
    assert (cell.guardrail, cell.strategy, cell.objective, cell.mode) == (
        "none", "corpus", "leak_canary", "campaign")
    assert cell.target.model == "qwen3:8b"


def test_the_grid_is_the_product_of_its_axes():
    cells = expand(_sweep(target=["mock:a", "mock:b"], guardrail=["none", "input"],
                          mode=["isolated", "campaign"]), _base())
    assert len(cells) == 8
    assert len({c.id for c in cells}) == 8
    # grouped by target so one model is loaded at a time
    assert [c.target.model for c in cells] == ["a"] * 4 + ["b"] * 4


def test_a_target_keeps_the_base_url_and_may_have_a_colon_in_its_model():
    base = _base()
    base.target.base_url = "http://box:11434"
    t = parse_target("ollama:qwen3:8b", base.target)
    assert (t.provider, t.model, t.base_url) == ("ollama", "qwen3:8b", "http://box:11434")
    assert parse_target(EndpointConfig(provider="mock", model="x"), base.target).model == "x"
    for bad in ("qwen3:8b", "nothere:m", "mock:", "plain"):
        with pytest.raises(ValueError, match="provider:model"):
            parse_target(bad, base.target)


def test_the_cell_id_is_a_safe_slug_and_the_run_id_is_deterministic():
    (cell,) = expand(_sweep(target=["ollama:qwen3:8b"], guardrail=["input"], mode=["isolated"]), _base())
    assert cell.id == "ollama-qwen3-8b-input-corpus-leak_canary-isolated"
    assert run_id(cell, 3) == cell.id + "-r003"


def test_cells_that_cannot_run_are_rejected_with_every_reason():
    with pytest.raises(ValueError) as exc:
        expand(_sweep(strategy=["corpus", "adaptive"], mode=["isolated"]), _base())
    assert "isolated mode needs the corpus strategy" in str(exc.value)
    no_endpoint = _base()
    no_endpoint.attacker.endpoint = None
    with pytest.raises(ValueError, match="attacker.endpoint"):
        expand(_sweep(strategy=["adaptive"]), no_endpoint)


def test_a_repeated_cell_is_rejected():
    with pytest.raises(ValueError, match="repeats a cell"):
        expand(_sweep(target=["mock:a", "mock:a"]), _base())


def test_guardrail_modes_that_do_not_exist_yet_are_refused():
    for mode in ("output", "both"):
        with pytest.raises(ValidationError, match="not available yet"):
            _sweep(guardrail=[mode])
    with pytest.raises(ValidationError, match="must be one of"):
        _sweep(guardrail=["sideways"])


def test_a_cell_builds_a_single_phase_run_config():
    (cell,) = expand(_sweep(guardrail=["input"], mode=["isolated"], objective=["out_of_scope"]), _base())
    cfg = cell.run_config(_base(), label="x-r001")
    assert cfg.guardrail is True and cfg.attacker.objectives == ["out_of_scope"]
    assert cfg.label == "x-r001" and _base().attacker.objectives == ["leak_canary"]   # base untouched


def test_the_plan_counts_runs_and_calls_and_warns_when_the_corpus_does_not_fit():
    cells = expand(_sweep(mode=["isolated", "campaign"]), _base(max_turns=40))
    plan = make_plan(_sweep(mode=["isolated", "campaign"]), _base(max_turns=40), cells)
    assert (plan.cells, plan.runs) == (2, 6) and plan.warnings == []
    assert plan.max_target_calls == 3 * (N_ATTACKS + N_ATTACKS)

    short = make_plan(_sweep(), _base(max_turns=6), expand(_sweep(), _base(max_turns=6)))
    assert any("below the corpus size" in w for w in short.warnings)
    assert short.max_target_calls == 3 * 6
    text = format_plan(short, expand(_sweep(), _base(max_turns=6)))
    assert "1 cell(s) x 3 repeat(s) = 3 run(s)" in text and "warning:" in text


def test_the_shipped_example_covers_the_whole_corpus():
    sweep, base = load_sweep("examples/sweep.yaml")
    cells = expand(sweep, base)
    assert len(cells) == 4 and base.attacker.max_turns >= N_ATTACKS
    assert make_plan(sweep, base, cells).warnings == []


def test_sampling_is_recorded_and_the_default_is_written_out():
    base = _base()
    cells = expand(_sweep(target=["mock:a"]), base)
    plan = describe(_sweep(), base, cells)
    cell = plan["cells"][0]
    assert cell["sampling"]["target"] == {"temperature": SAMPLING_DEFAULT, "seed": "not set"}
    assert cell["sampling"]["judge"]["temperature"] == SAMPLING_DEFAULT
    assert cell["sampling"]["attacker"] is None            # a corpus cell has no attacker model

    base.target.temperature = 0.7
    base.judge.temperature = 0.0
    set_plan = describe(_sweep(), base, expand(_sweep(), base))["cells"][0]["sampling"]
    assert set_plan["target"]["temperature"] == 0.7 and set_plan["judge"]["temperature"] == 0.0

    adaptive = describe(_sweep(strategy=["adaptive"]), base, expand(_sweep(strategy=["adaptive"]), base))
    assert adaptive["cells"][0]["sampling"]["attacker"]["temperature"] == SAMPLING_DEFAULT
    assert plan["max_turns"] == 40 and plan["corpus_size"] == N_ATTACKS and plan["cells"][0]["mode"] == "campaign"


def test_the_stored_plan_holds_no_secret():
    base = _base()
    base.target.api_key = "sk-very-secret"
    plan = describe(_sweep(), base, expand(_sweep(), base))
    assert "sk-very-secret" not in str(plan)


def test_the_fingerprint_changes_with_the_results_and_not_with_the_schedule():
    base, cells = _base(), expand(_sweep(guardrail=["none", "input"]), _base())
    same = fingerprint(base, cells)
    assert fingerprint(_base(), expand(_sweep(guardrail=["none", "input"]), _base())) == same
    assert fingerprint(_base(max_turns=10), cells) != same
    other_prompt = _base()
    other_prompt.system_prompt_text = "a different prompt {{CANARY}}"
    assert fingerprint(other_prompt, cells) != same
    assert fingerprint(base, expand(_sweep(guardrail=["none"]), base)) != same
    # repeats and workers are not part of what decides a result
    a = describe(SweepConfig(base=Path("x"), repeats=2, workers=1), base, cells)["fingerprint"]
    b = describe(SweepConfig(base=Path("x"), repeats=9, workers=4), base, cells)["fingerprint"]
    assert a == b


def test_load_sweep_reads_the_base_from_the_sweeps_folder(tmp_path):
    (tmp_path / "base.yaml").write_text(yaml.safe_dump({
        "target": {"provider": "mock"}, "system_prompt_text": "code is {{CANARY}}",
        "attacker": {"strategy": "corpus", "max_turns": 30}}), encoding="utf-8")
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({
        "base": "base.yaml", "repeats": 2, "grid": {"guardrail": ["none", "input"]}}), encoding="utf-8")
    sweep, base = load_sweep(tmp_path / "s.yaml")
    assert sweep.repeats == 2 and base.attacker.max_turns == 30
    assert len(expand(sweep, base)) == 2
