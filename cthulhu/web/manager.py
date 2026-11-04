"""Run manager: drives attack runs in background threads and buffers their events.

Each run executes in its own thread so several can run at once and the HTTP layer
stays responsive. The engine's on_event callback feeds an ordered event buffer;
a subscriber replays what it missed and then follows live until run_finished.
Cancellation flips a flag the engine checks between turns.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

from .. import campaign
from ..config import RunConfig
from ..report import build_session_report, run_meta


class Run:
    def __init__(self, run_id: str, meta: dict, label: str = "", plan: dict | None = None) -> None:
        self.id = run_id
        self.meta = meta
        self.label = label
        self.plan = plan or {}
        self.status = "running"          # running | done | cancelled | error
        self.events: list[dict] = []
        self.report: dict | None = None
        self.error: str | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._new = threading.Event()

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "cancelled", "error")

    def emit(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)
        self._new.set()


def _plan(cfg: RunConfig) -> dict:
    """What a run will do, so the UI can show progress: sessions are objectives x phases."""
    return {
        "objectives": len(cfg.attacker.objectives),
        "phases": 2 if cfg.guardrail else 1,
        "max_turns": cfg.attacker.max_turns,
    }


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def start(self, cfg: RunConfig) -> str:
        if cfg.attacker is None:
            raise ValueError("config has no 'attacker' section")
        with self._lock:
            label = (cfg.label or "").strip() or f"{cfg.target.model} #{len(self._runs) + 1}"
            run = Run(uuid.uuid4().hex, run_meta(cfg), label, _plan(cfg))
            self._runs[run.id] = run
        threading.Thread(target=self._worker, args=(run, cfg), daemon=True).start()
        return run.id

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        """Every run, oldest first."""
        with self._lock:
            return list(self._runs.values())

    def cancel_all(self) -> int:
        """Ask every running run to stop. Returns how many were still running."""
        return sum(self.cancel(run.id) for run in self.list())

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is not None and not run.terminal:
            run._cancel.set()
            return True
        return False

    def iter_events(self, run_id: str) -> Iterator[dict]:
        """Yield this run's events in order, replaying then following live, and
        return after the final run_finished event."""
        run = self._runs.get(run_id)
        if run is None:
            return
        i = 0
        while True:
            with run._lock:
                pending = run.events[i:]
            for event in pending:
                i += 1
                yield event
                if event.get("kind") == "run_finished":
                    return
            run._new.wait(timeout=0.5)
            run._new.clear()

    def _worker(self, run: Run, cfg: RunConfig) -> None:
        def on_event(phase: str, kind: str, data: dict) -> None:
            run.emit({"phase": phase, "kind": kind, **data})

        try:
            baseline, guarded = campaign.run_attack_run(
                cfg, on_event=on_event, should_stop=run._cancel.is_set)
            run.report = build_session_report(baseline, guarded, run.meta)
            run.status = "cancelled" if run._cancel.is_set() else "done"
        except Exception as exc:                 # a run must never take the server down
            run.error = str(exc)
            run.status = "error"
            run.emit({"phase": "", "kind": "error", "message": str(exc)})
        run.emit({"phase": "", "kind": "run_finished", "status": run.status})
