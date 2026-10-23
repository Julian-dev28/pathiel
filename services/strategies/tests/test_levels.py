"""services/strategies/levels.py: the pure candle math is covered end to end
against the real shapes in tests/test_oil_levels.py (which imports this
module directly). This file covers the parts specific to this module: the
live-fetch leg (fetch_candles/fetch_mid/summarize_coin) and its failure
modes, which scripts/oil_levels.py's CLI does not exercise (it has its own
curl-based fetch)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.strategies import levels as L

ROOT = Path(__file__).resolve().parents[3]


def test_fetch_candles_returns_rows_with_the_close_timestamp(monkeypatch):
    import pathiel.client.hl_client as hlc

    def fake_post(path, body):
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == "xyz:BRENTOIL"
        return [{"t": 0, "T": L.HOUR_MS - 1, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1}]

    monkeypatch.setattr(hlc, "_http_post", fake_post)
    rows = L.fetch_candles("xyz:BRENTOIL", "1h", 50, now_ms=L.HOUR_MS)
    assert rows[0]["T"] == L.HOUR_MS - 1


def test_fetch_candles_degrades_to_empty_on_a_bad_response(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "_http_post", lambda path, body: None)
    assert L.fetch_candles("xyz:BRENTOIL", "1h", 50, now_ms=0) == []


def test_fetch_mid_reads_the_hip3_coin(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "fetch_all_mids", lambda include_hip3=False: {"xyz:BRENTOIL": "107.5"})
    assert L.fetch_mid("xyz:BRENTOIL") == pytest.approx(107.5)


def test_fetch_mid_is_none_when_the_coin_is_missing(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "fetch_all_mids", lambda include_hip3=False: {})
    assert L.fetch_mid("xyz:BRENTOIL") is None


def test_summarize_coin_raises_a_clear_error_with_no_mid(monkeypatch):
    monkeypatch.setattr(L, "fetch_mid", lambda coin: None)
    with pytest.raises(RuntimeError, match="xyz:BRENTOIL"):
        L.summarize_coin("xyz:BRENTOIL")


def test_summarize_coin_stamps_the_coin_on_the_result(monkeypatch):
    monkeypatch.setattr(L, "fetch_mid", lambda coin: 107.5)
    monkeypatch.setattr(L, "fetch_candles", lambda coin, interval, hours, now_ms=None: [])
    out = L.summarize_coin("xyz:BRENTOIL", now_ms=1_000_000)
    assert out["coin"] == "xyz:BRENTOIL" and out["mid"] == 107.5


# ── LEVELS_TTL_S vs the Desk page's own poll interval (2026-10-24) ──────────
#
# oil_desk.js polls GET /api/desks/{id}/levels every REFRESH_MS. At the old
# 20s TTL, every single 60s poll was a cache MISS: the Levels card paid two
# full candleSnapshot reads (weight 20 each, ~2s wall clock measured live)
# on every refresh, forever, even after the venue-budget fix (af82adf)
# stopped those reads from being refused. Caching across the page's own
# poll cycle is free (1h/15m candles do not move inside a minute) and this
# pair of tests pins it two ways: the two constants cannot drift apart
# again (this test), and the cache actually behaves that way (the next
# one).

def _oil_desk_refresh_ms() -> float:
    text = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    m = re.search(r"REFRESH_MS\s*=\s*([\d_]+)", text)
    assert m, "oil_desk.js's REFRESH_MS constant not found"
    return int(m.group(1).replace("_", ""))


def test_levels_ttl_covers_one_full_desk_poll_interval():
    refresh_s = _oil_desk_refresh_ms() / 1000.0
    assert L.LEVELS_TTL_S >= refresh_s, (
        f"LEVELS_TTL_S ({L.LEVELS_TTL_S}s) is shorter than oil_desk.js's own poll "
        f"interval ({refresh_s}s) — every poll would be a cache miss again")


def test_summarize_coin_hits_the_cache_within_ttl_and_refetches_after(monkeypatch):
    calls = []
    monkeypatch.setattr(L, "fetch_mid", lambda coin: calls.append(coin) or 107.5)
    monkeypatch.setattr(L, "fetch_candles", lambda coin, interval, hours, now_ms=None: [])
    L._LEVELS_CACHE.clear()
    clock = [1_000.0]
    monkeypatch.setattr(L, "_mono", lambda: clock[0])

    L.summarize_coin("xyz:BRENTOIL")
    assert len(calls) == 1, "first call always fetches"

    clock[0] += L.LEVELS_TTL_S - 1   # still inside the window
    L.summarize_coin("xyz:BRENTOIL")
    assert len(calls) == 1, "a poll inside the TTL must be served from cache"

    clock[0] += 2   # now past LEVELS_TTL_S since the first fetch
    L.summarize_coin("xyz:BRENTOIL")
    assert len(calls) == 2, "a poll past the TTL must refetch"
