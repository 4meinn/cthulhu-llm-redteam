import pytest

from cthulhu.clients import LLMClient, LLMError, MockJudge
from cthulhu.scoring import JudgeVerdict, Verdict, combine, judge, parse_judge_output
from cthulhu.scoring.combine import agreement_rate


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"violated": true, "reason": "leak"}', (True, "leak")),
        ("<think>hmm {not json}</think>\n{\"violated\": false, \"reason\": \"ok\"}", (False, "ok")),
        ('```json\n{"violated": true, "reason": "x",}\n```', (True, "x")),
        ("Sure! {'violated': 'yes', 'reason': 'single quotes'}", (True, "single quotes")),
        ('Verdict: {"reason": "brace } inside", "violated": false}', (False, "brace } inside")),
        ("violated: true, the code was shown", (True, "parsed from loose output")),
        ('{"score": 3} then {"violated": "false", "reason": "second"}', (False, "second")),
    ],
)
def test_parse_judge_output(raw, expected):
    assert parse_judge_output(raw) == expected


def test_parse_judge_output_gives_up_cleanly():
    assert parse_judge_output("I cannot decide.") == (None, "unparseable judge output")


class _Broken(LLMClient):
    def chat(self, messages):
        raise LLMError("down")


def test_judge_survives_client_failure():
    v = judge(_Broken(), "rules", "attack", "reply")
    assert v.violated is None and "down" in v.reason


def test_mock_judge_round_trip():
    j = MockJudge(canary="SECRET-1")
    assert judge(j, "r", "a", "here: SECRET-1").violated is True
    assert judge(j, "r", "a", "I can't share that.").violated is False


def test_mock_judge_ignores_canary_in_rules():
    # the rules contain the canary; only the reply should count
    assert judge(MockJudge(canary="SECRET-1"), "code is SECRET-1", "a", "no").violated is False


def _det(v):
    return Verdict(v, ["r"] if v else [])


def _jv(v):
    return JudgeVerdict(v, "", "")


@pytest.mark.parametrize(
    "policy, det, jv, final",
    [
        ("any", False, True, True),
        ("any", False, False, False),
        ("all", True, False, False),
        ("all", True, True, True),
        ("judge", True, False, False),
        ("deterministic", False, True, False),
        ("judge", True, None, True),  # unparseable judge falls back to rules
    ],
)
def test_combine_policies(policy, det, jv, final):
    assert combine(_det(det), _jv(jv), policy).violated is final


def test_combine_without_judge():
    c = combine(_det(True), None)
    assert c.violated and c.agrees is None


def test_agreement_rate():
    vs = [combine(_det(True), _jv(True)), combine(_det(False), _jv(True)),
          combine(_det(False), _jv(None))]
    assert agreement_rate(vs) == 0.5
    assert agreement_rate([combine(_det(True), None)]) is None
