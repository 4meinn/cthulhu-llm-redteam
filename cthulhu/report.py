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
