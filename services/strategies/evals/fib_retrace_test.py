"""Fibonacci retracement test, 2026-11-16. Research only.

Operator, 2026-11-16: "also test fib retrace cuz thats what im doing". The
operator trades Brent by hand off Fibonacci retracements (a long of 26.84 at
92.96, stop 92.695, target 93.709, the stop ratcheted up as price nears the
target). This asks whether resting a limit at a retracement level of the
last swing makes money on this market, whether the ratchet helps, and how
it sits beside the live dip-buy book. One deterministic script, no LLM
call, no order, no config write.

The anchor swing, three definitions (five settings):

  a  range24   the 24h range: the low and the high of the last 24 closed 1h
               bars; the later of the two is the swing's end
  b  zz2/3/4   a zigzag on closed 1h bars: a pivot is confirmed once price
               reverses k x ATR14 from it (k 2 / 3 / 4); the swing is the
               last confirmed leg
  c  session   the low and the high since the last weekend reopen

Direction: "with" (buy a retrace of an up-swing, sell a retrace of a
down-swing), "thesis" (the same, only on the side of the journal thesis in
force), "long" (up-swings only). Family = swing definition x direction: 9.

Entry: the 38.2 / 50 / 61.8 / 78.6% retracement. "limit": a limit rests at
the level and fills when a bar trades THROUGH it by one tick, at the level,
maker fee. "confirm": after a bar reaches the level, the first bar that
closes back beyond it; market at the next bar's open, taker fee, slippage
from that bar's range. One entry per swing; a level that price has already
traded through since the swing's end is spent.

Stop: "next" (beyond the next deeper fib level), "origin" (beyond 100%),
"atr1" (the level less 1 x ATR14). "Beyond" = 0.1 ATR past the level.
Target: the swing extreme (0%), the -27.2% or the -61.8% extension, a
resting limit (maker, needs a trade-through).

The operator's ratchet, read at the tick: "be50" / "be75" move the stop to
the entry once the best price has covered 50% / 75% of the way to the
target; "fib75" moves it to the 38.2% level at 75% (never below the
entry); "off" leaves it.

The grid is the full cross less its duplicates (at 78.6% "next" is
"origin"; at 38.2% "fib75" is "be75"): 3,690 variants, each at 6% and 1.2%
risk. With that many tries the best row is the ceiling, not the
expectation, so a FAMILY is judged on its whole grid, declared here before
any run:

  PASS     a variant passes the bar AND more than half of the family's
           variants with 10 or more trades beside live are above zero
           beside live on every dataset
  LEANING  a variant passes or leans, the family as a whole does not
  FAIL     variants with 10 or more trades exist and none passes or leans
  NOISE    no variant has 10 trades beside live on the primary

The bar on a variant is `short_ladder_test.judge` (beat live on every
dataset, drawdown no worse, both halves, every grid neighbour, without the
best day) with at least 10 trades, plus one leg of this test's own: net
above zero ALONE on every long-history dataset. Under 10 trades a variant
is LEANING or NOISE, never PASS.

Costs: the account's measured xyz fee (`intraday_5m_test.measured_fees`,
0.90 / 0.31 bps taker / maker, read from the review ledger), NOT the sim's
4.5 / 1.5 bps constants, for the fib sleeve and for the live book it is
compared with. A resting limit fills at its price. A resting stop is a
taker fill with the sim's 3 bps slippage (a bar that opens beyond it fills
at its open). A market fill placed by the desk tick pays half the range of
a 5m bar, taken from the fill bar's range by the square-root-of-time rule
(0.289 x a 15m bar's range), never under 3 bps. Funding on the hour.

Sizing: `desk_tick_sim.risk_capped_notional` at 6% and 1.2% of equity at
the stop, `next_trade.fit_leverage` 20/15. One position at a time. Weekend
flat; no order on the first bar after the weekend. Each variant runs alone
and beside the live long book (`short_ladder_test.prepare`: no fib order
while the live long is open, an open fib trade closed when the live long
enters).

Datasets. Beside live, on 15m bars: brent_journal (primary), brent_replay30
and wti_journal. Alone only: brent_15m_50d and wti_15m_50d (every 15m bar
the venue serves), brent_1h_208d and wti_1h_208d (the same rules on 1h
bars, every 1h bar the venue serves). Walk-forward: both halves of the
primary.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_retrace_test \\
        --cache-dir <dir> --out-dir <dir>

Exit 0 when a family passes, 1 when none does, 2 with no journal.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import math
import multiprocessing
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, risk_capped_notional
from services.strategies.evals import intraday_5m_test as I5
from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.stop_cap_test import _iso, closed_only
from services.strategies.evals.trend_gate_test import base_params, side_only
from services.strategies.weekend import weekend_closed

START = SL.START
RISK_FULL = SL.RISK_FRAC            # 6% of equity at the stop, the desk's risk
RISK_SMALL = 0.012                  # 1.2%: "20% size"
RISKS = (("full", RISK_FULL), ("small", RISK_SMALL))
BRENT, WTI = SL.BRENT, SL.WTI
FIVE_MS, FIFTEEN_MS, HOUR_MS, DAY_MS = 300_000, 900_000, 3_600_000, 86_400_000
MIN_N = SL.MIN_N                    # 10: under this many trades a variant is LEANING or NOISE
PRIMARY = "brent_journal"
EPS = 1e-9
ATR_N = 14
MIN_SWING_ATR = 1.0                 # a range or session swing under 1 ATR is not a swing
SESSION_MIN_H = 4                   # closed 1h bars since the reopen before the session swing is read
STOP_BUF_ATR = 0.1                  # "beyond" a level = this many ATR past it
MIN_STOP_FRAC = I5.MIN_STOP_FRAC    # a stop closer than 5 bps to the fill is refused
WARMUP_15M_MS = 3 * DAY_MS          # alone-only windows start this far into the bars
WARMUP_1H_MS = 5 * DAY_MS

FIBS = (0.382, 0.5, 0.618, 0.786)
FIB_LADDER = FIBS + (1.0,)
ENTRIES = ("limit", "confirm")
STOPS = ("next", "origin", "atr1")
TARGETS = (0.0, 0.272, 0.618)       # 0% = the swing extreme, then the -27.2% and -61.8% extensions
RATCHETS = ("off", "be50", "be75", "fib75")
RATCHET_RULES: Dict[str, Optional[Tuple[float, str]]] = {
    "off": None, "be50": (0.5, "be"), "be75": (0.75, "be"), "fib75": (0.75, "fib")}
SWINGS = ("range24", "zz2", "zz3", "zz4", "session")
ZZ_K = {"zz2": 2.0, "zz3": 3.0, "zz4": 4.0}
SWING_DEF = {"range24": "a", "zz2": "b", "zz3": "b", "zz4": "b", "session": "c"}
SWING_LABEL = {"a": "a. 24h range", "b": "b. 1h zigzag (k x ATR)", "c": "c. session since the reopen"}
DIRECTIONS = ("with", "thesis", "long")
BESIDE = ("brent_journal", "brent_replay30", "wti_journal")
ALONE_ONLY = ("brent_15m_50d", "wti_15m_50d", "brent_1h_208d", "wti_1h_208d")
# the textbook setup, named before any run: the one unbiased row per family
REFERENCE = {"level": 0.618, "entry": "limit", "stop": "next", "target": 0.0, "ratchet": "off"}
REFERENCE_SWING = {"a": "range24", "b": "zz3", "c": "session"}


# ── level math (pure) ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Swing:
    side: int                 # +1 an up-swing (origin = the low), -1 a down-swing (origin = the high)
    origin: float             # the 100% level
    extreme: float            # the 0% level, the swing's end
    t_ext: int                # the open of the 1h bar that made the extreme
    key: Tuple[Any, ...]      # one entry per key

    @property
    def size(self) -> float:
        return abs(self.extreme - self.origin)


def fib_price(origin: float, extreme: float, ratio: float) -> float:
    """The price `ratio` of the way back from the swing's extreme to its
    origin: 0 = the extreme, 1 = the origin, a negative ratio an extension
    beyond the extreme. Works for both swing directions."""
    return extreme - ratio * (extreme - origin)


def next_level(ratio: float) -> float:
    """The next deeper level on the ladder 38.2 / 50 / 61.8 / 78.6 / 100."""
    for r in FIB_LADDER:
        if r > ratio + EPS:
            return r
    return 1.0


def stop_price(rule: str, s: Swing, level: float, atr: float) -> float:
    """"next": 0.1 ATR beyond the next deeper fib level. "origin": 0.1 ATR
    beyond the swing's origin. "atr1": the entry level less 1 ATR."""
    if rule == "atr1":
        return fib_price(s.origin, s.extreme, level) - s.side * atr
    ratio = next_level(level) if rule == "next" else 1.0
    if rule not in ("next", "origin"):
        raise ValueError(f"unknown stop rule {rule!r}")
    return fib_price(s.origin, s.extreme, ratio) - s.side * STOP_BUF_ATR * atr


