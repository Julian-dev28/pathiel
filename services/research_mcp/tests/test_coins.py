from __future__ import annotations

import pytest

from contracts.strategies.models import DeskSpec
from services.research_mcp import coins


def test_supported_coins_lists_exactly_the_brief():
    assert coins.SUPPORTED_COINS == ["xyz:BRENTOIL", "HYPE", "BTC", "ETH", "xyz:SP500"]


@pytest.mark.parametrize("raw, expected", [
    ("BRENTOIL", "xyz:BRENTOIL"), ("brentoil", "xyz:BRENTOIL"), ("xyz:BRENTOIL", "xyz:BRENTOIL"),
    ("brent", "xyz:BRENTOIL"), ("oil", "xyz:BRENTOIL"), (" OIL ", "xyz:BRENTOIL"),
    ("HYPE", "HYPE"), ("hype", "HYPE"), ("hyperliquid", "HYPE"),
    ("BTC", "BTC"), ("btc", "BTC"), ("bitcoin", "BTC"),
    ("ETH", "ETH"), ("eth", "ETH"), ("ethereum", "ETH"),
    ("SP500", "xyz:SP500"), ("sp500", "xyz:SP500"), ("xyz:SP500", "xyz:SP500"),
    ("S&P500", "xyz:SP500"), ("S&P 500", "xyz:SP500"), ("spx", "xyz:SP500"),
])
def test_canonical_coin_accepts_every_documented_alias(raw, expected):
    assert coins.canonical_coin(raw) == expected


@pytest.mark.parametrize("raw", ["", "SOL", "DOGE", "xyz:GOLD", "not-a-coin", None])
def test_canonical_coin_rejects_unsupported_input(raw):
    with pytest.raises(coins.UnsupportedCoinError) as exc_info:
        coins.canonical_coin(raw)
    msg = str(exc_info.value)
    assert "xyz:BRENTOIL" in msg and "HYPE" in msg  # the supported list is in the error


def test_desk_id_for_maps_each_coin():
    assert coins.desk_id_for("xyz:BRENTOIL") == "oil"
    assert coins.desk_id_for("HYPE") == "hype"
    assert coins.desk_id_for("BTC") == "btc"
    assert coins.desk_id_for("ETH") == "top5"
    assert coins.desk_id_for("xyz:SP500") == "sp500"


def test_desk_spec_for_returns_a_valid_spec_naming_the_coin():
    for raw in coins.SUPPORTED_COINS:
        spec = coins.desk_spec_for(raw)
        assert isinstance(spec, DeskSpec)
        desk_coins = {i.coin for i in spec.instruments}
        canon = coins.canonical_coin(raw)
        assert canon in desk_coins, f"{canon} missing from its own desk's instruments: {desk_coins}"


def test_desk_spec_for_is_a_fresh_pure_build_every_call():
    """Two calls return equal but distinct objects -- no shared mutable
    state a caller could accidentally corrupt for the next one."""
    a = coins.desk_spec_for("BTC")
    b = coins.desk_spec_for("BTC")
    assert a == b
    assert a is not b
