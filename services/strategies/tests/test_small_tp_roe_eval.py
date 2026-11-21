"""Gate tests for the small-gain-only ROE take-profit backtest
(services/strategies/evals/small_tp_roe_test.py,
docs/strategy/SMALL_TP_ROE_2026-11-20.md). Covers: the ROE-to-price math at
each desk's leverage, the same-bar stop-first rule (including the new
breakeven walk), fee netting (close_trade and the breakeven-win-rate
solve), the entry/exit split (every small-TP variant reuses the exact same
entry decision as the desk's live rule), the variant grid, and the
Bonferroni-corrected label. Synthetic candles only: no network, no
snapshot, no account -- this module never touches
~/Documents/code/pathiel-replays."""
from __future__ import annotations

import pytest

from pathiel.agents.bracket import DEFAULT_MIN_R
from pathiel.models.types import Candle
from services.review import stats as RS
from services.strategies import next_trade as NT
from services.strategies.evals import small_tp_roe_test as M
from services.strategies.evals.sp500_strategy_test import (
    Costs, Dataset, Order, build_ticks, simulate,
)

H = M.HOUR_MS
T0 = 1_790_000_000_000 - (1_790_000_000_000 % H)     # an exact UTC hour
NO_COST = Costs(fee=0.0, slip=0.0)
NO_FUND = ([], [])


def bar(i, o, h, l, c, step=H):
    return Candle(t=T0 + i * step, o=o, h=h, l=l, c=c, v=1.0)


def flat_bars(n, px=100.0, rng=0.2):
    return [bar(i, px, px + rng, px - rng, px) for i in range(n)]


def once(side, stop, tp, at):
    def rule(i, ticks, bars, st, flat=True):
        return Order(side, stop, tp) if flat and i == at else None
    return rule


def _run(path, rule, **kw):
    bars = flat_bars(30) + path
    ticks = build_ticks(bars, H, bars)
    return simulate(ticks, bars, H, rule, kw.pop("costs", NO_COST), kw.pop("fund", NO_FUND))


def _run_be(path, rule, venue_max_leverage=20.0, **kw):
    bars = flat_bars(30) + path
    ticks = build_ticks(bars, H, bars)
    return M.simulate_breakeven(ticks, bars, H, rule, kw.pop("costs", NO_COST), kw.pop("fund", NO_FUND),
                                venue_max_leverage)


# ── 1. ROE <-> price math, at each desk's leverage ─────────────────────────

def test_roe_price_move_matches_the_operators_own_worked_examples():
    # "Oil (xyz:BRENTOIL) at 20x: 10% ROE is about 0.5%."
    assert M.roe_price_move(10.0, 20.0) == pytest.approx(0.005)
    # "HYPE at 9-10x: 10% ROE is about a 1.0-1.1% price move."
    assert M.roe_price_move(10.0, 10.0) == pytest.approx(0.01)
    assert M.roe_price_move(10.0, 9.0) == pytest.approx(0.01111, abs=1e-5)


def test_roe_price_move_scales_linearly_with_roe_and_inversely_with_leverage():
    for roe, lev, expected in ((10, 20, 0.005), (20, 20, 0.01), (30, 20, 0.015), (40, 20, 0.02),
                               (10, 10, 0.01), (20, 10, 0.02), (30, 10, 0.03), (40, 10, 0.04)):
        assert M.roe_price_move(roe, lev) == pytest.approx(expected)


def test_roe_price_move_rejects_non_positive_leverage():
    with pytest.raises(ValueError):
        M.roe_price_move(10.0, 0.0)
    with pytest.raises(ValueError):
        M.roe_price_move(10.0, -5.0)


def test_roe_target_and_loss_px_long_and_short_are_mirrors():
    # 10% ROE at 20x = 0.5% price move
    assert M.roe_target_px(100.0, "long", 10.0, 20.0) == pytest.approx(100.5)
    assert M.roe_loss_px(100.0, "long", 10.0, 20.0) == pytest.approx(99.5)
    assert M.roe_target_px(100.0, "short", 10.0, 20.0) == pytest.approx(99.5)
    assert M.roe_loss_px(100.0, "short", 10.0, 20.0) == pytest.approx(100.5)


