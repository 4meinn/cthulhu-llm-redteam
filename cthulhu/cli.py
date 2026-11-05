"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .asr import merge_results, result_files, write_aggregate, write_asr, write_review
from .campaign import run_attack_run
from .clients import LLMClient, build_client
from .config import load_config
from .corpus import available_categories, load_corpus
from .export import write_results
from .guardrail import Guardrail
from .matrix_render import write_matrix
from .report import build_report, build_session_report, run_meta, write_report, write_session_report
from .runner import AttackResult, run_attacks
from .scoring import CombinedVerdict, combine, judge, score
from .sweep import (
    SweepMismatch, SweepResult, expand, format_plan, load_sweep, make_plan, run_sweep,
)
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
    atk.add_argument(
        "--guardrail-mode", choices=("input", "output", "both"), default=None,
        help="which guardrail stages the guarded phase uses (turns the guardrail on)",
    )
    atk.add_argument("--out", type=Path, help="output directory")

    sv = sub.add_parser("serve", help="run the local web backend on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    sub.add_parser("app", help="open the web interface in a desktop window")

    sw = sub.add_parser("sweep", help="run a grid of configs, each several times")
    sw.add_argument("--config", required=True, type=Path)
    sw.add_argument("--plan", action="store_true", help="show what would run and stop")
    sw.add_argument("--workers", type=int, help="runs at the same time (default from the file)")
    sw.add_argument("--out", type=Path, help="output folder (default from the file)")

    ag = sub.add_parser("aggregate", help="combine results files into attack success rates")
    ag.add_argument("paths", nargs="+", type=Path, help="results.json files, or sweep folders")
    ag.add_argument("--out", type=Path, help="where to write the asr and matrix files")
    ag.add_argument("--all", action="store_true",
                    help="keep the all-zero vector rows the matrix leaves out by default")
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
        if kind == "session_end":
            detail = f" ({data['ended_detail']})" if data.get("ended_detail") else ""
            print(f"  [{phase}] [{data['objective']}] ended: {data.get('ended_reason', '?')}{detail}"
                  f" after {data.get('turns', '?')} turn(s)")
            return
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
    if args.guardrail_mode:
        cfg.guardrail, cfg.guardrail_mode = True, args.guardrail_mode
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
          f"guardrail {cfg.guardrail_mode if cfg.guardrail else 'off'}")

    try:
        baseline, guarded = run_attack_run(cfg, on_event=_attack_printer(color))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = build_session_report(baseline, guarded, run_meta(cfg))
    json_path, md_path = write_session_report(report, cfg.output_dir)

    met_b = sum(s.success for s in baseline)
    summary = f"baseline: {met_b}/{len(baseline)} met"
    if guarded is not None:
        summary += f", guarded: {sum(s.success for s in guarded)}/{len(guarded)} met"
    print(paint(summary, "crimson" if met_b else "teal", color))
    results_json, results_csv = write_results(report, cfg.output_dir)
    asr_csv = write_asr(report["asr"], cfg.output_dir)
    print(f"wrote {json_path}, {md_path}, {results_json}, {results_csv} and {asr_csv}")
    return 0


def _wrote(*paths: Path | None) -> str:
    names = [str(p) for p in paths if p is not None]
    return "wrote " + ", ".join(names[:-1]) + " and " + names[-1]


def _cmd_sweep(args: argparse.Namespace) -> int:
    try:
        sweep, base = load_sweep(args.config)
        if args.workers:
            sweep.workers = args.workers
        if args.out:
            sweep.out = args.out
        cells = expand(sweep, base)
    except (ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    plan = make_plan(sweep, base, cells)
    print(format_plan(plan, cells))
    if args.plan:
        return 0

    def progress(run_id: str, status: str, result: SweepResult, total: int) -> None:
        count = len(result.done) + len(result.failed)
        note = f" ({result.failed[run_id]})" if run_id in result.failed else ""
        print(f"  [{count}/{total}] {run_id}: {status}{note}")

    try:
        result = run_sweep(sweep, base, cells, on_progress=progress)
    except SweepMismatch as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{len(result.done)} run(s) finished, {result.skipped} already done, "
          f"{len(result.failed)} failed")
    if result.cancelled:
        print("stopped early: run the same command again to carry on")
    try:
        records, _ = merge_results(result_files([sweep.out]))
    except ValueError:
        return 130 if result.cancelled else 1       # nothing finished, so nothing to tabulate
    csv_path, md_path = write_aggregate(records, sweep.out)
    matrix_md, matrix_csv = write_matrix(records, sweep.out)
    review = write_review(records, sweep.out)
    print(_wrote(csv_path, md_path, matrix_md, matrix_csv, review))
    return 130 if result.cancelled else (1 if result.failed else 0)


def _cmd_aggregate(args: argparse.Namespace) -> int:
    try:
        records, skipped = merge_results(result_files(args.paths))
    except (ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    for run_id in skipped:
        print(f"skipped {run_id}: already counted from an earlier file", file=sys.stderr)
    out = args.out or (args.paths[0] if len(args.paths) == 1 and args.paths[0].is_dir()
                       else Path("out/aggregate"))
    csv_path, md_path = write_aggregate(records, out)
    matrix_md, matrix_csv = write_matrix(records, out, show_all=args.all)
    review = write_review(records, out)
    print(f"{len(records)} session(s) from {len(result_files(args.paths))} file(s)")
    print(_wrote(csv_path, md_path, matrix_md, matrix_csv, review))
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


def _cmd_app() -> int:
    from .desktop import DesktopError, run_desktop

    try:
        return run_desktop()
    except (DesktopError, ImportError) as exc:
        print(exc, file=sys.stderr)
        return 2


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
    if args.command == "app":
        return _cmd_app()
    if args.command == "sweep":
        return _cmd_sweep(args)
    if args.command == "aggregate":
        return _cmd_aggregate(args)
    return _cmd_list(args)


if __name__ == "__main__":
    raise SystemExit(main())
