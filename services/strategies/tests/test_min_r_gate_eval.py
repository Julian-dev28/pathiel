"""Gate tests for the min_r gate eval's pure parts
(services/strategies/evals/min_r_gate_test.py): the grid, the per-trade and
per-window summaries, the drawdown-on-a-slice helper, the adoption bar, and
the real-tick-log parser/grouper/matcher. Synthetic inputs and literal
fixture lines copied from the real log only; no network, <2s. The log
fixture lines below are copied verbatim from `/tmp/oil-loop/progress.log`
(2026-11-09 and 2026-11-16/17/18), so the parser is gate-tested against
text the real desk actually wrote, not a cleaned-up approximation of it."""
from __future__ import annotations

import pytest

from services.strategies.backtest.desk_tick_sim import Trade, simulate
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.min_r_gate_test import (
    LIVE, adoption_verdict, adv_usd, curve_window, full_summary, group_episodes, half_summaries, load_tick_log_rows,
    match_episode_trade, max_drawdown_pct_curve, parse_tick_line, qualifying_ticks, replay_rows, side_summaries,
    summarize_trades, variants, window_trades, worst_losing_streak, would_enter_at)
from services.strategies.tests.test_desk_tick_sim import _c15, _warmup_1h, const_direction

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000
DAY_MS = 86_400_000


def _trade(entry_time=0, exit_time=None, net=0.0, r=None, side="long") -> Trade:
    return Trade(side=side, entry_time=entry_time, entry_px=100.0, size=1.0, stop_px=99.0, target_px=102.0,
                exit_time=exit_time, net_pnl_usd=net, r_multiple=r)


# ── the grid ─────────────────────────────────────────────────────────────

def test_variants_are_named_by_their_plain_min_r():
    v = variants()
    assert list(v) == ["1.25", "1.5", "1.75", "2", "2.5"]
    assert v["1.5"] == {"min_r": 1.5}
    assert v["2"] == {"min_r": 2.0}


def test_2_0_ties_live_brent_params_exactly():
    """The grid's "2" row is `live_brent_params()` itself -- the operator's
    live rule, not a reimplementation of it."""
    import dataclasses
    live = live_brent_params(starting_equity=64.0)
    swept = dataclasses.replace(live_brent_params(starting_equity=64.0), min_r=LIVE)
    assert swept == live


# ── per-trade / per-window summaries ───────────────────────────────────────

def test_worst_losing_streak_counts_non_winners_in_a_row():
    trades = [_trade(0, net=5.0), _trade(1, net=-1.0), _trade(2, net=-2.0), _trade(3, net=0.0),
              _trade(4, net=-1.0), _trade(5, net=3.0)]
    assert worst_losing_streak(trades) == 4   # indices 1,2,3,4 (a scratch still isn't a win, so it extends the run)
    assert worst_losing_streak([]) == 0
    assert worst_losing_streak([_trade(0, net=1.0)]) == 0


def test_summarize_trades_rates_and_sums():
    trades = [_trade(0, net=10.0, r=2.0), _trade(DAY_MS, net=-5.0, r=-1.0), _trade(2 * DAY_MS, net=20.0, r=3.0)]
    s = summarize_trades(trades, days=7.0)
    assert s["trades"] == 3
    assert s["trades_per_week"] == 3.0
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["mean_r"] == pytest.approx((2.0 - 1.0 + 3.0) / 3, abs=1e-3)
    assert s["net_r"] == pytest.approx(4.0)
    assert s["net_usd"] == pytest.approx(25.0)
    assert s["worst_losing_streak"] == 1


def test_summarize_trades_empty_is_all_none_or_zero():
    s = summarize_trades([], days=7.0)
    assert s == {"trades": 0, "trades_per_week": 0.0, "win_rate": None, "mean_r": None, "net_r": 0.0,
                "net_usd": 0.0, "worst_losing_streak": 0}


def test_max_drawdown_pct_curve_resets_at_the_segment_start():
    curve = [(0, 100.0), (1, 90.0), (2, 95.0), (3, 80.0), (4, 120.0)]
    # peak sequence: 100,100,100,100,120 -> worst (100-80)/100 = 20%
    assert max_drawdown_pct_curve(curve) == pytest.approx(20.0, abs=0.1)
    assert max_drawdown_pct_curve([]) == 0.0
    assert max_drawdown_pct_curve([(0, 50.0)]) == 0.0


def test_window_and_curve_window_filter_half_open():
    trades = [_trade(0), _trade(10), _trade(20)]
    assert [t.entry_time for t in window_trades(trades, 0, 20)] == [0, 10]
    curve = [(0, 1.0), (10, 2.0), (20, 3.0)]
    assert curve_window(curve, 0, 20) == [(0, 1.0), (10, 2.0)]


