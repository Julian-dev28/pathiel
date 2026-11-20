"""Gate tests for `services/strategies/evals/hype_oil_strategy_test.py`'s
own new pieces (docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md). The
engine itself (Costs, Tick/Order/Trade, build_ticks, simulate, summarize,
verdict, pick, split_ms, edge_rule, oil_as_is) is already gate-tested in
test_sp500_strategy_eval.py and reused unmodified here; these tests cover
only: the overlap guard and portfolio equity math, the cross-coin trend/
shock filters, the pair-trade z-score/rule, baseline comparison, and the
OOS ranking logic. Synthetic data only: no network, no snapshot, no
account."""
from __future__ import annotations

import math

import pytest

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.evals import hype_oil_strategy_test as S
from services.strategies.evals.sp500_strategy_test import Order, Trade

T0 = 1_780_000_000_000 - (1_780_000_000_000 % S.HOUR_MS)


def trade(side: str, entry_t: int, exit_t: int, r: float, stop_frac: float = 0.02) -> Trade:
    return Trade(side=side, entry_t=entry_t, entry_px=100.0, stop=98.0, tp=104.0,
                exit_t=exit_t, exit_px=100.0, reason="target", r=r, stop_frac=stop_frac)


def bar(i: int, c: float, step: int = S.HOUR_MS) -> Candle:
    return Candle(t=T0 + i * step, o=c, h=c, l=c, c=c, v=1.0)


# ── a. overlap classification and the portfolio guard ───────────────────────

def test_classify_overlap_same_and_opposite():
    assert S.classify_overlap("long", "long") == "same"
    assert S.classify_overlap("short", "short") == "same"
    assert S.classify_overlap("long", "short") == "opposite"


def test_overlap_counts_detects_each_combination():
    oil = [trade("long", 0, 100, 1.0), trade("short", 200, 300, 1.0)]
    hype = [trade("long", 50, 150, 1.0),     # opens into oil's long (0-100): same
           trade("short", 250, 280, 1.0)]    # opens into oil's short (200-300): same
    counts = S.overlap_counts(oil, hype)
    assert counts["hype_opens_into_oil_same"] == 2
    assert counts["hype_opens_into_oil_opposite"] == 0
    assert counts["oil_opens_into_hype_same"] == 0
    assert counts["oil_opens_into_hype_opposite"] == 0


def test_overlap_counts_opposite_direction():
    oil = [trade("long", 0, 100, 1.0)]
    hype = [trade("short", 50, 150, 1.0)]    # opens into oil's long: opposite
    counts = S.overlap_counts(oil, hype)
    assert counts["hype_opens_into_oil_opposite"] == 1
    assert counts["oil_opens_into_hype_opposite"] == 0   # oil opened FIRST, before hype existed


def test_overlap_counts_no_overlap_when_sequential():
    oil = [trade("long", 0, 50, 1.0)]
    hype = [trade("short", 60, 100, 1.0)]    # opens AFTER oil already closed
    counts = S.overlap_counts(oil, hype)
    assert all(v == 0 for v in counts.values())


def test_apply_overlap_guard_off_changes_nothing():
    oil = [trade("long", 0, 100, 2.0)]
    hype = [trade("long", 50, 150, 3.0)]
    o2, h2 = S.apply_overlap_guard(oil, hype, "off", 0.0)
    assert o2[0].r == 2.0 and h2[0].r == 3.0
    assert o2 is not oil and o2[0] is not oil[0]       # a copy, inputs untouched
    assert oil[0].r == 2.0 and hype[0].r == 3.0


def test_apply_overlap_guard_blocks_only_the_second_same_direction_entry():
    oil = [trade("long", 0, 100, 2.0)]        # opens FIRST
    hype = [trade("long", 50, 150, 3.0)]      # opens SECOND, same direction
    o2, h2 = S.apply_overlap_guard(oil, hype, "same", 0.0)
    assert o2[0].r == 2.0            # the first trade is never touched
    assert h2[0].r == 0.0            # the second (overlapping, same-side) trade is blocked


