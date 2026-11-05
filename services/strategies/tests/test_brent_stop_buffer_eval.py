"""Gate tests for the Brent stop buffer / weekend eval's pure parts
(services/strategies/evals/brent_stop_buffer_test.py): the grids, the
verdict rules (most datasets, plateau, the 20x line), the risk numbers,
the 2026-11-02 long regression, the Sat 10-31 long under the weekend
locks, and this weekend's trades against the window. No network: the
2026-11-02 bars are the real xyz:BRENTOIL 15m candles from 03:45Z to
08:45Z, typed in; the 1h warmup is synthetic with the real 24h range
(110.14 to 112.39 when the long opened)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction, Run, Trade, live_params, live_params_before_no_break, simulate
from services.strategies.evals import brent_stop_buffer_test as T
from services.strategies.evals.profit_lock_test import replay_position

HOUR_MS = 3_600_000
Q = 900_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _base(**over):
    return dataclasses.replace(live_params_before_no_break(), starting_equity=64.0, **over)


# ── the grids ──────────────────────────────────────────────────────────────

def test_buffer_grid():
    v = T.buffer_variants()
    assert v["live"] == {}
    assert "fixed_0.75" not in v
    assert v["fixed_0.90"] == {"stop_buffer": 0.9} and v["fixed_1.50"] == {"stop_buffer": 1.5}
    assert v["atr_1.3"] == {"stop_buffer_atr": 1.3} and v["atr_2.0"] == {"stop_buffer_atr": 2.0}
    assert v["pct_0.9"] == {"fixed_buffer_floor_frac": pytest.approx(0.009)}
    assert v["pct_0.85"] == {"fixed_buffer_floor_frac": pytest.approx(0.0085)}
    assert v["pct_1.2"] == {"fixed_buffer_floor_frac": pytest.approx(0.012)}
    for fam in T.FAMILIES.values():
        assert all(n in v for n in fam)
    assert T.FAMILIES["fixed"][1] == "live"


def test_weekend_grid():
    w = T.weekend_variants({"stop_buffer": 0.9}, "fixed_0.90")
    assert w == {"fixed_0.90_24/7": {"stop_buffer": 0.9, "weekend_off": None},
                 "fixed_0.90_wk_hold": {"stop_buffer": 0.9, "weekend_off": "hold"},
                 "fixed_0.90_wk_flat": {"stop_buffer": 0.9, "weekend_off": "flatten"}}
    assert set(T.weekend_variants({})) == {"24/7", "wk_hold", "wk_flat"}


def test_lock_grid():
    v = T.lock_variants()
    assert v["live"] == {}
    for name, ov in v.items():
        if name.startswith("wk_"):
            assert ov["lock_weekend_only"] is True, name
    assert v["wk_befees_t1"] == {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.0,
                                 "lock_plus_fees": True}
    assert "lock_weekend_only" not in v["all_befees_t1"] and v["all_befees_t1"]["lock_plus_fees"]
    assert v["wk_tgt50_l0.5"]["lock_trigger_target_frac"] == 0.5 and "lock_trigger_r" not in v["wk_tgt50_l0.5"]
    assert v["wk_trail_t1.5_l1"]["lock_trail"] is True
    # the profit-lock test's 8 trigger/lock pairs, fixed and trailing
    assert sum(1 for n in v if n.startswith(("wk_lock_", "wk_trail_"))) == 16
    for fam in T.lock_families().values():
        assert all(n in v for n in fam)


# ── verdict rules ──────────────────────────────────────────────────────────

def _sc(beats, loses, of, geo):
    return {"beats": beats, "loses": loses, "of": of, "geo_mean_ratio": geo}


FAMS = {"fixed": ["a", "live", "b", "c", "d"]}


def test_plateau_needs_every_non_live_neighbour_to_win():
    scores = {"a": _sc(1, 6, 7, 0.5), "b": _sc(6, 1, 7, 1.4), "c": _sc(5, 2, 7, 1.2), "d": _sc(2, 5, 7, 0.8)}
    assert T.plateau(scores, "b", FAMS)                   # live on one side, c wins on the other
    assert not T.plateau(scores, "c", FAMS)               # d loses
    assert not T.plateau(scores, "a", FAMS)               # only live beside it: no evidence
    assert not T.plateau(scores, "zzz", FAMS)
    scores["c"] = _sc(4, 3, 7, 0.99)                      # most datasets but geo under 1
    assert not T.plateau(scores, "b", FAMS)


def test_plateau_must_hold_on_every_axis():
    fams = {"x": ["p", "q", "r"], "y": ["s", "q", "t"]}
    good, bad = _sc(5, 1, 7, 1.2), _sc(1, 5, 7, 0.9)
    assert T.plateau({"p": good, "q": good, "r": good, "s": good, "t": good}, "q", fams)
    assert not T.plateau({"p": good, "q": good, "r": good, "s": good, "t": bad}, "q", fams)


def test_verdict_rules():
    scores = {"b": _sc(6, 1, 7, 1.4), "c": _sc(5, 2, 7, 1.2), "a": _sc(1, 6, 7, 0.5), "d": _sc(2, 5, 7, 0.8)}
    v = T.verdict(scores, {"b": 0.48}, "b", FAMS)
    assert v["adopt"] and v["fails"] == []
    v = T.verdict(scores, {"b": 0.51}, "b", FAMS)
    assert not v["adopt"] and v["fails"] == ["stop inside half the way to liquidation"]
    v = T.verdict({**scores, "b": _sc(3, 3, 7, 1.4)}, {"b": 0.4}, "b", FAMS)
    assert v["fails"][0] == "beats live on most datasets"
    assert T.verdict(scores, {}, None)["adopt"] is False
    assert T.verdict(scores, {"b": 0.4}, "b", families=None)["plateau"] is True
    assert T.rule_pass(scores, {"b": 0.4, "c": 0.4, "a": 0.4, "d": 0.4}, FAMS) == ["b"]


def test_pick_best_counts_datasets_first():
    scores = {"x": _sc(7, 0, 7, 1.1), "y": _sc(5, 2, 7, 2.0)}
    assert T.pick_best(scores) == "x"
    assert T.pick_best({}) is None


# ── risk numbers ───────────────────────────────────────────────────────────

def test_stop_liq_frac_on_the_live_long():
    # 110.0099, stop 109.25, liq ~107.19 at 20x on a 20x coin: about 27% of the way
    assert T.stop_liq_frac(110.0099, 109.25, live_params()) == pytest.approx(0.269, abs=0.002)
    assert T.stop_liq_frac(110.0, 110.0, live_params()) is None


def test_risk_numbers_worst_loss_and_liq_line():
    p = _base()
    t1, t2 = ms(2026, 11, 2, 3, 45), ms(2026, 11, 7, 12)
    run = Run(params=p, equity_curve=[(t1 - Q, 64.0), (t1, 63.0), (t2 - Q, 100.0), (t2, 100.0)])
    run.trades = [Trade(side="long", entry_time=t1, entry_px=110.0, size=1, stop_px=109.25, target_px=111.8,
                        net_pnl_usd=-16.0, orig_stop_px=109.25),
                  Trade(side="long", entry_time=t2, entry_px=110.0, size=1, stop_px=108.6, target_px=113,
                        net_pnl_usd=-20.0, orig_stop_px=108.6)]
    r = T.risk_numbers(run)
    assert r["worst_loss_usd"] == -20.0
    assert r["worst_loss_pct"] == -25.0                    # -16 of 64, worse than -20 of 100
    assert r["max_stop_liq_frac"] == pytest.approx(0.5, abs=0.01)
    assert r["weekend_opened"] == 1                        # Saturday noon


# ── the 2026-11-02 long ────────────────────────────────────────────────────

ENTRY_BAR = ms(2026, 11, 2, 3, 45)
# xyz:BRENTOIL 15m, 2026-11-02 03:45Z to 08:45Z (public candleSnapshot)
NOV2 = [(109.99, 110.03, 109.64, 109.70), (109.68, 109.74, 109.63, 109.67), (109.65, 109.92, 109.60, 109.86),
        (109.85, 110.09, 109.83, 110.01), (110.01, 110.05, 109.91, 109.92), (109.91, 109.91, 109.74, 109.75),
        (109.75, 109.83, 109.61, 109.75), (109.72, 109.74, 109.40, 109.54), (109.53, 109.86, 109.53, 109.78),
        (109.77, 110.11, 109.76, 110.01), (110.00, 110.14, 109.97, 110.01), (109.98, 110.23, 109.91, 110.23),
        (110.22, 110.32, 110.11, 110.18), (110.16, 110.24, 109.98, 110.03), (110.03, 110.15, 109.49, 109.94),
        (109.92, 110.50, 109.89, 110.44), (110.43, 110.50, 110.06, 110.26), (110.24, 110.36, 109.62, 109.65),
        (109.64, 109.93, 109.47, 109.49), (109.53, 109.61, 108.92, 109.35), (109.33, 109.36, 108.97, 109.27)]


def _nov2():
    c15 = [Candle(t=ENTRY_BAR + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(NOV2)]
    # 26 closed 1h bars before 03:45Z; ATR14 0.5; the 24h range 110.14 to 112.39
    first = ms(2026, 11, 2, 3) - 26 * HOUR_MS
    c1h = [Candle(t=first + i * HOUR_MS, o=111.0, h=111.25, l=110.75, c=111.0, v=1.0) for i in range(26)]
    c1h[10] = Candle(t=c1h[10].t, o=111.0, h=112.39, l=110.9, c=111.0, v=1.0)
    c1h[20] = Candle(t=c1h[20].t, o=111.0, h=111.1, l=110.14, c=111.0, v=1.0)
    return c15, c1h


def test_todays_long_live_stop_is_hit_on_the_0830_bar():
    c15, c1h = _nov2()
    out = T.todays_long(c15, c1h, {"live": {}}, _base())
    r = out["variants"]["live"]
    assert r["stop_px"] == pytest.approx(109.25) and r["target_px"] == pytest.approx(112.39 * 0.995)
    assert r["result"] == "stop" and r["exit"] == "2026-11-02 08:30Z"
    assert r["r_at_entry"] == pytest.approx(2.44, abs=0.01)
    assert r["pnl_usd"] == pytest.approx((109.25 - 110.0099) * 42.36, abs=0.01)
    assert out["low_after_entry"] == 108.92


def test_todays_long_with_every_wider_buffer():
    """No wider buffer survives: 0.80 to 0.90 lose more on the same bar,
    0.95 and up fail the 2R gate (the 108.92 low needed a 1.09 buffer, 1.69R),
    1.5 fails the 20x line too."""
    c15, c1h = _nov2()
    named = {k: v for k, v in T.buffer_variants().items() if k.startswith(("live", "fixed_", "pct_"))}
    out = T.todays_long(c15, c1h, named, _base())["variants"]
    live = out["live"]["pnl_usd"]
    for k in ("fixed_0.80", "fixed_0.85", "fixed_0.90", "pct_0.8"):
        assert out[k]["result"] == "stop" and out[k]["exit"] == "2026-11-02 08:30Z", k
        assert out[k]["pnl_usd"] < live, k
    for k in ("fixed_0.95", "fixed_1.00", "fixed_1.10", "fixed_1.25", "pct_0.9", "pct_1.0", "pct_1.2"):
        assert out[k]["result"] == "refused (min_r)" and out[k]["pnl_usd"] == 0.0, k
        assert out[k]["r_at_entry"] < 2.0, k
    assert out["fixed_1.50"]["result"] == "refused (leverage)" and out["fixed_1.50"]["stop_liq_frac"] > 0.5
    assert out["fixed_0.50"]["result"] == "stop" and out["fixed_0.50"]["exit"] == "2026-11-02 05:30Z"
    assert out["pct_0.6"] == out["live"]                    # 0.6% of 110 is 0.66: the 0.75 stays


def test_todays_long_a_surviving_stop_exits_on_the_flip():
    """A 1.1 buffer at 10x passes both gates (2R needs min_r lowered here) and
    sits under the 108.92 low; the 08:42Z short thesis closes it at the
    08:45Z open."""
    c15, c1h = _nov2()
    out = T.todays_long(c15, c1h, {"wide": {"stop_buffer": 1.1}}, _base(min_r=1.5))["variants"]["wide"]
    assert out["result"] == "direction_flip" and out["exit"] == "2026-11-02 08:45Z" and out["exit_px"] == 109.33


def test_flip_direction():
    d = T.flip_direction("long", 109.55, 1000)
    assert d(999) == Direction(side="long", invalidation_px=109.55) and d(1000).side == "short"
    assert T.flip_direction("short", None, None)(10 ** 13).side == "short"


# ── the Sat 10-31 long under weekend locks ────────────────────────────────

def test_replay_locked_matches_replay_position_without_the_new_options():
    bars = [Candle(t=ms(2026, 11, 3) + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(
        [(111.5, 111.8, 111.4, 111.7), (111.7, 112.1, 111.6, 112.0), (112.0, 112.05, 111.5, 111.55),
         (111.55, 111.6, 111.0, 111.1)])]
    for ov in ({"lock_trigger_r": 1.0, "lock_r": 0.0}, {"lock_trigger_r": 1.0, "lock_r": 0.5, "lock_trail": True},
               {"lock_trigger_r": 1.0, "lock_r": 0.5, "lock_confirm_close": False}, {}):
        p = _base(**ov)
        assert T.replay_locked("long", 111.48, 111.02, 113.66, bars, p) == \
            replay_position("long", 111.48, 111.02, 113.66, bars, p), ov


def test_replay_locked_skips_weekday_bars_with_weekend_only():
    # Tuesday bars: the weekend-only lock never arms, the plain lock does
    bars = [Candle(t=ms(2026, 11, 3) + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(
        [(111.5, 112.1, 111.4, 112.0), (112.0, 112.05, 111.4, 111.45), (111.45, 111.5, 111.0, 111.1)])]
    plain = T.replay_locked("long", 111.48, 111.02, 113.66, bars, _base(lock_trigger_r=1.0, lock_r=0.0))
    assert plain["reason"] == "lock"
    wk = T.replay_locked("long", 111.48, 111.02, 113.66, bars,
                         _base(lock_trigger_r=1.0, lock_r=0.0, lock_weekend_only=True))
    assert wk["reason"] == "stop" and wk["best_r"] == 0.0


def test_replay_locked_matches_the_sim_for_a_weekend_lock():
    """A Saturday market long opened by the sim and the same position walked
    by replay_locked close at the same place."""
    sat = ms(2026, 11, 7, 12)
    first = sat - 24 * HOUR_MS
    c1h = [Candle(t=first + i * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(24)]
    rows = [(100.3, 100.4, 100.2, 100.3), (100.3, 100.9, 100.3, 100.8), (100.8, 100.85, 100.6, 100.7),
            (100.6, 100.65, 100.2, 100.25)]
    c15 = [Candle(t=sat + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]
    p = _base(stop_buffer=0.2, target_offset_frac=0.0, slippage=0.0, lock_trigger_r=1.0, lock_r=0.0,
              lock_weekend_only=True)
    run = simulate(c15, c1h, lambda t: Direction(side="long"), p)
    tr = run.trades[0]
    r = T.replay_locked("long", tr.entry_px, tr.orig_stop_px, tr.target_px, c15[1:], p)
    assert tr.exit_reason == r["reason"] == "lock"
    assert tr.exit_px == pytest.approx(r["exit_px"]) and tr.exit_time == r["exit_time"]


def test_weekend_long_banks_or_closes_by_hand():
    fill = ms(2026, 10, 31, 16, 15)
    rows = [(111.5, 112.0, 111.4, 111.9), (111.9, 112.5, 111.8, 112.4), (112.4, 112.45, 111.6, 111.7),
            (111.7, 111.8, 111.6, 111.7)]
    c15 = [Candle(t=fill + (i + 1) * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]
    named = {"live": {}, "wk_lock_t1_l0.5": {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.5}}
    out = T.weekend_long(c15, named, _base())
    assert out["all_in_window"] and out["bars"] == 4
    assert out["variants"]["live"]["net_usd"] == 7.83 and out["variants"]["live"]["reason"].startswith("closed")
    lk = out["variants"]["wk_lock_t1_l0.5"]
    assert lk["reason"] == "lock" and lk["exit_px"] == pytest.approx(111.48 + 0.5 * 0.46)
    size = 40.73
    gross = 0.5 * 0.46 * size
    fees = 0.00045 * 111.48 * size + 0.00045 * (111.48 + 0.23) * size
    assert lk["net_usd"] == pytest.approx(round(gross - fees, 2))


def test_weekend_trade_rows():
    rows = T.weekend_trade_rows()
    assert [r["entry_in_window"] for r in rows] == [True, False, False, False]
    assert [r["open_at_window_start"] for r in rows] == [False, False, False, False]
    assert rows[0]["entry"] == "2026-10-31 16:18Z" and rows[0]["net_usd"] == 7.83
    # a Friday-afternoon position held past the close counts as open at the window start
    fri = T.weekend_trade_rows([{"desk": "oil", "side": "long", "entry_utc": (2026, 11, 6, 18, 0),
                                 "exit_utc": (2026, 11, 7, 2, 0), "net_usd": 1.0}])
    assert fri[0]["open_at_window_start"] and not fri[0]["entry_in_window"]


# ── runs + CSV rows ────────────────────────────────────────────────────────

def test_run_dataset_and_csv_rows():
    c15, c1h = _nov2()
    named = {"live": {}, "fixed_0.90": {"stop_buffer": 0.9}}
    r = T.run_dataset(c15, c1h, lambda t: Direction(side="long"), _base(), c15[0].t, c15[-1].t + Q, named,
                      attribute_entries=True)
    assert set(r["variants"]) == set(named) and "vs_live" in r["variants"]["fixed_0.90"]
    for s in r["variants"].values():
        assert {"worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "weekend_opened"} <= set(s)
    rows = T.summary_csv_rows("A", {"ds": r})
    assert [x["variant"] for x in rows] == ["live", "fixed_0.90"] and set(rows[0]) == set(T.SUMMARY_FIELDS)
    for e in T.entry_csv_rows({"ds": r}):
        assert set(e) == set(T.ENTRY_FIELDS)


# ── 2026-11-05: the live rule IS wk_flat ─────────────────────────────────────

def _weekend_bars():
    """A Friday-afternoon long carried into the window: 1h warmup with the
    24h range 100 to 104, a market long at 100.3 on the 20:45Z bar, then
    Saturday bars that never reach the stop or the target."""
    fri = ms(2026, 11, 6, 20, 45)
    c1h = [Candle(t=fri - (26 - i) * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(26)]
    rows = [(100.3, 100.4, 100.2, 100.3), (100.3, 100.9, 100.3, 100.8), (100.8, 100.85, 100.6, 100.7),
            (100.7, 100.75, 100.5, 100.6), (100.6, 100.65, 100.4, 100.5)]
    c15 = [Candle(t=fri + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]
    return c15, c1h


def test_live_baseline_equals_wk_flat_and_differs_from_24_7():
    """After the 2026-11-05 adoption `live` and `wk_flat` are the same
    Params and the same run (the eval's "tie" is by construction); `24/7`
    is the old rule and keeps the position through the weekend."""
    c15, c1h = _weekend_bars()
    base = dataclasses.replace(live_params(), starting_equity=64.0, stop_buffer=0.2, slippage=0.0)
    named = T.weekend_variants({}, "")
    named = {("live" if k == "24/7" else k): v for k, v in named.items()}
    assert dataclasses.replace(base, **named["wk_flat"]) == base       # the live rule is wk_flat
    r = T.run_dataset(c15, c1h, lambda t: Direction(side="long"), base, c15[0].t, c15[-1].t + Q,
                      {"live": {}, "wk_flat": named["wk_flat"], "24/7": T.weekend_variants({})["24/7"]})
    live, flat, always = (r["variants"][k] for k in ("live", "wk_flat", "24/7"))
    assert live == flat
    assert live["trades"] == 1 and always["trades"] == 0               # closed at 21:00Z vs still open
    run = simulate(c15, c1h, lambda t: Direction(side="long"), base)
    assert run.trades[0].exit_reason == "weekend" and run.trades[0].exit_time == ms(2026, 11, 6, 21)
    assert run.open_position_at_end is None
    old = simulate(c15, c1h, lambda t: Direction(side="long"), dataclasses.replace(base, weekend_off=None))
    assert old.trades == [] and old.open_position_at_end is not None


def test_live_ties_wk_flat_check():
    same = {"variants": {"live": {"final_equity": 1.0}, "wk_flat": {"final_equity": 1.0}}}
    diff = {"variants": {"live": {"final_equity": 1.0}, "wk_flat": {"final_equity": 2.0}}}
    assert T.live_ties_wk_flat({"a": same, "b": same}) == {"pass": True, "per_dataset": {"a": True, "b": True}}
    out = T.live_ties_wk_flat({"a": same, "b": diff})
    assert out["pass"] is False and out["per_dataset"] == {"a": True, "b": False}
    assert T.live_ties_wk_flat({})["pass"] is False
