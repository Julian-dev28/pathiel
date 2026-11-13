"""Gate tests for the rule fusion sweep (services/strategies/evals/
rule_fusion_test.py, 2026-11-13): the grid, the merged overrides, the
neighbour and marginal bookkeeping, every guard on hand-built trade lists,
the funnel, the overlap labels, and that every axis level is a real
`Params` field set the simulator accepts (synthetic candles, no network).
"""
from __future__ import annotations

import pytest

from services.strategies.backtest.desk_tick_sim import Params, live_params
from services.strategies.evals import rule_fusion_test as E
from services.strategies.tests.test_stop_cap import ENTER_LONG, QUIET, _run

DAY = 86_400_000
WEEK = 7 * DAY

TWO = {"delay": E.AXES["delay"], "trend": E.AXES["trend"]}


# ── the grid ───────────────────────────────────────────────────────────────

def test_axes_hold_the_tested_value_and_a_neighbour_with_off_first():
    for name, ax in E.AXES.items():
        levels = list(ax["levels"])
        assert levels[0] == "off" and ax["levels"]["off"] == {}, name
        assert len(levels) >= 3, f"{name} needs its tested value plus a neighbour"
        assert ax["primary"] in levels[1:], name
        for lv, ov in ax["levels"].items():
            Params(**ov)                          # every override is a real Params field


def test_the_tested_values_are_the_ones_the_earlier_tests_ran():
    p = {n: E.AXES[n]["levels"][E.AXES[n]["primary"]] for n in E.AXES}
    assert p["delay"] == {"entry_delay_min": 120.0}
    assert p["trend"] == {"trend_gate_span": 120}
    assert p["reclaim"] == {"reclaim_after_stops": 2, "reclaim_window_h": 24.0}
    assert p["cap"] == {"daily_stop_cap": 2}
    assert p["edge"] == {"entry_mode": "edge", "atr_rest_frac": 0.25}
    assert p["one_entry"] == {"max_stop_reentries": 1, "reentry_needs_1h_reclaim": True}
    assert p["lock"] == {"lock_trigger_r": 1.0, "lock_r": 0.0}
    assert p["half"] == {"partial_frac": 0.5, "partial_r": 3.0}


def test_combos_is_the_full_factorial_with_the_baseline_first():
    cs = E.combos()
    assert len(cs) == 4 * 3 * 3 * 3 * 3 * 3 * 3 * 3 == 8748
    assert cs[0] == E.baseline_combo() == ("off",) * 8
    assert len(set(cs)) == len(cs)
    assert E.combos(TWO) == [(d, t) for d in ("off", "90", "120", "180") for t in ("off", "120", "144")]


def test_names_rules_and_counts():
    b = E.baseline_combo(TWO)
    assert E.combo_name(b, TWO) == "baseline" and E.rules_on(b, TWO) == {} and E.n_rules(b, TWO) == 0
    c = ("120", "144")
    assert E.combo_name(c, TWO) == "delay120+trend144" and E.rules_on(c, TWO) == {"delay": "120", "trend": "144"}
    assert E.n_rules(c, TWO) == 2
    assert E.single("trend", None, TWO) == ("off", "120") and E.single("delay", "180", TWO) == ("180", "off")


def test_overrides_merge_every_on_axis_and_refuse_a_collision():
    assert E.overrides(("off", "off"), TWO) == {}
    assert E.overrides(("120", "144"), TWO) == {"entry_delay_min": 120.0, "trend_gate_span": 144}
    full = E.overrides(tuple(ax["primary"] for ax in E.AXES.values()))
    assert set(full) == {"entry_delay_min", "trend_gate_span", "reclaim_after_stops", "reclaim_window_h",
                         "daily_stop_cap", "entry_mode", "atr_rest_frac", "max_stop_reentries",
                         "reentry_needs_1h_reclaim", "lock_trigger_r", "lock_r", "partial_frac", "partial_r"}
    bad = {"a": {"levels": {"off": {}, "x": {"min_r": 1.0}}, "primary": "x"},
           "b": {"levels": {"off": {}, "y": {"min_r": 2.0}}, "primary": "y"}}
    with pytest.raises(ValueError):
        E.overrides(("x", "y"), bad)


def test_without_turns_one_axis_off():
    assert E.without(("120", "144"), "trend", TWO) == ("120", "off")
    assert E.without(("120", "144"), "delay", TWO) == ("off", "144")


def test_neighbours_are_the_adjacent_on_levels_only():
    assert E.neighbours(("120", "off"), TWO) == {"delay=90": ("90", "off"), "delay=180": ("180", "off")}
    assert E.neighbours(("90", "off"), TWO) == {"delay=120": ("120", "off")}
    assert E.neighbours(("180", "144"), TWO) == {"delay=120": ("120", "144"), "trend=120": ("180", "120")}
    assert E.neighbours(("off", "off"), TWO) == {}