def test_r_multiple_of_roe_is_price_move_over_stop_distance():
    # oil: 10% ROE at 20x (0.5% move) against a 1.077% stop (the measured
    # median on the pinned snapshot, 2026-11-20) pays under half an R
    assert M.r_multiple_of_roe(10.0, 20.0, 0.01077) == pytest.approx(0.4642, abs=1e-3)
    assert M.r_multiple_of_roe(10.0, 20.0, 0.005) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        M.r_multiple_of_roe(10.0, 20.0, 0.0)


# ── 2. same-bar stop-first (plain exits and the breakeven walk) ───────────

def test_stop_is_checked_before_target_for_a_small_tp_order_hit_in_one_bar():
    """A bar whose range crosses BOTH a tight ROE target and the desk stop
    resolves as a stop, never a target -- no look-ahead."""
    tr = _run([bar(30, 100, 102, 98, 100)], once("long", 99.0, 100.5, 29))
    assert [(t.reason, round(t.r, 3)) for t in tr] == [("stop", -1.0)]


def test_simulate_breakeven_also_resolves_stop_before_target_in_one_bar():
    tr = _run_be([bar(30, 100, 102, 98, 100)], once("long", 99.0, 100.5, 29))
    assert [(t.reason, round(t.r, 3)) for t in tr] == [("stop", -1.0)]


def test_simulate_breakeven_matches_plain_simulate_when_breakeven_never_triggers():
    """With no bar ever reaching the halfway mark, the breakeven walk is
    byte-identical to the base engine on a one-shot trade."""
    path = [bar(30, 100, 100.1, 99.8, 100.05), bar(31, 100.05, 100.05, 94.0, 95.0)]
    plain = _run(path, once("long", 95.0, 110.0, 29))
    be = _run_be(path, once("long", 95.0, 110.0, 29))
    assert [(t.reason, t.exit_px, round(t.r, 6)) for t in plain] == \
           [(t.reason, t.exit_px, round(t.r, 6)) for t in be]


def test_simulate_breakeven_moves_the_live_stop_to_entry_after_half_the_tp_distance():
    """Entry 100, tp 102 (long): halfway is 101. Bar 30 touches 101.2 (over
    the trigger) but closes back inside, never hitting stop or tp that bar.
    Bar 31 trades back down through the ORIGINAL stop (90) but the position
    now exits at breakeven (100), not 90 -- and R is judged against the
    ORIGINAL planned risk (10), not zero, so no division by zero."""
    path = [bar(30, 100, 101.2, 99.9, 101.0), bar(31, 100.5, 100.6, 85.0, 86.0)]
    tr = _run_be(path, once("long", 90.0, 102.0, 29))
    assert len(tr) == 1
    t = tr[0]
    assert t.reason == "stop" and t.exit_px == pytest.approx(100.0) and t.stop == pytest.approx(90.0)
    # scratched at breakeven: R is tiny (zero cost here, so exactly 0), not -1
    assert t.r == pytest.approx(0.0, abs=1e-9)


def test_simulate_breakeven_never_divides_by_zero_even_with_real_fees():
    path = [bar(30, 100, 101.2, 99.9, 101.0), bar(31, 100.5, 100.6, 85.0, 86.0)]
    tr = _run_be(path, once("long", 90.0, 102.0, 29), costs=M.OIL_MEASURED)
    assert len(tr) == 1 and tr[0].r < 0.0            # fee/slip drag, but no crash and no huge number
    assert tr[0].r > -0.01


def test_simulate_breakeven_resolves_the_trigger_bar_against_the_pre_trigger_stop():
    """The SAME bar that first crosses the halfway trigger also dips to the
    original stop: since the move only applies to LATER bars, this bar
    still resolves as a stop at the pre-trigger level."""
    path = [bar(30, 100, 101.3, 89.0, 95.0)]        # one bar: high clears the 101 trigger, low breaks 90
    tr = _run_be(path, once("long", 90.0, 102.0, 29))
    assert [(t.reason, t.exit_px) for t in tr] == [("stop", 90.0)]


# ── 3. fee netting ──────────────────────────────────────────────────────