def target_price(s: Swing, ext: float) -> float:
    """The swing extreme (ext 0) or the extension `ext` of the swing beyond it."""
    return fib_price(s.origin, s.extreme, -ext)


def ratchet_stop(rule: Optional[Tuple[float, str]], side: int, fill: float, target: float, best: Optional[float],
                 s: Swing) -> Optional[float]:
    """The stop the operator's ratchet asks for, or None while the best
    price since the fill has not covered `rule[0]` of the way to the target.
    "be": the entry. "fib": the swing's 38.2% level, never worse than the
    entry. Pure; the caller checks it is tighter and still behind price."""
    if rule is None or best is None:
        return None
    frac, mode = rule
    if side * (best - fill) < frac * side * (target - fill) - EPS:
        return None
    if mode == "be":
        return fill
    lvl = fib_price(s.origin, s.extreme, 0.382)
    return lvl if side * (lvl - fill) > 0 else fill


def cost_trade(entry: float, stop: float, target: float, taker: float, maker: float, slip: float,
               entry_fee: Optional[float] = None) -> Dict[str, Any]:
    """One planned trade, costed: R before and after costs (the entry fee,
    maker out at the target, taker plus slippage out at the stop) and the
    win rate it needs to break even. Either side."""
    side = 1.0 if target > entry else -1.0
    fee_in = taker if entry_fee is None else entry_fee
    win = side * (target - entry) - fee_in * entry - maker * target
    stop_fill = stop * (1.0 - side * slip)
    loss = side * (entry - stop_fill) + fee_in * entry + taker * stop_fill
    r = side * (target - entry) / (side * (entry - stop)) if entry != stop else 0.0
    r_net = win / loss if loss > 0 else 0.0
    return {"entry": entry, "stop": stop, "target": target, "side": "long" if side > 0 else "short",
            "stop_bps": round(abs(entry - stop) / entry * 1e4, 1), "planned_r": round(r, 3),
            "r_after_costs": round(r_net, 3),
            "breakeven_win_rate_pct": round(1.0 / (1.0 + r_net) * 100.0, 1) if r_net > -1 else None}


# ── swings (pure) ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pivot:
    kind: int                 # +1 a high, -1 a low
    px: float
    t: int                    # the open of the 1h bar that made it
    confirmed_i: int          # index of the 1h bar whose close confirmed it


def zigzag_pivots(hours: Sequence[Candle], atr: Sequence[Optional[float]], k: float) -> List[Pivot]:
    """Zigzag pivots on 1h bars. In an up-leg a bar with a higher high
    extends the leg; otherwise a bar whose low is k x ATR (the ATR at that
    bar) under the leg's high confirms that high as a pivot, at that bar's
    close, and a down-leg starts from the bar's low. Mirrored for a
    down-leg. The first leg starts on the first k x ATR move off the
    running extreme. Reads nothing after the bar it is on."""
    out: List[Pivot] = []
    leg = 0
    hi: Optional[Tuple[float, int]] = None
    lo: Optional[Tuple[float, int]] = None
    for i, b in enumerate(hours):
        a = atr[i] if i < len(atr) else None
        if hi is None or lo is None:
            hi, lo = (b.h, i), (b.l, i)
            continue
        if leg == 0:
            if b.h > hi[0]:
                hi = (b.h, i)
            if b.l < lo[0]:
                lo = (b.l, i)
            if not a:
                continue
            up = i > lo[1] and b.h - lo[0] >= k * a
            dn = i > hi[1] and hi[0] - b.l >= k * a
            if up and not dn:
                out.append(Pivot(-1, lo[0], hours[lo[1]].t, i))
                leg, hi = 1, (b.h, i)
            elif dn and not up:
                out.append(Pivot(1, hi[0], hours[hi[1]].t, i))
                leg, lo = -1, (b.l, i)
        elif leg == 1:
            if b.h > hi[0]:
                hi = (b.h, i)
            elif a and hi[0] - b.l >= k * a:
                out.append(Pivot(1, hi[0], hours[hi[1]].t, i))
                leg, lo = -1, (b.l, i)
        else:
            if b.l < lo[0]:
                lo = (b.l, i)
            elif a and b.h - lo[0] >= k * a:
                out.append(Pivot(-1, lo[0], hours[lo[1]].t, i))
                leg, hi = 1, (b.h, i)
    return out


def swing_from_pivots(pivots: Sequence[Pivot], n_closed: int) -> Optional[Swing]:
    """The last leg between two pivots both confirmed by the close of 1h bar
    n_closed - 1 (so known with `n_closed` bars closed)."""
    j = bisect.bisect_left([p.confirmed_i for p in pivots], n_closed) - 1
    if j < 1:
        return None
    a, b = pivots[j - 1], pivots[j]
    return Swing(side=b.kind, origin=a.px, extreme=b.px, t_ext=b.t, key=("zz", b.kind, b.t, b.px))


def swing_from_window(window: Sequence[Candle], atr: Optional[float], tag: str) -> Optional[Swing]:
    """The swing inside a window of closed 1h bars: from its low to its high
    when the high came later (an up-swing), from its high to its low when
    the low came later. None when both sit in one bar, or the move is under
    MIN_SWING_ATR x ATR. A tie takes the latest bar."""
    if not window or not atr:
        return None
    hi_i = max(range(len(window)), key=lambda i: (window[i].h, i))
    lo_i = min(range(len(window)), key=lambda i: (window[i].l, -i))
    hi, lo = window[hi_i].h, window[lo_i].l
    if hi_i == lo_i or hi - lo < MIN_SWING_ATR * atr:
        return None
    if hi_i > lo_i:
        return Swing(1, lo, hi, window[hi_i].t, (tag, 1, window[hi_i].t, hi))
    return Swing(-1, hi, lo, window[lo_i].t, (tag, -1, window[lo_i].t, lo))


# ── per-bar context ────────────────────────────────────────────────────────

@dataclass
class Ctx:
    step: int                               # the tick bar, ms (15m or 1h)
    t: List[int]
    o: List[float]
    h: List[float]
    l: List[float]
    c: List[float]
    atr: List[Optional[float]]              # ATR14 of the 1h bars closed at the tick
    weekend: List[bool]
    reopen: List[bool]                      # the first bar after the weekend
    fund: Dict[int, float]
    swings: Dict[str, List[Optional[Swing]]]   # per swing setting, the anchor known at each tick
    ret: Dict[str, List[Optional[float]]]      # the deepest retrace since the swing's end, bars before the tick


def _extreme_index(t: Sequence[int], h: Sequence[float], l: Sequence[float], s: Swing) -> int:
    """The tick bar that made the swing's extreme: the last bar inside the
    extreme's 1h bar whose high (low) reaches it; that hour's last bar when
    none does; -1 when the hour is before the first bar."""
    a, b = bisect.bisect_left(t, s.t_ext), bisect.bisect_left(t, s.t_ext + HOUR_MS)
    if b == 0:
        return -1
    for i in range(b - 1, a - 1, -1):
        if (h[i] >= s.extreme - EPS) if s.side > 0 else (l[i] <= s.extreme + EPS):
            return i
    return b - 1


def retrace_series(t: Sequence[int], h: Sequence[float], l: Sequence[float], swings: Sequence[Optional[Swing]]
                   ) -> List[Optional[float]]:
    """Per tick i: the deepest price against the swing since its extreme,
    over the bars after the extreme's bar and before bar i (None when there
    is none). Closed data only."""
    out: List[Optional[float]] = []
    key, cur, ext_i = None, None, -1
    for i, s in enumerate(swings):
        if s is None:
            key, cur = None, None
            out.append(None)
            continue
        if s.key != key:
            key, ext_i, cur = s.key, _extreme_index(t, h, l, s), None
            for j in range(ext_i + 1, i):
                v = l[j] if s.side > 0 else h[j]
                cur = v if cur is None else (min(cur, v) if s.side > 0 else max(cur, v))
        elif i - 1 > ext_i:
            v = l[i - 1] if s.side > 0 else h[i - 1]
            cur = v if cur is None else (min(cur, v) if s.side > 0 else max(cur, v))
        out.append(cur)
    return out