# ── the guards on hand-built trades ────────────────────────────────────────

def _t(exit_ms, r, usd, entry_ms=None):
    from services.strategies.evals.stop_cap_test import _iso
    entry_ms = exit_ms - 3_600_000 if entry_ms is None else entry_ms
    return {"entry_ms": entry_ms, "exit_ms": exit_ms, "entry": _iso(entry_ms), "exit": _iso(exit_ms),
            "r": r, "net_usd": usd, "side": "long"}


START = 1_760_000_000_000      # 2025-10-09 ... any epoch; the guards only look at offsets and ISO days


def test_drop_best_day_fails_an_edge_one_day_carries_and_passes_a_spread_one():
    base = [_t(START + 1 * DAY, -1.1, -17.0), _t(START + 8 * DAY, -1.0, -18.0), _t(START + 15 * DAY, 2.0, 30.0)]
    # the candidate skips the first stop only: one day carries it
    cand = [_t(START + 8 * DAY, -1.0, -18.0), _t(START + 15 * DAY, 2.0, 30.0)]
    a = E.drop_best_day(base, cand)
    assert a["delta_usd"] == 17.0 and a["best_day_usd"] == 17.0 and a["rest_usd"] == 0.0 and a["pass"] is False
    assert a["fluke_r"]["fluke"] is True
    # skips both stops: the rest still carries 1R
    a2 = E.drop_best_day(base, [_t(START + 15 * DAY, 2.0, 30.0)])
    assert a2["rest_usd"] == pytest.approx(17.0) and a2["fluke_r"]["rest_r"] == pytest.approx(1.0) and a2["pass"] is True
    # dollars positive after the best day but R under the quarter: still a fluke
    cand3 = [_t(START + 8 * DAY, -1.0, -17.9), _t(START + 15 * DAY, 2.0, 30.0)]
    a3 = E.drop_best_day(base, cand3)
    assert a3["rest_usd"] == pytest.approx(0.1) and a3["pass"] is False
    assert E.drop_best_day(base, base)["pass"] is False          # no change at all


def test_weeks_counts_wins_losses_and_leave_one_out():
    end = START + 4 * WEEK
    base = [_t(START + 1 * DAY, -1.0, -10.0), _t(START + 9 * DAY, -1.0, -10.0), _t(START + 16 * DAY, 2.0, 20.0)]
    cand = [_t(START + 9 * DAY, -1.0, -10.0), _t(START + 16 * DAY, 2.0, 25.0), _t(START + 23 * DAY, -1.0, -3.0)]
    w = E.weeks(base, cand, START, end)
    assert w["n_weeks"] == 4 and w["per_week_usd"] == {"0": 10.0, "1": 0.0, "2": 5.0, "3": -3.0}
    assert (w["won"], w["lost"], w["flat"]) == (2, 1, 1) and w["most_weeks"] is False
    assert w["leave_one_out_min_usd"] == pytest.approx(2.0) and w["pass"] is True
    # one week carries it: leave-one-out goes negative
    w2 = E.weeks(base, [_t(START + 9 * DAY, -1.0, -10.0), _t(START + 16 * DAY, 2.0, 20.0),
                        _t(START + 23 * DAY, -1.0, -12.0)], START, end)
    assert w2["won"] == 1 and w2["lost"] == 1 and w2["pass"] is False
    assert E.weeks(base, base, START, end)["pass"] is False
    # a week boundary: an exit exactly 7 days in belongs to week 1
    assert E.week_of(START + WEEK, START) == 1 and E.week_of(START + WEEK - 1, START) == 0
    assert E.week_of(None, START) == 0


def test_plateau_needs_every_neighbour_above_the_baseline():
    nets = {("off", "off"): 100.0, ("90", "off"): 110.0, ("120", "off"): 120.0, ("180", "off"): 95.0}
    p = E.plateau(("120", "off"), nets, 100.0, TWO)
    assert p["ok"] == {"delay=90": True, "delay=180": False} and p["pass"] is False
    assert E.plateau(("90", "off"), nets, 100.0, TWO)["pass"] is True
    assert E.plateau(("off", "off"), nets, 100.0, TWO)["pass"] is False           # nothing on, nothing to check
    assert E.plateau(("off", "120"), nets, 100.0, TWO)["pass"] is False           # neighbour not simulated