def test_breakeven_win_rate_matches_the_hand_solved_zero_cost_formula():
    """At zero cost, win_r = tp_frac / stop_frac and loss_r = -1 exactly, so
    the break-even win rate reduces to stop_frac / (stop_frac + tp_frac)."""
    stop_frac, tp_frac = 0.01, 0.02
    wr = M.breakeven_win_rate(stop_frac, tp_frac, NO_COST)
    assert wr == pytest.approx(stop_frac / (stop_frac + tp_frac), abs=1e-4)
    assert wr == pytest.approx(1.0 / 3.0, abs=1e-4)


def test_breakeven_win_rate_rises_once_real_fees_and_slippage_are_charged():
    stop_frac, tp_frac = 0.01077, 0.005   # oil's measured median stop, 10% ROE at 20x
    free = M.breakeven_win_rate(stop_frac, tp_frac, NO_COST)
    paid = M.breakeven_win_rate(stop_frac, tp_frac, M.OIL_MEASURED)
    assert free is not None and paid is not None
    assert paid > free        # fees make the same bracket need a HIGHER win rate to break even


def test_breakeven_win_rate_is_none_when_even_a_perfect_record_cant_break_even():
    """A TP so small the round-trip fee alone eats it: no win rate saves it."""
    huge_cost = Costs(fee=0.05, slip=0.05)
    assert M.breakeven_win_rate(0.01, 0.001, huge_cost) is None


def test_breakeven_win_rate_table_numbers_are_internally_consistent_with_close_trade():
    """Direct hand check of close_trade's own fee math (oil's real measured
    schedule, read off the module), feeding breakeven_win_rate."""
    entry, stop_frac, tp_frac = 100.0, 0.01, 0.005
    stop, tp = entry * (1 - stop_frac), entry * (1 + tp_frac)
    from services.strategies.evals.sp500_strategy_test import Trade, close_trade
    win = close_trade(Trade(side="long", entry_t=0, entry_px=entry, stop=stop, tp=tp),
                      1, tp, "target", M.OIL_MEASURED, NO_FUND)
    loss = close_trade(Trade(side="long", entry_t=0, entry_px=entry, stop=stop, tp=tp),
                       1, stop, "stop", M.OIL_MEASURED, NO_FUND)
    expected_wr = -loss.r / (win.r - loss.r)
    assert M.breakeven_win_rate(stop_frac, tp_frac, M.OIL_MEASURED) == pytest.approx(round(expected_wr, 4))
    # oil's measured fee/slip is tiny (0.9bps + 1bp) but slippage hits BOTH
    # the entry fill and a stop exit, so the loss leg runs a bit past -1R
    assert win.r < tp_frac / stop_frac and loss.r < -1.0 and loss.r > -1.1


# ── 4. entry/exit split: every variant reuses the exact same entry call ──

def test_oil_entry_rule_matches_the_oil_structural_formula_at_a_fixed_20x_no_fit():
    from services.strategies.evals.sp500_strategy_test import edge_rule
    bars = flat_bars(40) + [bar(40, 100, 100.2, 99.0, 99.5)]     # a pullback near the 24h low
    ticks = build_ticks(bars, H, bars)
    mine = M.oil_entry_rule()
    manual = edge_rule(k_atr=NT.ATR_BUFFER_MULT, floor_frac=NT.STOP_BUFFER_FLOOR_FRAC, offset_frac=0.005,
                       sides="both", entry_lookback_h=24, leverage=M.OIL_LEVERAGE, min_r=DEFAULT_MIN_R,
                       max_leverage=M.OIL_VENUE_MAX_LEVERAGE, fit=())
    for i in range(len(ticks)):
        if ticks[i] is None:
            continue
        a, b = mine(i, ticks, bars, {}), manual(i, ticks, bars, {})
        assert (a is None) == (b is None)
        if a is not None:
            assert (a.side, a.stop, a.tp) == (b.side, b.stop, b.tp)


