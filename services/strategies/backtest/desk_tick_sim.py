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

Profit lock (2026-11-01)
------------------------
`Params.lock_trigger_r` (None = off, the live rule). Once a bar's best
price (a long's high, a short's low) since the fill reaches `lock_trigger_r`
R in the position's favour (R = the per-unit risk at the fill), the stop
moves to the entry plus `lock_r` R (a long; the entry minus `lock_r` R for a
short). `lock_trail` instead keeps the stop `lock_trigger_r - lock_r` R
behind the best price from then on, never below the lock level. The stop
only ever tightens; the target does not move. A stop the lock set exits as
"lock". Intrabar order is unknown, so: the bar's stop and target are judged
first, against the stop as it stood when the bar opened; the lock is set
from that bar's high (low) after; and it fills in the SAME bar only when
`lock_confirm_close` is on and the bar's close is through the new stop
(price touched the trigger and then finished below the lock, so it crossed
the lock after the trigger). Otherwise it applies from the next bar, where
a bar that opens through it fills at the open. A market entry's own bar
counts (the fill is the open, so the bar's high came after it).
`Params.lock_trigger_target_frac` (2026-11-02) arms on the share of the
way to the target instead (0.5 = half way from entry to target), in place
of `lock_trigger_r`; `lock_plus_fees` adds the round trip at the taker
rate (in and out) to the lock level, so "break-even" nets about zero.

Daily stop-out cap (2026-11-02)
-------------------------------
`Params.daily_stop_cap` (None = off, the live rule). After that many
"stop" exits whose exit time falls in one UTC day, the sim takes no NEW
entry (and no flip) for the rest of that UTC day; the count starts over at
00:00 UTC. Only exit_reason "stop" counts: a target, lock, invalidation,
direction flip or time exit does not. `cap_per_side` counts and blocks per
side (N short stops block only shorts that day). An open position is never
touched: the cap only ever skips a setup that would otherwise have opened.
Each skipped setup is kept in `Run.cap_blocked` (time, side, entry, stop,
target) so an eval can walk it on and price what the cap cost or saved.
A stop on an entry bar (a market fill whose own bar hits the stop) counts
from the next tick.

`Params.stop_buffer_atr` (None = off) sets the entry stop's buffer per
tick to that many x the tick's ATR14, the live non-Brent rule (1.3 x
ATR14), instead of the fixed `stop_buffer`. Added with the cap so the
2026-11-02 silver desk's tighter stops can be replayed.

