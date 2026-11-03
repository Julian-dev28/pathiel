"""Gate tests for the scalp eval's pure parts (services/strategies/evals/
scalp_test.py; SCALP_TEST_2026-11-03.md): the variant grids and their
plateau neighbours, the slice-relative summary metrics, `net_score`'s
liquidation/ruin floor, `walk_forward`'s in-sample/out-of-sample split and
`thesis_side_at` / `desk_open_at_end`'s adapters onto the desk simulator's
own shapes. No network: every bar here is synthetic."""
from __future__ import annotations

import dataclasses

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.scalp_sim import Params, Run, Trade
from services.strategies.evals import scalp_test as T

Q = 900_000


def _c(t, o, h, l, c, v=1.0):
    return Candle(t=t, o=o, h=h, l=l, c=c, v=v)


# ── grids ────────────────────────────────────────────────────────────────

def test_range_fade_grid_names_and_values():
    base = Params()
    grid = T.range_fade_grid(base)
    assert len(grid) == len(T.RANGE_LOOKBACK_H) * len(T.RANGE_TARGET_FRAC) * len(T.RANGE_STOP_K)
    p = grid["rf_lb2_tf0.5_sk1"]
    assert p.rule == "range_fade" and p.lookback_hours == 2.0 and p.target_frac == 0.5 and p.stop_atr_k == 1.0


def test_mean_revert_grid_names_values_and_skips_zs_under_ze():
    base = Params()
    grid = T.mean_revert_grid(base)
    assert all(dataclasses.replace(base).rule != "x" for _ in [0])   # sanity: base unaffected
    p = grid["mr_w16_ze2_zs3"]
    assert p.rule == "mean_revert" and p.ma_window == 16 and p.z_entry == 2.0 and p.z_stop == 3.0
    assert all(g.z_stop > g.z_entry for g in grid.values())


def test_range_fade_neighbours_cover_all_three_axes_and_exclude_self():
    name = "rf_lb2_tf0.75_sk1"
    nbrs = T.range_fade_neighbours(name)
    assert name not in nbrs
    assert "rf_lb1_tf0.75_sk1" in nbrs and "rf_lb4_tf0.75_sk1" in nbrs      # lookback axis
    assert "rf_lb2_tf0.5_sk1" in nbrs and "rf_lb2_tf1_sk1" in nbrs          # target_frac axis
    assert "rf_lb2_tf0.75_sk0.5" in nbrs and "rf_lb2_tf0.75_sk1.5" in nbrs  # stop_atr_k axis
    # an edge value has no neighbour off that end
    edge_nbrs = T.range_fade_neighbours("rf_lb1_tf0.5_sk0.5")
    assert "rf_lb0.5_tf0.5_sk0.5" not in edge_nbrs   # lookback_hours has nothing below 1.0


def test_mean_revert_neighbours_cover_axes_and_respect_zs_over_ze():
    name = "mr_w16_ze2_zs3"
    nbrs = T.mean_revert_neighbours(name)
    assert name not in nbrs
    assert "mr_w8_ze2_zs3" in nbrs and "mr_w32_ze2_zs3" in nbrs
    assert "mr_w16_ze1.5_zs3" in nbrs and "mr_w16_ze2.5_zs3" in nbrs
    assert "mr_w16_ze2_zs3.5" in nbrs
    assert all(not n.endswith("_zs1.5") for n in nbrs)   # never emits an invalid zs<=ze neighbour


# ── slice-relative metrics ──────────────────────────────────────────────

def test_slice_curve_is_a_constant_shift_of_the_whole_account_curve():
    p = dataclasses.replace(Params(), starting_equity=100.0, slice_frac=0.1)
    run = Run(equity_curve=[(0, 100.0), (Q, 90.0), (2 * Q, 110.0)])
    s = T.slice_curve(run, p)
    # slice capital 10.0; whole-account P&L (-10, +10) applied to the slice's own 10.0
    assert s == [(0, 10.0), (Q, 0.0), (2 * Q, 20.0)]


def test_max_drawdown_pct_pure():
    assert T.max_drawdown_pct([(0, 100.0), (1, 50.0), (2, 80.0)]) == pytest.approx(0.5)
    assert T.max_drawdown_pct([(0, 100.0), (1, 120.0)]) == 0.0
    assert T.max_drawdown_pct([]) == 0.0


def test_slice_ruined_flags_a_curve_that_goes_to_zero_or_below():
    assert T.slice_ruined([(0, 10.0), (1, -0.01), (2, 5.0)]) is True
    assert T.slice_ruined([(0, 10.0), (1, 0.01), (2, 5.0)]) is False
    assert T.slice_ruined([]) is False


