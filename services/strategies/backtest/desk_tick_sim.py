"""Pure simulator for the desk-tick loop's mechanics (plan section 4).

    simulate(candles_15m, candles_1h, direction_at, params) -> Run

No I/O, no clock read. Every rule replays `.claude/skills/desk-tick/SKILL.md`
rows 2-11 (entry/stop/target/gate/leverage/size/top-up/exit/hygiene/one-slot)
on candles that already happened; row 1 (the thesis itself) is an input,
not something this module invents. It imports the live gates
(`check_leverage`, `check_levels`) from `pathiel.agents.bracket` rather than
re-implementing them, so the backtest and the live tool cannot drift apart.

Clock and lookahead
--------------------
One tick per 15m bar, oldest first. At tick `t` (a bar's OPEN time):
  - structural levels (8h/24h/48h range, ATR14) are built from 1h candles
    whose CLOSE time (`c.t + HOUR_MS`) is `<= t` — i.e. strictly already
    closed, mirroring `scripts/oil_levels.py`'s `closed_only`.
  - a resting order (entry or top-up add) can fill, or be cancelled by the
    hygiene rule, on bars AFTER the one it was placed on — never the same
    bar, so "fills on a later bar" (plan section 4) holds literally and no
    bar's own future high/low feeds the decision that placed the order.
  - the 1h invalidation-close check and hourly funding settle exactly once,
    on the tick that coincides with a 1h candle's close.
Shifting any bar strictly after `t` therefore cannot change what happens at
or before `t` — that is the no-lookahead property the gate tests check for
directly (test_desk_tick_sim.py::test_no_lookahead).

Entry, stop, target
--------------------
Flat + no resting order: if the mid (this bar's open) is within
`atr_rest_frac` ATR14 of the 8h range edge on the thesis side, rest a limit
there (long: 8h low; short: 8h high), capped so it is never worse than the
thesis's own entry when one is given (`Direction.entry_ceiling`: "never
chase above the thesis entry"). Stop sits `stop_buffer` dollars beyond the
edge; target is the next structure out, the 24h range edge on the far side.
Both must clear `check_leverage` (the 20x rule) and `check_levels`'s R gate
(`min_r`, default 1.5) or the setup is refused and counted by reason
(`Run.refused`); nothing is rested.

Exits
-----
Stop or target fires intrabar (stop: taker fee + `slippage`, since it is a
market order; target: maker fee, since the TP is a resting limit). If both
trigger in the same bar, the stop is assumed to have fired first (the
conservative read of one bar's price action). Independently, at each 1h
close, if the close is through `Direction.invalidation_px` (when the
direction series supplies one — the journaled theses do; the fixed and
daily-sign series rely on the stop alone, see `desk_tick_backtest.py`), the
position closes at that close price, taker fee. A direction series that
flips to the opposite side while a position is open closes it the same way
(`direction_flip`) — the one-slot rule means a reversed call cannot sit
next to the old position.

Liquidation
-----------
Checked every bar a position is open against Hyperliquid's isolated-margin
liquidation price (`pathiel.agents.bracket.liq_estimate`). A breach ends the
run immediately: equity is zeroed, `Run.liquidated` is True, and no later
bar is processed (the account is gone).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage, check_levels, liq_estimate
from pathiel.models.types import Candle

HOUR_MS = 3_600_000
FIFTEEN_MIN_MS = 900_000


# ── inputs ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Direction:
    """One direction series's answer at a given tick. `side` is the only
    required field — the three experiments differ in how much else they
    supply:
      - journaled theses: `invalidation_px` (parsed thesis text) and
        `entry_ceiling` (the thesis's own entry, never chase above it).
      - always-long/short and daily-sign: `side` only; stop/target/entry
        are entirely structural (no thesis to lean on), so the position's
        own stop is the only exit level besides target."""
    side: str                                   # "long" | "short"
    invalidation_px: Optional[float] = None
    entry_ceiling: Optional[float] = None


DirectionAt = Callable[[int], Optional[Direction]]


@dataclass(frozen=True)
class Params:
    stop_buffer: float = 0.50          # $ beyond the 8h edge. 0.25 until 2026-10-25; the first
                                       # 30-day run's sweep (0.10/0.25/0.50) found 0.50 the only
                                       # setting that turned the always-long control positive,
                                       # and the operator widened the live rule to match.
    slippage: float = 0.0003           # stop fill slippage, fraction of price (swept: 0.02%/0.05%)
    leverage: float = 20.0             # swept: 10x/20x
    max_leverage: float = 20.0         # venue max for xyz:BRENTOIL
    min_r: float = 1.5                 # the 1.5R gate (bracket.DEFAULT_MIN_R)
    taker_fee: float = 0.00045         # 4.5 bps
    maker_fee: float = 0.00015         # 1.5 bps
    starting_equity: float = 64.0
    free_margin_floor: float = 0.10    # place_bracket's 10% free-margin floor
    atr_rest_frac: float = 0.25        # rest an entry within this many ATR of the edge
    atr_cancel_frac: float = 1.0       # cancel a resting entry beyond this many ATR from mid


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
    exit_reason: Optional[str] = None            # stop|target|invalidation|direction_flip|liquidation
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0                         # entry + exit fee, for reporting
    exit_fee_usd: float = 0.0                     # just the exit leg — the only fee not yet applied to equity
    funding_usd: float = 0.0
    net_pnl_usd: float = 0.0
    risk_usd: float = 0.0                         # |entry-stop| * initial size, at open
    r_multiple: Optional[float] = None
    added: bool = False                           # a top-up filled during this trade


@dataclass
class Run:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)
    final_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    liquidated: bool = False
    liquidation_time: Optional[int] = None
    refused: Counter = field(default_factory=Counter)   # reason -> count
    rested: int = 0
    filled: int = 0
    cancelled_resting: int = 0
    fees_total_usd: float = 0.0
    funding_total_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    params: Optional[Params] = None
    open_position_at_end: Optional[Dict[str, Any]] = None


# ── internal state ───────────────────────────────────────────────────────

@dataclass
class _Resting:
    side: str
    limit_px: float
    stop_px: float
    target_px: float
    placed_at: int
    is_add: bool = False


@dataclass
class _Position:
    side: str
    size: float
    avg_entry: float
    stop_px: float
    target_px: float
    opened_at: int
    entry_fees_usd: float = 0.0
    funding_usd: float = 0.0
    risk_usd: float = 0.0
    added: bool = False


def _levels(closed_1h: Sequence[Candle]) -> Optional[Dict[str, Any]]:
    """8h/24h range + ATR14 from 1h candles already closed. None until at
    least 15 bars exist (ATR14 needs n+1)."""
    if len(closed_1h) < 15:
        return None
    window = closed_1h[-15:]
    trs = []
    for i in range(1, len(window)):
        h, lo, prev_c = window[i].h, window[i].l, window[i - 1].c
        trs.append(max(h - lo, abs(h - prev_c), abs(lo - prev_c)))
    atr14 = sum(trs) / 14.0
    r8 = closed_1h[-8:]
    r24 = closed_1h[-24:] if len(closed_1h) >= 24 else None
    return {
        "atr14": atr14,
        "range_8h_low": min(c.l for c in r8), "range_8h_high": max(c.h for c in r8),
        "range_24h_low": min(c.l for c in r24) if r24 else None,
        "range_24h_high": max(c.h for c in r24) if r24 else None,
    }


def _full_size_notional(equity: float, params: Params) -> float:
    """90% of equity as margin, times leverage — the full-port cap
    `place_bracket add=True` tops a position up to."""
    margin = equity * (1.0 - params.free_margin_floor)
    return margin * params.leverage


def _open_position(resting: _Resting, fill_px: float, t: int, params: Params) -> _Position:
    risk_usd = abs(fill_px - resting.stop_px)
    return _Position(side=resting.side, size=0.0, avg_entry=fill_px, stop_px=resting.stop_px,
                     target_px=resting.target_px, opened_at=t, risk_usd=risk_usd)


def _close_trade(pos: _Position, exit_px: float, t: int, reason: str, fee_rate: float,
                 initial_size: float) -> Trade:
    """Build the closed trade's report row. Does NOT touch equity — the
    entry fee, every top-up fee and every funding payment were already
    applied to `equity` incrementally as they happened; only the exit fee
    and the realized price move are still outstanding, which the caller
    applies as `equity += trade.gross_pnl_usd - trade.exit_fee_usd`."""
    side_sign = 1.0 if pos.side == "long" else -1.0
    gross = side_sign * (exit_px - pos.avg_entry) * pos.size
    exit_fee = fee_rate * exit_px * pos.size
    fees = pos.entry_fees_usd + exit_fee
    net = gross - fees - pos.funding_usd
    risk = pos.risk_usd * initial_size
    return Trade(side=pos.side, entry_time=pos.opened_at, entry_px=pos.avg_entry, size=pos.size,
                stop_px=pos.stop_px, target_px=pos.target_px, exit_time=t, exit_px=exit_px,
                exit_reason=reason, gross_pnl_usd=gross, fees_usd=fees, exit_fee_usd=exit_fee,
                funding_usd=pos.funding_usd, net_pnl_usd=net, risk_usd=risk,
                r_multiple=(net / risk) if risk > 1e-12 else None, added=pos.added)


def _funding_rate_at(funding_sorted: Sequence[Tuple[int, float]], t: int) -> Optional[float]:
    """The funding rate whose settlement time is exactly `t` (hourly rows),
    else None. `funding_sorted` is a list of (time_ms, rate) pairs, sorted."""
    lo, hi = 0, len(funding_sorted)
    while lo < hi:
        mid = (lo + hi) // 2
        if funding_sorted[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(funding_sorted) and funding_sorted[lo][0] == t:
        return funding_sorted[lo][1]
    return None


def simulate(candles_15m: Sequence[Candle], candles_1h: Sequence[Candle], direction_at: DirectionAt,
            params: Optional[Params] = None, funding_history: Optional[Sequence[Dict[str, Any]]] = None) -> Run:
    params = params or Params()
    c15 = sorted(candles_15m, key=lambda c: c.t)
    c1h = sorted(candles_1h, key=lambda c: c.t)
    funding_sorted: List[Tuple[int, float]] = sorted(
        (int(r["time"]), float(r["fundingRate"])) for r in (funding_history or [])
        if r.get("time") is not None and r.get("fundingRate") is not None
    )

    run = Run(params=params)
    equity = params.starting_equity
    peak_equity = equity
    resting: Optional[_Resting] = None
    resting_add: Optional[_Resting] = None
    position: Optional[_Position] = None
    initial_size = 0.0          # the size the position opened with, for R-multiple risk
    seen_1h_close: set = set()

    for bar in c15:
        if run.liquidated:
            break
        t = bar.t
        mid = bar.o
        closed_1h = [c for c in c1h if c.t + HOUR_MS <= t]
        lv = _levels(closed_1h)

        # 1. hourly funding settlement + invalidation, exactly on a 1h close
        close_time = t
        matching_1h = [c for c in c1h if c.t + HOUR_MS == close_time and close_time not in seen_1h_close]
        for c1 in matching_1h:
            seen_1h_close.add(close_time)
            if position is not None:
                rate = _funding_rate_at(funding_sorted, close_time)
                if rate:
                    notional = position.size * c1.c
                    side_sign = 1.0 if position.side == "long" else -1.0
                    pay = side_sign * rate * notional
                    equity -= pay
                    position.funding_usd += pay
                    run.funding_total_usd += pay
            d = direction_at(close_time)
            if position is not None and d is not None and d.invalidation_px is not None:
                breached = (c1.c <= d.invalidation_px) if position.side == "long" else (c1.c >= d.invalidation_px)
                if breached:
                    trade = _close_trade(position, c1.c, close_time, "invalidation", params.taker_fee, initial_size)
                    equity += trade.gross_pnl_usd - trade.exit_fee_usd
                    run.trades.append(trade)
                    run.gross_pnl_usd += trade.gross_pnl_usd
                    run.fees_total_usd += trade.fees_usd
                    position = None
                    resting_add = None

        # 2. a flipped direction closes an open position (one slot, reversed call)
        if position is not None:
            d = direction_at(t)
            if d is not None and d.side != position.side:
                trade = _close_trade(position, mid, t, "direction_flip", params.taker_fee, initial_size)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None
                resting_add = None

        # 3. resting entry: fill or hygiene-cancel (never on the bar it was placed)
        if resting is not None and resting.placed_at < t:
            is_long = resting.side == "long"
            filled = bar.l <= resting.limit_px if is_long else bar.h >= resting.limit_px
            if filled:
                run.filled += 1
                position = _open_position(resting, resting.limit_px, t, params)
                notional = _full_size_notional(equity, params)
                size = notional / resting.limit_px if resting.limit_px > 0 else 0.0
                position.size = size
                initial_size = size
                entry_fee = params.maker_fee * resting.limit_px * size
                position.entry_fees_usd = entry_fee
                equity -= entry_fee
                resting = None
            elif lv is not None and lv["atr14"]:
                edge = lv["range_8h_low"] if is_long else lv["range_8h_high"]
                near_edge = abs(mid - edge) <= params.atr_rest_frac * lv["atr14"]
                too_far = abs(mid - resting.limit_px) > params.atr_cancel_frac * lv["atr14"]
                if too_far and not near_edge:
                    run.cancelled_resting += 1
                    resting = None

        # 4. in position: stop/target fill, top-up, liquidation
        if position is not None and position.opened_at < t:
            is_long = position.side == "long"
            stop_hit = bar.l <= position.stop_px if is_long else bar.h >= position.stop_px
            target_hit = bar.h >= position.target_px if is_long else bar.l <= position.target_px
            if stop_hit:
                side_sign = 1.0 if is_long else -1.0
                exit_px = position.stop_px * (1.0 - side_sign * params.slippage)
                trade = _close_trade(position, exit_px, t, "stop", params.taker_fee, initial_size)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None
                resting_add = None
            elif target_hit:
                trade = _close_trade(position, position.target_px, t, "target", params.maker_fee, initial_size)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None
                resting_add = None
            else:
                # liquidation check (every bar, before top-up logic)
                liq_px = liq_estimate(position.avg_entry, params.leverage, params.max_leverage, is_long)
                liquidated_now = bar.l <= liq_px if is_long else bar.h >= liq_px
                if liquidated_now:
                    run.liquidated = True
                    run.liquidation_time = t
                    trade = _close_trade(position, liq_px, t, "liquidation", params.taker_fee, initial_size)
                    run.trades.append(trade)
                    run.gross_pnl_usd += trade.gross_pnl_usd
                    run.fees_total_usd += trade.fees_usd
                    equity = 0.0
                    position = None
                    resting_add = None
                else:
                    # top-up: one resting add at the original entry, while under full size
                    full = _full_size_notional(equity, params) / position.avg_entry
                    if resting_add is None and position.size < full - 1e-9:
                        resting_add = _Resting(side=position.side, limit_px=position.avg_entry,
                                               stop_px=position.stop_px, target_px=position.target_px,
                                               placed_at=t, is_add=True)
                    elif resting_add is not None and resting_add.placed_at < t:
                        filled_add = (bar.l <= resting_add.limit_px if is_long
                                      else bar.h >= resting_add.limit_px)
                        if filled_add:
                            add_size = max(0.0, full - position.size)
                            if add_size > 1e-9:
                                add_fee = params.maker_fee * resting_add.limit_px * add_size
                                equity -= add_fee
                                position.entry_fees_usd += add_fee
                                total = position.size + add_size
                                position.avg_entry = (position.avg_entry * position.size
                                                      + resting_add.limit_px * add_size) / total
                                position.size = total
                                position.added = True
                            resting_add = None

        # 5. new entry evaluation when flat
        if position is None and resting is None:
            d = direction_at(t)
            if d is not None and lv is not None and lv["atr14"] and lv["range_24h_low"] is not None:
                is_long = d.side == "long"
                edge = lv["range_8h_low"] if is_long else lv["range_8h_high"]
                if d.entry_ceiling is not None:
                    edge = min(edge, d.entry_ceiling) if is_long else max(edge, d.entry_ceiling)
                if abs(mid - edge) <= params.atr_rest_frac * lv["atr14"]:
                    stop_px = edge - params.stop_buffer if is_long else edge + params.stop_buffer
                    target_px = lv["range_24h_high"] if is_long else lv["range_24h_low"]
                    lev_gate = check_leverage(edge, stop_px, params.leverage, params.max_leverage)
                    r_gate = check_levels(edge, stop_px, target_px, params.min_r)
                    if not lev_gate["ok"]:
                        run.refused["leverage"] += 1
                    elif not r_gate["ok"]:
                        run.refused["min_r"] += 1
                    else:
                        resting = _Resting(side=d.side, limit_px=edge, stop_px=stop_px,
                                          target_px=target_px, placed_at=t)
                        run.rested += 1

        # Safety net: the dedicated liq-price check (step 4) can only fire
        # while a position is open and only catches a stop-distance breach;
        # an invalidation or direction-flip close (steps 1-2) realizes its
        # exit at that bar's CLOSE/open price regardless of how far price
        # gapped through it, so a violent enough single bar could in
        # principle realize a loss past what the account actually has. Any
        # path that leaves realized equity <= 0 is liquidation, full stop.
        if equity <= 0.0 and not run.liquidated:
            run.liquidated = True
            run.liquidation_time = t
            equity = 0.0
            position = None
            resting = None
            resting_add = None

        unrealized = 0.0
        if position is not None:
            side_sign = 1.0 if position.side == "long" else -1.0
            unrealized = side_sign * (mid - position.avg_entry) * position.size
        mark = equity + unrealized
        peak_equity = max(peak_equity, mark)
        if peak_equity > 0:
            run.max_drawdown_pct = max(run.max_drawdown_pct, (peak_equity - mark) / peak_equity)
        run.equity_curve.append((t, mark))

    # A position still open when the window ends is NOT force-closed into a
    # trade row (that would fabricate a fill the live loop never took) — it
    # stays marked-to-market in `equity_curve`'s last point, which is what
    # `final_equity` reports, and is surfaced separately so the eval can
    # show it without it skewing trade count / win rate / expectancy.
    if position is not None and not run.liquidated:
        side_sign = 1.0 if position.side == "long" else -1.0
        last_px = c15[-1].c if c15 else position.avg_entry
        run.open_position_at_end = {
            "side": position.side, "size": position.size, "entry_px": position.avg_entry,
            "mark_px": last_px, "unrealized_pnl_usd": side_sign * (last_px - position.avg_entry) * position.size,
        }

    run.final_equity = run.equity_curve[-1][1] if run.equity_curve else equity
    return run


__all__ = ["Direction", "DirectionAt", "Params", "Trade", "Run", "simulate"]
