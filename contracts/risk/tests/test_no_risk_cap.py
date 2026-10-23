"""Contract 1.1.0 (2026-10-23): the operator may opt out of the risk caps.

Julien trades full port at 10x by hand and through place_bracket; the stop he
chooses is the risk. risk_per_trade_pct and daily_loss_limit_pct accept 100
(no cap / never pause); above 100 is still refused, as is zero.
"""
import pytest
from pydantic import ValidationError

from contracts.risk.models import CONTRACT_VERSION, RiskProfile


def test_version_bumped():
    assert CONTRACT_VERSION == "1.1.0"


def test_one_hundred_percent_is_allowed():
    p = RiskProfile(risk_per_trade_pct=100, daily_loss_limit_pct=100, max_bumped_risk_pct=100)
    assert (p.risk_per_trade_pct, p.daily_loss_limit_pct, p.max_bumped_risk_pct) == (100, 100, 100)


@pytest.mark.parametrize("field", ["risk_per_trade_pct", "daily_loss_limit_pct", "max_bumped_risk_pct"])
@pytest.mark.parametrize("bad", [0, -1, 100.01, 250])
def test_outside_zero_to_one_hundred_is_refused(field, bad):
    with pytest.raises(ValidationError):
        RiskProfile(**{field: bad})
