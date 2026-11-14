"""Sweep: a grid of configurations, each run several times.

A sweep file names a base config and a grid of axes (target model, guardrail mode,
strategy, objective, mode). Every combination is a cell, and every cell runs
`repeats` times. One run is one cell on one repeat, and its results are the unit
that is written to disk, so a long sweep can stop and pick up again.

Sampling is never pinned. A run uses the model's own default sampling unless the
endpoint sets a temperature, and the plan records which, so a result can be read
and reproduced knowing what was left to chance.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from . import __version__
from . import campaign
from .campaign import PhaseEvent
from .config import EndpointConfig, RunConfig, load_config
from .guardrail import MODES
from .corpus import load_corpus
from .export import to_json
from .report import build_phase_report, run_meta
from .results import records_from_report
from .web.manager import RunManager

GUARDRAILS = MODES
SAMPLING_DEFAULT = "endpoint/model default, unspecified"
PROVIDERS = ("mock", "ollama", "openai")


class Grid(BaseModel):
    """The axes of a sweep. An axis left empty takes the base config's own value."""

    target: list[str | EndpointConfig] = Field(default_factory=list)
    guardrail: list[str] = Field(default_factory=list)
    strategy: list[Literal["corpus", "adaptive"]] = Field(default_factory=list)
    objective: list[str] = Field(default_factory=list)
    mode: list[Literal["campaign", "isolated"]] = Field(default_factory=list)

    @field_validator("guardrail")
    @classmethod
    def _known_guardrail(cls, values: list[str]) -> list[str]:
        for value in values:
            if value not in GUARDRAILS:
                raise ValueError(f"guardrail must be one of {', '.join(GUARDRAILS)}, got '{value}'")
        return values


class SweepConfig(BaseModel):
    name: str = "sweep"
    base: Path
    out: Path = Path("out/sweep")
    repeats: int = Field(default=1, ge=1)
    workers: int = Field(default=1, ge=1)
    grid: Grid = Field(default_factory=Grid)


@dataclass
class Cell:
    """One combination of the grid."""

    id: str
    target: EndpointConfig
    guardrail: str
    strategy: str
    objective: str
    mode: str

    def run_config(self, base: RunConfig, label: str = "") -> RunConfig:
        """The config for one run of this cell, copied from the base."""
        cfg = base.model_copy(deep=True)
        cfg.target = self.target.model_copy(deep=True)
        cfg.guardrail = self.guardrail != "none"
        if cfg.guardrail:
            cfg.guardrail_mode = self.guardrail
        cfg.attacker.strategy = self.strategy
        cfg.attacker.objectives = [self.objective]
        cfg.label = label or self.id
        return cfg


def run_id(cell: Cell, repeat: int) -> str:
    return f"{cell.id}-r{repeat:03d}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._]+", "-", text.lower()).strip("-")


def parse_target(spec: str | EndpointConfig, base: EndpointConfig) -> EndpointConfig:
    """A target is a full endpoint, or 'provider:model' that keeps the base's url and settings."""
    if isinstance(spec, EndpointConfig):
        return spec
    provider, sep, model = spec.partition(":")
    if not sep or provider not in PROVIDERS or not model.strip():
        raise ValueError(f"target '{spec}' must be provider:model, provider one of {', '.join(PROVIDERS)}")
    return base.model_copy(update={"provider": provider, "model": model.strip()})


