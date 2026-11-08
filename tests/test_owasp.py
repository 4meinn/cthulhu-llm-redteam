from cthulhu.report import OBJECTIVE_OWASP


def test_objectives_map_to_the_owasp_2025_entries():
    assert OBJECTIVE_OWASP["leak_canary"].startswith("LLM02")
    assert OBJECTIVE_OWASP["leak_system_prompt"].startswith("LLM07")