def test_marginal_demands_every_rule_add_value():
    nets = {("off", "off"): 100.0, ("120", "off"): 120.0, ("off", "120"): 90.0, ("120", "120"): 120.0}
    m = E.marginal(("120", "120"), nets, TWO)
    assert m["gains_usd"] == {"delay": 30.0, "trend": 0.0} and m["pass"] is False
    assert E.marginal(("120", "off"), nets, TWO) == {"gains_usd": {"delay": 20.0}, "pass": True}
    assert E.marginal(("off", "off"), nets, TWO)["pass"] is False


def test_old_bar_legs():
    plat = {"pass": True}
    s, b = {"net_usd": 120.0, "max_dd_pct": 20.0}, {"net_usd": 100.0, "max_dd_pct": 25.0}
    assert E.old_bar(s, b, plat, 10.0, 5.0, 3.0)["pass"] is True
    assert E.old_bar({**s, "net_usd": 99.0}, b, plat, 10.0, 5.0, 3.0)["pass"] is False
    assert E.old_bar({**s, "max_dd_pct": 26.0}, b, plat, 10.0, 5.0, 3.0)["pass"] is False
    assert E.old_bar(s, b, {"pass": False}, 10.0, 5.0, 3.0)["pass"] is False
    assert E.old_bar(s, b, plat, 4.0, 5.0, 3.0)["pass"] is False          # last third below the baseline's
    assert E.old_bar(s, b, plat, -1.0, -5.0, 3.0)["pass"] is False        # last third negative
    assert E.old_bar(s, b, plat, 10.0, 5.0, -0.1)["pass"] is False        # WTI negative
    assert E.old_bar(s, b, plat, 10.0, 5.0, None)["pass"] is False        # no WTI


def _result(net, dd, last, trades, wti=5.0):
    return {"brent": {"net_usd": net, "max_dd_pct": dd, "trades": len(trades), "wins": 1, "refused": {}},
            "brent_trades": trades, "last_third": {"net_usd": last}, "wti": {"net_usd": wti, "trades": 3}}


def test_judge_best_of_best_and_funnel_on_a_two_axis_grid():
    end = START + 4 * WEEK
    base_tr = [_t(START + 1 * DAY, -1.0, -10.0), _t(START + 9 * DAY, -1.0, -10.0), _t(START + 16 * DAY, 2.0, 20.0),
               _t(START + 23 * DAY, -1.0, -10.0)]
    # "delay" at every level skips two of the three stops (two weeks, 2R): a real plateau
    good_tr = [_t(START + 16 * DAY, 2.0, 20.0), _t(START + 23 * DAY, -1.0, -10.0)]
    # "trend" skips every stop and the winner: fewer trades, less money, tiny drawdown
    trend_tr = [_t(START + 23 * DAY, -1.0, -10.0)]
    results = {
        ("off", "off"): _result(-10.0, 30.0, -10.0, base_tr),
        ("90", "off"): _result(9.0, 20.0, 5.0, good_tr), ("120", "off"): _result(10.0, 20.0, 5.0, good_tr),
        ("180", "off"): _result(11.0, 20.0, 5.0, good_tr),
        ("off", "120"): _result(-10.0, 5.0, -10.0, trend_tr), ("off", "144"): _result(-10.0, 5.0, -10.0, trend_tr),
        ("90", "120"): _result(10.0, 20.0, 5.0, good_tr), ("120", "120"): _result(10.0, 20.0, 5.0, good_tr),
        ("180", "120"): _result(11.0, 20.0, 5.0, good_tr),
        ("90", "144"): _result(10.0, 20.0, 5.0, good_tr), ("120", "144"): _result(10.0, 20.0, 5.0, good_tr),
        ("180", "144"): _result(11.0, 20.0, 5.0, good_tr),
    }
    judged = E.judge(results, START, end, 100.0, TWO)
    b = judged[("off", "off")]
    assert b["name"] == "baseline" and b["roi_pct"] == -10.0 and b["pass"] is False and b["old_bar"] is None
    d120 = judged[("120", "off")]
    assert d120["old_bar"]["pass"] and d120["drop_best_day"]["pass"] and d120["weeks"]["pass"]
    assert d120["plateau"]["pass"] and d120["marginal"]["gains_usd"] == {"delay": 20.0} and d120["pass"] is True
    # the pair carries a rule that adds nothing: marginal fails it, so it is not minimal
    pair = judged[("120", "120")]
    assert pair["old_bar"]["pass"] is True and pair["marginal"]["gains_usd"] == {"delay": 20.0, "trend": 0.0}
    assert pair["pass"] is False
    # trend alone: net not above the baseline
    assert judged[("off", "120")]["old_bar"]["net"] is False and judged[("off", "120")]["pass"] is False
    # the smallest passer wins, ties by net; delay180 has one neighbour (120) above the baseline
    assert E.best_of_best(judged) == ("180", "off")
    f = E.funnel(judged)
    # the three delays alone, plus delay90 with either trend (10 > 9: the trend leg adds $1 there)
    assert f["combos_tried"] == 11 and f["beat_baseline_net"] == 9 and f["all"] == 5
    assert judged[("90", "120")]["pass"] and judged[("90", "120")]["marginal"]["gains_usd"] == {"delay": 20.0, "trend": 1.0}
    assert E.best_of_best({c: {**j, "pass": False} for c, j in judged.items()}) is None


