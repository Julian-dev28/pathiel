"""Gate tests for the long ladder / short-to-entry / short ladder eval
(services/strategies/evals/short_ladder_test.py, 2026-11-16): the ladder
sizing math ("risk 20%" = 20% of the normal 6%), every entry trigger
including the daily-close rule, the walkers on hand-built ticks, the
collision and missed-thesis bookkeeping and the bar's verdict labels.
Synthetic ticks only, no network, no state read.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import _full_size_notional
from services.strategies.evals import short_ladder_test as E
from services.strategies.evals.short_ladder_test import Tick, Variant

Q = E.FIFTEEN_MS
H = E.HOUR_MS
DAY = E.DAY_MS
T0 = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)   # a Tuesday, 00:00Z
EQ = 1000.0
P = E.base_params(EQ, E.RISK_FRAC)
NORMAL = E.RISK_FRAC * EQ            # $60: the normal per-trade risk on $1000


def tk(i, o, h, l, c, *, t=None, side="long", ceiling=100.0, tid="th1", **kw):
    base = dict(ok=True, hi24=o + 2.0, lo24=o - 2.0, atr=1.0, vwap24=100.0, side=side, ceiling=ceiling,
                thesis_id=tid, target=104.0)
    base.update(kw)
    return Tick(t=T0 + Q + i * Q if t is None else t, o=o, h=h, l=l, c=c, **base)


def one(run):
    assert len(run["trades"]) == 1, run["trades"]
    return run["trades"][0]


# ── the grid ───────────────────────────────────────────────────────────────

def test_grid_sizes_and_unique_names():
    a, b, d = E.entry_variants(), E.ladder_variants(), E.long_variants()
    assert len(a) == 4 * 3 * 2 * 2 == 48
    assert len(b) == 2 * 4 * 2 * 4 * 3 == 192
    assert len(d) == 4 * 2 * 4 * 3 * 2 * 3 * 3 == 1728
    names = [v.name for v in a + b + d]
    assert len(set(names)) == len(names)
    assert {v.name[0] for v in a} == {"A"} and {v.name[0] for v in b} == {"B"} and {v.name[0] for v in d} == {"D"}


def test_the_operators_example_is_in_the_grid():
    """+1% over the entry at 20% of normal risk, more at +1.5% and +2%, with
    and without a daily close."""
    names = {v.name for v in E.long_variants()}
    assert "D_f1_t3_ramp_touch_thesis_lo24_r0" in names and "D_f1_t3_flat20_eod00_thesis_lo24_r0" in names
    assert E.long_levels(100.0, 0.01, 3) == pytest.approx([101.0, 101.5, 102.0])
    assert "B_thesis_s1_t2_flat20_eod00" in {v.name for v in E.ladder_variants()}


def test_risk_shares_per_scheme_and_tier_count():
    assert Variant("ladder", scheme="ramp", tiers=3).risk == (0.2, 0.4, 0.6)
    assert Variant("ladder", scheme="ramp", tiers=2).risk == (0.2, 0.4)
    assert Variant("long", scheme="flat40", tiers=3).risk == (0.4, 0.4, 0.4)
    assert Variant("entry", risk_scale=0.2).risk == (0.2,)


def test_neighbours_move_one_ordered_axis_one_step():
    v = Variant("ladder", step=0.01, tiers=2, scheme="flat40")
    got = {n.name for n in E.neighbours(v)}
    assert got == {dataclass_name(v, step=0.005), dataclass_name(v, step=0.015), dataclass_name(v, tiers=3),
                   dataclass_name(v, scheme="flat20"), dataclass_name(v, scheme="flat60")}
    assert [n.scheme for n in E.neighbours(Variant("ladder", scheme="ramp", step=0.005, tiers=3))] == ["ramp", "ramp"]
    a = Variant("entry", stop="hi24", min_r=0.0)
    assert [n.min_r for n in E.neighbours(a)] == [1.0]            # hi24 has no stop neighbour
    assert {n.stop for n in E.neighbours(Variant("entry", stop="atr1.5", min_r=1.0))} == {"atr1.0", "atr2.0", "atr1.5"}
    d = Variant("long", stop="atr1.5", min_r=2.0, step=0.02, tiers=3, scheme="flat60")
    assert {(n.stop, n.min_r, n.step, n.tiers, n.scheme) for n in E.neighbours(d)} == {
        ("atr1.5", 2.0, 0.015, 3, "flat60"), ("atr1.5", 2.0, 0.02, 2, "flat60"), ("atr1.5", 2.0, 0.02, 3, "flat40"),
        ("atr1.5", 1.0, 0.02, 3, "flat60"), ("atr2.0", 2.0, 0.02, 3, "flat60")}


def dataclass_name(v, **over):
    import dataclasses
    return dataclasses.replace(v, **over).name


# ── sizing math ────────────────────────────────────────────────────────────

def test_risk_20_percent_means_a_fifth_of_the_normal_six_percent():
    assert E.RISK_FRAC == pytest.approx(0.06)
    assert E.tier_risk_frac(0.2) == pytest.approx(0.012)        # 1.2% of equity
    assert E.tier_risk_frac(0.4) == pytest.approx(0.024)
    assert E.tier_risk_frac(0.6) == pytest.approx(0.036)
    assert E.tier_risk_frac(1.0) == pytest.approx(0.06)


@pytest.mark.parametrize("share", [0.2, 0.4, 0.6, 1.0])
def test_tier_notional_loses_its_share_of_normal_risk_at_the_stop(share):
    entry, stop = 101.0, 102.0
    n = E.tier_notional(EQ, P, 20.0, entry, stop, share)
    per_dollar = (stop - entry) / entry + 2 * P.taker_fee + P.slippage
    assert n * per_dollar == pytest.approx(share * NORMAL)
    assert n == pytest.approx(share / 0.2 * E.tier_notional(EQ, P, 20.0, entry, stop, 0.2))


def test_tier_notional_is_capped_by_what_is_left_of_the_full_port_size():
    full = _full_size_notional(EQ, P, 20.0)
    tight = E.tier_notional(EQ, P, 20.0, 101.0, 101.05, 1.0)       # a stop this close wants more than the account has
    assert tight == pytest.approx(full)
    assert E.tier_notional(EQ, P, 20.0, 101.0, 101.05, 1.0, open_notional=full - 500.0) == pytest.approx(500.0)
    assert E.tier_notional(EQ, P, 20.0, 101.0, 101.05, 1.0, open_notional=full + 1.0) == 0.0


def test_ladder_levels_and_the_shared_stop():
    assert E.tier_levels(100.0, 0.01, 3) == pytest.approx([101.0, 102.0, 103.0])
    assert E.ladder_stop(100.0, 0.01, 3) == pytest.approx(104.0)   # one step above the last tier
    assert E.ladder_stop(100.0, 0.005, 2) == pytest.approx(101.5)
    assert E.long_levels(100.0, 0.005, 2) == pytest.approx([100.5, 101.0])


def test_stop_rules():
    assert E.entry_stop("hi24", 101.0, 103.0, 1.0, 0.75) == pytest.approx(103.75)
    assert E.entry_stop("hi24", 104.0, 103.0, 1.0, 0.75) == pytest.approx(104.75)   # already over the high
    assert E.entry_stop("atr1.5", 101.0, 103.0, 0.8, 0.75) == pytest.approx(102.2)
    assert E.long_stop("lo24", 101.0, 99.0, 0.5, 0.75) == pytest.approx(98.25)       # the live long rule's stop
    assert E.long_stop("atr2.0", 101.0, 99.0, 0.5, 0.75) == pytest.approx(100.0)
    with pytest.raises(ValueError):
        E.entry_stop("nope", 1, 1, 1, 1)
    with pytest.raises(ValueError):
        E.long_stop("nope", 1, 1, 1, 1)


def test_planned_r_and_the_operators_short():
    assert E.planned_r(101.0, 102.0, 99.0) == pytest.approx(2.0)
    op = E.operator_short(92.714, 93.80, 92.48, P)
    assert op["planned_r"] == pytest.approx(0.215, abs=1e-3)
    assert op["r_after_costs"] < op["planned_r"]
    assert op["breakeven_win_rate_pct"] > 80.0


# ── triggers ───────────────────────────────────────────────────────────────

def test_waiting_for_dip_is_a_long_thesis_with_the_mid_above_its_entry():
    assert E.waiting_for_dip("long", 92.7, 92.48)
    assert not E.waiting_for_dip("long", 92.48, 92.48)
    assert not E.waiting_for_dip("long", 92.0, 92.48)
    assert not E.waiting_for_dip("short", 92.7, 92.48)
    assert not E.waiting_for_dip("flat", 92.7, 92.48)
    assert not E.waiting_for_dip("long", 92.7, None)
    assert not E.waiting_for_dip(None, 92.7, 92.48)


def test_touch_tier_fills_at_the_level_as_a_maker_or_at_the_open_as_a_taker():
    assert E.tier_fill("touch", 101.0, T0 + Q, 100.5, 100.9, None) is None
    assert E.tier_fill("touch", 101.0, T0 + Q, 100.5, 101.0, None) == (101.0, True)
    assert E.tier_fill("touch", 101.0, T0 + Q, 101.4, 101.6, None) == (101.4, False)


@pytest.mark.parametrize("confirm,hour", [("eod00", 0), ("eod21", 21)])
def test_eod_tier_needs_the_daily_close_above_the_level(confirm, hour):
    eod = T0 + hour * H
    assert E.is_eod(eod, hour) and not E.is_eod(eod + Q, hour) and not E.is_eod(eod - H, hour)
    # an intraday spike over the level is not a signal
    assert E.tier_fill(confirm, 101.0, eod + 5 * H, 101.5, 102.0, 101.8) is None
    # the daily close above it is: sell the next bar's open, as a taker
    assert E.tier_fill(confirm, 101.0, eod, 101.1, 101.3, 101.2) == (101.1, False)
    # a close AT the level, under it, or no 1h bar closing there: nothing
    assert E.tier_fill(confirm, 101.0, eod, 101.1, 101.3, 101.0) is None
    assert E.tier_fill(confirm, 101.0, eod, 100.9, 101.3, 100.9) is None
    assert E.tier_fill(confirm, 101.0, eod, 101.1, 101.3, None) is None
    with pytest.raises(ValueError):
        E.tier_fill("eod13", 101.0, eod, 101.1, 101.3, 101.2)


def test_the_two_eod_hours_are_different_bars():
    assert E.tier_fill("eod00", 101.0, T0 + 21 * H, 101.1, 101.3, 101.2) is None
    assert E.tier_fill("eod21", 101.0, T0, 101.1, 101.3, 101.2) is None


def test_long_tier_is_a_buy_stop():
    assert E.long_tier_fill("touch", 101.0, T0 + Q, 100.5, 100.9, None) is None
    assert E.long_tier_fill("touch", 101.0, T0 + Q, 100.5, 101.2, None) == 101.0
    assert E.long_tier_fill("touch", 101.0, T0 + Q, 101.4, 101.6, None) == 101.4
    assert E.long_tier_fill("eod00", 101.0, T0, 101.3, 101.6, 101.2) == 101.3
    assert E.long_tier_fill("eod00", 101.0, T0 + Q, 101.3, 101.6, 101.2) is None


def test_reopen_tick_is_only_the_first_bar_after_the_weekend():
    sunday = int(datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 18:00 New York, winter
    assert E.reopen_tick(sunday)
    assert not E.reopen_tick(sunday + Q) and not E.reopen_tick(sunday - Q) and not E.reopen_tick(T0)


def test_vwap_weights_by_volume_and_falls_back_to_the_mean():
    bars = [Candle(t=0, o=1, h=10, l=10, c=10, v=1.0), Candle(t=1, o=1, h=20, l=20, c=20, v=3.0)]
    assert E.vwap(bars) == pytest.approx(17.5)
    flat = [Candle(t=0, o=1, h=10, l=10, c=10, v=0.0), Candle(t=1, o=1, h=20, l=20, c=20, v=0.0)]
    assert E.vwap(flat) == pytest.approx(15.0)
    assert E.vwap([]) is None


# ── A: short to the entry ──────────────────────────────────────────────────

A1 = Variant("entry", stop="atr1.0", min_r=0.0)


def test_short_to_entry_opens_above_the_entry_and_takes_the_entry_as_target():
    run = E.walk([tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 100.8, 100.9, 99.9, 100.0)], A1, P, EQ)
    t = one(run)
    assert t["reason"] == "target" and t["exit_px"] == 100.0 and t["target_px"] == 100.0
    assert t["stop_px"] == pytest.approx(102.0) and t["entry_px"] < 101.0     # sold the mid less slippage
    assert t["net_usd"] > 0 and run["curve"][-1] == pytest.approx(t["net_usd"])


def test_short_to_entry_needs_the_desk_to_be_waiting():
    bars = [tk(0, 99.5, 99.8, 99.4, 99.6), tk(1, 99.6, 99.9, 99.5, 99.7)]
    assert E.walk(bars, A1, P, EQ)["trades"] == []                 # at or under the entry: the long's turf
    up = [tk(0, 101.0, 101.2, 100.9, 101.0, side="short"), tk(1, 101.0, 101.2, 100.9, 101.0, side="flat")]
    assert E.walk(up, A1, P, EQ)["trades"] == []                   # no long thesis, no short to its entry
    cold = [tk(0, 101.0, 101.2, 100.9, 101.0, ok=False)]
    assert E.walk(cold, A1, P, EQ)["trades"] == []
    wk = [tk(0, 101.0, 101.2, 100.9, 101.0, weekend=True)]
    assert E.walk(wk, A1, P, EQ)["trades"] == []


def test_a_stopped_short_loses_about_the_normal_risk_and_a_fifth_at_risk_20():
    bars = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.5, 102.1, 101.4, 102.0)]
    full = one(E.walk(bars, A1, P, EQ))
    assert full["reason"] == "stop" and -NORMAL * 1.1 < full["net_usd"] < -NORMAL * 0.95
    fifth = one(E.walk(bars, Variant("entry", stop="atr1.0", risk_scale=0.2), P, EQ))
    assert fifth["net_usd"] == pytest.approx(full["net_usd"] * 0.2, rel=1e-6)


def test_min_r_gate_and_the_leverage_fit():
    bars = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.0, 101.2, 100.9, 101.0)]
    run = E.walk(bars, Variant("entry", stop="atr1.0", min_r=2.0), P, EQ)      # 1R planned
    assert run["trades"] == [] and run["refused"]["min_r"] == 2
    wide = [tk(0, 101.0, 101.2, 100.9, 101.0, atr=1.8)]                         # 1.8% stop: 15x, not 20x
    t = one(E.walk(wide, A1, P, EQ))
    assert t["leverage"] == 15.0 and NT.LEVERAGE_FIT == (20.0, 15.0)
    too_wide = [tk(0, 101.0, 101.2, 100.9, 101.0, atr=3.0)]
    run = E.walk(too_wide, A1, P, EQ)
    assert run["trades"] == [] and run["refused"]["leverage"] == 1


def test_one_short_per_thesis_unless_reentry_is_on():
    bars = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.5, 102.1, 101.4, 102.0), tk(2, 101.9, 102.0, 101.8, 101.9),
            tk(3, 101.9, 102.0, 101.8, 101.9)]
    assert len(E.walk(bars, A1, P, EQ)["trades"]) == 1
    again = E.walk(bars, Variant("entry", stop="atr1.0", reentry=True), P, EQ)["trades"]
    assert [t["reason"] for t in again] == ["stop", "open"]
    assert again[1]["entry_ms"] > again[0]["exit_ms"]              # never on the bar it was stopped on
    fresh = bars[:2] + [tk(2, 101.9, 102.0, 101.8, 101.9, tid="th2"), tk(3, 101.9, 102.0, 101.8, 101.9, tid="th2")]
    assert len(E.walk(fresh, A1, P, EQ)["trades"]) == 2


def test_the_short_closes_when_the_thesis_stops_being_long_or_price_is_at_its_entry():
    gone = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.1, 101.2, 100.9, 101.0, side="flat", ceiling=None)]
    assert one(E.walk(gone, A1, P, EQ))["reason"] == "thesis_change"
    moved = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.1, 101.2, 100.9, 101.0, ceiling=101.5, tid="th2")]
    assert one(E.walk(moved, A1, P, EQ))["reason"] == "at_entry"   # a rewrite put the entry above the mid
    wk = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.1, 101.2, 100.9, 101.0, weekend=True)]
    assert one(E.walk(wk, A1, P, EQ))["reason"] == "weekend"


def test_beside_the_live_long_no_short_while_it_is_open_and_out_when_it_enters():
    bars = [tk(i, 101.0, 101.2, 100.9, 101.0) for i in range(5)]
    curve = [EQ] * 5
    blocked = E.walk(bars, A1, P, EQ, curve, [True, True, False, False, False], [True, False, False, False, False])
    assert one(blocked)["entry_ms"] == bars[2].t
    cut = E.walk(bars, A1, P, EQ, curve, [False, False, False, True, True], [False, False, False, True, False])
    t = one(cut)
    assert t["entry_ms"] == bars[0].t and t["exit_ms"] == bars[3].t and t["reason"] == "long_entry"


def test_combined_sizing_reads_the_live_books_equity():
    bars = [tk(0, 101.0, 101.2, 100.9, 101.0), tk(1, 101.5, 102.1, 101.4, 102.0)]
    small = one(E.walk(bars, A1, P, EQ, [EQ, EQ], [False, False], [False, False]))
    big = one(E.walk(bars, A1, P, EQ, [2 * EQ, 2 * EQ], [False, False], [False, False]))
    assert big["net_usd"] == pytest.approx(2 * small["net_usd"], rel=1e-6)


# ── B: the short ladder ────────────────────────────────────────────────────

B2 = Variant("ladder", ref="thesis", step=0.01, tiers=2, scheme="flat20", confirm="touch")
UP = [tk(0, 100.5, 100.8, 100.4, 100.6), tk(1, 100.8, 101.3, 100.7, 101.2), tk(2, 101.2, 102.2, 101.1, 102.1)]


def test_ladder_does_nothing_under_the_first_tier():
    assert E.walk(UP[:1], B2, P, EQ)["trades"] == []


def test_ladder_fills_tier_by_tier_and_stops_one_step_above_the_last():
    run = E.walk(UP + [tk(3, 102.5, 103.1, 102.4, 103.0)], B2, P, EQ)
    t = one(run)
    assert t["reason"] == "stop" and t["tiers"] == 2 and t["stop_px"] == pytest.approx(103.0)
    # equal risk on a tier twice as close to the stop is twice the size: the average sits nearer tier 2
    assert t["target_px"] == 100.0 and 101.5 < t["entry_px"] < 102.0
    assert t["leverage"] == 15.0                                    # 2% from tier 1 to the stop: the 15x fit
    # two tiers at 20% each: about 40% of the normal risk
    assert -0.4 * NORMAL * 1.1 < t["net_usd"] < -0.4 * NORMAL * 0.9


def test_ramp_puts_more_risk_on_the_higher_tier():
    ramp = one(E.walk(UP + [tk(3, 102.5, 103.1, 102.4, 103.0)],
                      Variant("ladder", step=0.01, tiers=2, scheme="ramp"), P, EQ))
    assert -0.6 * NORMAL * 1.1 < ramp["net_usd"] < -0.6 * NORMAL * 0.9      # 20% + 40%


def test_ladder_takes_the_reference_as_its_target():
    t = one(E.walk(UP[:2] + [tk(2, 100.9, 101.0, 99.9, 100.0)], B2, P, EQ))
    assert t["reason"] == "target" and t["exit_px"] == 100.0 and t["tiers"] == 1 and t["net_usd"] > 0


def test_a_fill_and_the_stop_on_one_bar_is_a_loss_never_a_win():
    spike = [tk(0, 100.5, 103.2, 99.5, 100.0)]                      # through both tiers, the stop and the target
    t = one(E.walk(spike, B2, P, EQ))
    assert t["reason"] == "stop" and t["net_usd"] < 0


def test_ladder_rearms_after_a_stop_only_once_price_is_back_at_the_reference():
    stopped = UP + [tk(3, 102.5, 103.1, 102.4, 103.0)]
    still_up = stopped + [tk(4, 101.5, 101.6, 101.2, 101.4), tk(5, 101.5, 101.6, 101.2, 101.4)]
    assert len(E.walk(still_up, B2, P, EQ)["trades"]) == 1
    back = stopped + [tk(4, 100.4, 100.5, 99.9, 100.2), tk(5, 100.5, 101.2, 100.4, 101.1)]
    got = E.walk(back, B2, P, EQ)["trades"]
    assert [t["reason"] for t in got] == ["stop", "open"]


def test_eod_ladder_ignores_the_spike_and_sells_the_daily_close():
    v = Variant("ladder", ref="thesis", step=0.01, tiers=2, scheme="flat20", confirm="eod00")
    spike = [tk(0, 100.5, 102.5, 100.4, 100.6, t=T0 + 15 * H)]
    assert E.walk(spike, v, P, EQ)["trades"] == []
    close = spike + [tk(0, 101.1, 101.3, 101.0, 101.2, t=T0 + DAY, close_1h=101.2)]
    t = one(E.walk(close, v, P, EQ))
    assert t["tiers"] == 1 and t["entry_ms"] == T0 + DAY and t["entry_px"] < 101.1     # a taker at the open
    under = spike + [tk(0, 100.9, 101.3, 100.8, 101.2, t=T0 + DAY, close_1h=100.9)]
    assert E.walk(under, v, P, EQ)["trades"] == []
    both = spike + [tk(0, 102.2, 102.3, 102.1, 102.2, t=T0 + DAY, close_1h=102.2)]
    assert one(E.walk(both, v, P, EQ))["tiers"] == 2                # a close over both tiers sells both


def test_vwap_reference_needs_no_thesis():
    v = Variant("ladder", ref="vwap24", step=0.01, tiers=2, scheme="flat20", confirm="touch")
    bars = [tk(0, 100.8, 101.3, 100.7, 101.2, side=None, ceiling=None, tid=None)]
    assert one(E.walk(bars, v, P, EQ))["tiers"] == 1
    assert E.walk(bars, B2, P, EQ)["trades"] == []                  # the thesis ladder has no reference here


# ── D: the long ladder ─────────────────────────────────────────────────────

D3 = Variant("long", step=0.01, tiers=3, scheme="ramp", confirm="touch", target="thesis", stop="atr2.0")
RISE = [tk(0, 100.5, 100.9, 100.4, 100.8, atr=0.5), tk(1, 100.9, 101.2, 100.8, 101.1, atr=0.5),
        tk(2, 101.3, 101.6, 101.2, 101.5, atr=0.5), tk(3, 102.0, 104.2, 101.9, 104.0, atr=0.5)]


def test_long_ladder_waits_for_plus_one_percent():
    assert E.walk_long(RISE[:1], D3, P, EQ)["trades"] == []


def test_long_ladder_buys_each_tier_and_takes_the_thesis_target():
    run = E.walk_long(RISE + [tk(4, 104.0, 104.5, 103.9, 104.2, atr=0.5)], D3, P, EQ)
    t = one(run)
    assert t["reason"] == "target" and t["exit_px"] == 104.0 and t["tiers"] == 3
    assert t["stop_px"] == pytest.approx(101.0 - 2 * 0.5) and t["net_usd"] > 0
    assert t["entry_ms"] == RISE[1].t and t["exit_ms"] == T0 + Q + 4 * Q    # never the target on a fill bar


def test_first_long_tier_risks_a_fifth_of_normal():
    bars = RISE[:2] + [tk(2, 100.6, 100.7, 99.9, 100.0, atr=0.5)]
    t = one(E.walk_long(bars, D3, P, EQ))
    assert t["reason"] == "stop" and t["tiers"] == 1
    assert -0.2 * NORMAL * 1.1 < t["net_usd"] < -0.2 * NORMAL * 0.9


def test_long_ladder_runs_once_per_thesis():
    bars = RISE[:2] + [tk(2, 100.6, 100.7, 99.9, 100.0, atr=0.5), tk(3, 101.3, 101.6, 101.2, 101.5, atr=0.5)]
    assert len(E.walk_long(bars, D3, P, EQ)["trades"]) == 1
    fresh = bars[:3] + [tk(3, 101.3, 101.6, 101.2, 101.5, atr=0.5, tid="th2")]
    assert len(E.walk_long(fresh, D3, P, EQ)["trades"]) == 2


def test_long_ladder_needs_a_long_thesis_room_to_the_target_and_min_r():
    assert E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, side="short")], D3, P, EQ)["trades"] == []
    assert E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, side="long", ceiling=None)], D3, P, EQ)["trades"] == []
    run = E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, target=100.9)], D3, P, EQ)
    assert run["trades"] == [] and run["refused"]["no_room"] >= 1
    strict = Variant("long", step=0.01, tiers=1, scheme="flat20", stop="atr2.0", min_r=2.0)
    run = E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, target=102.0, atr=0.5)], strict, P, EQ)   # 1R
    assert run["trades"] == [] and run["refused"]["min_r"] == 1


def test_long_ladder_structural_stop_is_the_live_rules_and_must_fit_the_leverage():
    v = Variant("long", step=0.01, tiers=1, scheme="flat20", stop="lo24")
    t = one(E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, lo24=100.0)], v, P, EQ))
    assert t["stop_px"] == pytest.approx(99.25) and t["leverage"] == 15.0
    run = E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, lo24=98.0)], v, P, EQ)      # 3.7% away: too wide for 15x
    assert run["trades"] == [] and run["refused"]["leverage"] == 1


def test_long_ladder_target_can_be_the_live_rules_24h_high():
    v = Variant("long", step=0.01, tiers=1, scheme="flat20", stop="atr2.0", target="hi24")
    t = one(E.walk_long([tk(1, 100.9, 101.2, 100.8, 101.1, hi24=103.0, atr=0.5)], v, P, EQ))
    assert t["target_px"] == pytest.approx(103.0 * (1 - P.target_offset_frac))


def test_eod_long_ladder_needs_the_daily_close():
    v = Variant("long", step=0.01, tiers=1, scheme="flat20", confirm="eod00", stop="atr2.0")
    spike = [tk(0, 100.5, 101.8, 100.4, 100.6, t=T0 + 15 * H, atr=0.5)]
    assert E.walk_long(spike, v, P, EQ)["trades"] == []
    close = spike + [tk(0, 101.3, 101.4, 101.2, 101.3, t=T0 + DAY, close_1h=101.2, atr=0.5)]
    assert one(E.walk_long(close, v, P, EQ))["entry_ms"] == T0 + DAY


def test_long_ladder_beside_the_live_book_yields_to_the_dip_entry():
    bars = RISE[:2] + [tk(i, 101.1, 101.2, 101.0, 101.1, atr=0.5) for i in (2, 3, 4)]
    curve = [EQ] * 5
    cut = one(E.walk_long(bars, D3, P, EQ, curve, [False, False, False, True, True], [False, False, False, True, False]))
    assert cut["reason"] == "live_entry" and cut["exit_ms"] == bars[3].t
    blocked = E.walk_long(bars, D3, P, EQ, curve, [True] * 5, [True] + [False] * 4)
    assert blocked["trades"] == []


def test_long_ladder_exits_on_invalidation_and_thesis_change():
    inv = RISE[:2] + [tk(2, 101.0, 101.1, 100.9, 101.0, atr=0.5, close_1h=100.4, invalid=100.5)]
    assert one(E.walk_long(inv, D3, P, EQ))["reason"] == "invalidation"
    flip = RISE[:2] + [tk(2, 101.0, 101.1, 100.9, 101.0, atr=0.5, side="short")]
    assert one(E.walk_long(flip, D3, P, EQ))["reason"] == "thesis_change"


# ── bookkeeping ────────────────────────────────────────────────────────────

def test_max_dd_pct():
    assert E.max_dd_pct([100, 120, 90, 130], 100.0) == pytest.approx(25.0)
    assert E.max_dd_pct([100, 101], 100.0) == 0.0
    assert E.max_dd_pct([90.0], 100.0) == pytest.approx(10.0)       # the start equity is a peak


def test_summary_counts_reopen_fills_apart():
    run = {"trades": [{"net_usd": 5.0, "r": 1.0, "reason": "target", "tiers": 1, "reopen": True},
                      {"net_usd": -2.0, "r": -1.0, "reason": "stop", "tiers": 2, "reopen": False}],
           "curve": [5.0, 3.0], "refused": {}}
    s = E.summarize_short(run, [100.0, 100.0])
    assert (s["n"], s["wins"], s["win_rate"], s["net_usd"], s["avg_r"]) == (2, 1, 50.0, 3.0, 0.0)
    assert (s["reopen_n"], s["reopen_usd"], s["ex_reopen_usd"]) == (1, 5.0, -2.0)
    assert s["exits"] == {"stop": 1, "target": 1} and s["tiers_filled"] == 3
    assert s["max_dd_pct"] == pytest.approx(round(2 / 105 * 100, 1))


def test_long_masks_and_collisions():
    times = [0, 10, 20, 30, 40]
    open_, enter = E.long_masks(times, [(10, 20), (40, None)])
    assert open_ == [False, True, True, False, True] and enter == [False, True, False, False, True]
    shorts = [{"entry_ms": 0, "exit_ms": 5}, {"entry_ms": 5, "exit_ms": 12}, {"entry_ms": 15, "exit_ms": 18},
              {"entry_ms": 30, "exit_ms": None}]
    c = E.collisions(shorts, [(10, 20), (40, None)], end_ms=50)
    assert c == {"n": 4, "overlap": 3, "long_entered_inside": 2, "opened_while_long": 1, "rate_pct": 75.0}
    assert E.collisions([], [(10, 20)], 50)["rate_pct"] is None


def test_drop_best_day():
    trades = [{"exit": "2026-11-03 10:00Z", "entry": "2026-11-03 09:00Z", "net_usd": 10.0},
              {"exit": "2026-11-04 10:00Z", "entry": "2026-11-04 09:00Z", "net_usd": -3.0},
              {"exit": None, "entry": "2026-11-05 09:00Z", "net_usd": 1.0}]
    d = E.drop_best_day(trades)
    assert d["best_day"] == "2026-11-03" and d["rest_usd"] == -2.0 and d["pass"] is False
    assert E.drop_best_day([])["pass"] is False


def _row(net=5.0, n=12, dd=19.0, live_dd=20.0, halves=(1.0, 2.0), ex=None, dbd=True):
    comb = {"net_usd": net, "n": n, "ex_reopen_usd": net if ex is None else ex}
    return {"datasets": {E.PRIMARY: {"combined": comb, "live": {"max_dd_pct": live_dd}, "combined_dd_pct": dd},
                         "wti_journal": {"combined": dict(comb), "live": {"max_dd_pct": live_dd},
                                         "combined_dd_pct": dd}},
            "halves": list(halves), "halves_ex_reopen": list(halves), "drop_best_day": {"pass": dbd}}


def test_judge_pass_needs_every_leg_and_ten_trades():
    assert E.judge(_row(), [1.0, 2.0])["verdict"] == "PASS"
    assert E.judge(_row(net=-5.0), [1.0])["verdict"] == "FAIL"
    assert E.judge(_row(dd=20.1), [1.0])["verdict"] == "FAIL"               # drawdown no worse
    assert E.judge(_row(halves=(3.0, -0.1)), [1.0])["verdict"] == "FAIL"    # walk-forward
    assert E.judge(_row(), [1.0, -1.0])["verdict"] == "FAIL"                # plateau
    assert E.judge(_row(), [])["verdict"] == "FAIL"                         # no neighbour, no plateau
    assert E.judge(_row(dbd=False), [1.0])["verdict"] == "FAIL"


def test_judge_never_rounds_small_n_up():
    assert E.MIN_N == 10
    assert E.judge(_row(n=9), [1.0])["verdict"] == "LEANING"
    assert E.judge(_row(n=9, net=-5.0), [1.0])["verdict"] == "NOISE"
    assert E.judge(_row(n=0, net=0.0), [1.0])["verdict"] == "NOISE"
    assert E.judge(_row(n=10), [1.0])["verdict"] == "PASS"


def test_judge_reopen_leg_is_reported_apart_from_the_older_legs():
    j = E.judge(_row(ex=-0.5), [1.0])
    assert j["verdict"] == "FAIL" and j["legs"]["holds_without_reopen_ticks"] is False
    assert j["repo_bar_without_reopen_leg"] is True
    assert E.judge(_row(), [1.0])["repo_bar_without_reopen_leg"] is True
    assert E.judge(_row(n=9), [1.0])["repo_bar_without_reopen_leg"] is False


def test_best_of_prefers_a_pass_then_a_variant_that_traded():
    def r(kind, verdict, net, n):
        return {"kind": kind, "judge": {"verdict": verdict},
                "datasets": {E.PRIMARY: {"combined": {"net_usd": net, "n": n}}}}
    rows = {"a": r("long", "NOISE", 0.0, 0), "b": r("long", "NOISE", -3.0, 2), "c": r("long", "FAIL", -9.0, 12),
            "d": r("entry", "FAIL", 50.0, 20), "e": r("entry", "PASS", 5.0, 11), "f": r("entry", "LEANING", 9.0, 4)}
    assert E.best_of(rows, "long") == "b"          # the untraded 0.00 is not a result
    assert E.best_of(rows, "entry") == "e"
    assert E.best_of(rows, "ladder") is None


def _thesis(t, side="long", entry=100.0, targets=(104.0,)):
    return D.ThesisDirection(created_at_ms=t, side=side, entry_px=entry, stop_px=None, invalidation_px=99.0,
                             targets=list(targets), thesis_id=str(t))


def _c(t, o, h, l, c, v=1.0):
    return Candle(t=t, o=o, h=h, l=l, c=c, v=v)


def test_missed_theses_are_right_and_never_touched():
    theses = [_thesis(0), _thesis(4 * Q), _thesis(8 * Q), _thesis(12 * Q, side="flat", entry=None)]
    bars = ([_c(i * Q, 101.0, 102.0, 100.5, 101.8) for i in range(4)]              # ran away: missed
            + [_c((4 + i) * Q, 101.0, 101.5, 99.9, 101.4) for i in range(4)]       # dipped to the entry: touched
            + [_c((8 + i) * Q, 101.0, 101.2, 100.2, 100.4) for i in range(4)]      # never touched, and wrong
            + [_c((12 + i) * Q, 101.0, 101.2, 100.2, 100.4) for i in range(4)])
    rows = E.missed_theses(theses, bars, 16 * Q)
    assert [(m["touched"], m["right"], m["missed"]) for m in rows] == [
        (False, True, True), (True, True, False), (False, False, False)]
    assert rows[0]["gap_pct"] == pytest.approx(1.0) and rows[0]["hit_target"] is False
    got = E.caught(rows, [{"thesis_id": "0", "net_usd": 4.0}, {"thesis_id": "0", "net_usd": -1.0},
                          {"thesis_id": str(4 * Q), "net_usd": 9.0}])
    assert got == {"missed": 1, "caught": 1, "caught_wins": 1, "caught_net_usd": 3.0}


def test_thesis_lookup_and_the_wti_scale():
    theses = [_thesis(100), _thesis(200, side="flat", entry=None), _thesis(300, entry=90.0, targets=(95.0,))]
    at = E.thesis_lookup(theses)
    assert at(50) is None
    assert at(100) == ("long", 100.0, "100", 104.0, 99.0)
    assert at(250)[:2] == ("flat", None)
    scaled = E.thesis_lookup(theses, {100: 0.9})
    assert scaled(150) == ("long", pytest.approx(90.0), "100", pytest.approx(93.6), None)
    assert scaled(350)[1] is None                                   # no ratio for that thesis: no entry there


def test_price_ratio_uses_the_last_closed_hour():
    brent = [_c(0, 100, 100, 100, 100.0), _c(H, 100, 100, 100, 110.0)]
    wti = [_c(0, 90, 90, 90, 90.0), _c(H, 90, 90, 90, 88.0)]
    assert E.price_ratio([_thesis(H), _thesis(2 * H)], brent, wti) == {H: pytest.approx(0.9), 2 * H: pytest.approx(0.8)}
    assert E.price_ratio([_thesis(10)], brent, wti) == {}           # nothing closed yet


def test_build_ticks_reads_closed_hours_only():
    hours = [_c(T0 - (30 - i) * H, 100.0, 100.0 + i * 0.1, 99.0, 100.0, v=1.0) for i in range(31)]   # last opens T0
    c15 = [_c(T0, 100.0, 100.5, 99.5, 100.2), _c(T0 + Q, 100.2, 100.6, 100.0, 100.4), _c(T0 + H, 100.4, 100.9, 100.1, 100.3)]
    ticks = E.build_ticks(c15, hours, E.thesis_lookup([_thesis(T0 + Q)]), [{"time": T0, "fundingRate": "0.0001"}])
    first, second, third = ticks
    assert first.ok and first.hi24 == pytest.approx(102.9)          # the bar that OPENS at T0 is not closed yet
    assert third.hi24 == pytest.approx(103.0)                        # it is by T0 + 1h
    assert first.close_1h == 100.0 and second.close_1h is None and first.fund == pytest.approx(0.0001)
    assert first.side is None and second.side == "long" and second.ceiling == 100.0 and second.target == 104.0
    assert first.vwap24 == pytest.approx(sum((100.0 + i * 0.1 + 99.0 + 100.0) / 3 for i in range(6, 30)) / 24)


def test_baseline_is_the_live_rule_with_six_percent_risk_and_the_leverage_fit():
    assert P.risk_cap_frac == pytest.approx(0.06) and P.entry_fit_leverage == NT.leverage_fit_for(NT.BRENT)
    assert P.no_break_entry and P.weekend_off == "flatten" and P.min_r == 2.0


# ── E: the long ladder sized in equity ─────────────────────────────────────

E1 = Variant("equity", step=E.E_FIRST, tiers=E.E_TIERS, scheme="eq20_10", confirm="touch", target="thesis", stop="lo24")


def test_equity_grid_holds_the_operators_tiers():
    """"20% equity at .5%+, 10% equity at 1%+"."""
    grid = E.equity_variants()
    assert len(grid) == 3 * 3 * 2 * 3 * 3 == 162 and len({v.name for v in grid}) == 162
    assert E1.name == E.E_PRIMARY and E1.name in {v.name for v in grid}
    assert E1.risk == (0.20, 0.10)                                  # shares of EQUITY, not of risk
    assert E.long_levels(100.0, E1.step, E1.tiers) == pytest.approx([100.5, 101.0])
    assert {n.scheme for n in E.neighbours(E1)} >= {"eq10_5", "eq30_15"}
    assert [n.min_r for n in E.neighbours(E1) if n.min_r != E1.min_r] == [1.0]


def test_equity_tier_is_margin_times_leverage_not_a_risk_size():
    n20 = E.equity_tier_notional(EQ, P, 20.0, 0.20)
    assert n20 == pytest.approx(0.20 * EQ / (1 + 20.0 * P.taker_fee) * 20.0)
    assert 3.9 * EQ < n20 < 4.0 * EQ                                # 20% of equity at 20x is about 4x equity
    assert E.equity_tier_notional(EQ, P, 15.0, 0.20) == pytest.approx(0.20 * EQ / (1 + 15.0 * P.taker_fee) * 15.0)
    assert E.equity_tier_notional(EQ, P, 20.0, 0.10) == pytest.approx(n20 / 2)
    full = _full_size_notional(EQ, P, 20.0)
    assert E.equity_tier_notional(EQ, P, 20.0, 0.20, open_notional=full - 100.0) == pytest.approx(100.0)
    assert E.equity_tier_notional(EQ, P, 20.0, 0.20, open_notional=full + 1.0) == 0.0


def test_stop_risk_pct_says_what_the_equity_tier_really_risks():
    n20 = E.equity_tier_notional(EQ, P, 20.0, 0.20)
    pct = E.stop_risk_pct(n20, 100.5, 99.25, EQ, P)
    assert pct == pytest.approx(n20 * (1.25 / 100.5 + 2 * P.taker_fee + P.slippage) / EQ * 100)
    assert 5.0 < pct < 6.0                                          # about the desk's whole normal 6%, on tier 1 alone
    assert E.stop_risk_pct(n20, 100.5, 99.25, 0.0, P) == 0.0
    # the risk-sized tier D uses carries its share by construction
    n = E.tier_notional(EQ, P, 20.0, 100.5, 99.25, 0.2)
    assert E.stop_risk_pct(n, 100.5, 99.25, EQ, P) == pytest.approx(1.2)


def test_equity_ladder_buys_20_then_10_percent_of_equity_and_records_the_risk():
    bars = [tk(0, 100.2, 100.6, 100.1, 100.5, lo24=100.0), tk(1, 100.6, 101.1, 100.5, 101.0, lo24=100.0),
            tk(2, 100.9, 101.0, 99.2, 99.3, lo24=100.0)]
    t = one(E.walk_long(bars, E1, P, EQ))
    assert t["reason"] == "stop" and t["tiers"] == 2 and t["stop_px"] == pytest.approx(99.25)
    assert t["leverage"] == 20.0
    assert t["size"] * t["entry_px"] == pytest.approx(0.30 * EQ / (1 + 20.0 * P.taker_fee) * 20.0, rel=2e-3)
    r1, r2 = t["tier_risk_pct"]
    assert r1 == pytest.approx(E.stop_risk_pct(E.equity_tier_notional(EQ, P, 20.0, 0.20), 100.5, 99.25, EQ, P), abs=0.01)
    assert 5.0 < r1 < 6.0 and 3.0 < r2 < 4.0
    assert t["net_usd"] == pytest.approx(-(r1 + r2) / 100 * EQ, rel=0.08)      # the loss is what the tiers said
    s = E.summarize_short({"trades": [t], "curve": [t["net_usd"]], "refused": {}}, [EQ])
    assert s["tier_risk_pct_mean"] == [r1, r2] and s["trade_risk_pct_max"] == pytest.approx(r1 + r2, abs=0.01)


def test_equity_ladder_only_first_tier_when_price_stops_short_of_one_percent():
    bars = [tk(0, 100.2, 100.6, 100.1, 100.5, lo24=100.0), tk(1, 100.6, 100.9, 100.5, 100.8, lo24=100.0)]
    t = one(E.walk_long(bars, E1, P, EQ))
    assert t["tiers"] == 1 and len(t["tier_risk_pct"]) == 1
    assert E.walk_long([tk(0, 100.2, 100.4, 100.1, 100.3, lo24=100.0)], E1, P, EQ)["trades"] == []


# ── realistic weekend-reopen fills ─────────────────────────────────────────

SUN = int(datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc).timestamp() * 1000)     # a reopen tick
M5 = E.FIVE_MS


def test_reopen_fill_is_the_mid_five_minutes_in_not_the_open_print():
    c15 = [_c(SUN - Q, 98.0, 98.1, 97.9, 98.1), _c(SUN, 98.1, 98.2, 96.0, 96.8), _c(SUN + Q, 96.8, 97.0, 96.5, 96.6)]
    c5 = [_c(SUN, 98.1, 98.2, 96.0, 96.9), _c(SUN + M5, 96.9, 97.2, 96.8, 96.88), _c(SUN + 2 * M5, 96.88, 96.95, 96.7, 96.8)]
    f = E.reopen_fills(c15, c5, floor_slip=0.0003)
    assert set(f) == {SUN}
    got = f[SUN]
    assert got["source"] == "5m" and got["px"] == 96.9 and got["open_px"] == 98.1
    assert got["high"] == 97.2 and got["low"] == 96.7              # the first five minutes are not the desk's to trade
    assert got["slip"] == pytest.approx((97.2 - 96.8) / 96.9 / 2)
    assert E.REOPEN_DELAY_MS == 5 * 60_000


def test_reopen_fill_slippage_floor_and_the_fallback_without_5m_candles():
    week = 7 * DAY
    c15 = [_c(SUN - week, 110.0, 110.1, 109.5, 109.7), _c(SUN, 98.1, 98.2, 96.0, 96.8)]
    quiet = [_c(SUN + M5, 96.9, 96.91, 96.9, 96.9), _c(SUN + 2 * M5, 96.9, 96.9, 96.85, 96.85)]
    f = E.reopen_fills(c15, quiet, floor_slip=0.0003)
    assert f[SUN]["slip"] == pytest.approx(0.0003)                  # never under the sim's own slippage
    old = f[SUN - week]
    assert old["source"] == "next_tick" and old["px"] == 109.7 and old["high"] == old["low"] == 109.7
    assert old["slip"] == pytest.approx(f[SUN]["slip"])            # the median of what was measured
    none = E.reopen_fills(c15, [], floor_slip=0.0004)
    assert {v["source"] for v in none.values()} == {"next_tick"} and none[SUN]["slip"] == pytest.approx(0.0004)


def test_apply_reopen_reprices_only_the_reopen_tick():
    ticks = [tk(0, 98.1, 98.2, 96.0, 96.8, t=SUN, reopen=True, ceiling=97.2),
             tk(0, 96.8, 97.0, 96.5, 96.6, t=SUN + Q, ceiling=97.2)]
    fills = {SUN: {"px": 96.9, "high": 97.1, "low": 96.7, "slip": 0.002, "source": "5m", "open_px": 98.1}}
    out = E.apply_reopen(ticks, fills)
    assert (out[0].o, out[0].h, out[0].l, out[0].c, out[0].slip) == (96.9, 97.1, 96.7, 96.8, 0.002)
    assert out[1] is ticks[1] and ticks[0].o == 98.1               # the input is not touched
    assert E.apply_reopen(ticks, {})[0] is ticks[0]


def test_the_reopen_short_only_exists_on_the_open_print():
    """98.1 printed at the reopen, 96.9 five minutes later, thesis entry 97.2:
    the open-print backtest shorts 98.1 and covers at the entry; the desk,
    five minutes in, is already under its entry and has nothing to short."""
    ticks = [tk(0, 98.1, 98.2, 96.0, 96.8, t=SUN, reopen=True, ceiling=97.2),
             tk(0, 96.8, 97.0, 96.5, 96.6, t=SUN + Q, ceiling=97.2)]
    paper = one(E.walk(ticks, A1, P, EQ))
    assert paper["reopen"] and paper["net_usd"] > 0
    fills = {SUN: {"px": 96.9, "high": 97.1, "low": 96.7, "slip": 0.002, "source": "5m", "open_px": 98.1}}
    assert E.walk(E.apply_reopen(ticks, fills), A1, P, EQ)["trades"] == []


def test_a_market_fill_on_a_reopen_tick_pays_the_reopen_slippage():
    ticks = [tk(0, 98.1, 98.2, 97.9, 98.0, t=SUN, reopen=True, ceiling=97.2)]
    fills = {SUN: {"px": 98.0, "high": 98.05, "low": 97.9, "slip": 0.002, "source": "5m", "open_px": 98.1}}
    t = one(E.walk(E.apply_reopen(ticks, fills), A1, P, EQ))
    assert t["entry_px"] == pytest.approx(98.0 * (1 - 0.002), abs=1e-4)
    v = Variant("long", step=0.005, tiers=1, scheme="flat20", stop="atr2.0")
    long_t = one(E.walk_long(E.apply_reopen(ticks, fills), v, P, EQ))
    assert long_t["entry_px"] == pytest.approx(98.0 * (1 + 0.002), abs=1e-4)


def test_judge_on_realistic_fills_uses_the_five_older_legs():
    row = _row(ex=-0.5)
    assert E.judge(row, [1.0])["verdict"] == "FAIL"
    j = E.judge(row, [1.0], reopen_leg=False)
    assert j["verdict"] == "PASS" and j["reopen_leg"] is False
    assert E.judge(_row(net=-5.0), [1.0], reopen_leg=False)["verdict"] == "FAIL"
    assert E.judge(_row(n=9, ex=-0.5), [1.0], reopen_leg=False)["verdict"] == "LEANING"


def test_reopen_profit_says_how_much_survives():
    def r(net, reopen, n):
        return {"kind": "entry", "datasets": {E.PRIMARY: {"combined": {"net_usd": net, "reopen_usd": reopen, "reopen_n": n}}}}
    got = E.reopen_profit({"a": r(10.0, 8.0, 2), "b": r(5.0, 2.0, 1)}, {"a": r(3.0, 1.0, 2), "b": r(4.0, 1.5, 1)}, [E.PRIMARY])
    assert got == {"entry": {E.PRIMARY: {"variants": 2, "net_open_print_usd": 15.0, "net_realistic_usd": 7.0,
                                         "reopen_open_print_usd": 10.0, "reopen_realistic_usd": 2.5,
                                         "reopen_trades_open_print": 3, "reopen_trades_realistic": 3,
                                         "survives_pct": 25.0}}}
    lost = E.reopen_profit({"a": r(-1.0, -2.0, 1)}, {"a": r(-1.0, -3.0, 1)}, [E.PRIMARY])
    assert lost["entry"][E.PRIMARY]["survives_pct"] is None        # there was no reopen profit to keep


# ── CLI ────────────────────────────────────────────────────────────────────

def test_help_runs(capsys):
    with pytest.raises(SystemExit) as e:
        E.main(["--help"])
    assert e.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "--oil-theses-dir" in out and "--only" in out and "Research only" in out


def test_no_journal_exits_2_without_touching_the_network(tmp_path):
    rc = E.main(["--oil-theses-dir", str(tmp_path / "none"), "--cache-dir", str(tmp_path / "c"),
                 "--out-dir", str(tmp_path / "o")])
    assert rc == 2
