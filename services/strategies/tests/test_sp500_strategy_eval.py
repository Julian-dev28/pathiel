"""Gate tests for the S&P 500 strategy eval's pure parts
(services/strategies/evals/sp500_strategy_test.py,
docs/strategy/SP500_BACKTEST_2026-11-19.md). Synthetic candles only: no
network, no snapshot, no account."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.evals import sp500_strategy_test as S

H = S.HOUR_MS
T0 = 1_790_000_000_000 - (1_790_000_000_000 % H)     # an exact UTC hour
NO_COST = S.Costs(fee=0.0, slip=0.0)
NO_FUND = ([], [])


def bar(i, o, h, l, c, step=H):
    return Candle(t=T0 + i * step, o=o, h=h, l=l, c=c, v=1.0)


def flat_bars(n, px=7000.0, rng=10.0):
    """n hourly bars oscillating inside [px - rng, px + rng], closing at px."""
    return [bar(i, px, px + rng, px - rng, px) for i in range(n)]


def always(side, stop, tp, flat_at_ms=None):
    def rule(i, ticks, bars, st, flat=True):
        return S.Order(side, stop, tp, flat_at_ms) if flat else None
    return rule


def once(side, stop, tp, at):
    def rule(i, ticks, bars, st, flat=True):
        return S.Order(side, stop, tp) if flat and i == at else None
    return rule


# ── the 50x question ─────────────────────────────────────────────────────

def test_at_50x_liquidation_is_1_percent_away_and_the_stop_may_use_half():
    b = S.leverage_budget(50.0)
    assert b["liq_dist_pct"] == pytest.approx(1.010, abs=0.001)
    assert b["max_stop_pct"] == pytest.approx(0.505, abs=0.001)
    assert S.leverage_budget(20.0)["max_stop_pct"] == pytest.approx(2.020, abs=0.001)
    # the venue's one margin tier: maintenance margin 1 / (2 x 50) = 1%
    assert S.VENUE_MAX_LEVERAGE == 50.0


def test_the_oil_formula_can_never_trade_at_50x():
    """Oil's 0.8% stop floor is wider than the whole 0.505% stop budget."""
    assert NT.STOP_BUFFER_FLOOR_FRAC * 100 > S.leverage_budget(50.0)["max_stop_pct"]
    bars = flat_bars(60) + [bar(60, 7000, 7001, 6990.5, 6991)]     # a pullback to the 24h low
    ticks = S.build_ticks(bars, H, bars)
    rule = S.make_rule(S.oil_as_is(50.0))
    assert all(rule(i, ticks, bars, {}) is None for i, t in enumerate(ticks) if t is not None)


# ── levels per tick ──────────────────────────────────────────────────────

def test_ticks_read_only_closed_1h_bars_and_wait_for_25_of_them():
    bars = flat_bars(30)
    bars[29] = bar(29, 7000, 7500, 6500, 7000)          # the newest bar spikes
    ticks = S.build_ticks(bars, H, bars)
    assert ticks[23] is None and ticks[24] is not None  # 25 closed bars at bar 24's close
    assert ticks[28].high24 == 7010 and ticks[28].low24 == 6990
    assert ticks[29].high24 == 7500                     # closed at its own tick, not before
    # a 15m tick inside hour 29 must not see hour 29's high yet
    q = [Candle(t=T0 + 29 * H + k * 900_000, o=7000, h=7001, l=6999, c=7000, v=1) for k in range(4)]
    t15 = S.build_ticks(q, 900_000, bars)
    assert [t.high24 for t in t15] == [7010, 7010, 7010, 7500]


def test_atr_matches_the_levels_script():
    from services.strategies.levels import atr
    bars = [bar(i, 7000 + i, 7000 + i + 3 + (i % 5), 7000 + i - 2 - (i % 3), 7000 + i + 1) for i in range(40)]
    rows = [{"h": b.h, "l": b.l, "c": b.c} for b in bars]
    tk = S.build_ticks(bars, H, bars)[39]
    assert tk.atr == pytest.approx(atr(rows, 14))


