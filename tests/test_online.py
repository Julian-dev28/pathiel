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
    from hermes_agent.client.hl_client import fetch_all_mids
    mids = fetch_all_mids()
    assert isinstance(mids, dict) and mids
    assert "BTC" in mids
    assert float(mids["BTC"]) > 0


def test_fetch_hl_candles_live():
    from hermes_agent.client.hl_client import fetch_hl_candles
    candles = fetch_hl_candles("BTC", "1h", 20)
    assert len(candles) > 0
    for c in candles:
        assert c.h >= c.l
        assert c.h >= c.o and c.h >= c.c
        assert c.l <= c.o and c.l <= c.c
        assert c.v >= 0


def test_get_universe_live():
    from hermes_agent.client.universe import get_universe
    uni = get_universe()
    assert len(uni) > 50
    btc = next((m for m in uni if m["coin"] == "BTC"), None)
    assert btc is not None and btc["dayNtlVlm"] > 0
    vols = [m["dayNtlVlm"] for m in uni]
    assert vols == sorted(vols, reverse=True)  # sorted by 24h volume desc


def test_get_hl_atr_live():
    from hermes_agent.client.exchange import get_hl_atr
    assert get_hl_atr("4h", 14, "BTC") > 0


def test_funding_rate_live():
    """Verifies the funding-rate bug fix (_make_info -> fetch_funding_history)."""
    from hermes_agent.client.hl_client import fetch_funding_history
    from hermes_agent.agents.research import _fetch_funding_rate
    hist = fetch_funding_history("BTC", int(time.time() * 1000) - 86_400_000)
    assert isinstance(hist, list) and hist
    assert "fundingRate" in hist[-1]
    rate = _fetch_funding_rate("BTC")
    assert rate != "N/A"          # was permanently "N/A" before the fix
    assert rate.endswith("%/hr")


def test_market_get_funding_regime_live():
    from hermes_agent.agents.hyperfeed import market_get_funding_regime
    out = market_get_funding_regime()
    assert out["regime"] in ("LONG_CROWDED", "SHORT_CROWDED", "NEUTRAL")
    assert out["assets"]


def test_research_pipeline_live_without_llm(monkeypatch):
    """Full research data pipeline against the live API, minus the paid LLM call.

    With no OPENROUTER_API_KEY, _call_ai returns "" and the verdict defaults to
    PASS — so this exercises candle/funding/indicator fetching without spending.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from hermes_agent.agents.research import research
    perception = {"coin": "BTC", "type": "perp", "mid": 0,
                  "composite_score": 0, "triggers": []}
    analysis = research("BTC", perception)
    assert analysis["coin"] == "BTC"
    assert analysis["verdict"] == "PASS"   # no LLM key -> safe default
    assert analysis["id"] and analysis["created_at"]


def test_scan_once_live(monkeypatch):
    """A live market scan over a small universe; any results must be well-formed."""
    monkeypatch.setenv("HERMES_MAX_MARKETS", "10")
    from hermes_agent.agents.perception import scan_once
    from hermes_agent.client.universe import get_universe
    perceptions = scan_once(universe=get_universe(), min_score=0)
    assert isinstance(perceptions, list)
    for p in perceptions:
        for key in ("id", "coin", "type", "fired_at", "mid", "triggers", "composite_score"):
            assert key in p and p[key] not in (None, ""), f"perception field missing/empty: {key}"
        assert p["mid"] > 0
        assert 0 <= p["composite_score"] <= 100
        assert isinstance(p["triggers"], list) and p["triggers"]
