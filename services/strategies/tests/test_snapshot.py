"""Gate tests for services/strategies/backtest/snapshot.py
(FINAL_STRATEGY_TEST_2026-11-04.md). Synthetic data only, injected fetchers
-- no network."""
from __future__ import annotations

import os

from pathiel.models.types import Candle
from services.strategies.backtest import snapshot as S

FIFTEEN_MS = 900_000
HOUR_MS = S.HOUR_MS
DAY_MS = S.DAY_MS


def _c(t, c=100.0):
    return Candle(t=t, o=c, h=c + 1, l=c - 1, c=c, v=1.0)


# ── merge_candles / merge_funding (pure) ───────────────────────────────────

def test_merge_candles_dedupes_by_timestamp_fresh_wins():
    old = [_c(0, 100.0), _c(FIFTEEN_MS, 101.0)]
    fresh = [_c(FIFTEEN_MS, 999.0), _c(2 * FIFTEEN_MS, 102.0)]
    merged = S.merge_candles(old, fresh)
    assert [c.t for c in merged] == [0, FIFTEEN_MS, 2 * FIFTEEN_MS]
    assert merged[1].c == 999.0   # fresh wins on overlap


def test_merge_candles_sorted_even_if_inputs_are_not():
    old = [_c(2 * FIFTEEN_MS), _c(0)]
    fresh = [_c(FIFTEEN_MS)]
    merged = S.merge_candles(old, fresh)
    assert [c.t for c in merged] == [0, FIFTEEN_MS, 2 * FIFTEEN_MS]


def test_merge_candles_empty_inputs():
    assert S.merge_candles([], []) == []
    assert S.merge_candles([_c(0)], []) == [_c(0)]


def test_merge_funding_dedupes_by_time():
    old = [{"time": 0, "fundingRate": "0.01"}, {"time": HOUR_MS, "fundingRate": "0.02"}]
    fresh = [{"time": HOUR_MS, "fundingRate": "0.03"}, {"time": 2 * HOUR_MS, "fundingRate": "0.04"}]
    merged = S.merge_funding(old, fresh)
    assert [r["time"] for r in merged] == [0, HOUR_MS, 2 * HOUR_MS]
    assert merged[1]["fundingRate"] == "0.03"


# ── save/load roundtrip (pure I/O on a tmp dir) ─────────────────────────────

def test_load_snapshot_missing_dir_returns_empty_not_an_error(tmp_path):
    snap = S.load_snapshot(str(tmp_path / "nope"), "xyz:BRENTOIL")
    assert snap.c15 == snap.c1h == snap.c1d == snap.funding == []
    assert snap.window("15m") is None


def test_save_then_load_roundtrips_candles_and_funding(tmp_path):
    d = str(tmp_path)
    candles = [_c(0, 100.0), _c(FIFTEEN_MS, 101.0)]
    S._save_candles_file(S.candles_path(d, "xyz:BRENTOIL", "15m"), candles)
    S._save_funding_file(S.funding_path(d, "xyz:BRENTOIL"), [{"time": 0, "fundingRate": "0.01"}])
    snap = S.load_snapshot(d, "xyz:BRENTOIL")
    assert [c.t for c in snap.c15] == [0, FIFTEEN_MS]
    assert snap.c15[0].c == 100.0
    assert snap.funding == [{"time": 0, "fundingRate": "0.01"}]


def test_candles_path_is_collision_free_across_coins(tmp_path):
    d = str(tmp_path)
    assert S.candles_path(d, "xyz:CL", "15m") != S.candles_path(d, "xyz:BRENTOIL", "15m")


def test_window_reports_open_to_close(tmp_path):
    snap = S.Snapshot(coin="xyz:BRENTOIL", c15=[_c(0), _c(FIFTEEN_MS), _c(2 * FIFTEEN_MS)])
    start, end = snap.window("15m")
    assert start == 0
    assert end == 2 * FIFTEEN_MS + FIFTEEN_MS   # last bar's OPEN + one interval = its close


# ── fetch_and_save (injected fetchers: no network) ──────────────────────

def _paged_fetcher(rows_by_interval):
    def fetch_page(coin, interval, start_ms, end_ms):
        return [c for c in rows_by_interval.get(interval, []) if start_ms <= c.t < end_ms]
    return fetch_page