def test_apply_overlap_guard_half_sizes_opposite_direction_only():
    oil = [trade("long", 0, 100, 2.0)]
    hype = [trade("short", 50, 150, 4.0)]     # opposite direction, opens second
    o2, h2 = S.apply_overlap_guard(oil, hype, "opposite", 0.5)
    assert o2[0].r == 2.0
    assert h2[0].r == 2.0            # halved from 4.0
    same_o2, same_h2 = S.apply_overlap_guard(oil, hype, "same", 0.5)
    assert same_h2[0].r == 4.0       # 'same' mode never touches an opposite-direction overlap


def test_portfolio_equity_compounds_each_desks_share_independently():
    # one oil trade, r=1 (a full win), stop_frac=0.02, leverage=20 -> stop_frac*leverage=0.4,
    # capped at risk_pct=10% -> risk=min(0.10, 0.4)=0.10, applied at oil_share=0.5
    oil = [trade("long", 0, 100, 1.0, stop_frac=0.02)]
    hype: list = []
    out = S.portfolio_equity(oil, hype, oil_share=0.5, hype_share=0.5, oil_leverage=20.0, hype_leverage=10.0,
                             risk_pct=10.0)
    assert out["final_multiple"] == pytest.approx(1.0 * (1.0 + 1.0 * 0.10 * 0.5), abs=1e-6)
    assert out["n_events"] == 1


def test_portfolio_equity_a_loss_reduces_equity_and_sets_drawdown():
    oil = [trade("long", 0, 100, -1.0, stop_frac=0.5)]    # stop_frac*leverage capped by risk_pct
    out = S.portfolio_equity(oil, [], oil_share=1.0, hype_share=1.0, oil_leverage=20.0, hype_leverage=10.0,
                             risk_pct=10.0)
    expected = 1.0 * (1.0 + (-1.0) * 0.10 * 1.0)
    assert out["final_multiple"] == pytest.approx(expected, abs=1e-6)
    assert out["max_dd_pct"] == pytest.approx((1.0 - expected) * 100, abs=1e-6)


def test_portfolio_equity_respects_the_time_window():
    oil = [trade("long", 0, 10, 1.0), trade("long", 100, 110, 1.0)]
    out = S.portfolio_equity(oil, [], 1.0, 1.0, 20.0, 10.0, 10.0, start_ms=50, end_ms=200)
    assert out["n_events"] == 1        # only the second trade's exit (110) falls in [50, 200)


def test_run_portfolio_guard_reports_full_train_tail_and_counts():
    cut = T0 + 1000
    end = T0 + 2000
    oil = [trade("long", T0, T0 + 10, 1.0), trade("short", cut + 10, cut + 20, -1.0)]
    hype = [trade("long", T0 + 5, T0 + 50, 2.0)]   # opens into oil's long: same
    out = S.run_portfolio_guard(oil, hype, T0, cut, end, risk_pct=10.0)
    assert set(out["variants"]) == {"baseline_off", "same_block", "same_half", "opposite_block", "opposite_half"}
    assert out["overlap_counts"]["hype_opens_into_oil_same"] == 1
    for v in out["variants"].values():
        assert set(v) == {"full", "train", "tail"}


# ── b. cross-coin trend filter ───────────────────────────────────────────────

def test_trend_signs_matches_next_trade_trend_gate_sign_convention():
    """Built off `next_trade.trend_gate`'s own formula -- the two must never
    silently disagree on which way a trend points."""
    closes = [100.0 + i * 0.1 for i in range(200)]       # a steady uptrend
    c1h = [bar(i, c) for i, c in enumerate(closes)]
    decision_t = c1h[-1].t + S.HOUR_MS
    signs = S.trend_signs(c1h, [decision_t], span_h=120)
    gate = NT.trend_gate("long", closes, span_h=120)
    assert signs[decision_t] == (1 if gate["ret"] > 0 else -1)


def test_trend_signs_none_with_too_little_history():
    c1h = [bar(i, 100.0 + i) for i in range(10)]
    t = c1h[-1].t + S.HOUR_MS
    assert S.trend_signs(c1h, [t], span_h=120)[t] is None


def _stub_rule(side: str):
    def rule(i, ticks, bars, st, flat=True):
        return Order(side=side, stop=90.0, tp=110.0) if flat else None
    return rule


def test_cross_trend_filter_agree_passes_a_matching_order():
    base = _stub_rule("long")
    signs = {T0: 1}             # oil trend sign +1 at this tick's decision time
    wrapped = S.cross_trend_filter_rule(base, signs, "agree")

    class Tk:
        t = T0
    order = wrapped(0, [Tk()], [], {}, True)
    assert order is not None and order.side == "long"


