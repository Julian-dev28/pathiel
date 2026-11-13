"""services/intel/book_guard.py: the slippage math against a fixture book,
the venue read through the rate-limited client, never an order."""
from __future__ import annotations

import pytest

from services.intel import book_guard as BG

# A Brent book: mid 63.50, 2 bps spread, thin asks.
RAW = {"coin": "xyz:BRENTOIL", "levels": [
    [{"px": "63.49", "sz": "20", "n": 2}, {"px": "63.48", "sz": "30", "n": 1}, {"px": "63.45", "sz": "100", "n": 3}],
    [{"px": "63.51", "sz": "10", "n": 1}, {"px": "63.55", "sz": "10", "n": 1}, {"px": "63.60", "sz": "10", "n": 2}],
]}


def test_parse_l2_reads_the_venue_shape_and_tolerates_junk():
    book = BG.parse_l2(RAW)
    assert book["bids"][0] == (63.49, 20.0) and book["asks"][2] == (63.60, 10.0)
    assert BG.parse_l2(None) == {"bids": [], "asks": []}
    assert BG.parse_l2({"levels": [[{"px": "x"}], "nope"]}) == {"bids": [], "asks": []}


def test_walk_book_and_slippage_against_the_fixture():
    book = BG.parse_l2(RAW)
    mid = 63.50
    # $635.1 buys exactly the first ask level (10 x 63.51): vwap 63.51, filled
    vwap, filled = BG.walk_book(book["asks"], 635.1)
    assert filled and vwap == pytest.approx(63.51, abs=1e-9)
    # $1270.6 eats level one (635.1) and all of level two (635.5): 20 contracts
    vwap, filled = BG.walk_book(book["asks"], 1270.6)
    assert filled and vwap == pytest.approx(1270.6 / 20.0, abs=1e-9)
    # more than the asks hold ($1906.6 total): not filled, vwap over what was there
    vwap, filled = BG.walk_book(book["asks"], 5000.0)
    assert not filled and vwap == pytest.approx(1906.6 / 30.0, abs=1e-9)
    buy = BG.slippage(book["asks"], 1270.6, mid, "buy")
    assert buy["filled"] and buy["bps"] == pytest.approx((1270.6 / 20.0 / mid - 1) * 1e4, abs=0.01)
    sell = BG.slippage(book["bids"], 1269.8, mid, "sell")          # 20 x 63.49 = 1269.8, one level
    assert sell["filled"] and sell["bps"] == pytest.approx((1 - 63.49 / mid) * 1e4, abs=0.01)
    assert BG.slippage([], 100.0, mid, "buy") == {"bps": None, "vwap_px": None, "filled": False}
    assert BG.walk_book([(0.0, 5.0), (-1.0, 5.0)], 10.0) == (None, False)


def test_snapshot_from_book_reports_spread_and_both_sides():
    snap = BG.snapshot_from_book("xyz:BRENTOIL", BG.parse_l2(RAW), 1270.6, at_ms=1)
    assert snap["best_bid"] == 63.49 and snap["best_ask"] == 63.51 and snap["mid"] == 63.5
    assert snap["spread_bps"] == pytest.approx(0.02 / 63.5 * 1e4, abs=0.01)
    assert snap["buy"]["filled"] and snap["sell"]["filled"] and snap["depth_levels"] == {"bids": 3, "asks": 3}
    assert snap["notional_usd"] == 1270.6 and snap["at_ms"] == 1
    empty = BG.snapshot_from_book("xyz:CL", {"bids": [], "asks": []}, 100.0)
    assert empty["mid"] is None and empty["spread_bps"] is None and empty["buy"]["bps"] is None


def test_book_snapshot_goes_through_the_injected_fetch_and_reports_a_refused_read(monkeypatch):
    monkeypatch.setenv(BG.NOTIONAL_ENV, "1270.6")
    calls = []

    def fetch(coin):
        calls.append(coin)
        return BG.parse_l2(RAW) if coin == "xyz:BRENTOIL" else None
    snap = BG.book_snapshot(fetch=fetch)
    assert calls == ["xyz:BRENTOIL", "xyz:CL"]
    assert snap["xyz:BRENTOIL"]["spread_bps"] and snap["xyz:BRENTOIL"]["notional_usd"] == 1270.6
    assert "error" in snap["xyz:CL"] and "budget" in snap["xyz:CL"]["error"]
    line = BG.summary_line(snap)
    assert "xyz:BRENTOIL spread" in line and "xyz:CL book: no book" in line
    monkeypatch.setenv(BG.NOTIONAL_ENV, "junk")
    assert BG.notional_usd() == BG.DEFAULT_NOTIONAL_USD
    monkeypatch.setenv(BG.NOTIONAL_ENV, "-5")
    assert BG.notional_usd() == BG.DEFAULT_NOTIONAL_USD


def test_fetch_l2_book_uses_the_rate_limited_venue_post_and_never_raises(monkeypatch):
    import pathiel.client.hl_client as hl
    seen = []

    def fake_post(path, payload, timeout=5):
        seen.append((path, payload))
        return RAW
    monkeypatch.setattr(hl, "_http_post", fake_post)
    book = BG.fetch_l2_book("xyz:BRENTOIL")
    assert seen == [("/info", {"type": "l2Book", "coin": "xyz:BRENTOIL"})] and book["asks"][0] == (63.51, 10.0)
    monkeypatch.setattr(hl, "_http_post", lambda *a, **k: None)          # budget refused
    assert BG.fetch_l2_book("xyz:BRENTOIL") is None

    def boom(*a, **k):
        raise RuntimeError("venue")
    monkeypatch.setattr(hl, "_http_post", boom)
    assert BG.fetch_l2_book("xyz:BRENTOIL") is None
