"""Gate tests for the two-desk portfolio combiner
(services/strategies/backtest/portfolio.py, DESK_SPLIT_TEST_2026-11-02.md).

No network. Bars are synthetic: a seeded random walk for the parity tests,
and hand-built bars where the exact size of one trade is checked. Covers:
shares must add up to 1; each desk sizes off share x the WHOLE account;
one desk's loss shrinks the other desk's next size; a desk may lose more
than the start while the other carries the account (the cushion); 100/0
and 0/100 equal the single-desk sim byte for byte; the step generator
equals simulate.
"""
from __future__ import annotations

import dataclasses
import random
from typing import List, Optional, Tuple

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, _full_size_notional, live_params_before_no_break, risk_capped_notional,
    simulate, simulate_steps)
from services.strategies.backtest.portfolio import (
    Desk, account_equity, daily_changes, desk_correlation, pearson, portfolio_stats, simulate_portfolio,
    validate_shares)

HOUR_MS = 3_600_000
Q = 900_000
START = 225.0


def walk(seed: int, hours: int = 24 * 8, p0: float = 100.0, vol: float = 0.004) -> Tuple[List[Candle], List[Candle]]:
    """A seeded random walk: 15m bars and the 1h bars built from them."""
    rng = random.Random(seed)
    c15: List[Candle] = []
    px = p0
    for i in range(hours * 4):
        o = px
        c = o * (1.0 + rng.gauss(0.0, vol))
        h = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol / 2)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol / 2)))
        c15.append(Candle(t=i * Q, o=o, h=h, l=lo, c=c, v=1.0))
        px = c
    c1h = []
    for i in range(0, len(c15), 4):
        g = c15[i:i + 4]
        c1h.append(Candle(t=g[0].t, o=g[0].o, h=max(b.h for b in g), l=min(b.l for b in g), c=g[-1].c, v=4.0))
    return c15, c1h


def flipping(period_h: int = 7):
    """Long for `period_h` hours, then short, and so on: many entries and flips."""
    def at(t: int) -> Optional[Direction]:
        return Direction(side="long" if (t // (period_h * HOUR_MS)) % 2 == 0 else "short")
    return at


def never(t: int) -> Optional[Direction]:
    return None


def _p(**over) -> Params:
    return dataclasses.replace(live_params_before_no_break(), starting_equity=START, **over)


# ── shares ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shares", [{"oil": 0.6, "silver": 0.6}, {"oil": 0.5, "silver": 0.4},
                                    {"oil": 1.1, "silver": -0.1}, {}])
def test_shares_must_add_up_to_one(shares):
    with pytest.raises(ValueError):
        validate_shares(shares)


@pytest.mark.parametrize("oil", [1.0, 0.9, 0.7, 0.5, 0.3, 0.0])
def test_the_split_grid_is_valid(oil):
    validate_shares({"oil": oil, "silver": round(1.0 - oil, 10)})


def test_simulate_portfolio_refuses_bad_shares():
    c15, c1h = walk(1)
    with pytest.raises(ValueError):
        simulate_portfolio([Desk("oil", 0.6, c15, c1h, never, _p()), Desk("silver", 0.6, c15, c1h, never, _p())],
                           START)
    with pytest.raises(ValueError):
        simulate_portfolio([Desk("oil", 0.5, c15, c1h, never, _p()), Desk("oil", 0.5, c15, c1h, never, _p())],
                           START)


# ── the step generator is simulate ─────────────────────────────────────────

def test_simulate_steps_yields_every_tick_and_returns_the_run():
    c15, c1h = walk(2)
    p = _p(risk_cap_frac=0.15)
    steps = simulate_steps(c15, c1h, flipping(), p)
    marks = []
    while True:
        try:
            t, mark, _ = next(steps)
            marks.append((t, mark))
        except StopIteration as done:
            run = done.value
            break
    assert repr(run) == repr(simulate(c15, c1h, flipping(), p))
    assert marks == run.equity_curve
    assert len(run.trades) > 5


# ── 100/0 and 0/100 are the single-desk sim, byte for byte ────────────────

