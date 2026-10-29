"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .clients import LLMClient, build_client
from .config import load_config
from .corpus import available_categories, load_corpus
from .guardrail import Guardrail
from .report import build_report, write_report
from .runner import AttackResult, run_attacks
from .scoring import CombinedVerdict, combine, judge, score
from .ui import BANNER, color_enabled, paint


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cthulhu", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--no-color", action="store_true", help="plain output")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run attacks against the configured target")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--category", action="append", help="limit to a category (repeatable)")
    run.add_argument(
        "--guardrail", action=argparse.BooleanOptionalAction, default=None,
        help="override the config's guardrail setting",
    )
    run.add_argument("--out", type=Path, help="output directory")

    ls = sub.add_parser("list-attacks", help="show the attack corpus")
    ls.add_argument("--category", action="append")
    return p


def _cmd_run(args: argparse.Namespace, color: bool) -> int:
    cfg = load_config(args.config)
    if args.category:
        cfg.categories = args.category
    if args.guardrail is not None:
        cfg.guardrail = args.guardrail
    if args.out:
        cfg.output_dir = args.out

    attacks = load_corpus(cfg.categories, cfg.corpus_dir)
    rules = cfg.system_prompt()
    target = build_client(cfg.target, cfg.canary)
    print(paint(BANNER, "primary", color))
    print(f"{len(attacks)} attacks x {cfg.iterations} against {cfg.target.provider}:{cfg.target.model}")

    # Run every target call first (both passes), so the judge model loads once.
    baseline_results = run_attacks(target, attacks, rules, cfg.iterations)
    guarded_results = None
    if cfg.guardrail:
        guarded_results = run_attacks(target, attacks, rules, cfg.iterations, Guardrail())

    judge_client = build_client(cfg.judge, cfg.canary, role="judge") if cfg.judge else None
    baseline = _score(baseline_results, rules, cfg, judge_client)
    guarded = _score(guarded_results, rules, cfg, judge_client) if guarded_results else None

    meta = {
        "target": f"{cfg.target.provider}:{cfg.target.model}",
        "judge": f"{cfg.judge.provider}:{cfg.judge.model}" if cfg.judge else None,
        "scoring": cfg.scoring,
        "guardrail": cfg.guardrail,
        "iterations": cfg.iterations,
    }
    report = build_report(baseline, guarded, meta)
    json_path, md_path = write_report(report, cfg.output_dir)

    for cat, s in report["summary"].items():
        tone = "crimson" if (s["guarded"] if cfg.guardrail else s["baseline"]) else "teal"
        if cfg.guardrail:
            msg = f"{s['baseline']} -> {s['guarded']} bypassed ({s['blocked']} blocked) / {s['attempts']}"
        else:
            msg = f"{s['baseline']}/{s['attempts']} bypassed"
        print(f"  {cat}: " + paint(msg, tone, color))
    if report["meta"]["judge_agreement"] is not None:
        print(f"  judge / rule agreement: {report['meta']['judge_agreement']:.0%}")
    print(f"wrote {json_path} and {md_path}")
    return 0


def _score(
    results: list[AttackResult], rules: str, cfg, judge_client: LLMClient | None
) -> list[tuple[AttackResult, CombinedVerdict]]:
    """Score each result with both scorers; skip the judge on blocked prompts."""
    items = []
    for r in results:
        det = score(r.attack, r.response, cfg.canary)
        jv = None
        if judge_client and not r.blocked:
            jv = judge(judge_client, rules, r.prompt, r.response)
        items.append((r, combine(det, jv, cfg.scoring)))
    return items


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        attacks = load_corpus(args.category)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"categories: {', '.join(available_categories())}")
    for a in attacks:
        print(f"{a.id:10} {a.category:20} {a.owasp_tag:8} {a.prompt}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    color = color_enabled(args.no_color)
    if args.command == "run":
        return _cmd_run(args, color)
    return _cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
