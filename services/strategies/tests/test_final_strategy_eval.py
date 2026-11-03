"""Gate tests for the final oil-strategy adoption eval's pure parts
(services/strategies/evals/final_strategy_test.py,
FINAL_STRATEGY_TEST_2026-11-04.md). Synthetic candles/trades only, no
network, no live journal reads."""
from __future__ import annotations

import dataclasses

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Run, Trade
from services.strategies.evals import final_strategy_test as F
from services.strategies.evals.live_params import live_brent_params

FIFTEEN_MS = 900_000
HOUR_MS = 3_600_000


def _candle(t, c, h=None, l=None, o=None):
    return Candle(t=t, o=o if o is not None else c, h=h if h is not None else c, l=l if l is not None else c, c=c,
                 v=1.0)


def _trade(**kw):
    defaults = dict(side="long", entry_time=0, entry_px=100.0, size=1.0, stop_px=99.0, target_px=105.0,
                    exit_reason="stop", net_pnl_usd=-1.0, leverage=None, orig_stop_px=None, exit_px=None)
    defaults.update(kw)
    return Trade(**defaults)


# ── variants() ───────────────────────────────────────────────────────────

def test_variants_includes_every_named_combination():
    named = F.variants(((0, 1),))
    for name in ("live", "lev_fit", "entries_reclaim", "eia_2_2", "lev_fit_eia", "lev_fit_reclaim", "combined"):
        assert name in named
    for r in F.PARTIAL_RS:
        assert f"live_half_r{r:g}" in named
        assert f"combined_half_r{r:g}" in named
    assert named["live"] == {}
    assert named["lev_fit"]["entry_fit_leverage"] == F.LEV_FIT_OV["entry_fit_leverage"]


def test_variants_combined_is_union_of_the_three_axes():
    named = F.variants(((5, 9),))
    c = named["combined"]
    assert c["entry_fit_leverage"] == F.LEV_FIT_OV["entry_fit_leverage"]
    assert c["max_stop_reentries"] == 1
    assert c["reentry_needs_1h_reclaim"] is True
    assert c["event_blackout_windows"] == ((5, 9),)


def test_variants_half_off_carries_the_combined_overrides_too():
    named = F.variants(())
    h = named["combined_half_r4.0"] if "combined_half_r4.0" in named else named["combined_half_r4"]
    assert h["partial_frac"] == 0.5
    assert h["partial_r"] == 4.0
    assert h["entry_fit_leverage"] == F.LEV_FIT_OV["entry_fit_leverage"]
    live_h = named["live_half_r4"]
    assert "entry_fit_leverage" not in live_h   # live_half is the live rule, no lev_fit/reclaim/eia


# ── leverage_distribution ───────────────────────────────────────────────

def test_leverage_distribution_uses_trade_own_leverage_falling_back_to_params():
    p = dataclasses.replace(live_brent_params(), leverage=20.0)
    run = Run(trades=[_trade(leverage=20.0), _trade(leverage=10.0), _trade(leverage=None)])
    dist = F.leverage_distribution(run, p)
    assert dist == {"20x": 2, "10x": 1}   # the leverage=None trade falls back to params.leverage=20


def test_leverage_distribution_empty_run():
    assert F.leverage_distribution(Run(), live_brent_params()) == {}


# ── extra_trades ─────────────────────────────────────────────────────────

def test_extra_trades_finds_entries_the_baseline_never_opened():
    base = Run(trades=[_trade(entry_time=0)], refused={"leverage": 50})
    var = Run(trades=[_trade(entry_time=0), _trade(entry_time=HOUR_MS, leverage=10.0, net_pnl_usd=3.5)],
             refused={"leverage": 0})
    p = live_brent_params()
    out = F.extra_trades(base, var, p, p)
    assert out["count"] == 1
    assert out["trades"][0]["entry_time"] is not None
    assert out["net_usd_sum"] == 3.5
    assert out["base_refused_leverage"] == 50
    assert out["var_refused_leverage"] == 0


def test_extra_trades_none_when_entry_times_match():
    base = Run(trades=[_trade(entry_time=0), _trade(entry_time=HOUR_MS)])
    var = Run(trades=[_trade(entry_time=0), _trade(entry_time=HOUR_MS)])
    p = live_brent_params()
    out = F.extra_trades(base, var, p, p)
    assert out["count"] == 0
    assert out["net_usd_sum"] == 0


# ── corrected_max_stop_liq_frac (the leverage-correctness fix) ────────────

def test_corrected_liq_frac_uses_trade_leverage_not_params_leverage():
    """A stop that clears half the liquidation distance at the trade's OWN
    (lower) leverage must not be judged against params.leverage=20 instead
    -- the bug this function exists to fix (see its docstring)."""
    p = dataclasses.replace(live_brent_params(), leverage=20.0, max_leverage=20.0)
    # entry 100, stop 95: at 20x this is a wide stop (close to/through the
    # liq line); at 5x it is comfortably inside half the liq distance.
    tr_20x = _trade(entry_px=100.0, stop_px=95.0, orig_stop_px=95.0, leverage=20.0)
    tr_5x = _trade(entry_px=100.0, stop_px=95.0, orig_stop_px=95.0, leverage=5.0)
    frac_20x = F.corrected_max_stop_liq_frac(Run(trades=[tr_20x]), p)
    frac_5x = F.corrected_max_stop_liq_frac(Run(trades=[tr_5x]), p)
    assert frac_5x < frac_20x   # the same stop sits much closer to liquidation at 20x than at 5x
    assert frac_5x < 0.5        # 5x: well inside the gate


