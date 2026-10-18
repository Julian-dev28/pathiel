"""candle_store: disk-first daily candles, tail-only refetch, never a blank desk."""
from __future__ import annotations

import json
import os

import pytest

from pathiel.models.types import Candle
from services.strategies import candle_store as cs

DAY_MS = cs.DAY_MS
NOW_S = 1_800_000_000.0          # fixed clock: 2027-01-15
NOW_MS = int(NOW_S * 1000)


def bars(n: int, *, end_ms: int = NOW_MS, close0: float = 100.0):
    """n daily bars, the last one opening `end_ms` rounded down to the day."""
    last = end_ms - end_ms % DAY_MS
    return [Candle(t=last - (n - 1 - i) * DAY_MS, o=close0 + i, h=close0 + i + 1,
                   l=close0 + i - 1, c=close0 + i, v=1.0) for i in range(n)]


class Fetcher:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, coin, interval, count):
        self.calls.append((coin, interval, count))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(count) if callable(self.result) else self.result


def test_path_is_collision_free_and_reversible():
    assert os.path.basename(cs.path_for("xyz:CL")) == "xyz%3ACL.json"
    assert cs.path_for("xyz:CL") != cs.path_for("xyz_CL")


def test_cold_store_does_one_full_fetch_and_persists():
    f = Fetcher(bars(400))
    out = cs.get_daily("xyz:CL", 400, f, now=NOW_S)
    assert f.calls == [("xyz:CL", "1d", 400)]
    assert len(out) == 400
    stored = cs.load("xyz:CL")
    assert stored.requested == 400 and stored.fetched_at == NOW_S and stored.full_at == NOW_S


def test_fresh_store_makes_zero_requests():
    cs.get_daily("BTC", 400, Fetcher(bars(400)), now=NOW_S)
    f = Fetcher(AssertionError("must not fetch"))
    out = cs.get_daily("BTC", 400, f, max_age_s=600, now=NOW_S + 599)
    assert f.calls == [] and len(out) == 400


def test_stale_store_fetches_only_the_tail_and_newer_print_wins():
    stored = bars(400)
    cs.get_daily("BTC", 400, Fetcher(stored), now=NOW_S)
    later_s = NOW_S + 6 * 3600                        # same UTC day, 6h on: today's bar moved
    tail = bars(3, end_ms=int(later_s * 1000), close0=500.0)
    f = Fetcher(tail)
    out = cs.get_daily("BTC", 400, f, max_age_s=600, now=later_s)
    assert f.calls == [("BTC", "1d", cs.MIN_TAIL_DAYS)]   # 3 bars, not 400
    assert len(out) == 400
    assert out[-1].t == stored[-1].t and out[-1].c == 502.0   # today's bar re-printed
    assert out[-4].c == stored[-4].c                  # older closed bars untouched
    ts = [c.t for c in out]
    assert ts == sorted(set(ts))                      # no duplicates, oldest first


def test_tail_size_grows_with_the_gap():
    cs.get_daily("BTC", 400, Fetcher(bars(400)), now=NOW_S)
    stored = cs.load("BTC")
    stored.full_at = NOW_S + 5 * 86_400               # pretend a full fetch was recent
    cs.save("BTC", stored)
    later_s = NOW_S + 5 * 86_400 + 3600               # 5 days of missing bars
    f = Fetcher(bars(8, end_ms=int(later_s * 1000)))
    out = cs.get_daily("BTC", 400, f, max_age_s=600, now=later_s)
    n = f.calls[0][2]
    assert 6 <= n <= 8, f"tail fetch asked for {n} bars for a 5-day gap"
    assert len(out) == 400 and out[-1].t > bars(1)[-1].t


def test_failed_fetch_serves_disk_instead_of_blank():
    cs.get_daily("ETH", 400, Fetcher(bars(400)), now=NOW_S)
    for failing in (Fetcher([]), Fetcher(RuntimeError("429"))):
        out = cs.get_daily("ETH", 400, failing, max_age_s=0, now=NOW_S + 3600)
        assert len(out) == 400


def test_failed_fetch_with_nothing_on_disk_is_empty():
    assert cs.get_daily("SOL", 400, Fetcher([]), now=NOW_S) == []
    assert cs.load("SOL") is None                      # a failure is never persisted


def test_full_refetch_once_a_day():
    cs.get_daily("BTC", 400, Fetcher(bars(400)), now=NOW_S)
    f = Fetcher(bars(400))
    cs.get_daily("BTC", 400, f, max_age_s=0, now=NOW_S + cs.FULL_REFRESH_S + 1)
    assert f.calls == [("BTC", "1d", 400)]


def test_bigger_request_than_stored_triggers_full_fetch():
    cs.get_daily("BTC", 100, Fetcher(bars(100)), now=NOW_S)
    f = Fetcher(bars(400))
    out = cs.get_daily("BTC", 400, f, now=NOW_S + 1)
    assert f.calls == [("BTC", "1d", 400)] and len(out) == 400


def test_young_coin_with_short_history_is_not_refetched_while_fresh():
    """HL returns fewer bars than asked for a recently listed coin. That
    short answer is complete, so a fresh store must not loop on full fetches."""
    cs.get_daily("xyz:NEW", 400, Fetcher(bars(30)), now=NOW_S)
    f = Fetcher(AssertionError("must not fetch"))
    out = cs.get_daily("xyz:NEW", 400, f, now=NOW_S + 10)
    assert len(out) == 30 and f.calls == []


def test_corrupt_store_is_ignored_not_fatal(state_dir):
    path = cs.path_for("BTC")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"schema": cs.SCHEMA, "fetched_at": "x"}, fh)
    f = Fetcher(bars(10))
    assert len(cs.get_daily("BTC", 10, f, now=NOW_S)) == 10
    assert len(f.calls) == 1


def test_store_lives_under_the_state_dir(state_dir):
    assert cs.path_for("BTC").startswith(os.path.join(state_dir, "strategies", "candles"))


@pytest.mark.parametrize("old,new,expect_c", [
    ([], [(1, 10.0)], [10.0]),
    ([(1, 10.0), (2, 20.0)], [(2, 21.0), (3, 30.0)], [10.0, 21.0, 30.0]),
    ([(3, 30.0), (1, 10.0)], [], [10.0, 30.0]),
])
def test_merge(old, new, expect_c):
    mk = lambda rows: [Candle(t=t, o=c, h=c, l=c, c=c, v=0) for t, c in rows]
    assert [c.c for c in cs.merge(mk(old), mk(new))] == expect_c
