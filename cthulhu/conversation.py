"""Multi-turn conversation engine.

One session drives a single objective. The attacker proposes a move, the
guardrail inspects it, the target answers inside the running conversation, the
reply is scored, and the result is fed back so the attacker can change tactic.
The session ends when the objective is met, the attacker concedes, or the turn
budget runs out.

Three decisions worth stating, because they shape cost and correctness:

- **Turn vs attempt.** The session owns the turn budget (max_turns). The attacker
  no longer caps itself by history length, it only stops when it runs out of
  tactics. So a session is a bounded conversation, not a count of single shots.
- **When the judge runs.** The cheap deterministic check runs every turn and
  drives the loop and the attacker's feedback. The expensive LLM judge runs by
  judge_mode: every turn, only when the deterministic check is suspicious, or
  once at the end. on_suspicion is the default, since a judge call per turn is
  slow on a small local setup. on_suspicion and final rely on the deterministic
  signal to spot a mid-session win, so an objective with no such signal (acting
  out of scope, with no canary to catch) should use per_turn.
- **Context growth.** The target sees the real conversation, so it is kept to a
  sliding window of the most recent turns, and every message is clipped to a
  fixed length. No summarization, which would cost another model call and make
  results non-deterministic. The attacker keeps its full list of tried tactics
  regardless, so its memory does not shrink with the window.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Literal

from .attackers import Attacker, Objective, Turn
from .clients import LLMClient, LLMError, Message
from .guardrail import Guardrail
from .scoring import CombinedVerdict, Verdict, combine, judge, score_text

log = logging.getLogger(__name__)

JudgeMode = Literal["per_turn", "on_suspicion", "final"]
Scoring = Literal["any", "all", "deterministic", "judge"]
Event = Callable[[str, dict], None]


@dataclass
class Session:
    """The record of one objective's conversation."""

    objective: Objective
    turns: list[Turn]
    verdicts: list[CombinedVerdict | None]   # aligned with turns, None if not judged
    success: bool
    success_turn: int | None                 # 1-based index of the turn that won


def _window(convo: list[Message], max_turns: int, max_chars: int) -> list[Message]:
    """Keep the most recent turns and clip each message, so context stays bounded."""
    kept = convo[-2 * max_turns:]
    return [{"role": m["role"], "content": m["content"][:max_chars]} for m in kept]


def _call(client: LLMClient, messages: list[Message], retries: int = 2, backoff: float = 1.0):
    for attempt in range(retries + 1):
        try:
            return client.chat(messages), None
        except LLMError as exc:
            log.warning("target call failed (attempt %d): %s", attempt + 1, exc)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    return "", "target call failed"


def _last_sent_index(turns: list[Turn]) -> int | None:
    for i in range(len(turns) - 1, -1, -1):
        if not turns[i].blocked:
            return i
    return None


def run_session(
    attacker: Attacker,
    target: LLMClient,
    objective: Objective,
    *,
    rules: str,
    canary: str,
    max_turns: int = 6,
    guardrail: Guardrail | None = None,
    judge_client: LLMClient | None = None,
    judge_mode: JudgeMode = "on_suspicion",
    scoring: Scoring = "any",
    max_history_turns: int = 8,
    max_message_chars: int = 2000,
    on_event: Event | None = None,
) -> Session:
    """Run one objective to success, concession, or the turn budget."""
    attacker.reset(objective)
    system = guardrail.harden(rules) if guardrail else rules
    history: list[Turn] = []
    verdicts: list[CombinedVerdict | None] = []
    convo: list[Message] = []
    success, success_turn = False, None

    def emit(kind: str, **data: object) -> None:
        if on_event:
            on_event(kind, {"objective": objective.id, **data})

    emit("session_start", max_turns=max_turns)
    for i in range(1, max_turns + 1):
        move = attacker.next_move(history)
        if move is None or move.done:
            emit("attacker_done", turn=i, conceded=bool(move and move.done))
            break

        decision = guardrail.inspect(move.prompt) if guardrail else None
        if decision is not None and decision.blocked:
            note = "guardrail blocked: " + ", ".join(decision.matched)
            turn = Turn(move=move, sent="", response="", blocked=True, note=note)
            history.append(turn)
            verdicts.append(None)
            emit("turn", turn=i, tactic=move.tactic, blocked=True, note=note)
            continue

        sent = decision.prompt if decision is not None else move.prompt
        messages = (
            [{"role": "system", "content": system}]
            + _window(convo, max_history_turns, max_message_chars)
            + [{"role": "user", "content": sent}]
        )
        response, error = _call(target, messages)

        det = score_text(response, canary)
        # A canary or marker leak only counts toward objectives it can actually prove.
        det_eff = det if objective.deterministic else Verdict(False, [])
        do_judge = (
            judge_client is not None and not error
            and (judge_mode == "per_turn" or (judge_mode == "on_suspicion" and det_eff.violated))
        )
        jv = judge(judge_client, rules, sent, response) if do_judge else None
        verdict = combine(det_eff, jv, scoring)
        note = (jv.reason if jv else "") or error or ("leak detected" if det.violated else "no leak detected")

        turn = Turn(move=move, sent=sent, response=response, leaked=det.violated, note=note)
        history.append(turn)
        verdicts.append(verdict)
        convo.append({"role": "user", "content": sent})
        convo.append({"role": "assistant", "content": response})
        emit("turn", turn=i, tactic=move.tactic, blocked=False, response=response,
             leaked=det.violated, violated=verdict.violated, judged=jv is not None,
             reason=(jv.reason if jv else ""))

        if verdict.violated:
            success, success_turn = True, i
            emit("objective_met", turn=i)
            break

    # In final mode, or to confirm a win the loop never judged, judge once at the end.
    if judge_client is not None and judge_mode == "final":
        idx = (success_turn - 1) if success_turn else _last_sent_index(history)
        if idx is not None:
            turn = history[idx]
            det_eff = score_text(turn.response, canary) if objective.deterministic else Verdict(False, [])
            jv = judge(judge_client, rules, turn.sent, turn.response)
            verdicts[idx] = combine(det_eff, jv, scoring)
            turn.note = jv.reason or turn.note
            if verdicts[idx].violated and not success:
                success, success_turn = True, idx + 1

    emit("session_end", success=success, success_turn=success_turn, turns=len(history))
    return Session(objective, history, verdicts, success, success_turn)


def run_campaign(
    attacker: Attacker,
    target: LLMClient,
    objectives: list[Objective],
    **kwargs: object,
) -> list[Session]:
    """Run one session per objective, reusing the attacker (reset each time)."""
    return [run_session(attacker, target, obj, **kwargs) for obj in objectives]  # type: ignore[arg-type]