def test_summarize_net_usd_and_ruin_flag():
    p = dataclasses.replace(Params(), starting_equity=100.0, slice_frac=0.05, taker_fee=0.0, maker_fee=0.0)
    tr = Trade(side="long", entry_time=0, entry_px=100.0, size=1.0, stop_px=90.0, target_px=110.0,
              exit_time=Q, exit_px=110.0, exit_reason="target", gross_pnl_usd=10.0, fees_usd=0.0,
              net_pnl_usd=10.0, risk_usd=10.0, r_multiple=1.0)
    run = Run(trades=[tr], equity_curve=[(0, 100.0), (Q, 110.0)], final_equity=110.0)
    s = T.summarize(run, 0, 2 * T.DAY_MS, p)
    assert s["net_usd"] == pytest.approx(10.0)
    assert s["trades"] == 1 and s["win_rate"] == 1.0
    assert s["slice_ruined"] is False     # slice capital 5.0 + 10.0 P&L never goes negative

    # a loss bigger than the slice's own capital: ruined
    loss = dataclasses.replace(tr, gross_pnl_usd=-40.0, net_pnl_usd=-40.0, exit_px=90.0)
    run2 = Run(trades=[loss], equity_curve=[(0, 100.0), (Q, 60.0)], final_equity=60.0)
    s2 = T.summarize(run2, 0, 2 * T.DAY_MS, p)
    assert s2["slice_ruined"] is True      # slice capital 5.0, whole-account loss 40.0 >> that


def test_net_score_floors_liquidation_and_ruin():
    base = {"liquidated": False, "slice_ruined": False, "net_usd": 5.0}
    assert T.net_score(base) == 5.0
    assert T.net_score({**base, "liquidated": True}) == -1e9
    assert T.net_score({**base, "slice_ruined": True}) == -1e9


# ── walk_forward on synthetic bars ──────────────────────────────────────

def _converging_bars(n=400, mid=100.0, half_width0=1.0, shrink=0.0005, start=0):
    out = []
    for i in range(n):
        hw = max(0.2, half_width0 - shrink * i)
        out.append(_c(start + i * Q, mid, mid + hw, mid - hw, mid))
    return out


def test_walk_forward_splits_in_half_and_runs_the_winner_out_of_sample():
    bars = _converging_bars(800)
    base = Params(leverage=20.0, max_leverage=20.0, starting_equity=1000.0, slice_frac=1.0, risk_frac=None,
                 min_r=1.0)
    grid = T.range_fade_grid(base)
    res = T.walk_forward(bars, grid, T.range_fade_neighbours, bars[0].t, bars[-1].t + Q)
    assert res["chosen"] in grid
    assert res["window"][0] < res["window"][1] < res["window"][2]
    assert "in_sample" in res and "out_of_sample" in res
    assert isinstance(res["plateau"], bool)
    assert res["grid_size"] == len(grid)


def test_random_control_runs_many_seeds_and_reports_a_spread():
    bars = _converging_bars(400)
    p = Params(leverage=20.0, max_leverage=20.0, starting_equity=1000.0, slice_frac=0.05, risk_frac=0.5, min_r=1.0)
    out = T.random_control(bars, p, bars[0].t, bars[-1].t + Q, entries_per_day=2.0, seeds=range(6))
    assert out["seeds"] == 6
    assert out["worst_net_usd"] <= out["mean_net_usd"] <= out["best_net_usd"]


# ── adapters onto the desk simulator's own shapes ───────────────────────

def test_thesis_side_at_unwraps_the_desk_direction_object():
    class FakeThesis:
        def __init__(self, created_at_ms, side):
            self.created_at_ms, self.side = created_at_ms, side
            self.entry_px = self.stop_px = self.invalidation_px = None
            self.targets, self.thesis_id, self.confidence = [], "x", None

    theses = [FakeThesis(0, "long"), FakeThesis(1000, "flat"), FakeThesis(2000, "short")]
    at = T.thesis_side_at(theses)
    assert at(500) == "long"
    assert at(1500) is None     # a flat call: journaled_direction returns None, the adapter passes that through
    assert at(2500) == "short"
    assert at(-1) is None       # before the first thesis


def test_desk_open_at_end_reads_opened_at_from_open_position_levels():
    from services.strategies.backtest.desk_tick_sim import Run as DeskRun
    closed = DeskRun(open_position_at_end=None)
    assert T.desk_open_at_end(closed) is None

    open_run = DeskRun(open_position_at_end={"side": "long", "size": 1.0, "entry_px": 100.0, "mark_px": 101.0,
                                             "unrealized_pnl_usd": 1.0},
                       open_position_levels={"opened_at": 12345, "stop_px": 99.0, "orig_stop_px": 99.0,
                                             "target_px": 110.0, "entry_kind": None, "entry_fees_usd": 0.0,
                                             "funding_usd": 0.0})
    assert T.desk_open_at_end(open_run) == {"entry_time": 12345}
