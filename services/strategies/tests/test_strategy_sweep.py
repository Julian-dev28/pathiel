"""Gate tests for the pure parts of services/strategies/evals/strategy_sweep.py:
the per-day / per-week math, the stage filters and the grid builders. The
sweep itself hits the candle cache and is a periodic eval."""
from __future__ import annotations

import pytest

from services.strategies.backtest.desk_tick_sim import Run, Trade, live_params
from services.strategies.evals import strategy_sweep as S

DAY = 86_400_000


def _run(curve, trades=()):
    r = Run()
    r.equity_curve = list(curve)
    r.final_equity = curve[-1][1]
    r.trades = list(trades)
    return r


def test_daily_marks_keep_the_last_mark_of_each_utc_day_from_the_start():
    curve = [(0, 64.0), (DAY // 2, 70.0), (DAY, 72.0), (DAY + 5, 60.0), (2 * DAY + 1, 80.0)]
    assert S.daily_marks(curve, DAY) == [(1, 60.0), (2, 80.0)]


def test_period_returns_chain_from_the_start_and_count_a_partial_week():
    marks = [(d, 64.0 * 1.1 ** (d + 1)) for d in range(9)]          # +10% a day for 9 days
    days = S.period_returns(marks, 64.0, 1)
    assert days == pytest.approx([0.1] * 9)
    weeks = S.period_returns(marks, 64.0, 7)
    assert weeks == pytest.approx([1.1 ** 7 - 1, 1.1 ** 2 - 1])     # a full week, then 2 days
    assert S.period_returns([(0, 0.0), (1, 0.0)], 64.0, 1) == [-1.0, 0.0]   # gone stays gone


def test_per_day_is_the_compounding_rate_and_per_week_follows_from_it():
    curve = [(i * DAY, 64.0 * 1.05 ** i) for i in range(11)]       # +5%/day for 10 days
    trades = [Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=1.2,
                    net_pnl_usd=5.0, r_multiple=1.0, exit_reason="target")]
    out = S.per_day(_run(curve, trades), 0, 10 * DAY)
    assert out["daily_pct"] == pytest.approx(5.0, abs=1e-3)
    assert out["weekly_pct"] == pytest.approx((1.05 ** 7 - 1) * 100, abs=0.01)
    assert out["usd_per_day"] == pytest.approx((64 * 1.05 ** 10 - 64) / 10, abs=0.01)
    # the start day itself is flat (its only mark is the start), so 10 of 11 days are green
    assert out["green_days_pct"] == pytest.approx(90.9) and out["trades"] == 1 and out["exits"] == {"target": 1}


def test_a_wiped_account_reads_minus_100_percent_a_day():
    out = S.per_day(_run([(0, 64.0), (DAY, 0.0)]), 0, DAY)
    assert out["daily_pct"] == -100.0


def _row(j, c30, c50):
    return {"journaled": {"daily_pct": j}, "control30": {"daily_pct": c30}, "control50": {"daily_pct": c50}}


def test_a_change_goes_to_stage_two_on_two_of_three_and_ranks_only_if_both_controls_beat_live():
    live = _row(11.2, 1.0, -4.9)
    assert S.beats(_row(10.0, 4.8, -2.9), live)          # worse on theses, better on both controls
    assert not S.beats(_row(12.4, 0.4, -5.0), live)      # better on theses only
    assert S.robust(_row(5.0, 1.1, -4.0), live)
    assert not S.robust(_row(20.0, 1.1, -5.0), live)


def test_subsets_name_every_combination_of_a_winners_changes():
    subs = S.subsets({"min_r": 2.0, "entry_mode": "market"})
    assert subs == {"live": {}, "entry_mode": {"entry_mode": "market"}, "min_r": {"min_r": 2.0},
                    "entry_mode+min_r": {"entry_mode": "market", "min_r": 2.0}}


def test_the_stage_two_grid_takes_one_value_or_none_per_factor_and_has_a_cap():
    g = S.stage2_grid({"a": [{"min_r": 2.0}], "b": [{"stop_buffer": 1.0}, {"stop_buffer": 1.25}]})
    assert len(g) == 2 * 3 and {} in g and {"min_r": 2.0, "stop_buffer": 1.25} in g
    with pytest.raises(ValueError):
        S.stage2_grid({f"f{i}": [{"x": 1}, {"x": 2}, {"x": 3}] for i in range(8)}, cap=100)


def test_the_sweep_starts_from_the_live_rules_and_every_stage_one_change_is_a_real_param():
    base = S.base_params()
    live = live_params()
    assert (base.stop_buffer, base.target_offset_frac, base.target_mode, base.leverage) == \
        (live.stop_buffer, live.target_offset_frac, live.target_mode, live.leverage)
    for vals in S.STAGE1.values():
        for ov in vals:
            S.params_with(ov)                             # raises on an unknown field
    assert S.params_with({"session_utc": [7, 21]}).session_utc == (7, 21)   # JSON lists come back as tuples
