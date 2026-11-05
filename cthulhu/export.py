"""JSON and CSV export of result records.

JSON keeps the sessions with their turns nested and carries the attacker's free
text tactic. CSV is a flat table with one row per turn, the session fields
repeated on each row, so it groups cleanly by category, vector or config. The
tactic is model-written free text and stays out of the CSV: only the corpus
category and vector go in, and every text cell is still guarded against
spreadsheet formula injection.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from pathlib import Path

from .results import SessionRecord, records_from_report

SESSION_COLUMNS = (
    "run_id", "phase", "target", "judge", "strategy", "attacker_model", "guardrail", "mode",
    "objective", "success", "success_turn", "ended_reason", "needs_review",
)
TURN_COLUMNS = ("turn", "category", "vector", "outcome", "deterministic_signal", "judge_verdict")
CSV_COLUMNS = SESSION_COLUMNS + TURN_COLUMNS

_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> object:
    """Make a value safe for a spreadsheet: a leading formula character is escaped."""
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def to_json(records: list[SessionRecord]) -> str:
    return json.dumps({"sessions": [asdict(r) for r in records]}, indent=2, ensure_ascii=False)


def to_csv(records: list[SessionRecord]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for rec in records:
        session = {name: getattr(rec, name) for name in SESSION_COLUMNS}
        for turn in rec.turns:
            row = {**session, **{name: getattr(turn, name) for name in TURN_COLUMNS}}
            writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return buf.getvalue()


def write_results(report: dict, out_dir: Path, run_id: str = "") -> tuple[Path, Path]:
    """Write results.json and results.csv next to the report files."""
    records = records_from_report(report, run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, csv_path = out_dir / "results.json", out_dir / "results.csv"
    json_path.write_text(to_json(records), encoding="utf-8")
    csv_path.write_text(to_csv(records), encoding="utf-8", newline="")
    return json_path, csv_path
