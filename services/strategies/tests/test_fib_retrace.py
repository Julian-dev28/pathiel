"""Gate tests for the Fibonacci retracement eval
(services/strategies/evals/fib_retrace_test.py, 2026-11-16): the level
math, the three swing definitions (24h range, 1h zigzag, session since the
reopen) and that none of them reads ahead, the fill model (a touch is not a
fill, a trade-through is, a gap fills a resting order at the open, a level
already traded through is spent), the walker's exits and its one-position
rules beside the live book, the operator's ratchet, the costs, the grid and
the verdict labels. Synthetic bars only, no network, no state read.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.evals import fib_retrace_test as E
from services.strategies.evals import intraday_5m_test as I5
from services.strategies.evals.fib_retrace_test import Ctx, Pivot, Swing, Variant

Q = E.FIFTEEN_MS
H = E.HOUR_MS


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


T0 = ms(2026, 11, 3, 8)              # a Tuesday, 08:00Z, winter time
EQ = 1000.0
TAKER, MAKER = 0.00009, 0.00003
P = dataclasses.replace(E.base_params(EQ, E.RISK_FULL), taker_fee=TAKER, maker_fee=MAKER)
UP = Swing(1, 100.0, 110.0, T0 - 2 * H, ("t", 1, T0 - 2 * H, 110.0))       # 61.8% = 103.82, 78.6% = 102.14
DOWN = Swing(-1, 110.0, 100.0, T0 - 2 * H, ("t", -1, T0 - 2 * H, 100.0))   # 61.8% = 106.18, 78.6% = 107.86
V = Variant("range24", "with", 0.618, "limit", "next", 0.0, "off")


def ctx_of(rows, swing=UP, t0=T0, step=Q, atr=1.0, fund=None, swings=None):
    """rows of (o, h, l, c) -> a context whose anchor is `swing` on every tick."""
    t = [t0 + i * step for i in range(len(rows))]
    o, h, l, c = ([r[k] for r in rows] for k in range(4))
    sw = list(swings) if swings is not None else [swing] * len(rows)
    return Ctx(step=step, t=t, o=o, h=h, l=l, c=c, atr=[atr] * len(rows),
               weekend=[E.weekend_closed(x) for x in t], reopen=[E.SL.reopen_tick(x, step) for x in t],
               fund=fund or {}, swings={"range24": sw}, ret={"range24": E.retrace_series(t, h, l, sw)})


def run(rows, v=V, **kw):
    ctx_kw = {k: kw.pop(k) for k in ("swing", "t0", "step", "atr", "fund", "swings") if k in kw}
    ctx = ctx_of(rows, **ctx_kw)
    return ctx, E.walk(ctx, v, P, 0, len(rows) - 1, **kw)


def one(out):
    assert len(out["paths"]) == 1, out
    return out["paths"][0]


QUIET = (105.0, 105.2, 104.8, 105.0)


# ── level math ─────────────────────────────────────────────────────────────

def test_fib_price_both_directions_and_extensions():
    assert E.fib_price(100.0, 110.0, 0.0) == 110.0 and E.fib_price(100.0, 110.0, 1.0) == 100.0
    assert E.fib_price(100.0, 110.0, 0.618) == pytest.approx(103.82)
    assert E.fib_price(110.0, 100.0, 0.618) == pytest.approx(106.18)
    assert E.fib_price(100.0, 110.0, -0.272) == pytest.approx(112.72)
    assert E.fib_price(110.0, 100.0, -0.618) == pytest.approx(93.82)


def test_next_level_walks_the_ladder():
    assert [E.next_level(r) for r in E.FIBS] == [0.5, 0.618, 0.786, 1.0]
    assert E.next_level(1.0) == 1.0


def test_stop_rules():
    assert E.stop_price("next", UP, 0.618, 1.0) == pytest.approx(102.14 - 0.1)
    assert E.stop_price("origin", UP, 0.618, 1.0) == pytest.approx(99.9)
    assert E.stop_price("atr1", UP, 0.618, 2.0) == pytest.approx(101.82)
    assert E.stop_price("next", DOWN, 0.618, 1.0) == pytest.approx(107.86 + 0.1)
    assert E.stop_price("origin", DOWN, 0.5, 1.0) == pytest.approx(110.1)
    assert E.stop_price("next", UP, 0.786, 1.0) == E.stop_price("origin", UP, 0.786, 1.0)
    with pytest.raises(ValueError):
        E.stop_price("nope", UP, 0.5, 1.0)


def test_targets():
    assert [E.target_price(UP, x) for x in E.TARGETS] == pytest.approx([110.0, 112.72, 116.18])
    assert [E.target_price(DOWN, x) for x in E.TARGETS] == pytest.approx([100.0, 97.28, 93.82])


def test_cost_trade_on_the_operators_long():
    got = E.cost_trade(92.96, 92.695, 93.709, TAKER, MAKER, 0.0003)
    assert got["side"] == "long" and got["planned_r"] == pytest.approx(2.826, abs=1e-3)
    assert got["stop_bps"] == pytest.approx(28.5, abs=0.05)
    assert 2.0 < got["r_after_costs"] < got["planned_r"]
    assert got["breakeven_win_rate_pct"] == pytest.approx(100.0 / (1.0 + got["r_after_costs"]), abs=0.06)
    short = E.cost_trade(100.0, 101.0, 97.0, TAKER, MAKER, 0.0003)
    assert short["side"] == "short" and short["planned_r"] == pytest.approx(3.0)


# ── swings ─────────────────────────────────────────────────────────────────

def hours(rows, t0=T0 - 48 * H):
    return [Candle(t=t0 + i * H, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def leg(a, b, n):
    """n 1h bars walking from a to b, each 0.2 wide around its path."""
    step = (b - a) / n
    return [(a + i * step, max(a + i * step, a + (i + 1) * step) + 0.1, min(a + i * step, a + (i + 1) * step) - 0.1,
             a + (i + 1) * step) for i in range(n)]


def test_zigzag_confirms_a_pivot_only_after_k_atr_back():
    hs = hours(leg(100, 110, 10) + leg(110, 107.5, 5) + leg(107.5, 104, 2) + leg(104, 109, 5))
    atr = [1.0] * len(hs)
    piv = E.zigzag_pivots(hs, atr, 3.0)
    assert [(p.kind, round(p.px, 1)) for p in piv] == [(-1, 99.9), (1, 110.1), (-1, 103.9)]
    low, high, low2 = piv
    assert low.confirmed_i == 2                     # 3 ATR off the first low
    assert hs[high.confirmed_i].l <= 110.1 - 3.0 < hs[high.confirmed_i - 1].l
    assert high.t == hs[9].t and low2.t == hs[16].t
    assert all(p.confirmed_i > next(i for i, b in enumerate(hs) if b.t == p.t) for p in piv)


def test_zigzag_reads_nothing_ahead():
    hs = hours(leg(100, 110, 10) + leg(110, 104, 6) + leg(104, 112, 8) + leg(112, 105, 7))
    atr = [1.0] * len(hs)
    full = E.zigzag_pivots(hs, atr, 2.0)
    assert len(full) >= 3
    for n in range(1, len(hs) + 1):
        assert E.zigzag_pivots(hs[:n], atr[:n], 2.0) == [p for p in full if p.confirmed_i < n]


def test_zigzag_k_sets_the_reversal_size():
    hs = hours(leg(100, 110, 10) + leg(110, 107.4, 4) + leg(107.4, 112, 5) + leg(112, 100, 8))
    atr = [1.0] * len(hs)
    assert len(E.zigzag_pivots(hs, atr, 2.0)) > len(E.zigzag_pivots(hs, atr, 4.0))
    assert E.zigzag_pivots(hs, [None] * len(hs), 2.0) == []


def test_swing_from_pivots_needs_two_confirmed():
    piv = [Pivot(-1, 100.0, T0, 3), Pivot(1, 110.0, T0 + 9 * H, 14), Pivot(-1, 104.0, T0 + 16 * H, 20)]
    assert E.swing_from_pivots(piv, 14) is None                     # the high confirms at the close of bar 14
    s = E.swing_from_pivots(piv, 15)
    assert (s.side, s.origin, s.extreme, s.t_ext) == (1, 100.0, 110.0, T0 + 9 * H)
    s2 = E.swing_from_pivots(piv, 21)
    assert (s2.side, s2.origin, s2.extreme) == (-1, 110.0, 104.0) and s2.key != s.key


def test_swing_from_window_direction_is_the_later_extreme():
    up = E.swing_from_window(hours(leg(100, 110, 10) + leg(110, 106, 4)), 1.0, "r24")
    assert (up.side, round(up.origin, 1), round(up.extreme, 1)) == (1, 99.9, 110.1)
    down = E.swing_from_window(hours(leg(110, 100, 10) + leg(100, 104, 4)), 1.0, "r24")
    assert (down.side, round(down.origin, 1), round(down.extreme, 1)) == (-1, 110.1, 99.9)
    assert E.swing_from_window(hours([(100, 110, 99, 105)]), 1.0, "r24") is None      # both in one bar
    assert E.swing_from_window(hours(leg(100, 100.5, 5)), 1.0, "r24") is None          # under 1 ATR
    assert E.swing_from_window([], 1.0, "r24") is None and E.swing_from_window(hours(leg(100, 110, 5)), None, "x") is None


def test_retrace_series_counts_only_bars_after_the_extreme_and_before_the_tick():
    t = [T0 + i * Q for i in range(6)]
    h = [108.0, 110.0, 109.0, 108.0, 107.5, 109.0]
    l = [103.0, 107.0, 106.0, 105.0, 104.0, 106.5]
    s = Swing(1, 100.0, 110.0, T0, ("k",))
    assert E.retrace_series(t, h, l, [s] * 6) == [None, None, None, 106.0, 105.0, 104.0]
    d = Swing(-1, 110.0, 103.0, T0, ("d",))
    assert E.retrace_series(t, h, l, [d] * 6) == [None, None, 110.0, 110.0, 110.0, 110.0]
    assert E.retrace_series(t, h, l, [None, s, s, None, s, s])[4] == 105.0


def flat_hours(n, px=100.0, t0=T0 - 60 * H):
    return [Candle(t=t0 + i * H, o=px, h=px + 0.5, l=px - 0.5, c=px, v=1.0) for i in range(n)]


def quarter_bars(hs):
    out = []
    for b in hs:
        for k in range(4):
            out.append(Candle(t=b.t + k * Q, o=b.o, h=b.h if k == 2 else max(b.o, b.c), l=b.l if k == 1 else min(b.o, b.c),
                              c=b.c, v=1.0))
    return out


def test_build_ctx_reads_closed_hours_only():
    hs = flat_hours(30) + hours(leg(100, 106, 6) + leg(106, 104, 4), t0=T0 - 30 * H)
    bars = quarter_bars(hs)
    ctx = E.build_ctx(bars, hs)
    i = ctx.t.index(hs[36].t)                       # the tick at the open of the hour after the high
    s = ctx.swings["range24"][i]
    assert s.side == 1 and s.extreme == pytest.approx(106.1) and s.t_ext == hs[35].t
    assert ctx.swings["range24"][i - 1].extreme < 106.1          # one 15m earlier that hour was still open
    assert ctx.atr[i] == pytest.approx(I5._atr(hs[:36], 14)[-1])
    cut = E.build_ctx(bars[:i + 1], hs[:36])
    for name in E.SWINGS:
        assert cut.swings[name] == ctx.swings[name][:i + 1] and cut.ret[name] == ctx.ret[name][:i + 1]


def test_session_swing_starts_at_the_reopen():
    reopen = ms(2026, 11, 8, 23)                    # Sunday 18:00 New York, winter
    hs = flat_hours(40, 120.0, reopen - 40 * H) + hours(leg(100, 104, 4) + leg(104, 102, 3), t0=reopen)
    ctx = E.build_ctx(hs, hs, step=H)
    by_t = dict(zip(ctx.t, ctx.swings["session"]))
    assert by_t[reopen + 3 * H] is None             # three closed hours: too young
    s = by_t[reopen + 4 * H]
    assert s.side == 1 and s.origin == pytest.approx(99.9) and s.extreme == pytest.approx(104.1)
    assert ctx.reopen[ctx.t.index(reopen)] and not ctx.reopen[ctx.t.index(reopen + H)]
    assert by_t[reopen + 4 * H].origin < 110 < by_t[reopen - H].origin if by_t[reopen - H] else True


# ── the grid ───────────────────────────────────────────────────────────────

def test_grid_is_the_declared_3690_variants():
    vs = E.variants()
    assert len(vs) == 3690 and len({v.name for v in vs}) == 3690
    assert not any(v.level == 0.786 and v.stop == "next" for v in vs)
    assert not any(v.level == 0.382 and v.ratchet == "fib75" for v in vs)
    fams = {v.family for v in vs}
    assert fams == {f"{s}/{d}" for s in "abc" for d in E.DIRECTIONS}
    for sd in "abc":
        for d in E.DIRECTIONS:
            assert E.reference_variant(sd, d) in vs


def test_neighbours_step_one_axis_and_skip_duplicates():
    names = {n.name for n in E.neighbours(Variant("zz3", "long", 0.618, "limit", "next", 0.272, "off"))}
    assert names == {"zz3_long_L500_limit_next_T272_off", "zz3_long_L618_limit_next_T0_off",
                     "zz3_long_L618_limit_next_T618_off", "zz2_long_L618_limit_next_T272_off",
                     "zz4_long_L618_limit_next_T272_off"}          # 78.6 + "next" is a dropped duplicate
    assert len(E.neighbours(Variant("range24", "with", 0.382, "limit", "atr1", 0.0, "off"))) == 2


# ── the fill model ─────────────────────────────────────────────────────────

def test_a_touch_is_not_a_fill_a_trade_through_is():
    rows = [QUIET, (105, 105.1, 103.82, 104.5), (104.5, 104.6, 103.81, 104.2), (104.2, 110.5, 104.0, 110.2)]
    _, out = run(rows)
    q = one(out)
    assert q["i"] == 2 and q["fill"] == pytest.approx(103.82) and q["fee_in"] == MAKER
    assert q["reason"] == "target" and q["exit_px"] == pytest.approx(110.0) and q["fee_out"] == MAKER
    assert q["planned_r"] == pytest.approx((110 - 103.82) / (103.82 - 102.04))
    assert out["armed"] == 1


def test_stop_can_fire_on_the_fill_bar_and_pays_slippage():
    _, out = run([QUIET, (105, 105.1, 102.0, 102.5), QUIET])
    q = one(out)
    assert q["reason"] == "stop" and q["x"] == q["i"] == 1 and q["fee_out"] == TAKER
    assert q["exit_ref"] == pytest.approx(102.04) and q["exit_px"] == pytest.approx(102.04 * (1 - P.slippage))


def test_target_never_fills_on_the_fill_bar():
    _, out = run([QUIET, (105, 111.0, 103.5, 110.5), (110.5, 110.6, 110.2, 110.4)])
    q = one(out)
    assert q["i"] == 1 and q["reason"] == "target" and q["x"] == 2


def test_target_needs_a_trade_through():
    _, out = run([QUIET, (105, 105.1, 103.5, 104.0), (104, 110.0, 104.0, 109.5), (109.5, 110.01, 109.0, 109.8)])
    assert one(out)["x"] == 3


def test_stop_and_target_in_one_bar_is_a_loss():
    _, out = run([QUIET, (105, 105.1, 103.5, 104.0), (104, 111.0, 102.0, 105.0)])
    assert one(out)["reason"] == "stop"


def test_a_bar_that_opens_beyond_the_stop_fills_at_its_open():
    _, out = run([QUIET, (105, 105.1, 103.5, 104.0), (101.5, 101.8, 101.0, 101.2)])
    q = one(out)
    assert q["exit_ref"] == pytest.approx(101.5) and q["exit_px"] == pytest.approx(101.5 * (1 - P.slippage))


def test_a_level_already_traded_through_is_spent():
    rows = [(105, 105.1, 103.5, 104.5), QUIET, (105, 105.1, 103.0, 104.0), (104, 110.5, 104, 110)]
    _, out = run(rows, dataclasses.replace(V, direction="thesis"), thesis=[0, 1, 1, 1])
    assert out["paths"] == [] and out["armed"] == 0


def test_a_resting_order_is_filled_at_a_gap_open_a_new_one_is_not_placed_through_the_level():
    _, out = run([QUIET, (103.5, 103.6, 103.2, 103.4), (103.4, 110.5, 103.3, 110.0)])
    q = one(out)
    assert q["i"] == 1 and q["fill"] == pytest.approx(103.5) and q["fee_in"] == MAKER
    _, out = run([(103.5, 103.6, 103.2, 103.4), (103.4, 110.5, 103.3, 110.0)])
    assert out["paths"] == []


def test_one_entry_per_swing():
    rows = [QUIET, (105, 105.1, 102.0, 104.5), QUIET, (105, 105.1, 103.0, 104.0), (104, 111, 104, 110)]
    _, out = run(rows)
    assert len(out["paths"]) == 1 and out["paths"][0]["reason"] == "stop"
    other = Swing(1, 100.0, 110.0, T0 + 2 * Q, ("t2",))
    _, out = run(rows, swings=[UP, UP, UP, other, other])
    assert [q["reason"] for q in out["paths"]] == ["stop", "target"]


def test_short_side_is_mirrored():
    rows = [(105, 105.2, 104.8, 105), (105, 106.5, 104.9, 106.0), (106, 106.1, 99.5, 100.0)]
    _, out = run(rows, swing=DOWN)
    q = one(out)
    assert q["side"] == -1 and q["fill"] == pytest.approx(106.18) and q["stop"] == pytest.approx(107.96)
    assert q["reason"] == "target" and q["exit_px"] == pytest.approx(100.0)


def test_direction_filters():
    rows = [(105, 105.2, 104.8, 105), (105, 106.5, 104.9, 106.0), (106, 106.1, 99.5, 100.0)]
    assert run(rows, dataclasses.replace(V, direction="long"), swing=DOWN)[1]["paths"] == []
    th = dataclasses.replace(V, direction="thesis")
    assert run(rows, th, swing=DOWN, thesis=[1, 1, 1])[1]["paths"] == []
    assert run(rows, th, swing=DOWN)[1]["paths"] == []
    assert len(run(rows, th, swing=DOWN, thesis=[-1, -1, -1])[1]["paths"]) == 1


def test_confirm_enters_at_the_next_open_after_the_close_back():
    v = dataclasses.replace(V, entry="confirm")
    rows = [QUIET, (105, 105.0, 103.7, 103.9), (104.0, 104.4, 103.9, 104.2), (104.2, 110.5, 104.1, 110.0)]
    ctx, out = run(rows, v)
    q = one(out)
    slip = E.market_slip(ctx, 2, P.slippage)
    assert q["i"] == 2 and q["ref"] == 104.0 and q["fill"] == pytest.approx(104.0 * (1 + slip)) and q["fee_in"] == TAKER
    assert slip == pytest.approx(max(P.slippage, 0.5 * (5 / 15) ** 0.5 * 0.5 / 104.0))
    late = [QUIET, (105, 105.0, 103.4, 103.6), (103.6, 103.8, 103.5, 103.7), (103.7, 103.95, 103.6, 103.9),
            (103.9, 104.2, 103.85, 104.1), (104.1, 110.5, 104.0, 110.0)]
    assert one(run(late, v)[1])["i"] == 4           # the first close back above came two bars after the pierce


def test_confirm_is_dead_once_the_retrace_reaches_the_stop():
    v = dataclasses.replace(V, entry="confirm")
    rows = [QUIET, (105, 105.0, 101.9, 102.5), (102.5, 104.5, 102.4, 104.3), (104.3, 110.5, 104.2, 110.0)]
    assert run(rows, v)[1]["paths"] == []


def test_market_slip_scales_the_bar_range_and_has_a_floor():
    ctx = ctx_of([(100, 100.6, 100.0, 100.3)])
    assert E.market_slip(ctx, 0, 0.0003) == pytest.approx(0.5 * (1 / 3) ** 0.5 * 0.006)
    hour = ctx_of([(100, 100.6, 100.0, 100.3)], step=H)
    assert E.market_slip(hour, 0, 0.0) == pytest.approx(0.5 * (1 / 12) ** 0.5 * 0.006)
    assert E.market_slip(ctx_of([(100, 100.01, 100.0, 100.0)]), 0, 0.0003) == 0.0003


def test_entry_gate_refusals():
    assert E.entry_gate(100.0, 99.99, 105.0, 1, P, 0.0)[0] == "tight_stop"
    assert E.entry_gate(100.0, 99.0, 100.005, 1, P, 0.0)[0] == "no_room"
    assert E.entry_gate(100.0, 99.0, 101.0, 1, P, 2.0)[0] == "min_r"
    why, r, lev = E.entry_gate(100.0, 99.0, 103.0, 1, P, 2.0)
    assert why is None and r == pytest.approx(3.0) and lev in (20.0, 15.0)
    assert E.entry_gate(100.0, 80.0, 160.0, 1, P, 0.0)[0] == "leverage"


def test_min_r_gate_blocks_the_order():
    rows = [QUIET, (105, 105.1, 103.5, 104.0), (104, 110.5, 104.0, 110.2)]
    assert len(run(rows)[1]["paths"]) == 1
    out = run(rows, min_r=4.0)[1]                   # planned R is 3.47
    assert out["paths"] == [] and out["refused"] == {"min_r": 1}


# ── weekend and the live book ──────────────────────────────────────────────

def test_weekend_flat_and_no_order_in_the_weekend_or_on_the_reopen_bar():
    fri = ms(2026, 11, 6, 20)                       # the oil weekend starts 21:00Z
    rows = [QUIET, (105, 105.1, 103.5, 104.0), QUIET, QUIET, (104.6, 104.9, 104.2, 104.4), QUIET]
    ctx, out = run(rows, t0=fri)
    q = one(out)
    assert ctx.weekend[4] and not ctx.weekend[3]
    assert q["reason"] == "weekend" and q["x"] == 4 and q["fee_out"] == TAKER
    assert q["exit_px"] == pytest.approx(104.6 * (1 - E.market_slip(ctx, 4, P.slippage)))
    assert run([QUIET, (105, 105.1, 103.5, 104.0)], t0=ms(2026, 11, 7, 12))[1]["paths"] == []
    reopen = ms(2026, 11, 8, 23)
    ctx, out = run([(105, 105.1, 103.5, 104.0), QUIET, (105, 105.1, 103.5, 104.0)], t0=reopen)
    assert ctx.reopen[0] and out["paths"] == []       # skipped on the reopen bar, and the level is then spent
    assert one(run([(105, 105.1, 103.5, 104.0), QUIET])[1])["i"] == 0


def test_beside_live_no_order_while_the_long_is_open_and_closed_when_it_enters():
    rows = [QUIET, (105, 105.1, 103.5, 104.0), QUIET, (104.9, 105.3, 104.7, 105.0), QUIET]
    n = len(rows)
    blocked = I5.Live([EQ] * n, [True] * n, [False] * n)
    assert run(rows, live=blocked)[1]["paths"] == []
    live = I5.Live([EQ] * n, [False, False, False, True, True], [False, False, False, True, False])
    ctx, out = run(rows, live=live)
    q = one(out)
    assert q["reason"] == "long_entry" and q["x"] == 3
    assert q["exit_px"] == pytest.approx(104.9 * (1 - E.market_slip(ctx, 3, P.slippage)))


# ── the ratchet ────────────────────────────────────────────────────────────

def test_ratchet_stop_levels():
    be50, be75, fib75 = (E.RATCHET_RULES[k] for k in ("be50", "be75", "fib75"))
    assert E.ratchet_stop(None, 1, 103.82, 110.0, 109.0, UP) is None
    assert E.ratchet_stop(be50, 1, 103.82, 110.0, None, UP) is None
    assert E.ratchet_stop(be50, 1, 103.82, 110.0, 106.90, UP) is None
    assert E.ratchet_stop(be50, 1, 103.82, 110.0, 106.91, UP) == 103.82
    assert E.ratchet_stop(be75, 1, 103.82, 110.0, 108.0, UP) is None
    assert E.ratchet_stop(fib75, 1, 103.82, 110.0, 108.5, UP) == pytest.approx(106.18)
    assert E.ratchet_stop(fib75, 1, 106.18, 110.0, 109.5, UP) == 106.18          # at 38.2 it is break-even
    assert E.ratchet_stop(fib75, -1, 106.18, 100.0, 101.5, DOWN) == pytest.approx(103.82)
    assert E.ratchet_stop(be50, -1, 106.18, 100.0, 103.0, DOWN) == 106.18


RATCHET_ROWS = [QUIET, (105, 105.1, 103.5, 104.0), (104.0, 107.0, 104.0, 106.5), (106.5, 106.6, 103.0, 103.2),
                (103.2, 103.3, 101.5, 101.8)]


def test_ratchet_moves_the_stop_to_entry_at_the_next_tick():
    off = one(run(RATCHET_ROWS)[1])
    assert off["reason"] == "stop" and off["x"] == 4 and off["exit_ref"] == pytest.approx(102.04)
    on = one(run(RATCHET_ROWS, dataclasses.replace(V, ratchet="be50"))[1])
    assert on["reason"] == "ratchet" and on["x"] == 3 and on["moved"]
    assert on["exit_ref"] == pytest.approx(103.82) and on["stop"] == pytest.approx(102.04)
    assert on["exit_px"] == pytest.approx(103.82 * (1 - P.slippage)) and on["fee_out"] == TAKER
    late = one(run(RATCHET_ROWS, dataclasses.replace(V, ratchet="be75"))[1])
    assert late["reason"] == "stop" and not late["moved"]             # 107.0 is 51% of the way, not 75%


def test_ratchet_to_the_382_level_locks_profit():
    rows = [QUIET, (105, 105.1, 103.5, 104.0), (104.0, 108.6, 104.0, 108.2), (108.2, 108.3, 105.9, 106.0), QUIET]
    q = one(run(rows, dataclasses.replace(V, ratchet="fib75"))[1])
    assert q["reason"] == "ratchet" and q["exit_ref"] == pytest.approx(106.18) and q["exit_px"] > q["fill"]


def test_ratchet_is_not_set_through_the_price_and_cuts_a_winner():
    rows = [QUIET, (105, 105.1, 103.5, 104.0), (104.0, 107.0, 103.6, 103.7), (103.7, 110.5, 103.6, 110.2)]
    q = one(run(rows, dataclasses.replace(V, ratchet="be50"))[1])
    assert not q["moved"] and q["reason"] == "target"                 # the close was under the entry: no move
    cut = [QUIET, (105, 105.1, 103.5, 104.0), (104.0, 107.0, 104.0, 106.5), (106.5, 106.6, 103.7, 104.0),
           (104.0, 110.5, 104.0, 110.2)]
    assert one(run(cut)[1])["reason"] == "target"
    assert one(run(cut, dataclasses.replace(V, ratchet="be50"))[1])["reason"] == "ratchet"


# ── costs and sizing ───────────────────────────────────────────────────────

def test_sizing_fees_and_funding():
    t0 = ms(2026, 11, 3, 8, 30)
    rows = [QUIET, (105, 105.1, 103.5, 104.0), (104.0, 104.5, 103.9, 104.2), (104.2, 110.5, 104.0, 110.2)]
    ctx, out = run(rows, t0=t0, fund={t0 + 2 * Q: 0.0001})
    q = one(out)
    assert q["fund_unit"] == pytest.approx(0.0001 * 104.0)
    tr = E.size_paths(ctx, out["paths"], P, 0.06, EQ)[0]
    pp = dataclasses.replace(P, risk_cap_frac=0.06)
    notional = E.risk_capped_notional(EQ, pp, q["lev"], 103.82, q["stop"])
    size = notional / 103.82
    assert tr["notional"] == pytest.approx(notional) and tr["risk_pct"] <= 6.0 + 1e-9
    assert tr["fees_usd"] == pytest.approx(MAKER * notional + MAKER * 110.0 * size)
    assert tr["funding_usd"] == pytest.approx(0.0001 * 104.0 * size)
    assert tr["net_usd"] == pytest.approx((110.0 - 103.82) * size - tr["fees_usd"] - tr["funding_usd"])
    assert tr["slip_usd"] == pytest.approx(0.0) and tr["r"] == pytest.approx(tr["net_usd"] / tr["risk_usd"])
    small = E.size_paths(ctx, out["paths"], P, 0.012, EQ)[0]
    assert small["notional"] == pytest.approx(notional / 5.0, rel=0.02) and small["exit_px"] == tr["exit_px"]


def test_sizing_compounds_and_reads_the_live_equity():
    rows = [QUIET, (105, 105.1, 103.5, 104.0), (104, 110.5, 104, 110.2)]
    ctx, out = run(rows)
    live = I5.Live([2000.0] * 3, [False] * 3, [False] * 3)
    alone = E.size_paths(ctx, out["paths"], P, 0.012, EQ)[0]
    beside = E.size_paths(ctx, out["paths"], P, 0.012, EQ, live)[0]
    assert beside["notional"] == pytest.approx(2.0 * alone["notional"])
    assert E.size_paths(ctx, out["paths"], P, 0.012, 0.0) == []


def test_an_open_trade_is_marked_at_the_last_close():
    ctx, out = run([QUIET, (105, 105.1, 103.5, 104.0), (104.0, 105.0, 103.9, 104.8)])
    q = one(out)
    assert q["reason"] == "open" and q["exit_px"] == 104.8 and q["fee_out"] == 0.0
    assert E.size_paths(ctx, out["paths"], P, 0.06, EQ)[0]["exit_ms"] is None


# ── judging ────────────────────────────────────────────────────────────────

def _row(comb=5.0, n=12, dd=10.0, live_dd=20.0, halves=(1.0, 1.0), long_net=3.0, best_day=True):
    s = {"net_usd": comb, "n": n}
    return {"datasets": {"brent_journal": {"alone": {"net_usd": 1.0, "n": n}, "combined": s,
                                           "live": {"net_usd": 50.0, "max_dd_pct": live_dd}, "combined_dd_pct": dd},
                         "brent_1h_208d": {"alone": {"net_usd": long_net, "n": 30}}},
            "halves": list(halves), "drop_best_day": {"pass": best_day, "rest_usd": 1.0}}


def test_judge_adds_the_long_history_leg():
    assert E.judge(_row(), [1.0, 2.0])["verdict"] == "PASS"
    bad = E.judge(_row(long_net=-3.0), [1.0, 2.0])
    assert bad["verdict"] == "FAIL" and bad["repo_bar"] == "PASS" and not bad["legs"]["alone_on_long_history"]
    assert E.judge(_row(n=4), [1.0])["verdict"] == "LEANING"
    assert E.judge(_row(n=4, long_net=-1.0), [1.0])["verdict"] == "NOISE"
    assert E.judge(_row(comb=-5.0), [1.0])["verdict"] == "FAIL"
    assert E.judge(_row(dd=30.0), [1.0])["verdict"] == "FAIL"
    assert E.judge(_row(halves=(1.0, -1.0)), [1.0])["verdict"] == "FAIL"
    assert E.judge(_row(), [1.0, -2.0])["verdict"] == "FAIL"            # a losing neighbour: no plateau
    assert E.judge(_row(n=9, best_day=False), [1.0])["verdict"] == "NOISE"


def test_family_verdict_needs_breadth_for_a_pass():
    j = lambda v, n, b: {"verdict": v, "n_primary": n, "beats_all": b}   # noqa: E731
    assert E.family_verdict([j("PASS", 12, True), j("FAIL", 12, True), j("FAIL", 12, False)])["verdict"] == "PASS"
    lone = E.family_verdict([j("PASS", 12, True), j("FAIL", 12, False), j("FAIL", 12, False)])
    assert lone["verdict"] == "LEANING" and lone["breadth_pct"] == pytest.approx(33.3)
    assert E.family_verdict([j("LEANING", 3, True), j("NOISE", 2, False)])["verdict"] == "LEANING"
    assert E.family_verdict([j("FAIL", 12, False), j("NOISE", 2, False)])["verdict"] == "FAIL"
    assert E.family_verdict([j("NOISE", 2, False)])["verdict"] == "NOISE"
    assert E.family_verdict([])["verdict"] == "NOISE"


def test_alone_holds_needs_every_cell():
    row = {"datasets": {"a": {"alone": {"net_usd": 2.0, "n": 12}}, "b": {"alone": {"net_usd": 1.0, "n": 3}}},
           "long_halves": {"a": [1.0, 1.0]}}
    assert E.alone_holds(row)
    assert not E.alone_holds({**row, "long_halves": {"a": [1.0, -1.0]}})
    assert not E.alone_holds({**row, "long_halves": {}})
    row["datasets"]["b"]["alone"]["net_usd"] = -1.0
    assert not E.alone_holds(row)


# ── end to end on synthetic data ───────────────────────────────────────────

def _dataset(name, live=True, n_hours=120):
    path = leg(100, 106, 12) + leg(106, 103, 8) + leg(103, 109, 12) + leg(109, 104.5, 10) + leg(104.5, 111, 14)
    hs = flat_hours(30) + hours((path * 3)[:n_hours - 30], t0=T0 - 30 * H)
    bars = quarter_bars(hs)
    ctx = E.build_ctx(bars, hs)
    start, end = bars[100].t, bars[-1].t + Q
    run_ = None
    if live:
        ticks = [SimpleNamespace(t=b.t) for b in bars if start <= b.t < end]
        m = len(ticks)
        opn = [40 <= k < 60 for k in range(m)]
        run_ = {"ticks": ticks, "long_curve": [EQ + (10.0 if k >= 60 else 0.0) for k in range(m)], "long_open": opn,
                "long_entry": [k == 40 for k in range(m)], "intervals": [(ticks[40].t, ticks[59].t)],
                "live": {"trades": 1, "wins": 1, "net_usd": 10.0, "max_dd_pct": 0.0}}
    return E.make_dataset(name, ctx, [1] * len(ctx.t), P, start, end, run_)


def test_make_dataset_maps_the_live_book_onto_the_ticks():
    ds = _dataset("brent_journal")
    assert ds.live.long_open[ds.lo_i + 40] and not ds.live.long_open[ds.lo_i + 39]
    assert ds.live.long_entry[ds.lo_i + 40] and ds.live.eq[ds.lo_i + 60] == EQ + 10.0
    assert ds.live.eq[0] == P.starting_equity and not any(ds.live.long_open[:ds.lo_i])
    assert ds.live_sum["net_usd"] == 10.0 and _dataset("x", live=False).live is None


def test_evaluate_does_not_depend_on_the_order_of_the_work_and_judges_every_row():
    # the workers of a real run are forked; a gate test does not fork (pytest is multi-threaded), so the
    # order-independence a worker pool relies on is checked by running the variants in reverse
    datasets = {"brent_journal": _dataset("brent_journal"), "brent_15m_50d": _dataset("brent_15m_50d", live=False)}
    halves = [_dataset("h1"), _dataset("h2")]
    vs = [v for v in E.variants() if v.swing in ("range24", "zz2") and v.direction == "with" and v.stop == "next"
          and v.entry == "limit" and v.ratchet in ("off", "be50")]
    one_job = E.evaluate(vs, datasets, halves, jobs=1, long_halves={"brent_15m_50d": halves})
    two_jobs = E.evaluate(vs[::-1], datasets, halves, jobs=1, long_halves={"brent_15m_50d": halves})
    assert one_job == two_jobs and len(one_job) == 2 * len(vs)
    assert sum(r["datasets"]["brent_15m_50d"]["alone"]["n"] for r in one_job.values()) > 0
    for r in one_job.values():
        assert r["judge"]["verdict"] in ("PASS", "LEANING", "FAIL", "NOISE")
        d = r["datasets"]["brent_journal"]
        assert d["combined"]["n"] <= d["alone"]["n"] + 2 and "max_dd_pct" in d["combined"]
        assert d["combined_total_usd"] == pytest.approx(10.0 + d["combined"]["net_usd"], abs=0.011)
    fams = E.summarize_families(one_job, list(datasets))
    assert set(fams) == {"a/with", "b/with"} and fams["a/with"]["full"]["variants"] == len(vs) // 2
    assert fams["a/with"]["full"]["reference"] == "range24_with_L618_limit_next_T0_off@full"
    levels = E.level_table(one_job, list(datasets))
    assert set(levels) == {"38.2", "50.0", "61.8", "78.6"} and "fill_rate_pct" in levels["61.8"]["brent_15m_50d"]
    rat = E.ratchet_table(one_job, list(datasets))
    assert rat["be50"]["brent_15m_50d"]["pairs"] > 0 and rat["be75"]["brent_15m_50d"]["pairs"] == 0


def test_beside_live_never_holds_a_trade_through_a_live_long():
    ds = _dataset("brent_journal")
    for v in (V, dataclasses.replace(V, swing="zz2"), dataclasses.replace(V, entry="confirm", level=0.382)):
        for q in E.walk(ds.ctx, v, P, ds.lo_i, ds.hi_i, ds.thesis, ds.live)["paths"]:
            assert not ds.live.long_open[q["i"]]
            assert not any(ds.live.long_open[q["i"]:q["x"]])


# ── data helpers and the CLI ───────────────────────────────────────────────

def test_load_funding_pages_in_chunks(monkeypatch):
    calls = []

    def fake(coin, a, b, cache_dir):
        calls.append((a, b))
        return [{"time": a, "fundingRate": "0.0001"}]

    monkeypatch.setattr(D, "fetch_funding_cached", fake)
    rows = E.load_funding("xyz:BRENTOIL", 0, 50 * E.DAY_MS, "/nowhere")
    assert calls == [(0, 20 * E.DAY_MS), (20 * E.DAY_MS, 40 * E.DAY_MS), (40 * E.DAY_MS, 50 * E.DAY_MS)]
    assert len(rows) == 3


def test_merge_theses_keeps_the_replay_only_before_the_journal():
    th = lambda t: D.ThesisDirection(t, "long", 100.0, 99.0, None, [103.0], str(t))   # noqa: E731
    got = E.merge_theses([th(1), th(5), th(9)], [th(6), th(8)])
    assert [x.created_at_ms for x in got] == [1, 5, 6, 8]
    assert [x.created_at_ms for x in E.merge_theses([th(1)], [])] == [1]


def test_cli_help_and_no_journal(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        E.main(["--help"])
    assert e.value.code == 0 and "Research only" in capsys.readouterr().out
    assert E.main(["--oil-theses-dir", str(tmp_path / "none"), "--out-dir", str(tmp_path / "o"),
                   "--cache-dir", str(tmp_path / "c")]) == 2
