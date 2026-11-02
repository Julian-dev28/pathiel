"""Several desks on one clock, each sized off its share of the whole account.

    simulate_portfolio(desks, starting_equity) -> PortfolioRun

Built on `desk_tick_sim.simulate_steps` (2026-11-02, DESK_SPLIT_TEST_2026-
11-02.md, operator: "what will be our success if we split the portfolio
50/50 oil and silver?"). The per-desk rules are the sim's, untouched; this
module only decides how big each desk's next trade is.

Live sizing (scripts/pathiel-mcp-server.py `_desk_slice`): a trade on a
desk's coin sizes off `desk_equity_share[desk] x account equity`, read new
at every place_bracket, and account equity is the account value, so the
other desk's open P&L counts. Here:

  - Every desk's sim starts at the WHOLE starting equity plus a cushion
    (`CUSHION_X`, two or more desks only), so its own `equity` is start +
    cushion + its own P&L. A desk may lose more than the start while the
    other desk carries the account; the cushion keeps the sim's own
    "equity <= 0 is liquidation" net from firing on that.
  - At each sizing point (entry, flip, resting fill, top-up check) the sim
    asks the hook, which answers
        share x max(0, own equity now - cushion + sum over the other
                       desks of (their mark - start - cushion))
    i.e. share x the account's marked equity. The other desks' marks are
    the ones from the end of the previous clock tick (a snapshot taken
    before any desk steps), so desk order on a shared tick does not matter.
  - The account's mark on each clock tick is the first desk's mark plus
    the other desks' (mark - start - cushion). With one desk at share 1.0
    there is no cushion, the account mark is that desk's mark, and the
    hook returns 1.0 x (own - 0.0 + 0), the desk's own equity exactly, so
    a 100/0 portfolio IS the single-desk sim, byte for byte
    (test_portfolio.py).
  - With two or more desks the account is ruined when its mark reaches 0:
    from then on it marks 0 and every hook sizes at 0.

The clock is the union of every desk's tick times; a desk steps only on
its own bars. A desk with share 0 is not run at all (live: no desk, no
trades). Shares must each sit in [0, 1] and add up to 1.

Known simplification: the top-up's full-size check reads the desk's own
REALIZED equity plus the others' marks (the sim does not mark its own open
position into sizing); live would also count its own open P&L. Top-ups are
rare and the gap is that one position's open P&L.

A desk liquidated inside its sim (price through the isolated-margin
liquidation price) ends that desk's run, as the sim does. Alone, the sim's
zero is the account's zero. With other desks, the desk's P&L is the sum of
its trades' nets, the liquidation trade included (the move to the
liquidation price: that position's margin), and the other desks go on.
The eval reports `liquidated` per desk. In practice it does not happen:
the 20x gate keeps every stop inside the liquidation price and a bar's
stop is judged first, so this is a guard, not a path the data takes.

Correlation guard (2026-11-02, PULLBACK_AND_CORRELATION_TEST_2026-11-02.md,
operator: "YES" to testing a guard across desks). `simulate_portfolio(...,
guard=CorrGuard(...))` hands each guarded desk the sim's `entry_guard`: at a
new entry on `side`, if any OTHER desk held a position at the end of the
previous clock tick whose side conflicts (`pair` "same": that desk holds
`side` too; "opposite": it holds the other side), the entry's size is
multiplied by `mult` (0 blocks it, 0.5 halves it); otherwise 1.0. `desks`
names the desks it applies to (empty: every desk). Read off the previous
tick's snapshot, like the marks, so desk order on a shared tick does not
matter; an entry on the same tick as the other desk's entry does not see
it. `drivers` (B3, optional): per desk, a callable t -> the set of driver
tags of the call in force; the guard then acts only when the two desks'
tags overlap. None (the default) builds no hook at all, so every existing
result is byte-identical.

Pure: no I/O, no clock read.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest.desk_tick_sim import (
    DirectionAt, Params, Run, simulate_steps, tick_bars)

DAY_MS = 86_400_000
SHARE_TOLERANCE = 1e-9
# With two or more desks, each desk's sim starts at start x (1 + CUSHION_X):
# its own equity is then never near 0 however much it loses while the other
# desk carries the account, so the sim's "equity <= 0 is liquidation" net
# only ever fires on a real liquidation. The cushion is taken out of every
# sizing call and every P&L, and 1e4 x leaves about 1e-10 of a dollar of
# float precision. One desk alone runs with no cushion (the account IS the
# desk), which is what keeps 100/0 identical to the single-desk sim.
CUSHION_X = 1e4


@dataclass(frozen=True)
class Desk:
    """One desk: its share of the account, its bars, its calls and its rules."""
    name: str
    share: float
    candles_15m: Sequence[Candle]
    candles_1h: Sequence[Candle]
    direction_at: DirectionAt
    params: Params
    funding: Optional[Sequence[Dict[str, Any]]] = None


GUARD_PAIRS = ("same", "opposite")


@dataclass(frozen=True)
class CorrGuard:
    """The correlation guard across desks (module docstring)."""
    pair: str                               # "same" | "opposite"
    mult: float = 0.0                       # 0 blocks, 0.5 halves, 1 does nothing
    desks: Tuple[str, ...] = ()             # desks it applies to; () = every desk
    drivers: Optional[Mapping[str, Callable[[int], FrozenSet[str]]]] = None   # B3: only on a shared driver tag


def guard_mult(guard: CorrGuard, name: str, side: str, t: int, held: Mapping[str, Optional[str]]) -> float:
    """The size multiplier for desk `name`'s new `side` entry at `t`, given
    every desk's held side (None: flat). Pure."""
    if guard.desks and name not in guard.desks:
        return 1.0
    for other, h in held.items():
        if other == name or h is None:
            continue
        clash = (h == side) if guard.pair == "same" else (h != side)
        if not clash:
            continue
        if guard.drivers is not None:
            mine, theirs = guard.drivers.get(name), guard.drivers.get(other)
            if mine is None or theirs is None or not (mine(t) & theirs(t)):
                continue
        return guard.mult
    return 1.0