@pytest.mark.parametrize("over", [{}, {"risk_cap_frac": 0.15}, {"tick_minutes": 60}, {"entry_mode": "edge"}])
def test_one_desk_split_equals_the_single_desk_sim(over):
    c15, c1h = walk(3)
    s15, s1h = walk(4, p0=50.0)
    p = _p(**over)
    single = simulate(c15, c1h, flipping(), p)
    prun = simulate_portfolio([Desk("oil", 1.0, c15, c1h, flipping(), p),
                               Desk("silver", 0.0, s15, s1h, flipping(5), p)], START)
    assert list(prun.runs) == ["oil"]                 # a 0% desk is not run at all
    assert repr(prun.runs["oil"]) == repr(single)
    assert prun.equity_curve == single.equity_curve
    assert prun.final_equity == single.final_equity
    assert len(single.trades) > 3

    single_s = simulate(s15, s1h, flipping(5), p)
    prun_s = simulate_portfolio([Desk("oil", 0.0, c15, c1h, flipping(), p),
                                 Desk("silver", 1.0, s15, s1h, flipping(5), p)], START)
    assert repr(prun_s.runs["silver"]) == repr(single_s)
    assert prun_s.equity_curve == single_s.equity_curve


def test_one_desk_split_ignores_the_desks_own_starting_equity():
    """Every desk starts at the portfolio's start, whatever its params say."""
    c15, c1h = walk(5)
    p = _p(risk_cap_frac=0.15)
    prun = simulate_portfolio([Desk("oil", 1.0, c15, c1h, flipping(), dataclasses.replace(p, starting_equity=64.0))],
                              START)
    assert repr(prun.runs["oil"]) == repr(simulate(c15, c1h, flipping(), p))


# ── sizing off the whole account ───────────────────────────────────────────

def test_a_desk_sizes_off_its_share_of_total_equity():
    """50/50, the other desk never trades: every entry sizes at half the
    account as it stands, i.e. 0.5 x (start + this desk's own P&L so far),
    not off a separate $112.50 pot that keeps all of its own P&L."""
    c15, c1h = walk(6)
    p = _p(slippage=0.0)
    prun = simulate_portfolio([Desk("oil", 0.5, c15, c1h, flipping(), p),
                               Desk("silver", 0.5, c15, c1h, never, p)], START)
    trades = prun.runs["oil"].trades
    assert len(trades) > 3 and not any(tr.added for tr in trades)
    realized = 0.0
    for tr in trades:
        want = risk_capped_notional(0.5 * (START + realized), p, 20.0, tr.entry_px, tr.orig_stop_px)
        assert tr.size * tr.entry_px == pytest.approx(want, rel=1e-9)
        realized += tr.net_pnl_usd
    assert prun.final_equity == pytest.approx(START + realized, abs=1e-6) or prun.runs["oil"].open_position_at_end
    # the first entry is exactly half the full port of the start
    assert trades[0].size * trades[0].entry_px == pytest.approx(_full_size_notional(0.5 * START, p, 20.0), rel=1e-9)


def test_risk_cap_applies_to_the_desk_slice():
    c15, c1h = walk(7)
    p = _p(slippage=0.0, risk_cap_frac=0.15)
    prun = simulate_portfolio([Desk("oil", 0.7, c15, c1h, flipping(), p),
                               Desk("silver", 0.3, c15, c1h, never, p)], START)
    tr = prun.runs["oil"].trades[0]
    assert tr.size * tr.entry_px == pytest.approx(
        risk_capped_notional(0.7 * START, p, 20.0, tr.entry_px, tr.orig_stop_px), rel=1e-9)


def _bars_one_trade(entry_mid: float = 99.3, stop_low: Optional[float] = None, n_after: int = 6):
    """24 warm-up hours in [99, 101], then 15m bars: a long entry at the
    first bar's open (99.3; stop 98.25 = 24h low 99 less 0.75; target
    101 x 0.995 = 100.495, 2R clears), and, with `stop_low`, a bar that
    trades down through the stop."""
    c1h = [Candle(t=i * HOUR_MS, o=100.0, h=103.0, l=99.0, c=100.0, v=1.0) for i in range(24)]
    t0 = 24 * HOUR_MS
    c15 = [Candle(t=t0, o=entry_mid, h=entry_mid + 0.05, l=entry_mid - 0.05, c=entry_mid, v=1.0)]
    if stop_low is not None:
        c15.append(Candle(t=t0 + Q, o=entry_mid, h=entry_mid, l=stop_low, c=stop_low + 0.05, v=1.0))
    while len(c15) < n_after:
        last = c15[-1].c
        c15.append(Candle(t=t0 + len(c15) * Q, o=last, h=last + 0.02, l=last - 0.02, c=last, v=1.0))
    return c15, c1h


