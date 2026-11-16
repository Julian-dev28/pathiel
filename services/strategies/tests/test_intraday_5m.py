"""Gate tests for the intraday 5-minute eval
(services/strategies/evals/intraday_5m_test.py, 2026-11-16): the session
clock (CME day, weekend flat, the reopen bar, report anchors, where a
watcher or a cron tick fills), every signal generator, the cost model
(measured fee, half-range slippage, funding), the walker's exits and its
one-position rules beside the live book, and the verdict labels.
Synthetic bars only, no network, no state read.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.evals import intraday_5m_test as E
from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals.intraday_5m_test import Live, Scenario, Signal, Variant

F = E.FIVE_MS
H = E.HOUR_MS


def ms(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


T0 = ms(2026, 11, 3, 8)              # a Tuesday, 08:00Z, winter time
EQ = 1000.0
P = E.base_params(EQ, E.RISK_FULL)
FREE = Scenario("free", "next", 0.0, 0.0, "zero")
FAR = T0 + 100 * H


def bars(rows, t0=T0, v=1.0):
    """rows of (o, h, l, c) -> 5m candles from t0."""
    return [Candle(t=t0 + i * F, o=o, h=h, l=l, c=c, v=v) for i, (o, h, l, c) in enumerate(rows)]


def ctx_of(rows, t0=T0, **over):
    ctx = E.build_ctx(bars(rows, t0), [], None)
    return dataclasses.replace(ctx, **over) if over else ctx


def flat(n, px=100.0, w=0.1):
    return [(px, px + w, px - w, px)] * n


def one(run):
    assert len(run["trades"]) == 1, run
    return run["trades"][0]


# ── grid ───────────────────────────────────────────────────────────────────

def test_grid_is_208_declared_variants():
    vs = E.variants()
    assert len(vs) == 208 and len({v.name for v in vs}) == 208
    fam = {f: sum(1 for v in vs if v.family == f) for f in E.FAMILIES}
    assert fam == {1: 72, 2: 12, 3: 8, 4: 24, 5: 92}
    assert all(v.kind != "bb" for v in vs if v.thesis)       # family 5 is families 1 to 3 only


def test_neighbours_step_ordered_axes_only():
    v = Variant("orb", ("london", 15, "opp", 2.0))
    assert {n.name for n in E.neighbours(v)} == {"orb_london_r30_opp_t2", "orb_london_r15_opp_t1.5",
                                                 "orb_london_r15_opp_t3"}
    assert {n.name for n in E.neighbours(Variant("pb", ("1h", 0.5, 1.5), thesis=True))} == {
        "th_pb_1h_s1.5_t1.5", "th_pb_1h_s0.5_t3"}


# ── session clock ──────────────────────────────────────────────────────────

def test_session_key_turns_at_18_new_york():
    assert E.session_key(ms(2026, 11, 3, 22, 55)) == "2026-11-03"
    assert E.session_key(ms(2026, 11, 3, 23, 0)) == "2026-11-04"      # winter: 23:00Z
    assert E.session_key(ms(2026, 10, 27, 21, 55)) == "2026-10-27"
    assert E.session_key(ms(2026, 10, 27, 22, 0)) == "2026-10-28"     # summer: 22:00Z


def test_reopen_bar_is_the_first_bar_after_the_weekend():
    assert E.reopen_bar(ms(2026, 11, 8, 23, 0))
    assert not E.reopen_bar(ms(2026, 11, 8, 23, 5))
    assert not E.reopen_bar(ms(2026, 11, 8, 22, 55))
    assert E.weekend_closed(ms(2026, 11, 6, 21, 0)) and not E.weekend_closed(ms(2026, 11, 6, 20, 55))


def test_session_anchors_skip_the_weekend_and_follow_the_report_calendar():
    t0 = ms(2026, 11, 2)
    times = [t0 + i * F for i in range(14 * 288)]                   # Mon 11-02 .. Sun 11-15
    a = E.session_anchors(times)
    assert ms(2026, 11, 4, 7) in a["london"] and ms(2026, 11, 4, 13) in a["ny"]
    assert ms(2026, 11, 7, 7) not in a["london"] and ms(2026, 11, 8, 13) not in a["ny"]     # Sat, Sun
    assert len(a["london"]) == 10
    assert a["reopen"] == [ms(2026, 11, 8, 23), ms(2026, 11, 15, 23)]
    assert a["eia"] == [ms(2026, 11, 4, 15, 30), ms(2026, 11, 12, 17, 0)]      # Veterans Day week: Thu 12:00 ET
    assert a["api"] == [ms(2026, 11, 3, 21, 30), ms(2026, 11, 10, 21, 30)]
    assert E.session_anchors([]) == {"reopen": [], "london": [], "ny": [], "eia": [], "api": []}


@pytest.mark.parametrize("minute, next_, watcher, tick15", [
    (0, 0, 5, 5),        # close at :00, cron :03, filled :05
    (5, 5, 10, 20),      # close at :05, the :03 tick is gone: cron :18, filled :20
    (10, 10, 15, 20),
    (15, 15, 20, 20),
])
def test_fill_time_per_execution_model(minute, next_, watcher, tick15):
    t = T0 + minute * 60_000
    assert E.fill_time(t, "next") == T0 + next_ * 60_000
    assert E.fill_time(t, "watcher") == T0 + watcher * 60_000
    assert E.fill_time(t, "tick15") == T0 + tick15 * 60_000
    with pytest.raises(ValueError):
        E.fill_time(t, "nope")


# ── fees ───────────────────────────────────────────────────────────────────

def test_measured_fees_from_the_ledger(tmp_path):
    def row(i, kind, bps, coin="xyz:BRENTOIL", notional=1000.0):
        return {"id": f"t{i}", "coin": coin, "notional_usd": notional, "exit_px": 100.0, "max_size": notional / 100.0,
                "fees": 2 * notional * bps / 1e4, "exit_kind": kind}
    rows = [row(1, "stop", 0.9), row(2, "manual", 0.9), row(3, "target", 0.6), row(4, "target", 0.9),
            row(5, "stop", 9.0, coin="xyz:GOLD"), row(6, "stop", 9.0, notional=11.0),
            row(1, "stop", 0.9)]                                  # a duplicate id counts once
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    got = E.measured_fees(str(path))
    assert got["source"] == "ledger" and got["n_taker"] == 2 and got["n_maker"] == 1
    assert got["taker"] == pytest.approx(0.00009) and got["maker"] == pytest.approx(0.00003)
    assert got["n_target_paid_taker"] == 1
    assert E.measured_fees(str(tmp_path / "missing.jsonl")) == {
        "taker": E.FALLBACK_TAKER, "maker": E.FALLBACK_MAKER, "source": "fallback", "n_taker": 0, "n_maker": 0}


def test_scenarios_only_the_repo_one_uses_the_sim_fee():
    scs = E.scenarios(0.00009, 0.00003, P)
    assert list(scs) == ["free", "bot", "watcher", "tick15", "repo_fee"]
    assert scs["watcher"].taker == 0.00009 and scs["watcher"].delay == "watcher" and scs["watcher"].slip == "range"
    assert scs["repo_fee"].taker == P.taker_fee == 0.00045 and scs["repo_fee"].maker == P.maker_fee
    assert scs["free"].taker == 0.0 and scs["free"].slip == "zero" and scs["bot"].slip == "curve"


# ── context ────────────────────────────────────────────────────────────────

def test_ctx_reads_closed_data_only():
    rows = [(100, 101, 99, 100), (100, 102, 100, 101), (101, 101.5, 98, 99)]
    ctx = ctx_of(rows, t0=ms(2026, 11, 3, 22, 50))               # the third bar opens a new CME day
    assert ctx.sess_n == [1, 2, 1]
    assert ctx.sess_hi_prev == [None, 101, None] and ctx.sess_lo_prev == [None, 99, None]
    assert ctx.vwap[1] == pytest.approx((100 + 101) / 2) and ctx.vwap[2] == pytest.approx((101.5 + 98 + 99) / 3)
    assert ctx.atr == [None, None, None] and ctx.trend1h == [None, None, None]
    assert ctx.ema[0] == 100 and ctx.ema[1] == pytest.approx(100 + 2 / 21)
    assert E.trend_state(101.0, 100.0, 1.0) == 1 and E.trend_state(99.0, 100.0, 1.0) == -1
    assert E.trend_state(100.4, 100.0, 1.0) == 0 and E.trend_state(101.0, 100.0, None) is None


def test_ctx_thesis_side_is_read_at_the_close():
    flip = T0 + F                                                  # the thesis turns long at the first bar's close
    ctx = E.build_ctx(bars(flat(3)), [], lambda t: "long" if t >= flip else "short")
    assert ctx.thesis == [1, 1, 1]
    ctx = E.build_ctx(bars(flat(2)), [], lambda t: "flat")
    assert ctx.thesis == [0, 0]


# ── signals ────────────────────────────────────────────────────────────────

def orb_rows(breakout_close):
    return [(100, 100.5, 99.5, 100), (100, 100.8, 99.8, 100.2), (100.2, 100.6, 99.9, 100.1),   # the 15m range
            (100.1, 100.7, 100.0, 100.5),                                                       # inside
            (100.5, 101.2, 100.4, breakout_close)]


def test_orb_enters_on_the_first_close_outside_the_range():
    ctx = ctx_of(orb_rows(101.0))
    (s,) = E.orb_signals(ctx, [(T0, "london")], 15, "opp", 2.0)
    assert (s.i, s.side, s.stop, s.target_r, s.tag) == (4, 1, 99.5, 2.0, "london")
    assert s.expire_ms == T0 + 5 * F + E.ORB_HOLD_MIN * 60_000
    (m,) = E.orb_signals(ctx, [(T0, "london")], 15, "mid", 2.0)
    assert m.stop == pytest.approx((100.8 + 99.5) / 2)
    (d,) = E.orb_signals(ctx_of(orb_rows(99.0)), [(T0, "ny")], 15, "opp", 1.5)
    assert (d.side, d.stop) == (-1, 100.8)


def test_orb_needs_a_close_outside_a_whole_range_and_the_window():
    assert E.orb_signals(ctx_of(orb_rows(100.6)), [(T0, "london")], 15, "opp", 2.0) == []     # a wick, not a close
    assert E.orb_signals(ctx_of(orb_rows(101.0)), [(T0 - F, "london")], 15, "opp", 2.0) == []  # anchor bar missing
    assert E.orb_signals(ctx_of(orb_rows(101.0)), [(T0, "london")], 15, "opp", 2.0, window_min=5) == []
    gap = bars(orb_rows(101.0))
    del gap[1]                                                     # a hole inside the range
    assert E.orb_signals(E.build_ctx(gap, [], None), [(T0, "london")], 15, "opp", 2.0) == []


def vwap_ctx(last_close, trend=0):
    rows = flat(20) + [(100, max(100.1, last_close), min(99.9, last_close), last_close)]
    ctx = ctx_of(rows)
    return dataclasses.replace(ctx, trend1h=[trend] * len(rows))


def test_vwap_fades_a_fresh_stretch_only_when_the_trend_is_flat():
    ctx = vwap_ctx(101.0)
    a = ctx.atr[-1]
    (s,) = E.vwap_signals(ctx, 2.0, 1.0, 60)
    assert s.i == 20 and s.side == -1 and s.target_px == pytest.approx(ctx.vwap[20])
    assert s.stop == pytest.approx(101.0 + a) and s.expire_ms == ctx.t[20] + F + 60 * 60_000
    (b,) = E.vwap_signals(vwap_ctx(99.0), 2.0, 2.0, 120)
    assert b.side == 1 and b.stop == pytest.approx(99.0 - 2 * vwap_ctx(99.0).atr[-1])
    assert E.vwap_signals(vwap_ctx(101.0, trend=1), 2.0, 1.0, 60) == []
    assert E.vwap_signals(vwap_ctx(101.0, trend=None), 2.0, 1.0, 60) == []
    assert E.vwap_signals(vwap_ctx(100.2), 2.0, 1.0, 60) == []            # not stretched


def test_vwap_does_not_refire_while_still_stretched():
    rows = flat(20) + [(100, 101, 99.9, 101.0), (101, 101.3, 100.9, 101.2)]
    ctx = dataclasses.replace(ctx_of(rows), trend1h=[0] * 22)
    assert [s.i for s in E.vwap_signals(ctx, 2.0, 1.0, 60)] == [20]


def pb_rows():
    up = [(100 + 0.1 * i, 100.2 + 0.1 * i, 99.95 + 0.1 * i, 100.1 + 0.1 * i) for i in range(20)]   # a steady climb
    return up + [(102.0, 102.1, 101.2, 101.9),      # 20: dips through the EMA, closes above it
                 (101.9, 102.0, 101.3, 101.8)]      # 21: a second dip, no new high in between


def test_pullback_buys_the_first_dip_to_the_ema_after_a_new_high():
    rows = pb_rows()
    ctx = dataclasses.replace(ctx_of(rows), trend1h=[1] * len(rows))
    assert ctx.l[20] <= ctx.ema[20] < ctx.c[20]
    (s,) = E.pullback_signals(ctx, "1h", 0.5, 1.5)
    assert s.i == 20 and s.side == 1 and s.target_r == 1.5
    assert s.stop == pytest.approx(ctx.l[20] - 0.5 * ctx.atr[20])
    assert E.pullback_signals(dataclasses.replace(ctx, trend1h=[0] * len(rows)), "1h", 0.5, 1.5) == []
    assert E.pullback_signals(ctx, "4h", 0.5, 1.5) == []                  # trend4h unknown here
    down = dataclasses.replace(ctx, trend1h=[-1] * len(rows))
    assert E.pullback_signals(down, "1h", 0.5, 1.5) == []                 # no new session low: never armed


def test_pullback_mirrors_for_a_down_trend():
    rows = [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in pb_rows()]
    ctx = dataclasses.replace(ctx_of(rows), trend1h=[-1] * len(rows))
    (s,) = E.pullback_signals(ctx, "1h", 1.5, 3.0)
    assert s.i == 20 and s.side == -1 and s.stop == pytest.approx(ctx.h[20] + 1.5 * ctx.atr[20])


def test_bigbar_continuation_and_fade():
    rows = flat(15) + [(100, 101.0, 99.9, 100.9)]                         # range 1.1 against an ATR of 0.2
    ctx = ctx_of(rows)
    (c,) = E.bigbar_signals(ctx, 3.0, "cont", 1.5, 30)
    assert (c.i, c.side) == (15, 1) and c.stop == pytest.approx(100.9 - 0.55)
    (f,) = E.bigbar_signals(ctx, 3.0, "fade", 3.0, 120)
    assert f.side == -1 and f.stop == pytest.approx(100.9 + 0.55) and f.expire_ms == ctx.t[15] + F + 120 * 60_000
    assert E.bigbar_signals(ctx, 6.0, "cont", 1.5, 30) == []              # not big enough
    doji = ctx_of(flat(15) + [(100, 101.0, 99.9, 100.0)])
    assert E.bigbar_signals(doji, 3.0, "cont", 1.5, 30) == []             # no body, no direction


def test_thesis_only_keeps_the_thesis_side():
    ctx = dataclasses.replace(ctx_of(flat(4)), thesis=[1, 1, -1, 0])
    sig = [Signal(0, 1, 99, FAR), Signal(1, -1, 101, FAR), Signal(2, -1, 101, FAR), Signal(3, 1, 99, FAR)]
    assert [s.i for s in E.thesis_only(ctx, sig)] == [0, 2]
    v = Variant("bb", (3.0, "cont", 1.5, 30))
    rows = flat(15) + [(100, 101.0, 99.9, 100.9)]
    long_ctx = dataclasses.replace(ctx_of(rows), thesis=[-1] * 16)
    assert len(E.signals_for(v, long_ctx, {})) == 1
    assert E.signals_for(dataclasses.replace(v, thesis=True), long_ctx, {}) == []


# ── walker and costs ───────────────────────────────────────────────────────

LONG = Signal(0, 1, 99.5, FAR, target_r=2.0)
WIN = [(100, 100.1, 99.9, 100), (100, 100.2, 99.9, 100.1), (100.1, 101.2, 100.0, 101.1), (101.1, 101.2, 101, 101.1)]


def test_target_win_is_two_r_with_no_costs():
    tr = one(E.walk5(ctx_of(WIN), [LONG], P, 0.06, FREE, EQ))
    assert tr["entry_px"] == 100.0 and tr["target_px"] == 101.0 and tr["reason"] == "target"
    assert tr["notional"] == pytest.approx(0.06 * EQ / 0.005)             # the stop costs 6% of equity
    assert tr["net_usd"] == pytest.approx(120.0) and tr["r"] == pytest.approx(2.0)
    assert tr["risk_pct"] == pytest.approx(6.0) and tr["entry"] == SL._iso(T0 + F)


def test_small_risk_is_a_fifth_of_the_size():
    full = one(E.walk5(ctx_of(WIN), [LONG], P, E.RISK_FULL, FREE, EQ))
    small = one(E.walk5(ctx_of(WIN), [LONG], P, E.RISK_SMALL, FREE, EQ))
    assert small["notional"] == pytest.approx(full["notional"] / 5.0)
    assert small["r"] == pytest.approx(full["r"])


def test_fees_taker_in_maker_out_at_the_target():
    sc = Scenario("fee", "next", 0.001, 0.0005, "zero")
    tr = one(E.walk5(ctx_of(WIN), [LONG], P, 0.06, sc, EQ))
    notional = 0.06 * EQ / (0.005 + 2 * 0.001)
    size = notional / 100.0
    assert tr["notional"] == pytest.approx(notional)
    assert tr["fees_usd"] == pytest.approx(0.001 * notional + 0.0005 * 101.0 * size)
    assert tr["net_usd"] == pytest.approx(size * 1.0 - tr["fees_usd"])


def test_a_target_needs_a_trade_through_and_never_fills_on_the_fill_bar():
    touch = [WIN[0], (100, 100.2, 99.9, 100.1), (100.1, 101.0, 100.0, 100.9), (100.9, 101.0, 100.8, 100.9)]
    assert one(E.walk5(ctx_of(touch), [LONG], P, 0.06, FREE, EQ))["reason"] == "open"
    spike = [WIN[0], (100, 101.5, 99.9, 100.1), (100.1, 100.2, 100.0, 100.1)]
    assert one(E.walk5(ctx_of(spike), [LONG], P, 0.06, FREE, EQ))["reason"] == "open"


def test_stop_on_the_fill_bar_and_a_gap_through_it():
    hit = [WIN[0], (100, 100.1, 99.4, 99.6), (99.6, 99.7, 99.5, 99.6)]
    tr = one(E.walk5(ctx_of(hit), [LONG], P, 0.06, FREE, EQ))
    assert tr["reason"] == "stop" and tr["exit_px"] == 99.5 and tr["r"] == pytest.approx(-1.0)
    gap = [WIN[0], (100, 100.1, 99.9, 100), (99.0, 99.1, 98.9, 99.0)]
    tr = one(E.walk5(ctx_of(gap), [LONG], P, 0.06, FREE, EQ))
    assert tr["reason"] == "stop" and tr["exit_px"] == 99.0 and tr["r"] == pytest.approx(-2.0)
    both = [WIN[0], (100, 100.1, 99.9, 100), (100, 101.5, 99.4, 100)]      # stop and target on one bar: the loss
    assert one(E.walk5(ctx_of(both), [LONG], P, 0.06, FREE, EQ))["reason"] == "stop"


def test_short_is_the_mirror():
    rows = [(100, 100.1, 99.9, 100), (100, 100.1, 99.8, 99.9), (99.9, 100.0, 98.8, 98.9)]
    tr = one(E.walk5(ctx_of(rows), [Signal(0, -1, 100.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ))
    assert tr["side"] == "short" and tr["target_px"] == 99.0 and tr["reason"] == "target"
    assert tr["net_usd"] == pytest.approx(120.0)


def test_time_stop_closes_at_the_open():
    rows = [WIN[0]] + [(100, 100.2, 99.9, 100.1), (100.2, 100.3, 100.1, 100.2), (100.3, 100.4, 100.2, 100.3)]
    sig = Signal(0, 1, 99.5, T0 + 3 * F, target_r=2.0)
    tr = one(E.walk5(ctx_of(rows), [sig], P, 0.06, FREE, EQ))
    assert tr["reason"] == "time" and tr["exit_px"] == 100.3 and tr["hold_min"] == 10.0
    late = Signal(0, 1, 99.5, T0 + F, target_r=2.0)                       # already expired at the fill
    run = E.walk5(ctx_of(rows), [late], P, 0.06, FREE, EQ)
    assert run["trades"] == [] and run["refused"] == {"late": 1}


def test_weekend_flat_no_entry_and_an_open_trade_closed():
    fri = ms(2026, 11, 6, 20, 45)                                         # the weekend opens at 21:00Z
    rows = flat(6)
    ctx = ctx_of(rows, t0=fri)
    assert ctx.weekend == [False, False, False, True, True, True]
    tr = one(E.walk5(ctx, [Signal(0, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ))
    assert tr["reason"] == "weekend" and tr["exit"] == SL._iso(fri + 3 * F)
    run = E.walk5(ctx, [Signal(2, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ)
    assert run["trades"] == [] and run["refused"] == {"weekend": 1}


def test_one_position_at_a_time():
    rows = flat(8)
    sig = [Signal(0, 1, 99.5, T0 + 4 * F, target_r=2.0), Signal(1, 1, 99.5, FAR, target_r=2.0),
           Signal(4, 1, 99.5, FAR, target_r=2.0)]
    run = E.walk5(ctx_of(rows), sig, P, 0.06, FREE, EQ)
    assert [t["reason"] for t in run["trades"]] == ["time", "open"] and run["refused"] == {"busy": 1}
    assert run["trades"][1]["entry"] == SL._iso(T0 + 5 * F)


def test_watcher_fills_one_bar_later_and_pays_half_the_bar_range():
    rows = [WIN[0], (100, 100.2, 99.9, 100.1), (100.2, 100.8, 100.2, 100.6), (100.6, 100.7, 100.5, 100.6)]
    sc = Scenario("w", "watcher", 0.0, 0.0, "range")
    tr = one(E.walk5(ctx_of(rows), [LONG], P, 0.06, sc, EQ))
    slip = (100.8 - 100.2) / 100.2 / 2.0
    assert tr["entry"] == SL._iso(T0 + 2 * F) and tr["entry_px"] == pytest.approx(100.2 * (1 + slip), abs=1e-4)
    assert tr["slip_usd"] == pytest.approx(100.2 * slip * tr["size"])
    calm = [WIN[0], WIN[0], (100, 100.01, 100.0, 100.0), (100, 100.01, 100.0, 100.0)]
    tr = one(E.walk5(ctx_of(calm), [LONG], P, 0.06, sc, EQ))
    assert tr["entry_px"] == pytest.approx(100.0 * (1 + P.slippage))      # never under the sim's 3 bps


def test_stop_slippage_by_scenario():
    hit = [WIN[0], (100, 100.1, 99.4, 99.6), (99.6, 99.7, 99.5, 99.6)]
    rng = one(E.walk5(ctx_of(hit), [LONG], P, 0.06, Scenario("w", "next", 0.0, 0.0, "range"), EQ))
    assert rng["exit_px"] == pytest.approx(99.5 * (1 - P.slippage), abs=1e-4)
    bot = one(E.walk5(ctx_of(hit), [LONG], P, 0.06, Scenario("b", "next", 0.0, 0.0, "curve"), EQ))
    assert bot["entry_px"] == pytest.approx(100.0 * (1 + E.exit_slip_bps(bot["notional"]) / 1e4), abs=1e-4)
    assert 99.5 * (1 - P.slippage) < bot["exit_px"] < 99.5


def test_refusals_stop_too_close_no_room_no_bar():
    ctx = ctx_of(WIN)
    assert E.walk5(ctx, [Signal(0, 1, 99.99, FAR, target_r=2.0)], P, 0.06, FREE, EQ)["refused"] == {"past_stop": 1}
    assert E.walk5(ctx, [Signal(0, 1, 100.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ)["refused"] == {"past_stop": 1}
    assert E.walk5(ctx, [Signal(0, -1, 100.5, FAR, target_px=100.2)], P, 0.06, FREE, EQ)["refused"] == {"no_room": 1}
    assert E.walk5(ctx, [Signal(3, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ)["refused"] == {"no_bar": 1}
    assert E.walk5(ctx, [Signal(0, 1, 80.0, FAR, target_r=2.0)], P, 0.06, FREE, EQ)["refused"] == {"leverage": 1}


def test_funding_is_paid_on_the_hour_by_the_long():
    t0 = T0 + H - 2 * F                                                   # the third bar opens on the hour
    rows = flat(5)
    ctx = dataclasses.replace(ctx_of(rows, t0=t0), fund={T0 + H: 0.001})
    tr = one(E.walk5(ctx, [Signal(0, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ))
    assert tr["funding_usd"] == pytest.approx(0.001 * tr["size"] * 100.0)
    assert tr["net_usd"] == pytest.approx(-tr["funding_usd"])
    sh = one(E.walk5(ctx, [Signal(0, -1, 100.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ))
    assert sh["funding_usd"] == pytest.approx(-0.001 * sh["size"] * 100.0)


def test_beside_the_live_book_no_entry_while_long_and_closed_at_its_entry():
    rows = flat(6)
    ctx = ctx_of(rows)
    n = len(rows)
    live = Live([2000.0] * n, [False, False, False, True, True, True], [False, False, False, True, False, False])
    tr = one(E.walk5(ctx, [Signal(0, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ, live=live))
    assert tr["reason"] == "long_entry" and tr["exit"] == SL._iso(T0 + 3 * F)
    assert tr["notional"] == pytest.approx(0.06 * 2000.0 / 0.005)         # sized off the live book's equity
    run = E.walk5(ctx, [Signal(3, 1, 99.5, FAR, target_r=2.0)], P, 0.06, FREE, EQ, live=live)
    assert run["trades"] == [] and run["refused"] == {"live_long": 1}


def test_window_bounds_signals_and_marks_an_open_trade():
    rows = flat(8)
    ctx = ctx_of(rows)
    sig = [Signal(0, 1, 99.5, FAR, target_r=2.0), Signal(5, 1, 99.5, FAR, target_r=2.0)]
    run = E.walk5(ctx, sig, P, 0.06, FREE, EQ, lo_ms=T0 + 2 * F, hi_ms=T0 + 7 * F)
    tr = one(run)
    assert tr["entry"] == SL._iso(T0 + 6 * F) and tr["reason"] == "open" and tr["exit"] is None


# ── summaries and verdicts ─────────────────────────────────────────────────

def test_curve_summary_and_hours():
    ctx = ctx_of(WIN)
    run = E.walk5(ctx, [LONG], P, 0.06, FREE, EQ)
    curve = E.pnl_curve(ctx, run["trades"], 0, 3)
    assert curve[0] == 0.0 and curve[1] == pytest.approx(0.1 * 120) and curve[2] == curve[3] == pytest.approx(120.0)
    s = E.summarize(run["trades"], curve, [EQ] * 4)
    assert (s["n"], s["wins"], s["win_rate"], s["net_usd"], s["max_dd_pct"]) == (1, 1, 100.0, 120.0, 0.0)
    assert s["raw_bps"] == pytest.approx(100.0) and s["cost_bps"] == 0.0 and s["profit_factor"] is None
    empty = E.summarize([])
    assert empty["n"] == 0 and empty["win_rate"] is None and empty["exp_usd"] is None
    h = E.by_hour([{"hour": 7, "net_usd": -3.0}, {"hour": 7, "net_usd": 1.0}, {"hour": 13, "net_usd": 5.0}])
    assert h["best"] == {"hour": 13, "n": 1, "net_usd": 5.0} and h["worst"] == {"hour": 7, "n": 2, "net_usd": -2.0}
    assert E.by_hour([]) == {"hours": {}, "best": None, "worst": None}


def test_drawdown_is_read_on_the_marked_curve():
    hit = [WIN[0], (100, 100.1, 99.4, 99.6), (99.6, 99.7, 99.5, 99.6)]
    ctx = ctx_of(hit)
    run = E.walk5(ctx, [LONG], P, 0.06, FREE, EQ)
    s = E.summarize(run["trades"], E.pnl_curve(ctx, run["trades"], 0, 2), [EQ] * 3)
    assert s["net_usd"] == -60.0 and s["max_dd_pct"] == 6.0 and s["profit_factor"] == 0.0


def judged(n, net=5.0, dd=10.0, halves=(1.0, 1.0)):
    ds = {"combined": {"net_usd": net, "n": n}, "combined_dd_pct": dd, "live": {"max_dd_pct": 20.0}}
    row = {"datasets": {E.PRIMARY: ds, "wti_5m": ds}, "halves": list(halves), "drop_best_day": {"pass": True}}
    return SL.judge(row, [1.0, 2.0], E.PRIMARY, min_n=E.MIN_N, reopen_leg=False)["verdict"]


def test_under_thirty_trades_is_never_a_pass():
    assert judged(30) == "PASS" and judged(29) == "LEANING"
    assert judged(29, net=-1.0) == "NOISE" and judged(30, net=-1.0) == "FAIL"
    assert judged(30, dd=25.0) == "FAIL" and judged(30, halves=(1.0, -1.0)) == "FAIL"


def test_family_verdict():
    assert E.family_verdict(["NOISE", "PASS", "FAIL"]) == "PASS"
    assert E.family_verdict(["NOISE", "LEANING", "FAIL"]) == "LEANING"
    assert E.family_verdict(["NOISE", "FAIL"]) == "FAIL"
    assert E.family_verdict(["NOISE"]) == "NOISE"
    assert E.family_verdict(["NOISE"], alone_judged=4, alone_winners=0) == "FAIL"     # lost alone with the trades
    assert E.family_verdict(["NOISE"], alone_judged=4, alone_winners=1) == "NOISE"


def test_cost_table_and_latency_drift():
    rows = [(100, 100.2, 99.9, 100.1)] * 20
    ctx = ctx_of(rows)
    scs = E.scenarios(0.00009, 0.00003, P)
    c = E.cost_table(ctx, 0, 19, scs, P, 3000.0)
    assert c["median_body_bps"] == pytest.approx(10.0) and c["median_range_bps"] == pytest.approx(30.0)
    w = c["scenarios"]["watcher"]
    assert w["entry_slip_bps"] == pytest.approx(15.0) and w["stop_slip_bps"] == pytest.approx(3.0)
    assert w["round_trip_stop_bps"] == pytest.approx(0.9 * 2 + 15.0 + 3.0)
    assert c["scenarios"]["free"]["round_trip_stop_bps"] == 0.0
    assert c["scenarios"]["repo_fee"]["round_trip_stop_bps"] == pytest.approx(9.0 + 18.0)
    m = [Candle(t=T0 + i * 60_000, o=100.0 + (0.05 if i % 5 == 2 else 0.0), h=100.1, l=99.9, c=100.0, v=1.0)
         for i in range(10)]
    assert E.latency_drift(m) == {"n": 2, "minutes": 2, "median_bps": 5.0, "p90_bps": 5.0}
    assert E.latency_drift([]) == {"n": 0}


def test_merge_and_contiguous():
    a = bars(flat(3))
    b = bars(flat(2, px=50.0), t0=T0 + 2 * F)
    m = E.merge_candles(a, b)
    assert [c.t for c in m] == [T0 + i * F for i in range(4)] and m[2].o == 50.0     # the later set wins
    holed = a + bars(flat(3), t0=T0 + 50 * F)
    assert [c.t for c in E.contiguous_from(holed)] == [T0 + (50 + i) * F for i in range(3)]
    assert E.contiguous_from(a) == a


def test_cli_help_and_no_journal(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        E.main(["--help"])
    assert e.value.code == 0 and "Research only" in capsys.readouterr().out
    assert E.main(["--oil-theses-dir", str(tmp_path / "none"), "--out-dir", str(tmp_path / "out"),
                   "--cache-dir", str(tmp_path / "cache")]) == 2
