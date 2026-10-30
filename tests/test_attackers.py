import pytest

from cthulhu.attackers import (
    AdaptiveAttacker,
    AttackMove,
    CorpusAttacker,
    DEFAULT_OBJECTIVES,
    MockAttacker,
    Turn,
)
from cthulhu.clients import LLMClient, LLMError
from cthulhu.corpus import load_corpus
from cthulhu.jsonx import extract_objects, first_object_with, loads_lenient

OBJ = DEFAULT_OBJECTIVES["leak_canary"]


def _turn(move="x", response="no", leaked=False, blocked=False, note=""):
    m = move if isinstance(move, AttackMove) else AttackMove(move)
    return Turn(move=m, sent=m.prompt, response=response, leaked=leaked, blocked=blocked, note=note)


# --- jsonx ---------------------------------------------------------------

def test_extract_objects_ignores_braces_in_strings():
    assert extract_objects('{"a": "has } brace"} tail {"b": 2}') == ['{"a": "has } brace"}', '{"b": 2}']


def test_loads_lenient_trailing_comma_and_single_quotes():
    assert loads_lenient('{"a": 1,}') == {"a": 1}
    assert loads_lenient("{'a': 'b'}") == {"a": "b"}


def test_first_object_with_picks_the_one_that_has_the_key():
    text = 'noise {"score": 3} then {"prompt": "go", "tactic": "t"}'
    assert first_object_with(text, "prompt") == {"prompt": "go", "tactic": "t"}
    assert first_object_with(text, "missing") is None


# --- corpus attacker -----------------------------------------------------

def test_corpus_attacker_walks_then_stops():
    attacks = load_corpus(["direct_injection"])
    atk = CorpusAttacker(attacks)
    atk.reset(OBJ)
    moves = []
    while (m := atk.next_move([])) is not None:
        moves.append(m)
    assert len(moves) == len(attacks)
    assert moves[0].tactic.startswith("corpus:direct_injection:")
    atk.reset(OBJ)
    assert atk.next_move([]) is not None  # reset rewinds


# --- mock attacker -------------------------------------------------------

def test_mock_attacker_follows_script_and_stops_on_leak():
    atk = MockAttacker()
    atk.reset(OBJ)
    first = atk.next_move([])
    assert first.tactic == "benign warm-up"
    # once a turn reports a leak, the attacker concedes
    assert atk.next_move([_turn(leaked=True)]) is None


# --- adaptive attacker ---------------------------------------------------

class ScriptedClient(LLMClient):
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


class BrokenClient(LLMClient):
    def chat(self, messages):
        raise LLMError("attacker model down")


def test_adaptive_parses_messy_json_and_remembers_tactics():
    client = ScriptedClient([
        '<think>let me try</think>\n```json\n{"tactic": "t1", "prompt": "p1",}\n```',
        "{'tactic': 't2', 'prompt': 'p2'}",
    ])
    atk = AdaptiveAttacker(client)
    atk.reset(OBJ)
    first = atk.next_move([])
    assert (first.prompt, first.tactic) == ("p1", "t1")
    second = atk.next_move([_turn(move=first, note="refused")])
    assert second.prompt == "p2"
    # the second request tells the model not to repeat the first tactic
    user_msg = client.seen[1][1]["content"]
    assert "t1" in user_msg and "refused" in user_msg


def test_adaptive_give_up_marks_done():
    atk = AdaptiveAttacker(ScriptedClient(['{"prompt": "", "give_up": true}']))
    atk.reset(OBJ)
    move = atk.next_move([])
    assert move.done is True


def test_adaptive_stops_at_max_attempts():
    atk = AdaptiveAttacker(ScriptedClient(['{"prompt": "never reached"}']), max_attempts=2)
    atk.reset(OBJ)
    assert atk.next_move([_turn(), _turn()]) is None


def test_adaptive_survives_model_failure_and_bad_output():
    broken = AdaptiveAttacker(BrokenClient())
    broken.reset(OBJ)
    assert broken.next_move([]) is None

    noprompt = AdaptiveAttacker(ScriptedClient(['{"tactic": "only a tactic"}']))
    noprompt.reset(OBJ)
    assert noprompt.next_move([]) is None


def test_adaptive_needs_reset_first():
    assert AdaptiveAttacker(ScriptedClient(["{}"])).next_move([]) is None
