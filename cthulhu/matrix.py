"""Comparison matrices over result records.

This is presentation only. The counts, rates and Wilson intervals come from
asr.aggregate, nothing is recomputed here, so a matrix cell always agrees with the
ASR table. Four views:

- defence: per target, categories (or vectors) against the guardrail mode. Isolated
  mode only, since that is where a vector's own rate is clean.
- context: per target and guardrail, each vector alone (isolated) beside the same vector
  inside a campaign, with the turns it won at. It shows what only works with the
  earlier conversation behind it, and what a campaign never reached.
- session: attacker against defender, one cell per pair, the share of campaign
  sessions that broke and at which turns.

Three rules hold in every view. A guardrail mode with no data gets no column, and a cell
with no run says "not run", which is not the same as 0. Judge-only successes are never
added to proven. A cell with n below LOW_N is marked, because the count is the
information there, not the rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .asr import AsrRow, MODEL_AUTHORED, aggregate, wilson
from .results import SessionRecord

GUARDRAIL_ORDER = ("none", "input", "output", "both")
LOW_N = 10


# --- shared pieces ----------------------------------------------------------

@dataclass
class Delta:
    """The change in proven rate against the unguarded column, in percentage points."""

    points: float
    apart: bool          # the two Wilson intervals do not touch


def _rank(guardrail: str) -> tuple[int, str]:
    """Known modes in their fixed order, then any other by name."""
    known = guardrail in GUARDRAIL_ORDER
    return (GUARDRAIL_ORDER.index(guardrail) if known else len(GUARDRAIL_ORDER), guardrail)


def _guardrails(present: set[str]) -> list[str]:
    return sorted(present, key=_rank)


def delta(cell: AsrRow | None, base: AsrRow | None) -> Delta | None:
    if cell is None or base is None or not cell.n or not base.n:
        return None
    (lo, hi), (base_lo, base_hi) = cell.proven_ci, base.proven_ci
    return Delta(round((cell.proven_rate - base.proven_rate) * 100, 1), hi < base_lo or lo > base_hi)


# --- defence and vector tables ----------------------------------------------

@dataclass
class MatrixRow:
    label: str
    cells: dict[str, AsrRow | None]
    deltas: dict[str, Delta | None]


@dataclass
class DefenceTable:
    target: str
    view: str                        # "category" or "vector"
    guardrails: list[str]
    rows: list[MatrixRow]


def _interesting(row: MatrixRow) -> bool:
    return any(c is not None and (c.proven or c.judge_only or c.blocked or c.filtered)
               for c in row.cells.values())


def defence_tables(records: list[SessionRecord], view: str = "category",
                   show_all: bool = False) -> list[DefenceTable]:
    """One table per target, from the isolated records."""
    iso = [r for r in records if r.mode == "isolated"]
    dims = ("config", "category") if view == "category" else ("config", "vector")
    key = "category" if view == "category" else "vector"
    category_of = {t.vector: t.category for r in iso for t in r.turns if t.vector}
    cells: dict[tuple[str, str], dict[str, AsrRow]] = {}      # (target, label) -> guardrail -> row
    for row in aggregate(iso, by=dims):
        label = row.key[key]
        if view == "vector" and category_of.get(label):
            label = f"{label} ({category_of[label]})"
        cells.setdefault((row.key["target"], label), {})[row.key["guardrail"]] = row
    if view == "category":
        for row in aggregate(iso, by=("config",)):
            cells.setdefault((row.key["target"], "all"), {})[row.key["guardrail"]] = row

    tables = []
    for target in sorted({t for t, _ in cells}):
        labels = [lab for t, lab in cells if t == target]
        guardrails = _guardrails({g for lab in labels for g in cells[(target, lab)]})
        ordered = sorted(l for l in labels if l != "all") + (["all"] if "all" in labels else [])
        rows = []
        for label in ordered:
            have = cells[(target, label)]
            row_cells = {g: have.get(g) for g in guardrails}
            base = row_cells.get("none")
            rows.append(MatrixRow(label, row_cells, {g: delta(row_cells[g], base)
                                                     for g in guardrails if g != "none"}))
        if view == "vector" and not show_all:
            rows = [r for r in rows if _interesting(r)]
        if rows:
            tables.append(DefenceTable(target, view, guardrails, rows))
    return tables


# --- context view -----------------------------------------------------------

@dataclass
class ContextRow:
    vector: str
    isolated: AsrRow | None
    campaign: AsrRow | None
    win_turns: list[int]
    note: str                        # "only in context", "not reached in campaign" or ""


@dataclass
class ContextTable:
    target: str
    guardrail: str
    rows: list[ContextRow]


def _context_note(iso: AsrRow | None, camp: AsrRow | None) -> str:
    if camp is None or not camp.n:
        return "not reached in campaign"
    if camp.proven and iso is not None and iso.n and not iso.proven:
        return "only in context"
    return ""


def context_tables(records: list[SessionRecord], show_all: bool = False) -> list[ContextTable]:
    iso = [r for r in records if r.mode == "isolated"]
    camp = [r for r in records if r.mode == "campaign" and r.strategy == "corpus"]
    iso_rows = {(r.key["target"], r.key["guardrail"], r.key["vector"]): r
                for r in aggregate(iso, by=("config", "vector"))}
    camp_rows = {(r.key["target"], r.key["guardrail"], r.key["vector"]): r
                 for r in aggregate(camp, by=("config", "vector"))}
    wins: dict[tuple[str, str, str], list[int]] = {}
    for rec in camp:
        if rec.needs_review:
            continue
        for turn in rec.turns:
            if turn.outcome == "objective_met":
                wins.setdefault((rec.target, rec.guardrail, turn.vector), []).append(turn.turn)

    tables = []
    defenders = sorted({(t, g) for t, g, _ in iso_rows} & {(t, g) for t, g, _ in camp_rows},
                       key=lambda d: (d[0], _rank(d[1])))
    for target, guardrail in defenders:
        vectors = sorted({v for t, g, v in list(iso_rows) + list(camp_rows)
                          if (t, g) == (target, guardrail) and v != MODEL_AUTHORED})
        rows = []
        for vector in vectors:
            i, c = iso_rows.get((target, guardrail, vector)), camp_rows.get((target, guardrail, vector))
            note = _context_note(i, c)
            row = ContextRow(vector, i, c, sorted(wins.get((target, guardrail, vector), [])), note)
            shown = ((i is not None and (i.proven or i.judge_only))
                     or (c is not None and (c.proven or c.judge_only)))
            if show_all or shown:
                rows.append(row)
        if rows:
            tables.append(ContextTable(target, guardrail, rows))
    return tables


# --- session view -----------------------------------------------------------

@dataclass
class SessionCell:
    sessions: int
    proven: int
    judge_only: int
    break_turns: list[int]
    rate: float
    ci: tuple[float, float]


@dataclass
class SessionTable:
    attackers: list[tuple[str, str]]                       # (strategy, attacker model)
    defenders: list[tuple[str, str]]                       # (target, guardrail)
    cells: dict[tuple[tuple[str, str], tuple[str, str]], SessionCell] = field(default_factory=dict)


def session_table(records: list[SessionRecord]) -> SessionTable | None:
    camp = [r for r in records if r.mode == "campaign"]
    if not camp:
        return None
    groups: dict[tuple, list[SessionRecord]] = {}
    for rec in camp:
        groups.setdefault(((rec.strategy, rec.attacker_model), (rec.target, rec.guardrail)), []).append(rec)
    cells = {}
    for key, recs in groups.items():
        proven = [r for r in recs if r.success and not r.needs_review]
        judge_only = sum(1 for r in recs if r.success and r.needs_review)
        n = len(recs)
        cells[key] = SessionCell(n, len(proven), judge_only,
                                 sorted(r.success_turn for r in proven if r.success_turn),
                                 len(proven) / n, wilson(len(proven), n))
    attackers = sorted({a for a, _ in cells})
    defenders = sorted({d for _, d in cells}, key=lambda d: (d[0], _rank(d[1])))
    return SessionTable(attackers, defenders, cells)


# --- all of it --------------------------------------------------------------

@dataclass
class Matrices:
    defence: list[DefenceTable]
    vectors: list[DefenceTable]
    context: list[ContextTable]
    session: SessionTable | None

    def empty(self) -> bool:
        return not (self.defence or self.vectors or self.context or self.session)


def build_matrices(records: list[SessionRecord], show_all: bool = False) -> Matrices:
    return Matrices(defence_tables(records, "category"), defence_tables(records, "vector", show_all),
                    context_tables(records, show_all), session_table(records))