def test_sides_for():
    tk = S.build_ticks(flat_bars(130), H, flat_bars(130))[129]
    assert S.sides_for("both", tk) == ("long", "short")
    assert S.sides_for("short", tk) == ("short",)
    assert S.sides_for("trend120", tk) == ()             # a flat return trades nothing
    up = [bar(i, 7000 + i, 7001 + i, 6999 + i, 7000 + i) for i in range(130)]
    assert S.sides_for("trend120", S.build_ticks(up, H, up)[129]) == ("long",)
    assert S.sides_for("trend24", S.build_ticks(up, H, up)[30]) == ("long",)
    assert S.sides_for("trend120", S.build_ticks(up, H, up)[30]) == ()   # under 121 closed bars


# ── the simulator ────────────────────────────────────────────────────────

def _run(path, rule, **kw):
    bars = flat_bars(30) + path
    ticks = S.build_ticks(bars, H, bars)
    return S.simulate(ticks, bars, H, rule, kw.pop("costs", NO_COST), kw.pop("fund", NO_FUND), **kw)


def test_stop_is_checked_before_the_target_inside_one_bar():
    tr = _run([bar(30, 7000, 7100, 6900, 7000)], once("long", 6950.0, 7050.0, 29))
    assert [(t.reason, t.r) for t in tr] == [("stop", -1.0)]


def test_a_bar_that_opens_through_the_stop_fills_at_its_open():
    tr = _run([bar(30, 6900, 6910, 6890, 6900)], once("long", 6950.0, 7200.0, 29))
    assert tr[0].reason == "stop" and tr[0].exit_px == 6900 and tr[0].r == pytest.approx(-2.0)


def test_a_target_needs_a_trade_through_it():
    touch = _run([bar(30, 7000, 7050, 6990, 7040), bar(31, 7040, 7049, 7000, 7010)], once("long", 6950.0, 7050.0, 29))
    assert touch == []                                   # touched 7050, never through: still open at the end
    through = _run([bar(30, 7000, 7051, 6990, 7040)], once("long", 6950.0, 7050.0, 29))
    assert [(t.reason, t.r) for t in through] == [("target", 1.0)]


def test_a_short_mirrors_a_long():
    tr = _run([bar(30, 7000, 7005, 6890, 6900)], once("short", 7050.0, 6900.0, 29))
    assert [(t.reason, t.r) for t in tr] == [("target", 2.0)]


def test_costs_fees_slippage_and_funding_all_cut_r():
    path = [bar(30, 7000, 7005, 6940, 6950)]
    free = _run(path, once("long", 6950.0, 7200.0, 29))[0].r
    paid = _run(path, once("long", 6950.0, 7200.0, 29), costs=S.Costs(fee=0.0005, slip=0.001))[0].r
    assert free == -1.0 and paid < free
    # entry slip 7, stop slip 6.95, fees 0.0005 x (7007 + 6943.05), over a 50 point risk
    assert paid == pytest.approx(-(50 + 7 + 6.95 + 0.0005 * (7007 + 6943.05)) / 50)
    fund = S.funding_index([{"time": T0 + 30 * H + 1, "fundingRate": "0.001"}])
    long_r = _run(path, once("long", 6950.0, 7200.0, 29), fund=fund)[0].r
    short_r = _run([bar(30, 7000, 7060, 6995, 7050)], once("short", 7050.0, 6800.0, 29), fund=fund)[0].r
    assert long_r == pytest.approx(-1.0 - 0.001 * 7000 / 50)       # a long pays a positive rate
    assert short_r == pytest.approx(-1.0 + 0.001 * 7000 / 50)      # a short earns it


def test_funding_between_is_the_half_open_window():
    fund = S.funding_index([{"time": 10, "fundingRate": "1"}, {"time": 20, "fundingRate": "2"},
                            {"time": 30, "fundingRate": "4"}])
    assert S.funding_between(fund, 10, 30) == 6.0
    assert S.funding_between(fund, 0, 10) == 1.0
    assert S.funding_between(fund, 30, 99) == 0.0