def test_overlap_table_and_labels():
    assert E.overlap_label(10.0, 5.0, 20.0, 0, 5.0) == "adds up"
    assert E.overlap_label(10.0, 5.0, 10.0, 3, -5.0) == "overlap"
    assert E.overlap_label(10.0, 5.0, 2.0, 0, -13.0) == "cancel"
    assert E.overlap_label(10.0, 5.0, 8.0, 0, -7.0) == "mixed"
    base_tr = [_t(START + 1 * DAY, -1.0, -10.0, START), _t(START + 2 * DAY, -1.0, -10.0, START + DAY),
               _t(START + 3 * DAY, 2.0, 20.0, START + 2 * DAY)]
    a_tr = base_tr[1:]                 # delay removes the first
    b_tr = [base_tr[0], base_tr[2]]    # trend removes the second
    results = {("off", "off"): _result(0.0, 1.0, 0.0, base_tr), ("120", "off"): _result(10.0, 1.0, 0.0, a_tr),
               ("off", "120"): _result(10.0, 1.0, 0.0, b_tr), ("120", "120"): _result(20.0, 1.0, 0.0, base_tr[2:])}
    rows = E.overlap_table(results, TWO)
    assert len(rows) == 1
    r = rows[0]
    assert (r["a"], r["b"]) == ("delay120", "trend120") and r["removed_a"] == 1 and r["removed_b"] == 1
    assert r["shared_removed"] == 0 and r["interaction_usd"] == 0.0 and r["label"] == "adds up"
    assert E.overlap_table({("off", "off"): results[("off", "off")]}, TWO) == []


# ── the simulator accepts every level, alone and all together ─────────────

def test_every_level_runs_in_the_sim_and_off_is_the_baseline():
    rows = [ENTER_LONG, QUIET, QUIET]
    base = _run(rows)
    for name, ax in E.AXES.items():
        for lv, ov in ax["levels"].items():
            run = _run(rows, **ov)
            if lv == "off":
                assert [(t.entry_time, t.entry_px) for t in run.trades] == [(t.entry_time, t.entry_px) for t in base.trades]
    full = _run(rows, **E.overrides(tuple(ax["primary"] for ax in E.AXES.values())))
    assert full.final_equity > 0


def test_the_live_rule_has_every_axis_off():
    lp = live_params()
    assert lp.entry_delay_min is None and lp.trend_gate_span is None and lp.reclaim_after_stops is None
    assert lp.daily_stop_cap is None and lp.entry_mode == "market" and lp.max_stop_reentries is None
    assert lp.lock_trigger_r is None and lp.partial_frac == 0.0


def test_the_baseline_carries_the_live_leverage_fit_and_the_other_baseline_is_derived():
    """2026-11-13 correction: the sweep's baseline is live_brent_params'
    (fit on); the diagnostic's other baseline is the unfitted 20x rule, and
    --no-lev-fit swaps them. Both read the fit off next_trade, never a typed
    (20, 15)."""
    from services.strategies.next_trade import BRENT, leverage_fit_for
    live_fit = tuple(leverage_fit_for(BRENT))
    assert E.base_params().entry_fit_leverage == live_fit
    assert E.LIVE_FIT == {"entry_fit_leverage": live_fit}
    assert E.UNFITTED == {"entry_fit_leverage": None}
    assert not hasattr(E, "LEV_FIT_DIAGNOSTIC")        # the typed (20, 15) is gone
    # the fit changes a wide-stop entry, so the two baselines are not the same run
    rows = [ENTER_LONG, QUIET, QUIET]
    fit, unfit = _run(rows, **E.LIVE_FIT), _run(rows, **E.UNFITTED)
    assert fit.final_equity > 0 and unfit.final_equity > 0


def test_roi_and_trade_rows():
    assert E.roi_pct(142.82, 182.09) == 78.4 and E.roi_pct(5.0, 0.0) == 0.0
    run = _run([ENTER_LONG, QUIET, QUIET])
    rows = E.trade_rows(run)
    assert len(rows) == len(run.trades)
    assert {"entry_ms", "exit_ms", "r", "net_usd", "reason"} <= set(rows[0]) if rows else True
