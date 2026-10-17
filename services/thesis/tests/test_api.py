

# ── scenario unit coercion (live finding 2026-10-17) ─────────────────────────

def _oil_draft(oil):
    from services.thesis.llm_schema import ThesisDraft
    t = oil.thesis
    return ThesisDraft(call=t.call, scenarios=t.scenarios, bull=t.bull, bear=t.bear,
                       what_changes_my_mind=t.what_changes_my_mind, lessons=t.lessons,
                       leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in t.leverage_view])


def test_percent_scenario_bounds_are_coerced_to_fractions(oil):
    """A live draft wrote -12.37 for -12.37%; every scenario price check then
    failed and forced a paid repair round. Coerced deterministically now."""
    from contracts.strategies.models import Scenario
    from services.thesis import api as thesis_api
    pct = _oil_draft(oil).model_copy(update={"scenarios": [
        Scenario(name="down", probability=0.4, move_lo_pct=-12.0, move_hi_pct=-2.0, description="d"),
        Scenario(name="up", probability=0.6, move_lo_pct=-2.0, move_hi_pct=8.0, description="u"),
    ]})
    fixed = thesis_api._coerce_scenario_units(pct)
    got = [(round(x.move_lo_pct, 6), round(x.move_hi_pct, 6)) for x in fixed.scenarios]
    assert got == [(-0.12, -0.02), (-0.02, 0.08)]


def test_fraction_scenario_bounds_are_left_alone(oil):
    from services.thesis import api as thesis_api
    draft = _oil_draft(oil)
    assert thesis_api._coerce_scenario_units(draft) is draft


def test_parse_draft_applies_the_unit_coercion(oil):
    from contracts.strategies.models import Scenario
    from services.thesis import api as thesis_api
    pct = _oil_draft(oil).model_copy(update={"scenarios": [
        Scenario(name="a", probability=0.3, move_lo_pct=-50.0, move_hi_pct=-2.0, description="d"),
        Scenario(name="b", probability=0.4, move_lo_pct=-2.0, move_hi_pct=2.0, description="c"),
        Scenario(name="c", probability=0.3, move_lo_pct=2.0, move_hi_pct=50.0, description="u"),
    ]})
    draft, err = thesis_api._parse_draft(pct.model_dump_json())
    assert err is None
    assert draft.scenarios[0].move_lo_pct == -0.5


def test_scenario_schema_tells_the_model_bounds_are_fractions():
    import json
    from services.thesis.llm_schema import thesis_draft_json_schema
    text = json.dumps(thesis_draft_json_schema(3))
    assert "FRACTION" in text


def test_thesis_effort_env_override(monkeypatch):
    from services.thesis import api as thesis_api
    monkeypatch.delenv("PATHIEL_THESIS_EFFORT", raising=False)
    assert thesis_api.thesis_effort() == thesis_api.THESIS_EFFORT_DEFAULT
    monkeypatch.setenv("PATHIEL_THESIS_EFFORT", "Medium")
    assert thesis_api.thesis_effort() == "medium"


# ── instrument normalization and number parsing (eval 2026-10-17) ────────────

def test_instrument_spellings_map_to_the_desk_coin(oil):
    from services.thesis import api as thesis_api
    for spelled in ("CL", "cl-perp", "WTI", "xyz:cl", "CL/USD"):
        d = _oil_draft(oil)
        d = d.model_copy(update={"call": d.call.model_copy(update={"instrument": spelled})})
        assert thesis_api._normalize_instrument(d, oil.analytics).call.instrument == "xyz:CL", spelled


def test_unknown_instrument_is_left_for_the_validator(oil):
    from services.thesis import api as thesis_api
    d = _oil_draft(oil)
    d = d.model_copy(update={"call": d.call.model_copy(update={"instrument": "DOGE-PERP"})})
    assert thesis_api._normalize_instrument(d, oil.analytics).call.instrument == "DOGE-PERP"


def test_thousands_separators_parse_as_one_number():
    from services.thesis.prompt import extract_numbers
    assert extract_numbers("net long fell 10,403 contracts") == [10403.0]
    assert extract_numbers("BTC 120,000 then 3.5%") == [120000.0, 3.5]
    assert extract_numbers("rungs 5,10,20") == [5.0, 10.0, 20.0]
    assert extract_numbers("10,4031") == [10.0, 4031.0]
