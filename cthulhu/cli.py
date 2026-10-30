"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .campaign import run_attack_run
from .clients import LLMClient, build_client
from .config import load_config
from .corpus import available_categories, load_corpus
from .guardrail import Guardrail
from .report import build_report, build_session_report, write_report, write_session_report
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

    atk = sub.add_parser("attack", help="run multi-turn adaptive attacks")
    atk.add_argument("--config", required=True, type=Path)
    atk.add_argument("--objective", action="append", help="objective id (repeatable)")
    atk.add_argument(
        "--guardrail", action=argparse.BooleanOptionalAction, default=None,
        help="override the config's guardrail setting",
    )
    atk.add_argument("--out", type=Path, help="output directory")

    sv = sub.add_parser("serve", help="run the local web backend on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
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
    rules, canary = cfg.resolve_target()
    target = build_client(cfg.target, canary)
    print(paint(BANNER, "primary", color))
    print(f"{len(attacks)} attacks x {cfg.iterations} against {cfg.target.provider}:{cfg.target.model}")

    # Run every target call first (both passes), so the judge model loads once.
    baseline_results = run_attacks(target, attacks, rules, cfg.iterations)
    guarded_results = None
    if cfg.guardrail:
        guarded_results = run_attacks(target, attacks, rules, cfg.iterations, Guardrail())

    judge_client = build_client(cfg.judge, canary, role="judge") if cfg.judge else None
    baseline = _score(baseline_results, rules, canary, cfg, judge_client)
    guarded = _score(guarded_results, rules, canary, cfg, judge_client) if guarded_results else None

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
    results: list[AttackResult], rules: str, canary: str, cfg, judge_client: LLMClient | None
) -> list[tuple[AttackResult, CombinedVerdict]]:
    """Score each result with both scorers; skip the judge on blocked prompts."""
    items = []
    for r in results:
        det = score(r.attack, r.response, canary)
        jv = None
        if judge_client and not r.blocked:
            jv = judge(judge_client, rules, r.prompt, r.response)
        items.append((r, combine(det, jv, cfg.scoring)))
    return items


def _attack_printer(color: bool):
    def on_event(phase: str, kind: str, data: dict) -> None:
        if kind != "turn":
            return
        if data.get("blocked"):
            tag, tone = "blocked", "teal"
        elif data.get("violated"):
            tag, tone = "objective met", "crimson"
        elif data.get("leaked"):
            tag, tone = "canary leaked", "crimson"
        else:
            tag, tone = "held", "teal"
        print(f"  [{phase}] [{data['objective']}] turn {data['turn']} "
              f"({data.get('tactic', '')}): " + paint(tag, tone, color))
    return on_event


def _cmd_attack(args: argparse.Namespace, color: bool) -> int:
    cfg = load_config(args.config)
    if cfg.attacker is None:
        print("config has no 'attacker' section; add one to run multi-turn attacks",
              file=sys.stderr)
        return 2
    if args.guardrail is not None:
        cfg.guardrail = args.guardrail
    if args.out:
        cfg.output_dir = args.out
    if args.objective:
        cfg.attacker.objectives = args.objective

    if "out_of_scope" in cfg.attacker.objectives and cfg.attacker.judge_mode != "per_turn":
        print("warning: the out_of_scope objective has no deterministic signal; set "
              "judge_mode: per_turn in the config or successes may be missed", file=sys.stderr)

    print(paint(BANNER, "primary", color))
    print(f"{cfg.attacker.strategy} attacker, {len(cfg.attacker.objectives)} objective(s), "
          f"up to {cfg.attacker.max_turns} turns, judge={cfg.attacker.judge_mode}, "
          f"guardrail {'on' if cfg.guardrail else 'off'}")

    try:
        baseline, guarded = run_attack_run(cfg, on_event=_attack_printer(color))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    meta = {
        "target": f"{cfg.target.provider}:{cfg.target.model}",
        "judge": f"{cfg.judge.provider}:{cfg.judge.model}" if cfg.judge else None,
        "scoring": cfg.scoring,
        "judge_mode": cfg.attacker.judge_mode,
        "guardrail": cfg.guardrail,
    }
    report = build_session_report(baseline, guarded, meta)
    json_path, md_path = write_session_report(report, cfg.output_dir)

    met_b = sum(s.success for s in baseline)
    summary = f"baseline: {met_b}/{len(baseline)} met"
    if guarded is not None:
        summary += f", guarded: {sum(s.success for s in guarded)}/{len(guarded)} met"
    print(paint(summary, "crimson" if met_b else "teal", color))
    print(f"wrote {json_path} and {md_path}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .web.app import app
    except ImportError:
        print('the web backend needs the web extra: pip install -e ".[web]"', file=sys.stderr)
        return 2
    # Bind to loopback only. There is deliberately no flag to change the host: a
    # red-team tool must not be opened to the network by accident.
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


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
    if args.command == "attack":
        return _cmd_attack(args, color)
    if args.command == "serve":
        return _cmd_serve(args)
    return _cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