def load_sweep(path: str | Path) -> tuple[SweepConfig, RunConfig]:
    """Read a sweep file and the base config it names. A relative base is read from the sweep's folder."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sweep = SweepConfig.model_validate(raw)
    base_path = sweep.base if sweep.base.is_absolute() else path.parent / sweep.base
    return sweep, load_config(base_path)


def expand(sweep: SweepConfig, base: RunConfig) -> list[Cell]:
    """Every combination of the grid, in run order: grouped by target so a model loads once."""
    if base.attacker is None:
        raise ValueError("the base config has no 'attacker' section")
    grid = sweep.grid
    targets = [parse_target(t, base.target) for t in grid.target] or [base.target]
    guardrails = grid.guardrail or [base.guardrail_mode if base.guardrail else "none"]
    strategies = grid.strategy or [base.attacker.strategy]
    objectives = grid.objective or list(base.attacker.objectives)
    modes = grid.mode or ["campaign"]

    cells, bad = [], []
    for target, guardrail, strategy, objective, mode in itertools.product(
            targets, guardrails, strategies, objectives, modes):
        cell_id = "-".join([_slug(target.provider), _slug(target.model), guardrail, strategy,
                            _slug(objective), mode])
        if mode == "isolated" and strategy != "corpus":
            bad.append(f"{cell_id}: isolated mode needs the corpus strategy")
        if strategy == "adaptive" and base.attacker.endpoint is None:
            bad.append(f"{cell_id}: adaptive strategy needs attacker.endpoint in the base config")
        cells.append(Cell(cell_id, target, guardrail, strategy, objective, mode))
    if bad:
        raise ValueError("the grid has cells that cannot run:\n  " + "\n  ".join(bad))
    ids = [c.id for c in cells]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError("the grid repeats a cell: " + ", ".join(dupes))
    return cells


def sampling_of(endpoint: EndpointConfig | None) -> dict | None:
    """What is known about how an endpoint samples. Unset is said out loud, not left blank."""
    if endpoint is None:
        return None
    temperature = endpoint.temperature
    return {"temperature": SAMPLING_DEFAULT if temperature is None else temperature,
            "seed": "not set"}


def _scrubbed(base: RunConfig) -> dict:
    """The base config as data, without secrets or paths that do not change the result."""
    dump = base.model_dump(mode="json", exclude={"output_dir", "label", "system_prompt_file",
                                                  "system_prompt_text"})
    for endpoint in (dump.get("target"), dump.get("judge"), (dump.get("attacker") or {}).get("endpoint")):
        if endpoint:
            endpoint["api_key"] = None
    return dump


def fingerprint(base: RunConfig, cells: list[Cell]) -> str:
    """A hash of what decides the results. repeats, workers and the output folder are left out,
    so a sweep can be resumed with more repeats or on more workers."""
    corpus_ids = [a.id for a in load_corpus(base.categories, base.corpus_dir)]
    payload = {
        "base": _scrubbed(base),
        "prompt": hashlib.sha256(base._raw_system_prompt().encode("utf-8")).hexdigest(),
        "corpus": hashlib.sha256(",".join(corpus_ids).encode("utf-8")).hexdigest(),
        "cells": [{"id": c.id, "target": c.target.model_dump(mode="json", exclude={"api_key"}),
                   "guardrail": c.guardrail, "strategy": c.strategy, "objective": c.objective,
                   "mode": c.mode} for c in cells],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def describe(sweep: SweepConfig, base: RunConfig, cells: list[Cell]) -> dict:
    """The plan as it is stored in sweep.json."""
    judge = sampling_of(base.judge)
    attacker = sampling_of(base.attacker.endpoint)
    return {
        "name": sweep.name,
        "cthulhu": __version__,
        "fingerprint": fingerprint(base, cells),
        "repeats": sweep.repeats,
        "workers": sweep.workers,
        "max_turns": base.attacker.max_turns,
        "judge_mode": base.attacker.judge_mode,
        "scoring": base.scoring,
        "judge": f"{base.judge.provider}:{base.judge.model}" if base.judge else None,
        "attacker_model": (f"{base.attacker.endpoint.provider}:{base.attacker.endpoint.model}"
                           if base.attacker.endpoint else None),
        "corpus_size": len(load_corpus(base.categories, base.corpus_dir)),
        "cells": [{
            "id": c.id, "target": f"{c.target.provider}:{c.target.model}",
            "guardrail": c.guardrail, "strategy": c.strategy, "objective": c.objective,
            "mode": c.mode,
            "sampling": {"target": sampling_of(c.target), "judge": judge,
                         "attacker": attacker if c.strategy == "adaptive" else None},
        } for c in cells],
    }


@dataclass
class Plan:
    cells: int
    repeats: int
    runs: int
    max_target_calls: int
    warnings: list[str] = field(default_factory=list)


def make_plan(sweep: SweepConfig, base: RunConfig, cells: list[Cell]) -> Plan:
    """What the sweep will cost, without running anything. Calls are an upper bound."""
    n_attacks = len(load_corpus(base.categories, base.corpus_dir))
    max_turns = base.attacker.max_turns
    per_run = 0
    short = 0
    for cell in cells:
        if cell.mode == "isolated":
            per_run += n_attacks
        elif cell.strategy == "corpus":
            per_run += min(max_turns, n_attacks)
            short += max_turns < n_attacks
        else:
            per_run += max_turns
    warnings = []
    if short:
        warnings.append(
            f"{short} campaign cell(s) use the corpus with max_turns {max_turns}, below the "
            f"corpus size {n_attacks}: the vectors past turn {max_turns} are never tried")
    return Plan(len(cells), sweep.repeats, len(cells) * sweep.repeats,
                per_run * sweep.repeats, warnings)


def format_plan(plan: Plan, cells: list[Cell]) -> str:
    lines = [f"{plan.cells} cell(s) x {plan.repeats} repeat(s) = {plan.runs} run(s), "
             f"up to {plan.max_target_calls} target call(s)"]
    lines += [f"  {c.id}" for c in cells]
    lines += [f"warning: {w}" for w in plan.warnings]
    return "\n".join(lines)


# --- running ----------------------------------------------------------------

class SweepMismatch(ValueError):
    """The folder holds a sweep made from a different plan."""


@dataclass
class RunSpec:
    cell: Cell
    repeat: int

    @property
    def id(self) -> str:
        return run_id(self.cell, self.repeat)


@dataclass
class SweepResult:
    done: list[str] = field(default_factory=list)       # runs finished and written this time
    skipped: int = 0                                    # runs already on disk from before
    failed: dict[str, str] = field(default_factory=dict)  # run id -> why it did not finish
    cancelled: bool = False


def _write_atomic(path: Path, text: str) -> None:
    """Write beside the target and rename, so the file is either whole or absent."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def prepare(plan: dict, out: Path) -> Path:
    """Create the sweep folder and sweep.json, or check that an existing one is the same sweep.

    Returns the runs folder. Leftover temp files from a run that was cut off are removed.
    """
    runs = out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stored = out / "sweep.json"
    if stored.is_file():
        old = json.loads(stored.read_text(encoding="utf-8"))
        if old.get("fingerprint") != plan["fingerprint"]:
            raise SweepMismatch(
                f"{out} holds a sweep made from a different plan (config, prompt, corpus or "
                "grid changed). Use a new output folder, or delete this one to start over.")
        created = old.get("created")
    else:
        created = None
    plan = {**plan, "created": created or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _write_atomic(stored, json.dumps(plan, indent=2, ensure_ascii=False))
    for stale in runs.glob("*.tmp"):
        stale.unlink()
    return runs


def pending_runs(cells: list[Cell], repeats: int, runs_dir: Path) -> tuple[list[RunSpec], int]:
    """The runs not yet on disk, and how many were. Within a target the repeats go round the
    cells, so an interrupted sweep has a balanced set of results, not all of the first cells."""
    done = {p.name[: -len(".results.json")] for p in runs_dir.glob("*.results.json")}
    order: dict[tuple[str, str], int] = {}
    for cell in cells:
        order.setdefault((cell.target.provider, cell.target.model), len(order))
    specs = [(order[(c.target.provider, c.target.model)], rep, i, RunSpec(c, rep))
             for i, c in enumerate(cells) for rep in range(1, repeats + 1)]
    todo = [spec for *_, spec in sorted(specs, key=lambda t: t[:3]) if spec.id not in done]
    return todo, len(specs) - len(todo)


def _cell_runner(cell: Cell) -> Callable:
    phase = "baseline" if cell.guardrail == "none" else "guarded"

    def runner(cfg: RunConfig, on_event: PhaseEvent, should_stop: Callable[[], bool]) -> dict:
        sessions = campaign.run_phase(
            cfg, guardrail=cell.guardrail, mode=cell.mode,
            on_event=lambda kind, data: on_event(phase, kind, data), should_stop=should_stop)
        return build_phase_report(sessions, phase, {**run_meta(cfg), "mode": cell.mode})

    return runner


def run_sweep(
    sweep: SweepConfig,
    base: RunConfig,
    cells: list[Cell],
    *,
    manager: RunManager | None = None,
    on_progress: Callable[[str, str, SweepResult, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_start: Callable[[str, int], None] | None = None,
    poll: float = 0.05,
) -> SweepResult:
    """Run every pending run through the run manager, writing each result file as it finishes.

    A run is written only when it completes, so what is on disk is always whole, and running
    the same sweep again does the rest. on_progress(run_id, status, result, total_pending) is
    called when a run ends, on_start(run_id, total_pending) when one begins. should_stop, or
    Ctrl+C, cancels what is running and returns.
    """
    manager = manager or RunManager()
    runs_dir = prepare(describe(sweep, base, cells), sweep.out)
    todo, skipped = pending_runs(cells, sweep.repeats, runs_dir)
    result = SweepResult(skipped=skipped)
    total = len(todo)
    active: dict[str, RunSpec] = {}

    def settle(manager_id: str, spec: RunSpec) -> None:
        run = manager.get(manager_id)
        if run.status == "done" and run.report is not None:
            records = records_from_report(run.report, spec.id)
            _write_atomic(runs_dir / f"{spec.id}.results.json", to_json(records))
            result.done.append(spec.id)
        else:
            result.failed[spec.id] = run.error or run.status
        if on_progress:
            on_progress(spec.id, run.status, result, total)

    try:
        while todo or active:
            if should_stop is not None and should_stop():
                raise KeyboardInterrupt
            while todo and len(active) < sweep.workers:
                spec = todo.pop(0)
                manager_id = manager.start(spec.cell.run_config(base, spec.id), runner=_cell_runner(spec.cell))
                active[manager_id] = spec
                if on_start:
                    on_start(spec.id, total)
            time.sleep(poll)
            for manager_id, spec in list(active.items()):
                if manager.get(manager_id).terminal:
                    del active[manager_id]
                    settle(manager_id, spec)
    except KeyboardInterrupt:
        result.cancelled = True
        manager.cancel_all()
        while active:                       # let the cancelled runs wind down, none is written
            time.sleep(poll)
            for manager_id in [m for m in active if manager.get(m).terminal]:
                spec = active.pop(manager_id)
                if manager.get(manager_id).status == "done":   # it finished before the cancel landed
                    settle(manager_id, spec)
    return result
