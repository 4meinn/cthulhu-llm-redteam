"""Shared orchestration for multi-turn attack runs.

The CLI `attack` command, the sweep runner and the web backend all drive a run
through here, so there is one place that turns a config into campaigns.

A phase runs in one of two modes. campaign is one conversation per objective: the
context builds up and the session stops at its first success. isolated gives every
corpus attack its own fresh one-turn session, so each vector is tried the same
number of times with no context from the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .attackers import (
    AdaptiveAttacker, Attacker, CorpusAttacker, DEFAULT_OBJECTIVES, Objective, seed_picker,
)
from .clients import LLMClient, build_client
from .config import RunConfig
from .conversation import Event, Session, run_campaign, run_session
from .corpus import load_corpus
from .guardrail import Guardrail

# (phase, kind, data): phase is "baseline" or "guarded".
PhaseEvent = Callable[[str, str, dict], None]

MODES = ("campaign", "isolated")


def build_attacker(cfg: RunConfig, canary: str) -> Attacker:
    if cfg.attacker.strategy == "adaptive":
        if cfg.attacker.endpoint is None:
            raise ValueError("adaptive strategy needs an attacker.endpoint in the config")
        client = build_client(cfg.attacker.endpoint, canary, role="attacker")
        seeds = seed_picker(load_corpus([], cfg.corpus_dir)) if cfg.attacker.seed_from_corpus else None
        return AdaptiveAttacker(client, max_history=cfg.attacker.max_history_turns, seeds=seeds,
                                strike_mode=cfg.attacker.strike_mode)
    return CorpusAttacker(load_corpus(cfg.categories, cfg.corpus_dir))


def resolve_objectives(ids: list[str]) -> list[Objective]:
    return [DEFAULT_OBJECTIVES.get(oid, Objective(oid, oid)) for oid in ids]


@dataclass
class _Context:
    """What every phase of one run shares: the target, judge, canary and scoring settings."""

    canary: str
    target: LLMClient
    objectives: list[Objective]
    kw: dict


def _context(cfg: RunConfig, should_stop: Callable[[], bool] | None) -> _Context:
    if cfg.attacker is None:
        raise ValueError("config has no 'attacker' section")
    rules, canary = cfg.resolve_target()
    judge_client = build_client(cfg.judge, canary, role="judge") if cfg.judge else None
    kw = dict(
        rules=rules, canary=canary, max_turns=cfg.attacker.max_turns,
        judge_client=judge_client, judge_mode=cfg.attacker.judge_mode, scoring=cfg.scoring,
        max_history_turns=cfg.attacker.max_history_turns,
        max_message_chars=cfg.attacker.max_message_chars,
        should_stop=should_stop,
    )
    return _Context(canary, build_client(cfg.target, canary),
                    resolve_objectives(cfg.attacker.objectives), kw)


def _run_isolated(cfg: RunConfig, ctx: _Context, guardrail: Guardrail | None,
                  on_event: Event | None) -> list[Session]:
    if cfg.attacker.strategy != "corpus":
        raise ValueError("isolated mode needs the corpus strategy, an adaptive attacker has no vectors")
    attacks = load_corpus(cfg.categories, cfg.corpus_dir)
    kw = {**ctx.kw, "max_turns": 1}
    should_stop = kw["should_stop"]
    sessions: list[Session] = []
    for objective in ctx.objectives:
        for attack in attacks:
            if should_stop is not None and should_stop():
                return sessions
            sessions.append(run_session(CorpusAttacker([attack]), ctx.target, objective,
                                        guardrail=guardrail, on_event=on_event, **kw))
    return sessions


def _run_phase(cfg: RunConfig, ctx: _Context, *, guardrail: bool, mode: str,
               on_event: Event | None) -> list[Session]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    guard = Guardrail() if guardrail else None
    if mode == "isolated":
        return _run_isolated(cfg, ctx, guard, on_event)
    return run_campaign(build_attacker(cfg, ctx.canary), ctx.target, ctx.objectives,
                        guardrail=guard, on_event=on_event, **ctx.kw)


def run_phase(
    cfg: RunConfig,
    *,
    guardrail: bool,
    mode: str = "campaign",
    on_event: Event | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Session]:
    """Run one phase on its own: with or without the guardrail, in the given mode."""
    return _run_phase(cfg, _context(cfg, should_stop), guardrail=guardrail, mode=mode,
                      on_event=on_event)


def run_attack_run(
    cfg: RunConfig,
    on_event: PhaseEvent | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[Session], list[Session] | None]:
    """Run the baseline campaign and, when the guardrail is on, the guarded one."""
    ctx = _context(cfg, should_stop)

    def phase_cb(phase: str):
        if on_event is None:
            return None
        return lambda kind, data: on_event(phase, kind, data)

    baseline = _run_phase(cfg, ctx, guardrail=False, mode="campaign", on_event=phase_cb("baseline"))
    guarded = None
    if cfg.guardrail:
        guarded = _run_phase(cfg, ctx, guardrail=True, mode="campaign", on_event=phase_cb("guarded"))
    return baseline, guarded