def _long_at(times):
    def at(t):
        return Direction(side="long") if t in times else None
    return at


def test_one_desks_loss_shrinks_the_other_desks_next_size():
    """Oil buys at 24h+0 and is stopped on the next bar; silver's only call
    comes two bars later. Silver sizes off half the account AFTER oil's
    loss, smaller than when oil has no calls."""
    t0 = 24 * HOUR_MS
    o15, o1h = _bars_one_trade(stop_low=98.0)
    s15, s1h = _bars_one_trade()
    p = _p(slippage=0.0)
    silver_calls = _long_at({t0 + 2 * Q})
    with_loss = simulate_portfolio([Desk("oil", 0.5, o15, o1h, _long_at({t0}), p),
                                    Desk("silver", 0.5, s15, s1h, silver_calls, p)], START)
    no_oil = simulate_portfolio([Desk("oil", 0.5, o15, o1h, never, p),
                                 Desk("silver", 0.5, s15, s1h, silver_calls, p)], START)
    oil_tr = with_loss.runs["oil"].trades
    assert len(oil_tr) == 1 and oil_tr[0].exit_reason == "stop" and oil_tr[0].net_pnl_usd < 0
    s_loss = with_loss.runs["silver"].open_position_at_end
    s_flat = no_oil.runs["silver"].open_position_at_end
    assert s_loss and s_flat
    n_loss, n_flat = s_loss["size"] * s_loss["entry_px"], s_flat["size"] * s_flat["entry_px"]
    assert n_loss < n_flat
    assert n_flat == pytest.approx(_full_size_notional(0.5 * START, p, 20.0), rel=1e-9)
    # rel 1e-9: two desks run on the cushion (start x 1e4), about 1e-12 of float noise
    assert n_loss == pytest.approx(_full_size_notional(0.5 * (START + oil_tr[0].net_pnl_usd), p, 20.0), rel=1e-9)


def test_the_other_desks_open_loss_counts_too():
    """Account equity is marked: silver's entry while oil is still open and
    under water sizes off half of start + oil's unrealized loss."""
    t0 = 24 * HOUR_MS
    o15, o1h = _bars_one_trade()
    # oil drifts down without reaching its 98.25 stop
    o15 = o15[:1] + [Candle(t=t0 + Q, o=99.3, h=99.3, l=98.6, c=98.7, v=1.0),
                     Candle(t=t0 + 2 * Q, o=98.7, h=98.8, l=98.6, c=98.7, v=1.0),
                     Candle(t=t0 + 3 * Q, o=98.7, h=98.8, l=98.6, c=98.7, v=1.0)]
    s15, s1h = _bars_one_trade(n_after=4)
    p = _p(slippage=0.0)
    prun = simulate_portfolio([Desk("oil", 0.5, o15, o1h, _long_at({t0}), p),
                               Desk("silver", 0.5, s15, s1h, _long_at({t0 + 2 * Q}), p)], START)
    oil_mark_before = dict(prun.desk_pnl["oil"])[t0 + Q]        # end of the tick before silver's entry
    assert oil_mark_before < 0
    s = prun.runs["silver"].open_position_at_end
    assert s["size"] * s["entry_px"] == pytest.approx(
        _full_size_notional(0.5 * (START + oil_mark_before), p, 20.0), rel=1e-9)


def _trend(c15: List[Candle], k: float) -> List[Candle]:
    f = [1.0 + k * c.t / 1e10 for c in c15]
    return [Candle(t=c.t, o=c.o * x, h=c.h * x, l=c.l * x, c=c.c * x, v=1.0) for c, x in zip(c15, f)]