def test_one_position_at_a_time_and_reentry_after_the_exit_bar():
    path = [bar(30, 7000, 7005, 6940, 6950), bar(31, 6950, 7001, 6945, 7000), bar(32, 7000, 7005, 6940, 6950)]
    tr = _run(path, always("long", 6945.0, 7200.0))
    # the entry at bar 24's close stays open through the flat bars and stops at 30;
    # re-enters at 30's close (6950); the low of 31 (6945) stops that one too
    assert [t.reason for t in tr] == ["stop", "stop", "stop"]
    assert tr[0].entry_t < tr[0].exit_t <= tr[1].entry_t


def test_blackout_windows_and_the_weekend_modes_hold_new_entries():
    bars = flat_bars(30 + 24 * 8)
    ticks = S.build_ticks(bars, H, bars)
    rule = always("long", 6000.0, 9000.0, None)
    first = next(t.t for t in ticks if t is not None)
    held = S.simulate(ticks, bars, H, rule, NO_COST, NO_FUND, blackouts=[(first, first + 3 * H)])
    assert held == []                                    # one entry, never closed: nothing scored
    # the entry itself waited for the window to end
    entries = []

    def spy(i, tk, b, st, flat=True):
        if flat:
            entries.append(tk[i].t)
        return rule(i, tk, b, st, flat)
    S.simulate(ticks, bars, H, spy, NO_COST, NO_FUND, blackouts=[(first, first + 3 * H)])
    assert entries[-1] == first + 3 * H
    wk = S.simulate(ticks, bars, H, rule, NO_COST, NO_FUND, weekend="flat")
    assert wk and all(t.reason == "weekend" for t in wk)
    assert all(not S.weekend_closed(t.entry_t) for t in wk)
    assert S.in_windows([(10, 20), (30, 40)], 35) and not S.in_windows([(10, 20), (30, 40)], 20)


# ── the rules ────────────────────────────────────────────────────────────

def wave_bars(n):
    """Hourly bars walking 6960 -> 7044 -> 6960 every 24 hours, 7 points a
    bar: a 24h range of about 1.2% of price with an ATR near 0.13%."""
    out, prev = [], 6960.0
    for i in range(n):
        k = i % 24
        c = 6960.0 + 7.0 * (k if k <= 12 else 24 - k)
        out.append(bar(i, prev, max(prev, c) + 1, min(prev, c) - 1, c))
        prev = c
    return out


def test_the_edge_rule_is_the_live_next_trade_and_refuses_a_stop_too_wide_for_50x():
    base = wave_bars(72)                                 # ends one bar above the wave's low
    near = base + [bar(72, 6967, 6968, 6961, 6963)]      # 4 points above the 24h low
    far = base + [bar(72, 6967, 6969, 6966, 6968)]       # 9 points above it
    kw = dict(k_atr=1.0, floor_frac=0.004, offset_frac=0.0, leverage=50.0, sides="both")
    rule = S.edge_rule(**kw)
    tk = S.build_ticks(near, H, near)[72]
    assert (tk.low24, tk.high24) == (6959.0, 7045.0) and tk.atr < 0.004 * tk.mid     # the floor binds
    o = rule(72, S.build_ticks(near, H, near), near, {})
    assert o.side == "long" and o.tp == 7045.0
    assert o.stop == pytest.approx(6959 - 0.004 * 6963)
    assert (6963 - o.stop) / 6963 * 100 <= S.leverage_budget(50.0)["max_stop_pct"]
    far_ticks = S.build_ticks(far, H, far)
    assert (6968 - (6959 - 0.004 * 6968)) / 6968 * 100 > S.leverage_budget(50.0)["max_stop_pct"]
    assert rule(72, far_ticks, far, {}) is None                           # 0.53% stop: no 50x
    assert S.edge_rule(**{**kw, "leverage": 20.0})(72, far_ticks, far, {}) is not None
    assert rule(72, S.build_ticks(near, H, near), near, {}, False) is None    # not flat: no order