def test_fetch_and_save_writes_every_interval_and_funding(tmp_path):
    d = str(tmp_path)
    rows = {"15m": [_c(0), _c(FIFTEEN_MS)], "1h": [_c(0), _c(HOUR_MS)], "1d": [_c(0)]}
    fetch_page = _paged_fetcher(rows)

    def fetch_funding(coin, start_ms, end_ms):
        return [{"time": 0, "fundingRate": "0.01"}]

    snap = S.fetch_and_save(d, "xyz:BRENTOIL", start_ms=0, end_ms=2 * HOUR_MS,
                            fetch_page=fetch_page, fetch_funding=fetch_funding)
    assert len(snap.c15) == 2
    assert len(snap.c1h) == 2
    assert len(snap.c1d) == 1
    assert len(snap.funding) == 1
    assert os.path.exists(S.meta_path(d))
    meta = snap.meta
    assert meta["coin"] == "xyz:BRENTOIL"
    assert len(meta["fetches"]) == 1


def test_fetch_and_save_merge_appends_on_a_second_call(tmp_path):
    """The venue's 5000-bar window rolls forward; a later call for a LATER
    window must not drop the earlier bars already on disk (the whole point
    of merge-append -- extending the archive past the single-request cap)."""
    d = str(tmp_path)
    first_rows = {"15m": [_c(0), _c(FIFTEEN_MS)]}
    S.fetch_and_save(d, "xyz:BRENTOIL", start_ms=0, end_ms=2 * FIFTEEN_MS,
                     fetch_page=_paged_fetcher(first_rows), fetch_funding=lambda *a: [])

    second_rows = {"15m": [_c(10 * FIFTEEN_MS), _c(11 * FIFTEEN_MS)]}
    snap = S.fetch_and_save(d, "xyz:BRENTOIL", start_ms=9 * FIFTEEN_MS, end_ms=12 * FIFTEEN_MS,
                            fetch_page=_paged_fetcher(second_rows), fetch_funding=lambda *a: [])
    assert [c.t for c in snap.c15] == [0, FIFTEEN_MS, 10 * FIFTEEN_MS, 11 * FIFTEEN_MS]
    assert len(snap.meta["fetches"]) == 2


def test_fetch_funding_paged_pages_forward_past_the_500_row_cap():
    """fundingHistory caps at 500 rows/call, oldest-first (confirmed live
    2026-11-03). A single 70-day window needs several pages; this stubs a
    source that enforces the same cap and checks the pager assembles the
    full run with no gaps or duplicates."""
    all_rows = [{"time": i * HOUR_MS, "fundingRate": "0.01"} for i in range(1200)]

    def fetch_funding(coin, start_ms, end_ms):
        page = [r for r in all_rows if start_ms <= r["time"] <= end_ms]
        return page[:500]

    got = S.fetch_funding_paged("xyz:BRENTOIL", 0, 1199 * HOUR_MS, fetch_funding)
    assert [r["time"] for r in got] == [r["time"] for r in all_rows]


def test_fetch_funding_paged_stops_on_empty_page():
    def fetch_funding(coin, start_ms, end_ms):
        return [{"time": 0, "fundingRate": "0.01"}] if start_ms == 0 else []

    got = S.fetch_funding_paged("xyz:BRENTOIL", 0, 10 * HOUR_MS, fetch_funding)
    assert got == [{"time": 0, "fundingRate": "0.01"}]


def test_fetch_and_save_fresh_overwrites_stale_row_at_same_timestamp(tmp_path):
    d = str(tmp_path)
    S.fetch_and_save(d, "xyz:BRENTOIL", start_ms=0, end_ms=FIFTEEN_MS,
                     fetch_page=_paged_fetcher({"15m": [_c(0, 100.0)]}), fetch_funding=lambda *a: [])
    snap = S.fetch_and_save(d, "xyz:BRENTOIL", start_ms=0, end_ms=FIFTEEN_MS,
                            fetch_page=_paged_fetcher({"15m": [_c(0, 105.0)]}), fetch_funding=lambda *a: [])
    assert snap.c15[0].c == 105.0