def build_ctx(bars: Sequence[Candle], hours: Sequence[Candle], funding: Optional[Sequence[Dict[str, Any]]] = None,
              step: int = FIFTEEN_MS) -> Ctx:
    """Everything a variant reads at a tick (the OPEN of bar i), from 1h
    bars closed at or before that moment and tick bars before i."""
    bars = sorted(bars, key=lambda b: b.t)
    hours = sorted(hours, key=lambda b: b.t)
    hclose = [b.t + HOUR_MS for b in hours]
    atr_h = I5._atr(hours, ATR_N)
    reopens = [b.t for b in hours if SL.reopen_tick(b.t, HOUR_MS)]
    pivots = {name: zigzag_pivots(hours, atr_h, k) for name, k in ZZ_K.items()}
    t = [b.t for b in bars]
    swings: Dict[str, List[Optional[Swing]]] = {name: [] for name in SWINGS}
    atr: List[Optional[float]] = []
    cache_n = -1
    cache: Dict[str, Any] = {}
    for b in bars:
        n = bisect.bisect_right(hclose, b.t)
        if n != cache_n:
            a = atr_h[n - 1] if n else None
            cache = {"atr": a, "range24": swing_from_window(hours[n - 24:n], a, "r24") if n >= 24 else None}
            for name in ZZ_K:
                cache[name] = swing_from_pivots(pivots[name], n)
            cache_n = n
        r = bisect.bisect_right(reopens, b.t) - 1
        sess = None
        if r >= 0:
            first = bisect.bisect_left(hclose, reopens[r] + HOUR_MS)
            if n - first >= SESSION_MIN_H:
                skey = ("sess", n)
                if cache.get("_sess_key") != skey:
                    cache["_sess_key"], cache["_sess"] = skey, swing_from_window(hours[first:n], cache["atr"], "sess")
                sess = cache["_sess"]
        atr.append(cache["atr"])
        swings["range24"].append(cache["range24"])
        for name in ZZ_K:
            swings[name].append(cache[name])
        swings["session"].append(sess)
    h, l = [b.h for b in bars], [b.l for b in bars]
    fund = {int(r["time"]) - int(r["time"]) % HOUR_MS: float(r["fundingRate"]) for r in (funding or [])
            if r.get("time") is not None and r.get("fundingRate") is not None}
    return Ctx(step=step, t=t, o=[b.o for b in bars], h=h, l=l, c=[b.c for b in bars], atr=atr,
               weekend=[weekend_closed(x) for x in t], reopen=[SL.reopen_tick(x, step) for x in t], fund=fund,
               swings=swings, ret={name: retrace_series(t, h, l, sw) for name, sw in swings.items()})


# ── the grid ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    swing: str
    direction: str
    level: float
    entry: str
    stop: str
    target: float
    ratchet: str

    @property
    def name(self) -> str:
        return (f"{self.swing}_{self.direction}_L{self.level * 1000:.0f}_{self.entry}_{self.stop}"
                f"_T{self.target * 1000:.0f}_{self.ratchet}")

    @property
    def swing_def(self) -> str:
        return SWING_DEF[self.swing]

    @property
    def family(self) -> str:
        return f"{self.swing_def}/{self.direction}"


def is_duplicate(v: Variant) -> bool:
    """At 78.6% the next deeper level IS the origin, and at 38.2% the
    38.2 ratchet IS break-even: one of each pair is kept."""
    return (v.level == FIBS[-1] and v.stop == "next") or (v.level == FIBS[0] and v.ratchet == "fib75")


def variants() -> List[Variant]:
    out = [Variant(s, d, lv, e, st, tg, rt) for s in SWINGS for d in DIRECTIONS for lv in FIBS for e in ENTRIES
           for st in STOPS for tg in TARGETS for rt in RATCHETS]
    return [v for v in out if not is_duplicate(v)]


def _shift(levels: Sequence[Any], value: Any) -> List[Any]:
    i = list(levels).index(value)
    return [levels[j] for j in (i - 1, i + 1) if 0 <= j < len(levels)]


def neighbours(v: Variant) -> List[Variant]:
    """One step along each ordered axis: the fib level, the target, and the
    zigzag's k. A step that lands on a dropped duplicate is skipped."""
    out = [dataclasses.replace(v, level=x) for x in _shift(FIBS, v.level)]
    out += [dataclasses.replace(v, target=x) for x in _shift(TARGETS, v.target)]
    if v.swing in ZZ_K:
        out += [dataclasses.replace(v, swing=x) for x in _shift(tuple(ZZ_K), v.swing)]
    return [n for n in out if not is_duplicate(n)]


def reference_variant(swing_def: str, direction: str, **over: Any) -> Variant:
    return Variant(**{"swing": REFERENCE_SWING[swing_def], "direction": direction, **REFERENCE, **over})


# ── the walker ─────────────────────────────────────────────────────────────

def market_slip(ctx: Ctx, x: int, base: float) -> float:
    """Slippage of a market fill placed by the desk tick on bar x: half the
    range of a 5m bar, taken from this bar's range by the square-root-of-
    time rule, never under the sim's own slippage."""
    if ctx.o[x] <= 0:
        return base
    return max(base, 0.5 * math.sqrt(FIVE_MS / ctx.step) * (ctx.h[x] - ctx.l[x]) / ctx.o[x])


def entry_gate(fill: float, stop: float, target: float, side: int, p: Params, min_r: float
               ) -> Tuple[Optional[str], float, float]:
    """(refusal or None, planned R, leverage) for an entry at `fill`."""
    dist = side * (fill - stop)
    if fill <= 0 or dist / fill < MIN_STOP_FRAC:
        return "tight_stop", 0.0, 0.0
    room = side * (target - fill)
    if room <= p.tick_size:
        return "no_room", 0.0, 0.0
    r = room / dist
    if r < min_r - EPS:
        return "min_r", r, 0.0
    lev, gate = NT.fit_leverage(fill, stop, p.leverage, p.max_leverage, p.entry_fit_leverage or ())
    if not gate.get("ok"):
        return "leverage", r, lev
    return None, r, lev


def walk(ctx: Ctx, v: Variant, p: Params, lo_i: int, hi_i: int, thesis: Optional[Sequence[int]] = None,
         live: Optional[I5.Live] = None, min_r: float = 0.0) -> Dict[str, Any]:
    """One variant over bars [lo_i, hi_i], one position at a time. Returns
    size-free paths (the fills do not depend on size at this account's
    notional: the book curve is under the 3 bps floor), the swings an order
    rested on ("armed"), and the refusals.

    At the open of bar i, flat: no order in the weekend, on the reopen bar,
    while the live long is open (beside live), without a swing on an
    allowed side, or on a swing already traded. "limit": the level must not
    have traded through since the swing's end and the open must be on the
    swing's side of it; the order rests for bar i and fills at the level
    when the bar trades through it by one tick (an order that rested on an
    earlier bar and is gapped through fills at the open). "confirm": the retrace has
    reached the level without reaching the stop, bar i closes back beyond
    the level (the first such close), and the fill is the open of bar i+1.

    In a trade, per bar x: at its open (x after the fill bar) the ratchet
    is read off closed bars, funding is charged on the hour, and the trade
    is closed at market when the live long enters or the weekend starts;
    then the stop (a bar that opens beyond it fills at its open; it can
    fire on the fill bar), then the target (never on the fill bar). A stop
    and a target in one bar is a loss. A trade open at the end is marked at
    the last close."""
    sw, ret = ctx.swings[v.swing], ctx.ret[v.swing]
    rule = RATCHET_RULES[v.ratchet]
    tick, base = p.tick_size, p.slippage
    taker, maker = p.taker_fee, p.maker_fee
    o, h, l, c, t = ctx.o, ctx.h, ctx.l, ctx.c, ctx.t
    done: set = set()
    armed: set = set()
    refused: set = set()
    paths: List[Dict[str, Any]] = []
    i = lo_i
    while i <= hi_i:
        s = sw[i]
        if (s is None or s.key in done or ctx.weekend[i] or ctx.reopen[i]
                or (live is not None and live.long_open[i])):
            i += 1
            continue
        side = s.side
        a = ctx.atr[i]
        if not a or (v.direction == "long" and side < 0) or (
                v.direction == "thesis" and (thesis is None or thesis[i] != side)):
            i += 1
            continue
        level = fib_price(s.origin, s.extreme, v.level)
        stop = stop_price(v.stop, s, v.level, a)
        target = target_price(s, v.target)
        r = ret[i]
        ext = l[i] if side > 0 else h[i]
        if v.entry == "limit":
            gapped = side * (o[i] - level) <= 0
            if (r is not None and side * (r - level) <= -tick + EPS) or (gapped and s.key not in armed):
                i += 1
                continue
            px = o[i] if gapped else level          # an order already resting is filled at a gap's open
            why, plan_r, lev = entry_gate(px, stop, target, side, p, min_r)
            if why:
                refused.add((s.key, why))
                if gapped:
                    done.add(s.key)
                i += 1
                continue
            armed.add(s.key)
            if not gapped and side * (level - ext) < tick - EPS:
                i += 1
                continue
            j, ref, fill, fee_in = i, px, px, maker
        else:
            deep = ext if r is None else (min(r, ext) if side > 0 else max(r, ext))
            if side * (deep - stop) <= 0:
                done.add(s.key)                     # the retrace reached the stop first: the setup is dead
                i += 1
                continue
            reached = side * (ext - level) <= 0 or (
                i > lo_i and r is not None and side * (r - level) <= 0 and side * (c[i - 1] - level) <= 0)
            j = i + 1
            if (not reached or side * (c[i] - level) <= 0 or j > hi_i or ctx.weekend[j]
                    or (live is not None and live.long_open[j])):
                i += 1
                continue
            armed.add(s.key)
            ref = o[j]
            fill = ref * (1.0 + side * market_slip(ctx, j, base))
            why, plan_r, lev = entry_gate(fill, stop, target, side, p, min_r)
            if why:
                refused.add((s.key, why))
                done.add(s.key)
                i += 1
                continue
            fee_in = taker
        done.add(s.key)
        cur, moved, best = stop, False, None
        fund, reason, x_exit, fee_out = 0.0, "open", hi_i, 0.0
        exit_ref = exit_px = None
        for x in range(j, hi_i + 1):
            if x > j:
                if rule is not None and not moved:
                    new = ratchet_stop(rule, side, fill, target, best, s)
                    if new is not None and side * (new - cur) > EPS and side * (c[x - 1] - new) > tick:
                        cur, moved = new, True
                if t[x] % HOUR_MS == 0:
                    fund += side * ctx.fund.get(t[x], 0.0) * o[x]
                why = ("long_entry" if live is not None and live.long_entry[x]
                       else "weekend" if ctx.weekend[x] else None)
                if why:
                    exit_ref = o[x]
                    exit_px = exit_ref * (1.0 - side * market_slip(ctx, x, base))
                    reason, x_exit, fee_out = why, x, taker
                    break
            near = l[x] if side > 0 else h[x]
            if side * (near - cur) <= 0:
                exit_ref = min(cur, o[x]) if side > 0 else max(cur, o[x])
                exit_px = exit_ref * (1.0 - side * base)
                reason, x_exit, fee_out = ("ratchet" if moved else "stop"), x, taker
                break
            far = h[x] if side > 0 else l[x]
            if x > j:
                if side * (far - target) >= tick - EPS:
                    exit_ref = exit_px = target
                    reason, x_exit, fee_out = "target", x, maker
                    break
                best = far if best is None else (max(best, far) if side > 0 else min(best, far))
            elif v.entry == "confirm":
                best = far
        if exit_px is None:
            exit_ref = exit_px = c[hi_i]
        paths.append({"i": j, "x": x_exit, "side": side, "ref": ref, "fill": fill, "stop": stop, "stop_final": cur,
                      "target": target, "exit_ref": exit_ref, "exit_px": exit_px, "reason": reason,
                      "fund_unit": fund, "fee_in": fee_in, "fee_out": fee_out, "lev": lev, "planned_r": plan_r,
                      "level": v.level, "moved": moved})
        if reason == "open":
            break
        i = x_exit + 1
    return {"paths": paths, "armed": len(armed), "refused": dict(Counter(w for _, w in refused))}


