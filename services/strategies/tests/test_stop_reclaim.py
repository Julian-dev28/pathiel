"""Gate tests for the stop-reclaim rule (desk_tick_sim.Params.reclaim_after_stops
/ reclaim_window_h, reclaim_blocks; 2026-11-10) and the pure parts of its
eval (services/strategies/evals/stop_reclaim_test.py).

Operator, 2026-11-09/10: four Brent longs in a row were stopped (96.10,
95.60, 93.90, 92.60) while the thesis kept moving the buy lower. The rule:
after 2 same-side stops inside 24h, no new entry on that side until a
CLOSED 1h bar closes back beyond the last stopped trade's stop.

Synthetic candles only, no network. Same flat warmup as test_stop_cap: a
24h range of 100 to 104, so a market long at 100.3 stops at 99.8 with the
target at 104, and a market short at 103.7 stops at 104.2 with the target
at 100. The sim's own result is pinned OFF (the backtest failed its bar,
STOP_RECLAIM_TEST_2026-11-10.md): `live_params()` must keep it off.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, Trade, live_params, reclaim_blocks, simulate)
from services.strategies.evals import stop_reclaim_test as E
from services.strategies.tests.test_stop_cap import (
    ENTER_LONG, ENTER_SHORT, QUIET, STOP_LONG, STOP_SHORT, THREE_STOPS, _bars, _params, _run, _warmup)

HOUR_MS = 3_600_000
Q = 900_000
T0 = 24 * HOUR_MS          # the first tick: right after the 24h warmup


def _ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _stop(side: str, exit_time: int, stop_px: float, reason: str = "stop") -> Trade:
    return Trade(side=side, entry_time=exit_time - HOUR_MS, entry_px=stop_px + (1 if side == "long" else -1),
                 size=1.0, stop_px=stop_px, target_px=stop_px + (5 if side == "long" else -5),
                 exit_time=exit_time, exit_px=stop_px, exit_reason=reason)


def _bar(t: int, c: float) -> Candle:
    return Candle(t=t, o=c, h=c + 0.1, l=c - 0.1, c=c, v=1.0)


ON = Params(reclaim_after_stops=2)
NOW = 100 * HOUR_MS
STOPS = [_stop("long", NOW - 6 * HOUR_MS, 95.05), _stop("long", NOW - 2 * HOUR_MS, 94.80)]


def _long(t):
    return Direction(side="long")


def _short(t):
    return Direction(side="short")


# ── defaults / golden ──────────────────────────────────────────────────────

def test_rule_is_off_by_default_and_off_live():
    assert Params().reclaim_after_stops is None and Params().reclaim_window_h == 24.0
    assert live_params().reclaim_after_stops is None


def test_off_is_byte_identical():
    off = _run(THREE_STOPS)
    explicit = _run(THREE_STOPS, reclaim_after_stops=None, reclaim_window_h=1.0)
    assert [dataclasses.asdict(t) for t in off.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert off.equity_curve == explicit.equity_curve
    assert off.refused.get("reclaim", 0) == 0 and off.reclaim_blocked == []


def test_bad_values_are_refused():
    with pytest.raises(ValueError):
        simulate([], _warmup(), _long, _params(reclaim_after_stops=0))
    with pytest.raises(ValueError):
        simulate([], _warmup(), _long, _params(reclaim_after_stops=2, reclaim_window_h=0))


# ── the pure rule ──────────────────────────────────────────────────────────

def test_two_stops_in_the_window_block_until_a_reclaim():
    block = reclaim_blocks(STOPS, [], NOW, "long", ON)
    assert block == {"reclaim_px": 94.80, "stops_in_window": 2, "last_stop_time": NOW - 2 * HOUR_MS}


def test_off_or_one_stop_never_blocks():
    assert reclaim_blocks(STOPS, [], NOW, "long", Params()) is None
    assert reclaim_blocks(STOPS[1:], [], NOW, "long", ON) is None


def test_a_closed_1h_bar_back_beyond_the_last_stop_clears_it():
    reclaim = _bar(NOW - 2 * HOUR_MS, 94.9)          # closes at NOW - 1h, after the stop, above 94.80
    not_enough = _bar(NOW - 2 * HOUR_MS, 94.5)       # closes below the stop: still broken
    assert reclaim_blocks(STOPS, [reclaim], NOW, "long", ON) is None
    assert reclaim_blocks(STOPS, [not_enough], NOW, "long", ON) is not None


def test_a_bar_that_closed_before_or_at_the_stop_proves_nothing():
    before = _bar(NOW - 4 * HOUR_MS, 96.0)           # closes at NOW - 3h, before the 94.80 stop at NOW - 2h
    at = _bar(NOW - 3 * HOUR_MS, 96.0)               # closes exactly when the stop filled
    assert reclaim_blocks(STOPS, [before, at], NOW, "long", ON) is not None


def test_the_open_1h_bar_is_not_read():
    # the caller passes CLOSED bars only; a bar whose close is after `now` is never in the list.
    # Here the only bar closing after the stop closes after NOW, so at NOW it is still forming:
    forming = _bar(NOW - 30 * 60_000, 95.5)
    closed_at_now = [b for b in [forming] if b.t + HOUR_MS <= NOW]
    assert closed_at_now == [] and reclaim_blocks(STOPS, closed_at_now, NOW, "long", ON) is not None
    # once it has closed (an hour later) it clears the block
    assert reclaim_blocks(STOPS, [forming], NOW + HOUR_MS, "long", ON) is None


def test_stops_older_than_the_window_do_not_count():
    old = [_stop("long", NOW - 30 * HOUR_MS, 95.05), _stop("long", NOW - 2 * HOUR_MS, 94.80)]
    assert reclaim_blocks(old, [], NOW, "long", ON) is None
    assert reclaim_blocks(old, [], NOW, "long", Params(reclaim_after_stops=2, reclaim_window_h=48)) is not None


def test_only_stop_exits_on_that_side_count():
    mixed = [_stop("long", NOW - 6 * HOUR_MS, 95.05, "target"), _stop("long", NOW - 2 * HOUR_MS, 94.80)]
    assert reclaim_blocks(mixed, [], NOW, "long", ON) is None
    assert reclaim_blocks(STOPS, [], NOW, "short", ON) is None         # two long stops never block a short


def test_mirror_for_shorts():
    shorts = [_stop("short", NOW - 6 * HOUR_MS, 104.2), _stop("short", NOW - 2 * HOUR_MS, 105.0)]
    assert reclaim_blocks(shorts, [], NOW, "short", ON)["reclaim_px"] == 105.0
    assert reclaim_blocks(shorts, [_bar(NOW - 2 * HOUR_MS, 104.9)], NOW, "short", ON) is None   # closed under 105
    assert reclaim_blocks(shorts, [_bar(NOW - 2 * HOUR_MS, 105.2)], NOW, "short", ON) is not None
    assert reclaim_blocks(shorts, [], NOW, "long", ON) is None


def test_the_2026_11_09_sequence_is_refused_until_a_1h_close_above_the_last_stop():
    """The incident: longs stopped at 95.05 (06:30Z) and 94.80 (11:15Z) on
    11-09; the 13:00Z buy near 92.60 is refused until a 1h bar closes above
    94.80. The 12:00 bar closing 93.1 does not clear it; a 14:00 bar
    closing 94.9 does."""
    stops = [_stop("long", _ms("2026-11-09 06:30"), 95.05), _stop("long", _ms("2026-11-09 11:15"), 94.80)]
    at_1300 = _ms("2026-11-09 13:00")
    bar_12 = _bar(_ms("2026-11-09 12:00"), 93.1)           # closed 13:00Z
    block = reclaim_blocks(stops, [bar_12], at_1300, "long", ON)
    assert block is not None and block["reclaim_px"] == 94.80 and block["stops_in_window"] == 2
    bar_14 = _bar(_ms("2026-11-09 14:00"), 94.9)           # closed 15:00Z
    assert reclaim_blocks(stops, [bar_12, bar_14], _ms("2026-11-09 15:00"), "long", ON) is None
    # the morning's first stop alone never blocked anything
    assert reclaim_blocks(stops[:1], [], _ms("2026-11-09 07:00"), "long", ON) is None


# ── inside the sim ─────────────────────────────────────────────────────────

def test_the_third_entry_is_skipped_and_kept_for_the_eval():
    off = _run(THREE_STOPS)
    on = _run(THREE_STOPS, reclaim_after_stops=2)
    assert len(off.trades) == 3 and off.position_side == "long"      # three stops, the fourth entry still open
    assert len(on.trades) == 2 and all(t.exit_reason == "stop" for t in on.trades) and on.position_side is None
    assert on.refused["reclaim"] == 3                 # ticks 4, 5 and 6 would all have bought
    assert [b["time"] for b in on.reclaim_blocked] == [T0 + 4 * Q, T0 + 5 * Q, T0 + 6 * Q]
    row = on.reclaim_blocked[0]
    assert row["kind"] == "entry" and row["side"] == "long" and row["is_market"] is True
    assert row["reclaim_px"] == pytest.approx(99.8) and row["stops_in_window"] == 2
    assert row["entry_px"] == pytest.approx(100.3) and row["stop_px"] == pytest.approx(99.8)
    assert on.final_equity == pytest.approx(sum(t.net_pnl_usd for t in on.trades) + 64.0)


def test_one_stop_never_blocks_the_re_entry():
    on = _run([ENTER_LONG, STOP_LONG, QUIET], reclaim_after_stops=2)
    assert len(on.trades) == 1 and on.refused.get("reclaim", 0) == 0
    assert on.open_position_levels["opened_at"] == T0 + 2 * Q          # the re-entry, still open at the end


def test_a_closed_1h_bar_back_above_the_stop_lets_the_next_entry_through():
    rows = [ENTER_LONG, STOP_LONG, QUIET, STOP_LONG, QUIET, QUIET, QUIET, QUIET]
    # a 1h bar opening at tick 3 closes at tick 7 (T0 + 105m), back above the 99.8 stop
    c1h = _warmup() + [Candle(t=T0 + 3 * Q, o=100.3, h=100.4, l=100.1, c=100.3, v=1.0)]
    on = simulate(_bars(rows, T0), c1h, _long, _params(reclaim_after_stops=2))
    assert on.refused["reclaim"] == 3                 # ticks 4, 5, 6: the bar is still forming
    assert [t.entry_time for t in on.trades] == [T0, T0 + 2 * Q]
    assert on.open_position_levels["opened_at"] == T0 + 7 * Q         # the reclaim let tick 7 buy
    without = simulate(_bars(rows, T0), _warmup(), _long, _params(reclaim_after_stops=2))
    assert without.refused["reclaim"] == 4 and len(without.trades) == 2 and without.position_side is None


def test_the_block_expires_with_the_window():
    rows = [ENTER_LONG, STOP_LONG, QUIET, STOP_LONG] + [QUIET] * 6
    short_window = _run(rows, reclaim_after_stops=2, reclaim_window_h=1.0)
    # the two stops sit at ticks 1 and 3; one hour after tick 1 (tick 5) only one stop is inside the window
    assert [t.entry_time for t in short_window.trades] == [T0, T0 + 2 * Q]
    assert short_window.open_position_levels["opened_at"] == T0 + 5 * Q
    assert short_window.refused["reclaim"] == 1 and short_window.reclaim_blocked[0]["time"] == T0 + 4 * Q


def test_shorts_mirror_inside_the_sim_and_the_other_side_is_free():
    rows = [ENTER_SHORT, STOP_SHORT, (103.7, 103.8, 103.6, 103.7), STOP_SHORT, (103.7, 103.8, 103.6, 103.7)]
    on = _run(rows, direction=_short, reclaim_after_stops=2)
    assert len(on.trades) == 2 and on.refused["reclaim"] == 1
    assert on.reclaim_blocked[0]["side"] == "short" and on.reclaim_blocked[0]["reclaim_px"] == pytest.approx(104.2)

    def flip(t):
        return Direction(side="short") if t < T0 + 4 * Q else Direction(side="long")
    # after two short stops the thesis turns long: the long at tick 4 is not blocked
    free = _run(rows[:4] + [QUIET], direction=flip, reclaim_after_stops=2)
    assert free.position_side == "long" and free.open_position_levels["opened_at"] == T0 + 4 * Q
    assert free.refused.get("reclaim", 0) == 0


def test_an_open_position_is_never_touched():
    # a stop, then a re-entry that is blocked... until the block is on, the open position runs as before:
    # the rule only gates NEW entries, it never closes, moves or resizes a position
    rows = [ENTER_LONG, (100.3, 102.0, 100.25, 101.8), (101.8, 102.5, 101.5, 102.0)]
    off, on = _run(rows), _run(rows, reclaim_after_stops=1)
    assert off.trades == on.trades == []                       # still open at the end, both
    assert off.open_position_at_end == on.open_position_at_end and on.open_position_at_end is not None
    assert on.refused.get("reclaim", 0) == 0


# ── the eval's pure parts ──────────────────────────────────────────────────

def test_grid_and_params():
    assert E.variants() == {"baseline": {}, "reclaim": {"reclaim_after_stops": 2, "reclaim_window_h": 24.0}}
    p = E.base_params()
    assert p.reclaim_after_stops is None and p.risk_cap_frac == 0.06 and p.starting_equity == 194.0
    assert p.weekend_off == "flatten" and p.no_break_entry is True and p.size_slippage is True
    # the live 20/15 leverage fit (2026-11-13 correction), read off next_trade
    from services.strategies.next_trade import BRENT, leverage_fit_for
    assert p.entry_fit_leverage == tuple(leverage_fit_for(BRENT))
    assert dataclasses.replace(p, entry_fit_leverage=None).entry_fit_leverage is None   # what --no-lev-fit runs


def test_thirds_are_equal_and_contiguous():
    th = E.thirds(0, 9 * HOUR_MS)
    assert th == [(0, 3 * HOUR_MS), (3 * HOUR_MS, 6 * HOUR_MS), (6 * HOUR_MS, 9 * HOUR_MS)]
    assert E.thirds(0, 10)[-1][1] == 10


def _ds(base_full, rec_full, thirds):
    return {"variants": {"baseline": {"net_usd": base_full}, "reclaim": {"net_usd": rec_full}},
            "thirds": [{"baseline": {"net_usd": b}, "reclaim": {"net_usd": r}} for b, r in thirds]}


def test_the_bar_needs_the_full_window_and_two_thirds():
    ok = E.passes(_ds(10, 12, [(1, 2), (3, 3), (5, 4)]))
    assert ok["pass"] and ok["thirds_not_worse"] == 2 and ok["delta_usd"] == 2
    assert not E.passes(_ds(10, 9, [(1, 2), (3, 3), (5, 6)]))["pass"]          # full window worse
    assert not E.passes(_ds(10, 12, [(1, 0), (3, 2), (5, 6)]))["pass"]         # only one third
    assert E.passes(_ds(10, 10, [(1, 1), (3, 3), (5, 5)]))["pass"]             # a no-op passes


def test_verdict_needs_every_thesis_dataset():
    good, bad = _ds(10, 12, [(1, 2), (3, 3), (5, 4)]), _ds(10, 9, [(1, 2), (3, 3), (5, 6)])
    assert E.verdict({"brent_journal": good, "brent_replay30": good, "brent_always_long_30d": bad})["pass"]
    v = E.verdict({"brent_journal": bad, "brent_replay30": good})
    assert not v["pass"] and v["datasets_judged"] == ["brent_journal", "brent_replay30"]
    assert not E.verdict({"brent_always_long_30d": good})["pass"]              # no thesis set: never a pass


def test_walk_weekend_closes_at_the_first_weekend_bar():
    # Friday 2026-11-06 21:00Z is 16:00 New York: the oil weekend opens
    fri_2045 = _ms("2026-11-06 20:45")
    bars = [Candle(t=fri_2045 + i * Q, o=97.0 + i * 0.1, h=97.3 + i * 0.1, l=96.9, c=97.1 + i * 0.1, v=1.0)
            for i in range(4)]
    p = _params(weekend_off="flatten")
    reason, px, t = E.walk_weekend("long", fri_2045, 97.0, 93.0, 104.0, bars, {}, _long, p)
    assert (reason, px, t) == ("weekend", pytest.approx(97.1), fri_2045 + Q)
    reason2, _, _ = E.walk_weekend("long", fri_2045, 97.0, 93.0, 104.0, bars, {}, _long, _params())
    assert reason2 == "end"                                                   # weekends traded: walked to the end


def test_summaries_and_incident():
    run = _run(THREE_STOPS, reclaim_after_stops=2)
    s = E.summarize(run, 64.0)
    assert s["trades"] == 2 and s["wins"] == 0 and s["blocked_ticks"] == 3 and s["refused_reclaim"] == 3
    assert s["net_usd"] == pytest.approx(round(run.final_equity - 64.0, 2))
    rows = [{"r_net": -1.0, "usd_est": -5.0, "outcome": "loss"}, {"r_net": 2.0, "usd_est": 9.0, "outcome": "win"},
            {"reason": "rest_not_walked"}]
    b = E.blocked_summary(rows)
    assert b == {"entries": 3, "wins": 1, "losses": 1, "open": 0, "r_net": 1.0, "usd_est": 4.0}
    ds = {"trades": {"baseline": [{"entry": "2026-11-09 07:45Z", "exit": "2026-11-09 11:15Z"},
                                  {"entry": "2026-11-04 17:30Z", "exit": "2026-11-06 21:00Z"}]},
          "blocked_rows": [{"time": "2026-11-10 01:00Z"}, {"time": "2026-11-04 17:30Z"}]}
    inc = E.incident(ds)
    assert len(inc["trades"]["baseline"]) == 1 and inc["blocked"] == [{"time": "2026-11-10 01:00Z"}]