def test_corrected_liq_frac_falls_back_to_params_leverage_when_trade_leverage_is_none():
    p = dataclasses.replace(live_brent_params(), leverage=20.0)
    tr = _trade(entry_px=100.0, stop_px=95.0, orig_stop_px=95.0, leverage=None)
    tr_explicit = _trade(entry_px=100.0, stop_px=95.0, orig_stop_px=95.0, leverage=20.0)
    assert F.corrected_max_stop_liq_frac(Run(trades=[tr]), p) == \
        F.corrected_max_stop_liq_frac(Run(trades=[tr_explicit]), p)


def test_corrected_liq_frac_uses_orig_stop_px_over_a_moved_stop():
    p = live_brent_params()
    tr = _trade(entry_px=100.0, stop_px=101.0, orig_stop_px=95.0, leverage=20.0)   # stop moved to breakeven+
    frac = F.corrected_max_stop_liq_frac(Run(trades=[tr]), p)
    # should be computed off orig_stop_px=95, not the moved stop_px=101 (which isn't even on the right side)
    assert frac > 0


def test_corrected_liq_frac_empty_run_is_zero():
    assert F.corrected_max_stop_liq_frac(Run(), live_brent_params()) == 0.0


# ── realized_stop_overshoot ──────────────────────────────────────────────

def test_realized_overshoot_is_zero_when_fill_matches_the_nominal_stop():
    p = live_brent_params()
    tr = _trade(entry_px=100.0, stop_px=99.25, orig_stop_px=99.25, exit_px=99.25, exit_reason="stop", leverage=20.0)
    out = F.realized_stop_overshoot(Run(trades=[tr]), p)
    assert out["slippage_overshoot"] == 0.0


def test_realized_overshoot_is_positive_when_fill_slips_past_the_nominal_stop():
    p = live_brent_params()
    # fill trades through further than the nominal stop (gap/slippage) -> closer to liq than the gate judged
    tr = _trade(entry_px=100.0, stop_px=99.25, orig_stop_px=99.25, exit_px=97.5, exit_reason="stop", leverage=20.0)
    out = F.realized_stop_overshoot(Run(trades=[tr]), p)
    assert out["slippage_overshoot"] > 0.0
    assert out["max_realized_stop_liq_frac"] > out["max_nominal_stop_liq_frac"]


def test_realized_overshoot_ignores_non_stop_exits():
    p = live_brent_params()
    tr = _trade(entry_px=100.0, stop_px=99.25, orig_stop_px=99.25, exit_px=50.0, exit_reason="target", leverage=20.0)
    out = F.realized_stop_overshoot(Run(trades=[tr]), p)
    assert out["max_nominal_stop_liq_frac"] == 0.0
    assert out["max_realized_stop_liq_frac"] == 0.0


# ── regression_row (the 2026-11-03 long, half-off at 4R/5R) ──────────────

def _regression_bars():
    """Synthetic 15m bars standing in for the real 103.61/102.99/111.77
    trade: rallies through 4R and 5R (but never 6R), then stops out."""
    t0 = F._ms((2026, 11, 2, 18, 0))
    bars = []
    # rally bar by bar up through 4R (106.09) and 5R (106.71), short of 6R (107.33)
    highs = [104.5, 105.5, 106.2, 106.8, 107.0, 106.9, 106.5]
    for i, h in enumerate(highs):
        bars.append(_candle(t0 + (i + 1) * FIFTEEN_MS, c=h - 0.05, h=h, l=h - 0.3))
    # then stop out
    bars.append(_candle(t0 + (len(highs) + 1) * FIFTEEN_MS, c=102.0, h=103.2, l=101.9))
    return bars


def test_regression_row_baseline_stops_out_for_a_loss():
    base = live_brent_params()
    out = F.regression_row(_regression_bars(), base, partial_rs=(3.0, 4.0, 5.0, 6.0))
    assert out["baseline"]["exit"] == "stop"
    assert out["baseline"]["net_usd"] < 0


def test_regression_row_half_off_at_4r_and_5r_fills_but_6r_never_fills():
    base = live_brent_params()
    out = F.regression_row(_regression_bars(), base, partial_rs=(3.0, 4.0, 5.0, 6.0))
    assert out["half_off"]["r4"]["partial_filled"] is True
    assert out["half_off"]["r5"]["partial_filled"] is True
    assert out["half_off"]["r6"]["partial_filled"] is False   # the rally tops out short of 6R