def test_half_summaries_partition_the_full_trade_list():
    class FakeRun:
        trades = [_trade(0, net=1.0), _trade(DAY_MS, net=2.0), _trade(9 * DAY_MS, net=-1.0)]
        equity_curve = [(0, 64.0), (DAY_MS, 65.0), (9 * DAY_MS, 64.0)]
    h1, h2 = half_summaries(FakeRun(), 0, 10 * DAY_MS)
    assert h1["trades"] == 2 and h2["trades"] == 1
    assert h1["net_usd"] == pytest.approx(3.0) and h2["net_usd"] == pytest.approx(-1.0)


def test_side_summaries_split_long_and_short():
    class FakeRun:
        trades = [_trade(0, net=5.0, side="long"), _trade(1, net=-3.0, side="short")]
    s = side_summaries(FakeRun(), 0, 7 * DAY_MS)
    assert s["long"]["trades"] == 1 and s["long"]["net_usd"] == pytest.approx(5.0)
    assert s["short"]["trades"] == 1 and s["short"]["net_usd"] == pytest.approx(-3.0)


def test_full_summary_reads_the_runs_own_fields():
    from collections import Counter
    class FakeRun:
        trades = [_trade(0, net=4.0, r=1.0)]
        max_drawdown_pct = 0.123
        liquidated = False
        refused = Counter({"min_r": 7})
        final_equity = 68.0
    s = full_summary(FakeRun(), 0, 7 * DAY_MS)
    assert s["max_dd_pct"] == 12.3 and s["refused_min_r"] == 7 and s["final_equity"] == 68.0


# ── a real sim proves lower min_r admits a trade a higher one refuses ──────

def test_lower_min_r_admits_what_the_live_gate_refuses():
    """`Params(stop_buffer=0.50)` on a flat 8h/24h range of [99,101] prices
    R = 0.8 (`test_desk_tick_sim.py::test_min_r_gate_refuses`): 2.0 and 1.5
    both refuse it outright (never rests); 0.5 clears the gate and rests
    the limit (this one-bar fixture has no later bar to fill it on, so the
    trade itself only shows up in `test_pullback_fills_and_stops`-style
    multi-bar scenarios -- `rested` is what changes here). Grounds that the
    grid's variants actually change what the engine admits, not just a
    label."""
    c1h = _warmup_1h(o=99.1, h=99.3, l=98.9, c=99.1)
    c15 = [_c15(24 * HOUR_MS, 98.9, 98.9, 98.9, 98.9)]
    from services.strategies.backtest.desk_tick_sim import Params
    import dataclasses
    base = Params(stop_buffer=0.50, leverage=20.0, max_leverage=20.0)
    for r in (2.0, 1.5):
        run = simulate(c15, c1h, const_direction("long"), dataclasses.replace(base, min_r=r))
        assert run.trades == [] and run.rested == 0 and run.refused["min_r"] >= 1
    run = simulate(c15, c1h, const_direction("long"), dataclasses.replace(base, min_r=0.5))
    assert run.rested == 1 and "min_r" not in run.refused


# ── adoption bar ────────────────────────────────────────────────────────────

def _full(net_by_r, dd_by_r=None):
    dd_by_r = dd_by_r or {r: 50.0 for r in net_by_r}
    return {f"{r:g}": {"net_usd": net_by_r[r], "max_dd_pct": dd_by_r[r]} for r in net_by_r}


def test_adv_usd_is_the_edge_over_live():
    by_r = _full({1.5: 100.0, 2.0: 60.0})
    assert adv_usd(by_r, 1.5) == pytest.approx(40.0)
    assert adv_usd({}, 1.5) is None


def test_adoption_verdict_adopts_when_all_three_bars_clear():
    # adv(2.0) = 0 by definition; adv(1.5) = 40; adv(1.25) = 25, adv(1.75) = 30 -- both >= 0.5 * 40.
    full = _full({1.25: 85.0, 1.5: 100.0, 1.75: 90.0, 2.0: 60.0, 2.5: 20.0},
                 {1.25: 50.0, 1.5: 51.0, 1.75: 50.0, 2.0: 50.0, 2.5: 50.0})
    primary = {"ds1": {"full": full, "half1": _full({1.5: 10.0, 2.0: 5.0}), "half2": _full({1.5: 10.0, 2.0: 5.0})}}
    v = adoption_verdict(primary)
    assert v == {"adopt": True, "both_halves": True, "dd_ok": True, "plateau_ok": True, "reasons": [],
                "verdict": "ADOPT 1.5R"}


