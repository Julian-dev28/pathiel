import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.risk.models import DerivedLimits, PreviewIn, RiskProfile

FIX = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.parametrize("name,model", [
    ("profile_starter", RiskProfile), ("limits_15", DerivedLimits), ("limits_5", DerivedLimits),
])
def test_fixture_round_trips(name, model):
    raw = json.loads((FIX / f"{name}.json").read_text())
    assert json.loads(model.model_validate(raw).model_dump_json()) == raw


def test_default_profile_is_auto_with_nothing_forced():
    p = RiskProfile()
    assert p.preset == "auto" and p.risk_per_trade_pct is None


def test_profile_bounds():
    with pytest.raises(ValidationError):
        RiskProfile(risk_per_trade_pct=0)
    with pytest.raises(ValidationError):
        RiskProfile(max_leverage=100)


def test_every_book_row_needs_a_reason():
    raw = json.loads((FIX / "limits_15.json").read_text())
    del raw["books"][0]["reason"]
    with pytest.raises(ValidationError):
        DerivedLimits.model_validate(raw)


def test_preview_rejects_negative_equity():
    with pytest.raises(ValidationError):
        PreviewIn(equity_usd=-1)