def test_regression_row_half_off_beats_the_full_size_stop_out():
    """Locking in half the size near the top before the full reversal and
    stop-out must leave the half-off variant with a better (less negative,
    or positive) net than taking the full loss on the whole position."""
    base = live_brent_params()
    out = F.regression_row(_regression_bars(), base, partial_rs=(4.0, 5.0))
    assert out["half_off"]["r4"]["net_usd"] > out["baseline"]["net_usd"]
    assert out["half_off"]["r5"]["net_usd"] > out["baseline"]["net_usd"]


def test_regression_row_no_bars_after_entry_stays_open():
    base = live_brent_params()
    out = F.regression_row([], base, partial_rs=(4.0,))
    assert out["baseline"]["exit"] == "open_at_data_end"
    assert out["baseline"]["net_usd"] < 0   # just the entry fee


# ── dd_and_liq / adopt_verdict ────────────────────────────────────────────

def _fake_results():
    return {
        "journal": {"variants": {"live": {"max_dd_pct": 50.0, "max_stop_liq_frac": 0.4},
                                 "good": {"max_dd_pct": 40.0, "max_stop_liq_frac": 0.3},
                                 "bad_liq": {"max_dd_pct": 40.0, "max_stop_liq_frac": 0.9}}},
        "oct": {"variants": {"live": {"max_dd_pct": 60.0, "max_stop_liq_frac": 0.45},
                             "good": {"max_dd_pct": 55.0, "max_stop_liq_frac": 0.35},
                             "bad_liq": {"max_dd_pct": 50.0, "max_stop_liq_frac": 0.95}}},
    }


def test_dd_and_liq_takes_the_worst_across_datasets():
    named = {"live": {}, "good": {}, "bad_liq": {}}
    max_dd, max_liq = F.dd_and_liq(_fake_results(), named)
    assert max_dd["live"] == 60.0
    assert max_liq["bad_liq"] == 0.95


def test_adopt_verdict_rejects_a_candidate_past_half_the_liquidation_distance():
    scores = {"bad_liq": {"beats": 2, "loses": 0, "of": 2, "geo_mean_ratio": 1.5}}
    max_dd = {"bad_liq": 50.0}
    max_liq = {"bad_liq": 0.95}
    v = F.adopt_verdict(scores, max_dd, max_liq, "bad_liq", live_dd=60.0)
    assert v["adopt"] is False
    assert any("liquidation" in w for w in v["fails"])


def test_adopt_verdict_rejects_fewer_than_three_of_four_beats():
    scores = {"meh": {"beats": 2, "loses": 2, "of": 4, "geo_mean_ratio": 1.0}}
    v = F.adopt_verdict(scores, {"meh": 10.0}, {"meh": 0.3}, "meh", live_dd=10.0)
    assert v["adopt"] is False
    assert any("3/4" in w for w in v["fails"])


def test_adopt_verdict_rejects_drawdown_worse_than_live_by_more_than_2_points():
    scores = {"risky": {"beats": 4, "loses": 0, "of": 4, "geo_mean_ratio": 2.0}}
    v = F.adopt_verdict(scores, {"risky": 65.0}, {"risky": 0.3}, "risky", live_dd=60.0)
    assert v["adopt"] is False
    assert any("DD" in w for w in v["fails"])


def test_adopt_verdict_passes_a_clean_candidate_outside_any_family():
    scores = {"clean": {"beats": 4, "loses": 0, "of": 4, "geo_mean_ratio": 1.8}}
    v = F.adopt_verdict(scores, {"clean": 58.0}, {"clean": 0.45}, "clean", live_dd=60.0)
    assert v["adopt"] is True
    assert v["fails"] == []


def test_adopt_verdict_plateau_requires_a_good_neighbour_for_half_off_grid():
    # live_half_r3 good, r4 bad (only 1/4 beats): r3's only neighbour (r4) fails -> r3's plateau fails
    scores = {
        "live_half_r3.0": {"beats": 3, "loses": 1, "of": 4, "geo_mean_ratio": 1.2},
        "live_half_r4.0": {"beats": 1, "loses": 3, "of": 4, "geo_mean_ratio": 0.8},
    }
    families = {"half_live": ["live_half_r3.0", "live_half_r4.0"]}
    orig_families = F.FAMILIES
    F.FAMILIES = families
    try:
        v = F.adopt_verdict(scores, {"live_half_r3.0": 10.0}, {"live_half_r3.0": 0.3}, "live_half_r3.0", live_dd=10.0)
    finally:
        F.FAMILIES = orig_families
    assert v["adopt"] is False
    assert "plateau" in v["fails"]


# ── summary_csv_rows ───────────────────────────────────────────────────────

def test_summary_csv_rows_shape():
    results = {"journal": {"variants": {"live": {
        "final_equity": 64.0, "return_pct": 0.0, "days": 10.0, "max_dd_pct": 5.0, "trades": 3, "win_rate": 0.5,
        "avg_r": 1.0, "worst_loss_pct": -5.0, "max_stop_liq_frac": 0.3, "exits": {"stop": 1, "target": 2}}}}}
    rows = F.summary_csv_rows(results)
    assert len(rows) == 1
    assert rows[0]["dataset"] == "journal"
    assert rows[0]["stop_exits"] == 1
    assert rows[0]["per_day_pct"] == 0.0