def size_paths(ctx: Ctx, paths: Sequence[Dict[str, Any]], p: Params, risk_frac: float, start_equity: float,
               live: Optional[I5.Live] = None) -> List[Dict[str, Any]]:
    """The paths as trades at `risk_frac` of equity at the stop. Equity is
    the start (alone) or the live book's marked equity at the fill (beside
    live), plus the sleeve's own closed P&L. A path met with no equity left
    is dropped."""
    pp = dataclasses.replace(p, risk_cap_frac=risk_frac)
    pnl = 0.0
    out: List[Dict[str, Any]] = []
    for q in paths:
        j, x, side = q["i"], q["x"], q["side"]
        eq = (live.eq[j] if live is not None else start_equity) + pnl
        if eq <= 0:
            continue
        notional = risk_capped_notional(eq, pp, q["lev"], q["fill"], q["stop"])
        size = notional / q["fill"]
        fees = q["fee_in"] * notional + q["fee_out"] * q["exit_px"] * size
        funding = q["fund_unit"] * size
        raw = side * (q["exit_ref"] - q["ref"]) * size
        gross = side * (q["exit_px"] - q["fill"]) * size
        net = gross - fees - funding
        risk_usd = side * (q["fill"] - q["stop"]) * size
        pnl += net
        exit_ms = None if q["reason"] == "open" else ctx.t[x]
        out.append({"entry_ms": ctx.t[j], "exit_ms": exit_ms, "entry": _iso(ctx.t[j]), "exit": _iso(exit_ms),
                    "side": "long" if side > 0 else "short", "entry_px": round(q["fill"], 4),
                    "exit_px": round(q["exit_px"], 4), "stop_px": round(q["stop"], 4),
                    "stop_final_px": round(q["stop_final"], 4), "target_px": round(q["target"], 4),
                    "size": size, "notional": notional, "leverage": q["lev"], "reason": q["reason"],
                    "raw_usd": raw, "slip_usd": raw - gross, "fees_usd": fees, "funding_usd": funding,
                    "net_usd": net, "risk_usd": risk_usd, "risk_pct": risk_usd / eq * 100.0,
                    "r": net / risk_usd if risk_usd > 1e-12 else None, "planned_r": round(q["planned_r"], 3),
                    "level": q["level"], "i": j, "x": x, "hold_min": (ctx.t[x] - ctx.t[j]) / 60_000.0})
    return out


# ── datasets ───────────────────────────────────────────────────────────────

@dataclass
class Dataset:
    name: str
    ctx: Ctx
    lo_i: int
    hi_i: int
    start_ms: int
    end_ms: int
    thesis: List[int]                       # per tick: +1 long, -1 short, 0 none
    params: Params
    live: Optional[I5.Live] = None
    live_sum: Optional[Dict[str, Any]] = None
    intervals: Optional[List[Tuple[int, Optional[int]]]] = None


def thesis_sides(ctx: Ctx, thesis_at: Optional[SL.ThesisAt]) -> List[int]:
    out = []
    for x in ctx.t:
        side = (thesis_at(x) or (None,))[0] if thesis_at else None
        out.append(1 if side == "long" else -1 if side == "short" else 0)
    return out


def make_dataset(name: str, ctx: Ctx, thesis: List[int], bp: Params, start_ms: int, end_ms: int,
                 live_run: Optional[Dict[str, Any]] = None) -> Dataset:
    """One window of a context. With `live_run` (`short_ladder_test.
    prepare` on the same window) the live long book is mapped onto the
    ticks: its marked equity, when its long is open, when it enters."""
    lo_i, hi_i = bisect.bisect_left(ctx.t, start_ms), bisect.bisect_left(ctx.t, end_ms) - 1
    ds = Dataset(name, ctx, lo_i, hi_i, start_ms, end_ms, thesis, bp)
    if live_run is not None:
        by_t = {tk.t: k for k, tk in enumerate(live_run["ticks"])}
        eq, opn, ent = [], [], []
        last = bp.starting_equity
        for x in ctx.t:
            k = by_t.get(x)
            if k is not None:
                last = live_run["long_curve"][k]
            eq.append(last)
            opn.append(bool(live_run["long_open"][k]) if k is not None else False)
            ent.append(bool(live_run["long_entry"][k]) if k is not None else False)
        ds.live, ds.intervals = I5.Live(eq, opn, ent), live_run["intervals"]
        ds.live_sum = {**live_run["live"], "max_dd_pct": SL.max_dd_pct(eq[lo_i:hi_i + 1], bp.starting_equity)}
    return ds


def run_variant(v: Variant, ds: Dataset, curves: bool = True, min_r: float = 0.0, keep: bool = False
                ) -> Dict[str, Dict[str, Any]]:
    """One variant on one dataset at every risk size: alone, and (with a
    live book) beside it. Keyed by the risk label."""
    ctx, bp = ds.ctx, ds.params
    start = bp.starting_equity
    alone = walk(ctx, v, bp, ds.lo_i, ds.hi_i, ds.thesis, None, min_r)
    comb = walk(ctx, v, bp, ds.lo_i, ds.hi_i, ds.thesis, ds.live, min_r) if ds.live is not None else None
    out: Dict[str, Dict[str, Any]] = {}
    for label, risk in RISKS:
        ta = size_paths(ctx, alone["paths"], bp, risk, start)
        if curves:
            flat = [start] * (ds.hi_i - ds.lo_i + 1)
            sa = I5.summarize(ta, I5.pnl_curve(ctx, ta, ds.lo_i, ds.hi_i), flat, alone["refused"])
        else:
            sa = I5.summarize(ta)
        sa["armed"] = alone["armed"]
        row: Dict[str, Any] = {"alone": sa}
        if comb is not None:
            tc = size_paths(ctx, comb["paths"], bp, risk, start, ds.live)
            if curves:
                sc = I5.summarize(tc, I5.pnl_curve(ctx, tc, ds.lo_i, ds.hi_i), ds.live.eq[ds.lo_i:ds.hi_i + 1],
                                  comb["refused"])
                row["collisions"] = SL.collisions(ta, ds.intervals or [], ds.end_ms)
                row["drop_best_day"] = SL.drop_best_day(tc)
            else:
                sc = I5.summarize(tc)
            row.update({"combined": sc, "live": ds.live_sum, "combined_dd_pct": sc.get("max_dd_pct"),
                        "combined_total_usd": round(ds.live_sum["net_usd"] + sc["net_usd"], 2)})
            if keep:
                row["trades_combined"] = tc
        if keep:
            row["trades_alone"] = ta
        out[label] = row
    return out


# ── judging (pure) ─────────────────────────────────────────────────────────

