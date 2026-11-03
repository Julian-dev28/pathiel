"""Intraday "wiggle" scalp simulator (SCALP_TEST_2026-11-03.md).

Operator, 2026-11-03: "can u test a strategy where we play the wiggle?
like something small like 5% of the portfolio at 20x leverage
intraday/intrahour swing trade so we can still profit in a sideways market
like this?" Brent is chopping 103-107 while the oil desk holds a far-target
fade of the 24h range (OIL_STRATEGY.md); this is a SEPARATE, small, fast
mean-reversion book meant to bank the chop itself.

This module is pure and deterministic -- no I/O, no network, no LLM. It
reuses the live gates from `pathiel.agents.bracket` (`check_leverage`,
`check_levels`, `liq_estimate`) rather than re-deriving the 20x line or the
reward:risk gate, so this backtest and the live `place_bracket` tool cannot
drift apart. `services.strategies.backtest.regime`'s causal label is reused
for the optional chop gate.

    simulate(bars, params, direction_at=None, active_at=None,
             regime_series=None) -> Run

Two rule families (`Params.rule`):

  "range_fade"    Fade the high/low of the last `lookback_hours` of CLOSED
                  bars. Target: `target_frac` of the way across that range
                  (0.5 = the midpoint, 1.0 = the opposite edge). Stop:
                  `stop_atr_k` x ATR beyond the edge the entry rests at.
  "mean_revert"   A rolling mean/std over the last `ma_window` CLOSED bars.
                  Entry at `z_entry` standard deviations from the mean
                  (price below the mean = long, above = short). Target: the
                  mean AT THE TICK THE TRADE OPENED (fixed at entry, not
                  re-chased as the mean drifts -- see "Why the target is
                  fixed" below). Stop: `z_stop` standard deviations from
                  that same entry-time mean (wider than the entry z).

Both are "fade the extreme, exit at the middle" rules; `rule` just picks
what "the extreme" and "the middle" mean.

Clock and lookahead
--------------------
One tick per bar (`Params.bar_minutes`: 5 or 15 -- must match the
granularity of the `bars` passed in), oldest first, at the bar's OPEN
time. Every level a tick reads (the range edge, ATR, the rolling mean/std)
is built from bars STRICTLY BEFORE the current one (`bars[:i]`), then
tested against the CURRENT bar's own high/low/close. This is a stricter,
simpler no-lookahead rule than `desk_tick_sim`'s "resting order fills on a
bar after the one it rested on": here the level is recomputed fresh every
tick (as if the resting limit is re-rested at the fresh edge each bar it
doesn't fill), so a tick's own bar can never move the level it is tested
against. Shifting any bar at or after `t` therefore cannot change what a
tick at or before `t` decided (test_scalp_sim.py::test_no_lookahead).

Entry, stop, target
--------------------
Flat and no position: for each side `params.sides` allows (both by
default; `direction_at`/`active_at` below narrow it per tick), compute the
rule's entry/stop/target from bars closed before this tick. Gate with
`check_leverage` (the 20x line: the stop must sit inside half the way to
liquidation) and `check_levels` (`min_r`). A side that fails either gate is
refused and counted (`Run.refused`); nothing rests for it. A side that
passes rests a limit at its entry price for THIS bar only (re-rested next
bar from the fresh level if it still doesn't fill) -- it fills, maker fee,
if the bar's low (long) / high (short) reaches it. If both sides would
fill in the same bar (a wide bar spanning both edges -- rare, only
possible for `range_fade` on a short lookback), the side whose entry is
closer to the bar's OPEN fills (the more conservative read: price is
assumed to have moved toward that edge first).

Exits
-----
Stop or target fires intrabar: stop is a market order (taker fee +
`slippage`), target is a resting limit (maker fee, fills on a bare touch,
same convention as `desk_tick_sim`'s `target_mode="touch"`). If both
trigger in the same bar, the stop is assumed to have fired first (the
conservative read of one bar's price action, same as `desk_tick_sim`).
`Params.time_stop_bars` closes at market (taker fee + slippage) after that
many bars in the trade, counted from the bar AFTER the fill. Liquidation
(`pathiel.agents.bracket.liq_estimate`) is checked every bar a position is
open; a breach ends the run at once (equity zeroed, `Run.liquidated` True),
same as `desk_tick_sim`.

Why the target is fixed at entry (mean_revert)
-----------------------------------------------
The rolling mean moves every bar. Chasing it as a moving target would let
a position that is already profitable keep its target just out of reach
in a trending bar, and makes "R multiple" ill-defined (the denominator
would change after the fact). Freezing stop AND target at the values the
rule computed at entry keeps every trade a plain, auditable bracket, the
same shape `place_bracket` places live.

Gates
-----
`direction_at(t) -> Optional[str]`: "long" | "short" | "flat" | None. When
given, a NEW entry on a tick is only allowed on the side `direction_at(t)`
names; "flat" or None blocks both sides that tick (an open position is
never touched). This is the thesis-direction gate: pass the journaled
Brent thesis's side (`services.strategies.backtest.data.load_journaled_
theses` + the same call-interval logic `desk_tick_backtest.journaled_
direction` uses) to trade the scalp only with the desk's own thesis.

`active_at(t) -> bool`: when given and False, no NEW entry that tick (an
open position is never touched). This is the generic "only while X" gate
-- used here for "Brent scalp only while the oil desk itself is flat"
(`oil_desk_flat_at` below, built from a `desk_tick_sim.Run`'s own trade
list: Hyperliquid nets one position per coin per account, so a true Brent
scalp can only ever run in the gaps between the oil desk's own trades).

`regime_series` (`services.strategies.backtest.regime.RegimeSeries`) with
`Params.regime_filter = "chop_only"`: a NEW entry is only allowed while the
causal regime label at the tick is `regime.CHOP` (a trend label, OR a
missing/warm-up label, blocks -- conservative: trade the wiggle only when
the label is confidently sideways).

Sizing
------
The book trades `Params.slice_frac` of the (own-equity) book's equity as
its margin pool, same formula as `desk_tick_sim._full_size_notional`
(margin net of the opening taker fee, times leverage). `Params.risk_frac`
caps that further: the stop-out loss (price move to the stop, taker fee
both ways, flat slippage) may cost at most `risk_frac` of the SLICE's own
margin, same formula as `desk_tick_sim.risk_capped_notional` scoped to the
slice instead of the whole account. The stop itself must still clear
`check_leverage`'s half-the-liquidation-distance line at `params.leverage`
-- risk_frac only ever shrinks the size, never the leverage or the stop.

Random-entry control
---------------------
`simulate_random_control(bars, params, seed, entries_per_day)` is NOT the
rule above -- it is the overfitting guard's control: it opens at a random
bar and side (market order, taker fee) with the SAME stop distance
(`stop_atr_k` x ATR at that bar) and SAME minimum reward (`min_r` x risk)
the rule's own gate requires, same sizing, same fees. If the rule's edge
is real, it should beat many seeds of "trade this often, this big, for
this long, picked at random" -- not just beat doing nothing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from random import Random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage, check_levels, liq_estimate
from pathiel.models.types import Candle
from services.strategies.backtest.regime import CHOP, RegimeConfig, RegimeSeries

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

RULES = ("range_fade", "mean_revert")


# ── inputs ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Params:
    rule: str = "range_fade"             # "range_fade" | "mean_revert"
    bar_minutes: int = 15                # 5 | 15 -- must match the candles passed to simulate()
    lookback_hours: float = 2.0          # range_fade: hours of CLOSED bars behind the range edge (1/2/4)
    target_frac: float = 0.5             # range_fade: share of the range from the edge to the far side (0.5 = mid)
    ma_window: int = 48                  # mean_revert: rolling mean/std window, in bars
    z_entry: float = 2.0                 # mean_revert: enter once |z| >= this (1.5 / 2.0 / 2.5)
    z_stop: float = 3.5                  # mean_revert: stop at this wider |z| from the entry-time mean
    atr_period: int = 14                 # ATR period, in bars of `bar_minutes`
    stop_atr_k: float = 0.5              # range_fade stop: k x ATR beyond the edge the entry rests at
    time_stop_bars: Optional[int] = None  # close at market after this many bars in the trade (None = off)
    leverage: float = 20.0
    max_leverage: float = 20.0           # venue max leverage for the coin (xyz:BRENTOIL / xyz:CL: 20)
    min_r: float = 1.2                   # reward:risk gate (bracket.check_levels)
    taker_fee: float = 0.00045           # 4.5 bps (desk_tick_sim.Params)
    maker_fee: float = 0.00015           # 1.5 bps
    slippage: float = 0.0003             # stop / time-exit fill slippage, fraction of price
    starting_equity: float = 64.0        # the WHOLE account's equity; the slice below is a share of this
    slice_frac: float = 0.05             # share of equity this book sizes off (the 5% ask; 0.15 also tested)
    risk_frac: float = 0.5               # cap: a stop may cost at most this share of the SLICE's own margin
    sides: Tuple[str, ...] = ("long", "short")   # both edges by default
    regime_filter: Optional[str] = None  # None | "chop_only"
    regime_cfg: RegimeConfig = field(default_factory=RegimeConfig)


Direction = Optional[str]                 # "long" | "short" | "flat" | None
DirectionAt = Callable[[int], Direction]
ActiveAt = Callable[[int], bool]


# ── outputs ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    side: str
    entry_time: int
    entry_px: float
    size: float
    stop_px: float
    target_px: float
    exit_time: Optional[int] = None
    exit_px: Optional[float] = None
    exit_reason: Optional[str] = None     # stop | target | time | liquidation
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    net_pnl_usd: float = 0.0
    risk_usd: float = 0.0
    r_multiple: Optional[float] = None
    bars_held: int = 0


@dataclass
class Run:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)
    final_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    liquidated: bool = False
    liquidation_time: Optional[int] = None
    refused: Counter = field(default_factory=Counter)
    fees_total_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    params: Optional[Params] = None
    open_position_at_end: Optional[Dict[str, Any]] = None


@dataclass
class _Position:
    side: str
    entry_time: int
    entry_px: float
    size: float
    stop_px: float
    target_px: float
    risk_usd: float
    entry_fee_usd: float
    bars_open: int = 0


# ── ATR / range / rolling mean (pure, causal given the slice passed in) ────

def true_ranges(bars: Sequence[Candle]) -> List[float]:
    """True range of each bar against the one before it (len = len(bars)-1)."""
    return [max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars, bars[1:])]


def atr(closed: Sequence[Candle], period: int) -> Optional[float]:
    """Simple ATR over the last `period` true ranges of `closed` (already
    sorted oldest-first, already the bars strictly before the tick).
    None until `period + 1` bars exist."""
    if len(closed) < period + 1:
        return None
    trs = true_ranges(closed[-(period + 1):])
    return sum(trs) / period


def range_edges(closed: Sequence[Candle], n_bars: int) -> Optional[Tuple[float, float]]:
    """(low, high) of the last `n_bars` of `closed`. None until that many exist."""
    if len(closed) < n_bars or n_bars <= 0:
        return None
    window = closed[-n_bars:]
    return min(c.l for c in window), max(c.h for c in window)


def rolling_mean_std(closed: Sequence[Candle], window: int) -> Optional[Tuple[float, float]]:
    """(mean, std) of the closes of the last `window` bars of `closed`
    (population std, matching a simple rolling z-score). None until
    `window` bars exist, or the std is zero (a dead/flat tape -- no
    meaningful z-score)."""
    if len(closed) < window or window <= 1:
        return None
    closes = [c.c for c in closed[-window:]]
    mean = sum(closes) / window
    var = sum((x - mean) ** 2 for x in closes) / window
    std = var ** 0.5
    if std <= 0:
        return None
    return mean, std


# ── sizing (reuses the live full-port / risk-cap formulas, scoped to the slice) ──

def slice_equity(equity: float, params: Params) -> float:
    """The book's own margin pool: `slice_frac` of the whole account's equity."""
    return max(0.0, equity) * params.slice_frac


def full_size_notional(slice_eq: float, params: Params) -> float:
    """Slice equity, net of the opening taker fee, times leverage -- the
    same shape as `desk_tick_sim._full_size_notional`, scoped to the slice."""
    margin = slice_eq / (1.0 + params.leverage * params.taker_fee)
    return margin * params.leverage


def risk_capped_size(slice_eq: float, params: Params, entry: float, stop: float) -> float:
    """Position size (units of the coin), the smaller of the full-slice size
    and the size whose stop-out costs `risk_frac` of the SLICE's equity.
    Same formula as `desk_tick_sim.risk_capped_notional`, scoped to the
    slice. Pure."""
    full_notional = full_size_notional(slice_eq, params)
    if entry <= 0:
        return 0.0
    full_size = full_notional / entry
    if params.risk_frac is None or params.risk_frac <= 0:
        return full_size
    per_unit = abs(entry - stop) + 2.0 * params.taker_fee * entry + params.slippage * entry
    if per_unit <= 0:
        return full_size
    capped_size = (params.risk_frac * slice_eq) / per_unit
    return min(full_size, capped_size)


# ── one side's candidate setup (pure) ───────────────────────────────────────

def _range_fade_setup(side: str, closed: Sequence[Candle], params: Params) -> Optional[Dict[str, Any]]:
    n_bars = max(1, round(params.lookback_hours * 60.0 / params.bar_minutes))
    edges = range_edges(closed, n_bars)
    a = atr(closed, params.atr_period)
    if edges is None or a is None or a <= 0:
        return None
    lo, hi = edges
    if hi <= lo:
        return None
    width = hi - lo
    if side == "long":
        entry = lo
        stop = lo - params.stop_atr_k * a
        target = lo + params.target_frac * width
    else:
        entry = hi
        stop = hi + params.stop_atr_k * a
        target = hi - params.target_frac * width
    return {"side": side, "entry_px": entry, "stop_px": stop, "target_px": target}


def _mean_revert_setup(side: str, closed: Sequence[Candle], params: Params) -> Optional[Dict[str, Any]]:
    ms = rolling_mean_std(closed, params.ma_window)
    if ms is None:
        return None
    mean, std = ms
    if side == "long":
        entry = mean - params.z_entry * std
        stop = mean - params.z_stop * std
        target = mean
    else:
        entry = mean + params.z_entry * std
        stop = mean + params.z_stop * std
        target = mean
    return {"side": side, "entry_px": entry, "stop_px": stop, "target_px": target}


def candidate_setup(side: str, closed: Sequence[Candle], params: Params) -> Optional[Dict[str, Any]]:
    if params.rule == "range_fade":
        return _range_fade_setup(side, closed, params)
    if params.rule == "mean_revert":
        return _mean_revert_setup(side, closed, params)
    raise ValueError(f"unknown rule {params.rule!r}")


def gated_setup(side: str, closed: Sequence[Candle], params: Params) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """`candidate_setup` run through the live leverage and min-R gates.
    Returns (setup-or-None, refusal-reason-or-None). Pure."""
    s = candidate_setup(side, closed, params)
    if s is None:
        return None, "no_levels"
    entry, stop, target = s["entry_px"], s["stop_px"], s["target_px"]
    if entry <= 0 or stop <= 0 or target <= 0 or entry == stop:
        return None, "bad_levels"
    lev_gate = check_leverage(entry, stop, params.leverage, params.max_leverage)
    if not lev_gate["ok"]:
        return None, "leverage"
    r_gate = check_levels(entry, stop, target, params.min_r)
    if not r_gate["ok"]:
        return None, "min_r"
    return s, None


# ── the simulator ────────────────────────────────────────────────────────

def _fill_this_bar(setup: Dict[str, Any], bar: Candle) -> bool:
    return bar.l <= setup["entry_px"] if setup["side"] == "long" else bar.h >= setup["entry_px"]


def _close_trade(pos: _Position, exit_px: float, t: int, reason: str, fee_rate: float, params: Params) -> Trade:
    sign = 1.0 if pos.side == "long" else -1.0
    gross = sign * (exit_px - pos.entry_px) * pos.size
    exit_fee = fee_rate * exit_px * pos.size
    fees = pos.entry_fee_usd + exit_fee
    net = gross - fees
    r = net / pos.risk_usd if pos.risk_usd > 0 else None
    return Trade(side=pos.side, entry_time=pos.entry_time, entry_px=pos.entry_px, size=pos.size,
                stop_px=pos.stop_px, target_px=pos.target_px, exit_time=t, exit_px=exit_px,
                exit_reason=reason, gross_pnl_usd=gross, fees_usd=fees, net_pnl_usd=net,
                risk_usd=pos.risk_usd, r_multiple=r, bars_held=pos.bars_open)


def simulate(bars: Sequence[Candle], params: Params, direction_at: Optional[DirectionAt] = None,
            active_at: Optional[ActiveAt] = None, regime_series: Optional[RegimeSeries] = None) -> Run:
    """Replay `params.rule` over `bars` (one bar_minutes granularity, oldest
    first), one tick per bar. See the module docstring for the full rule.
    Pure given its inputs."""
    ordered = sorted(bars, key=lambda c: c.t)
    run = Run(params=params)
    equity = params.starting_equity
    peak = equity
    pos: Optional[_Position] = None
    run.equity_curve.append((ordered[0].t if ordered else 0, equity))

    for i, bar in enumerate(ordered):
        t = bar.t
        closed = ordered[:i]
        # A new entry is only ever considered when the book was ALREADY
        # flat coming into this bar -- never right after a close inside
        # the same iteration. Without this, a position that closes on bar
        # i (at its target, say) would immediately re-test entry using the
        # structure as of bar i's OWN open (unchanged this iteration), and
        # bar i's own high/low can coincidentally re-fill at the very edge
        # it just exited at, cascading into several same-bar round trips.
        # No live order could place, rest AND fill inside the bar it was
        # born in -- same one-decision-per-bar discipline as
        # `desk_tick_sim` ("fills on a bar after the one it was placed").
        was_flat = pos is None

        if pos is not None:
            pos.bars_open += 1
            is_long = pos.side == "long"
            stop_hit = (bar.l <= pos.stop_px) if is_long else (bar.h >= pos.stop_px)
            target_hit = (bar.h >= pos.target_px) if is_long else (bar.l <= pos.target_px)
            exit_reason, exit_px = None, None
            if stop_hit:          # conservative: the stop is assumed to fire first in a two-sided bar
                exit_reason = "stop"
                sign = 1.0 if is_long else -1.0
                exit_px = pos.stop_px * (1.0 - sign * params.slippage)
            elif target_hit:
                exit_reason, exit_px = "target", pos.target_px
            elif params.time_stop_bars is not None and pos.bars_open >= params.time_stop_bars:
                exit_reason = "time"
                sign = 1.0 if is_long else -1.0
                exit_px = bar.c * (1.0 - sign * params.slippage)
            if exit_reason is not None:
                fee_rate = params.maker_fee if exit_reason == "target" else params.taker_fee
                trade = _close_trade(pos, exit_px, t, exit_reason, fee_rate, params)
                equity += trade.gross_pnl_usd - (trade.fees_usd - pos.entry_fee_usd)
                run.trades.append(trade)
                run.fees_total_usd += trade.fees_usd
                run.gross_pnl_usd += trade.gross_pnl_usd
                pos = None
            # liquidation check every bar a position is (still) open
            if pos is not None:
                liq = liq_estimate(pos.entry_px, params.leverage, params.max_leverage, is_long)
                breached = (bar.l <= liq) if is_long else (bar.h >= liq)
                if breached:
                    run.liquidated, run.liquidation_time = True, t
                    equity = 0.0
                    run.equity_curve.append((t, equity))
                    run.final_equity = equity
                    run.max_drawdown_pct = 1.0
                    return run

        if was_flat:
            allowed = set(params.sides)
            if direction_at is not None:
                d = direction_at(t)
                allowed &= ({d} if d in ("long", "short") else set())
            if active_at is not None and not active_at(t):
                allowed = set()
            if params.regime_filter == "chop_only":
                label = regime_series.label_at(t) if regime_series is not None else None
                if label != CHOP:
                    allowed = set()
                    run.refused["regime_not_chop"] += 1
            candidates: List[Dict[str, Any]] = []
            for side in sorted(allowed):
                setup, reason = gated_setup(side, closed, params)
                if setup is None:
                    if reason is not None:
                        run.refused[reason] += 1
                    continue
                if _fill_this_bar(setup, bar):
                    candidates.append(setup)
            if candidates:
                # a wide bar that would fill both sides: the one closer to
                # the bar's open is taken (conservative read of the path)
                setup = min(candidates, key=lambda s: abs(s["entry_px"] - bar.o))
                s_eq = slice_equity(equity, params)
                size = risk_capped_size(s_eq, params, setup["entry_px"], setup["stop_px"])
                if size > 0:
                    entry_fee = params.maker_fee * setup["entry_px"] * size
                    equity -= entry_fee
                    risk_usd = abs(setup["entry_px"] - setup["stop_px"]) * size
                    pos = _Position(side=setup["side"], entry_time=t, entry_px=setup["entry_px"], size=size,
                                    stop_px=setup["stop_px"], target_px=setup["target_px"], risk_usd=risk_usd,
                                    entry_fee_usd=entry_fee)
                    run.fees_total_usd += entry_fee

        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        run.max_drawdown_pct = max(run.max_drawdown_pct, dd)
        run.equity_curve.append((t, equity))

    run.final_equity = equity
    if pos is not None:
        run.open_position_at_end = {"side": pos.side, "entry_px": pos.entry_px, "size": pos.size,
                                    "stop_px": pos.stop_px, "target_px": pos.target_px,
                                    "entry_time": pos.entry_time, "risk_usd": pos.risk_usd}
    return run


# ── random-entry control (the overfitting guard) ────────────────────────────

def simulate_random_control(bars: Sequence[Candle], params: Params, seed: int,
                            entries_per_day: float = 4.0) -> Run:
    """The control: a market entry at a RANDOM bar and RANDOM side (never
    the rule's own trigger), stop `stop_atr_k` x ATR away and target
    `min_r` x that risk away -- the same stop distance and the same
    minimum reward the rule's own gate requires, same leverage/sizing/fee
    schedule (entry is a market order here, taker fee, since nothing is
    resting at a chosen level; the exits are identical to `simulate`'s).
    `entries_per_day` sets the random draw rate (a fair comparison needs
    roughly the rule's own trade frequency). Deterministic for a given
    `seed`. Pure given its inputs."""
    ordered = sorted(bars, key=lambda c: c.t)
    run = Run(params=params)
    equity = params.starting_equity
    peak = equity
    pos: Optional[_Position] = None
    rng = Random(seed)
    bars_per_day = max(1.0, DAY_MS / (params.bar_minutes * MINUTE_MS))
    p_entry = min(1.0, entries_per_day / bars_per_day)
    run.equity_curve.append((ordered[0].t if ordered else 0, equity))

    for i, bar in enumerate(ordered):
        t = bar.t
        closed = ordered[:i]
        was_flat = pos is None   # see simulate()'s comment: never re-enter inside the bar that just closed

        if pos is not None:
            pos.bars_open += 1
            is_long = pos.side == "long"
            stop_hit = (bar.l <= pos.stop_px) if is_long else (bar.h >= pos.stop_px)
            target_hit = (bar.h >= pos.target_px) if is_long else (bar.l <= pos.target_px)
            exit_reason, exit_px = None, None
            if stop_hit:
                exit_reason = "stop"
                sign = 1.0 if is_long else -1.0
                exit_px = pos.stop_px * (1.0 - sign * params.slippage)
            elif target_hit:
                exit_reason, exit_px = "target", pos.target_px
            elif params.time_stop_bars is not None and pos.bars_open >= params.time_stop_bars:
                exit_reason = "time"
                sign = 1.0 if is_long else -1.0
                exit_px = bar.c * (1.0 - sign * params.slippage)
            if exit_reason is not None:
                fee_rate = params.maker_fee if exit_reason == "target" else params.taker_fee
                trade = _close_trade(pos, exit_px, t, exit_reason, fee_rate, params)
                equity += trade.gross_pnl_usd - (trade.fees_usd - pos.entry_fee_usd)
                run.trades.append(trade)
                run.fees_total_usd += trade.fees_usd
                run.gross_pnl_usd += trade.gross_pnl_usd
                pos = None
            if pos is not None:
                liq = liq_estimate(pos.entry_px, params.leverage, params.max_leverage, is_long)
                breached = (bar.l <= liq) if is_long else (bar.h >= liq)
                if breached:
                    run.liquidated, run.liquidation_time = True, t
                    equity = 0.0
                    run.equity_curve.append((t, equity))
                    run.final_equity = equity
                    run.max_drawdown_pct = 1.0
                    return run

        if was_flat and rng.random() < p_entry:
            a = atr(closed, params.atr_period)
            if a is not None and a > 0:
                side = rng.choice(("long", "short"))
                entry = bar.o
                sign = 1.0 if side == "long" else -1.0
                stop = entry - sign * params.stop_atr_k * a
                risk = abs(entry - stop)
                target = entry + sign * params.min_r * risk
                lev_gate = check_leverage(entry, stop, params.leverage, params.max_leverage)
                if lev_gate["ok"]:
                    s_eq = slice_equity(equity, params)
                    size = risk_capped_size(s_eq, params, entry, stop)
                    if size > 0:
                        entry_fee = params.taker_fee * entry * size
                        equity -= entry_fee
                        pos = _Position(side=side, entry_time=t, entry_px=entry, size=size, stop_px=stop,
                                        target_px=target, risk_usd=risk * size, entry_fee_usd=entry_fee)
                        run.fees_total_usd += entry_fee

        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        run.max_drawdown_pct = max(run.max_drawdown_pct, dd)
        run.equity_curve.append((t, equity))

    run.final_equity = equity
    return run


# ── "the oil desk is flat" gate (built from the live sim's own trade list) ──

def oil_desk_flat_at(desk_trades: Sequence[Any], open_position_at_end: Optional[Dict[str, Any]] = None
                     ) -> ActiveAt:
    """`active_at` for "this coin's OWN desk is flat": False for any time
    strictly inside [entry_time, exit_time) of one of the desk's own trades
    (a `desk_tick_sim.Trade`-shaped sequence; duck-typed on `.entry_time` /
    `.exit_time`), and False from the last trade's exit (or the window end,
    with an `open_position_at_end`) onward if a position is still open at
    the end of the desk's own run. Models the Hyperliquid netting fact: a
    same-coin scalp can only ever run in the gaps between the desk's own
    position. Pure given the trade list."""
    intervals: List[Tuple[int, int]] = []
    for tr in desk_trades:
        if tr.exit_time is not None:
            intervals.append((tr.entry_time, tr.exit_time))
        else:
            intervals.append((tr.entry_time, float("inf")))
    if open_position_at_end is not None:
        intervals.append((open_position_at_end["entry_time"], float("inf")))

    def at(t: int) -> bool:
        return not any(lo <= t < hi for lo, hi in intervals)
    return at


__all__ = [
    "RULES", "Params", "Trade", "Run", "Direction", "DirectionAt", "ActiveAt",
    "true_ranges", "atr", "range_edges", "rolling_mean_std",
    "slice_equity", "full_size_notional", "risk_capped_size",
    "candidate_setup", "gated_setup", "simulate", "simulate_random_control", "oil_desk_flat_at",
    "MINUTE_MS", "HOUR_MS", "DAY_MS",
]