@dataclass
class PortfolioRun:
    starting_equity: float
    shares: Dict[str, float]
    runs: Dict[str, Run] = field(default_factory=dict)                    # each active desk's own Run
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)   # account mark per clock tick
    desk_pnl: Dict[str, List[Tuple[int, float]]] = field(default_factory=dict)   # each desk's P&L to date
    final_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    ruined: bool = False                     # the account's mark reached 0 (two or more desks)
    guard_log: List[Dict[str, Any]] = field(default_factory=list)   # every entry the guard blocked or scaled


def validate_shares(shares: Mapping[str, float]) -> None:
    """Raise ValueError unless every share is in [0, 1] and they add up to 1."""
    if not shares:
        raise ValueError("no desks")
    for name, s in shares.items():
        if not (0.0 <= float(s) <= 1.0):
            raise ValueError(f"share for {name!r} is {s}, outside [0, 1]")
    total = sum(float(s) for s in shares.values())
    if abs(total - 1.0) > SHARE_TOLERANCE:
        raise ValueError(f"shares add up to {total}, not 1")


def account_equity(own: float, cushion: float, others: Mapping[str, float], base: float) -> float:
    """The account's equity seen from one desk: its own equity less the
    cushion it was started with, plus every other desk's P&L (their mark
    less `base`, the start + cushion each desk began at). With no other
    desks and no cushion it is `own` exactly (`own - 0.0` and an empty sum
    of int 0 both leave a float unchanged)."""
    return (own - cushion) + sum(m - base for m in others.values())


def simulate_portfolio(desks: Sequence[Desk], starting_equity: float,
                       guard: Optional[CorrGuard] = None) -> PortfolioRun:
    validate_shares({d.name: d.share for d in desks})
    if guard is not None:
        if guard.pair not in GUARD_PAIRS:
            raise ValueError(f"unknown guard pair {guard.pair!r}")
        if not (0.0 <= guard.mult <= 1.0):
            raise ValueError(f"guard mult {guard.mult} must be in [0, 1]")
    names = [d.name for d in desks]
    if len(set(names)) != len(names):
        raise ValueError(f"desk names must be unique: {names}")
    start = float(starting_equity)
    out = PortfolioRun(starting_equity=start, shares={d.name: d.share for d in desks})
    active = [d for d in desks if d.share > 0]
    multi = len(active) > 1
    cushion = CUSHION_X * start if multi else 0.0
    base = start + cushion

    snapshot: Dict[str, float] = {d.name: base for d in active}   # marks at the end of the last clock tick
    marks: Dict[str, float] = dict(snapshot)
    held_snap: Dict[str, Optional[str]] = {d.name: None for d in active}   # held sides, same snapshot
    held: Dict[str, Optional[str]] = dict(held_snap)

    def hook_for(name: str, share: float):
        def size_equity(t: int, own: float) -> float:
            if out.ruined:
                return 0.0
            others = {k: v for k, v in snapshot.items() if k != name}
            return share * max(account_equity(own, cushion, others, base), 0.0)
        return size_equity

    def guard_for(name: str):
        if guard is None:
            return None

        def entry_guard(t: int, side: str) -> float:
            m = guard_mult(guard, name, side, t, held_snap)
            if m != 1.0:
                out.guard_log.append({"time": t, "desk": name, "side": side, "mult": m,
                                      "held": {k: v for k, v in held_snap.items() if k != name and v}})
            return m
        return entry_guard

    gens, times, pos, done = {}, {}, {}, {}
    for d in active:
        p = dataclasses.replace(d.params, starting_equity=base)
        gens[d.name] = simulate_steps(d.candles_15m, d.candles_1h, d.direction_at, p, d.funding,
                                      hook_for(d.name, d.share), guard_for(d.name))
        times[d.name] = [b.t for b in tick_bars(d.candles_15m, d.candles_1h, p)]
        pos[d.name] = 0
        done[d.name] = False
        out.desk_pnl[d.name] = []

    clock = sorted({t for ts in times.values() for t in ts})
    peak = start
    for t in clock:
        for d in active:
            n = d.name
            if done[n] or pos[n] >= len(times[n]) or times[n][pos[n]] != t:
                continue
            pos[n] += 1
            try:
                _, marks[n], run = next(gens[n])
            except StopIteration as stop:      # liquidated last tick: the sim ends early
                out.runs[n] = stop.value
                done[n] = True
                held[n] = None
                continue
            held[n] = run.position_side
            if multi and run.liquidated:
                # The sim zeroes a liquidated desk's equity (its whole
                # account). Here the desk loses what its trades lost: the
                # liquidation trade's net is the move to the liquidation
                # price, i.e. that position's isolated margin.
                marks[n] = base + sum(tr.net_pnl_usd for tr in run.trades)
        snapshot.update(marks)
        held_snap.update(held)
        if not active:
            continue
        if multi:
            total = start + sum(marks[d.name] - base for d in active)
        else:
            total = marks[active[0].name]
        if multi and total <= 0.0 and not out.ruined:
            out.ruined = True                  # the account is gone; sizes go to 0 from here
        if out.ruined:
            total = 0.0
        out.equity_curve.append((t, total))
        for d in active:
            out.desk_pnl[d.name].append((t, marks[d.name] - base))
        peak = max(peak, total)
        if peak > 0:
            out.max_drawdown_pct = max(out.max_drawdown_pct, (peak - total) / peak)

    for d in active:
        if not done[d.name]:
            try:
                while True:
                    next(gens[d.name])
            except StopIteration as stop:
                out.runs[d.name] = stop.value
    out.final_equity = out.equity_curve[-1][1] if out.equity_curve else start
    return out


