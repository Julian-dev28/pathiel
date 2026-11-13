"""services/pool/fetch.py: the venue reads (paged, deduped, fail-closed on a
stuck page) and the offline bundle round-trip. Offline: every POST is a
fake `post` callable, every candle fetch an injected fetcher."""
from __future__ import annotations

import pytest

import contracts.pool as pool
from pathiel.models.types import Candle
from services.pool import fetch as F
from services.pool import venue
from services.pool.tests import pool_fakes as PF

ADDR = "0x" + "11" * 20


def test_fetch_fills_requests_the_contracts_body():
    seen = []

    def post(payload):
        seen.append(payload)
        return []

    F.fetch_fills(ADDR, 1000, 2000, post=post, sleep=lambda s: None)
    assert seen == [pool.fills_request(ADDR, 1000, 2000)]


def test_fetch_orders_requests_the_contracts_body():
    seen = []

    def post(payload):
        seen.append(payload)
        return []

    F.fetch_orders(ADDR, post=post)
    assert seen == [pool.orders_request(ADDR)]


def _page(n, t0, coin="xyz:BRENTOIL", tid0=0):
    return [PF.fill(coin, "B", 1.0, 70.0 + i, t0 + i, 0.0, tid=tid0 + i) for i in range(n)]


def test_paging_across_three_pages_with_a_duplicate_row_at_each_boundary():
    page1 = _page(pool.FILLS_PAGE, t0=0, tid0=0)
    # page2 starts from page1's last row's time (inclusive): the first row repeats (same tid)
    page2 = [page1[-1]] + _page(pool.FILLS_PAGE - 1, t0=page1[-1]["time"], tid0=pool.FILLS_PAGE)
    page3 = [page2[-1]] + _page(5, t0=page2[-1]["time"], tid0=2 * pool.FILLS_PAGE)
    pages = [page1, page2, page3]
    calls = []
    slept = []

    def post(payload):
        calls.append(payload)
        return pages[len(calls) - 1]

    out = F.fetch_fills(ADDR, 0, 10 ** 12, post=post, sleep=slept.append)
    assert len(calls) == 3
    assert calls[0]["startTime"] == 0
    assert calls[1]["startTime"] == page1[-1]["time"]
    assert calls[2]["startTime"] == page2[-1]["time"]
    assert slept == [0.25, 0.25]
    want_tids = {row["tid"] for row in page1} | {row["tid"] for row in page2} | {row["tid"] for row in page3}
    assert {row["tid"] for row in out} == want_tids
    assert len(out) == len(want_tids)
    assert out == sorted(out, key=lambda r: r["time"])


def test_the_no_advance_guard():
    stuck = _page(pool.FILLS_PAGE, t0=5000, tid0=0)

    def post(payload):
        return stuck   # a second, identical full page: the venue never advances

    with pytest.raises(venue.VenueError, match="did not advance"):
        F.fetch_fills(ADDR, 0, 10 ** 12, post=post, sleep=lambda s: None)


def test_other_coins_are_dropped():
    rows = [PF.fill("xyz:BRENTOIL", "B", 1.0, 70.0, 1000, 0.0, tid=1),
            PF.fill("BTC", "B", 1.0, 70000.0, 1001, 0.0, tid=2)]
    out = F.fetch_fills(ADDR, 0, 10 ** 12, post=lambda p: rows, sleep=lambda s: None)
    assert [r["coin"] for r in out] == ["xyz:BRENTOIL"]


def test_fetch_orders_keeps_only_pool_coin_orders():
    rows = [PF.order("xyz:SILVER", "A", 24.0, 1.0, 1000),
            PF.order("ETH", "A", 3000.0, 1.0, 1000)]
    out = F.fetch_orders(ADDR, post=lambda p: rows)
    assert len(out) == 1 and out[0]["order"]["coin"] == "xyz:SILVER"


def test_venue_error_propagates_from_fetch_fills_and_fetch_orders():
    def boom(payload):
        raise venue.VenueError("down")

    with pytest.raises(venue.VenueError):
        F.fetch_fills(ADDR, 0, 1000, post=boom, sleep=lambda s: None)
    with pytest.raises(venue.VenueError):
        F.fetch_orders(ADDR, post=boom)


def test_member_inputs_uses_the_members_own_count_from(monkeypatch):
    seen = {}

    def post(payload):
        seen.setdefault("calls", []).append(payload)
        return []

    member = {"address": ADDR, "kind": "member", "signer_role": "master",
              "count_from": "2026-10-20T00:00:00Z", "count_from_ms": 1_000}
    out = F.member_inputs(member, 5_000, post=post)
    assert out == {"fills": [], "orders": []}
    assert seen["calls"][0] == pool.fills_request(ADDR, 1_000, 5_000)
    assert seen["calls"][1] == pool.orders_request(ADDR)


def test_load_candles_uses_the_injected_fetcher_for_every_pool_coin(tmp_path):
    requested = []

    def fetch(coin, interval, count):
        requested.append((coin, interval, count))
        return [Candle(t=0, o=1.0, h=1.0, l=1.0, c=1.0, v=0.0)]

    bars5, bars1h = F.load_candles(str(tmp_path), fetch=fetch)
    assert set(bars5) == set(pool.POOL_COINS) and set(bars1h) == set(pool.POOL_COINS)
    assert all((c, "5m", F.BARS_5M) in requested for c in pool.POOL_COINS)
    assert all((c, "1h", F.BARS_1H) in requested for c in pool.POOL_COINS)


def test_save_bundle_then_load_bundle_round_trips(tmp_path):
    coin = "xyz:BRENTOIL"
    bundle = {
        "as_of_ms": 1_700_000_000_000,
        "inputs": {ADDR: {"fills": [PF.fill(coin, "B", 1.0, 70.0, 1000, 0.0, tid=1)],
                          "orders": [PF.order(coin, "A", 69.0, 1.0, 1001)]}},
        "bars5": {coin: PF.candles(coin, 0, 300_000, 3)},
        "bars1h": {coin: PF.candles(coin, 0, 3_600_000, 2)},
    }
    out = tmp_path / "bundle"
    F.save_bundle(str(out), bundle)
    back = F.load_bundle(str(out))
    assert back["as_of_ms"] == bundle["as_of_ms"]
    assert back["inputs"][ADDR]["fills"] == bundle["inputs"][ADDR]["fills"]
    assert back["inputs"][ADDR]["orders"] == bundle["inputs"][ADDR]["orders"]
    assert [b.model_dump() for b in back["bars5"][coin]] == [b.model_dump() for b in bundle["bars5"][coin]]
    assert [b.model_dump() for b in back["bars1h"][coin]] == [b.model_dump() for b in bundle["bars1h"][coin]]


def test_an_empty_bundle_round_trips_to_no_members_and_no_coins(tmp_path):
    out = tmp_path / "empty"
    F.save_bundle(str(out), {"as_of_ms": 1_700_000_000_000, "inputs": {}, "bars5": {}, "bars1h": {}})
    back = F.load_bundle(str(out))
    assert back == {"as_of_ms": 1_700_000_000_000, "inputs": {}, "bars5": {}, "bars1h": {}}