def test_adoption_verdict_fails_on_a_half_that_loses():
    primary = {"ds1": {"full": _full({1.25: 70, 1.5: 100, 1.75: 90, 2.0: 60, 2.5: 20}),
                       "half1": _full({1.5: 10.0, 2.0: 5.0}), "half2": _full({1.5: 3.0, 2.0: 8.0})}}
    v = adoption_verdict(primary)
    assert v["adopt"] is False and v["both_halves"] is False
    assert "half2" in v["reasons"][0]


def test_adoption_verdict_fails_on_a_material_drawdown():
    full = _full({1.25: 85, 1.5: 100, 1.75: 90, 2.0: 60, 2.5: 20}, {1.25: 50, 1.5: 70, 1.75: 50, 2.0: 50, 2.5: 50})
    primary = {"ds1": {"full": full, "half1": _full({1.5: 10.0, 2.0: 5.0}), "half2": _full({1.5: 10.0, 2.0: 5.0})}}
    v = adoption_verdict(primary)
    assert v["adopt"] is False and v["dd_ok"] is False and "drawdown" in v["reasons"][0]


def test_adoption_verdict_fails_plateau_when_a_neighbour_collapses():
    full = _full({1.25: -50, 1.5: 100, 1.75: 90, 2.0: 60, 2.5: 20})   # 1.25 is way under half of 1.5's +40 edge
    primary = {"ds1": {"full": full, "half1": _full({1.5: 10.0, 2.0: 5.0}), "half2": _full({1.5: 10.0, 2.0: 5.0})}}
    v = adoption_verdict(primary)
    assert v["adopt"] is False and v["plateau_ok"] is False


def test_adoption_verdict_leaning_when_two_of_three_pass():
    full = _full({1.25: 85, 1.5: 100, 1.75: 90, 2.0: 60, 2.5: 20}, {1.25: 50, 1.5: 70, 1.75: 50, 2.0: 50, 2.5: 50})
    primary = {"ds1": {"full": full, "half1": _full({1.5: 10.0, 2.0: 5.0}), "half2": _full({1.5: 10.0, 2.0: 5.0})}}
    v = adoption_verdict(primary)
    assert v["verdict"].startswith("LEANING") and v["both_halves"] and v["plateau_ok"] and not v["dd_ok"]


# ── real-tick-log parser (literal lines from /tmp/oil-loop/progress.log) ──

# 2026-11-09: a pure chase block beside a high R -- must NOT qualify at any min_r.
CHASE_LINE = ("DESK-TICK oil 2026-11-09T09:05Z coin=xyz:BRENTOIL mid=95.7605 pos=flat action=NONE "
             "pays 1.86R, mid above thesis entry 95.60")
# 2026-11-16T02:40Z: the lone qualifying tick at 1.5R (no chase/break/blackout phrase).
PURE_R_LINE = ("DESK-TICK oil 2026-11-16T02:40Z coin=xyz:BRENTOIL mid=92.43 pos=flat action=NONE "
              "pays 1.52R at 92.43, needs 2R; buy at 92.30 or lower")
# 2026-11-16T13:10Z/13:25Z/13:40Z: three consecutive qualifying ticks, one episode.
CLUSTER_LINES = [
    "DESK-TICK oil 2026-11-16T13:10Z coin=xyz:BRENTOIL mid=91.8995 pos=flat action=NONE "
    "pays 1.63R at 91.90, waits for 91.77 or lower",
    "DESK-TICK oil 2026-11-16T13:25Z coin=xyz:BRENTOIL mid=91.836 pos=flat action=NONE "
    "pays 1.79R at 91.836, needs 2R; buy at 91.77 or lower",
    "DESK-TICK oil 2026-11-16T13:40Z coin=xyz:BRENTOIL mid=91.9285 pos=flat action=NONE "
    "pays 1.56R at 91.93, needs 2R; buys at 91.77 or lower",
]
# 2026-11-18: an EIA blackout beside an R quote -- must NOT qualify either.
BLACKOUT_LINE = ("DESK-TICK oil 2026-11-18T13:40Z coin=xyz:BRENTOIL mid=89.9015 pos=flat action=NONE "
                 "EIA blackout until 2026-11-18T17:30Z; short waits 91.22, pays 0.46R")
# A HOLD line (not NONE) and the old OIL-TICK header: neither parses into a would-enter row.
HOLD_LINE = ("DESK-TICK oil 2026-10-23T06:04:15Z coin=xyz:BRENTOIL mid=108.00 pos=1.27@107.61 stop=106.70 "
            "tp=108.45 action=HOLD covered, +$0.49")
OLD_HEADER_LINE = ("OIL-TICK 2026-10-22T16:30:11Z mid=107.53 pos=1.27@107.61 stop=106.70 tp=108.45 "
                   "action=HOLD in position, covered")