def test_orb_builds_the_range_then_trades_the_first_close_outside_it_once_a_day():
    from datetime import datetime
    from services.strategies.weekend import NEW_YORK
    q = 900_000
    open_ms = int(datetime(2026, 11, 17, 9, 30, tzinfo=NEW_YORK).timestamp() * 1000)   # a Tuesday
    hours = [Candle(t=open_ms - (40 - i) * H, o=7000, h=7005, l=6995, c=7000, v=1) for i in range(40)]

    def b15(k, o, h, l, c):
        return Candle(t=open_ms + k * q, o=o, h=h, l=l, c=c, v=1)
    day = [b15(0, 7000, 7010, 6995, 7005), b15(1, 7005, 7012, 6990, 7000),      # the range: 6990..7012
           b15(2, 7000, 7011, 6998, 7008),                                     # inside: nothing
           b15(3, 7008, 7020, 7007, 7018),                                     # closes above 7012: long
           b15(4, 7018, 7030, 7015, 7025), b15(5, 7025, 7031, 6980, 6985)]     # then back under the range
    ticks = S.build_ticks(day, q, hours)
    rule = S.orb_rule(stop_mode="far", target_r=2.0, leverage=50.0)
    st = {}
    got = [rule(i, ticks, day, st) for i in range(len(day))]
    assert [g is not None for g in got] == [False, False, False, True, False, False]
    o = got[3]
    assert (o.side, o.stop, o.tp) == ("long", 6990.0, 7018 + 2 * 28)
    assert o.flat_at_ms == int(datetime(2026, 11, 17, 15, 45, tzinfo=NEW_YORK).timestamp() * 1000)
    mid = S.orb_rule(stop_mode="mid", target_r=1.5, leverage=50.0)
    st = {}
    o = [mid(i, ticks, day, st) for i in range(len(day))][3]
    assert o.stop == 7001.0 and o.tp == pytest.approx(7018 + 1.5 * 17)


# ── scoring and the verdict ──────────────────────────────────────────────

def test_max_drawdown_and_the_summary_row():
    assert S.max_drawdown_r([1, -1, -1, 2, -3]) == 3.0
    assert S.max_drawdown_r([]) == 0.0
    week = 7 * S.DAY_MS
    tr = [S.Trade("long", 10, 7000, 6965, 7100, exit_t=20, r=2.0, stop_frac=0.005, max_lev_ok=50),
          S.Trade("short", 30, 7000, 7070, 6900, exit_t=40, r=-1.0, stop_frac=0.010, max_lev_ok=25, reason="stop"),
          S.Trade("long", week + 5, 7000, 6965, 7100, exit_t=week + 9, r=5.0, stop_frac=0.005, max_lev_ok=50)]
    row = S.summarize(tr, 0, week)
    assert (row["trades"], row["per_week"], row["net_r"], row["max_dd_r"]) == (2, 2.0, 1.0, 1.0)
    assert row["win_rate"] == 0.5 and row["long"] == {"trades": 1, "net_r": 2.0}
    assert row["short"] == {"trades": 1, "net_r": -1.0} and row["fits_50x"] == 0.5
    assert S.summarize([], 0, week)["trades"] == 0
    assert S.split_ms(0, 1000) == 700


def test_equity_path_risks_the_cap_or_what_full_size_can_carry():
    tr = [S.Trade("long", 1, 7000, 6965, None, exit_t=2, r=-1.0, stop_frac=0.005),
          S.Trade("long", 3, 7000, 6965, None, exit_t=4, r=2.0, stop_frac=0.0005)]
    eq = S.equity_path(tr, 50.0, 6.0)
    # trade 1 risks the 6% cap (0.5% x 50 = 25% would be more); trade 2 only 0.05% x 50 = 2.5%
    assert eq["final_multiple"] == pytest.approx(0.94 * 1.05, abs=0.001)
    assert eq["max_dd_pct"] == 6.0


