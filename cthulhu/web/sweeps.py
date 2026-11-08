"""Sweep manager: runs one sweep at a time in a background thread for the panel.

The sweep itself is cthulhu.sweep.run_sweep, unchanged. This only starts it on a thread, keeps
its progress and events, and reads results back from the sweep folder. Nothing here is
kept only in memory that matters: every finished run is a file in the sweep's folder, so a
server that stops loses only the run in progress, and the same request again carries on.

The folder is chosen here, from the sweep's name, never by the client, and file paths in
the config (a custom corpus, a system prompt file) are not read for a request that came over
HTTP. A custom corpus stays a command line feature.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..asr import asr_section, merge_results, result_files
from ..config import RunConfig
from ..matrix import build_matrices
from ..matrix_render import matrix_json
from ..sweep import (
    Grid, SweepConfig, SweepMismatch, SweepResult, describe, expand, make_plan, pending_runs,
    run_sweep, _slug,
)
from .inline import inline_config
from .manager import RunManager

DEFAULT_ROOT = Path("out") / "sweeps"
MAX_WORKERS = 4
MAX_NAME = 60


class SweepBusy(RuntimeError):
    """A sweep is already running. The panel runs one at a time."""


class SweepRequest(BaseModel):
    """What the panel sends. The base config is inline, never a path."""

    name: str
    base: RunConfig
    repeats: int = Field(default=1, ge=1)
    workers: int = Field(default=1, ge=1, le=MAX_WORKERS)
    grid: Grid = Field(default_factory=Grid)


def sweep_slug(name: str) -> str:
    slug = _slug(name.strip())[:MAX_NAME].strip("-.")
    if not slug:
        raise ValueError("the sweep needs a name with letters or digits in it")
    return slug


class Sweep:
    def __init__(self, sweep_id: str, config: SweepConfig, plan: dict, total: int, pending: int, skipped: int):
        self.id = sweep_id
        self.config = config
        self.plan = plan
        self.total = total                  # every run of the grid
        self.pending = pending              # runs that were not on disk when it started
        self.skipped = skipped
        self.status = "running"             # running | done | cancelled | error
        self.result = SweepResult(skipped=skipped)
        self.active: list[str] = []
        self.error: str | None = None
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.events: list[dict] = []
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._new = threading.Event()

    @property
    def terminal(self) -> bool:
        return self.status != "running"

    def emit(self, event: dict) -> None:
        with self._lock:
            self.events.append({**event, "done": len(self.result.done), "failed": len(self.result.failed),
                                "skipped": self.skipped, "pending": self.pending})
        self._new.set()

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.config.name, "status": self.status, "live": True,
            "started": self.started, "total": self.total, "pending": self.pending,
            "skipped": self.skipped, "done": len(self.result.done), "failed": dict(self.result.failed),
            "active": list(self.active), "error": self.error,
            "repeats": self.config.repeats, "workers": self.config.workers, "plan": self.plan,
        }


class SweepManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self._sweeps: dict[str, Sweep] = {}
        self._lock = threading.Lock()

    # --- preparing a request -------------------------------------------------

    def _build(self, req: SweepRequest):
        slug = sweep_slug(req.name)
        base = inline_config(req.base)
        config = SweepConfig(name=slug, base=Path("inline"), out=self.root / slug,
                             repeats=req.repeats, workers=req.workers, grid=req.grid)
        cells = expand(config, base)
        return slug, config, base, cells

    def plan(self, req: SweepRequest) -> dict:
        """What the sweep would do, without starting anything or writing a file."""
        slug, config, base, cells = self._build(req)
        plan = make_plan(config, base, cells)
        runs_dir = config.out / "runs"
        _, on_disk = pending_runs(cells, config.repeats, runs_dir)
        stored = describe(config, base, cells)
        return {
            "id": slug, "cells": [c.id for c in cells], "n_cells": plan.cells, "repeats": plan.repeats,
            "runs": plan.runs, "max_target_calls": plan.max_target_calls, "warnings": plan.warnings,
            "on_disk": on_disk, "pending": plan.runs - on_disk,
            "state": self._state(config.out, stored["fingerprint"]),
        }

    def _state(self, out: Path, fingerprint: str) -> str:
        """new, resume (same plan already in the folder) or different (the folder holds another plan)."""
        stored = out / "sweep.json"
        if not stored.is_file():
            return "new"
        old = json.loads(stored.read_text(encoding="utf-8"))
        return "resume" if old.get("fingerprint") == fingerprint else "different"

    # --- running -------------------------------------------------------------

    def start(self, req: SweepRequest) -> str:
        slug, config, base, cells = self._build(req)
        with self._lock:
            if any(not s.terminal for s in self._sweeps.values()):
                raise SweepBusy("a sweep is already running")
            stored = describe(config, base, cells)
            if self._state(config.out, stored["fingerprint"]) == "different":
                raise SweepMismatch(
                    f"'{slug}' holds a sweep made from a different plan (config, prompt, corpus or grid "
                    "changed). Use another name, or delete that folder to start over.")
            _, on_disk = pending_runs(cells, config.repeats, config.out / "runs")
            total = len(cells) * config.repeats
            sweep = Sweep(slug, config, {**stored, "cells": [c["id"] for c in stored["cells"]]},
                          total, total - on_disk, on_disk)
            self._sweeps[slug] = sweep
        threading.Thread(target=self._worker, args=(sweep, base, cells), daemon=True).start()
        return slug

    def _worker(self, sweep: Sweep, base: RunConfig, cells: list) -> None:
        def on_start(run_id: str, total: int) -> None:
            sweep.active.append(run_id)
            sweep.emit({"kind": "run_started", "run_id": run_id})

        def on_progress(run_id: str, status: str, result: SweepResult, total: int) -> None:
            sweep.result = result                     # the live object, so counts are current
            if run_id in sweep.active:
                sweep.active.remove(run_id)
            kind = "run_done" if run_id in result.done else "run_failed"
            sweep.emit({"kind": kind, "run_id": run_id, "status": status})

        try:
            result = run_sweep(sweep.config, base, cells, manager=RunManager(), on_progress=on_progress,
                               on_start=on_start, should_stop=sweep._cancel.is_set)
            sweep.result = result
            sweep.status = "cancelled" if result.cancelled else "done"
        except Exception as exc:                       # a sweep must never take the server down
            sweep.error = str(exc)
            sweep.status = "error"
            sweep.emit({"kind": "error", "message": str(exc)})
        sweep.active.clear()
        sweep.emit({"kind": "sweep_finished", "status": sweep.status})

    def cancel(self, sweep_id: str) -> bool:
        sweep = self._sweeps.get(sweep_id)
        if sweep is not None and not sweep.terminal:
            sweep._cancel.set()
            return True
        return False

    # --- reading -------------------------------------------------------------

    def _on_disk(self, slug: str) -> dict | None:
        folder = self.root / slug
        stored = folder / "sweep.json"
        if not stored.is_file():
            return None
        plan = json.loads(stored.read_text(encoding="utf-8"))
        runs = len(list((folder / "runs").glob("*.results.json")))
        total = len(plan.get("cells", [])) * plan.get("repeats", 1)
        return {"id": slug, "name": plan.get("name", slug), "status": "idle", "live": False,
                "created": plan.get("created"), "total": total, "done": runs, "repeats": plan.get("repeats"),
                "workers": plan.get("workers"), "plan": {**plan, "cells": [c["id"] for c in plan.get("cells", [])]},
                "failed": {}, "active": [], "error": None}

    def get(self, sweep_id: str) -> dict | None:
        live = self._sweeps.get(sweep_id)
        if live is not None:
            return live.summary()
        if sweep_id != _slug(sweep_id) or not sweep_id:
            return None
        return self._on_disk(sweep_id)

    def list(self) -> list[dict]:
        """Every sweep: the live ones, and the ones whose folder is on disk."""
        found = {s.id: s.summary() for s in self._sweeps.values()}
        if self.root.is_dir():
            for folder in sorted(self.root.iterdir()):
                if folder.is_dir() and folder.name not in found:
                    entry = self._on_disk(folder.name)
                    if entry is not None:
                        found[folder.name] = entry
        return list(found.values())

    def iter_events(self, sweep_id: str) -> Iterator[dict]:
        """A live sweep's events in order, replayed then followed, until sweep_finished."""
        sweep = self._sweeps.get(sweep_id)
        if sweep is None:
            return
        i = 0
        while True:
            with sweep._lock:
                pending = sweep.events[i:]
            for event in pending:
                i += 1
                yield event
                if event.get("kind") == "sweep_finished":
                    return
            sweep._new.wait(timeout=0.5)
            sweep._new.clear()

    def matrix(self, sweep_id: str) -> dict | None:
        """The ASR rows and the matrices from the result files so far, or None when there are none.

        It reads the finished runs only, so while a sweep is running this is partial."""
        info = self.get(sweep_id)
        if info is None:
            return None
        try:
            records, _ = merge_results(result_files([self.root / sweep_id / "runs"]))
        except ValueError:
            return None
        return {"id": sweep_id, "runs": len({r.run_id for r in records}), "sessions": len(records),
                "partial": info["status"] == "running", "asr": asr_section(records),
                "matrix": matrix_json(build_matrices(records))}
