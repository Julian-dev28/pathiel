"""Gate tests for the stop cap eval's pure parts
(services/strategies/evals/stop_cap_test.py): the grid, the walk that
prices each blocked entry, the one-slot shadow chain, the summaries and the
data filters. Synthetic inputs only, no network."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Direction, Params, Run, Trade
from services.strategies.evals.stop_cap_test import (
    blocked_summary, closed_only, equity_at, same_market, shadow_chain, stops_per_day, variants, walk_entry)
from services.strategies.tests.test_stop_cap import THREE_STOPS, _run

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
Q = 900_000
LIMIT = Params(target_mode="limit", taker_fee=0.0, maker_fee=0.0)


def _bars(rows, t0=0):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _long(t):
    return Direction(side="long")


def test_grid_is_baseline_plus_three_caps_each_way():
    v = variants()
    assert list(v) == ["baseline", "cap_1", "cap_2", "cap_3", "side_1", "side_2", "side_3"]
    assert v["baseline"] == {}
    assert v["cap_2"] == {"daily_stop_cap": 2, "cap_per_side": False}
    assert v["side_3"] == {"daily_stop_cap": 3, "cap_per_side": True}


# ── walk_entry ─────────────────────────────────────────────────────────────

def test_walk_entry_bar_can_stop_but_cannot_fill_the_target():
    # the entry bar trades through both: only the stop counts on it
    c15 = _bars([(100, 104.5, 98.5, 100)])
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, {}, _long, LIMIT) == ("stop", 99.0, 0)
    c15 = _bars([(100, 104.5, 99.5, 104), (104, 104.6, 103.9, 104.5)])
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, {}, _long, LIMIT) == ("target", 104.0, Q)


def test_walk_stop_is_judged_before_the_target_in_one_bar():
    c15 = _bars([(100, 100.5, 99.8, 100), (100, 104.5, 98.5, 100)])
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, {}, _long, LIMIT)[0] == "stop"


def test_walk_limit_target_needs_a_trade_through():
    c15 = _bars([(100, 100.5, 99.8, 100), (100, 104.0, 99.8, 103.9), (103.9, 104.02, 103.5, 104)])
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, {}, _long, LIMIT) == ("target", 104.0, 2 * Q)


def test_walk_direction_flip_and_invalidation_close_it():
    c15 = _bars([(100, 100.5, 99.8, 100), (100.5, 100.6, 100.2, 100.4)])
    flip = lambda t: Direction(side="long") if t < Q else Direction(side="short")
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, {}, flip, LIMIT) == ("direction_flip", 100.5, Q)
    inv = lambda t: Direction(side="long", invalidation_px=99.6)
    c1h = {Q: Candle(t=Q - HOUR_MS, o=100, h=100, l=99.5, c=99.55, v=1)}
    assert walk_entry("long", 0, 100.0, 99.0, 104.0, c15, c1h, inv, LIMIT) == ("invalidation", 99.55, Q)


def test_walk_short_mirrors_and_end_marks_out():
    c15 = _bars([(100, 100.2, 99.9, 100), (100, 100.1, 98.0, 98.5)])
    assert walk_entry("short", 0, 100.0, 101.0, 96.0, c15, {}, lambda t: Direction(side="short"), LIMIT) \
        == ("end", 98.5, Q)


# ── shadow chain ───────────────────────────────────────────────────────────

def _blocked(t, side="long", entry=100.0, stop=99.0, target=104.0, market=True):
    return {"time": t, "kind": "entry", "side": side, "entry_px": entry, "stop_px": stop,
            "target_px": target, "is_market": market, "stops_today": 1}


def test_chain_is_one_slot_and_resumes_after_the_shadow_exit():
    c15 = _bars([(100, 100.2, 99.8, 100), (100, 100.2, 98.5, 99), (100, 100.2, 99.8, 100),
                 (100, 104.5, 99.9, 104.2)])
    run = Run(params=LIMIT, equity_curve=[(i * Q, 64.0) for i in range(4)])
    # blocked at bars 0, 1 (while shadow #1 is open: skipped), 2 (after its stop: walked)
    run.cap_blocked = [_blocked(0), _blocked(Q), _blocked(2 * Q)]
    rows = shadow_chain(run, c15, [], _long, LIMIT)
    assert [(r["time"][-6:], r["reason"]) for r in rows] == [("00:00Z", "stop"), ("00:30Z", "target")]
    assert [r["r_gross"] for r in rows] == [-1.0, 4.0]
    assert [r["outcome"] for r in rows] == ["loss", "win"]


def test_chain_fees_and_dollars_match_the_sim_sizing():
    p = Params(target_mode="limit")                     # 4.5 bps taker in, 1.5 bps maker on the target
    c15 = _bars([(100, 100.2, 99.8, 100), (100, 104.5, 99.9, 104.2)])
    run = Run(params=p, equity_curve=[(0, 64.0), (Q, 64.0)])
    run.cap_blocked = [_blocked(0)]
    (row,) = shadow_chain(run, c15, [], _long, p)
    fee_px = p.taker_fee * 100.0 + p.maker_fee * 104.0
    assert row["r_net"] == pytest.approx(round(4.0 - fee_px, 3))
    notional = 64.0 / (1 + 20 * p.taker_fee) * 20
    assert row["usd_est"] == pytest.approx(round(notional / 100.0 * (4.0 - fee_px), 2))


def test_rest_setups_are_listed_not_walked():
    run = Run(params=LIMIT)
    run.cap_blocked = [_blocked(0, market=False)]
    (row,) = shadow_chain(run, _bars([(100, 101, 99, 100)]), [], _long, LIMIT)
    assert row["reason"] == "rest_not_walked" and "r_net" not in row
    s = blocked_summary([row])
    assert s["entries"] == 1 and s["wins"] == s["losses"] == s["open"] == 0 and s["r_net"] == 0


def test_blocked_summary_signs():
    rows = [{"reason": "stop", "r_net": -1.1, "usd_est": -5.0, "outcome": "loss"},
            {"reason": "target", "r_net": 3.0, "usd_est": 12.0, "outcome": "win"},
            {"reason": "end", "r_net": 0.5, "usd_est": 2.0, "outcome": "open"}]
    s = blocked_summary(rows)
    assert (s["wins"], s["losses"], s["open"]) == (1, 1, 1)
    assert s["r_net"] == pytest.approx(2.4) and s["usd_est"] == pytest.approx(9.0)
    assert s["reasons"] == {"end": 1, "stop": 1, "target": 1}


def test_chain_on_a_real_sim_run_prices_the_blocked_reentry():
    run = _run(THREE_STOPS, daily_stop_cap=1)
    from services.strategies.tests.test_stop_cap import _bars as sim_bars, _params
    c15 = sim_bars(THREE_STOPS, 24 * HOUR_MS)
    rows = shadow_chain(run, c15, [], _long, _params(daily_stop_cap=1))
    # the first blocked re-entry (bar 2) is stopped on bar 3; the next (bar 4) on bar 5; bar 6 runs to the end
    assert [r["reason"] for r in rows] == ["stop", "stop", "end"]
    assert all(r["r_gross"] == pytest.approx(-1.0) for r in rows[:2])


# ── summaries + filters ────────────────────────────────────────────────────

def test_stops_per_day_histogram():
    def tr(day, reason="stop"):
        return Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=2,
                     exit_time=day * DAY_MS + 5, exit_reason=reason)
    run = Run(trades=[tr(0), tr(1), tr(1), tr(2), tr(2), tr(2), tr(2), tr(2), tr(3, "target")])
    assert stops_per_day(run) == {"1": 1, "2": 1, "3": 0, "4+": 1}


def test_equity_at_reads_the_curve():
    run = Run(params=Params(starting_equity=64.0), equity_curve=[(0, 64.0), (Q, 60.0), (2 * Q, 70.0)])
    assert equity_at(run, Q + 5) == 60.0 and equity_at(run, -1) == 64.0 and equity_at(run, 9 * Q) == 70.0


def test_closed_only_drops_the_forming_bar():
    c = _bars([(1, 1, 1, 1)] * 3)
    assert [x.t for x in closed_only(c, Q, 3 * Q - 1)] == [0, Q]
    assert len(closed_only(c, Q, 3 * Q)) == 3


def test_same_market_drops_the_gold_thesis():
    c1h = [Candle(t=i * HOUR_MS, o=50, h=50.2, l=49.8, c=50.0, v=1) for i in range(48)]
    def th(t, entry, side="short"):
        return D.ThesisDirection(created_at_ms=t, side=side, entry_px=entry, stop_px=None,
                                 invalidation_px=None, targets=[], thesis_id=str(t))
    kept = same_market([th(2 * HOUR_MS, 4028.0), th(10 * HOUR_MS, 49.7), th(20 * HOUR_MS, None, "flat"),
                        th(30 * HOUR_MS, 50.4)], c1h)
    assert [k.created_at_ms for k in kept] == [10 * HOUR_MS, 20 * HOUR_MS, 30 * HOUR_MS]
    # a flat thesis before any silver one is dropped too (it belongs to the old market)
    assert same_market([th(1 * HOUR_MS, None, "flat")], c1h) == []
