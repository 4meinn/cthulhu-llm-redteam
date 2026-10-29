"""Sends attack prompts to the target and collects replies.

When a guardrail is supplied it runs before the target: a blocked prompt is
never sent, a sanitized prompt is sent in place of the original, and the system
prompt is hardened for every call.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .clients import LLMClient, LLMError, Message
from .corpus import Attack
from .guardrail import Guardrail

log = logging.getLogger(__name__)


@dataclass
class AttackResult:
    attack: Attack
    iteration: int
    prompt: str                       # what was actually sent (post-sanitize)
    response: str
    error: str | None = None
    blocked: bool = False             # guardrail refused to send it
    guardrail: list[str] = field(default_factory=list)  # rule names that matched


def _call(client: LLMClient, messages: list[Message], retries: int, backoff: float) -> tuple[str, str | None]:
    for attempt in range(retries + 1):
        try:
            return client.chat(messages), None
        except LLMError as exc:
            log.warning("attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
            else:
                return "", str(exc)
    return "", "exhausted retries"


def run_attacks(
    client: LLMClient,
    attacks: list[Attack],
    system_prompt: str,
    iterations: int = 1,
    guardrail: Guardrail | None = None,
    retries: int = 2,
    backoff: float = 1.0,
) -> list[AttackResult]:
    """Run every attack `iterations` times, optionally behind a guardrail."""
    system = guardrail.harden(system_prompt) if guardrail else system_prompt
    results: list[AttackResult] = []
    for attack in attacks:
        original = attack.render()
        decision = guardrail.inspect(original) if guardrail else None

        if decision and decision.blocked:
            for i in range(iterations):
                results.append(AttackResult(
                    attack, i, "", "", blocked=True, guardrail=decision.matched))
            continue

        sent = decision.prompt if decision else original
        matched = decision.matched if decision else []
        messages: list[Message] = [
            {"role": "system", "content": system},
            {"role": "user", "content": sent},
        ]
        for i in range(iterations):
            response, error = _call(client, messages, retries, backoff)
            results.append(AttackResult(attack, i, sent, response, error, guardrail=matched))
    return results
