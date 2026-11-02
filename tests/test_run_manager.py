import time

import pytest

from cthulhu.clients import LLMClient
from cthulhu.config import AttackerConfig, EndpointConfig, RunConfig
from cthulhu.web.manager import RunManager

CANARY = "NW-test-CANARY"


def _cfg(**over):
    base = dict(
        target=EndpointConfig(provider="mock"),
        judge=EndpointConfig(provider="mock"),
        system_prompt_text="code is {{CANARY}}",
        canary=CANARY,
        guardrail=True,
        attacker=AttackerConfig(strategy="adaptive", endpoint=EndpointConfig(provider="mock"),
                                objectives=["leak_canary"], max_turns=6),
    )
    base.update(over)
    return RunConfig(**base)


def test_run_completes_and_builds_a_report():
    mgr = RunManager()
    rid = mgr.start(_cfg())
    events = list(mgr.iter_events(rid))            # blocks until run_finished
    assert events[-1]["kind"] == "run_finished" and events[-1]["status"] == "done"
    assert any(e["kind"] == "turn" for e in events)
    run = mgr.get(rid)
    assert run.report["baseline"][0]["success"] is True
    assert run.report["guarded"] is not None


def test_missing_attacker_is_rejected():
    with pytest.raises(ValueError):
        RunManager().start(_cfg(attacker=None))


def test_unknown_run_id_is_handled():
    mgr = RunManager()
    assert mgr.get("nope") is None
    assert mgr.cancel("nope") is False


def test_run_can_be_cancelled(monkeypatch):
    import cthulhu.campaign as camp

    class SlowSilent(LLMClient):
        def chat(self, messages):
            time.sleep(0.03)
            return "I'm sorry, I can't help with that."

    real = camp.build_client
    monkeypatch.setattr(camp, "build_client",
                        lambda cfg, canary="CANARY", role="target":
                        SlowSilent() if role == "target" else real(cfg, canary, role))

    mgr = RunManager()
    rid = mgr.start(_cfg(guardrail=False, attacker=AttackerConfig(
        strategy="adaptive", endpoint=EndpointConfig(provider="mock"),
        objectives=["leak_canary"], max_turns=10)))
    mgr.cancel(rid)
    events = list(mgr.iter_events(rid))
    assert events[-1]["status"] == "cancelled"
    assert mgr.get(rid).status == "cancelled"


def test_cancel_all_stops_every_running_run_and_skips_finished_ones(monkeypatch):
    import cthulhu.campaign as camp

    class SlowSilent(LLMClient):
        def chat(self, messages):
            time.sleep(0.03)
            return "I'm sorry, I can't help with that."

    real = camp.build_client
    monkeypatch.setattr(camp, "build_client",
                        lambda cfg, canary="CANARY", role="target":
                        SlowSilent() if role == "target" else real(cfg, canary, role))
    long_run = dict(guardrail=False, attacker=AttackerConfig(
        strategy="adaptive", endpoint=EndpointConfig(provider="mock"),
        objectives=["leak_canary"], max_turns=10))

    mgr = RunManager()
    done = mgr.start(_cfg(guardrail=False, attacker=AttackerConfig(
        strategy="adaptive", endpoint=EndpointConfig(provider="mock"),
        objectives=["leak_canary"], max_turns=1)))
    list(mgr.iter_events(done))
    first, second = mgr.start(_cfg(**long_run)), mgr.start(_cfg(**long_run))
    assert mgr.cancel_all() == 2
    for rid in (first, second):
        assert list(mgr.iter_events(rid))[-1]["status"] == "cancelled"
    assert mgr.get(done).status == "done"
    assert mgr.cancel_all() == 0


def test_runs_are_listed_oldest_first_with_default_labels():
    mgr = RunManager()
    one = mgr.start(_cfg(label="alpha"))
    two = mgr.start(_cfg())
    assert [r.id for r in mgr.list()] == [one, two]
    assert mgr.get(one).label == "alpha" and mgr.get(two).label == "qwen3:8b #2"
    list(mgr.iter_events(one))
    list(mgr.iter_events(two))
