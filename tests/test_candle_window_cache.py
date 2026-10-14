"""Rolling-window candle cache (hl_client.fetch_hl_candles).

The window store keys on (coin, interval) only and serves any count <= stored
length by slicing the tail — collapsing the per-candidate fan-out where
chronos(×50)/rvol(×60)/scan(×100)/coin-tape(×290) each paid a separate flat
weight-20 candleSnapshot for the SAME 5m series. These tests pin:

  * slice-serves-shorter-count with ONE network fetch
  * a longer request UPGRADES the window (refetch, longer stored series)
  * a stale refetch re-fetches max(requested, stored) — never downgrades
  * per-interval TTLs (5m=30s refetchable, 1h=600s fresh)
  * slice reads do NOT extend the TTL
  * fresh=True bypasses the read but write-back is UPGRADE-ONLY (a fresh short
    fetch never shrinks a still-fresh longer window)
  * transient failures are never cached
  * HERMES_CANDLE_CACHE_TTL_S override: 0 disables, N forces uniform TTL
"""

import time as _time_mod

import pytest

from hermes_trader.client import hl_client


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000}


def _raw_bars(n: int, interval: str = "5m", t0: int = 1_700_000_000_000):
    step = INTERVAL_MS.get(interval, 300_000)
    return [
        {"t": t0 + i * step, "o": "1", "h": "2", "l": "0.5", "c": str(i), "v": "10"}
        for i in range(n)
    ]


class _FakePost:
    """Stand-in for hl_client._http_post: counts calls, records payloads."""

    def __init__(self, bars_fn=None):
        self.calls = []
        self._bars_fn = bars_fn or (lambda count: _raw_bars(count))

    def __call__(self, path, payload, timeout=5):
        req = payload.get("req", {})
        count = len(self._bars_n(req))
        self.calls.append({"payload": payload, "count": count})
        return self._bars_fn(count)

    @staticmethod
    def _bars_n(req):
        step = INTERVAL_MS.get(req.get("interval", "5m"), 300_000)
        n = (req["endTime"] - req["startTime"]) // step
        return list(range(n))


@pytest.fixture()
def env(monkeypatch):
    """Isolated window store + fake HTTP + controllable clock."""
    windows = {}
    monkeypatch.setattr(hl_client, "_CANDLE_WINDOWS", windows)
    monkeypatch.setattr(hl_client, "_CANDLE_TTL_OVERRIDE_S", None)
    fake = _FakePost()
    monkeypatch.setattr(hl_client, "_http_post", fake)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(hl_client.time, "time", lambda: clock["now"])
    # end_time in the payload uses time.time()*1000 — keep it monotonic-safe.
    yield {"windows": windows, "post": fake, "clock": clock, "monkeypatch": monkeypatch}


def _fetched_counts(post):
    return [c["count"] for c in post.calls]


def test_slice_serves_shorter_count_with_one_fetch(env):
    """×100 then ×50 on the same coin+interval = ONE network fetch (the fix)."""
    a = hl_client.fetch_hl_candles("BTC", "5m", 100)
    b = hl_client.fetch_hl_candles("BTC", "5m", 50)
    assert _fetched_counts(env["post"]) == [100]
    assert len(a) == 100 and len(b) == 50
    # The slice is the TAIL of the stored window — same bars, newest end.
    assert [c.c for c in b] == [c.c for c in a[-50:]]


def test_longer_request_upgrades_window(env):
    """×50 then ×290: second call refetches at 290 and the window stores 290."""
    hl_client.fetch_hl_candles("BTC", "5m", 50)
    out = hl_client.fetch_hl_candles("BTC", "5m", 290)
    assert _fetched_counts(env["post"]) == [50, 290]
    assert len(out) == 290
    stored_at, bars = env["windows"]["BTC|5m"]
    assert len(bars) == 290
    # A third, SHORTER read now slices the 290-window — no new fetch.
    hl_client.fetch_hl_candles("BTC", "5m", 60)
    assert _fetched_counts(env["post"]) == [50, 290]


