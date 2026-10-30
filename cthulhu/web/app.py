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
from ..report import session_report_to_markdown
from .manager import RunManager

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Cthulhu", docs_url=None, redoc_url=None)
manager = RunManager()
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


@app.post("/runs")
def start_run(cfg: RunConfig):
    try:
        run_id = manager.start(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run_id}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = _require(run_id)
    out = {"id": run.id, "status": run.status, "error": run.error, "meta": run.meta}
    if run.report is not None:
        out["summary"] = _summary(run.report)
    return out


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