Stop buffer floor (2026-11-02)
------------------------------
`Params.stop_buffer_floor` (dollars) and `stop_buffer_floor_frac` (a
fraction of the entry price), both None = off. They apply only with
`stop_buffer_atr` on (the live non-Brent rule; Brent's fixed 0.75 never
reads them, the same way `next_trade.stop_buffer_for` returns 0.75 before
any ATR math): the buffer becomes max(stop_buffer_atr x ATR14, floor,
floor_frac x entry). A floor only ever widens a stop, never narrows one.
The widened stop goes through the same 20x and min-R gates; a tick where
the floored stop fails a gate that the bare ATR stop would have passed is
counted in `Run.floor_blocked` (by gate) and kept in
`Run.floor_blocked_rows`, so an eval can say how often the floor cost an
entry. `Run.floor_widened` counts entries opened with a widened stop.
`Params.fixed_buffer_floor_frac` (None = off) is the same floor for the
fixed-dollar buffer (Brent's 0.75, `stop_buffer_atr` None): the buffer
becomes max(stop_buffer, fixed_buffer_floor_frac x entry), with the same
floor_blocked / floor_widened bookkeeping against the bare fixed stop.
(2026-11-02, BRENT_STOP_BUFFER_TEST_2026-11-02.md.)

Weekend window (2026-11-02)
---------------------------
`weekend_closed(t)`: True from Friday 16:00 to Sunday 18:00 New York time,
the oil futures close-to-reopen. zoneinfo does the daylight saving, so it
is Fri 21:00 to Sun 23:00 UTC in US winter time and Fri 20:00 to Sun 22:00
UTC in US summer time. The xyz HIP-3 perps trade straight through it.
`Params.weekend_off` (None = off, the live rule): "hold" takes no NEW entry
(and no flip) on a tick inside the window, open positions keep their stop
and target; "flatten" also closes an open position at market (taker fee,
exit slippage, exit_reason "weekend") on the first tick inside the window
and cancels any resting order. `Params.lock_weekend_only` (default False):
the profit lock below only folds in bars and moves the stop on ticks inside
the window; on weekdays the live fixed stop rules run unchanged. A stop the
lock tightened over the weekend stays where it is after the reopen (a stop
never widens).

Risk-capped size (2026-11-02)
-----------------------------
`Params.risk_cap_frac` (None = off, the live full-port size). Every entry,
top-up and flip sizes at `risk_capped_notional`: the smaller of the
full-port notional and the notional whose stop-out (price to the stop,
the taker fee in and out, the flat `slippage`) costs `risk_cap_frac` of
equity. The position keeps its leverage setting, so the liquidation price
and the 20x gate are unchanged; only the size shrinks. A wide stop sizes
down, a tight one stays at full port. (LOSS_AUDIT_2026-11-02.md.)

Entry filters (2026-11-02)
--------------------------
All off by default; each only ever skips a new entry (never a flip, never
an open position). `Params.entry_delay_min`: none in the first N minutes
of a call (a journaled thesis's created_at, `call_start`; a control series
has no rewrites, so its side's start). `Params.side_age_h`: none until the
side in force has held N hours (same-side rewrites do not reset it; a flat
call between two same-side calls does not either). `Params.confirm_1h`:
none unless the last closed 1h candle agrees (a long's closed above its
open, a short's below). `Params.no_break_entry`: in market mode, none once
the mid is already past the entry range's own edge (a long under the 24h
low, a short over the 24h high: the 2026-11-02 Brent long bought 110.00
under a 110.14 low). Refusals are counted in `Run.refused` ("delay",
"side_age", "confirm_1h", "break"). (LOSS_AUDIT_2026-11-02.md.)

Regime filter and range-scaled levels (2026-11-02)
--------------------------------------------------
`Params.regime_filter` (None = off, the live rule) skips a NEW entry by
the causal regime label (`services.strategies.backtest.regime`): "no_chop"
none on a chop label, "with_trend" none against a trend label, "trend_only"
both, "veto_20d" none against the 20-day trend unless the 48h run is a
straight line the entry's way. The labels are built once per run from the
1h candles passed in (`label_series`, thresholds `regime_cfg`) and read at
the UTC day's open (`regime_refresh` "day") or at the last 1h close
("hour"); both use only bars closed by then. An open position is never
touched. Refusals: "regime_chop", "regime_against", "regime_veto".
`Params.range_buffer_k` (None = off) sets the fixed stop buffer from the
24h range at the tick: max(stop_buffer, k x range), or k x range alone with
`range_buffer_floor` False. `Params.target_offset_range_frac` (None = off)
puts the target that share of the 24h range inside the far edge, in place
of `target_offset_frac` x price. (OIL_REGIME_TEST_2026-11-02.md.)

Portfolio hook (2026-11-02)
---------------------------
`simulate_steps` is `simulate` one tick at a time (it yields the tick
time, the marked equity and the Run after each tick), and both take
`size_equity(t, own_equity) -> equity to size off`, called at every
sizing point: a market entry or flip, a resting entry's fill and the
top-up's full-size check. None (the default) sizes off the desk's own
equity, so every existing result is byte-identical.
`services.strategies.backtest.portfolio` runs several desks on one clock
with it, each sized off its share of the whole account, as live
(DESK_SPLIT_TEST_2026-11-02.md).

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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from pathiel.agents.bracket import check_leverage, check_levels, liq_estimate
from pathiel.models.types import Candle
from services.strategies.backtest.regime import FILTERS as REGIME_FILTERS
from services.strategies.backtest.regime import RegimeConfig, RegimeSeries, label_series, regime_blocks

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
    # AI-informed variants (2026-10-30 replay build, Params.ai_levels /
    # conviction_leverage): the thesis's own stop and first target, and its
    # confidence. None on every direction series that doesn't set them
    # (always-long/short, daily-sign, and journaled theses before this
    # field existed) -- ai_levels/conviction_leverage then have nothing to
    # act on and the sim behaves exactly as before these fields existed.
    stop_px: Optional[float] = None
    target_px: Optional[float] = None
    confidence: Optional[float] = None


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
    # Flip after a target (2026-10-30, operator: "upon long close short down to
    # long entry"): once a target fills, the next tick opens the OPPOSITE side
    # at market, its target the closed trade's entry, its stop `flip_stop_buffer`
    # beyond the far range extreme. One attempt per target; a flip never flips
    # again; the thesis's invalidation and a direction flip do not close it
    # (it trades against the call on purpose); its own stop, target, time stop
    # and liquidation do. Off by default.
    flip_after_target: bool = False
    flip_min_r: Optional[float] = None       # R gate for the flip (default: min_r)
    flip_leverage: Optional[float] = None    # leverage for the flip (default: leverage)
    flip_stop_buffer: Optional[float] = None  # default: stop_buffer
    flip_time_stop_hours: Optional[float] = None
    # ── AI-informed variants (2026-10-30, operator: "more AI informed, AI
    # powered strategy that is more profitable" -- services/thesis/evals/
    # replay.py's 30-day replay is what makes there be enough theses to test
    # these against). Both off by default; off keeps every existing result
    # byte-identical (see test_ai_variants.py's golden check).
    ai_levels: bool = False               # try the thesis's own entry/stop/first target before the structural rule
    conviction_leverage: Optional[Tuple[float, float, float]] = None   # (threshold, high_lev, low_lev); floored at 15x
    # ── profit lock (2026-11-01, operator after a Brent long reached +1.9R and
    # closed +0.4R: "we were up like 35$ and we round tripped" ... "yeah
    # backtest it"). See "Profit lock" above. Off (None) keeps every
    # existing result byte-identical (test_profit_lock.py's golden check).
    lock_trigger_r: Optional[float] = None  # arm once the best price is this many R in favour
    lock_r: float = 0.0                     # then stop to entry + lock_r R (0 = break-even, no fees)
    lock_trail: bool = False                # after arming, trail (lock_trigger_r - lock_r) R behind the best price
    lock_confirm_close: bool = True         # same-bar fill at a new lock only when the bar closes through it
    # ── daily stop-out cap (2026-11-02, operator after the silver desk shorted
    # three times into a rising market: stopped at 01:05Z and 05:30Z, then a
    # third short at 05:35Z). See "Daily stop-out cap" above. Off (None)
    # keeps every existing result byte-identical (test_stop_cap.py).
    daily_stop_cap: Optional[int] = None    # after this many stop exits in one UTC day, no new entries that day
    cap_per_side: bool = False              # count and block per side instead of per desk
    # The live non-Brent stop rule, per tick (2026-11-02): the stop sits this
    # many x the tick's own 1h ATR14 beyond the entry structure
    # (next_trade.stop_buffer_for: 1.3 x ATR14), instead of the fixed
    # `stop_buffer` dollars. None (the default) keeps `stop_buffer`, and every
    # existing result byte-identical. A flip's stop is unchanged by it.
    stop_buffer_atr: Optional[float] = None
    # Stop buffer floor (2026-11-02, operator "yes" after the stop-cap test
    # found the live per-tick ATR stop half the width the silver 30-day test
    # ran): with stop_buffer_atr on, the buffer is max(stop_buffer_atr x
    # ATR14, stop_buffer_floor, stop_buffer_floor_frac x entry). Both None
    # (the default) keep every existing result byte-identical. Ignored when
    # stop_buffer_atr is None (Brent's fixed buffer). A flip is unchanged.
    stop_buffer_floor: Optional[float] = None       # dollars
    stop_buffer_floor_frac: Optional[float] = None  # fraction of the entry price (0.006 = 0.6%)
    # ── 2026-11-02 Brent stop buffer / weekend test (BRENT_STOP_BUFFER_TEST_
    # 2026-11-02.md). Every default keeps every existing result byte-identical.
    # A floor under the FIXED buffer: max(stop_buffer, frac x entry), read only
    # with stop_buffer_atr None (the ATR rule has its own floor fields above).
    fixed_buffer_floor_frac: Optional[float] = None
    weekend_off: Optional[str] = None        # None | "hold" (no new entries) | "flatten" (and close at the window open)
    lock_weekend_only: bool = False          # the profit lock acts only inside weekend_closed()
    lock_trigger_target_frac: Optional[float] = None  # arm at this share of the way to the target (not with lock_trigger_r)
    lock_plus_fees: bool = False             # lock level + the taker round trip, so break-even nets about zero
    # ── 2026-11-02 loss audit (LOSS_AUDIT_2026-11-02.md; see "Risk-capped
    # size" and "Entry filters" above). Every default keeps every existing
    # result byte-identical (test_loss_fix.py's golden check).
    risk_cap_frac: Optional[float] = None    # size so a stop at the fill loses at most this share of equity
    entry_delay_min: Optional[float] = None  # no new entry in the first N minutes of a call
    side_age_h: Optional[float] = None       # no new entry until the call's side has held N hours
    confirm_1h: bool = False                 # the last closed 1h candle must agree with the side
    no_break_entry: bool = False             # no market entry once the mid is past the entry range's own edge
    # ── 2026-11-02 oil regime test (OIL_REGIME_TEST_2026-11-02.md; see
    # "Regime filter and range-scaled levels" above). Every default keeps
    # every existing result byte-identical (test_oil_regime_sim.py).
    regime_filter: Optional[str] = None      # None | no_chop | with_trend | trend_only | veto_20d (regime.FILTERS)
    regime_cfg: Optional[RegimeConfig] = None  # the labeller's thresholds (None: RegimeConfig())
    regime_refresh: str = "day"              # "day": the label at 00:00Z | "hour": at the last 1h close
    range_buffer_k: Optional[float] = None   # stop buffer from the 24h range: max(stop_buffer, k x range) ...
    range_buffer_floor: bool = True          # ... or k x range alone when False
    target_offset_range_frac: Optional[float] = None  # target this share of the 24h range inside the far edge


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
    exit_reason: Optional[str] = None            # stop|lock|target|invalidation|direction_flip|time|liquidation
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
    is_flip: bool = False                         # opened by flip_after_target, against the call


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
    # Setups the daily stop cap skipped (Params.daily_stop_cap), oldest first:
    # {"time", "kind" ("entry"|"flip"), "side", "entry_px", "stop_px",
    #  "target_px", "is_market", "stops_today"}. Empty when the cap is off.
    cap_blocked: List[Dict[str, Any]] = field(default_factory=list)
    # Stop buffer floor (Params.stop_buffer_floor / _frac). floor_blocked:
    # gate ("leverage"|"min_r") -> ticks the floored stop failed that gate
    # while the bare ATR stop passed both. floor_blocked_rows: those ticks,
    # oldest first ({"time", "side", "gate", "entry_px", "stop_px",
    # "raw_stop_px", "target_px"}). floor_widened: entries opened with a stop
    # the floor widened. All empty / 0 when the floor is off.
    floor_blocked: Counter = field(default_factory=Counter)
    floor_blocked_rows: List[Dict[str, Any]] = field(default_factory=list)
    floor_widened: int = 0


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
    # The leverage this entry will size and gate at (Params.conviction_
    # leverage). None (every resting_add top-up, and every resting entry
    # before this variant existed) means params.leverage, via
    # _full_size_notional's own leverage-or-params.leverage fallback.
    leverage: Optional[float] = None


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
    is_flip: bool = False                         # opened by flip_after_target
    leverage: Optional[float] = None              # this position's leverage when it differs from Params.leverage
    lock_best: Optional[float] = None             # best high (long) / low (short) since the fill, profit lock
    locked: bool = False                          # the profit lock has tightened the stop


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


def _full_size_notional(equity: float, params: Params, leverage: Optional[float] = None) -> float:
    """Equity less the floor, as margin net of the opening taker fee, times
    leverage: the full-port size place_bracket sizes and tops up to
    (services.risk.derive.margin_after_open_fee)."""
    lev = leverage or params.leverage
    usable = equity * (1.0 - params.free_margin_floor)
    margin = usable / (1.0 + lev * params.taker_fee)
    return margin * lev


def risk_capped_notional(equity: float, params: Params, leverage: Optional[float], entry: float,
                         stop: float) -> float:
    """The notional a new entry sizes at: the full-port size, or with
    `risk_cap_frac` the smaller of that and the size whose stop-out (price
    to the stop, the taker fee both ways, the flat stop slippage) costs
    `risk_cap_frac` of equity. The leverage stays what the position is set
    to, so the liquidation price does not move; only the size shrinks. Pure."""
    full = _full_size_notional(equity, params, leverage)
    if params.risk_cap_frac is None or entry <= 0:
        return full
    per_dollar = abs(entry - stop) / entry + 2.0 * params.taker_fee + params.slippage
    if per_dollar <= 0:
        return full
    return min(full, params.risk_cap_frac * equity / per_dollar)


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


# The operator's floor (2026-10-30): a conviction-sized trade's leverage
# never drops below this, regardless of what Params.conviction_leverage's
# low_lev is configured to.
CONVICTION_LEVERAGE_FLOOR = 15.0


def trade_leverage(d: Optional[Direction], params: Params) -> float:
    """Per-trade leverage for a NEW entry (variant B, Params.conviction_
    leverage = (threshold, high_lev, low_lev)): high_lev when the
    direction's thesis confidence is >= threshold, else low_lev, both
    floored at CONVICTION_LEVERAGE_FLOOR. Off (conviction_leverage is None)
    or no confidence to read (a control direction series, e.g.
    always_direction, or a journaled thesis predating this field):
    params.leverage, unchanged from before this variant existed."""
    if params.conviction_leverage is None:
        return params.leverage
    threshold, high_lev, low_lev = params.conviction_leverage
    if d is not None and d.confidence is not None:
        lev = high_lev if d.confidence >= threshold else low_lev
    else:
        lev = params.leverage
    return max(lev, CONVICTION_LEVERAGE_FLOOR)


def _ai_setup(d: Direction, mid: float, params: Params, leverage: float) -> Optional[Dict[str, Any]]:
    """Variant A (Params.ai_levels): try the thesis's own entry
    (`entry_ceiling`), stop (`stop_px`) and first target (`target_px`)
    before the structural rule. None when the thesis doesn't carry all
    three -- nothing to try, the caller falls back to `_setup`. Market
    entry when the mid already pays the thesis's price or better (<=, a
    long; >=, a short -- so it can never chase past the thesis entry, by
    construction, same spirit as `Direction.entry_ceiling` elsewhere);
    otherwise a resting limit AT the thesis entry. Gated by the same
    check_leverage/check_levels the structural rule uses, at the trade's
    own leverage (Params.conviction_leverage, when set): a thesis level
    that fails either gate returns {"refused": ...}, same shape as
    `_setup`'s refusal, and the caller falls back to the structural rule
    rather than treating it as a hard stop. The thesis target gets the
    same `target_offset_frac` discount the structural rule applies
    (place_bracket's own convention: the take-profit sits a touch inside
    the called level, so a move that only approaches it still fills)."""
    entry_px = d.entry_ceiling
    if entry_px is None or d.stop_px is None or d.target_px is None:
        return None
    is_long = d.side == "long"
    is_market = (mid <= entry_px) if is_long else (mid >= entry_px)
    entry = mid if is_market else entry_px
    target_px = offset_target(d.target_px, d.side, params.target_offset_frac)
    if not check_leverage(entry, d.stop_px, leverage, params.max_leverage)["ok"]:
        return {"refused": "ai_leverage"}
    if not check_levels(entry, d.stop_px, target_px, params.min_r)["ok"]:
        return {"refused": "ai_min_r"}
    return {"side": d.side, "entry_px": entry, "stop_px": d.stop_px, "target_px": target_px,
            "orig_target_px": d.target_px, "is_market": is_market}


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


def lock_on(params: Params) -> bool:
    """Is any profit lock configured (an R trigger or a target-share trigger)?"""
    return params.lock_trigger_r is not None or params.lock_trigger_target_frac is not None


def profit_lock_stop(side: str, entry: float, risk: float, best: float,
                     params: Params, target: Optional[float] = None) -> Optional[float]:
    """The stop the profit lock asks for, or None when it is not armed:
    `best` (the best price since the fill) has not yet reached
    `lock_trigger_r` x `risk` in favour of `entry` (or, with
    `lock_trigger_target_frac`, that share of the way from `entry` to
    `target`; None without a target). Pure."""
    if not lock_on(params) or risk <= 0:
        return None
    sign = 1.0 if side == "long" else -1.0
    if params.lock_trigger_target_frac is not None:
        if target is None:
            return None
        need = params.lock_trigger_target_frac * abs(target - entry)
    else:
        need = params.lock_trigger_r * risk
    if sign * (best - entry) + 1e-12 < need:
        return None
    stop = entry + sign * params.lock_r * risk
    if params.lock_plus_fees:
        stop += sign * 2.0 * params.taker_fee * entry
    if params.lock_trail:
        gap = ((params.lock_trigger_r - params.lock_r) * risk if params.lock_trigger_target_frac is None
               else need - params.lock_r * risk)
        trail = best - sign * gap
        stop = max(stop, trail) if sign > 0 else min(stop, trail)
    return stop


_NEW_YORK = ZoneInfo("America/New_York")
WEEKEND_CLOSE_NY = (4, 16 * 60)     # Friday 16:00 New York (weekday, minute of day)
WEEKEND_OPEN_NY = (6, 18 * 60)      # Sunday 18:00 New York


def weekend_closed(t_ms: int) -> bool:
    """True when `t_ms` falls in the oil futures weekend: Friday 16:00 to
    Sunday 18:00 New York time (Fri 21:00 to Sun 23:00 UTC in winter, Fri
    20:00 to Sun 22:00 UTC in summer; zoneinfo does the switch). Pure."""
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(_NEW_YORK)
    wd, minute = lt.weekday(), lt.hour * 60 + lt.minute
    if wd == 5:
        return True
    if wd == WEEKEND_CLOSE_NY[0]:
        return minute >= WEEKEND_CLOSE_NY[1]
    if wd == WEEKEND_OPEN_NY[0]:
        return minute < WEEKEND_OPEN_NY[1]
    return False


def filters_on(params: Params) -> bool:
    """Is any tick-level entry filter of the 2026-11-02 loss audit configured?"""
    return (params.entry_delay_min is not None or params.side_age_h is not None or params.confirm_1h)


def call_start(d: Direction, side_since: Optional[int]) -> Optional[int]:
    """When the call `d` began: its regime_id when that is a time in ms (a
    journaled thesis's created_at), else when its side began."""
    rid = d.regime_id
    if rid is not None and rid.isdigit():
        return int(rid)
    return side_since


def entry_filter(d: Direction, t: int, side_since: Optional[int], last_1h: Optional[Candle],
                 params: Params) -> Optional[str]:
    """The reason the loss-audit filters refuse a new `d.side` entry at
    tick `t`, or None. `side_since`: when the side in force began (a run
    of same-side calls counts as one side). `last_1h`: the last 1h candle
    closed at or before `t`. Pure.
      entry_delay_min  "delay"      under N minutes since the call began
      side_age_h       "side_age"   the side has held under N hours
      confirm_1h       "confirm_1h" that candle closed against the side (or flat)"""
    if params.entry_delay_min is not None:
        start = call_start(d, side_since)
        if start is not None and t - start < params.entry_delay_min * 60_000:
            return "delay"
    if params.side_age_h is not None and side_since is not None and t - side_since < params.side_age_h * HOUR_MS:
        return "side_age"
    if params.confirm_1h:
        if last_1h is None:
            return "confirm_1h"
        agree = last_1h.c > last_1h.o if d.side == "long" else last_1h.c < last_1h.o
        if not agree:
            return "confirm_1h"
    return None


def _profit_lock(pos: _Position, bar: Candle, params: Params, t: Optional[int] = None) -> bool:
    """Fold `bar`'s best price into the position and tighten the stop to
    the profit lock's level. Never widens. True when this bar moved it.
    With `lock_weekend_only`, a bar outside the weekend window is skipped
    whole (its best price is not folded in either)."""
    if not lock_on(params) or pos.risk_usd <= 0:
        return False
    if params.lock_weekend_only and (t is None or not weekend_closed(t)):
        return False
    is_long = pos.side == "long"
    best = bar.h if is_long else bar.l
    pos.lock_best = best if pos.lock_best is None else (
        max(pos.lock_best, best) if is_long else min(pos.lock_best, best))
    new_stop = profit_lock_stop(pos.side, pos.avg_entry, pos.risk_usd, pos.lock_best, params, pos.target_px)
    if new_stop is None:
        return False
    tighter = new_stop > pos.stop_px + 1e-12 if is_long else new_stop < pos.stop_px - 1e-12
    if not tighter:
        return False
    pos.stop_px = new_stop
    pos.stop_moved = True
    pos.locked = True
    return True


def _lock_same_bar_exit(pos: _Position, bar: Candle, params: Params) -> Optional[float]:
    """After `_profit_lock` moved the stop inside `bar`: the exit price
    when the bar's close confirms price went back through the new stop
    (taker, slippage), else None (the stop applies from the next bar)."""
    if not params.lock_confirm_close:
        return None
    is_long = pos.side == "long"
    if not (bar.c <= pos.stop_px if is_long else bar.c >= pos.stop_px):
        return None
    sign = 1.0 if is_long else -1.0
    return pos.stop_px * (1.0 - sign * _market_exit_slip(params, pos.size * pos.stop_px))


DAY_MS = 86_400_000


def tally_stops(trades: Sequence[Trade], start: int, stops: Counter) -> int:
    """Fold `trades[start:]`'s "stop" exits into `stops`, keyed by the exit's
    UTC day: (day, None) for the desk and (day, side) per side. Returns the
    new start (len(trades)), so each trade is counted once. Mutates `stops`."""
    for tr in trades[start:]:
        if tr.exit_reason == "stop" and tr.exit_time is not None:
            day = tr.exit_time // DAY_MS
            stops[(day, None)] += 1
            stops[(day, tr.side)] += 1
    return len(trades)


def stop_cap_blocks(stops: Counter, t: int, side: str, params: Params) -> bool:
    """Does the daily stop cap forbid a new `side` entry at tick `t`? True
    once `daily_stop_cap` stops have filled on `t`'s UTC day (on that side,
    with `cap_per_side`). Always False when the cap is off. Pure."""
    if params.daily_stop_cap is None:
        return False
    key = (t // DAY_MS, side if params.cap_per_side else None)
    return stops[key] >= params.daily_stop_cap


def _cap_row(kind: str, setup: Dict[str, Any], t: int, is_market: bool, stops: Counter,
             params: Params) -> Dict[str, Any]:
    side = setup["side"]
    return {"time": t, "kind": kind, "side": side, "entry_px": setup["entry_px"],
            "stop_px": setup["stop_px"], "target_px": setup["target_px"], "is_market": is_market,
            "stops_today": stops[(t // DAY_MS, side if params.cap_per_side else None)]}


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
                target_missed=pos.target_touched and reason != "target", is_flip=pos.is_flip)


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


def floored_buffer(raw_buffer: float, price: float, floor: Optional[float],
                   floor_frac: Optional[float]) -> float:
    """The stop buffer with a floor under it: max(raw_buffer, floor,
    floor_frac x price). A None floor is no floor. Never below raw_buffer,
    so a floor can only widen a stop. Pure."""
    buf = raw_buffer
    if floor is not None:
        buf = max(buf, floor)
    if floor_frac is not None:
        buf = max(buf, floor_frac * price)
    return buf


def _setup(d: Direction, mid: float, lv: Dict[str, Any], params: Params,
          leverage: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The one setup a flat tick may take, or None (price not at the edge),
    or {"refused": reason}. Edge mode (the live rule): a limit at the entry
    range edge, capped at the thesis entry, only when the mid is within
    `atr_rest_frac` ATR of it. Market mode: the mid itself, refused above a
    long thesis's entry (below a short's: "never chase"), stop beyond the
    entry range (or beyond the mid if price already broke it). Target: the
    far range edge, `target_offset_frac` inside it, or `target_r` x risk.
    `leverage` (default None -> params.leverage, unchanged from before
    Params.conviction_leverage existed) is the leverage the leverage gate
    checks and the trade will actually size at."""
    lev = params.leverage if leverage is None else leverage
    is_long = d.side == "long"
    edge = lv["entry_low"] if is_long else lv["entry_high"]
    if params.entry_mode == "market":
        if d.entry_ceiling is not None and (mid > d.entry_ceiling if is_long else mid < d.entry_ceiling):
            return {"refused": "chase"}
        if params.no_break_entry and (mid < edge if is_long else mid > edge):
            return {"refused": "break"}
        entry = mid
        struct = min(edge, mid) if is_long else max(edge, mid)
    else:
        if d.entry_ceiling is not None:
            edge = min(edge, d.entry_ceiling) if is_long else max(edge, d.entry_ceiling)
        if abs(mid - edge) > params.atr_rest_frac * lv["atr14"]:
            return None
        entry = struct = edge
    buf = params.stop_buffer if params.stop_buffer_atr is None else params.stop_buffer_atr * lv["atr14"]
    if params.range_buffer_k is not None and params.stop_buffer_atr is None and lv["target_low"] is not None:
        rng_buf = params.range_buffer_k * (lv["target_high"] - lv["target_low"])
        buf = max(buf, rng_buf) if params.range_buffer_floor else rng_buf
    raw_buf = buf
    if params.stop_buffer_atr is not None and (params.stop_buffer_floor is not None
                                               or params.stop_buffer_floor_frac is not None):
        buf = floored_buffer(raw_buf, entry, params.stop_buffer_floor, params.stop_buffer_floor_frac)
    elif params.stop_buffer_atr is None and params.fixed_buffer_floor_frac is not None:
        buf = floored_buffer(raw_buf, entry, None, params.fixed_buffer_floor_frac)
    stop_px = struct - buf if is_long else struct + buf
    structural_target = lv["target_high"] if is_long else lv["target_low"]
    if params.target_r is not None:
        risk = abs(entry - stop_px)
        target_px = entry + params.target_r * risk if is_long else entry - params.target_r * risk
        structural_target = target_px
    elif params.target_offset_range_frac is not None:
        off = params.target_offset_range_frac * (lv["target_high"] - lv["target_low"])
        target_px = structural_target - off if is_long else structural_target + off
    else:
        target_px = offset_target(structural_target, d.side, params.target_offset_frac)
    gate = None
    if not check_leverage(entry, stop_px, lev, params.max_leverage)["ok"]:
        gate = "leverage"
    elif not check_levels(entry, stop_px, target_px, params.min_r)["ok"]:
        gate = "min_r"
    if gate is not None:
        out: Dict[str, Any] = {"refused": gate}
        if buf > raw_buf:
            # would the bare ATR stop have passed? then the floor cost this entry
            raw_stop = struct - raw_buf if is_long else struct + raw_buf
            raw_target = target_px
            if params.target_r is not None:
                raw_risk = abs(entry - raw_stop)
                raw_target = entry + params.target_r * raw_risk if is_long else entry - params.target_r * raw_risk
            if (check_leverage(entry, raw_stop, lev, params.max_leverage)["ok"]
                    and check_levels(entry, raw_stop, raw_target, params.min_r)["ok"]):
                out["floor_blocked"] = {"side": d.side, "gate": gate, "entry_px": entry, "stop_px": stop_px,
                                        "raw_stop_px": raw_stop, "target_px": raw_target}
        return out
    out = {"side": d.side, "entry_px": entry, "stop_px": stop_px, "target_px": target_px,
           "orig_target_px": structural_target}
    if buf > raw_buf:
        out["floored"] = True
    return out


def _market_open(setup: Dict[str, Any], bar: Candle, t: int, equity: float,
                 params: Params, leverage: Optional[float] = None,
                 size_eq: Optional[float] = None) -> Tuple[_Position, float, float]:
    """Open at the mid as a taker, size-aware slippage: (position, size, equity after the fee).
    `size_eq` is the equity the size is taken off (the portfolio hook, see
    `simulate_steps`); None sizes off `equity`, as before the hook existed."""
    sign = 1.0 if setup["side"] == "long" else -1.0
    notional = risk_capped_notional(equity if size_eq is None else size_eq, params, leverage,
                                    setup["entry_px"], setup["stop_px"])
    fill = setup["entry_px"] * (1.0 + sign * _market_exit_slip(params, notional))
    rest = _Resting(side=setup["side"], limit_px=fill, stop_px=setup["stop_px"],
                    target_px=setup["target_px"], placed_at=t, orig_target_px=setup["orig_target_px"])
    pos = _open_position(rest, fill, t, params)
    pos.leverage = leverage
    pos.size = notional / fill if fill > 0 else 0.0
    fee = params.taker_fee * fill * pos.size
    pos.entry_fees_usd = fee
    return pos, pos.size, equity - fee


def _flip_setup(side: str, back_to: float, mid: float, lv: Dict[str, Any],
                params: Params) -> Dict[str, Any]:
    """The flip after a target: `side` at the mid, target `back_to` (the
    closed trade's entry), stop `flip_stop_buffer` beyond the furthest of the
    entry range, the target range and the mid on the stop side. Returns the
    setup or {"refused": reason}."""
    short = side == "short"
    buf = params.flip_stop_buffer if params.flip_stop_buffer is not None else params.stop_buffer
    if short:
        struct = max(lv["entry_high"], lv["target_high"] or mid, mid)
        stop_px, ok_side = struct + buf, back_to < mid
    else:
        struct = min(lv["entry_low"], lv["target_low"] or mid, mid)
        stop_px, ok_side = struct - buf, back_to > mid
    if not ok_side:
        return {"refused": "flip_no_room"}
    lev = params.flip_leverage or params.leverage
    if not check_leverage(mid, stop_px, lev, params.max_leverage)["ok"]:
        return {"refused": "flip_leverage"}
    min_r = params.flip_min_r if params.flip_min_r is not None else params.min_r
    if not check_levels(mid, stop_px, back_to, min_r)["ok"]:
        return {"refused": "flip_min_r"}
    return {"side": side, "entry_px": mid, "stop_px": stop_px, "target_px": back_to,
            "orig_target_px": back_to, "leverage": lev}


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


def tick_bars(candles_15m: Sequence[Candle], candles_1h: Sequence[Candle], params: Params) -> List[Candle]:
    """The bars the sim takes one decision on, oldest first: 1h bars for the
    hourly loop (`tick_minutes` >= 60), else 15m bars."""
    return sorted(candles_1h if params.tick_minutes >= 60 else candles_15m, key=lambda c: c.t)


# The portfolio hook (2026-11-02, DESK_SPLIT_TEST_2026-11-02.md): called at
# every sizing point (a market entry or flip, a resting entry's fill, the
# top-up's full-size check) with (tick time, this desk's own realized
# equity) and returns the equity to size off. Live sizes each desk off
# desk_share x the whole account's equity, so the portfolio combiner
# (`services.strategies.backtest.portfolio`) passes share x (own + the other
# desks' P&L). None sizes off the desk's own equity, exactly as before.
SizeEquity = Callable[[int, float], float]


def simulate(candles_15m: Sequence[Candle], candles_1h: Sequence[Candle], direction_at: DirectionAt,
            params: Optional[Params] = None, funding_history: Optional[Sequence[Dict[str, Any]]] = None,
            size_equity: Optional[SizeEquity] = None) -> Run:
    """Run the whole window and return the Run (drains `simulate_steps`)."""
    steps = simulate_steps(candles_15m, candles_1h, direction_at, params, funding_history, size_equity)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def simulate_steps(candles_15m: Sequence[Candle], candles_1h: Sequence[Candle], direction_at: DirectionAt,
                   params: Optional[Params] = None, funding_history: Optional[Sequence[Dict[str, Any]]] = None,
                   size_equity: Optional[SizeEquity] = None) -> Generator[Tuple[int, float, Run], None, Run]:
    """`simulate`, one tick at a time: yields (tick time, marked equity,
    the Run so far) after each tick and returns the Run when the window
    ends (or the account is liquidated). Lets a caller run several desks
    on one clock and feed each desk's sizing the others' equity through
    `size_equity`."""
    params = params or Params()
    if params.target_mode not in ("touch", "market", "limit"):
        raise ValueError(f"unknown target_mode {params.target_mode!r}")
    if params.entry_mode not in ("edge", "market"):
        raise ValueError(f"unknown entry_mode {params.entry_mode!r}")
    if params.lock_trigger_r is not None and not (params.lock_r < params.lock_trigger_r):
        raise ValueError(f"lock_r {params.lock_r} must sit below lock_trigger_r {params.lock_trigger_r}")
    if params.daily_stop_cap is not None and params.daily_stop_cap < 1:
        raise ValueError(f"daily_stop_cap {params.daily_stop_cap} must be 1 or more (None turns it off)")
    if params.stop_buffer_floor is not None and params.stop_buffer_floor < 0:
        raise ValueError(f"stop_buffer_floor {params.stop_buffer_floor} must be 0 or more (None turns it off)")
    if params.stop_buffer_floor_frac is not None and not (0 <= params.stop_buffer_floor_frac < 1):
        raise ValueError(f"stop_buffer_floor_frac {params.stop_buffer_floor_frac} must be in [0, 1)")
    if params.fixed_buffer_floor_frac is not None and not (0 <= params.fixed_buffer_floor_frac < 1):
        raise ValueError(f"fixed_buffer_floor_frac {params.fixed_buffer_floor_frac} must be in [0, 1)")
    if params.weekend_off not in (None, "hold", "flatten"):
        raise ValueError(f"unknown weekend_off {params.weekend_off!r}")
    if params.lock_trigger_target_frac is not None:
        if params.lock_trigger_r is not None:
            raise ValueError("set lock_trigger_r or lock_trigger_target_frac, not both")
        if not (0 < params.lock_trigger_target_frac < 1):
            raise ValueError(f"lock_trigger_target_frac {params.lock_trigger_target_frac} must be in (0, 1)")
    if params.regime_filter is not None and params.regime_filter not in REGIME_FILTERS:
        raise ValueError(f"unknown regime_filter {params.regime_filter!r}")
    if params.regime_refresh not in ("day", "hour"):
        raise ValueError(f"unknown regime_refresh {params.regime_refresh!r}")
    c15 = sorted(candles_15m, key=lambda c: c.t)
    c1h = sorted(candles_1h, key=lambda c: c.t)
    regime: Optional[RegimeSeries] = None
    regime_cfg = params.regime_cfg or RegimeConfig()
    if params.regime_filter is not None:
        regime = label_series(c1h, regime_cfg)
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
    pending_flip: Optional[Tuple[str, float]] = None   # (side to open, target) after a target filled
    seen_1h_close: set = set()
    cap_on = params.daily_stop_cap is not None
    stops_by_day: Counter = Counter()  # (UTC day, None|side) -> stop exits (Params.daily_stop_cap)
    n_tallied = 0
    filt_on = filters_on(params)
    side_seen: Optional[str] = None      # the side in force (entry filters), and since when
    side_since: Optional[int] = None

    # The tick series: one decision per 15m bar, or per 1h bar for the hourly
    # loop (2026-10-25). With 1h ticks every tick lands on a 1h close, so the
    # invalidation check and funding settle on every tick; stop and target
    # fills read the 1h bar's high/low, the same conservative intrabar rule.
    ticks = tick_bars(c15, c1h, params)

    def size_eq(t: int, eq: float) -> float:
        return eq if size_equity is None else size_equity(t, eq)
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
        if filt_on:
            d_now = direction_at(t)
            if d_now is not None and d_now.side != side_seen:
                side_seen = d_now.side
                start = call_start(d_now, None)
                side_since = start if start is not None and start <= t else t

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
            if position is not None and not position.is_flip and d is not None and d.invalidation_px is not None:
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
        if position is not None and not position.is_flip:
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

        # 2b. weekend flatten (Params.weekend_off "flatten"): out at market on
        # the first tick inside the window, resting orders cancelled
        in_weekend = params.weekend_off is not None and weekend_closed(t)
        if in_weekend and params.weekend_off == "flatten":
            resting = None
            resting_add = None
            if position is not None and position.opened_at < t:
                sgn = 1.0 if position.side == "long" else -1.0
                wk_px = mid * (1.0 - sgn * _market_exit_slip(params, position.size * mid))
                trade = _close_trade(position, wk_px, t, "weekend", params.taker_fee, initial_size)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None

        # 3. resting entry: fill or hygiene-cancel (never on the bar it was placed)
        if resting is not None and resting.placed_at < t:
            is_long = resting.side == "long"
            filled = bar.l <= resting.limit_px if is_long else bar.h >= resting.limit_px
            if filled:
                run.filled += 1
                position = _open_position(resting, resting.limit_px, t, params)
                position.leverage = resting.leverage
                notional = risk_capped_notional(size_eq(t, equity), params, resting.leverage, resting.limit_px,
                                                resting.stop_px)
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
                trade = _close_trade(position, exit_px, t, "lock" if position.locked else "stop",
                                     params.taker_fee, initial_size)
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
                if params.flip_after_target and not position.is_flip:
                    pending_flip = ("short" if is_long else "long", position.avg_entry)
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                run.trades.append(trade)
                run.gross_pnl_usd += trade.gross_pnl_usd
                run.fees_total_usd += trade.fees_usd
                position = None
                resting_add = None
            else:
                # liquidation check (every bar, before top-up logic)
                liq_px = liq_estimate(position.avg_entry, position.leverage or params.leverage,
                                      params.max_leverage, is_long)
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
                elif (lambda h: bool(h) and t - position.opened_at >= h * HOUR_MS)(
                        params.flip_time_stop_hours if position.is_flip else params.time_stop_hours):
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
                    full = risk_capped_notional(size_eq(t, equity), params, position.leverage, position.avg_entry,
                                                position.stop_px) / position.avg_entry
                    if (resting_add is None and position.size < full - 1e-9 and not position.partial_done
                            and not position.is_flip):
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
                    if _profit_lock(position, bar, params, t):
                        lock_px = _lock_same_bar_exit(position, bar, params)
                        if lock_px is not None:
                            trade = _close_trade(position, lock_px, t, "lock", params.taker_fee, initial_size)
                            equity += trade.gross_pnl_usd - trade.exit_fee_usd
                            run.trades.append(trade)
                            run.gross_pnl_usd += trade.gross_pnl_usd
                            run.fees_total_usd += trade.fees_usd
                            position = None
                            resting_add = None

        # 5a. the flip after a target: one market attempt on the next tick
        if pending_flip is not None and position is None and resting is None \
                and len(run.trades) == trades_before_bar and lv is not None and lv["atr14"]:
            side, back_to = pending_flip
            pending_flip = None
            fs = _flip_setup(side, back_to, mid, lv, params)
            if cap_on:
                n_tallied = tally_stops(run.trades, n_tallied, stops_by_day)
            if in_weekend:
                run.refused["weekend"] += 1
            elif fs.get("refused"):
                run.refused[fs["refused"]] += 1
            elif cap_on and stop_cap_blocks(stops_by_day, t, fs["side"], params):
                run.refused["stop_cap"] += 1
                run.cap_blocked.append(_cap_row("flip", fs, t, True, stops_by_day, params))
            else:
                run.filled += 1
                position, initial_size, equity = _market_open(fs, bar, t, equity, params, fs["leverage"],
                                                              size_eq(t, equity))
                position.is_flip = True
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

        # 5. new entry evaluation when flat (a pending flip goes first, next tick)
        if position is None and resting is None and pending_flip is None:
            d = direction_at(t)
            if blocked_regime is not None and regime_key(d, t) == blocked_regime:
                d = None            # a target filled under this call: wait for the next one
            if d is not None and params.session_utc is not None and not _in_session(t, params.session_utc):
                d = None
            if d is not None and in_weekend:
                run.refused["weekend"] += 1
                d = None
            if d is not None and filt_on:
                why = entry_filter(d, t, side_since, c1h[n_closed - 1] if n_closed else None, params)
                if why is not None:
                    run.refused[why] += 1
                    d = None
            if d is not None and regime is not None:
                asof = t - t % DAY_MS if params.regime_refresh == "day" else t
                why = regime_blocks(d.side, regime.at(asof), params.regime_filter, regime_cfg)
                if why is not None:
                    run.refused[why] += 1
                    d = None
            if d is not None and lv is not None and lv["atr14"] and lv["target_low"] is not None:
                # Variant A (Params.ai_levels): try the thesis's own levels
                # first, at the leverage variant B (conviction_leverage)
                # would size this trade at either way. A None/refused AI
                # setup falls back to the structural rule exactly as if
                # ai_levels were off.
                lev = trade_leverage(d, params)
                ai_setup = _ai_setup(d, mid, params, lev) if params.ai_levels else None
                if ai_setup is not None and not ai_setup.get("refused"):
                    setup = ai_setup
                    open_now = setup["is_market"]
                else:
                    setup = _setup(d, mid, lv, params, lev)
                    open_now = params.entry_mode == "market"
                if cap_on:
                    n_tallied = tally_stops(run.trades, n_tallied, stops_by_day)
                if setup is None:
                    pass
                elif setup.get("refused"):
                    run.refused[setup["refused"]] += 1
                    fb = setup.get("floor_blocked")
                    if fb is not None:
                        run.floor_blocked[fb["gate"]] += 1
                        run.floor_blocked_rows.append({"time": t, **fb})
                elif open_now and len(run.trades) > trades_before_bar:
                    pass            # a trade closed inside this bar: its open is in the past now
                elif cap_on and stop_cap_blocks(stops_by_day, t, setup["side"], params):
                    # the daily stop cap: skip this entry, keep it for the eval
                    run.refused["stop_cap"] += 1
                    run.cap_blocked.append(_cap_row("entry", setup, t, open_now, stops_by_day, params))
                elif open_now:
                    run.filled += 1
                    run.floor_widened += 1 if setup.get("floored") else 0
                    position, initial_size, equity = _market_open(setup, bar, t, equity, params, lev,
                                                                  size_eq(t, equity))
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
                    elif _profit_lock(position, bar, params, t):
                        # the open was the fill, so this bar's high came after it
                        lock_px = _lock_same_bar_exit(position, bar, params)
                        if lock_px is not None:
                            trade = _close_trade(position, lock_px, t, "lock", params.taker_fee, initial_size)
                            equity += trade.gross_pnl_usd - trade.exit_fee_usd
                            run.trades.append(trade)
                            run.gross_pnl_usd += trade.gross_pnl_usd
                            run.fees_total_usd += trade.fees_usd
                            position = None
                else:
                    resting = _Resting(side=d.side, limit_px=setup["entry_px"], stop_px=setup["stop_px"],
                                       target_px=setup["target_px"], placed_at=t,
                                       orig_target_px=setup["orig_target_px"], leverage=lev)
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
        yield t, mark, run

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


__all__ = ["Direction", "DirectionAt", "Params", "Trade", "Run", "simulate", "simulate_steps", "tick_bars",
          "SizeEquity", "offset_target", "regime_key",
          "trade_leverage", "CONVICTION_LEVERAGE_FLOOR", "live_params", "profit_lock_stop", "stop_cap_blocks",
          "tally_stops", "floored_buffer", "weekend_closed", "lock_on", "risk_capped_notional", "filters_on",
          "call_start", "entry_filter"]
