"""FastAPI backend for Cthulhu.

Serves on 127.0.0.1 only (see cli serve). A Host header check rejects any request
that did not arrive as localhost, which stops a remote page from using a browser
to reach this server through DNS rebinding. No CORS is enabled, so only the UI
served from the same origin can call it.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import RunConfig
from ..export import to_csv, to_json
from ..report import session_report_to_markdown
from ..results import records_from_report
from ..sweep import SweepMismatch
from .manager import RunManager
from .sweeps import SweepBusy, SweepManager, SweepRequest

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Cthulhu", docs_url=None, redoc_url=None)
manager = RunManager()
sweeps = SweepManager()
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.middleware("http")
async def _only_localhost(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "host not allowed"}, status_code=400)
    return await call_next(request)


def _require(run_id: str):
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return run


def _summary(report: dict) -> dict:
    def tally(key: str) -> dict:
        sessions = report.get(key) or []
        return {"met": sum(s["success"] for s in sessions), "total": len(sessions)}

    out = {"baseline": tally("baseline")}
    if report.get("guarded") is not None:
        out["guarded"] = tally("guarded")
    return out


@app.get("/")
def root():
    return FileResponse(_STATIC / "index.html")


def _entry(run) -> dict:
    out = {"id": run.id, "label": run.label, "status": run.status, "error": run.error,
           "meta": run.meta, "plan": run.plan}
    if run.report is not None:
        out["summary"] = _summary(run.report)
    return out


@app.get("/runs")
def list_runs():
    return {"runs": [_entry(run) for run in manager.list()]}


@app.post("/runs/cancel-all")
def cancel_all_runs():
    return {"cancelled": manager.cancel_all()}


@app.post("/runs")
def start_run(cfg: RunConfig):
    try:
        run_id = manager.start(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    return _entry(_require(run_id))


@app.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    _require(run_id)
    return {"cancelled": manager.cancel(run_id)}


@app.get("/runs/{run_id}/events")
def run_events(run_id: str):
    _require(run_id)

    def stream():
        for event in manager.iter_events(run_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/runs/{run_id}/report")
def run_report(run_id: str):
    run = _require(run_id)
    if run.report is None:
        raise HTTPException(status_code=409, detail="report not ready")
    return run.report


@app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
def run_report_markdown(run_id: str):
    run = _require(run_id)
    if run.report is None:
        raise HTTPException(status_code=409, detail="report not ready")
    return session_report_to_markdown(run.report)


@app.get("/runs/{run_id}/results.json", response_class=PlainTextResponse)
def run_results_json(run_id: str):
    run = _require(run_id)
    if run.report is None:
        raise HTTPException(status_code=409, detail="report not ready")
    return PlainTextResponse(to_json(records_from_report(run.report, run.id)),
                             media_type="application/json")


@app.get("/runs/{run_id}/results.csv", response_class=PlainTextResponse)
def run_results_csv(run_id: str):
    run = _require(run_id)
    if run.report is None:
        raise HTTPException(status_code=409, detail="report not ready")
    return PlainTextResponse(to_csv(records_from_report(run.report, run.id)),
                             media_type="text/csv; charset=utf-8")


# --- sweeps -----------------------------------------------------------------
# One sweep at a time, run on the server. It goes on if the panel is closed, and what
# finished is on disk under out/sweeps/<name>, so the same request again carries on.

def _sweep_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (SweepBusy, SweepMismatch)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _require_sweep(sweep_id: str) -> dict:
    info = sweeps.get(sweep_id)
    if info is None:
        raise HTTPException(status_code=404, detail="unknown sweep")
    return info


@app.post("/sweeps/plan")
def plan_sweep(req: SweepRequest):
    try:
        return sweeps.plan(req)
    except ValueError as exc:
        raise _sweep_error(exc) from exc


@app.get("/sweeps")
def list_sweeps():
    return {"sweeps": sweeps.list()}


@app.post("/sweeps")
def start_sweep(req: SweepRequest):
    try:
        return {"sweep_id": sweeps.start(req)}
    except (ValueError, SweepBusy) as exc:
        raise _sweep_error(exc) from exc


@app.get("/sweeps/{sweep_id}")
def get_sweep(sweep_id: str):
    return _require_sweep(sweep_id)


@app.post("/sweeps/{sweep_id}/cancel")
def cancel_sweep(sweep_id: str):
    _require_sweep(sweep_id)
    return {"cancelled": sweeps.cancel(sweep_id)}


@app.get("/sweeps/{sweep_id}/events")
def sweep_events(sweep_id: str):
    info = _require_sweep(sweep_id)
    if not info["live"]:
        raise HTTPException(status_code=409, detail="this sweep is not running in this server session")

    def stream():
        for event in sweeps.iter_events(sweep_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/sweeps/{sweep_id}/matrix")
def sweep_matrix(sweep_id: str):
    _require_sweep(sweep_id)
    data = sweeps.matrix(sweep_id)
    if data is None:
        raise HTTPException(status_code=409, detail="no finished run yet")
    return data