def judge(row: Dict[str, Any], neighbour_nets: Sequence[Optional[float]], primary: str = PRIMARY) -> Dict[str, Any]:
    """`short_ladder_test.judge` on the datasets that have a live book, at
    10 trades, plus this test's own leg: net above zero ALONE on every
    long-history dataset. A PASS that fails the added leg is a FAIL, a
    LEANING a NOISE."""
    beside = {k: d for k, d in row["datasets"].items() if "combined" in d}
    base = SL.judge({**row, "datasets": beside}, neighbour_nets, primary, min_n=MIN_N, reopen_leg=False)
    long_ok = {k: d["alone"]["net_usd"] > EPS for k, d in row["datasets"].items() if "combined" not in d}
    ok = bool(long_ok) and all(long_ok.values())
    verdict = base["verdict"]
    if not ok:
        verdict = {"PASS": "FAIL", "LEANING": "NOISE"}.get(verdict, verdict)
    legs = {k: v for k, v in base["legs"].items() if k != "holds_without_reopen_ticks"}
    legs["alone_on_long_history"] = ok
    return {"verdict": verdict, "legs": legs, "n_primary": base["n_primary"], "long_history": long_ok,
            "repo_bar": base["verdict"]}


def family_verdict(judged: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The family rule of the module docstring. `judged`: one
    {"verdict", "n_primary", "beats_all"} per variant, `beats_all` = above
    zero beside live on every dataset."""
    counts = Counter(j["verdict"] for j in judged)
    big = [j for j in judged if j["n_primary"] >= MIN_N]
    breadth = sum(1 for j in big if j["beats_all"]) / len(big) if big else 0.0
    if counts["PASS"] and breadth > 0.5:
        verdict = "PASS"
    elif counts["PASS"] or counts["LEANING"]:
        verdict = "LEANING"
    elif big:
        verdict = "FAIL"
    else:
        verdict = "NOISE"
    return {"verdict": verdict, "counts": dict(counts), "judged_10_plus": len(big),
            "breadth_pct": round(breadth * 100.0, 1)}


# ── evaluation ─────────────────────────────────────────────────────────────

_G: Dict[str, Any] = {}


def evaluate_one(v: Variant, datasets: Dict[str, Dataset], halves: Sequence[Dataset], min_r: float = 0.0,
                 long_halves: Optional[Dict[str, Sequence[Dataset]]] = None) -> Dict[str, Dict[str, Any]]:
    """One variant everywhere, one row per risk label (not yet judged).
    `long_halves`: the two halves of each long-history dataset, run alone."""
    per = {name: run_variant(v, ds, True, min_r) for name, ds in datasets.items()}
    hv = [run_variant(v, hds, False, min_r) for hds in halves]
    lh = {name: [run_variant(v, hds, False, min_r) for hds in pair] for name, pair in (long_halves or {}).items()}
    rows: Dict[str, Dict[str, Any]] = {}
    for label, risk in RISKS:
        dsr = {name: per[name][label] for name in datasets}
        prim = dsr.get(PRIMARY, {})
        rows[label] = {
            "name": v.name, "risk": label, "risk_frac": risk, "family": v.family, "swing": v.swing,
            "swing_def": v.swing_def, "direction": v.direction, "level": v.level, "entry": v.entry, "stop": v.stop,
            "target": v.target, "ratchet": v.ratchet, "datasets": dsr,
            "halves": [x[label]["combined"]["net_usd"] for x in hv],
            "halves_n": [x[label]["combined"]["n"] for x in hv],
            "halves_alone": [x[label]["alone"]["net_usd"] for x in hv],
            "halves_n_alone": [x[label]["alone"]["n"] for x in hv],
            "long_halves": {name: [x[label]["alone"]["net_usd"] for x in pair] for name, pair in lh.items()},
            "long_halves_n": {name: [x[label]["alone"]["n"] for x in pair] for name, pair in lh.items()},
            "drop_best_day": prim.get("drop_best_day", {"pass": False, "rest_usd": 0.0})}
    return rows


def _work(v: Variant) -> Dict[str, Dict[str, Any]]:
    return evaluate_one(v, _G["datasets"], _G["halves"], _G["min_r"], _G["long_halves"])


def evaluate(vs: Sequence[Variant], datasets: Dict[str, Dataset], halves: Sequence[Dataset], jobs: int = 1,
             min_r: float = 0.0, log: Optional[Callable[[str], None]] = None,
             long_halves: Optional[Dict[str, Sequence[Dataset]]] = None) -> Dict[str, Dict[str, Any]]:
    """Every variant at both risk sizes, judged. Rows keyed
    "<variant>@<risk>". With jobs > 1 the variants are split over forked
    workers; the result does not depend on the split."""
    _G.update(datasets=datasets, halves=list(halves), min_r=min_r, long_halves=long_halves or {})
    t0 = time.time()
    rows: Dict[str, Dict[str, Any]] = {}

    def take(i: int, got: Dict[str, Dict[str, Any]]) -> None:
        for label, row in got.items():
            rows[f"{row['name']}@{label}"] = row
        if log is not None and (i % max(1, len(vs) // 20) == 0 or i == len(vs)):
            eta = (time.time() - t0) / i * (len(vs) - i)
            log(f"{i / len(vs) * 100.0:.0f}% done, about {eta:.0f}s left; {i}/{len(vs)} variants")

    if jobs > 1 and len(vs) > 1:
        with multiprocessing.get_context("fork").Pool(jobs) as pool:
            for i, got in enumerate(pool.imap(_work, vs, chunksize=16), 1):
                take(i, got)
    else:
        for i, v in enumerate(vs, 1):
            take(i, _work(v))
    for v in vs:
        for label, _ in RISKS:
            row = rows[f"{v.name}@{label}"]
            nets = [rows[f"{nb.name}@{label}"]["datasets"][PRIMARY]["combined"]["net_usd"]
                    if f"{nb.name}@{label}" in rows and PRIMARY in rows[f"{nb.name}@{label}"]["datasets"] else None
                    for nb in neighbours(v)]
            row["neighbour_nets"] = nets
            row["judge"] = judge(row, nets)
            row["beats_all"] = all(d["combined"]["net_usd"] > EPS for d in row["datasets"].values() if "combined" in d)
            row["alone_holds"] = alone_holds(row)
    return rows


def alone_holds(row: Dict[str, Any], min_n: int = MIN_N) -> bool:
    """Does the variant make money ALONE everywhere it can be read: above
    zero on every dataset, in both halves of every long-history dataset
    that has halves, with at least `min_n` trades on each of those?"""
    lh = row.get("long_halves") or {}
    if not lh or any(d["alone"]["net_usd"] <= EPS for d in row["datasets"].values()):
        return False
    return all(row["datasets"][name]["alone"]["n"] >= min_n and all(x > EPS for x in nets)
               for name, nets in lh.items())


VERDICT_RANK = {"PASS": 0, "LEANING": 1, "FAIL": 2, "NOISE": 2}


def best_of(rows: Dict[str, Dict[str, Any]], keys: Sequence[str]) -> Optional[str]:
    """The best of `keys`: a variant that traded beside live first, a PASS,
    then a LEANING, then the highest net beside live on the primary, the
    name breaking ties. The ceiling of its grid."""
    keys = [k for k in keys if PRIMARY in rows[k]["datasets"]]
    if not keys:
        return None
    return min(keys, key=lambda k: (rows[k]["datasets"][PRIMARY]["combined"]["n"] == 0,
                                    VERDICT_RANK[rows[k]["judge"]["verdict"]],
                                    -rows[k]["datasets"][PRIMARY]["combined"]["net_usd"], k))


def _med(xs: Sequence[float]) -> Optional[float]:
    return round(statistics.median(xs), 2) if xs else None


def grid_stats(rows: Sequence[Dict[str, Any]], ds: str, mode: str) -> Dict[str, Any]:
    """A set of rows on one dataset, alone or beside live ("combined")."""
    got = [r["datasets"][ds][mode] for r in rows if ds in r["datasets"] and mode in r["datasets"][ds]]
    traded = [s for s in got if s["n"]]
    n, wins = sum(s["n"] for s in got), sum(s["wins"] for s in got)
    rs = sum(s["avg_r"] * s["n"] for s in traded if s["avg_r"] is not None)
    return {"variants": len(got), "traded": len(traded), "above_zero": sum(1 for s in traded if s["net_usd"] > EPS),
            "judged_10_plus": sum(1 for s in got if s["n"] >= MIN_N),
            "above_zero_10_plus": sum(1 for s in got if s["n"] >= MIN_N and s["net_usd"] > EPS),
            "trades": n, "wins": wins, "win_rate": round(wins / n * 100.0, 1) if n else None,
            "avg_r": round(rs / n, 3) if n else None, "net_sum_usd": round(sum(s["net_usd"] for s in got), 2),
            "net_median_usd": _med([s["net_usd"] for s in traded]),
            "armed": sum(s.get("armed", 0) for s in got)}


def summarize_families(rows: Dict[str, Dict[str, Any]], names: Sequence[str]) -> Dict[str, Any]:
    """Per family and risk size: the grid's numbers on every dataset, the
    family verdict, the best variant (the ceiling) and the reference
    variant (the textbook setup, named before the run)."""
    out: Dict[str, Any] = {}
    fams = sorted({r["family"] for r in rows.values()})
    for fam in fams:
        swing_def, direction = fam.split("/")
        per: Dict[str, Any] = {"swing_def": swing_def, "direction": direction, "label": SWING_LABEL[swing_def]}
        for label, _ in RISKS:
            keys = sorted(k for k, r in rows.items() if r["family"] == fam and r["risk"] == label)
            rs = [rows[k] for k in keys]
            fv = family_verdict([{"verdict": r["judge"]["verdict"], "n_primary": r["judge"]["n_primary"],
                                  "beats_all": r["beats_all"]} for r in rs])
            ref = f"{reference_variant(swing_def, direction).name}@{label}"
            per[label] = {
                **fv, "variants": len(keys), "best": best_of(rows, keys), "reference": ref if ref in rows else None,
                "alone": {ds: grid_stats(rs, ds, "alone") for ds in names},
                "beside": {ds: grid_stats(rs, ds, "combined") for ds in names if ds in BESIDE},
                "beats_all_beside": sum(1 for r in rs if r["beats_all"]
                                        and r["datasets"][PRIMARY]["combined"]["n"] > 0),
                "beats_all_and_both_halves": sum(1 for r in rs if r["beats_all"] and len(r["halves"]) == 2
                                                 and all(x > EPS for x in r["halves"])),
                "alone_holds": sum(1 for r in rs if r["alone_holds"]),
                "long_halves_above_zero": {name: [sum(1 for r in rs if r["long_halves"][name][k] > EPS)
                                                  for k in (0, 1)] for name in (rs[0]["long_halves"] if rs else {})},
                "axes": {ax: axis_table({f"{r['name']}@{label}": r for r in rs}, names, ax, label)
                         for ax in ("level", "entry", "stop", "target", "ratchet", "swing")},
                "collisions": {ds: {"n": sum(r["datasets"][ds]["collisions"]["n"] for r in rs),
                                    "overlap": sum(r["datasets"][ds]["collisions"]["overlap"] for r in rs)}
                               for ds in names if ds in BESIDE}}
        out[fam] = per
    return out


def level_table(rows: Dict[str, Dict[str, Any]], names: Sequence[str], risk: str = "full") -> Dict[str, Any]:
    """Per fib level, the resting-limit variants with no ratchet, alone:
    how often an order that rested was filled, and how the fills did."""
    out: Dict[str, Any] = {}
    for lv in FIBS:
        rs = [r for r in rows.values() if r["risk"] == risk and r["level"] == lv and r["entry"] == "limit"
              and r["ratchet"] == "off"]
        per = {}
        for ds in names:
            g = grid_stats(rs, ds, "alone")
            g["fill_rate_pct"] = round(g["trades"] / g["armed"] * 100.0, 1) if g["armed"] else None
            per[ds] = g
        out[f"{lv * 100:.1f}"] = per
    return out


def ratchet_table(rows: Dict[str, Dict[str, Any]], names: Sequence[str], risk: str = "full") -> Dict[str, Any]:
    """Each ratchet setting against its twin with the ratchet off (every
    other axis equal), alone: the paired change in net, and the pooled
    exits and win rate with and without."""
    by_name = {r["name"]: r for r in rows.values() if r["risk"] == risk}
    out: Dict[str, Any] = {}
    for rt in RATCHETS[1:]:
        per = {}
        for ds in names:
            deltas, on, off = [], [], []
            for r in by_name.values():
                if r["ratchet"] != rt or ds not in r["datasets"]:
                    continue
                twin = by_name.get(r["name"][:-len(rt)] + "off")
                if twin is None:
                    continue
                a, b = r["datasets"][ds]["alone"], twin["datasets"][ds]["alone"]
                if not b["n"]:
                    continue
                deltas.append(a["net_usd"] - b["net_usd"])
                on.append(r)
                off.append(twin)
            g_on, g_off = grid_stats(on, ds, "alone"), grid_stats(off, ds, "alone")
            ex_on, ex_off = Counter(), Counter()
            for r in on:
                ex_on.update(r["datasets"][ds]["alone"]["exits"])
            for r in off:
                ex_off.update(r["datasets"][ds]["alone"]["exits"])
            per[ds] = {"pairs": len(deltas), "better": sum(1 for d in deltas if d > EPS),
                       "worse": sum(1 for d in deltas if d < -EPS), "delta_median_usd": _med(deltas),
                       "delta_sum_usd": round(sum(deltas), 2),
                       "win_rate_on": g_on["win_rate"], "win_rate_off": g_off["win_rate"],
                       "avg_r_on": g_on["avg_r"], "avg_r_off": g_off["avg_r"],
                       "net_sum_on": g_on["net_sum_usd"], "net_sum_off": g_off["net_sum_usd"],
                       "exits_on": dict(sorted(ex_on.items())), "exits_off": dict(sorted(ex_off.items()))}
        out[rt] = per
    return out


def axis_table(rows: Dict[str, Dict[str, Any]], names: Sequence[str], axis: str, risk: str = "full"
               ) -> Dict[str, Any]:
    """The whole grid cut along one axis, alone."""
    vals = sorted({str(r[axis]) for r in rows.values()})
    return {val: {ds: grid_stats([r for r in rows.values() if r["risk"] == risk and str(r[axis]) == val], ds, "alone")
                  for ds in names} for val in vals}


# ── data ───────────────────────────────────────────────────────────────────

def load_funding(coin: str, start_ms: int, end_ms: int, cache_dir: str, chunk_ms: int = 20 * DAY_MS
                 ) -> List[Dict[str, Any]]:
    """Hourly funding over a long window through the cached fetcher, in
    20-day requests (the venue returns 500 rows a request)."""
    rows: List[Dict[str, Any]] = []
    a = start_ms
    while a < end_ms:
        b = min(end_ms, a + chunk_ms)
        rows += D.fetch_funding_cached(coin, a, b, cache_dir)
        a = b
    return rows


def merge_theses(replay: Sequence[D.ThesisDirection], journal: Sequence[D.ThesisDirection]
                 ) -> List[D.ThesisDirection]:
    """The replay theses written before the journal starts, then the journal."""
    if not journal:
        return list(replay)
    first = min(th.created_at_ms for th in journal)
    return sorted([th for th in replay if th.created_at_ms < first] + list(journal), key=lambda th: th.created_at_ms)


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    ap = argparse.ArgumentParser(
        description="Fibonacci retracement backtest on Brent and WTI: three swing definitions (24h range, 1h "
                    "zigzag, session since the reopen) x three directions (with the swing, thesis side, long "
                    "only), entries at 38.2/50/61.8/78.6%, three stops, three targets and the operator's stop "
                    "ratchet, each alone and beside the live long book. Research only: reads candles, the "
                    "journal and the review ledger; never trades or writes config.",
        epilog="Exit 0: a family passes the bar. 1: none does. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else ""),
        help="READ ONLY: the live oil journal's theses (default: $PATHIEL_STATE_DIR/strategies/oil/theses)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses",
                    help="READ ONLY: the 30-thesis replay set; brent_replay30 is skipped when missing")
    ap.add_argument("--ledger", default=os.path.join(state, "review", "ledger.jsonl") if state else "",
                    help="READ ONLY: the review's trade ledger, for the measured fee")
    ap.add_argument("--cache-dir", default="/tmp/fib-retrace/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/fib-retrace")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--min-r", type=float, default=0.0,
                    help="refuse an entry planned under this R (place_bracket's gate is 2; default 0 = a hand trade)")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1), help="worker processes")
    ap.add_argument("--no-wti", action="store_true", help="skip the WTI datasets")
    ap.add_argument("--op-trade", type=float, nargs=3, metavar=("ENTRY", "STOP", "TARGET"),
                    default=(92.96, 92.695, 93.709), help="the operator's own trade, costed in the report")
    ap.add_argument("--detail", nargs="*", default=[], metavar="NAME", help="variants whose trades to keep")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} fib-retrace: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        long_1h = D.fetch_candles_cached(coin, "1h", 5000, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15 = closed_only(got["c15"], FIFTEEN_MS, asof)
        c1h = closed_only(I5.merge_candles(long_1h, got["c1h"]), HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        end_h = min(c1h[-1].t + HOUR_MS, end - end % HOUR_MS)
        c1h = [b for b in c1h if b.t + HOUR_MS <= end_h]
        return c15, c1h, end, end_h, load_funding(coin, c1h[0].t, end, a.cache_dir)

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    replay = D.load_journaled_theses(a.oil_replay_dir) if a.oil_replay_dir else []
    fees = I5.measured_fees(a.ledger)
    repo = base_params(a.start_equity, RISK_FULL)
    bp = dataclasses.replace(repo, taker_fee=fees["taker"], maker_fee=fees["maker"])
    merged = merge_theses(replay, theses)

    datasets: Dict[str, Dataset] = {}
    meta: Dict[str, Any] = {}
    live_repo_fee: Dict[str, Any] = {}

    def add(name: str, ctx: Ctx, th_at, start: int, end: int, live_args=None) -> Dataset:
        run = None
        if live_args is not None:
            c15, c1h, dir_at, fund = live_args
            run = SL.prepare(c15, c1h, dir_at, th_at, bp, fund, start, end)
            live_repo_fee[name] = SL.prepare(c15, c1h, dir_at, th_at, repo, fund, start, end)["live"]
        ds = make_dataset(name, ctx, thesis_sides(ctx, th_at), bp, start, end, run)
        datasets[name] = ds
        meta[name] = {"window": [_iso(start), _iso(end)], "days": round((end - start) / DAY_MS, 1),
                      "bars": ds.hi_i - ds.lo_i + 1, "bar": "15m" if ctx.step == FIFTEEN_MS else "1h",
                      "live": ds.live_sum}
        return ds

    b15, b1h, b_end, b_end_h, b_fund = load(BRENT)
    bctx = build_ctx(b15, b1h, b_fund, FIFTEEN_MS)
    bctx_h = build_ctx(b1h, b1h, b_fund, HOUR_MS)
    start = max(min(th.created_at_ms for th in theses), b15[0].t)
    start += -start % FIFTEEN_MS
    dir_at, th_at = journaled_direction(theses), SL.thesis_lookup(theses)
    add(PRIMARY, bctx, th_at, start, b_end, (b15, b1h, dir_at, b_fund))
    mid = start + (b_end - start) // 2
    mid -= mid % FIFTEEN_MS
    halves = []
    for k, (s, e) in enumerate(((start, mid), (mid, b_end)), 1):
        run = SL.prepare(b15, b1h, dir_at, th_at, bp, b_fund, s, e)
        halves.append(make_dataset(f"half{k}", bctx, datasets[PRIMARY].thesis, bp, s, e, run))
    if replay:
        r_start = max(min(th.created_at_ms for th in replay), b15[0].t)
        r_start += -r_start % FIFTEEN_MS
        r_end = min(b_end, max(th.created_at_ms for th in replay) + DAY_MS)
        r_end -= r_end % FIFTEEN_MS
        add("brent_replay30", bctx, SL.thesis_lookup(replay), r_start, r_end,
            (b15, b1h, journaled_direction(replay), b_fund))
    else:
        log(f"brent_replay30: no theses at {a.oil_replay_dir!r}, skipped")
    m_at = SL.thesis_lookup(merged)
    add("brent_15m_50d", bctx, m_at, b15[0].t + WARMUP_15M_MS, b_end)
    add("brent_1h_208d", bctx_h, m_at, b1h[0].t + WARMUP_1H_MS, b_end_h)
    slip_check = {BRENT: round(statistics.median(market_slip(bctx, i, 0.0) for i in range(len(bctx.t))) * 1e4, 2)}
    if not a.no_wti:
        w15, w1h, w_end, w_end_h, w_fund = load(WTI)
        wctx = build_ctx(w15, w1h, w_fund, FIFTEEN_MS)
        wctx_h = build_ctx(w1h, w1h, w_fund, HOUR_MS)
        w_start, w_stop = max(start, w15[0].t), min(b_end, w_end)
        add("wti_journal", wctx, SL.thesis_lookup(theses, SL.price_ratio(theses, b1h, w1h)), w_start, w_stop,
            (w15, w1h, side_only(dir_at), w_fund))
        wm_at = SL.thesis_lookup(merged, SL.price_ratio(merged, b1h, w1h))
        add("wti_15m_50d", wctx, wm_at, w15[0].t + WARMUP_15M_MS, w_end)
        add("wti_1h_208d", wctx_h, wm_at, w1h[0].t + WARMUP_1H_MS, w_end_h)
        slip_check[WTI] = round(statistics.median(market_slip(wctx, i, 0.0) for i in range(len(wctx.t))) * 1e4, 2)

    long_halves: Dict[str, List[Dataset]] = {}
    for name in ("brent_1h_208d", "wti_1h_208d"):
        if name in datasets:
            d = datasets[name]
            m = d.start_ms + (d.end_ms - d.start_ms) // 2
            m -= m % HOUR_MS
            long_halves[name] = [make_dataset(f"{name}_h{k}", d.ctx, d.thesis, bp, s, e)
                                 for k, (s, e) in enumerate(((d.start_ms, m), (m, d.end_ms)), 1)]
            meta[name]["halves_split"] = _iso(m)
    vs = variants()
    names = list(datasets)
    log(f"{len(theses)} journal theses, {len(replay)} replay; {len(vs)} variants x {len(RISKS)} risk sizes on "
        f"{names}; halves split at {_iso(mid)}; fee {fees}; min_r {a.min_r}; jobs {a.jobs}")
    log(f"follow with: tail -f {os.path.join(a.out_dir, 'progress.log')}")
    rows = evaluate(vs, datasets, halves, a.jobs, a.min_r, log, long_halves)
    fams = summarize_families(rows, names)
    passing = sorted(f for f, d in fams.items() if d["full"]["verdict"] == "PASS")
    by_name = {v.name: v for v in vs}
    wanted = list(dict.fromkeys(
        [rows[k]["name"] for f in fams.values() for label, _ in RISKS
         for k in (f[label]["best"], f[label]["reference"]) if k] + [n for n in a.detail if n in by_name]))
    detail_rows = {f"{n}@{label}": rows[f"{n}@{label}"] for n in wanted for label, _ in RISKS}
    trades = {n: {ds: run_variant(by_name[n], d, True, a.min_r, keep=True)["full"] for ds, d in datasets.items()}
              for n in wanted}
    op = cost_trade(a.op_trade[0], a.op_trade[1], a.op_trade[2], bp.taker_fee, bp.maker_fee, bp.slippage)
    op_maker = cost_trade(a.op_trade[0], a.op_trade[1], a.op_trade[2], bp.taker_fee, bp.maker_fee, bp.slippage,
                          entry_fee=bp.maker_fee)

    csv_path = os.path.join(a.out_dir, "variants.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        head = ["variant", "risk", "family", "swing", "direction", "level", "entry", "stop", "target", "ratchet",
                "verdict"]
        for ds in names:
            modes = ("alone", "beside") if ds in BESIDE else ("alone",)
            head += [f"{ds}_{m}_{k}" for m in modes for k in
                     ("n", "wins", "net_usd", "avg_r", "pf", "max_dd_pct", "armed")]
            if ds in BESIDE:
                head += [f"{ds}_live_dd_pct", f"{ds}_overlap"]
        lh_names = list(long_halves)
        w.writerow(head + ["half1_usd", "half2_usd", "half1_n", "half2_n", "half1_alone_usd", "half2_alone_usd",
                           "rest_without_best_day_usd"]
                   + [f"{n}_half{k}_alone_usd" for n in lh_names for k in (1, 2)] + ["alone_holds"])
        for k in sorted(rows):
            r = rows[k]
            line = [r["name"], r["risk"], r["family"], r["swing"], r["direction"], r["level"], r["entry"],
                    r["stop"], r["target"], r["ratchet"], r["judge"]["verdict"]]
            for ds in names:
                d = r["datasets"][ds]
                for m in (("alone", "combined") if ds in BESIDE else ("alone",)):
                    s = d[m]
                    line += [s["n"], s["wins"], s["net_usd"], s["avg_r"], s["profit_factor"], s.get("max_dd_pct"),
                             s.get("armed")]
                if ds in BESIDE:
                    line += [d["live"]["max_dd_pct"], d["collisions"]["overlap"]]
            w.writerow(line + r["halves"] + r["halves_n"] + r["halves_alone"] + [r["drop_best_day"]["rest_usd"]]
                       + [x for n in lh_names for x in r["long_halves"][n]] + [r["alone_holds"]])
    trades_path = os.path.join(a.out_dir, "best_trades.csv")
    with open(trades_path, "w", newline="") as fh:
        fields = ["variant", "dataset", "mode", "entry", "exit", "side", "level", "entry_px", "exit_px", "stop_px",
                  "stop_final_px", "target_px", "planned_r", "leverage", "notional", "reason", "raw_usd", "slip_usd",
                  "fees_usd", "funding_usd", "net_usd", "r", "risk_pct", "hold_min"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for name, per in trades.items():
            for ds, d in per.items():
                for mode in ("alone", "combined"):
                    for tr in d.get(f"trades_{mode}", []):
                        w.writerow({"variant": name, "dataset": ds, "mode": mode, **tr})
    full = [r for r in rows.values() if r["risk"] == "full"]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "start_equity": a.start_equity, "risks": dict(RISKS), "min_n": MIN_N, "min_r": a.min_r, "datasets": meta,
        "halves_split": _iso(mid), "live_halves": [h.live_sum for h in halves], "live_at_repo_fee": live_repo_fee,
        "fees": fees, "repo_fees": {"taker": repo.taker_fee, "maker": repo.maker_fee},
        "market_slip_median_bps": slip_check, "n_variants": len(vs), "n_judged_rows": len(rows),
        "n_families": len(fams), "verdicts": {label: dict(Counter(r["judge"]["verdict"] for r in rows.values()
                                                                  if r["risk"] == label)) for label, _ in RISKS},
        "beats_all_beside": sum(1 for r in full if r["beats_all"] and r["datasets"][PRIMARY]["combined"]["n"] > 0),
        "beats_all_and_both_halves": sum(1 for r in full if r["beats_all"] and all(x > EPS for x in r["halves"])),
        "alone_holds": sorted(r["name"] for r in full if r["alone_holds"]),
        "families": fams, "passing": passing, "levels": level_table(rows, names),
        "ratchet": ratchet_table(rows, names),
        "axes": {ax: axis_table(rows, names, ax) for ax in ("entry", "stop", "target", "swing")},
        "operator_trade": {"taker_in": op, "maker_in": op_maker}, "detail": detail_rows,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    log(f"done in {time.time() - t0:.0f}s; families passing: {passing or 'none'}")
    print(f"\nreport: {out_path}\nvariants: {csv_path}\nbest trades: {trades_path}")
    return 0 if passing else 1


# ── the printed report (markdown tables) ───────────────────────────────────

def _f(x: Any, nd: int = 2, sign: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x:+,.{nd}f}" if sign else f"{x:,.{nd}f}"


def _row_line(label: str, where: str, s: Dict[str, Any], dd_live: Optional[float] = None) -> str:
    dd = "-" if s.get("max_dd_pct") is None else f"{s['max_dd_pct']}%" + (f" vs {dd_live}%" if dd_live is not None else "")
    wr = "-" if s["win_rate"] is None else f"{s['win_rate']:.0f}%"
    return (f"| {label} | {where} | {s['n']} | {wr} | {_f(s['net_usd'])} | {_f(s['avg_r'])} | "
            f"{_f(s['profit_factor'], 2, False)} | {dd} |")


def _print(rep: Dict[str, Any]) -> None:
    names = list(rep["datasets"])
    print(f"\n# Fib retrace: {rep['n_variants']} variants x {len(rep['risks'])} risk sizes, {rep['n_families']} "
          f"families, min R {rep['min_r']}, {rep['seconds']}s")
    print(f"fee {rep['fees']}; median market slip bps {rep['market_slip_median_bps']}")
    print("\n| dataset | bar | window | days | live trades | wins | net $ | max DD | live net at the sim's fee |")
    print("|---|---|---|---|---|---|---|---|---|")
    for ds, m in rep["datasets"].items():
        lv = m["live"]
        rf = rep["live_at_repo_fee"].get(ds)
        print(f"| {ds} | {m['bar']} | {m['window'][0]} to {m['window'][1]} | {m['days']} | "
              + (f"{lv['trades']} | {lv['wins']} | {_f(lv['net_usd'])} | {lv['max_dd_pct']}% | {_f(rf['net_usd'])} |"
                 if lv else "- | - | - | - | - |"))
    print(f"halves split {rep['halves_split']}: " + "; ".join(
        f"{h['trades']} trades, {_f(h['net_usd'])}" for h in rep["live_halves"]))
    print(f"verdicts {rep['verdicts']}; above zero beside live on every dataset: {rep['beats_all_beside']}; "
          f"and in both halves: {rep['beats_all_and_both_halves']}")
    for label in rep["risks"]:
        print(f"\n## Families at {label} risk")
        print("| family | variants | verdict | 10+ trades beside live | breadth | "
              + " | ".join(f"{ds} alone: trades / win% / avg R / above zero / median $" for ds in names)
              + " | " + " | ".join(f"{ds} beside: above zero / traded, median $" for ds in names if ds in BESIDE)
              + " | all beside + both halves |")
        print("|---|---|---|---|---|" + "---|" * (len(names) + sum(1 for d in names if d in BESIDE) + 1))
        for fam, f in rep["families"].items():
            g = f[label]
            alone = " | ".join(
                f"{x['trades']} / {x['win_rate']}% / {_f(x['avg_r'])} / {x['above_zero']} of {x['traded']} / "
                f"{_f(x['net_median_usd'])}" for x in (g["alone"][ds] for ds in names))
            beside = " | ".join(f"{x['above_zero']} of {x['traded']}, {_f(x['net_median_usd'])}"
                                for x in (g["beside"][ds] for ds in names if ds in BESIDE))
            print(f"| {fam} | {g['variants']} | **{g['verdict']}** {g['counts']} | {g['judged_10_plus']} | "
                  f"{g['breadth_pct']}% | {alone} | {beside} | {g['beats_all_and_both_halves']} |")
        print("\nalone everywhere (every dataset, both halves of each 208d set, 10+ trades there) / long halves above zero:")
        for fam, f in rep["families"].items():
            print(f"- {fam}: {f[label]['alone_holds']} of {f[label]['variants']}; {f[label]['long_halves_above_zero']}")
        for kind in ("reference", "best"):
            print(f"\n### {kind} variant per family, {label} risk")
            print("| variant | where | n | win % | net $ | avg R | PF | max DD |")
            print("|---|---|---|---|---|---|---|---|")
            for fam, f in rep["families"].items():
                key = f[label][kind]
                r = rep["detail"].get(key) if key else None
                if r is None:
                    continue
                first = f"{fam}: {r['name']} [{r['judge']['verdict']}]"
                for ds in names:
                    d = r["datasets"][ds]
                    print(_row_line(first, f"{ds} alone", d["alone"]))
                    first = ""
                    if "combined" in d:
                        print(_row_line("", f"{ds} beside live", d["combined"], d["live"]["max_dd_pct"]))
                print(f"| | halves beside live / alone | {r['halves_n']} / {r['halves_n_alone']} | | "
                      f"{' , '.join(_f(x) for x in r['halves'])} / {' , '.join(_f(x) for x in r['halves_alone'])} | | | |")
                for nm, nets in r["long_halves"].items():
                    print(f"| | {nm} halves alone | {r['long_halves_n'][nm]} | | {' , '.join(_f(x) for x in nets)} | | | |")
    print("\n## Fib levels (resting limit, no ratchet, alone, full risk)")
    print("| level | " + " | ".join(f"{ds}: armed / fills / fill % / win % / avg R / net sum $" for ds in names) + " |")
    print("|---|" + "---|" * len(names))
    for lv, per in rep["levels"].items():
        print(f"| {lv}% | " + " | ".join(
            f"{g['armed']} / {g['trades']} / {g['fill_rate_pct']}% / {g['win_rate']}% / {_f(g['avg_r'])} / "
            f"{_f(g['net_sum_usd'])}" for g in (per[ds] for ds in names)) + " |")
    print("\n## Ratchet against its twin with the ratchet off (alone, full risk)")
    print("| ratchet | dataset | pairs | better / worse | median change $ | sum change $ | win % off -> on | "
          "avg R off -> on | exits off | exits on |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for rt, per in rep["ratchet"].items():
        for ds in names:
            g = per[ds]
            print(f"| {rt} | {ds} | {g['pairs']} | {g['better']} / {g['worse']} | {_f(g['delta_median_usd'])} | "
                  f"{_f(g['delta_sum_usd'])} | {g['win_rate_off']}% -> {g['win_rate_on']}% | "
                  f"{_f(g['avg_r_off'])} -> {_f(g['avg_r_on'])} | {g['exits_off']} | {g['exits_on']} |")
    for ax, tab in rep["axes"].items():
        print(f"\n## By {ax} (whole grid, alone, full risk)")
        print("| " + ax + " | " + " | ".join(f"{ds}: trades / win % / avg R / above zero / median $" for ds in names) + " |")
        print("|---|" + "---|" * len(names))
        for val, per in tab.items():
            print(f"| {val} | " + " | ".join(
                f"{g['trades']} / {g['win_rate']}% / {_f(g['avg_r'])} / {g['above_zero']} of {g['traded']} / "
                f"{_f(g['net_median_usd'])}" for g in (per[ds] for ds in names)) + " |")
    longs = [ds for ds in names if ds in ALONE_ONLY]
    for sd in sorted({f["swing_def"] for f in rep["families"].values()}):
        fams = {k: f for k, f in rep["families"].items() if f["swing_def"] == sd}
        for ax in ("level", "entry", "stop", "target", "ratchet", "swing"):
            print(f"\n## Swing {sd} by {ax}, per direction (alone, full risk): trades / win % / avg R / above zero / median $")
            print("| direction | " + ax + " | " + " | ".join(longs) + " |")
            print("|---|---|" + "---|" * len(longs))
            for fam, f in fams.items():
                for val, per in f["full"]["axes"][ax].items():
                    print(f"| {f['direction']} | {val} | " + " | ".join(
                        f"{g['trades']} / {g['win_rate']}% / {_f(g['avg_r'])} / {g['above_zero']} of {g['traded']} / "
                        f"{_f(g['net_median_usd'])}" for g in (per[ds] for ds in longs)) + " |")
    print(f"\nalone everywhere: {len(rep['alone_holds'])} variants: {rep['alone_holds'][:40]}")
    print(f"\noperator trade: {rep['operator_trade']}")


if __name__ == "__main__":
    raise SystemExit(main())