def test_stale_refetch_keeps_longer_window_warm(env):
    """After TTL expiry a ×100 request re-fetches the stored 290 (flat weight)."""
    hl_client.fetch_hl_candles("BTC", "5m", 290)
    env["clock"]["now"] += 31.0  # 5m window TTL is 30s
    out = hl_client.fetch_hl_candles("BTC", "5m", 100)
    assert _fetched_counts(env["post"]) == [290, 290]
    assert len(out) == 100  # caller still gets exactly what it asked for
    _, bars = env["windows"]["BTC|5m"]
    assert len(bars) == 290


def test_per_interval_ttl(env):
    """31s stale for 5m, still fresh for 1h (600s)."""
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    hl_client.fetch_hl_candles("BTC", "1h", 100)
    env["clock"]["now"] += 31.0
    hl_client.fetch_hl_candles("BTC", "5m", 100)   # stale -> refetch
    hl_client.fetch_hl_candles("BTC", "1h", 100)   # fresh -> slice
    assert len(env["post"].calls) == 3


def test_slice_read_does_not_extend_ttl(env):
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    env["clock"]["now"] += 29.0
    hl_client.fetch_hl_candles("BTC", "5m", 50)    # slice hit at t=29
    env["clock"]["now"] += 2.0                     # t=31 > 30s from FETCH
    hl_client.fetch_hl_candles("BTC", "5m", 50)    # must refetch, not extend
    assert _fetched_counts(env["post"]) == [100, 100]


def test_fresh_bypasses_read_but_writeback_is_upgrade_only(env):
    """fresh=True sees the live tip; its short write-back must not shrink the
    still-fresh longer window the scan/tape consumers slice from."""
    hl_client.fetch_hl_candles("BTC", "1m", 100)
    out = hl_client.fetch_hl_candles("BTC", "1m", 3, fresh=True)
    assert len(out) == 3
    assert _fetched_counts(env["post"]) == [100, 100]  # refetch keeps 100 warm
    _, bars = env["windows"]["BTC|1m"]
    assert len(bars) == 100
    # And a NON-fresh short read after it still hits the window (no new fetch).
    hl_client.fetch_hl_candles("BTC", "1m", 3)
    assert _fetched_counts(env["post"]) == [100, 100]


def test_transient_failure_is_never_cached(env):
    """Non-list after retries -> [] and NO window entry (next call retries)."""
    boom = {"n": 0}

    def _fail(path, payload, timeout=5):
        boom["n"] += 1
        return None

    env["monkeypatch"].setattr(hl_client, "_http_post", _fail)
    slept = []
    env["monkeypatch"].setattr(hl_client.time, "sleep", lambda s: slept.append(s))
    out = hl_client.fetch_hl_candles("BTC", "5m", 100)
    assert out == []
    assert boom["n"] == 4  # initial + 3 retries
    assert "BTC|5m" not in env["windows"]


def test_env_override_zero_disables_caching(env):
    env["monkeypatch"].setattr(hl_client, "_CANDLE_TTL_OVERRIDE_S", 0.0)
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    assert _fetched_counts(env["post"]) == [100, 100]
    assert env["windows"] == {}


def test_env_override_forces_uniform_ttl(env):
    env["monkeypatch"].setattr(hl_client, "_CANDLE_TTL_OVERRIDE_S", 600.0)
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    env["clock"]["now"] += 31.0  # would be stale at the 30s per-interval TTL
    hl_client.fetch_hl_candles("BTC", "5m", 100)
    assert _fetched_counts(env["post"]) == [100]  # override keeps it fresh


def test_upgrade_ratchet_capped(env):
    """A pathological huge count is honored for the CALLER but the stored
    window keeps only the capped tail (and a later short read slices it)."""
    env["monkeypatch"].setattr(hl_client, "_MAX_STORED_BARS", 500)
    out = hl_client.fetch_hl_candles("BTC", "5m", 5000)
    assert _fetched_counts(env["post"]) == [5000]  # caller's count honored
    assert len(out) == 5000
    _, bars = env["windows"]["BTC|5m"]
    assert len(bars) == 500  # stored tail capped
    hl_client.fetch_hl_candles("BTC", "5m", 100)   # slices the stored 500
    assert _fetched_counts(env["post"]) == [5000]
