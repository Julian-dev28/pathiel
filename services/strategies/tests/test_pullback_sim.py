"""Gate tests for the 2026-11-02 pullback entry and the entry / correlation
guard (desk_tick_sim `Params.pullback_entry` and friends, `simulate(...,
entry_guard=...)`, portfolio `CorrGuard`; see
docs/strategy/PULLBACK_AND_CORRELATION_TEST_2026-11-02.md).

Synthetic bars, no network. Covers: everything off changes nothing; each
mode's levels land where the rule says (a1 structure stop, a2 retest, a3
resting bounce with its lapse rules, a4 fresh thesis levels); the thesis-
stop cap and the target choice; the leverage gate and the fit-leverage
option; the live rule keeps priority; the guard blocks, scales and leaves
top-ups scaled; the portfolio guard reads the other desk's held side.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    HOUR_MS, Direction, _levels, _Resting, _setup, live_params, pb_cancel, pullback_setup, retest_level, simulate)
from services.strategies.backtest.portfolio import CorrGuard, Desk, guard_mult, simulate_portfolio

Q = 900_000
T0 = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _base(**over):
    return dataclasses.replace(live_params(), starting_equity=225.0, risk_cap_frac=0.15, **over)


def bar(i, o, h, lo, c, start=T0, step=HOUR_MS):
    return Candle(t=start + i * step, o=o, h=h, l=lo, c=c, v=1.0)


def breakdown_hours():
    """30 hours around 110 (24h range 109.7-110.3), a 3-hour drop to 108.5,
    then 6 quiet hours at 108.5: a short thesis written at 110.2 is a chase
    down here, and the 24h-high stop is far out of reach of 2R."""
    out = []
    for i in range(30):
        c = 110.0 + (0.2 if i % 2 else -0.2)
        out.append(bar(i, 110.0, max(110.0, c) + 0.1, min(110.0, c) - 0.1, c))
    for j, c in enumerate((109.5, 109.0, 108.5)):
        o = out[-1].c
        out.append(bar(30 + j, o, o + 0.05, c - 0.05, c))
    for j in range(6):
        out.append(bar(33 + j, 108.5, 108.55, 108.45, 108.5))
    return out


def quarters_after(c1h, from_i, px=108.5):
    """Flat 15m ticks at `px` from 1h bar `from_i` on."""
    t0 = c1h[from_i].t
    n = (len(c1h) - from_i) * 4
    return [Candle(t=t0 + k * Q, o=px, h=px + 0.02, l=px - 0.02, c=px, v=1.0) for k in range(n)]


SHORT = Direction(side="short", entry_ceiling=110.2, stop_px=111.5, target_px=104.0, regime_id=str(T0))


def lv_at(c1h, n):
    return _levels(c1h[:n], 24, 24)


# ── off changes nothing ────────────────────────────────────────────────────

def _key(run):
    return ([dataclasses.asdict(t) for t in run.trades], run.equity_curve, run.final_equity, dict(run.refused),
            run.open_position_at_end, run.filled, run.rested)


def test_off_and_neutral_guard_change_nothing():
    c1h = breakdown_hours()
    c15 = quarters_after(c1h, 34)
    d_at = lambda t: SHORT   # noqa: E731
    ref = _key(simulate(c15, c1h, d_at, _base()))
    # pullback fields set but the mode off
    assert _key(simulate(c15, c1h, d_at, _base(pb_struct_h=4, pb_target="thesis", pb_fit_leverage=True))) == ref
    # a guard that always says 1.0
    assert _key(simulate(c15, c1h, d_at, _base(), entry_guard=lambda t, s: 1.0)) == ref
    long_ = lambda t: Direction(side="long")   # noqa: E731
    ref_l = _key(simulate(c15, c1h, long_, _base()))
    assert _key(simulate(c15, c1h, long_, _base(), entry_guard=lambda t, s: 1.0)) == ref_l


def test_validation():
    c1h = breakdown_hours()
    d_at = lambda t: SHORT   # noqa: E731
    for bad in ({"pullback_entry": "a9"}, {"pullback_entry": "a1", "pb_target": "mid"},
                {"pullback_entry": "a1", "pb_struct_h": 0}, {"pullback_entry": "a3", "pb_cancel_h": 0.0}):
        with pytest.raises(ValueError):
            simulate([], c1h, d_at, _base(**bad))


# ── a1: structure stop ─────────────────────────────────────────────────────

def test_live_refuses_the_chase_and_a1_sells_on_the_nearer_structure():
    c1h = breakdown_hours()
    n = 37                                   # 37 bars closed: the drop and 4 quiet hours behind
    lv = lv_at(c1h, n)
    mid = 108.5
    assert _setup(SHORT, mid, lv, _base()) == {"refused": "chase"}
    p = _base(pullback_entry="a1", pb_struct_h=4, pb_target="thesis")
    s = pullback_setup(SHORT, c1h[n].t, mid, lv, c1h[:n], p)
    assert s["pb"] == "a1" and s["is_market"] and s["side"] == "short"
    assert s["stop_px"] == pytest.approx(108.55 + 0.75)                    # the 4h high + Brent's 0.75
    assert s["target_px"] == pytest.approx(104.0 * 1.005)                  # the thesis target, live offset
    assert s["leverage"] == 20.0
    # the far 24h edge sits above a short that already broke it: no room
    far = pullback_setup(SHORT, c1h[n].t, mid, lv, c1h[:n], dataclasses.replace(p, pb_target="far"))
    assert far == {"refused": "pb_no_room"}
    # an 8h window reaches back into the drop: the stop no longer fits 20x
    wide = pullback_setup(SHORT, c1h[n].t, mid, lv, c1h[:n], dataclasses.replace(p, pb_struct_h=8))
    assert wide == {"refused": "pb_leverage"}


def test_a1_thesis_stop_cap_and_fit_leverage():
    c1h = breakdown_hours()
    n = 37
    lv = lv_at(c1h, n)
    p = _base(pullback_entry="a1", pb_struct_h=4, pb_target="thesis")
    tight = dataclasses.replace(SHORT, stop_px=109.0)
    assert pullback_setup(tight, c1h[n].t, 108.5, lv, c1h[:n], p) == {"refused": "pb_wide"}
    assert pullback_setup(tight, c1h[n].t, 108.5, lv, c1h[:n],
                          dataclasses.replace(p, pb_cap_thesis_stop=False))["pb"] == "a1"
    # the 8h stop (111.05, 2.35% away) is past half the way to liquidation at 20x; fit finds 13x
    far_target = dataclasses.replace(SHORT, target_px=100.0)
    p8 = dataclasses.replace(p, pb_struct_h=8)
    assert pullback_setup(far_target, c1h[n].t, 108.5, lv, c1h[:n], p8) == {"refused": "pb_leverage"}
    fit = pullback_setup(far_target, c1h[n].t, 108.5, lv, c1h[:n], dataclasses.replace(p8, pb_fit_leverage=True))
    assert fit["pb"] == "a1" and fit["leverage"] == 13.0
    assert fit["stop_px"] == pytest.approx(110.3 + 0.75)


def test_a1_in_the_sim_takes_the_short_live_never_takes():
    c1h = breakdown_hours()
    c15 = quarters_after(c1h, 37)
    d_at = lambda t: SHORT   # noqa: E731
    live = simulate(c15, c1h, d_at, _base())
    assert not live.trades and live.open_position_at_end is None and live.refused["chase"] > 0
    run = simulate(c15, c1h, d_at, _base(pullback_entry="a1", pb_struct_h=4, pb_target="thesis"))
    assert run.open_position_at_end["side"] == "short"
    assert run.open_position_levels["entry_kind"] == "a1"
    assert run.open_position_levels["orig_stop_px"] == pytest.approx(109.3)
    assert run.position_side == "short"


def range_hours():
    """30 hours swinging 108.2 <-> 111.8 (24h range 108.1-111.9): a long at
    108.3 pays over 2R with its stop 0.75 under the low, inside 20x."""
    out, prev = [], 110.0
    for i in range(30):
        c = 111.8 if i % 2 else 108.2
        out.append(bar(i, prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c))
        prev = c
    return out


def flat_ticks(c1h, px=108.3, n=12):
    t0 = c1h[-1].t + HOUR_MS
    return [Candle(t=t0 + k * Q, o=px, h=px + 0.02, l=px - 0.02, c=px, v=1.0) for k in range(n)]


LONG = lambda t: Direction(side="long")   # noqa: E731


def test_live_keeps_priority_over_the_pullback():
    """The mid near the 24h low on a long call: live buys; a1 changes nothing."""
    c1h = range_hours()
    c15 = flat_ticks(c1h)
    ref = _key(simulate(c15, c1h, LONG, _base()))
    assert ref[4] is not None and ref[4]["side"] == "long"      # live is in, open at the end
    assert _key(simulate(c15, c1h, LONG, _base(pullback_entry="a1", pb_struct_h=4))) == ref


# ── a2: retest of a broken level ───────────────────────────────────────────

def retest_hours(last_h, last_c):
    out = []
    for i in range(26):
        c = 110.0 + (0.1 if i % 2 else -0.1)
        out.append(bar(i, 110.0, 110.2, 109.8, c))
    out.append(bar(26, 110.0, 110.0, 109.4, 109.5))       # closes under the 24h low 109.8: the break
    out.append(bar(27, 109.5, 109.6, 109.42, 109.45))     # holds over bar 26's low 109.4: no new break
    out.append(bar(28, 109.3, last_h, 109.25, last_c))   # back up to the level?
    return out


def test_retest_level():
    hit = retest_level(retest_hours(109.85, 109.6), "short", 12)
    assert hit is not None and hit[0] == pytest.approx(109.8) and hit[1].t == T0 + 28 * HOUR_MS
    assert retest_level(retest_hours(109.7, 109.6), "short", 12) is None      # never reached the level
    assert retest_level(retest_hours(109.9, 109.85), "short", 12) is None     # closed back above it
    assert retest_level(retest_hours(109.85, 109.6), "short", 1) is None      # the break is 2 bars back
    assert retest_level(retest_hours(109.85, 109.6), "long", 12) is None
    # the mirror: a break over the 24h high, back down to it, close above
    up = [Candle(t=c.t, o=220 - c.o, h=220 - c.l, l=220 - c.h, c=220 - c.c, v=1.0) for c in retest_hours(109.85, 109.6)]
    hit = retest_level(up, "long", 12)
    assert hit is not None and hit[0] == pytest.approx(110.2)


def test_a2_setup_stops_above_the_retest_bar():
    c1h = retest_hours(109.85, 109.6)
    n = len(c1h)
    lv = lv_at(c1h, n)
    d = Direction(side="short", entry_ceiling=110.2, stop_px=111.5, target_px=105.0, regime_id=str(T0))
    s = pullback_setup(d, c1h[-1].t + HOUR_MS, 109.55, lv, c1h, _base(pullback_entry="a2", pb_target="thesis"))
    assert s["pb"] == "a2" and s["is_market"]
    assert s["stop_px"] == pytest.approx(109.85 + 0.75)
    assert pullback_setup(d, c1h[-1].t + HOUR_MS, 109.55, lv, c1h[:-1], _base(pullback_entry="a2")) is None


# ── a3: resting bounce ─────────────────────────────────────────────────────

def test_a3_rests_a_bounce_and_lapses():
    c1h = breakdown_hours()
    n = 37
    lv = lv_at(c1h, n)
    t = c1h[n].t
    p = _base(pullback_entry="a3", pb_struct_h=4, pb_target="thesis", pb_bounce_atr=1.0, pb_cancel_h=4.0)
    s = pullback_setup(SHORT, t, 108.5, lv, c1h[:n], p)
    assert not s["is_market"] and s["entry_px"] == pytest.approx(108.45 + lv["atr14"])
    assert s["expires_at"] == t + 4 * HOUR_MS
    assert s["stop_px"] == pytest.approx(max(108.55, s["entry_px"]) + 0.75)
    # a bounce already behind the mid is a1's job
    assert pullback_setup(SHORT, t, 110.0, lv, c1h[:n], p) == {"refused": "pb_bounced"}
    rest = _Resting(side="short", limit_px=s["entry_px"], stop_px=s["stop_px"], target_px=s["target_px"], placed_at=t,
                    pb="a3", expires_at=s["expires_at"], call_id=SHORT.regime_id)
    assert not pb_cancel(rest, SHORT, t + HOUR_MS)
    assert pb_cancel(rest, SHORT, t + 4 * HOUR_MS)                                   # expired
    assert pb_cancel(rest, Direction(side="long", regime_id=SHORT.regime_id), t + HOUR_MS)   # side change
    assert pb_cancel(rest, None, t + HOUR_MS)                                        # no call
    assert pb_cancel(rest, dataclasses.replace(SHORT, regime_id="9"), t + HOUR_MS)   # rewrite


def test_a3_in_the_sim_fills_on_the_bounce_with_maker_fee():
    c1h = breakdown_hours()
    t_rest = c1h[37].t
    lv = lv_at(c1h, 37)
    limit = 108.45 + 0.5 * lv["atr14"]
    ticks = [Candle(t=t_rest, o=108.5, h=108.52, l=108.48, c=108.5, v=1.0),
             Candle(t=t_rest + Q, o=108.5, h=limit + 0.05, l=108.48, c=108.6, v=1.0),
             Candle(t=t_rest + 2 * Q, o=108.6, h=108.62, l=108.55, c=108.6, v=1.0)]
    run = simulate(ticks, c1h, lambda t: SHORT,
                   _base(pullback_entry="a3", pb_struct_h=4, pb_target="thesis", pb_bounce_atr=0.5))
    assert run.rested == 1 and run.filled == 1
    op, lvs = run.open_position_at_end, run.open_position_levels
    assert op["side"] == "short" and op["entry_px"] == pytest.approx(limit)
    assert lvs["entry_kind"] == "a3"
    assert lvs["entry_fees_usd"] == pytest.approx(0.00015 * limit * op["size"])


def test_a3_resting_entry_expires_in_the_sim():
    c1h = breakdown_hours()
    t_rest = c1h[37].t
    ticks = [Candle(t=t_rest + k * Q, o=108.5, h=108.52, l=108.48, c=108.5, v=1.0) for k in range(12)]
    run = simulate(ticks, c1h, lambda t: SHORT, _base(pullback_entry="a3", pb_struct_h=4, pb_target="thesis",
                                                      pb_bounce_atr=1.0, pb_cancel_h=1.0))
    assert run.filled == 0 and run.cancelled_resting >= 2 and run.rested >= 3   # lapses, then rests again


# ── a4: the fresh thesis's own levels ──────────────────────────────────────

def test_a4_fresh_stale_and_the_leverage_gate():
    c1h = breakdown_hours()
    n = 37
    lv = lv_at(c1h, n)
    t = c1h[n].t
    born = t - HOUR_MS
    d = Direction(side="short", entry_ceiling=108.4, stop_px=109.2, target_px=105.0, regime_id=str(born))
    p = _base(pullback_entry="a4", pb_fresh_h=2.0)
    s = pullback_setup(d, t, 108.5, lv, c1h[:n], p)
    assert s["pb"] == "a4" and s["is_market"] and s["stop_px"] == 109.2 and s["expires_at"] == born + 2 * HOUR_MS
    assert pullback_setup(d, born + 2 * HOUR_MS, 108.5, lv, c1h[:n], p) == {"refused": "pb_stale"}
    # a mid under a short thesis's entry rests a limit at the entry
    s2 = pullback_setup(d, t, 108.3, lv, c1h[:n], p)
    assert not s2["is_market"] and s2["entry_px"] == 108.4
    # a thesis stop 1.4% out (written for 5x) fails 20x; fit-leverage trades it at 18x
    wide = dataclasses.replace(d, stop_px=110.0, target_px=104.0)
    assert pullback_setup(wide, t, 108.5, lv, c1h[:n], p) == {"refused": "pb_ai_leverage"}
    fit = pullback_setup(wide, t, 108.5, lv, c1h[:n], dataclasses.replace(p, pb_fit_leverage=True))
    assert fit["pb"] == "a4" and fit["leverage"] == 18.0
    # no thesis levels (a control series): nothing to use
    assert pullback_setup(Direction(side="short", regime_id=str(born)), t, 108.5, lv, c1h[:n], p) \
        == {"refused": "pb_no_levels"}


# ── a5: breakdown/breakout continuation ─────────────────────────────────────
#
# 24 flat "prior" hours (24h range 100.0-104.0, so the measured move is 4.0),
# then ONE bar that CLOSES under the prior low (99.6 < 100.0) -- the
# breakdown itself, not a pullback back up to it. `pb_breakout_stop_h=1`
# isolates the stop to that one bar's own high (100.05) so the fixture's
# numbers are easy to hand-check; the default (4) is covered by the sim-level
# test below on a calmer fixture.

PRIOR_LOW, PRIOR_HIGH = 100.0, 104.0
BREAKOUT_SHORT = Direction(side="short", regime_id=str(T0))
BREAKOUT_LONG = Direction(side="long", regime_id=str(T0))


def breakout_hours_short(break_close: float = 99.6) -> list:
    out = [bar(i, 102.0, PRIOR_HIGH, PRIOR_LOW, 102.0) for i in range(24)]
    out.append(bar(24, 100.0, 100.05, 95.0, break_close))
    return out


def breakout_hours_long(break_close: float = 104.4) -> list:
    out = [bar(i, 102.0, PRIOR_HIGH, PRIOR_LOW, 102.0) for i in range(24)]
    out.append(bar(24, 104.0, 109.0, 103.95, break_close))
    return out


def test_a5_validation():
    c1h = breakout_hours_short()
    for bad in ({"pullback_entry": "a5", "pb_breakout_lookback_h": 0}, {"pullback_entry": "a5", "pb_breakout_stop_h": 0}):
        with pytest.raises(ValueError):
            simulate([], c1h, lambda t: BREAKOUT_SHORT, _base(**bad))


def test_a5_short_breaks_and_targets_the_measured_move():
    c1h = breakout_hours_short()
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    mid = 99.7
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured")
    s = pullback_setup(BREAKOUT_SHORT, t, mid, lv, c1h, p)
    assert s["pb"] == "a5" and s["is_market"] and s["side"] == "short" and s["leverage"] == 20.0
    assert s["stop_px"] == pytest.approx(100.05 + 0.75)                      # the break bar's own high + Brent's 0.75
    measured_target = (PRIOR_LOW - (PRIOR_HIGH - PRIOR_LOW)) * 1.005         # 96.0 x the live 0.5% offset
    assert s["target_px"] == pytest.approx(measured_target)
    risk = s["stop_px"] - mid
    r = (mid - s["target_px"]) / risk
    assert r >= 2.0                                                          # clears the live 2R gate


def test_a5_no_break_yet_returns_none():
    c1h = breakout_hours_short(break_close=100.5)   # closed back inside the prior range: no signal
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured")
    assert pullback_setup(BREAKOUT_SHORT, t, 100.3, lv, c1h, p) is None


def test_a5_long_mirrors_short():
    c1h = breakout_hours_long()
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    mid = 104.3
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured")
    s = pullback_setup(BREAKOUT_LONG, t, mid, lv, c1h, p)
    assert s["pb"] == "a5" and s["is_market"] and s["side"] == "long"
    assert s["stop_px"] == pytest.approx(103.95 - 0.75)                      # the break bar's own low - Brent's 0.75
    measured_target = (PRIOR_HIGH + (PRIOR_HIGH - PRIOR_LOW)) * 0.995
    assert s["target_px"] == pytest.approx(measured_target)


def test_a5_fixed_r_target_overrides_measured_move():
    c1h = breakout_hours_short()
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    mid = 99.7
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured", target_r=2.5)
    s = pullback_setup(BREAKOUT_SHORT, t, mid, lv, c1h, p)
    risk = s["stop_px"] - mid
    assert s["target_px"] == pytest.approx(mid - 2.5 * risk)                 # target_r wins over pb_target="measured"


def test_a5_pb_target_r_wins_over_global_target_r_and_leaves_the_main_rule_alone():
    c1h = breakout_hours_short()
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    mid = 99.7
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured", target_r=1.0, pb_target_r=3.0)
    s = pullback_setup(BREAKOUT_SHORT, t, mid, lv, c1h, p)
    risk = s["stop_px"] - mid
    assert s["target_px"] == pytest.approx(mid - 3.0 * risk)   # pb_target_r (3.0) wins over target_r (1.0)
    # the global target_r (unset here) still reaches the MAIN rule's own target; pb_target_r never does
    main_wide = dataclasses.replace(BREAKOUT_SHORT, entry_ceiling=None)
    main_r22 = _setup(main_wide, mid, lv, dataclasses.replace(p, target_r=2.2, pb_target_r=None), 1.0)
    main_live = _setup(main_wide, mid, lv, dataclasses.replace(p, target_r=None, pb_target_r=None), 1.0)
    assert not main_r22.get("refused")    # target_r=2.2 gives the far-stop main entry enough R to pass
    # target_r changes the main rule's own target/gate outcome; pb_target_r (set on `p`, read above) never does
    assert main_r22.get("target_px") != main_live.get("target_px") or bool(main_r22.get("refused")) != bool(main_live.get("refused"))


def test_a5_cap_thesis_stop():
    c1h = breakout_hours_short()
    t = c1h[24].t + HOUR_MS
    lv = _levels(c1h, 24, 24)
    mid = 99.7
    p = _base(pullback_entry="a5", pb_breakout_stop_h=1, pb_target="measured")
    tight = dataclasses.replace(BREAKOUT_SHORT, stop_px=100.3)               # narrower than a5's own 100.8
    assert pullback_setup(tight, t, mid, lv, c1h, p) == {"refused": "pb_wide"}
    assert pullback_setup(tight, t, mid, lv, c1h, dataclasses.replace(p, pb_cap_thesis_stop=False))["pb"] == "a5"


def breakout_hours_short_calm_tail() -> list:
    """18 wide "far" hours (100.6-104.0, the measured-move's raw material),
    6 tight "near" hours right at the low (99.95-100.05 -- so the sim's own
    DEFAULT `pb_breakout_stop_h` of 4 reads a tight recent structure, not
    the far bars' wide range, and the 20x gate still clears), then the
    break bar itself."""
    out = [bar(i, 101.0, 104.0, 100.6, 101.0) for i in range(18)]
    out += [bar(18 + j, 100.0, 100.05, 99.95, 100.0) for j in range(6)]
    out.append(bar(24, 99.95, 100.0, 95.0, 99.6))
    return out


def test_a5_in_the_sim_takes_the_entry_live_refuses():
    """A short thesis entered well above the current mid (a chase live
    refuses outright): a5 catches the breakdown the moment the 1h bar
    closes under the prior 24h low, on the sim's own default
    `pb_breakout_stop_h` (4)."""
    c1h = breakout_hours_short_calm_tail()
    c15 = flat_ticks(c1h, px=99.6, n=8)       # ticks start right after the break bar CLOSES
    chase_thesis = lambda t: Direction(side="short", entry_ceiling=103.0, regime_id=str(T0))   # noqa: E731
    live = simulate(c15, c1h, chase_thesis, _base())
    assert not live.trades and live.open_position_at_end is None and live.refused["chase"] > 0
    run = simulate(c15, c1h, chase_thesis, _base(pullback_entry="a5", pb_target="measured"))
    assert run.open_position_at_end is not None and run.open_position_at_end["side"] == "short"
    assert run.open_position_levels["entry_kind"] == "a5"
    assert run.position_side == "short"


# ── the entry guard ────────────────────────────────────────────────────────

def test_entry_guard_blocks_and_scales_and_top_up_keeps_the_scale():
    c1h = range_hours()
    c15 = flat_ticks(c1h)
    full = simulate(c15, c1h, LONG, _base())
    blocked = simulate(c15, c1h, LONG, _base(), entry_guard=lambda t, s: 0.0)
    assert not blocked.trades and blocked.open_position_at_end is None and blocked.refused["guard"] > 0
    half = simulate(c15, c1h, LONG, _base(), entry_guard=lambda t, s: 0.5)
    assert half.open_position_at_end["size"] == pytest.approx(0.5 * full.open_position_at_end["size"], rel=1e-9)
    assert half.guard_scaled == 1
    # a top-up rests at the entry while under full size; the scaled position is
    # at its own (scaled) full size, so none fills even when price trades there
    dip = c15 + [Candle(t=c15[-1].t + Q, o=108.3, h=108.35, l=108.2, c=108.3, v=1.0)]
    half_dip = simulate(dip, c1h, LONG, _base(), entry_guard=lambda t, s: 0.5)
    assert half_dip.open_position_at_end["size"] == pytest.approx(half.open_position_at_end["size"], rel=1e-9)


# ── the portfolio's correlation guard ──────────────────────────────────────

def test_guard_mult_pairs_desks_and_drivers():
    g = CorrGuard(pair="same", mult=0.0, desks=("silver",))
    assert guard_mult(g, "silver", "short", 0, {"oil": "short", "silver": None}) == 0.0
    assert guard_mult(g, "silver", "long", 0, {"oil": "short", "silver": None}) == 1.0
    assert guard_mult(g, "oil", "short", 0, {"oil": None, "silver": "short"}) == 1.0     # oil is not guarded
    assert guard_mult(g, "silver", "short", 0, {"oil": None}) == 1.0                    # oil flat
    o = CorrGuard(pair="opposite", mult=0.5)
    assert guard_mult(o, "oil", "long", 0, {"silver": "short"}) == 0.5
    assert guard_mult(o, "oil", "short", 0, {"silver": "short"}) == 1.0
    tagged = CorrGuard(pair="same", mult=0.0, drivers={"oil": lambda t: frozenset({"iran"}),
                                                        "silver": lambda t: frozenset({"rates"})})
    assert guard_mult(tagged, "silver", "short", 0, {"oil": "short"}) == 1.0           # no shared driver
    shared = dataclasses.replace(tagged, drivers={"oil": lambda t: frozenset({"iran"}),
                                                  "silver": lambda t: frozenset({"iran", "rates"})})
    assert guard_mult(shared, "silver", "short", 0, {"oil": "short"}) == 0.0


def _desks(c1h, c15):
    long_ = lambda t: Direction(side="long")   # noqa: E731
    p = _base()
    return [Desk("oil", 0.9, c15, c1h, long_, p), Desk("silver", 0.1, c15, c1h, long_, p)]


def test_portfolio_guard_none_is_byte_identical_and_validated():
    c1h = range_hours()
    c15 = flat_ticks(c1h)
    a = simulate_portfolio(_desks(c1h, c15), 225.0)
    b = simulate_portfolio(_desks(c1h, c15), 225.0, None)
    assert repr(a.runs) == repr(b.runs) and a.equity_curve == b.equity_curve and not b.guard_log
    with pytest.raises(ValueError):
        simulate_portfolio(_desks(c1h, c15), 225.0, CorrGuard(pair="both"))
    with pytest.raises(ValueError):
        simulate_portfolio(_desks(c1h, c15), 225.0, CorrGuard(pair="same", mult=1.5))


def test_portfolio_same_side_guard_blocks_the_second_desk_on_the_next_tick():
    """Both desks long on one clock: on the first tick neither sees the
    other (the snapshot is the previous tick's), so both open; with the
    silver desk entering later it is blocked while oil holds."""
    c1h = range_hours()
    c15 = flat_ticks(c1h)
    long_ = LONG
    later = lambda t: Direction(side="long") if t >= c15[2].t else None   # noqa: E731
    p = _base()
    desks = [Desk("oil", 0.9, c15, c1h, long_, p), Desk("silver", 0.1, c15, c1h, later, p)]
    free = simulate_portfolio(desks, 225.0)
    assert free.runs["silver"].trades or free.runs["silver"].open_position_at_end
    guarded = simulate_portfolio(desks, 225.0, CorrGuard(pair="same", mult=0.0, desks=("silver",)))
    s = guarded.runs["silver"]
    assert not s.trades and s.open_position_at_end is None and s.refused["guard"] > 0
    assert guarded.guard_log and guarded.guard_log[0]["held"] == {"oil": "long"}
    # the opposite-pair guard leaves a same-side pair alone
    opp = simulate_portfolio(desks, 225.0, CorrGuard(pair="opposite", mult=0.0))
    assert repr(opp.runs) == repr(free.runs)
