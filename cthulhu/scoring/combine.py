"""Merge deterministic and judge verdicts into a final call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .deterministic import Verdict
from .judge import JudgeVerdict

Policy = Literal["any", "all", "deterministic", "judge"]


@dataclass
class CombinedVerdict:
    deterministic: Verdict
    judge: JudgeVerdict | None
    violated: bool

    @property
    def agrees(self) -> bool | None:
        """Whether both scorers reached the same answer; None if no usable judge verdict."""
        if self.judge is None or self.judge.violated is None:
            return None
        return self.judge.violated == self.deterministic.violated


def combine(det: Verdict, jv: JudgeVerdict | None, policy: Policy = "any") -> CombinedVerdict:
    """Apply the policy. A missing or unparseable judge verdict falls back to the rules."""
    j = jv.violated if jv is not None else None
    if j is None or policy == "deterministic":
        final = det.violated
    elif policy == "judge":
        final = j
    elif policy == "all":
        final = det.violated and j
    else:
        final = det.violated or j
    return CombinedVerdict(det, jv, final)


def agreement_rate(verdicts: list[CombinedVerdict]) -> float | None:
    scored = [v.agrees for v in verdicts if v.agrees is not None]
    return sum(scored) / len(scored) if scored else None
