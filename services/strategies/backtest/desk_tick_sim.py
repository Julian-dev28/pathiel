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
One tick per 15m bar (`Params.tick_minutes` 15) or per 1h bar (60, the
hourly loop since 2026-10-25), oldest first. At tick `t` (a bar's OPEN time):
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

Target execution (2026-10-28)
-----------------------------
`Params.target_mode`: "touch" (the default, unchanged: the target fills on
a bare touch at the target price, maker fee, no slippage, i.e. an ideal
resting limit), "market" (the live order: Hyperliquid "Take Profit Market",
so a touch triggers a market sell, taker fee plus exit slippage) or "limit"
(a resting reduce-only limit at the target that fills only when a bar
trades THROUGH it by `tick_size`, maker fee, no slippage; a bare touch does
not fill, and a touch that reverses to the stop takes the stop).
`Params.size_slippage` (default off) prices every market exit (stops,
market targets, invalidation and direction-flip closes) off
`EXIT_SLIP_CURVE`, the per-side slip measured on the live Brent book, so a
big position's exit costs more than a small one's.

Moving the stop (2026-10-28)
----------------------------
`Params.stop_mode` "fixed" (the live rule: the stop never moves),
"breakeven" or "trail". Both arm once a bar after the fill has traded
`stop_trigger_r` R in the position's favour (R = the per-unit risk at the
fill). "breakeven" then moves the stop to the entry plus the round-trip fee
(maker in, taker out), so a stop-out there nets about zero. "trail" moves it
to the best tick-bar close since the fill minus `trail_atr` x ATR14 (closed
1h bars), re-evaluated every bar. The stop only ever tightens. The decision
reads the bar that just finished and applies from the next bar (no
lookahead). A moved stop that the next bar opens through fills at that
open, not at the stop price: a stop above the market triggers at once.
Fixed stops keep the sim's original fill at the stop price.

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
    # Which call this answer belongs to (a journaled thesis's created_at).
    # `Params.reentry_after_target=False` blocks a new entry under the same
    # call after a target filled; a series with no calls (the controls) is
    # treated as one call per UTC day, the desk's daily-rewrite floor.
    regime_id: Optional[str] = None


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
    free_margin_floor: float = 0.0     # place_bracket's reserve (10% until 2026-10-26, 2% until 10-27)
    atr_rest_frac: float = 0.25        # rest an entry within this many ATR of the edge
    atr_cancel_frac: float = 1.0       # cancel a resting entry beyond this many ATR from mid
    tick_minutes: int = 15             # one decision per 15m bar (the loop until 2026-10-25) or per
                                       # 1h bar (60: the hourly loop the operator switched to that day)
    stop_mode: str = "fixed"           # fixed | breakeven | trail (see "Moving the stop")
    stop_trigger_r: float = 1.0        # R in favour before a breakeven/trail stop arms
    trail_atr: float = 1.0             # trail distance in ATR14 (stop_mode "trail")
    target_mode: str = "touch"         # touch | market | limit (see "Target execution")
    tick_size: float = 0.01            # xyz:BRENTOIL price step; a limit target needs a trade-through
    size_slippage: bool = False        # price market exits off EXIT_SLIP_CURVE by notional
    # Closer targets (2026-10-29, operator: "test .3% lower targets and then
    # reentry"): the take-profit sits this fraction of price inside the
    # structural target (a long's target x (1 - f), a short's x (1 + f)).
    target_offset_frac: float = 0.0
    # After a target fills, may the desk re-enter under the SAME call? True
    # is what the sim has always done (the next tick evaluates a new entry);
    # False waits for the next call (see Direction.regime_id).
    reentry_after_target: bool = True
    # ── strategy variants (2026-10-30 strategy sweep, operator: "expand on
    # various High ROI strategies ... think the most profit PER DAY, THEN PER
    # WEEK"). Every default keeps the behaviour above unchanged.
    target_r: Optional[float] = None        # fixed-R target (entry +/- target_r x risk) instead of the far range edge
    partial_frac: float = 0.0               # scale out this share of the opening size at partial_r ...
    partial_r: float = 1.0                  # ... as a resting limit (fills on a trade-through, maker fee)
    partial_breakeven: bool = False         # after the partial fills, stop the rest at entry + round-trip fees
    time_stop_hours: Optional[float] = None  # close at market after this long in the trade
    entry_mode: str = "edge"                # edge: rest a limit at the range edge | market: take the mid now
    entry_lookback_h: int = 8               # closed 1h bars behind the entry edge and the stop
    target_lookback_h: int = 24             # closed 1h bars behind the far-edge target
    session_utc: Optional[Tuple[int, int]] = None  # new entries only when start <= UTC hour < end