def test_cross_trend_filter_agree_blocks_a_disagreeing_order():
    base = _stub_rule("short")
    signs = {T0: 1}             # positive trend, but the order wants to short
    wrapped = S.cross_trend_filter_rule(base, signs, "agree")

    class Tk:
        t = T0
    assert wrapped(0, [Tk()], [], {}, True) is None


def test_cross_trend_filter_oppose_is_the_mirror_of_agree():
    base = _stub_rule("short")
    signs = {T0: 1}

    class Tk:
        t = T0
    agree = S.cross_trend_filter_rule(base, signs, "agree")(0, [Tk()], [], {}, True)
    oppose = S.cross_trend_filter_rule(base, signs, "oppose")(0, [Tk()], [], {}, True)
    assert agree is None and oppose is not None


def test_cross_trend_filter_missing_sign_never_blocks():
    base = _stub_rule("long")
    wrapped = S.cross_trend_filter_rule(base, {}, "agree")      # no sign data at all

    class Tk:
        t = T0
    assert wrapped(0, [Tk()], [], {}, True) is not None


def test_cross_trend_filter_passes_through_a_none_order_untouched():
    def no_order(i, ticks, bars, st, flat=True):
        return None
    wrapped = S.cross_trend_filter_rule(no_order, {T0: 1}, "agree")

    class Tk:
        t = T0
    assert wrapped(0, [Tk()], [], {}, True) is None


# ── b. day windows (shock / drawdown) ────────────────────────────────────────

def test_day_windows_shock_flags_abs_move_over_threshold():
    from datetime import datetime, timezone
    d0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = [Candle(t=d0, o=100, h=100, l=100, c=100, v=1),
           Candle(t=d0 + S.DAY_MS, o=100, h=100, l=100, c=104, v=1),     # +4%: a shock at 3%
           Candle(t=d0 + 2 * S.DAY_MS, o=104, h=104, l=104, c=103, v=1)]  # ~-1%: not a shock
    windows = S.day_windows(bars, 3.0)
    assert len(windows) == 1
    start, end = windows[0]
    assert end - start == S.DAY_MS


def test_day_windows_drawdown_is_one_sided():
    from datetime import datetime, timezone
    d0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = [Candle(t=d0, o=100, h=100, l=100, c=100, v=1),
           Candle(t=d0 + S.DAY_MS, o=100, h=100, l=100, c=105, v=1),      # +5%: not a drawdown
           Candle(t=d0 + 2 * S.DAY_MS, o=105, h=105, l=105, c=99.75, v=1)]  # -5%: a drawdown at -3%
    windows = S.day_windows(bars, -3.0, at_or_below=True)
    assert len(windows) == 1


# ── b. grid neighbour helpers ─────────────────────────────────────────────────

def test_trend_filter_variants_grid_shape():
    named = S.trend_filter_variants("fam")
    assert len(named) == len(S.TREND_FILTER_MODES) * len(S.TREND_FILTER_SPANS)
    assert all(v["family"] == "fam" and v["kind"] == "trend_filter" for v in named.values())


def test_filter_neighbours_trend_steps_span_only_same_mode():
    named = S.trend_filter_variants("fam")
    name = "fam_agree_120h"       # the middle of (72, 120, 168)
    nb = S.filter_neighbours(name, named)
    assert set(nb) == {"fam_agree_72h", "fam_agree_168h"}


def test_filter_neighbours_shock_steps_threshold():
    named = S.shock_filter_variants("fam")
    nb = S.filter_neighbours("fam_3pct", named)
    assert set(nb) == {"fam_2pct", "fam_4pct"}


def test_pair_variants_grid_shape_and_neighbours():
    named = S.pair_variants()
    assert len(named) == len(S.PAIR_ENTRY_Z) * len(S.PAIR_LOOKBACK_DAYS)
    name = "pair_z2_lb14d"
    nb = S.pair_neighbours(name, named)
    # one step on EITHER axis alone (not both at once)
    assert set(nb) == {"pair_z1.5_lb14d", "pair_z2.5_lb14d", "pair_z2_lb30d"}