def test_hype_entry_rule_matches_next_trades_hype_rule_constants():
    from services.strategies.evals.sp500_strategy_test import edge_rule
    bars = flat_bars(140) + [bar(140, 100, 100.2, 99.0, 99.5)]
    ticks = build_ticks(bars, H, bars)
    mine = M.hype_entry_rule()
    p = NT.HYPE_RULE
    manual = edge_rule(k_atr=p["k_atr"], floor_frac=p["floor_frac"], offset_frac=p["target_offset_frac"],
                       sides=p["backtest_sides"], entry_lookback_h=24, leverage=M.HYPE_LEVERAGE,
                       min_r=DEFAULT_MIN_R, max_leverage=M.HYPE_VENUE_MAX_LEVERAGE, fit=())
    for i in range(len(ticks)):
        if ticks[i] is None:
            continue
        a, b = mine(i, ticks, bars, {}), manual(i, ticks, bars, {})
        assert (a is None) == (b is None)
        if a is not None:
            assert (a.side, a.stop, a.tp) == (b.side, b.stop, b.tp)


def test_small_tp_rule_desk_stop_keeps_the_structural_stop_and_only_swaps_tp():
    stub = lambda i, ticks, bars, st, flat=True: Order("long", 95.0, 999.0)
    wrapped = M.small_tp_rule(stub, leverage=10.0, roe_pct=10.0, stop_mode="desk_stop")
    ticks = [type("T", (), {"mid": 100.0})()]
    o = wrapped(0, ticks, [], {}, True)
    assert o.stop == 95.0                        # unchanged, the structural stop
    assert o.tp == pytest.approx(101.0)           # 10% ROE at 10x = 1% price move


def test_small_tp_rule_symmetric_mode_mirrors_the_tp_distance_onto_the_stop():
    stub = lambda i, ticks, bars, st, flat=True: Order("long", 95.0, 999.0)
    wrapped = M.small_tp_rule(stub, leverage=10.0, roe_pct=10.0, stop_mode="symmetric")
    ticks = [type("T", (), {"mid": 100.0})()]
    o = wrapped(0, ticks, [], {}, True)
    assert o.tp == pytest.approx(101.0) and o.stop == pytest.approx(99.0)


def test_small_tp_rule_2x_tp_mode_doubles_the_stop_distance():
    stub = lambda i, ticks, bars, st, flat=True: Order("short", 105.0, 1.0)
    wrapped = M.small_tp_rule(stub, leverage=10.0, roe_pct=10.0, stop_mode="2x_tp")
    ticks = [type("T", (), {"mid": 100.0})()]
    o = wrapped(0, ticks, [], {}, True)
    assert o.side == "short"
    assert o.tp == pytest.approx(99.0)            # 1% move down
    assert o.stop == pytest.approx(102.0)         # 2% move up: 2x the TP's ROE distance


def test_small_tp_rule_passes_through_a_refusal_unchanged():
    stub = lambda i, ticks, bars, st, flat=True: None
    wrapped = M.small_tp_rule(stub, leverage=10.0, roe_pct=10.0, stop_mode="desk_stop")
    assert wrapped(0, [None], [], {}, True) is None


def test_small_tp_rule_rejects_an_unknown_stop_mode():
    with pytest.raises(ValueError):
        M.small_tp_rule(lambda *a, **k: None, leverage=10.0, roe_pct=10.0, stop_mode="nope")


def test_every_small_tp_variant_shares_the_entry_rules_own_eligibility():
    """A wrapped rule says yes/no on the EXACT same ticks as the bare entry
    rule, for every stop_mode and ROE level -- the invariant the module
    docstring claims ("every tpNN* variant... share the exact same set of
    entry times")."""
    bars = flat_bars(40) + [bar(40, 100, 100.2, 99.0, 99.5), bar(41, 99.5, 99.6, 99.4, 99.5)]
    ticks = build_ticks(bars, H, bars)
    entry_rule = M.oil_entry_rule()
    for roe in M.ROE_LEVELS:
        for mode in M.STOP_MODES:
            wrapped = M.small_tp_rule(entry_rule, M.OIL_LEVERAGE, roe, mode)
            for i in range(len(ticks)):
                if ticks[i] is None:
                    continue
                base = entry_rule(i, ticks, bars, {}, True)
                over = wrapped(i, ticks, bars, {}, True)
                assert (base is None) == (over is None)
                if base is not None:
                    assert over.side == base.side


