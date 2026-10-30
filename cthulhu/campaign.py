"""Shared orchestration for multi-turn attack runs.

The CLI `attack` command and the web backend both drive a run through here, so
there is one place that turns a config into a baseline and a guarded campaign.
"""

from __future__ import annotations

from typing import Callable

from .attackers import AdaptiveAttacker, Attacker, CorpusAttacker, DEFAULT_OBJECTIVES, Objective
from .clients import build_client
from .config import RunConfig
from .conversation import Session, run_campaign
from .corpus import load_corpus
from .guardrail import Guardrail

# (phase, kind, data): phase is "baseline" or "guarded".
PhaseEvent = Callable[[str, str, dict], None]


def build_attacker(cfg: RunConfig, canary: str) -> Attacker:
    if cfg.attacker.strategy == "adaptive":
        if cfg.attacker.endpoint is None:
            raise ValueError("adaptive strategy needs an attacker.endpoint in the config")
        client = build_client(cfg.attacker.endpoint, canary, role="attacker")
        return AdaptiveAttacker(client, max_history=cfg.attacker.max_history_turns)
    return CorpusAttacker(load_corpus(cfg.categories, cfg.corpus_dir))


def resolve_objectives(ids: list[str]) -> list[Objective]:
    return [DEFAULT_OBJECTIVES.get(oid, Objective(oid, oid)) for oid in ids]


def run_attack_run(
    cfg: RunConfig,
    on_event: PhaseEvent | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[Session], list[Session] | None]:
    """Run the baseline campaign and, when the guardrail is on, the guarded one."""
    if cfg.attacker is None:
        raise ValueError("config has no 'attacker' section")
    rules, canary = cfg.resolve_target()
    target = build_client(cfg.target, canary)
    judge_client = build_client(cfg.judge, canary, role="judge") if cfg.judge else None
    objectives = resolve_objectives(cfg.attacker.objectives)

    kw = dict(
        rules=rules, canary=canary, max_turns=cfg.attacker.max_turns,
        judge_client=judge_client, judge_mode=cfg.attacker.judge_mode, scoring=cfg.scoring,
        max_history_turns=cfg.attacker.max_history_turns,
        max_message_chars=cfg.attacker.max_message_chars,
        should_stop=should_stop,
    )

    def phase_cb(phase: str):
        if on_event is None:
            return None
        return lambda kind, data: on_event(phase, kind, data)

    baseline = run_campaign(build_attacker(cfg, canary), target, objectives,
                            guardrail=None, on_event=phase_cb("baseline"), **kw)
    guarded = None
    if cfg.guardrail:
        guarded = run_campaign(build_attacker(cfg, canary), target, objectives,
                               guardrail=Guardrail(), on_event=phase_cb("guarded"), **kw)
    return baseline, guarded
