"""Online tests — exercise the live Hyperliquid public API.

These hit https://api.hyperliquid.xyz over read-only endpoints: no credentials,
no money, no writes. They are deselected by default; run explicitly with:

    pytest -m online

Deliberately NOT covered (and never auto-tested):
  - order placement / leverage / cancels — those move real funds on mainnet;
  - the OpenRouter `research()` LLM call — billable. The research test below
    runs the full data pipeline with the LLM key removed, so it never spends.
"""
import time

import pytest

pytestmark = pytest.mark.online


def test_fetch_all_mids_live():
    from pathiel.client.hl_client import fetch_all_mids
    mids = fetch_all_mids()
    assert isinstance(mids, dict) and mids
    assert "BTC" in mids
    assert float(mids["BTC"]) > 0


def test_fetch_hl_candles_live():
    from pathiel.client.hl_client import fetch_hl_candles
    candles = fetch_hl_candles("BTC", "1h", 20)
    assert len(candles) > 0
    for c in candles:
        assert c.h >= c.l
        assert c.h >= c.o and c.h >= c.c
        assert c.l <= c.o and c.l <= c.c
        assert c.v >= 0


def test_get_universe_live():
    from pathiel.client.universe import get_universe
    uni = get_universe()
    assert len(uni) > 50
    btc = next((m for m in uni if m["coin"] == "BTC"), None)
    assert btc is not None and btc["dayNtlVlm"] > 0
    vols = [m["dayNtlVlm"] for m in uni]
    assert vols == sorted(vols, reverse=True)  # sorted by 24h volume desc


def test_get_max_leverage_live():
    """Per-coin max leverage — used to cap order leverage so it isn't rejected."""
    from pathiel.client.exchange import get_max_leverage
    btc = get_max_leverage("BTC")
    assert isinstance(btc, int) and 1 <= btc <= 100
    # different coins genuinely have different maxes
    eth = get_max_leverage("ETH")
    assert isinstance(eth, int) and 1 <= eth <= 100


def test_ioc_cross_price_crosses_book():
    """The IOC limit price must cross the live book — a buy >= best ask,
    a sell <= best bid — or the order matches nothing ('could not
    immediately match')."""
    from pathiel.client.exchange import _ioc_cross_price, _get_info, get_hl_price
    info = _get_info()
    for coin in ("BTC", "ETH"):
        mid = get_hl_price(coin)
        levels = info.l2_snapshot(coin)["levels"]
        best_bid, best_ask = float(levels[0][0]["px"]), float(levels[1][0]["px"])
        assert _ioc_cross_price(coin, True, mid) >= best_ask    # buy crosses ask
        assert _ioc_cross_price(coin, False, mid) <= best_bid   # sell crosses bid


def test_get_hl_atr_live():
    from pathiel.client.exchange import get_hl_atr
    assert get_hl_atr("4h", 14, "BTC") > 0


def test_funding_rate_live():
    """Verifies the funding-rate bug fix (_make_info -> fetch_funding_history).
    The `_fetch_funding_rate` half of this test went with pathiel.agents.research
    (deleted 2026-10-23); the venue read it wrapped is what is left to check."""
    from pathiel.client.hl_client import fetch_funding_history
    hist = fetch_funding_history("BTC", int(time.time() * 1000) - 86_400_000)
    assert isinstance(hist, list) and hist
    assert "fundingRate" in hist[-1]
    assert float(hist[-1]["fundingRate"]) == float(hist[-1]["fundingRate"])  # parses


def test_market_get_funding_regime_live():
    from pathiel.agents.hyperfeed import market_get_funding_regime
    out = market_get_funding_regime()
    assert out["regime"] in ("LONG_CROWDED", "SHORT_CROWDED", "NEUTRAL")
    assert out["assets"]


def test_account_state_has_available():
    """fetch_account_state exposes `available` USDC — the base for trade sizing."""
    from pathiel.client.hl_client import fetch_account_state, resolve_user_address
    user = resolve_user_address()
    if not user:
        import pytest as _pt
        _pt.skip("no Hyperliquid address configured")
    state = fetch_account_state(user)
    assert "available" in state and "equity" in state
    assert isinstance(state["available"], float)
    assert state["available"] >= 0
    assert state["available"] <= state["equity"] + 1e-6  # free <= total