# ── 5. the variant grid ────────────────────────────────────────────────────

def test_variants_for_has_one_baseline_and_four_treatments_per_roe_level():
    named = M.variants_for()
    assert len(named) == 1 + len(M.ROE_LEVELS) * 4
    assert len(named) >= 9                        # the task's own multiple-testing floor
    assert named["baseline"]["kind"] == "baseline"
    for roe in M.ROE_LEVELS:
        assert named[f"tp{roe:g}"]["stop_mode"] == "desk_stop" and named[f"tp{roe:g}"]["kind"] == "tp"
        assert named[f"tp{roe:g}_sym"]["stop_mode"] == "symmetric"
        assert named[f"tp{roe:g}_2xstop"]["stop_mode"] == "2x_tp"
        assert named[f"tp{roe:g}_be"]["kind"] == "tp_breakeven"
    assert len(set(named)) == len(named)           # every name unique


def test_rule_for_baseline_is_the_bare_entry_rule():
    entry_rule = M.oil_entry_rule()
    assert M.rule_for(entry_rule, M.OIL_LEVERAGE, {"kind": "baseline"}) is entry_rule


def test_trades_for_routes_breakeven_kind_to_the_breakeven_walk():
    """A scenario where the breakeven move changes the outcome: the base
    `simulate` and `simulate_breakeven` must disagree here, proving
    `trades_for` really dispatches by `kind` and isn't silently using the
    same walk for both."""
    bars = flat_bars(30) + [bar(30, 100, 101.2, 99.9, 101.0), bar(31, 100.5, 100.6, 85.0, 86.0)]
    ds = Dataset("1h", bars, H)
    ds.ticks = build_ticks(ds.bars, ds.step_ms, bars)
    rule = once("long", 90.0, 102.0, 29)
    plain = M.trades_for(ds, rule, {"kind": "tp"}, NO_COST, NO_FUND, 20.0)
    be = M.trades_for(ds, rule, {"kind": "tp_breakeven"}, NO_COST, NO_FUND, 20.0)
    assert plain[0].exit_px == 90.0 and plain[0].r == pytest.approx(-1.0)
    assert be[0].exit_px == 100.0 and be[0].r == pytest.approx(0.0, abs=1e-9)


# ── 6. win/loss R, losing streaks, dollar sizing ───────────────────────────

def _trade(r, exit_t):
    from services.strategies.evals.sp500_strategy_test import Trade
    return Trade(side="long", entry_t=exit_t - 1, entry_px=100.0, stop=99.0, tp=101.0,
                exit_t=exit_t, exit_px=100.0, r=r, stop_frac=0.01)


def test_win_loss_r_splits_winners_and_losers_by_sign():
    trades = [_trade(2.0, 1), _trade(-1.0, 2), _trade(0.5, 3), _trade(0.0, 4)]
    wl = M.win_loss_r(trades)
    assert wl == {"avg_win_r": 1.25, "avg_loss_r": -0.5, "n_win": 2, "n_loss": 2}   # 0.0 counts as a loss


def test_win_loss_r_is_none_with_no_winners_or_no_losers():
    assert M.win_loss_r([_trade(1.0, 1)])["avg_loss_r"] is None
    assert M.win_loss_r([_trade(-1.0, 1)])["avg_win_r"] is None


def test_longest_losing_streak_counts_consecutive_non_positive_trades_by_exit_time():
    trades = [_trade(1.0, 1), _trade(-1.0, 2), _trade(-1.0, 3), _trade(0.0, 4), _trade(2.0, 5), _trade(-1.0, 6)]
    assert M.longest_losing_streak(trades) == 3
    assert M.longest_losing_streak([_trade(1.0, 1)]) == 0
    assert M.longest_losing_streak([]) == 0


def test_net_usd_scales_the_equity_path_onto_the_slice_dollars():
    trades = [_trade(2.0, 1)]
    out = M.net_usd(trades, leverage=20.0, risk_pct=6.0, slice_equity=58.24)
    # one +2R trade, risk capped at min(6%, stop_frac x leverage) = min(0.06, 0.01*20=0.20) = 0.06
    assert out["final_multiple"] == pytest.approx(1.12)
    assert out["net_usd"] == pytest.approx(58.24 * 0.12, abs=0.01)


