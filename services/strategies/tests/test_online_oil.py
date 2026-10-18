"""Online tests: real Hyperliquid reads, deselected by default per
pyproject's addopts. Run with:

    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest -q -m online services/strategies

Validates the build brief's measurable outcome #1 directly: `brief oil`
builds a full DeskAnalytics from live HL data in under 10 seconds.
"""
from __future__ import annotations

import time

import pytest

from services.strategies import desks, facade
from services.strategies.analytics import build_analytics

pytestmark = pytest.mark.online


def test_build_analytics_for_oil_completes_under_10s_with_real_data():
    spec = desks.require_spec("oil")
    t0 = time.monotonic()
    result = build_analytics(spec)
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"oil analytics build took {elapsed:.1f}s, budget is 10s"
    assert len(result.instruments) >= 2          # at least CL + BRENTOIL came back
    brent = next((i for i in result.instruments if i.coin == "xyz:BRENTOIL"), None)
    cl = next((i for i in result.instruments if i.coin == "xyz:CL"), None)
    assert brent is not None and brent.mid > 0
    assert cl is not None and cl.mid > 0
    assert result.pairs, "Brent-WTI pair should resolve with live data"
    assert result.ladder, "primary instrument should produce ladder rungs"
    # The desk is priced off Brent (2026-10-19): every rung and forward row is Brent's.
    assert {r.coin for r in result.ladder} == {"xyz:BRENTOIL"}
    assert {f.coin for f in result.forward} == {"xyz:BRENTOIL"}
    assert result.account.source in ("venue", "assumed")


def test_get_brief_oil_end_to_end_under_10s_with_real_data():
    t0 = time.monotonic()
    brief = facade.get_brief("oil")
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"get_brief('oil') took {elapsed:.1f}s, budget is 10s"
    assert brief.analytics is not None
    assert brief.spec.id == "oil"


def test_list_cards_real_desks_all_validate():
    """Every registered desk's spec should resolve to a real, validated
    DeskCard shape even cold (no refresh has run) — exercised with the real
    registry, not fakes, as a final sanity sweep."""
    cards = facade.list_cards()
    assert len(cards) == len(desks.list_desk_ids())


def test_a_second_brief_build_inside_the_store_window_makes_zero_candle_requests(monkeypatch):
    """The 2026-10-18 speed fix, measured live: the first build fills
    services/strategies/candle_store; a rebuild inside its max age reads the
    disk copy and sends no candle request at all (it took 22-122s per desk
    when every brief re-downloaded 400 days through the server's budget)."""
    import pathiel.client.hl_client as hl
    from services.strategies import analytics as A

    real = hl.fetch_hl_candles
    calls = []

    def counting(coin, interval="5m", count=100):
        calls.append((coin, interval, count))
        return real(coin, interval, count)

    monkeypatch.setattr(hl, "fetch_hl_candles", counting)
    spec = desks.require_spec("oil")
    build_analytics(spec, account_fetcher=A.default_account_snapshot)
    first = len(calls)
    t0 = time.monotonic()
    result = build_analytics(spec, account_fetcher=A.default_account_snapshot)
    elapsed = time.monotonic() - t0
    assert first >= 1
    assert len(calls) == first, f"second build fetched candles: {calls[first:]}"
    assert elapsed < 3.0, f"store-warm oil build took {elapsed:.1f}s"
    assert result.instruments
