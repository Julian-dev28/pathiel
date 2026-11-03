"""Gate tests for the silver strategy eval's pure parts
(services/strategies/evals/silver_strategy_test.py,
SILVER_STRATEGY_TEST_2026-11-04.md). Synthetic candles/theses/trades only,
no network, no live journal reads."""
from __future__ import annotations

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.backtest.desk_tick_sim import Run, Trade
from services.strategies.evals import silver_strategy_test as S

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _candle(t, c):
    return Candle(t=t, o=c, h=c, l=c, c=c, v=1.0)


def _thesis(created_at_ms, side, entry_px=100.0, thesis_id=None):
    return ThesisDirection(created_at_ms=created_at_ms, side=side, entry_px=entry_px, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id=thesis_id or f"t{created_at_ms}")


def _trade(entry_time, **kw):
    defaults = dict(side="short", entry_px=100.0, size=1.0, stop_px=101.0, target_px=95.0, exit_reason="stop",
                    net_pnl_usd=-1.0)
    defaults.update(kw)
    return Trade(entry_time=entry_time, **defaults)


# ── variants() / FAMILIES ───────────────────────────────────────────────

def test_variants_includes_every_named_candidate():
    named = S.variants()
    for name in ("live", "lev_fit", "ai_levels", "entry_lookback_8h", "pullback_8h", "breakout_8h_measured",
                "breakout_24h_measured", "breakout_8h_thesis", "breakout_24h_thesis", "breakout_24h_r2",
                "min_r_1.5"):
        assert name in named
    assert named["live"] == {}
    assert named["lev_fit"]["entry_fit_leverage"] == S.LEV_FIT_OV["entry_fit_leverage"]
    assert named["breakout_24h_measured"] == {"pullback_entry": "a5", "pb_breakout_lookback_h": 24,
                                              "pb_breakout_stop_h": 4, "pb_target": "measured"}
    assert named["breakout_24h_r2"]["pb_target_r"] == 2.0
    assert "pb_target" not in named["breakout_24h_r2"]   # the fixed-R variant doesn't also set a structural target


def test_families_every_member_is_a_real_variant():
    named = S.variants()
    for grid in S.FAMILIES.values():
        for name in grid:
            assert name in named


# ── calls_with_verdict ──────────────────────────────────────────────────

def test_calls_with_verdict_right_and_wrong_direction():
    # short at 100, price falls to 90 within the horizon: direction right
    c1h = [_candle(0, 100.0), _candle(HOUR_MS, 95.0), _candle(DAY_MS, 90.0)]
    th = _thesis(0, "short", entry_px=100.0)
    rows = S.calls_with_verdict([th], c1h, horizon_days=1.0)
    assert len(rows) == 1 and rows[0]["direction_right"] is True and rows[0]["ret_pct"] == 10.0

    # same price path, a LONG call: direction wrong
    th_long = _thesis(0, "long", entry_px=100.0)
    rows_l = S.calls_with_verdict([th_long], c1h, horizon_days=1.0)
    assert rows_l[0]["direction_right"] is False


def test_calls_with_verdict_skips_flat_and_no_entry_px_and_calls_past_the_data_end():
    c1h = [_candle(0, 100.0)]
    flat = _thesis(0, "flat", entry_px=None)
    no_entry = _thesis(0, "short", entry_px=None)
    # a call made well after the last candle: no bar in [created_at, +horizon] to judge it against
    too_late = _thesis(10 * DAY_MS, "short", entry_px=100.0)
    assert S.calls_with_verdict([flat], c1h, 1.0) == []
    assert S.calls_with_verdict([no_entry], c1h, 1.0) == []
    assert S.calls_with_verdict([too_late], c1h, 1.0) == []