# Per-side market-sell slippage (bps) by notional (USD), walked on the live
# xyz:BRENTOIL book 2026-10-28 ~15:30Z (l2Book nSigFigs=5 for the top $2.2M,
# nSigFigs=4 for the next 175 bps, $7.9M resting). Linear between points, the
# last slope extended past $7M.
EXIT_SLIP_CURVE: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.9), (500_000.0, 2.8), (1_000_000.0, 3.8), (2_000_000.0, 7.5), (3_000_000.0, 12.9),
    (4_000_000.0, 16.8), (5_000_000.0, 21.7), (6_000_000.0, 26.2), (7_000_000.0, 34.1))


def exit_slip_bps(notional_usd: float) -> float:
    """Per-side slippage in bps for a market exit of `notional_usd`."""
    n = max(0.0, float(notional_usd))
    pts = EXIT_SLIP_CURVE
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if n <= x1:
            return y0 + (y1 - y0) * (n - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    return y1 + (y1 - y0) / (x1 - x0) * (n - x1)


def _market_exit_slip(params: "Params", notional_usd: float) -> float:
    """Fractional slip for a market exit: the flat `slippage`, or, with
    size_slippage on, the larger of that and the book curve at this size."""
    if not params.size_slippage:
        return params.slippage
    return max(params.slippage, exit_slip_bps(notional_usd) / 1e4)


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
    orig_stop_px: Optional[float] = None          # the stop at the fill, before any move
    stop_moved: bool = False                      # a breakeven/trail move tightened the stop
    target_missed: bool = False                   # limit target touched, never traded through, closed elsewhere
    orig_target_px: Optional[float] = None        # the structural target before any offset
    offset_capture: bool = False                  # filled the offset target; never traded through the original


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
    orig_target_px: Optional[float] = None


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
    orig_stop_px: float = 0.0
    best_close: Optional[float] = None
    armed: bool = False
    stop_moved: bool = False
    target_touched: bool = False                  # a bar touched the target without filling it (limit mode)
    orig_target_px: Optional[float] = None
    reached_orig_target: bool = False             # a bar traded through the structural target
    partial_done: bool = False                    # the scale-out (Params.partial_frac) already filled
    partial_gross_usd: float = 0.0                # realized P&L of the scale-out, already in equity


def _levels(closed_1h: Sequence[Candle], entry_h: int = 8, target_h: int = 24) -> Optional[Dict[str, Any]]:
    """8h/24h range + ATR14 from 1h candles already closed. None until at
    least 15 bars exist (ATR14 needs n+1). `entry_*` / `target_*` are the
    same ranges over `entry_h` / `target_h` bars (Params.entry_lookback_h,
    target_lookback_h; 8 and 24 by default, so they equal the 8h/24h keys);
    a target range is None until that many bars exist."""
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
    re_ = closed_1h[-entry_h:]
    rt = closed_1h[-target_h:] if len(closed_1h) >= target_h else None
    return {
        "atr14": atr14,
        "range_8h_low": min(c.l for c in r8), "range_8h_high": max(c.h for c in r8),
        "range_24h_low": min(c.l for c in r24) if r24 else None,
        "range_24h_high": max(c.h for c in r24) if r24 else None,
        "entry_low": min(c.l for c in re_), "entry_high": max(c.h for c in re_),
        "target_low": min(c.l for c in rt) if rt else None,
        "target_high": max(c.h for c in rt) if rt else None,
    }


def _full_size_notional(equity: float, params: Params) -> float:
    """Equity less the floor, as margin net of the opening taker fee, times
    leverage: the full-port size place_bracket sizes and tops up to
    (services.risk.derive.margin_after_open_fee)."""
    usable = equity * (1.0 - params.free_margin_floor)
    margin = usable / (1.0 + params.leverage * params.taker_fee)
    return margin * params.leverage


def _open_position(resting: _Resting, fill_px: float, t: int, params: Params) -> _Position:
    risk_usd = abs(fill_px - resting.stop_px)
    return _Position(side=resting.side, size=0.0, avg_entry=fill_px, stop_px=resting.stop_px,
                     target_px=resting.target_px, opened_at=t, risk_usd=risk_usd,
                     orig_stop_px=resting.stop_px,
                     orig_target_px=resting.orig_target_px if resting.orig_target_px is not None
                     else resting.target_px)


# The live desk's rules. 2026-10-30 (operator adopted the scenario sweep's
# best combination): 0.5% target offset, 0.75 stop buffer, limit target, fixed
# stop, 20x, re-entry on. 2026-10-30 later ("switch it", the strategy sweep's
# winner): market entry, 24h entry range, 2R gate. Params() keeps the older
# defaults the existing evals and tests pin; compare a variant against
# live_params(). docs/strategy/OIL_STRATEGY.md has both rule sets.
def live_params() -> "Params":
    return Params(stop_buffer=0.75, target_offset_frac=0.005, target_mode="limit", leverage=20.0,
                  entry_mode="market", entry_lookback_h=24, min_r=2.0)


def offset_target(target_px: float, side: str, frac: float) -> float:
    """The take-profit `frac` of price inside the structural target: closer
    to the entry, so a move that only approaches the target still fills."""
    if frac <= 0:
        return target_px
    return target_px * (1.0 - frac) if side == "long" else target_px * (1.0 + frac)


def regime_key(d: Optional["Direction"], t: int) -> Optional[Tuple[str, Any]]:
    """The call a tick belongs to: the direction's own regime_id, else its UTC day."""
    if d is None:
        return None
    if d.regime_id is not None:
        return ("call", d.regime_id)
    return ("day", t // 86_400_000)


def _manage_stop(pos: _Position, bar: Candle, atr14: Optional[float], params: Params) -> None:
    """Tighten the stop after `bar` closed, per `params.stop_mode`. Never
    widens. Pure on its inputs; mutates `pos`."""
    if params.stop_mode == "fixed" or pos.risk_usd <= 0:
        return
    is_long = pos.side == "long"
    sign = 1.0 if is_long else -1.0
    pos.best_close = bar.c if pos.best_close is None else (
        max(pos.best_close, bar.c) if is_long else min(pos.best_close, bar.c))
    if not pos.armed:
        excursion = (bar.h - pos.avg_entry) if is_long else (pos.avg_entry - bar.l)
        if excursion + 1e-12 >= params.stop_trigger_r * pos.risk_usd:
            pos.armed = True
    if not pos.armed:
        return
    if params.stop_mode == "breakeven":
        fees = params.maker_fee + params.taker_fee
        new_stop = pos.avg_entry * (1.0 + sign * fees)
    elif params.stop_mode == "trail":
        if not atr14:
            return
        new_stop = pos.best_close - sign * params.trail_atr * atr14
    else:
        raise ValueError(f"unknown stop_mode {params.stop_mode!r}")
    tighter = new_stop > pos.stop_px + 1e-12 if is_long else new_stop < pos.stop_px - 1e-12
    if tighter:
        pos.stop_px = new_stop
        pos.stop_moved = True


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
    # A scale-out (Params.partial_frac) was realized into equity when it
    # filled: it counts in the trade's net and R, not in `gross_pnl_usd`
    # (which stays "the move still to apply", the caller's equity update).
    net = gross + pos.partial_gross_usd - fees - pos.funding_usd
    risk = pos.risk_usd * initial_size
    return Trade(side=pos.side, entry_time=pos.opened_at, entry_px=pos.avg_entry, size=pos.size,
                stop_px=pos.stop_px, target_px=pos.target_px, exit_time=t, exit_px=exit_px,
                exit_reason=reason, gross_pnl_usd=gross, fees_usd=fees, exit_fee_usd=exit_fee,
                funding_usd=pos.funding_usd, net_pnl_usd=net, risk_usd=risk,
                r_multiple=(net / risk) if risk > 1e-12 else None, added=pos.added,
                orig_stop_px=pos.orig_stop_px, stop_moved=pos.stop_moved,
                target_missed=pos.target_touched and reason != "target")


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


def _in_session(t: int, session: Tuple[int, int]) -> bool:
    """Is `t`'s UTC hour inside [start, end)? A window that wraps midnight
    (start > end) is the hours outside [end, start)."""
    hr = (t // HOUR_MS) % 24
    start, end = session
    return start <= hr < end if start <= end else (hr >= start or hr < end)


def _setup(d: Direction, mid: float, lv: Dict[str, Any], params: Params) -> Optional[Dict[str, Any]]:
    """The one setup a flat tick may take, or None (price not at the edge),
    or {"refused": reason}. Edge mode (the live rule): a limit at the entry
    range edge, capped at the thesis entry, only when the mid is within
    `atr_rest_frac` ATR of it. Market mode: the mid itself, refused above a
    long thesis's entry (below a short's: "never chase"), stop beyond the
    entry range (or beyond the mid if price already broke it). Target: the
    far range edge, `target_offset_frac` inside it, or `target_r` x risk."""
    is_long = d.side == "long"
    edge = lv["entry_low"] if is_long else lv["entry_high"]
    if params.entry_mode == "market":
        if d.entry_ceiling is not None and (mid > d.entry_ceiling if is_long else mid < d.entry_ceiling):
            return {"refused": "chase"}
        entry = mid
        struct = min(edge, mid) if is_long else max(edge, mid)
    else:
        if d.entry_ceiling is not None:
            edge = min(edge, d.entry_ceiling) if is_long else max(edge, d.entry_ceiling)
        if abs(mid - edge) > params.atr_rest_frac * lv["atr14"]:
            return None
        entry = struct = edge
    stop_px = struct - params.stop_buffer if is_long else struct + params.stop_buffer
    structural_target = lv["target_high"] if is_long else lv["target_low"]
    if params.target_r is not None:
        risk = abs(entry - stop_px)
        target_px = entry + params.target_r * risk if is_long else entry - params.target_r * risk
        structural_target = target_px
    else:
        target_px = offset_target(structural_target, d.side, params.target_offset_frac)
    if not check_leverage(entry, stop_px, params.leverage, params.max_leverage)["ok"]:
        return {"refused": "leverage"}
    if not check_levels(entry, stop_px, target_px, params.min_r)["ok"]:
        return {"refused": "min_r"}
    return {"side": d.side, "entry_px": entry, "stop_px": stop_px, "target_px": target_px,
            "orig_target_px": structural_target}


def _market_open(setup: Dict[str, Any], bar: Candle, t: int, equity: float,
                 params: Params) -> Tuple[_Position, float, float]:
    """Open at the mid as a taker, size-aware slippage: (position, size, equity after the fee)."""
    sign = 1.0 if setup["side"] == "long" else -1.0
    notional = _full_size_notional(equity, params)
    fill = setup["entry_px"] * (1.0 + sign * _market_exit_slip(params, notional))
    rest = _Resting(side=setup["side"], limit_px=fill, stop_px=setup["stop_px"],
                    target_px=setup["target_px"], placed_at=t, orig_target_px=setup["orig_target_px"])
    pos = _open_position(rest, fill, t, params)
    pos.size = notional / fill if fill > 0 else 0.0
    fee = params.taker_fee * fill * pos.size
    pos.entry_fees_usd = fee
    return pos, pos.size, equity - fee


def _scale_out(pos: _Position, bar: Candle, initial_size: float, equity: float, run: Run,
               params: Params) -> float:
    """Fill the resting scale-out limit (`partial_frac` of the opening size
    at `partial_r` R) when `bar` trades through it, and realize it into
    equity at once (maker fee). With `partial_breakeven` the rest's stop
    then goes to entry + round-trip fees, from the next bar. Returns equity."""
    is_long = pos.side == "long"
    px = pos.avg_entry + params.partial_r * pos.risk_usd if is_long else pos.avg_entry - params.partial_r * pos.risk_usd
    if (px >= pos.target_px) if is_long else (px <= pos.target_px):
        pos.partial_done = True          # the scale-out sits at or past the target: the target does the job
        return equity
    through = bar.h >= px + params.tick_size - 1e-9 if is_long else bar.l <= px - params.tick_size + 1e-9
    if not through:
        return equity
    size = min(pos.size, initial_size * params.partial_frac)
    sign = 1.0 if is_long else -1.0
    gross = sign * (px - pos.avg_entry) * size
    fee = params.maker_fee * px * size
    pos.size -= size
    pos.partial_gross_usd += gross
    pos.entry_fees_usd += fee            # counted in the trade's fees at close
    pos.partial_done = True
    run.gross_pnl_usd += gross
    if params.partial_breakeven:
        be = pos.avg_entry * (1.0 + sign * (params.maker_fee + params.taker_fee))
        if (be > pos.stop_px) if is_long else (be < pos.stop_px):
            pos.stop_px = be
            pos.stop_moved = True
    return equity + gross - fee


def simulate(candles_15m: Sequence[Candle], candles_1h: Sequence[Candle], direction_at: DirectionAt,
            params: Optional[Params] = None, funding_history: Optional[Sequence[Dict[str, Any]]] = None) -> Run:
    params = params or Params()
    if params.target_mode not in ("touch", "market", "limit"):
        raise ValueError(f"unknown target_mode {params.target_mode!r}")
    if params.entry_mode not in ("edge", "market"):
        raise ValueError(f"unknown entry_mode {params.entry_mode!r}")
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
    blocked_regime: Optional[Tuple[str, Any]] = None   # no re-entry under this call (reentry_after_target=False)
    seen_1h_close: set = set()

    # The tick series: one decision per 15m bar, or per 1h bar for the hourly
    # loop (2026-10-25). With 1h ticks every tick lands on a 1h close, so the
    # invalidation check and funding settle on every tick; stop and target
    # fills read the 1h bar's high/low, the same conservative intrabar rule.
    ticks = c1h if params.tick_minutes >= 60 else c15
    # 1h bars by close time, and a pointer past the bars already closed: the
    # same "closed strictly before t" set as a full scan, without the O(n)
    # rescan every tick.
    by_close = {c.t + HOUR_MS: c for c in c1h}
    need = max(15, params.entry_lookback_h, params.target_lookback_h)
    n_closed = 0

    for bar in ticks:
        if run.liquidated:
            break
        t = bar.t
        mid = bar.o
        trades_before_bar = len(run.trades)
        while n_closed < len(c1h) and c1h[n_closed].t + HOUR_MS <= t:
            n_closed += 1
        lv = _levels(c1h[max(0, n_closed - need):n_closed], params.entry_lookback_h, params.target_lookback_h)

        # 1. hourly funding settlement + invalidation, exactly on a 1h close
        close_time = t
        c_close = by_close.get(close_time)
        matching_1h = [c_close] if c_close is not None and close_time not in seen_1h_close else []
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
                    inv_px = c1.c
                    if params.size_slippage:
                        sgn = 1.0 if position.side == "long" else -1.0
                        inv_px = c1.c * (1.0 - sgn * _market_exit_slip(params, position.size * c1.c))
                    trade = _close_trade(position, inv_px, close_time, "invalidation", params.taker_fee, initial_size)
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
                flip_px = mid
                if params.size_slippage:
                    sgn = 1.0 if position.side == "long" else -1.0
                    flip_px = mid * (1.0 - sgn * _market_exit_slip(params, position.size * mid))
                trade = _close_trade(position, flip_px, t, "direction_flip", params.taker_fee, initial_size)
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
                edge = lv["entry_low"] if is_long else lv["entry_high"]
                near_edge = abs(mid - edge) <= params.atr_rest_frac * lv["atr14"]
                too_far = abs(mid - resting.limit_px) > params.atr_cancel_frac * lv["atr14"]
                if too_far and not near_edge:
                    run.cancelled_resting += 1
                    resting = None

        # 4. in position: stop/target fill, top-up, liquidation
        if position is not None and position.opened_at < t:
            is_long = position.side == "long"
            if position.orig_target_px is not None and (
                    bar.h >= position.orig_target_px + params.tick_size - 1e-9 if is_long
                    else bar.l <= position.orig_target_px - params.tick_size + 1e-9):
                position.reached_orig_target = True
            stop_hit = bar.l <= position.stop_px if is_long else bar.h >= position.stop_px
            target_touch = bar.h >= position.target_px if is_long else bar.l <= position.target_px
            if params.target_mode == "limit":
                # a resting limit fills only when price trades THROUGH it
                through = (bar.h >= position.target_px + params.tick_size - 1e-9 if is_long
                           else bar.l <= position.target_px - params.tick_size + 1e-9)
                target_hit = through
                if target_touch and not through:
                    position.target_touched = True
            else:
                target_hit = target_touch
            if not stop_hit and params.partial_frac > 0 and not position.partial_done:
                equity = _scale_out(position, bar, initial_size, equity, run, params)
            if stop_hit:
                side_sign = 1.0 if is_long else -1.0
                trigger_px = position.stop_px
                if position.stop_moved:
                    # a moved stop the bar opened through fills at the open
                    trigger_px = min(trigger_px, bar.o) if is_long else max(trigger_px, bar.o)
                exit_px = trigger_px * (1.0 - side_sign * _market_exit_slip(params, position.size * trigger_px))
                trade = _close_trade(position, exit_px, t, "stop", params.taker_fee, initial_size)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None
                resting_add = None
            elif target_hit:
                if params.target_mode == "market":
                    # Take Profit Market: the touch triggers a market sell
                    side_sign = 1.0 if is_long else -1.0
                    tgt_px = position.target_px * (1.0 - side_sign * _market_exit_slip(
                        params, position.size * position.target_px))
                    trade = _close_trade(position, tgt_px, t, "target", params.taker_fee, initial_size)
                else:
                    trade = _close_trade(position, position.target_px, t, "target", params.maker_fee, initial_size)
                trade.orig_target_px = position.orig_target_px
                trade.offset_capture = (params.target_offset_frac > 0 and not position.reached_orig_target)
                if not params.reentry_after_target:
                    blocked_regime = regime_key(direction_at(t), t)
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
                elif params.time_stop_hours and t - position.opened_at >= params.time_stop_hours * HOUR_MS:
                    side_sign = 1.0 if is_long else -1.0
                    time_px = mid * (1.0 - side_sign * _market_exit_slip(params, position.size * mid))
                    trade = _close_trade(position, time_px, t, "time", params.taker_fee, initial_size)
                    equity += trade.gross_pnl_usd - trade.exit_fee_usd
                    run.trades.append(trade)
                    run.gross_pnl_usd += trade.gross_pnl_usd
                    run.fees_total_usd += trade.fees_usd
                    position = None
                    resting_add = None
                else:
                    # top-up: one resting add at the original entry, while under
                    # full size; never after a scale-out (that would undo it)
                    full = _full_size_notional(equity, params) / position.avg_entry
                    if resting_add is None and position.size < full - 1e-9 and not position.partial_done:
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
                    _manage_stop(position, bar, lv["atr14"] if lv else None, params)

        # 5. new entry evaluation when flat
        if position is None and resting is None:
            d = direction_at(t)
            if blocked_regime is not None and regime_key(d, t) == blocked_regime:
                d = None            # a target filled under this call: wait for the next one
            if d is not None and params.session_utc is not None and not _in_session(t, params.session_utc):
                d = None
            if d is not None and lv is not None and lv["atr14"] and lv["target_low"] is not None:
                setup = _setup(d, mid, lv, params)
                if setup is None:
                    pass
                elif setup.get("refused"):
                    run.refused[setup["refused"]] += 1
                elif params.entry_mode == "market" and len(run.trades) > trades_before_bar:
                    pass            # a trade closed inside this bar: its open is in the past now
                elif params.entry_mode == "market":
                    run.filled += 1
                    position, initial_size, equity = _market_open(setup, bar, t, equity, params)
                    # conservative: the entry bar's own range can stop it out
                    is_long = position.side == "long"
                    if (bar.l <= position.stop_px) if is_long else (bar.h >= position.stop_px):
                        side_sign = 1.0 if is_long else -1.0
                        exit_px = position.stop_px * (1.0 - side_sign * _market_exit_slip(
                            params, position.size * position.stop_px))
                        trade = _close_trade(position, exit_px, t, "stop", params.taker_fee, initial_size)
                        equity += trade.gross_pnl_usd - trade.exit_fee_usd
                        run.trades.append(trade)
                        run.gross_pnl_usd += trade.gross_pnl_usd
                        run.fees_total_usd += trade.fees_usd
                        position = None
                else:
                    resting = _Resting(side=d.side, limit_px=setup["entry_px"], stop_px=setup["stop_px"],
                                       target_px=setup["target_px"], placed_at=t,
                                       orig_target_px=setup["orig_target_px"])
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
        last_px = ticks[-1].c if ticks else position.avg_entry
        run.open_position_at_end = {
            "side": position.side, "size": position.size, "entry_px": position.avg_entry,
            "mark_px": last_px, "unrealized_pnl_usd": side_sign * (last_px - position.avg_entry) * position.size,
        }

    run.final_equity = run.equity_curve[-1][1] if run.equity_curve else equity
    return run


__all__ = ["Direction", "DirectionAt", "Params", "Trade", "Run", "simulate", "offset_target", "regime_key"]
