"""The static corpus as an attacker.

This keeps the MVP behavior available under the new interface: each corpus entry
is sent once as its own single-turn attempt, ignoring the conversation history.
It is the offline-testable baseline and the seed pool the adaptive agent can
draw its opening moves from.
"""

from __future__ import annotations

from ..corpus import Attack
from .base import AttackMove, Attacker, Objective, Turn


class CorpusAttacker(Attacker):
    def __init__(self, attacks: list[Attack]) -> None:
        self._attacks = attacks
        self._i = 0

    def reset(self, objective: Objective) -> None:
        self._i = 0

    def next_move(self, history: list[Turn]) -> AttackMove | None:
        if self._i >= len(self._attacks):
            return None
        attack = self._attacks[self._i]
        self._i += 1
        return AttackMove(prompt=attack.render(), tactic=f"corpus:{attack.category}:{attack.id}")