def _row(full, tail, stress, n=40, tail_n=10, ex=1.0):
    def r(net, k):
        return {"trades": k, "net_r": net, "net_r_ex_best2": ex}
    return {"full": r(full, n), "tail": r(tail, tail_n), "stress": r(stress, n)}


def test_verdict_edge_leaning_noise():
    good = _row(5, 2, 1)
    assert S.verdict(good, good, [1, 1, -1, 2])["verdict"] == "EDGE"
    assert S.verdict(good, _row(-1, 2, 1), [1, 1])["verdict"] == "LEANING"          # 15m full fails
    assert S.verdict(_row(5, 2, -1), good, [1, 1])["verdict"] == "LEANING"          # stress fails
    assert S.verdict(_row(5, 2, 1, ex=-0.5), good, [1, 1])["verdict"] == "NOISE"    # two trades carry it
    assert S.verdict(good, good, [1, -1, -1])["verdict"] == "NOISE"                 # neighbours disagree
    assert S.verdict(_row(5, -2, 1), good, [1, 1])["verdict"] == "NOISE"            # the tail loses
    assert S.verdict(_row(5, 2, 1, tail_n=2), good, [1, 1])["verdict"] == "NOISE"   # a 2-trade tail
    out = S.verdict(_row(5, 2, 1, n=20), good, [1, 1])
    assert out["verdict"] == "LEANING" and out["failed"] == ["trades_1h_30"]


# ── variants ─────────────────────────────────────────────────────────────

def test_the_grid_and_its_neighbours():
    named = S.variants(50.0)
    fams = {f: sum(1 for v in named.values() if v["family"] == f) for f in ("edge24", "pull8", "orb")}
    assert fams == {"edge24": 108, "pull8": 72, "orb": 6}
    assert all(v["kw"]["leverage"] == 50.0 for v in named.values())
    assert not any(v["kw"].get("sides") == "both" for v in named.values() if v["family"] == "pull8")
    nb = S.neighbours("edge24_k1_f0.4_o0_both_50x", named)
    assert sorted(nb) == sorted(["edge24_k0.5_f0.4_o0_both_50x", "edge24_k1.3_f0.4_o0_both_50x",
                                 "edge24_k1_f0.25_o0_both_50x", "edge24_k1_f0.4_o0.1_both_50x"])
    assert len(S.neighbours("orb_far_2r_50x", named)) == 5


def test_the_live_variant_is_next_trades_own_constants_and_sits_on_the_grid():
    live = S.live_variant()
    assert live["kw"] == {"k_atr": NT.SP500_RULE["k_atr"], "floor_frac": NT.SP500_RULE["floor_frac"],
                          "offset_frac": NT.target_offset_frac_for(S.COIN), "sides": "both",
                          "entry_lookback_h": 24, "leverage": NT.SP500_LEVERAGE,
                          "fit": NT.leverage_fit_for(S.COIN)}
    name = S.edge_name("edge24", live["kw"]["k_atr"], live["kw"]["floor_frac"], live["kw"]["offset_frac"],
                       live["kw"]["sides"], 50.0)
    assert name in S.variants(50.0)
    oil = S.oil_as_is(20.0, NT.LEVERAGE_FIT)["kw"]
    assert (oil["k_atr"], oil["floor_frac"], oil["offset_frac"]) == (1.3, 0.008, 0.005)


def test_noise_table_and_event_windows_are_pure_shapes():
    bars = [bar(i, 7000, 7007, 6993, 7000) for i in range(60)]
    n = S.noise_table(bars, bars)
    assert n["atr14_1h_pct"]["p50"] == pytest.approx(0.2, abs=0.001)
    assert n["range_24h_pct"]["p50"] == pytest.approx(0.2, abs=0.001)
    from datetime import datetime, timezone
    a = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    b = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    wins = S.event_windows(a, b)
    assert wins == sorted(wins) and all(e - s == 4 * H for s, e in wins) and len(wins) >= 2
