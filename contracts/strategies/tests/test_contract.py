"""Gate tests for the strategies contract: fixtures parse, invariants hold."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.strategies.models import (
    CONTRACT_VERSION, Call, DeskAnalytics, DeskSpec, Instrument, IntelPacket, Scenario, Thesis,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.parametrize("name,model", [
    ("oil_spec", DeskSpec), ("oil_analytics", DeskAnalytics),
    ("oil_intel", IntelPacket), ("oil_thesis", Thesis), ("oil_thesis_brent", Thesis),
])
def test_fixture_round_trips(name, model):
    raw = json.loads((FIX / f"{name}.json").read_text())
    obj = model.model_validate(raw)
    assert json.loads(obj.model_dump_json()) == raw


def test_fixtures_are_current():
    """Fixtures are generated; a stale fixture means _build.py and the JSON drifted."""
    raw = json.loads((FIX / "oil_thesis.json").read_text())
    assert raw["contract_version"] == CONTRACT_VERSION


def test_extra_fields_rejected():
    raw = json.loads((FIX / "oil_spec.json").read_text())
    raw["surprise"] = 1
    with pytest.raises(ValidationError):
        DeskSpec.model_validate(raw)


def test_desk_needs_primary():
    with pytest.raises(ValidationError):
        DeskSpec(id="x1", name="X", tagline="t",
                 instruments=[Instrument(coin="BTC", label="BTC", role="hedge")])


def test_desk_id_slug():
    with pytest.raises(ValidationError):
        DeskSpec(id="Oil Desk", name="X", tagline="t",
                 instruments=[Instrument(coin="BTC", label="BTC")])


def test_scenarios_must_sum_to_one():
    raw = json.loads((FIX / "oil_thesis.json").read_text())
    raw["scenarios"][0]["probability"] = 0.9
    with pytest.raises(ValidationError, match="sum to"):
        Thesis.model_validate(raw)


def test_one_liner_capped():
    with pytest.raises(ValidationError):
        Call(side="long", instrument="xyz:CL", one_liner="x" * 161, confidence=0.5,
             invalidation="i")


def test_probability_bounds():
    with pytest.raises(ValidationError):
        Scenario(name="s", probability=1.2, move_lo_pct=0, move_hi_pct=1, description="d")
