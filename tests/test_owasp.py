from cthulhu.report import OBJECTIVE_OWASP


def test_objectives_map_to_the_owasp_2025_entries():
    assert OBJECTIVE_OWASP["leak_canary"].startswith("LLM02")
    assert OBJECTIVE_OWASP["leak_system_prompt"].startswith("LLM07")


def test_out_of_scope_is_excessive_agency_and_every_objective_has_a_label():
    from cthulhu.attackers.base import DEFAULT_OBJECTIVES
    assert OBJECTIVE_OWASP["out_of_scope"].startswith("LLM06")
    assert set(DEFAULT_OBJECTIVES) <= set(OBJECTIVE_OWASP)
