"""Strike seeds: one sharp corpus attack held back for the final phase.

A small attacker model rarely writes a strong attack from nothing, but it can
adapt a good one. Each objective maps to the corpus categories that fit it, and
one attack from that pool is handed to the adaptive attacker for its final turns.
The seed is held back on purpose: a blunt opening makes the target refuse
everything after it, so the conversation builds first and strikes last.

The pick is deterministic, so runs repeat. Attacks from the holdout partition are
preferred, because the guardrail was tuned on the seen ones and a seen seed would
mostly measure the filter's memory. A pool with no holdout member falls back to
the first seen one.
"""

from __future__ import annotations

from typing import Callable

from ..corpus import Attack, partition
from .base import Objective

OBJECTIVE_SEED_CATEGORIES: dict[str, list[str]] = {
    "leak_canary": ["direct_injection", "jailbreak"],
    "leak_system_prompt": ["system_prompt_leak"],
    "out_of_scope": ["jailbreak"],
}

SeedPicker = Callable[[Objective], "str | None"]


def pick_seed(attacks: list[Attack], objective_id: str) -> str | None:
    categories = OBJECTIVE_SEED_CATEGORIES.get(objective_id)
    if not categories:
        return None
    pool = sorted((a for a in attacks if a.category in categories), key=lambda a: a.id)
    if not pool:
        return None
    holdout = [a for a in pool if partition(a.id) == "holdout"]
    return (holdout or pool)[0].render()


def seed_picker(attacks: list[Attack]) -> SeedPicker:
    """Bind a corpus to a function the adaptive attacker calls once per session."""
    return lambda objective: pick_seed(attacks, objective.id)