def _hours(c15: List[Candle]) -> List[Candle]:
    return [Candle(t=c15[i].t, o=c15[i].o, h=max(b.h for b in c15[i:i + 4]), l=min(b.l for b in c15[i:i + 4]),
                   c=c15[i + 3].c, v=4.0) for i in range(0, len(c15) - 3, 4)]


def _carry_scenario(monkeypatch=None, cushion: Optional[float] = None):
    """Oil always long on an uptrend; silver whipsawed on a choppy walk
    until its losses pass the $225 start (seeded, checked below)."""
    if cushion is not None:
        from services.strategies.backtest import portfolio
        monkeypatch.setattr(portfolio, "CUSHION_X", cushion)
    u15, _ = walk(13, hours=24 * 10, vol=0.002)
    s15, _ = walk(113, hours=24 * 10, vol=0.006)
    rise = _trend(u15, 2.0)
    p = _p()
    return simulate_portfolio([Desk("oil", 0.5, rise, _hours(rise), lambda t: Direction(side="long"), p),
                               Desk("silver", 0.5, s15, _hours(s15), flipping(2), p)], START)


def test_a_desk_may_lose_more_than_the_start_while_the_other_carries_the_account():
    """Regression (found building this eval): with no cushion, a desk whose
    losses passed the $225 start hit the sim's own "equity <= 0 is
    liquidation" net and stopped trading although the account was still
    up. With the cushion it keeps trading and is not liquidated."""
    prun = _carry_scenario()
    stats = portfolio_stats(prun)
    assert stats["liquidated"] == [] and not prun.ruined and prun.final_equity > 0
    pnl = prun.desk_pnl["silver"]
    crossed = next(t for t, v in pnl if v < -START)       # the scenario only tests something if this exists
    assert any(tr.entry_time > crossed for tr in prun.runs["silver"].trades)
    assert prun.final_equity == pytest.approx(START + stats["desk_pnl"]["oil"] + stats["desk_pnl"]["silver"],
                                              abs=0.01)


def test_without_the_cushion_that_desk_would_be_cut_off(monkeypatch):
    """The bug the cushion fixes, shown: CUSHION_X 0 marks silver liquidated."""
    prun = _carry_scenario(monkeypatch, cushion=0.0)
    assert portfolio_stats(prun)["liquidated"] == ["silver"]


def test_account_equity_is_exact_for_one_desk():
    assert account_equity(123.456789, 0.0, {}, 225.0) == 123.456789
    assert account_equity(2_250_300.0, 2_250_000.0, {"b": 2_250_225.0 - 50.0}, 2_250_225.0) == pytest.approx(250.0)


# ── stats ──────────────────────────────────────────────────────────────────

def test_pearson_and_daily_changes():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None
    assert pearson([1, 2], [1, 2]) is None
    day = 86_400_000
    ch = daily_changes([(0, 230.0), (100, 240.0), (day, 216.0), (2 * day + 5, 216.0)], 225.0)
    assert ch == [(0, 15.0, 225.0), (1, -24.0, 240.0), (2, 0.0, 216.0)]


def test_portfolio_stats_on_a_two_desk_run():
    c15, c1h = walk(8)
    s15, s1h = walk(9, p0=50.0)
    p = _p(risk_cap_frac=0.15)
    prun = simulate_portfolio([Desk("oil", 0.7, c15, c1h, flipping(), p),
                               Desk("silver", 0.3, s15, s1h, flipping(5), p)], START)
    s = portfolio_stats(prun)
    assert s["trades"] == len(prun.runs["oil"].trades) + len(prun.runs["silver"].trades)
    assert s["final_equity"] == pytest.approx(START + s["desk_pnl"]["oil"] + s["desk_pnl"]["silver"], abs=0.02)
    assert 0.0 <= s["max_dd_pct"] <= 100.0 and s["worst_day_pct"] <= 0.0
    assert s["corr"] is None or -1.0 <= s["corr"] <= 1.0
    assert desk_correlation(prun) == pytest.approx(s["corr"], abs=1e-3) if s["corr"] is not None else True
    # same clock: one account mark per 15m tick of the union
    assert [t for t, _ in prun.equity_curve] == sorted({c.t for c in c15} | {c.t for c in s15})
