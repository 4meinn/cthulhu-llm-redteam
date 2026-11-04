"""Markdown and CSV for the comparison matrices.

The tables are built in matrix.py. This only writes them out: matrix.md reads like a
report, matrix.csv is one tidy row per cell for analysis.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .asr import AsrRow
from .export import safe_cell
from .matrix import (
    LOW_N, ContextRow, ContextTable, DefenceTable, Delta, Matrices, SessionCell, SessionTable,
    build_matrices,
)
from .results import SessionRecord

LOW_N_MARK = "†"      # the dagger
NOT_RUN = "not run"
NO_RESULT = "no result"

# --- Markdown ---------------------------------------------------------------

NOTES = (
    "Rates are proven successes over attack turns that produced a result, with a Wilson 95% "
    "interval. Blocked turns count as defended, error turns are left out. Judge-only "
    "successes are shown beside a rate and never added to it. "
    f"{LOW_N_MARK} marks n under {LOW_N}: read the count, not the rate. "
    "\"not run\" means no run covered that cell, which is not the same as 0.",
    "The change against the unguarded column is in percentage points. \"Intervals apart\" means "
    "the two Wilson intervals do not touch, so a real difference is likely. \"Intervals overlap\" "
    "is unclear: it does not mean there is no difference, the sample may just be too small to "
    "tell. This is not a significance test.",
)
CONTEXT_NOTE = (
    "A campaign stops at its first success, so a vector later in the corpus is tried less often "
    "than an earlier one: \"tried\" is how many times it was reached. \"only in context\" marks a "
    "vector that won in a campaign and never won on its own."
)


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)"


def _cell_text(row: AsrRow | None) -> str:
    if row is None:
        return NOT_RUN
    if not row.n:
        return NO_RESULT + (f" ({row.error} error(s))" if row.error else "")
    text = _pct(row.proven, row.n) + (f" {LOW_N_MARK}" if row.n < LOW_N else "")
    extras = []
    if row.judge_only:
        extras.append(f"+{row.judge_only} judge only")
    if row.blocked:
        extras.append(f"{row.blocked} blocked")
    if row.error:
        extras.append(f"{row.error} error(s)")
    return text + (f" ({', '.join(extras)})" if extras else "")


def _delta_text(d: Delta | None) -> str:
    if d is None:
        return "n/a"
    return f"{d.points:+.1f} points, " + ("intervals apart" if d.apart else "intervals overlap, unclear")


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _defence_md(table: DefenceTable) -> list[str]:
    what = "category" if table.view == "category" else "vector"
    versus = [g for g in table.guardrails if g != "none"] if "none" in table.guardrails else []
    head = [what.capitalize()] + table.guardrails + [f"{g} vs none" for g in versus]
    lines = [f"### Defence matrix by {what}: {table.target} (isolated)", "",
             _md_row(head), "|" + "---|" * len(head)]
    for row in table.rows:
        lines.append(_md_row([row.label] + [_cell_text(row.cells[g]) for g in table.guardrails]
                             + [_delta_text(row.deltas.get(g)) for g in versus]))
    return lines + [""]


def _context_cell(row: ContextRow) -> tuple[str, str]:
    camp = row.campaign
    tried = str(camp.n) if camp is not None and camp.n else "not reached"
    if camp is None or not camp.n:
        return tried, ""
    won = str(camp.proven)
    if row.win_turns:
        won += " (turn " + ", ".join(str(t) for t in row.win_turns) + ")"
    return tried, won


def _context_md(table: ContextTable) -> list[str]:
    head = ["Vector", "Isolated", "Campaign tried", "Campaign won", "Note"]
    lines = [f"### Context effect: {table.target}, guardrail {table.guardrail}", "",
             _md_row(head), "|" + "---|" * len(head)]
    for row in table.rows:
        tried, won = _context_cell(row)
        note = "" if row.campaign is None or not row.campaign.n else row.note   # "not reached" is in the tried column
        lines.append(_md_row([row.vector, _cell_text(row.isolated), tried, won, note]))
    return lines + [""]


def _session_cell_text(cell: SessionCell | None) -> str:
    if cell is None:
        return NOT_RUN
    text = f"{cell.proven}/{cell.sessions} proven ({100 * cell.rate:.1f}%)"
    if cell.sessions < LOW_N:
        text += f" {LOW_N_MARK}"
    if cell.break_turns:
        text += ", broke at turn " + ", ".join(str(t) for t in cell.break_turns)
    if cell.judge_only:
        text += f" (+{cell.judge_only} judge only)"
    return text


def _attacker_label(attacker: tuple[str, str]) -> str:
    strategy, model = attacker
    return f"{strategy} ({model})" if model else strategy


def _session_md(table: SessionTable) -> list[str]:
    head = ["Attacker"] + [f"{t}, guardrail {g}" for t, g in table.defenders]
    lines = ["### Session matrix: attacker against defender (campaign sessions)", "",
             _md_row(head), "|" + "---|" * len(head)]
    for attacker in table.attackers:
        lines.append(_md_row([_attacker_label(attacker)]
                             + [_session_cell_text(table.cells.get((attacker, d))) for d in table.defenders]))
    return lines + [""]


def matrix_markdown(matrices: Matrices) -> str:
    lines = ["# Cthulhu comparison matrix", ""]
    if matrices.empty():
        return "\n".join(lines + ["No results to compare.", ""])
    for table in matrices.defence:
        lines += _defence_md(table)
    for table in matrices.vectors:
        lines += _defence_md(table)
    for table in matrices.context:
        lines += _context_md(table)
    if matrices.context:
        lines += [CONTEXT_NOTE, ""]
    if matrices.session:
        lines += _session_md(matrices.session)
    return "\n".join(lines + ["## Reading these tables", ""] + [n + "\n" for n in NOTES])


# --- CSV --------------------------------------------------------------------

CSV_COLUMNS = (
    "view", "target", "mode", "strategy", "attacker_model", "guardrail", "row", "n", "proven",
    "proven_rate", "ci_low", "ci_high", "judge_only", "blocked", "error", "low_n",
    "delta_points", "intervals", "break_turns", "note",
)


def _asr_fields(row: AsrRow | None) -> dict:
    if row is None:
        return {}
    ci = row.proven_ci or (None, None)
    return {"n": row.n, "proven": row.proven, "proven_rate": row.proven_rate, "ci_low": ci[0],
            "ci_high": ci[1], "judge_only": row.judge_only, "blocked": row.blocked,
            "error": row.error, "low_n": row.n < LOW_N}


def matrix_rows(matrices: Matrices) -> list[dict]:
    """Every cell of every view as one tidy row, so the CSV groups and filters cleanly."""
    out: list[dict] = []
    for view, tables in (("defence_category", matrices.defence), ("defence_vector", matrices.vectors)):
        for table in tables:
            for row in table.rows:
                for g in table.guardrails:
                    cell = row.cells[g]
                    if cell is None:
                        continue
                    d = row.deltas.get(g)
                    out.append({"view": view, "target": table.target, "mode": "isolated",
                                "strategy": "corpus", "guardrail": g, "row": row.label,
                                **_asr_fields(cell),
                                "delta_points": d.points if d else None,
                                "intervals": ("apart" if d.apart else "overlap") if d else None})
    for table in matrices.context:
        for row in table.rows:
            for mode, cell in (("isolated", row.isolated), ("campaign", row.campaign)):
                if cell is None:
                    continue
                out.append({"view": "context", "target": table.target, "mode": mode,
                            "strategy": "corpus", "guardrail": table.guardrail, "row": row.vector,
                            **_asr_fields(cell),
                            "break_turns": " ".join(str(t) for t in row.win_turns) if mode == "campaign" else None,
                            "note": row.note if mode == "campaign" else None})
    if matrices.session:
        table = matrices.session
        pairs = [(a, d) for a in table.attackers for d in table.defenders if (a, d) in table.cells]
        for attacker, defender in pairs:
            cell = table.cells[(attacker, defender)]
            out.append({"view": "session", "target": defender[0], "mode": "campaign",
                        "strategy": attacker[0], "attacker_model": attacker[1],
                        "guardrail": defender[1], "row": "sessions", "n": cell.sessions,
                        "proven": cell.proven, "proven_rate": round(cell.rate, 4),
                        "ci_low": cell.ci[0], "ci_high": cell.ci[1], "judge_only": cell.judge_only,
                        "low_n": cell.sessions < LOW_N,
                        "break_turns": " ".join(str(t) for t in cell.break_turns)})
    return out


def matrix_csv(matrices: Matrices) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in matrix_rows(matrices):
        writer.writerow({c: safe_cell(row.get(c)) for c in CSV_COLUMNS})
    return buf.getvalue()


def write_matrix(records: list[SessionRecord], out_dir: Path, show_all: bool = False) -> tuple[Path, Path]:
    """Write matrix.md and matrix.csv for any set of records."""
    matrices = build_matrices(records, show_all)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, csv_path = out_dir / "matrix.md", out_dir / "matrix.csv"
    md_path.write_text(matrix_markdown(matrices), encoding="utf-8")
    csv_path.write_text(matrix_csv(matrices), encoding="utf-8", newline="")
    return md_path, csv_path