# ── stats (pure) ──────────────────────────────────────────────────────────

def daily_last(curve: Sequence[Tuple[int, float]]) -> List[Tuple[int, float]]:
    """The last value of each UTC day, oldest first (day index, value)."""
    by_day: Dict[int, float] = {}
    for t, v in curve:
        by_day[t // DAY_MS] = v
    return sorted(by_day.items())


def daily_changes(curve: Sequence[Tuple[int, float]], first_prev: float) -> List[Tuple[int, float, float]]:
    """(day, change, value at the previous day's end) for each UTC day;
    the first day's previous value is `first_prev`."""
    out = []
    prev = first_prev
    for day, v in daily_last(curve):
        out.append((day, v - prev, prev))
        prev = v
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson correlation, None under 3 points or with a flat series."""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-18 or syy <= 1e-18:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def desk_correlation(prun: PortfolioRun) -> Optional[float]:
    """Correlation of the first two active desks' daily P&L (None with
    fewer than two desks, or one that never moved)."""
    names = list(prun.desk_pnl)
    if len(names) < 2:
        return None
    a = {d: c for d, c, _ in daily_changes(prun.desk_pnl[names[0]], 0.0)}
    b = {d: c for d, c, _ in daily_changes(prun.desk_pnl[names[1]], 0.0)}
    days = sorted(set(a) | set(b))
    return pearson([a.get(d, 0.0) for d in days], [b.get(d, 0.0) for d in days])


def portfolio_stats(prun: PortfolioRun) -> Dict[str, Any]:
    """Final equity, return, max drawdown, worst day (% of the previous
    day's close), closed trades, win rate, the desks' daily P&L
    correlation, and per-desk P&L and trades."""
    trades = [tr for r in prun.runs.values() for tr in r.trades]
    wins = sum(1 for tr in trades if tr.net_pnl_usd > 0)
    days = daily_changes(prun.equity_curve, prun.starting_equity)
    worst = min((c / p for _, c, p in days if p > 0), default=0.0)
    corr = desk_correlation(prun)
    start = prun.starting_equity
    return {
        "final_equity": round(prun.final_equity, 2),
        "return_pct": round(100.0 * (prun.final_equity / start - 1.0), 1) if start else None,
        "max_dd_pct": round(100.0 * prun.max_drawdown_pct, 1),
        "worst_day_pct": round(100.0 * min(worst, 0.0), 1),
        "trades": len(trades),
        "topped_up": sum(1 for tr in trades if tr.added),
        "win_rate": round(wins / len(trades), 3) if trades else None,
        "corr": round(corr, 3) if corr is not None else None,
        "desk_pnl": {n: round(c[-1][1], 2) if c else 0.0 for n, c in prun.desk_pnl.items()},
        "desk_trades": {n: len(r.trades) for n, r in prun.runs.items()},
        "liquidated": sorted(n for n, r in prun.runs.items() if r.liquidated),
        "ruined": prun.ruined,
        "open_at_end": sorted(n for n, r in prun.runs.items() if r.open_position_at_end),
    }


__all__ = ["Desk", "PortfolioRun", "CorrGuard", "GUARD_PAIRS", "guard_mult", "validate_shares", "account_equity", "simulate_portfolio", "daily_last",
           "daily_changes", "pearson", "desk_correlation", "portfolio_stats"]