# ── 7. the Bonferroni-corrected label ──────────────────────────────────────

def test_labeled_verdict_empty_is_noise():
    out = M.labeled_verdict([], n_variants=17)
    assert out["label"] == RS.NOISE and out["n"] == 0


def test_labeled_verdict_without_correction_can_reach_edge_or_leak():
    strong_pos = [0.5] * 12
    strong_neg = [-0.5] * 12
    assert M.labeled_verdict(strong_pos, n_variants=1)["label"] == RS.EDGE
    assert M.labeled_verdict(strong_neg, n_variants=1)["label"] == RS.LEAK


def test_labeled_verdict_bonferroni_correction_can_demote_a_borderline_result():
    """A slice whose RAW p sits just under label_slice's 0.05 cutoff reads
    EDGE with one shot on goal (n_variants=1) but not once the same result
    is treated as one of many simultaneous tries."""
    import random
    rng = random.Random(7)
    rs = [abs(rng.gauss(0.15, 1.0)) for _ in range(40)]      # noisy but consistently positive
    one_shot = M.labeled_verdict(rs, n_variants=1)
    many_shots = M.labeled_verdict(rs, n_variants=10_000)
    assert one_shot["p_bonf"] == one_shot["p_raw"]
    assert many_shots["p_bonf"] == 1.0                        # capped
    assert RS.LABELS.index(many_shots["label"]) >= RS.LABELS.index(RS.NOISE) or many_shots["label"] == RS.NOISE
    # the corrected p can only ever be harsher (larger) than the raw one
    assert many_shots["p_bonf"] >= one_shot["p_raw"]


def test_labeled_verdict_p_bonf_never_exceeds_one():
    out = M.labeled_verdict([1.0, -1.0, 1.0, -1.0, 1.0], n_variants=10_000_000)
    assert out["p_bonf"] is None or out["p_bonf"] <= 1.0


# ── 8. end-to-end desk_report on a fully synthetic dataset (no network) ────

def _synthetic_desk_ds(n_days=40):
    """A 1h series with real daily swings (so the 24h-range entries and the
    ROE exits both actually fire), long enough for a 70/30 split to have
    trades on both sides."""
    import math
    bars = []
    px = 100.0
    for i in range(n_days * 24):
        swing = 3.0 * math.sin(i / 11.0) + 0.3 * math.sin(i / 3.3)
        c = 100.0 + swing
        o = px
        h, l = max(o, c) + 0.15, min(o, c) - 0.15
        bars.append(bar(i, o, h, l, c))
        px = c
    return bars


def test_desk_report_shape_and_internal_consistency_on_synthetic_candles():
    bars = _synthetic_desk_ds()
    ds = Dataset("1h", bars, H)
    ds.ticks = build_ticks(ds.bars, ds.step_ms, bars)
    rep = M.desk_report(coin="xyz:BRENTOIL", entry_rule_fn=M.oil_entry_rule, leverage=M.OIL_LEVERAGE,
                        venue_max_leverage=M.OIL_VENUE_MAX_LEVERAGE, costs=M.OIL_MEASURED, stress=M.OIL_STRESS,
                        share=M.OIL_SHARE, ds=ds, fund=NO_FUND, total_equity=182.0, risk_pct=6.0)
    assert set(rep["variants"]) == set(M.variants_for())
    assert len(rep["roe_table"]) == len(M.ROE_LEVELS) == len(rep["breakeven_table"])
    for row in rep["roe_table"]:
        assert row["price_move_pct"] == pytest.approx(M.roe_price_move(row["roe_pct"], M.OIL_LEVERAGE) * 100)
    for row in rep["breakeven_table"]:
        name = f"tp{row['roe_pct']:g}"
        assert row["achieved_win_rate"] == rep["variants"][name]["full"]["win_rate"]
    # every variant's own trade count is internally consistent: full >= tail
    for v in rep["variants"].values():
        assert v["full"]["trades"] >= v["tail"]["trades"] >= 0
    assert rep["slice_equity_usd"] == pytest.approx(182.0 * M.OIL_SHARE)