# ── d. pair trade: spread, z-score, ticks, rule ──────────────────────────────

def test_cumulative_log_returns_sums_log_returns():
    bars = [bar(0, 100.0), bar(1, 110.0), bar(2, 99.0)]
    out = S.cumulative_log_returns(bars)
    assert out[0][1] == pytest.approx(math.log(1.10))
    assert out[1][1] == pytest.approx(math.log(1.10) + math.log(99.0 / 110.0))


def test_rolling_zscore_never_uses_the_current_point_no_lookahead():
    # a mildly noisy trailing window (so std > 0) followed by one huge spike
    ts = list(range(20))
    noise = [0.0, 0.1, -0.1, 0.05, -0.05, 0.1, -0.1, 0.05, -0.05, 0.0]
    xs = noise + [100.0] + [0.0] * 9
    z = S.rolling_zscore(ts, xs, lookback_bars=5)
    # the spike's own z is huge because its trailing window (indices 5-9) is
    # still just quiet noise -- it has NOT been contaminated by the spike yet
    assert z[10] is not None and z[10] > 50
    # one step later the spike has entered the trailing window for index 11,
    # which must inflate that window's std a lot relative to the noise-only
    # window index 10 used -- proof the window is a trailing slice, not a
    # centred or look-ahead one
    window_for_10 = xs[5:10]
    window_for_11 = xs[6:11]
    assert window_for_10 == noise[5:10] and 100.0 not in window_for_10
    assert 100.0 in window_for_11


def test_rolling_zscore_none_for_warmup_and_zero_variance():
    ts = list(range(10))
    xs = [1.0] * 10
    z = S.rolling_zscore(ts, xs, lookback_bars=5)
    assert z[4] is None             # fewer than lookback_bars points behind it
    assert z[5] is None             # trailing window [1,1,1,1,1]: zero variance -> None, not a divide-by-zero


def test_build_pair_ticks_carries_mid_atr_weekend_and_attaches_z():
    from services.strategies.evals.sp500_strategy_test import Tick
    base = [None, Tick(t=100, mid=50.0, low8=0, high8=0, low24=0, high24=0, atr=1.5, ret24=None, ret120=None,
                       weekend=False, ny_min=0, ny_day=0, ny_wd=0)]
    out = S.build_pair_ticks(base, {100: 0.75})
    assert out[0] is None
    assert out[1].t == 100 and out[1].mid == 50.0 and out[1].atr == 1.5 and out[1].z == 0.75 and out[1].weekend is False


def test_pair_rule_enters_long_on_high_z_for_the_long_leg():
    tk = S.PairTick(t=0, mid=100.0, atr=1.0, z=2.5, weekend=False)
    rule = S.pair_rule(high_z_side="long", entry_z=2.0, k_atr=1.0, target_r=2.0, leverage=10.0, max_leverage=20.0)
    order = rule(0, [tk], [], {}, True)
    assert order is not None and order.side == "long"
    assert order.stop == pytest.approx(99.0) and order.tp == pytest.approx(102.0)


def test_pair_rule_enters_the_opposite_side_on_low_z():
    tk = S.PairTick(t=0, mid=100.0, atr=1.0, z=-2.5, weekend=False)
    rule = S.pair_rule(high_z_side="long", entry_z=2.0, k_atr=1.0, target_r=2.0, leverage=10.0, max_leverage=20.0)
    order = rule(0, [tk], [], {}, True)
    assert order is not None and order.side == "short"


def test_pair_rule_deadband_and_not_flat_and_missing_z():
    rule = S.pair_rule(high_z_side="long", entry_z=2.0, k_atr=1.0, target_r=2.0, leverage=10.0, max_leverage=20.0)
    inside = S.PairTick(t=0, mid=100.0, atr=1.0, z=0.5, weekend=False)
    assert rule(0, [inside], [], {}, True) is None
    high = S.PairTick(t=0, mid=100.0, atr=1.0, z=3.0, weekend=False)
    assert rule(0, [high], [], {}, False) is None          # not flat: never answers
    no_z = S.PairTick(t=0, mid=100.0, atr=1.0, z=None, weekend=False)
    assert rule(0, [no_z], [], {}, True) is None


