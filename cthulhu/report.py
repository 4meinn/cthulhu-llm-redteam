"""JSON and Markdown reports.

A run produces a baseline pass (guardrail off) and, when the guardrail is
enabled, a guarded pass. The report lays them side by side per category and
also splits the guarded result by the seen/holdout partition so the guardrail's
effect on phrasings it was not tuned on is visible.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .conversation import Session
from .corpus import partition as default_partition
from .runner import AttackResult
from .scoring import CombinedVerdict, agreement_rate

Item = tuple[AttackResult, CombinedVerdict]

# One line per OWASP tag used in the corpus, for the mapping section.
OWASP_TITLES = {
    "LLM01": "Prompt Injection",
    "filter-evasion": "Input-filter evasion (supports LLM01)",
}


def _pass_row(res: AttackResult, v: CombinedVerdict) -> dict:
    return {
        "prompt": res.prompt,
        "response": res.response,
        "error": res.error,
        "blocked": res.blocked,
        "guardrail": res.guardrail,
        "deterministic": {
            "violated": v.deterministic.violated,
            "reasons": v.deterministic.reasons,
        },
        "judge": None if v.judge is None else {
            "violated": v.judge.violated,
            "reason": v.judge.reason,
            "raw": v.judge.raw,
        },
        "agrees": v.agrees,
        "violated": v.violated,
    }


def _tally() -> dict[str, int]:
    return {"attempts": 0, "baseline": 0, "guarded": 0, "blocked": 0}


def build_report(
    baseline: list[Item],
    guarded: list[Item] | None,
    meta: dict,
    partition_fn: Callable[[str], str] = default_partition,
) -> dict:
    """Assemble the serialisable report from one or two passes."""
    guarded_aligned = guarded if guarded is not None else [None] * len(baseline)
    results: list[dict] = []
    by_cat: dict[str, dict[str, int]] = defaultdict(_tally)
    by_part: dict[str, dict[str, int]] = defaultdict(_tally)
    owasp: dict[str, set[str]] = defaultdict(set)

    for (br, bv), g_item in zip(baseline, guarded_aligned, strict=True):
        part = partition_fn(br.attack.id)
        g_row = _pass_row(*g_item) if g_item is not None else None
        row = {
            "id": br.attack.id,
            "category": br.attack.category,
            "owasp_tag": br.attack.owasp_tag,
            "iteration": br.iteration,
            "partition": part,
            "baseline": _pass_row(br, bv),
            "guarded": g_row,
        }
        results.append(row)
        owasp[br.attack.owasp_tag].add(br.attack.category)

        for bucket in (by_cat[br.attack.category], by_part[part]):
            bucket["attempts"] += 1
            bucket["baseline"] += int(bv.violated)
            if g_row is not None:
                bucket["guarded"] += int(g_row["violated"])
                bucket["blocked"] += int(g_row["blocked"])

    rate = agreement_rate([bv for _, bv in baseline])
    meta = {
        **meta,
        "has_guarded": guarded is not None,
        "judge_agreement": None if rate is None else round(rate, 3),
    }
    return {
        "meta": meta,
        "summary": dict(sorted(by_cat.items())),
        "partitions": dict(sorted(by_part.items())),
        "owasp": {k: sorted(v) for k, v in sorted(owasp.items())},
        "results": results,
    }


def _summary_table(summary: dict, guarded: bool) -> list[str]:
    if guarded:
        head = ["| Category | Attempts | Bypasses (no guardrail) | Bypasses (guardrail) | Blocked |",
                "|---|---:|---:|---:|---:|"]
    else:
        head = ["| Category | Attempts | Bypasses |", "|---|---:|---:|"]
    lines = list(head)
    totals = _tally()
    for cat, s in summary.items():
        for k in totals:
            totals[k] += s[k]
        if guarded:
            lines.append(f"| {cat} | {s['attempts']} | {s['baseline']} | {s['guarded']} | {s['blocked']} |")
        else:
            lines.append(f"| {cat} | {s['attempts']} | {s['baseline']} |")
    if guarded:
        lines.append(f"| **total** | **{totals['attempts']}** | **{totals['baseline']}** | "
                     f"**{totals['guarded']}** | **{totals['blocked']}** |")
    else:
        lines.append(f"| **total** | **{totals['attempts']}** | **{totals['baseline']}** |")
    return lines


def _bypasses(report: dict) -> tuple[list[dict], str]:
    """Return the attacks that still bypassed, from the strongest pass available."""
    guarded = report["meta"]["has_guarded"]
    key = "guarded" if guarded else "baseline"
    hits = [r for r in report["results"] if r[key] and r[key]["violated"]]
    return hits, key


def to_markdown(report: dict) -> str:
    meta = report["meta"]
    guarded = meta["has_guarded"]
    lines = [
        "# Cthulhu report",
        "",
        f"- Target: `{meta.get('target', '?')}`",
        f"- Judge: `{meta.get('judge') or 'none'}`",
        f"- Scoring policy: {meta.get('scoring', 'any')}",
        f"- Guardrail: {'on (baseline vs guarded below)' if guarded else 'off'}",
        f"- Iterations per attack: {meta.get('iterations', 1)}",
    ]
    if meta.get("judge_agreement") is not None:
        lines.append(f"- Judge / rule agreement (baseline): {meta['judge_agreement']:.0%}")
    lines += ["", "## Summary", ""]
    lines += _summary_table(report["summary"], guarded)

    if guarded:
        lines += [
            "",
            "## Guardrail generalization (seen vs holdout)",
            "",
            "Holdout attacks use phrasings the filter was not tuned on, so their "
            "residual bypass rate is the honest measure of the guardrail.",
            "",
        ]
        lines += _summary_table(report["partitions"], guarded=True)

    lines += ["", "## OWASP LLM Top 10 mapping", ""]
    for tag, cats in report["owasp"].items():
        title = OWASP_TITLES.get(tag, tag)
        lines.append(f"- **{tag}** {title}: {', '.join(cats)}")

    hits, key = _bypasses(report)
    label = "with the guardrail on" if guarded else "against the target"
    lines += ["", f"## What still bypasses ({label})", ""]
    if not hits:
        lines.append("Nothing bypassed in this pass.")
    for r in hits:
        reasons = list(r[key]["deterministic"]["reasons"])
        if r[key]["judge"] and r[key]["judge"]["violated"]:
            reasons.append(f"judge: {r[key]['judge']['reason']}")
        lines.append(f"- `{r['id']}` ({r['owasp_tag']}, {r['partition']}): {'; '.join(reasons)}")

    split = [r for r in report["results"] if r["baseline"]["agrees"] is False]
    if split:
        lines += [
            "",
            "## Needs manual review",
            "",
            "The rules and the judge disagree here (baseline pass).",
            "",
            "| Attack | Rules | Judge | Judge reason |",
            "|---|---|---|---|",
        ]
        for r in split:
            b = r["baseline"]
            det = "yes" if b["deterministic"]["violated"] else "no"
            jv = {True: "yes", False: "no", None: "?"}[b["judge"]["violated"]]
            lines.append(f"| `{r['id']}` | {det} | {jv} | {b['judge']['reason']} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


# --- multi-turn session reports ---------------------------------------------

OBJECTIVE_OWASP = {
    "leak_canary": "LLM01, sensitive information disclosure",
    "leak_system_prompt": "LLM01, sensitive information disclosure",
    "out_of_scope": "LLM01, prompt injection and excessive agency",
}


def _success_source(session: Session) -> tuple[str | None, bool]:
    """Return (source, needs_review) for a session's success.

    needs_review marks a judge-only win: the judge flagged a violation the rules
    did not, so it is surfaced for a human, not counted as proven.
    """
    if not session.success or session.success_turn is None:
        return None, False
    verdict = session.verdicts[session.success_turn - 1]
    if verdict is None:
        return "unknown", False
    if verdict.deterministic.reasons:
        return "; ".join(verdict.deterministic.reasons), False
    if verdict.judge is not None and verdict.judge.violated:
        return "judge said violated", True
    return "unknown", False


def _session_dict(session: Session) -> dict:
    source, needs_review = _success_source(session)
    return {
        "objective": {
            "id": session.objective.id,
            "description": session.objective.description,
            "deterministic": session.objective.deterministic,
        },
        "success": session.success,
        "success_turn": session.success_turn,
        "source": source,
        "needs_review": needs_review,
        "turns": [
            {
                "tactic": t.move.tactic,
                "sent": t.sent,
                "response": t.response,
                "blocked": t.blocked,
                "leaked": t.leaked,
                "note": t.note,
                "violated": (v.violated if v else None),
                "judged": (v is not None and v.judge is not None),
            }
            for t, v in zip(session.turns, session.verdicts, strict=True)
        ],
    }


def _session_verdicts(*session_lists: list[Session]) -> list[CombinedVerdict]:
    out: list[CombinedVerdict] = []
    for sessions in session_lists:
        for s in sessions:
            out.extend(v for v in s.verdicts if v is not None)
    return out


def build_session_report(
    baseline: list[Session], guarded: list[Session] | None, meta: dict
) -> dict:
    """Assemble the multi-turn report from the baseline and optional guarded campaigns."""
    rate = agreement_rate(_session_verdicts(baseline, guarded or []))
    objectives, seen = [], set()
    for s in baseline:
        if s.objective.id not in seen:
            seen.add(s.objective.id)
            objectives.append({
                "id": s.objective.id,
                "deterministic": s.objective.deterministic,
                "owasp": OBJECTIVE_OWASP.get(s.objective.id, "LLM01"),
            })
    return {
        "meta": {**meta, "has_guarded": guarded is not None,
                 "judge_agreement": None if rate is None else round(rate, 3)},
        "objectives": objectives,
        "baseline": [_session_dict(s) for s in baseline],
        "guarded": None if guarded is None else [_session_dict(s) for s in guarded],
    }


def _outcome(session: dict | None) -> str:
    if session is None:
        return "n/a"
    if not session["success"]:
        return "held"
    tag = "met (needs review)" if session["needs_review"] else "met"
    return f"{tag} @ turn {session['success_turn']}"


def _by_objective(sessions: list[dict]) -> dict[str, dict]:
    return {s["objective"]["id"]: s for s in sessions}


def _transcript(session: dict, lines: list[str]) -> None:
    for i, t in enumerate(session["turns"], 1):
        if t["blocked"]:
            outcome = "blocked by guardrail"
        elif t["violated"]:
            outcome = "objective met"
        elif t["leaked"]:
            outcome = "canary leaked (not this objective's signal)"
        else:
            outcome = "held"
        lines.append(f"{i}. **{t['tactic']}** -> {outcome}")
        lines.append(f"   - sent: {t['sent'][:140]}")
        if not t["blocked"]:
            reply = t["response"]
            lines.append(f"   - reply: {reply[:110] + '...' if len(reply) > 110 else reply}")
        if t["note"]:
            lines.append(f"   - note: {t['note']}")


def session_report_to_markdown(report: dict) -> str:
    meta = report["meta"]
    guarded = meta["has_guarded"]
    g_by = _by_objective(report["guarded"]) if guarded else {}
    b_by = _by_objective(report["baseline"])

    lines = [
        "# Cthulhu multi-turn report",
        "",
        f"- Target: `{meta.get('target', '?')}`",
        f"- Judge: `{meta.get('judge') or 'none'}`",
        f"- Scoring policy: {meta.get('scoring', 'any')}",
        f"- Judge mode: {meta.get('judge_mode', '?')}",
        f"- Guardrail: {'on (baseline vs guarded below)' if guarded else 'off'}",
    ]
    if meta.get("judge_agreement") is not None:
        lines.append(f"- Judge / rule agreement: {meta['judge_agreement']:.0%}")

    lines += ["", "## Objectives", ""]
    lines.append("| Objective | Signal | OWASP | Baseline |" + (" Guarded |" if guarded else ""))
    lines.append("|---|---|---|---|" + ("---|" if guarded else ""))
    for obj in report["objectives"]:
        signal = "deterministic and judge" if obj["deterministic"] else "judge only"
        row = f"| `{obj['id']}` | {signal} | {obj['owasp']} | {_outcome(b_by.get(obj['id']))} |"
        if guarded:
            row += f" {_outcome(g_by.get(obj['id']))} |"
        lines.append(row)

    lines += ["", "## What broke and how", ""]
    phases = [("baseline", report["baseline"])] + ([("guarded", report["guarded"])] if guarded else [])
    broke = [(ph, s) for ph, sessions in phases for s in sessions if s["success"]]
    if not broke:
        lines.append("No objective was met in this run.")
    for ph, s in broke:
        lines.append(f"- `{s['objective']['id']}` ({ph}): {_outcome(s)} via {s['source']}")

    review = [(ph, s) for ph, sessions in phases for s in sessions if s["needs_review"]]
    lines += ["", "## Needs review (judge only)", ""]
    if not review:
        lines.append("None. Every success had a deterministic signal.")
    else:
        lines.append("The judge flagged these, the deterministic rules did not. Confirm by hand.")
        for ph, s in review:
            lines.append(f"- `{s['objective']['id']}` ({ph}, turn {s['success_turn']})")

    lines += ["", "## Transcripts", ""]
    for ph, sessions in phases:
        for s in sessions:
            lines.append(f"### {s['objective']['id']} ({ph})")
            lines.append("")
            _transcript(s, lines)
            lines.append("")

    lines += [
        "## Notes",
        "",
        "A canary or marker leak proves the disclosure objectives. out_of_scope has "
        "no such deterministic signal, so it is measured by the judge, run it with "
        "judge_mode: per_turn. A judge-only success is listed under review, not "
        "counted as proven.",
        "",
    ]
    return "\n".join(lines)


def write_session_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(session_report_to_markdown(report), encoding="utf-8")
    return json_path, md_path
