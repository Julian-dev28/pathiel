"""Gate tests for the oil regime eval (services/strategies/evals/
oil_regime_test.py, docs/strategy/OIL_REGIME_TEST_2026-11-02.md).

No network. The variant grid and its plateau families, the with-trend side
override (and its causality), the per-regime attribution, the scoring and
the adopt rule (including the sitting-out guard and the fill-slippage
allowance on the liquidation line), funding paging, and the 2026-11-02
Brent long on its real bars: the hourly with-trend filter refuses it, the
day label does not.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (Direction, Params, Run, Trade, live_params,
                                                        live_params_before_no_break, simulate)
from services.strategies.backtest.regime import (
    CHOP, DAY_MS, HOUR_MS, TREND_DOWN, TREND_UP, RegimeConfig, RegimeFeatures, RegimeSeries, label_series)
from services.strategies.evals import oil_regime_test as O
from services.strategies.tests.test_loss_fix import NOV2_1H, NOV2_15M, ms, nov2_direction

CFG = RegimeConfig()
T0 = ms(2026, 10, 1)


def series_of(rows):
    """A RegimeSeries from (close time, label, ret20, er, move) rows."""
    feats = [RegimeFeatures(t, r, e, m, 5.0, lab) for t, lab, r, e, m in rows]
    return RegimeSeries([f.asof_ms for f in feats], feats, CFG)


# ── the grid ───────────────────────────────────────────────────────────────

def test_variants_are_valid_params_and_live_is_empty():
    named = O.variants()
    assert named[O.BASELINE] == {}
    base = live_params()
    for v, ov in named.items():
        dataclasses.replace(base, **O.sim_overrides(ov))          # every override is a real field
    assert "_override" in named["override_day"] and O.sim_overrides(named["override_day"]) == {}
    for mode in O.FILTER_MODES:
        for ref in O.REFRESH:
            assert named[f"{mode}_{ref}"] == {"regime_filter": mode, "regime_refresh": ref}


def test_families_hold_every_shift_and_grid_neighbours():
    fam, named = O.families(), O.variants()
    assert len(fam["no_chop_day"]) == 6 and all(n in named for n in fam["no_chop_day"])
    assert named[fam["trend_only_hour"][0]]["regime_cfg"] == CFG.shifted("trend_ret", 0.75)
    assert fam["rstop_max_0.2"] == ["rstop_max_0.15", "rstop_max_0.25"]
    assert fam["rstop_max_0.1"] == ["rstop_max_0.15"]
    assert "no_break" not in fam                                  # a single variant has no plateau


# ── direction wrappers ─────────────────────────────────────────────────────

def test_from_start_hides_calls_before_the_window():
    d = O.from_start(lambda t: Direction(side="long"), T0)
    assert d(T0 - 1) is None and d(T0).side == "long"


def test_override_flips_a_call_that_fights_the_20d_trend():
    day1, day2, day3 = T0, T0 + DAY_MS, T0 + 2 * DAY_MS
    s = series_of([(day1, CHOP, 0.08, 0.10, -0.5),          # 20d up, no break: a short is overridden
                   (day2, TREND_DOWN, 0.08, 0.45, -3.0),    # 20d up but a straight-line fall: the short stands
                   (day3, CHOP, 0.01, 0.10, 0.2)])          # no 20d trend: nothing to override
    short = lambda t: Direction(side="short", entry_ceiling=110.0, invalidation_px=112.0, regime_id="c1")  # noqa
    d = O.override_direction(short, s, CFG, "day")
    got = d(day1 + 5 * HOUR_MS)
    assert got.side == "long" and got.entry_ceiling is None and got.invalidation_px is None
    assert got.regime_id == "c1"
    assert d(day2 + 5 * HOUR_MS).side == "short"
    assert d(day3 + 1).side == "short"
    assert O.override_direction(lambda t: None, s, CFG, "day")(day1) is None


def test_override_day_reads_the_open_and_hour_the_last_close():
    s = series_of([(T0, CHOP, 0.08, 0.1, -0.5), (T0 + 3 * HOUR_MS, TREND_DOWN, 0.08, 0.45, -3.0)])
    short = lambda t: Direction(side="short")   # noqa: E731
    t = T0 + 5 * HOUR_MS
    assert O.override_direction(short, s, CFG, "day")(t).side == "long"     # the 00:00 features
    assert O.override_direction(short, s, CFG, "hour")(t).side == "short"   # the 03:00 close saw the break


def test_thesis_direction_takes_the_latest_call():
    d = O.thesis_direction(O.NOV2_THESES)
    assert d(ms(2026, 11, 1)) is None
    assert d(ms(2026, 11, 2, 3, 45)).entry_ceiling == 111.7
    assert d(ms(2026, 11, 2, 4)).entry_ceiling == 110.0
    assert d(ms(2026, 11, 2, 9)).side == "short"


# ── attribution ────────────────────────────────────────────────────────────

def _run(trades, curve, start=225.0):
    r = Run(trades=trades, equity_curve=curve, params=dataclasses.replace(Params(), starting_equity=start))
    r.final_equity = curve[-1][1]
    return r


def _tr(t, r, net):
    return Trade(side="long", entry_time=t, entry_px=100.0, size=1.0, stop_px=99.0, target_px=103.0,
                 net_pnl_usd=net, r_multiple=r)


def test_by_regime_tags_trades_by_entry_day_and_compounds_days():
    s = series_of([(T0, TREND_UP, 0.1, 0.1, 1.0), (T0 + DAY_MS, CHOP, 0.0, 0.1, 0.0)])
    curve = [(T0 - HOUR_MS, 225.0), (T0 + HOUR_MS, 240.0), (T0 + 20 * HOUR_MS, 250.0),
             (T0 + DAY_MS + HOUR_MS, 225.0)]
    run = _run([_tr(T0 + HOUR_MS, 1.5, 25.0), _tr(T0 + DAY_MS + 2 * HOUR_MS, -1.0, -25.0)], curve)
    out = O.by_regime(run, s, T0, 225.0)
    assert out[TREND_UP] == {"days": 1, "trades": 1, "wins": 1, "win_rate": 1.0, "mean_r": 1.5, "sum_r": 1.5,
                             "days_return_pct": pytest.approx(11.1)}
    assert out[CHOP]["trades"] == 1 and out[CHOP]["sum_r"] == -1.0 and out[CHOP]["days_return_pct"] == -10.0
    assert TREND_DOWN not in out
    assert O.day_returns(run, T0, 225.0) == {T0 // DAY_MS: pytest.approx(25 / 225), T0 // DAY_MS + 1: pytest.approx(-0.1)}


def test_monthly_returns():
    oct31, nov1 = ms(2026, 10, 31), ms(2026, 11, 1)
    run = _run([], [(oct31, 250.0), (nov1, 200.0), (nov1 + HOUR_MS, 300.0)])
    assert O.monthly_returns(run, oct31, 225.0) == {"2026-10": pytest.approx(11.1), "2026-11": 20.0}


# ── scoring and the adopt rule ─────────────────────────────────────────────

def _rows(live, var, dd_live=50.0, dd_var=40.0):
    return {f"ds{i}": {O.BASELINE: {"final_equity": a, "max_dd_pct": dd_live},
                       "x": {"final_equity": b, "max_dd_pct": dd_var}} for i, (a, b) in enumerate(zip(live, var))}


def test_score_counts_wins_geo_mean_and_sitting_out():
    sc = O.score(_rows([100, 400, 300], [225, 300, 600]))["x"]
    assert (sc["beats"], sc["loses"], sc["of"]) == (2, 1, 3)
    assert sc["geo_mean_ratio"] == pytest.approx((2.25 * 0.75 * 2.0) ** (1 / 3), abs=1e-4)
    assert sc["flat_beats"] == 1                                   # only ds0 lost money under live
    assert sc["mean_dd"] == 40.0 and sc["live_mean_dd"] == 50.0
    assert O.score(_rows([100], [0.0]))["x"]["geo_mean_ratio"] == pytest.approx(0.01)


def _sc(beats, loses, of=8, geo=1.2, flat=5, dd=40.0, live_dd=50.0):
    return {"beats": beats, "loses": loses, "of": of, "geo_mean_ratio": geo, "mean_dd": dd,
            "live_mean_dd": live_dd, "flat_beats": flat}


def test_verdict_needs_more_wins_than_sitting_out():
    fam = {"a": ["b"]}
    scores = {"a": _sc(5, 3), "b": _sc(6, 2)}
    v = O.verdict(scores, "a", fam, {"a": 0.5})
    assert not v["adopt"] and v["fails"] == ["beats live where sitting out does not"]
    scores["a"] = _sc(6, 1)
    assert O.verdict(scores, "a", fam, {"a": 0.5})["adopt"]


def test_verdict_plateau_drawdown_and_liquidation():
    fam = {"a": ["b", "c"]}
    scores = {"a": _sc(6, 1), "b": _sc(6, 1), "c": _sc(3, 5)}
    assert O.verdict(scores, "a", fam, {"a": 0.5})["fails"] == ["plateau"]
    scores["c"] = _sc(6, 2)
    assert O.verdict(scores, "a", fam, {"a": 0.51})["adopt"]                       # fill slippage allowed
    assert "stop inside half the way to liquidation" in O.verdict(scores, "a", fam, {"a": 0.52})["fails"]
    scores["a"] = _sc(6, 1, dd=60.0)
    assert O.verdict(scores, "a", fam, {"a": 0.5})["fails"] == ["drawdown no worse"]
    assert "plateau" in O.verdict({"z": _sc(7, 0)}, "z", {}, {})["fails"]           # no neighbours, no plateau


def test_regime_delta_and_sign_stability():
    def res(sr_up, sr_chop, n_up=2, n_chop=3):
        return {"by_regime": {TREND_UP: {"trades": n_up, "sum_r": sr_up}, CHOP: {"trades": n_chop, "sum_r": sr_chop}}}
    rows = {"d1": {O.BASELINE: res(1.0, -2.0), "x": res(2.0, -1.0, 1, 1)},
            "d2": {O.BASELINE: res(0.5, 1.0), "x": res(0.5, 0.0, 2, 0)}}
    d = O.regime_delta(rows, "x")
    assert d[TREND_UP] == {"live_trades": 4, "live_sum_r": 1.5, "d_trades": -1, "d_sum_r": 1.0}
    assert d[CHOP] == {"live_trades": 6, "live_sum_r": -1.0, "d_trades": -5, "d_sum_r": 0.0}
    assert d[TREND_DOWN]["live_trades"] == 0
    base = {TREND_UP: {"sum_r": 3.0}, CHOP: {"sum_r": -0.2}}
    shifted = {"a": {TREND_UP: {"sum_r": 1.0}, CHOP: {"sum_r": 0.3}},
               "b": {TREND_UP: {"sum_r": -1.0}, CHOP: {"sum_r": -2.0}}}
    assert O.sign_stability(base, shifted) == {TREND_UP: 1, TREND_DOWN: 2, CHOP: 1}


def test_paged_funding_pages_on_full_batches():
    calls = []

    def fetch(coin, start, end):
        calls.append(start)
        first = -(-start // HOUR_MS) * HOUR_MS             # the venue's rows sit on the hour
        return [{"time": t, "fundingRate": "0.0001"} for t in range(first, end, HOUR_MS)][:500]

    out = O.paged_funding("xyz:BRENTOIL", 0, 1200 * HOUR_MS, fetch)
    assert len(out) == 1200 and len(calls) == 3
    assert [r["time"] for r in out] == sorted({r["time"] for r in out})


# ── 2026-11-02: the Brent long on its real bars ────────────────────────────

def nov2_with_history():
    """20 days of a steady rise to the 11-01 open (a 20-day uptrend, as
    Brent had: +8.1% at the 11-01 close), then the real 1h bars from
    2026-11-01 00:00Z and the real 15m bars from 11-02 03:45Z."""
    h0 = ms(2026, 11, 1)
    n = 20 * 24
    first = NOV2_1H[0][0]
    hist = []
    prev = first / 1.08
    for i in range(n):
        c = first / 1.08 * (1.08 ** ((i + 1) / n)) + 0.03 * math.sin(i)
        hist.append(Candle(t=h0 - (n - i) * HOUR_MS, o=prev, h=max(prev, c) + 0.05, l=min(prev, c) - 0.05,
                           c=c, v=1.0))
        prev = c
    real = [Candle(t=h0 + i * HOUR_MS, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(NOV2_1H)]
    q0 = ms(2026, 11, 2, 3, 45)
    c15 = [Candle(t=q0 + i * 900_000, o=o, h=h, l=lo, c=c, v=1.0) for i, (o, h, lo, c) in enumerate(NOV2_15M)]
    return c15, hist + real


def test_nov2_hourly_with_trend_refuses_the_long_the_day_label_does_not():
    c15, c1h = nov2_with_history()
    s = label_series(c1h)
    assert s.label_at(ms(2026, 11, 2)) != TREND_DOWN                  # what the desk knew at 00:00
    assert s.label_at(ms(2026, 11, 2, 3, 45)) == TREND_DOWN           # the 03:00 close: a straight-line fall
    # The rule live on 11-02 (breaks allowed; no_break_entry since 11-04).
    base = dataclasses.replace(live_params_before_no_break(), starting_equity=225.0, size_slippage=True,
                               risk_cap_frac=0.15)
    live = simulate(c15, c1h, nov2_direction, base)
    assert live.trades[0].entry_time == ms(2026, 11, 2, 3, 45) and live.trades[0].exit_reason == "stop"
    by_day = simulate(c15, c1h, nov2_direction, dataclasses.replace(base, regime_filter="with_trend"))
    assert by_day.trades[0].entry_time == live.trades[0].entry_time
    by_hour = simulate(c15, c1h, nov2_direction,
                       dataclasses.replace(base, regime_filter="with_trend", regime_refresh="hour"))
    assert by_hour.refused["regime_against"] >= 1
    assert not any(t.side == "long" and t.exit_reason == "stop" for t in by_hour.trades)