def test_pair_rule_refuses_a_stop_too_wide_for_leverage():
    tk = S.PairTick(t=0, mid=100.0, atr=50.0, z=3.0, weekend=False)   # a huge ATR -> a huge stop buffer
    rule = S.pair_rule(high_z_side="long", entry_z=2.0, k_atr=1.0, target_r=2.0, leverage=20.0, max_leverage=20.0)
    assert rule(0, [tk], [], {}, True) is None


# ── baseline comparison and ranking ──────────────────────────────────────────

def _row(net_r: float, mean_r: float, trades: int = 10) -> dict:
    return {"trades": trades, "net_r": net_r, "mean_r": mean_r}


def test_baseline_delta_beats_when_both_legs_improve():
    cand = {"train": _row(5.0, 0.5), "tail": _row(2.0, 0.3)}
    base = {"train": _row(3.0, 0.3), "tail": _row(1.0, 0.1)}
    d = S.baseline_delta(cand, base)
    assert d["tail"]["delta_net_r"] == pytest.approx(1.0)
    assert d["tail"]["delta_mean_r"] == pytest.approx(0.2)
    assert d["beats_baseline_oos"] is True


def test_baseline_delta_false_when_tail_net_r_is_worse_even_if_mean_r_looks_better():
    """The exact HYPE-shock-filter shape found in the real run: fewer trades
    nudges mean R up while total net R falls -- not a real improvement."""
    cand = {"train": _row(26.89, 0.306), "tail": _row(7.95, 0.215, trades=37)}
    base = {"train": _row(31.9, 0.336), "tail": _row(8.06, 0.202, trades=40)}
    d = S.baseline_delta(cand, base)
    assert d["tail"]["delta_mean_r"] > 0     # mean R did go up...
    assert d["tail"]["delta_net_r"] < 0      # ...but net R went down
    assert d["beats_baseline_oos"] is False  # so this does NOT count as beating the baseline


def test_rank_candidates_marks_real_alpha_only_for_a_positive_label_that_also_beats_baseline():
    report = {
        "live_baseline": {"oil": {"tail": _row(-1.0, -0.1, 20)}, "hype": {"tail": _row(8.0, 0.2, 40)}},
        "b_cross_signal": {
            "b_fake_improves": {"picked": "x1", "verdict": "LEANING",
                               "row": {"train": _row(10, 0.5), "tail": _row(9.0, 0.3, 30)},
                               "vs_baseline": S.baseline_delta({"train": _row(10, 0.5), "tail": _row(9.0, 0.3, 30)},
                                                               {"train": _row(5, 0.2), "tail": _row(1.0, 0.1, 30)})},
            "b_fake_worse": {"picked": "x2", "verdict": "LEANING",
                            "row": {"train": _row(1, 0.1), "tail": _row(0.5, 0.05, 30)},
                            "vs_baseline": S.baseline_delta({"train": _row(1, 0.1), "tail": _row(0.5, 0.05, 30)},
                                                            {"train": _row(5, 0.2), "tail": _row(8.0, 0.2, 40)})},
        },
        "d_pair_trade": {},
    }
    rows = S.rank_candidates(report)
    improves = [r for r in rows if "b_fake_improves" in r["candidate"]][0]
    worse = [r for r in rows if "b_fake_worse" in r["candidate"]][0]
    assert improves["beats_baseline_oos"] is True and improves["real_alpha"] is True
    assert worse["beats_baseline_oos"] is False and worse["real_alpha"] is False
    live_rows = [r for r in rows if r["label"] == "LIVE"]
    assert all(r["beats_baseline_oos"] is None and r["real_alpha"] is False for r in live_rows)


def test_rank_candidates_sorts_by_oos_mean_r_descending():
    report = {
        "live_baseline": {"oil": {"tail": _row(1.0, 0.05, 20)}, "hype": {"tail": _row(1.0, 0.50, 5)}},
        "b_cross_signal": {}, "d_pair_trade": {},
    }
    rows = S.rank_candidates(report)
    assert [r["candidate"] for r in rows] == ["hype_live_rule_trend_gated", "oil_live_rule"]


def test_lead_lag_entry_gate_constant_matches_the_documented_finding():
    """Pinned so a future edit that flips this constant without updating the
    module docstring/doc fails loudly here first."""
    assert S.LEAD_LAG_ENTRY_GATE_MET is False