def test_parse_tick_line_reads_time_mid_action_and_r():
    row = parse_tick_line(PURE_R_LINE)
    assert row["desk"] == "oil" and row["coin"] == "xyz:BRENTOIL" and row["action"] == "NONE"
    assert row["mid"] == pytest.approx(92.43) and row["pays_r"] == pytest.approx(1.52)
    assert row["blocked_other"] is False


def test_parse_tick_line_flags_chase_and_blackout():
    assert parse_tick_line(CHASE_LINE)["blocked_other"] is True
    assert parse_tick_line(BLACKOUT_LINE)["blocked_other"] is True


def test_parse_tick_line_none_for_a_non_desk_tick_line():
    assert parse_tick_line(OLD_HEADER_LINE) is None
    assert parse_tick_line("") is None
    assert parse_tick_line("   \n") is None


def test_parse_tick_line_hold_action_has_no_pays_r_but_still_parses():
    row = parse_tick_line(HOLD_LINE)
    assert row is not None and row["action"] == "HOLD" and row["pays_r"] is None


def test_would_enter_at_needs_r_and_no_other_blocker():
    pure = parse_tick_line(PURE_R_LINE)
    chase = parse_tick_line(CHASE_LINE)
    assert would_enter_at(pure, 1.5) is True
    assert would_enter_at(pure, 1.6) is False       # 1.52 < 1.6
    assert would_enter_at(chase, 1.5) is False      # R is 1.86 but chase blocks regardless
    assert would_enter_at(parse_tick_line(BLACKOUT_LINE), 0.1) is False


def test_qualifying_ticks_matches_the_real_log_count():
    rows = [parse_tick_line(l) for l in [CHASE_LINE, PURE_R_LINE, *CLUSTER_LINES, BLACKOUT_LINE]]
    q = qualifying_ticks(rows, 1.5)
    assert len(q) == 4   # the pure-R tick plus all three cluster ticks; chase and blackout excluded
    assert qualifying_ticks(rows, 2.0) == []   # nothing clears the live gate (as the real desk found)


def test_group_episodes_folds_the_15_minute_cluster_and_splits_the_gap():
    rows = [parse_tick_line(l) for l in [PURE_R_LINE, *CLUSTER_LINES]]
    eps = group_episodes(rows, max_gap_ms=20 * 60 * 1000)
    assert len(eps) == 2
    assert len(eps[0]) == 1 and len(eps[1]) == 3
    assert eps[0][0]["mid"] == pytest.approx(92.43)


def test_group_episodes_pure_and_order_independent():
    rows = [parse_tick_line(l) for l in reversed(CLUSTER_LINES)]   # out of order on purpose
    eps = group_episodes(rows)
    assert len(eps) == 1 and len(eps[0]) == 3
    assert eps[0][0]["mid"] == pytest.approx(91.8995)   # still sorted oldest-first inside the episode


def test_match_episode_trade_picks_the_closest_within_tolerance():
    ep = [parse_tick_line(PURE_R_LINE)]      # ts 02:40Z
    t0 = ep[0]["ts_ms"]
    near = _trade(entry_time=t0 + 20 * 60 * 1000)   # 03:00Z-ish fill, 20 min later
    far = _trade(entry_time=t0 + 50 * 60 * 1000)    # outside the 30-min default tolerance
    assert match_episode_trade(ep, [far, near]) is near
    assert match_episode_trade(ep, [far]) is None
    assert match_episode_trade([], [near]) is None


def test_replay_rows_reports_matched_and_unmatched_episodes():
    rows = [parse_tick_line(l) for l in [PURE_R_LINE, *CLUSTER_LINES]]
    t0 = rows[0]["ts_ms"]
    trade = _trade(entry_time=t0 + 20 * 60 * 1000, net=40.23, r=1.79)
    trade.exit_reason = "target"
    out = replay_rows(rows, [trade])
    assert len(out) == 2
    assert out[0]["matched_trade"] is True and out[0]["net_usd"] == pytest.approx(40.23)
    assert out[1]["matched_trade"] is False and "side" not in out[1]


def test_load_tick_log_rows_keeps_the_trailing_window_anchored_to_the_file(tmp_path):
    p = tmp_path / "progress.log"
    old = PURE_R_LINE.replace("2026-11-16T02:40Z", "2026-10-01T00:00Z")
    p.write_text(old + "\n" + PURE_R_LINE + "\n" + "\n".join(CLUSTER_LINES) + "\n")
    rows = load_tick_log_rows(str(p), days=7.0)
    assert len(rows) == 4   # the 10-01 row falls outside the 7-day window anchored to the file's own last ts
    assert all(r["ts_ms"] >= rows[0]["ts_ms"] for r in rows)


def test_load_tick_log_rows_missing_file_is_empty():
    assert load_tick_log_rows("/nonexistent/path/progress.log") == []
