"""Gate tests for the strategy tournament (services/strategies/evals/strategy_tournament.py).

Offline, deterministic, synthetic hourly candles. Each candidate's signal
logic, the shared engine's mechanics (costs, sizing, stop-first, gaps,
weekend flat, leverage fit), the walk-forward selector, the pass bar and
the JSON shape the eval prints.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import pytest

from pathiel.models.types import Candle
from services.strategies import levels as L
from services.strategies.evals import strategy_tournament as T
from services.strategies.weekend import weekend_closed

H = T.HOUR_MS
# Monday 2026-05-04 00:00Z: a week with the oil weekend (Fri 20:00Z to Sun 22:00Z in US summer) inside it.
MONDAY = int(datetime(2026, 5, 4, tzinfo=timezone.utc).timestamp() * 1000)
# A start far from any weekend for the signal tests: features need up to 240 bars,
# so most tests run with a small start_i and never reach a Friday evening.
T0 = int(datetime(2026, 5, 11, tzinfo=timezone.utc).timestamp() * 1000)   # Monday 00:00Z


def bars(closes: Sequence[float], spread: float = 0.2, t0: int = T0, highs: Optional[Sequence[float]] = None,
         lows: Optional[Sequence[float]] = None, opens: Optional[Sequence[float]] = None) -> List[Candle]:
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else (closes[i - 1] if i else c)
        h = highs[i] if highs else max(o, c) + spread
        lo = lows[i] if lows else min(o, c) - spread
        out.append(Candle(t=t0 + i * H, o=o, h=h, l=lo, c=c, v=1.0))
    return out


def flat(n: int, px: float = 100.0) -> List[float]:
    return [px] * n


# 30 flat hours, a spike to 106 and a slide back to 101: at bar 35 the mid 101 sits in a
# 99.9..106.1 range, stop 99.15 (1.85 risk), target 105.57 (4.57 reward): 2.47R, fits 15x.
DIP = flat(30, 100.0) + [106.0, 104.0, 103.0, 102.0, 101.0]


COSTS = T.Costs()


# ── features ──────────────────────────────────────────────────────────────────

def test_features_use_closed_bars_only_and_match_levels_atr():
    closes = [100 + (i % 7) * 0.3 for i in range(60)]
    b = bars(closes)
    f = T.compute_features(b)
    rows = [{"h": c.h, "l": c.l, "c": c.c} for c in b[:40]]
    assert f.atr[40] == pytest.approx(L.atr(rows, 14))
    assert f.hi24[40] == max(c.h for c in b[16:40]) and f.lo24[40] == min(c.l for c in b[16:40])
    assert f.last_close[40] == b[39].c
    assert f.donch_hi[24][40] == max(c.h for c in b[15:39])      # the 24 bars BEFORE the last closed bar
    assert f.ret[120][40] is None and f.ema[48][40] is None and f.ema[48][50] is not None
    # no lookahead: moving bar 40 and later changes nothing at index 40
    b2 = list(b)
    b2[40] = Candle(t=b[40].t, o=50, h=200, l=10, c=150, v=1)
    f2 = T.compute_features(b2)
    for name in ("atr", "hi24", "lo24", "hi4", "lo4", "last_close", "last_move", "drift4"):
        assert getattr(f, name)[:41] == getattr(f2, name)[:41]
    assert f.atr[41] != f2.atr[41]


# ── A: the live dip-buy, mechanical ──────────────────────────────────────────

def test_a_range_setup_is_the_live_rule():
    s = T.range_setup(1, 101.0, 100.0, 106.0, 0.75, 2.0, T.LIVE_LEV_FIT)
    assert s.side == 1 and s.stop_px == 99.25 and s.target_px == pytest.approx(106 * 0.995)
    assert T.range_setup(1, 99.9, 100.0, 106.0, 0.75, 2.0, T.LIVE_LEV_FIT) is None     # past the edge: no entry
    assert T.range_setup(1, 103.0, 100.0, 106.0, 0.75, 2.0, T.LIVE_LEV_FIT) is None     # under 2R
    m = T.range_setup(-1, 105.0, 100.0, 106.0, 0.75, 2.0, T.LIVE_LEV_FIT)
    assert m.side == -1 and m.stop_px == 106.75 and m.target_px == pytest.approx(100 * 1.005)
    assert T.range_setup(-1, 106.1, 100.0, 106.0, 0.75, 2.0, T.LIVE_LEV_FIT) is None


def test_a_long_buys_the_dip_and_never_shorts():
    b = bars(DIP + flat(30, 101.0), spread=0.1)
    f = T.compute_features(b)
    i = 35
    s = T.make_baseline(1).setup(i, b[i].o, f)
    assert s and s.side == 1 and s.stop_px == pytest.approx(99.9 - 0.75) and s.lev_fit == T.LIVE_LEV_FIT
    assert s.target_px == pytest.approx(106.1 * 0.995)
    assert T.fit_leverage(b[i].o, s.stop_px, COSTS, s.lev_fit) == 15.0
    assert T.make_baseline(-1).setup(i, b[i].o, f) is None


# ── B: trend filter ───────────────────────────────────────────────────────────

def test_b_trend_filter_blocks_the_dip_buy_in_a_downtrend_and_sells_the_rip():
    down = [200 - i * 0.5 for i in range(200)]            # a steady slide: ret120 < 0, close < ema
    b = bars(down, spread=0.1)
    f = T.compute_features(b)
    i = 199
    hi, lo = f.hi24[i], f.lo24[i]
    assert T.make_trend_filter("ret", 120).setup(i, lo + 0.3, f) is None      # a dip is not bought
    s = T.make_trend_filter("ret", 120).setup(i, hi - 0.3, f)                 # the rip is sold
    assert s and s.side == -1 and s.stop_px == pytest.approx(hi + 0.75)
    assert T.make_trend_filter("ema", 48).setup(i, hi - 0.3, f).side == -1
    up = [100 + i * 0.5 for i in range(200)]
    fu = T.compute_features(bars(up, spread=0.1))
    assert T.make_trend_filter("ret", 120).setup(199, fu.hi24[199] - 0.3, fu) is None
    assert T.make_trend_filter("ema", 120).setup(199, fu.lo24[199] + 0.3, fu).side == 1


# ── C: Donchian breakout with an ATR trail ────────────────────────────────────

def test_c_donchian_fires_on_a_close_over_the_prior_high_then_trails():
    closes = flat(40, 100.0) + [101.0] + [101.0 + k * 0.5 for k in range(1, 20)] + flat(10, 110.0)
    b = bars(closes, spread=0.2)
    f = T.compute_features(b)
    strat = T.make_donchian(24)
    assert strat.setup(40, b[40].o, f) is None               # no break yet
    s = strat.setup(41, b[41].o, f)                            # bar 40 closed over the prior 24h high (100.2)
    assert s and s.side == 1 and s.target_px is None and s.trail_atr == 3.0
    assert s.stop_px == pytest.approx(b[41].o - 2.0 * f.atr[41])
    assert T.make_donchian(96).setup(41, b[41].o, f) is None   # not enough history for 96
    res = T.run_strategy(b, strat, COSTS, start_i=30, feats=f)
    tr = res.trades[0]
    assert tr.side == 1 and tr.stop_px > tr.entry_px           # the trail ratcheted above the entry
    assert tr.reason in ("stop", "end") and tr.pnl > 0


def test_c_donchian_short_mirror():
    closes = flat(40, 100.0) + [99.0] + [99.0 - k * 0.5 for k in range(1, 10)]
    b = bars(closes, spread=0.2)
    f = T.compute_features(b)
    s = T.make_donchian(24).setup(41, b[41].o, f)
    assert s and s.side == -1 and s.stop_px == pytest.approx(b[41].o + 2.0 * f.atr[41])


# ── D: momentum after the price alarms ────────────────────────────────────────

def test_d_alarm_direction_shock_and_drift():
    closes = flat(30, 100.0) + [101.2]                 # a +1.2% bar: shock up
    f = T.compute_features(bars(closes, spread=0.05))
    assert T.alarm_direction(31, f) == 1
    closes = flat(30, 100.0) + [100.5, 100.9, 101.4, 101.8]   # +1.8% over 4 closes, no single 1% bar
    f = T.compute_features(bars(closes, spread=0.05))
    assert all(abs(x) < T.SHOCK for x in f.last_move[31:35] if x is not None)
    assert T.alarm_direction(34, f) == 1
    closes = flat(30, 100.0) + [98.9]
    assert T.alarm_direction(31, T.compute_features(bars(closes, spread=0.05))) == -1
    assert T.alarm_direction(31, T.compute_features(bars(flat(31, 100.0), spread=0.05))) == 0


def test_d_trades_with_the_move_stop_beyond_the_4h_extreme_and_times_out():
    closes = flat(30, 100.0) + [98.8] + flat(40, 98.8)
    b = bars(closes, spread=0.1)
    f = T.compute_features(b)
    s = T.make_alarm_momentum(2.0).setup(31, b[31].o, f)
    assert s and s.side == -1 and s.stop_px == pytest.approx(max(f.hi4[31], b[31].o) + 0.75)
    assert s.target_px == pytest.approx(b[31].o - 2.0 * (s.stop_px - b[31].o)) and s.max_hold_bars == 24
    res = T.run_strategy(b, T.make_alarm_momentum(2.0), COSTS, start_i=20, feats=f)
    assert res.trades and res.trades[0].reason == "time"
    assert (res.trades[0].exit_t - res.trades[0].entry_t) == 24 * H


# ── E: mean reversion, wider stop, smaller size ──────────────────────────────

def test_e_wider_atr_stop_sizes_smaller_than_the_baseline():
    b = bars(DIP + flat(10, 101.0), spread=0.1)
    f = T.compute_features(b)
    i = 35
    lo, hi, a = f.lo24[i], f.hi24[i], f.atr[i]
    e = T.make_mean_reversion(2.0).setup(i, b[i].o, f)
    assert e and e.side == 1 and e.stop_px == pytest.approx(lo - 2.0 * a) and e.lev_fit == T.WIDE_LEV_FIT
    base = T.make_baseline(1).setup(i, b[i].o, f)
    _, n_base = T.size_for(182.09, b[i].o, base.stop_px, 20.0, COSTS)
    _, n_wide = T.size_for(182.09, b[i].o, e.stop_px, 20.0, COSTS)
    assert n_wide < n_base
    assert T.make_mean_reversion(2.0).setup(i, (lo + hi) / 2, f) is None       # mid-range: nothing
    top = T.make_mean_reversion(1.5).setup(i, hi - 0.05, f)
    assert top and top.side == -1 and top.stop_px == pytest.approx(hi + 1.5 * a)


# ── F: time-series momentum ───────────────────────────────────────────────────

def test_f_tsmom_holds_the_return_sign_and_flips_at_the_close_that_changes_it():
    up = [100 + i * 0.1 for i in range(130)]
    down = [up[-1] - (k + 1) * 0.5 for k in range(140)]
    b = bars(up + down, spread=0.05, t0=T0 - 121 * H)      # bar 121 opens Monday 00:00Z: no weekend in the way
    f = T.compute_features(b)
    strat = T.make_tsmom(120)
    s = strat.setup(125, b[125].o, f)
    assert s and s.side == 1 and s.target_px is None and s.stop_px == pytest.approx(b[125].o - 2 * f.atr[125])
    res = T.run_strategy(b, strat, COSTS, start_i=121, feats=f)
    reasons = [t.reason for t in res.trades]
    assert "flip" in reasons
    flip = next(t for t in res.trades if t.reason == "flip")
    nxt = res.trades[res.trades.index(flip) + 1]
    assert flip.side == 1 and nxt.side == -1 and nxt.entry_t == flip.exit_t   # the short opens at the next bar


def test_g_flat_never_trades():
    res = T.run_strategy(bars(flat(300, 100.0)), T.FLAT, COSTS, start_i=240)
    assert res.trades == [] and res.final_equity == COSTS.start_equity


# ── engine mechanics ──────────────────────────────────────────────────────────

def _one_long(hi_after: float, lo_after: float, open_after: float = 101.0):
    """A dip-buy long at 101 on a quiet entry bar (DIP), then one bar with the given extremes."""
    b = bars(DIP, spread=0.1)
    b.append(Candle(t=b[-1].t + H, o=101.0, h=101.1, l=100.9, c=101.0, v=1))       # the entry bar
    b.append(Candle(t=b[-1].t + H, o=open_after, h=hi_after, l=lo_after, c=open_after, v=1))
    b += bars(flat(5, open_after), spread=0.0, t0=b[-1].t + H)
    return b


def test_engine_stop_first_when_stop_and_target_touch_in_one_bar():
    b = _one_long(hi_after=110.0, lo_after=98.0)
    res = T.run_strategy(b, T.make_baseline(1), COSTS, start_i=35)
    tr = res.trades[0]
    assert tr.reason == "stop" and tr.r == pytest.approx(-1.0, abs=0.01)
    assert tr.frac == pytest.approx(-COSTS.risk_frac, abs=0.0005)      # a stop costs 6% of equity, fees in
    assert tr.exit_px == pytest.approx(tr.stop_px * (1 - COSTS.slippage))


def test_engine_target_pays_about_2r_at_the_taker_fee_and_a_gap_fills_at_the_open():
    b = _one_long(hi_after=110.0, lo_after=100.5)
    tr = T.run_strategy(b, T.make_baseline(1), COSTS, start_i=35).trades[0]
    assert tr.reason == "target" and tr.exit_px == tr.target_px and 2.1 < tr.r < 2.47   # 2.47R less fees
    b = _one_long(hi_after=98.5, lo_after=97.0, open_after=98.0)       # opens through the 99.15 stop
    tr = T.run_strategy(b, T.make_baseline(1), COSTS, start_i=35).trades[0]
    assert tr.reason == "stop" and tr.exit_px == pytest.approx(98.0 * (1 - COSTS.slippage)) and tr.r < -1.0


def test_engine_weekend_flattens_and_takes_no_entry():
    # a dip-buy setup every bar of a week starting Monday: the Friday 20:00Z bar closes it
    b = bars(DIP + flat(24 * 7, 101.0), spread=0.1, t0=MONDAY - 35 * H)
    res = T.run_strategy(b, T.make_baseline(1), COSTS, start_i=35)
    wk = [t for t in res.trades if t.reason == "weekend"]
    assert len(wk) == 1 and weekend_closed(wk[0].exit_t) and not weekend_closed(wk[0].exit_t - H)
    for t in res.trades:
        assert not weekend_closed(t.entry_t)


def test_fit_leverage_and_size():
    assert T.fit_leverage(100.0, 99.0, COSTS, T.LIVE_LEV_FIT) == 20.0          # 1% stop fits 20x
    assert T.fit_leverage(100.0, 98.0, COSTS, T.LIVE_LEV_FIT) == 15.0          # 2% stop needs 15x
    assert T.fit_leverage(100.0, 97.0, COSTS, T.LIVE_LEV_FIT) is None          # live refuses
    assert T.fit_leverage(100.0, 97.0, COSTS, T.WIDE_LEV_FIT) == 10.0          # the wide set goes on down
    assert T.fit_leverage(100.0, 96.0, COSTS, T.WIDE_LEV_FIT) == 8.0
    assert T.fit_leverage(100.0, 85.0, COSTS, T.WIDE_LEV_FIT) is None
    size, notional = T.size_for(182.09, 100.0, 99.0, 20.0, COSTS)
    loss_at_stop = size * 1.0 + notional * (2 * COSTS.taker_fee + COSTS.slippage)
    assert loss_at_stop == pytest.approx(0.06 * 182.09)
    assert T.size_for(182.09, 100.0, 99.9, 1.0, COSTS)[1] == pytest.approx(0.95 * 182.09)   # margin cap
    assert T.size_for(1.0, 100.0, 99.0, 20.0, COSTS) == (0.0, 0.0)              # under the venue minimum


# ── selector ──────────────────────────────────────────────────────────────────

def _tr(name, entry_day, exit_day, frac, side=1):
    return T.Trade(name, side, entry_day * T.DAY_MS, exit_day * T.DAY_MS, 100, 100, 99, None, 20, 100,
                   frac * 100, frac, min(0.0, frac), frac / 0.06, "target")


def test_selector_picks_the_best_trailing_candidate_out_of_sample():
    runs = {
        "X": [_tr("X", 1, 2, 0.10), _tr("X", 8, 9, -0.06), _tr("X", 15, 16, 0.10)],
        "Y": [_tr("Y", 1, 2, 0.02), _tr("Y", 8, 9, 0.12), _tr("Y", 15, 16, -0.06)],
    }
    out = T.walk_forward(runs, 0, 21 * T.DAY_MS, 7, 7, 100.0, ["X", "Y"])
    picks = [p["pick"] for p in out["picks"]]
    assert picks == ["X", "Y"]                      # week 2 follows X (best in week 1), week 3 follows Y
    assert [r["strategy"] for r in out["trade_rows"]] == ["X", "Y"]
    assert out["net"] == pytest.approx(100 * (1 - 0.06) * (1 - 0.06) - 100, abs=0.01)


def test_selector_goes_flat_with_no_positive_history_and_has_no_lookahead():
    runs = {"X": [_tr("X", 1, 2, -0.06), _tr("X", 8, 9, 0.50)], "Y": [_tr("Y", 1, 2, -0.02)]}
    out = T.walk_forward(runs, 0, 14 * T.DAY_MS, 7, 7, 100.0, ["X", "Y"])
    assert out["picks"][0]["pick"] == "G_flat" and out["trades"] == 0      # X's week-2 win cannot pick X for week 2
    runs["X"][1] = _tr("X", 8, 9, -0.50)                                   # changing the future changes no pick
    out2 = T.walk_forward(runs, 0, 14 * T.DAY_MS, 7, 7, 100.0, ["X", "Y"])
    assert [p["pick"] for p in out2["picks"]] == [p["pick"] for p in out["picks"]]


def test_selector_holds_one_position_across_a_rebalance():
    runs = {"X": [_tr("X", 1, 2, 0.10), _tr("X", 13, 16, 0.10)],         # X's trade straddles day 14
            "Y": [_tr("Y", 1, 2, 0.05), _tr("Y", 10, 11, 0.30), _tr("Y", 14.5, 15, 0.05), _tr("Y", 17, 18, 0.05)]}
    out = T.walk_forward(runs, 0, 21 * T.DAY_MS, 7, 7, 100.0, ["X", "Y"])
    rows = out["trade_rows"]
    assert [r["strategy"] for r in rows] == ["X", "Y"]             # Y's day-14.5 entry waits: X is still open
    assert rows[0]["exit"] == T._iso(16 * T.DAY_MS) and rows[1]["entry"] == T._iso(17 * T.DAY_MS)


def test_equal_split_sums_the_slices():
    runs = {"X": [_tr("X", 1, 2, 0.10)], "Y": [_tr("Y", 3, 4, -0.06)]}
    out = T.equal_split(runs, 0, 7 * T.DAY_MS, 100.0, ["X", "Y"])
    assert out["net"] == pytest.approx(50 * 0.10 - 50 * 0.06) and out["trades"] == 2 and out["slices"] == 2


# ── metrics and the pass bar ──────────────────────────────────────────────────

def test_thirds_sum_to_net_and_drawdown_reads_the_trough():
    trades = [_tr("X", 1, 2, 0.10), _tr("X", 11, 12, -0.06), _tr("X", 21, 22, 0.10)]
    row = T.summarize("X", trades, 0, 30 * T.DAY_MS, 100.0)
    assert sum(row["thirds"]) == pytest.approx(row["net"], abs=0.02) and row["trades"] == 3 and row["wins"] == 2
    assert row["max_dd"] == pytest.approx(0.06, abs=1e-6)
    assert T.max_drawdown([(0, 100), (1, 120), (2, 90), (3, 130)]) == pytest.approx(0.25)


def test_pass_bar_checks_every_leg():
    good = {"net": 50.0, "thirds": [10.0, -5.0, 45.0], "max_dd": 0.2, "trades": 20}
    assert T.passes(good, -10.0, 1.0)["pass"]
    assert not T.passes(good, 60.0, 1.0)["pass"]                                   # under the baseline
    assert not T.passes({**good, "net": -1.0}, -10.0, 1.0)["pass"]
    assert not T.passes({**good, "thirds": [60.0, -5.0, -5.0]}, -10.0, 1.0)["checks"]["thirds_2_of_3"]
    assert not T.passes({**good, "max_dd": 0.35}, -10.0, 1.0)["checks"]["max_dd_under"]
    assert not T.passes({**good, "trades": 14}, -10.0, 1.0)["checks"]["min_trades"]
    assert not T.passes(good, -10.0, -0.01)["checks"]["wti_not_negative"]
    assert not T.passes(good, -10.0, None)["checks"]["wti_not_negative"]


# ── the eval end to end, offline ──────────────────────────────────────────────

def _synthetic_market(n: int = 700) -> List[Candle]:
    closes = [100 + 6 * ((i // 40) % 2) + (i % 5) * 0.3 for i in range(n)]       # a square wave with wiggle
    return bars(closes, spread=0.2, t0=MONDAY)


def test_tournament_json_shape_and_exit_code(monkeypatch, tmp_path, capsys):
    b = _synthetic_market()
    out = T.tournament(b, b, T.Costs(), start_i=T.WARMUP_BARS)
    names = [r["name"] for r in out["candidates"]]
    assert set(names) == {s.name for s in T.candidates()} and out["baseline"] == "A_long"
    assert out["candidates"] == sorted(out["candidates"], key=lambda r: -r["net"])
    assert len(out["selectors"]) == 4 and out["equal_split"]["slices"] == len(names) - 1
    assert out["hindsight_best"]["label"] == "hindsight, not tradeable"
    for r in out["candidates"]:
        assert set(r["checks"]) == {"net_gt_baseline", "net_positive", "thirds_2_of_3", "max_dd_under",
                                    "min_trades", "wti_not_negative"}
        assert r["wti"]["trades"] == r["trades"]            # the same bars twice: the same trades
    assert T.markdown_tables(out).startswith("| Candidate |")
    monkeypatch.setattr(T, "load_bars", lambda coin, cache_dir, fetch_page=None: b)
    code = T.main(["--cache-dir", str(tmp_path), "--json", str(tmp_path / "r.json")])
    assert code == (0 if out["any_pass"] else 1)
    printed = json.loads(capsys.readouterr().out)
    assert "trades" not in printed and printed["window"]["days"] > 0
    assert json.load(open(tmp_path / "r.json"))["trades"]["A_long"] is not None