def test_calls_with_verdict_window_end_is_the_next_calls_start_last_is_open_ended():
    c1h = [_candle(0, 100.0), _candle(HOUR_MS, 100.0), _candle(2 * HOUR_MS, 100.0)]
    rows = S.calls_with_verdict([_thesis(0, "short"), _thesis(HOUR_MS, "long")], c1h, horizon_days=1.0)
    first = next(r for r in rows if r["created_at_ms"] == 0)
    last = next(r for r in rows if r["created_at_ms"] == HOUR_MS)
    assert first["window_end_ms"] == HOUR_MS
    assert last["window_end_ms"] is None


# ── missed_opportunity ───────────────────────────────────────────────────

def test_missed_opportunity_counts_right_calls_with_no_trade_inside_their_window():
    calls = [
        {"thesis_id": "a", "created_at_ms": 0, "window_end_ms": HOUR_MS, "direction_right": True, "ret_pct": 1.0,
         "created_at": "a", "side": "short"},
        {"thesis_id": "b", "created_at_ms": HOUR_MS, "window_end_ms": 2 * HOUR_MS, "direction_right": True,
         "ret_pct": 1.0, "created_at": "b", "side": "short"},
        {"thesis_id": "c", "created_at_ms": 2 * HOUR_MS, "window_end_ms": None, "direction_right": False,
         "ret_pct": -1.0, "created_at": "c", "side": "short"},
    ]
    # a trade opened inside call "b"'s window; call "a" and "c" see no trade
    run = Run(trades=[_trade(entry_time=HOUR_MS + 100)])
    out = S.missed_opportunity(calls, run)
    assert out["n_calls"] == 3 and out["n_right"] == 2 and out["n_missed"] == 1
    assert out["missed_calls"] == [{"thesis_id": "a", "created_at": "a", "side": "short", "ret_pct": 1.0}]
    assert out["missed_rate_of_right"] == 0.5
    assert out["missed_rate_of_calls"] == round(1 / 3, 3)


def test_missed_opportunity_no_right_calls_is_none_not_a_crash():
    calls = [{"thesis_id": "a", "created_at_ms": 0, "window_end_ms": None, "direction_right": False,
             "ret_pct": -1.0, "created_at": "a", "side": "short"}]
    out = S.missed_opportunity(calls, Run())
    assert out["n_right"] == 0 and out["missed_rate_of_right"] is None


def test_missed_opportunity_empty_calls():
    out = S.missed_opportunity([], Run())
    assert out == {"n_calls": 0, "n_right": 0, "n_missed": 0, "missed_rate_of_calls": None,
                  "missed_rate_of_right": None, "missed_calls": []}


def test_missed_opportunity_open_ended_last_call_sees_any_later_trade():
    calls = [{"thesis_id": "a", "created_at_ms": HOUR_MS, "window_end_ms": None, "direction_right": True,
             "ret_pct": 1.0, "created_at": "a", "side": "short"}]
    run = Run(trades=[_trade(entry_time=5 * HOUR_MS)])
    assert S.missed_opportunity(calls, run)["n_missed"] == 0
    run_before = Run(trades=[_trade(entry_time=HOUR_MS - 1)])   # before the call started: doesn't count
    assert S.missed_opportunity(calls, run_before)["n_missed"] == 1


# ── pick_top2 / combo_overrides ──────────────────────────────────────────

def _score(beats, loses, of, geo):
    return {"beats": beats, "loses": loses, "of": of, "geo_mean_ratio": geo}


def test_pick_top2_ranks_by_beats_minus_loses_then_geo_mean():
    scores = {S.BASELINE: _score(0, 0, 2, 1.0), "a": _score(2, 0, 2, 1.1), "b": _score(1, 1, 2, 2.0),
             "c": _score(1, 1, 2, 1.5), "d": _score(0, 2, 2, 0.5)}
    assert S.pick_top2(scores) == ["a", "b"]   # a wins on beats-loses; b beats c on geo-mean at the same beats-loses
    assert S.BASELINE not in S.pick_top2(scores)


