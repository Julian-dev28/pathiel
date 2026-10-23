"""Unit tests for the new desk-type heuristics added for intel-hard-data:
is_metals_desk / is_indices_desk gate sources/fred.py and sources/cboe.py
the same way is_oil_desk/is_crypto_desk already gate sources/news.py."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import desk_rules


def _spec(desk_id: str, *coins_labels) -> DeskSpec:
    return DeskSpec(id=desk_id, name=desk_id, tagline="t", instruments=[
        Instrument(coin=c, label=l, role="primary" if i == 0 else "secondary")
        for i, (c, l) in enumerate(coins_labels)
    ])


def test_is_metals_desk_true_for_gold_and_silver():
    spec = _spec("metals", ("xyz:GOLD", "Gold"), ("xyz:SILVER", "Silver"))
    assert desk_rules.is_metals_desk(spec) is True


def test_is_metals_desk_false_for_oil():
    spec = _spec("oil", ("xyz:BRENTOIL", "Brent"))
    assert desk_rules.is_metals_desk(spec) is False


def test_is_indices_desk_true_for_sp500_and_nasdaq():
    sp = _spec("indices", ("xyz:SP500", "S&P 500"))
    ndx = _spec("indices2", ("xyz:NDX", "Nasdaq-100"))
    assert desk_rules.is_indices_desk(sp) is True
    assert desk_rules.is_indices_desk(ndx) is True


def test_is_indices_desk_true_for_xyz100_composite():
    spec = _spec("indices", ("xyz:XYZ100", "XYZ100"))
    assert desk_rules.is_indices_desk(spec) is True


def test_is_indices_desk_false_for_metals_or_ai():
    metals = _spec("metals", ("xyz:GOLD", "Gold"))
    ai = _spec("ai", ("xyz:NVDA", "Nvidia"))
    assert desk_rules.is_indices_desk(metals) is False
    assert desk_rules.is_indices_desk(ai) is False


def test_is_natgas_desk_true_for_the_dedicated_gas_desk():
    """Regression: the "gas" desk (services/strategies/desks/gas.py, added
    on overhaul-2026-10-17 alongside this branch) has no "oil"/"wti"/
    "brent"/"crude" token anywhere in its id or instrument labels, so
    is_oil_desk alone would miss it and sources/eia.py's natural-gas-
    storage number — this desk's own primary edge — would never fire."""
    gas = _spec("gas", ("xyz:NATGAS", "Nat Gas"))
    assert desk_rules.is_natgas_desk(gas) is True


def test_is_natgas_desk_true_for_oil_desk_too():
    oil = _spec("oil", ("xyz:BRENTOIL", "Brent"), ("xyz:NATGAS", "Nat Gas"))
    assert desk_rules.is_natgas_desk(oil) is True


def test_is_natgas_desk_false_for_unrelated_desks():
    metals = _spec("metals", ("xyz:GOLD", "Gold"))
    assert desk_rules.is_natgas_desk(metals) is False