def test_pick_top2_respects_exclude():
    scores = {S.BASELINE: _score(0, 0, 2, 1.0), "a": _score(2, 0, 2, 1.1), "b": _score(1, 1, 2, 2.0)}
    assert S.pick_top2(scores, exclude=("a",)) == ["b"]


def test_combo_overrides_unions_the_named_variants():
    named = {"x": {"min_r": 1.5}, "y": {"ai_levels": True}}
    assert S.combo_overrides(named, ["x", "y"]) == {"min_r": 1.5, "ai_levels": True}


# ── dd_and_liq / adopt_verdict ────────────────────────────────────────────

def _results(dd_by_variant, liq_by_variant):
    return {"ds1": {"variants": {v: {"max_dd_pct": dd, "max_stop_liq_frac": liq_by_variant[v]}
                                 for v, dd in dd_by_variant.items()}}}


def test_dd_and_liq_takes_the_worst_across_datasets():
    results = {"a": {"variants": {"x": {"max_dd_pct": 10.0, "max_stop_liq_frac": 0.3}}},
              "b": {"variants": {"x": {"max_dd_pct": 40.0, "max_stop_liq_frac": 0.2}}}}
    max_dd, max_liq = S.dd_and_liq(results, {"x": {}})
    assert max_dd["x"] == 40.0 and max_liq["x"] == 0.3


def test_adopt_verdict_requires_both_thesis_datasets_beaten():
    thesis_scores = {"x": _score(1, 1, 2, 1.1)}      # ties/loses one of two: "most of 2" fails
    control_scores = {"x": _score(2, 1, 3, 1.1)}
    v = S.adopt_verdict(thesis_scores, control_scores, {"x": 10.0}, {"x": 0.3}, "x", live_dd=10.0)
    assert v["adopt"] is False and "beats live on both thesis datasets" in v["fails"]


def test_adopt_verdict_passes_when_every_gate_clears():
    thesis_scores = {"x": _score(2, 0, 2, 1.2)}
    control_scores = {"x": _score(2, 1, 3, 1.1)}
    v = S.adopt_verdict(thesis_scores, control_scores, {"x": 10.0}, {"x": 0.3}, "x", live_dd=10.0)
    assert v["adopt"] is True and v["fails"] == []


def test_adopt_verdict_fails_on_dd_liq_or_plateau():
    thesis_scores = {"x": _score(2, 0, 2, 1.2), "fam_lo": _score(0, 2, 2, 0.5), "fam_hi": _score(2, 0, 2, 1.2)}
    control_scores = {"x": _score(2, 1, 3, 1.1)}
    families = {"fam": ["fam_lo", "fam_hi"]}
    # DD blown
    v_dd = S.adopt_verdict(thesis_scores, control_scores, {"x": 20.0}, {"x": 0.3}, "x", live_dd=10.0)
    assert v_dd["adopt"] is False and "max DD within 2pts of live" in v_dd["fails"]
    # liq over the gate
    v_liq = S.adopt_verdict(thesis_scores, control_scores, {"x": 10.0}, {"x": 0.6}, "x", live_dd=10.0)
    assert v_liq["adopt"] is False and "stop inside half the liquidation distance" in v_liq["fails"]
    # plateau: fam_hi's only neighbour (fam_lo) does not beat live
    old_families = S.FAMILIES
    try:
        S.FAMILIES = families
        v_plateau = S.adopt_verdict(thesis_scores, control_scores, {"fam_hi": 10.0}, {"fam_hi": 0.3}, "fam_hi",
                                    live_dd=10.0)
        assert v_plateau["adopt"] is False and "plateau" in v_plateau["fails"]
    finally:
        S.FAMILIES = old_families


def test_adopt_verdict_no_score_is_a_clean_refusal():
    assert S.adopt_verdict({}, {}, {}, {}, "nope", live_dd=0.0) == {"adopt": False, "candidate": "nope",
                                                                     "why": "no score"}
