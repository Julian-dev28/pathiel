"""Long ladder, short-to-entry and tiered short ladder test, 2026-11-16.
Research only.

Operator, 2026-11-15 (Brent reopened near 92.7, the desk waiting to buy
92.48 or lower and price sitting above it): "research shorting just to
meet entry price! also if its always shorts failed, then test a small
higher entry say, a tiered risk ladder, if +1% entry, risk 20% if eod
candle close, etc". Clarified the same evening: the ladder meant is a LONG
one, so the desk still takes part when price runs above its entry and
never dips back.

Four questions, one deterministic script, no LLM call, no order, no
config write.

D. Long ladder above the entry ("long" variants, the main test). While a
   LONG thesis is in force, tier j buys at thesis entry x (1 + first +
   j x 0.5%): first tier +0.5 / +1 / +1.5 / +2%, 2 or 3 tiers. Risk per
   tier as a share of the NORMAL per-trade risk (6% of equity): "risk
   20%" is read as 20% of that budget, 1.2% of equity. Schemes: 20% /
   40% / 60% on every tier, or a ramp 20 -> 40 -> 60. Confirmation:
   "touch" (a buy stop at the tier), or a daily CLOSE above the tier
   before buying the next open ("eod00" = the venue's 1d candle, 00:00Z;
   "eod21" = 21:00Z, the futures settlement hour). Stop: the live rule's
   (0.75 under the 24h low), or 1.5 / 2.0 x the 1h ATR14 under the first
   fill (the structural stop is usually too wide for 15x from up there).
   Target: the thesis's first target, or the live rule's (0.5% inside the
   24h high). Minimum R per tier 0 / 1 / 2. One ladder per thesis. Also
   counted: the theses the live rule MISSED (direction right, entry never
   touched) and how many the ladder traded.

A. Short to the entry ("entry" variants). While a LONG thesis is in force
   and the mid sits above its entry (the live rule refuses the long there:
   "never chase"), open a SHORT at the mid whose target is the thesis
   entry, the level the desk plans to buy. Grid: the stop (0.75 above the
   24h high, the live long's stop mirrored; or 1.0 / 1.5 / 2.0 x ATR14
   above the mid), a minimum planned R (0 / 1 / 2), one short per thesis
   or re-entry allowed, and full or 20% risk.

B. Tiered short ladder ("ladder" variants). No short at the market: tier k
   sells only at reference x (1 + k x step). Reference: the long thesis's
   entry, or the 24h volume-weighted price (VWAP of the last 24 closed 1h
   bars). Steps +0.5 / +1 / +1.5 / +2%, 2 or 3 tiers, the same risk
   schemes and confirmations as D. One stop for the whole ladder, one
   step above the last tier; target back at the reference.

C. Fit with the live long book. The account holds one position per desk.
   Every variant runs twice: alone (its own equity), and "combined": no
   new position while the live long is open, an open one closed at the
   tick the live long enters. Collisions are counted on the alone run.

E. Long ladder sized in EQUITY ("equity" variants; operator: "test the
   tiered entry 20% equity at .5%+ 10% equity at 1%+"). Tier 1 at +0.5%
   over the thesis entry posts 20% of desk equity as margin, tier 2 at +1%
   another 10%, each times the fitted leverage (20% at 20x is a notional
   of about 4x equity). The stop does not enter the size, so the report
   says what each tier really risks at its stop in % of equity. Same
   stops, targets, confirmations and R gates as D; margin schemes 10/5,
   20/10, 30/15.

Realistic weekend-reopen fills (second pass, every study). The first
pass fills a reopen-tick entry at the 15m bar's open print. The desk
cannot: its cron runs at :03/:18/:33/:48 and a tick takes a minute or
two. `reopen_fills` moves that fill to the open of the 5m bar five
minutes after the reopen, with half that bar's high-low range as
slippage (measured on 5m candles, not the book-impact curve, which was
walked on a weekday book); where the venue no longer serves 5m candles
(only its last 5,000) the fill is the next 15m tick at the median
measured slippage. That pass is judged on the five older legs: nothing
is removed, so the no-reopen leg has nothing to do.

The live long book is `desk_tick_sim.simulate` with
`trend_gate_test.base_params` (the live Brent rule, 6% risk, the 20/15
leverage fit, size slippage), the same baseline the fusion sweep judges
against. The walkers below reuse the sim's fee and slippage constants,
`risk_capped_notional`, `_full_size_notional`, `next_trade.fit_leverage`
(20x, 15x on a wide stop), the weekend flat and the funding rows.
Datasets: the live oil journal on Brent (primary), the 30-thesis replay
on Brent when its directory exists, and the journal on WTI (the thesis
entry and target carried over by the WTI/Brent price ratio at the
thesis's own hour).

The bar (RULE_FUSION_2026-11-13 / FINAL_STRATEGY_TEST_2026-11-04, the
strict reading): combined with the live book the variant must beat live
on EVERY dataset, with max drawdown no worse on every dataset, both
halves of the primary window positive (walk-forward), every neighbour on
its grid positive (plateau), and the gain must survive dropping its best
exit day. One leg is this test's own: the gain must also stand without
the trades entered on the first tick after a weekend, whose fill is the
perp's stale weekend price. Fewer than 10 trades on the primary is never
PASS or FAIL: LEANING when every money leg holds, NOISE otherwise.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.short_ladder_test \\
        --cache-dir <dir> --out-dir <dir>

Reads candles through the eval disk cache and the venue's public /info.
Exit 0 when a variant passes the bar, 1 when none does, 2 when the journal
is missing.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import (
    Params, Run, _full_size_notional, _funding_rate_at, _levels, _market_exit_slip, offset_target, risk_capped_notional,
    simulate, weekend_closed)
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.stop_cap_test import _iso, closed_only
from services.strategies.evals.trend_gate_test import base_params, side_only
from services.strategies.evals.winner_traits_test import summarize

START = 182.09                 # the fusion sweep's start equity, so the live baseline compares
RISK_FRAC = 0.06               # the desk's normal per-trade risk (risk_per_trade_pct 6 since 2026-11-05)
BRENT = NT.BRENT
WTI = "xyz:CL"
FIFTEEN_MS = 900_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
MIN_N = 10                     # under this many trades a variant is LEANING or NOISE, never PASS or FAIL
EPS = 1e-9
PRIMARY = "brent_journal"

A_STOPS = ("hi24", "atr1.0", "atr1.5", "atr2.0")
A_ATR_STOPS = A_STOPS[1:]      # the ordered part of the stop axis (hi24 has no neighbour)
A_MIN_R = (0.0, 1.0, 2.0)
A_RISK = (1.0, 0.2)
B_REFS = ("thesis", "vwap24")
B_STEPS = (0.005, 0.01, 0.015, 0.02)
B_TIERS = (2, 3)
B_SCHEMES: Dict[str, Tuple[float, ...]] = {"flat20": (0.2, 0.2, 0.2), "flat40": (0.4, 0.4, 0.4),
                                           "flat60": (0.6, 0.6, 0.6), "ramp": (0.2, 0.4, 0.6)}
B_FLAT = ("flat20", "flat40", "flat60")
EOD_HOURS = {"eod00": 0, "eod21": 21}     # UTC hour of the daily close a tier needs
B_CONFIRMS = ("touch", "eod00", "eod21")
D_FIRST = B_STEPS             # D: the first long tier, this far above the thesis entry
D_GAP = 0.005                 # D: each further tier another 0.5% up (+1 / +1.5 / +2%)
D_TARGETS = ("thesis", "hi24")
D_STOPS = ("lo24", "atr1.5", "atr2.0")   # the live rule's structural stop, or N x ATR14 under the first fill
D_ATR_STOPS = D_STOPS[1:]
D_MIN_R = (0.0, 1.0, 2.0)
# E: the long ladder sized in EQUITY, not risk. Margin per tier as a share of
# desk equity: tier 1 at +0.5% over the thesis entry, tier 2 at +1%.
E_SCHEMES: Dict[str, Tuple[float, ...]] = {"eq10_5": (0.10, 0.05), "eq20_10": (0.20, 0.10), "eq30_15": (0.30, 0.15)}
E_ORDER = tuple(E_SCHEMES)
E_FIRST, E_TIERS = 0.005, 2
E_PRIMARY = "E_eq20_10_touch_thesis_lo24_r0"      # the operator's words: 20% equity at +0.5%, 10% at +1%
FIVE_MS = 300_000
REOPEN_DELAY_MS = FIVE_MS      # the desk cron runs at :03/:18/:33/:48 and a tick takes 1-2 min: first real fill ~5 min in


# ── variants (pure) ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    kind: str                     # "long" (D) | "equity" (E, long ladder sized in equity) | "entry" (A) | "ladder" (B)
    stop: str = "hi24"            # A: hi24 | atr1.0 | atr1.5 | atr2.0; D: lo24 | atr1.5 | atr2.0
    min_r: float = 0.0            # A, D: planned R the trade (D: each tier) must pay at its fill
    reentry: bool = False         # A: another short under the same thesis after one closed
    risk_scale: float = 1.0       # A: share of the normal risk
    ref: str = "thesis"           # B: thesis | vwap24
    step: float = 0.01            # B: distance between tiers; D: the first tier above the thesis entry
    tiers: int = 2                # B: 2 | 3
    scheme: str = "flat20"        # B: risk per tier (B_SCHEMES)
    confirm: str = "touch"        # B, D: touch | eod00 | eod21
    target: str = "thesis"        # D: thesis (its first target) | hi24 (the live rule: 0.5% inside the 24h high)

    @property
    def risk(self) -> Tuple[float, ...]:
        """Share of the normal per-trade risk each tier takes."""
        if self.kind == "entry":
            return (self.risk_scale,)
        if self.kind == "equity":
            return E_SCHEMES[self.scheme]          # shares of EQUITY used as margin, not shares of risk
        return B_SCHEMES[self.scheme][:self.tiers]

    @property
    def name(self) -> str:
        if self.kind == "entry":
            return (f"A_{self.stop}_r{self.min_r:g}_{'re' if self.reentry else 'once'}"
                    f"_risk{round(self.risk_scale * 100)}")
        if self.kind == "equity":
            return f"E_{self.scheme}_{self.confirm}_{self.target}_{self.stop}_r{self.min_r:g}"
        if self.kind == "long":
            return (f"D_f{self.step * 100:g}_t{self.tiers}_{self.scheme}_{self.confirm}_{self.target}"
                    f"_{self.stop}_r{self.min_r:g}")
        return f"B_{self.ref}_s{self.step * 100:g}_t{self.tiers}_{self.scheme}_{self.confirm}"


def entry_variants() -> List[Variant]:
    return [Variant("entry", stop=s, min_r=r, reentry=re, risk_scale=k)
            for s in A_STOPS for r in A_MIN_R for re in (False, True) for k in A_RISK]


def ladder_variants() -> List[Variant]:
    return [Variant("ladder", ref=ref, step=st, tiers=n, scheme=sc, confirm=cf)
            for ref in B_REFS for st in B_STEPS for n in B_TIERS for sc in B_SCHEMES for cf in B_CONFIRMS]


def long_variants() -> List[Variant]:
    return [Variant("long", step=f, tiers=n, scheme=sc, confirm=cf, target=tg, stop=st, min_r=r)
            for f in D_FIRST for n in B_TIERS for sc in B_SCHEMES for cf in B_CONFIRMS for tg in D_TARGETS
            for st in D_STOPS for r in D_MIN_R]


def equity_variants() -> List[Variant]:
    return [Variant("equity", step=E_FIRST, tiers=E_TIERS, scheme=sc, confirm=cf, target=tg, stop=st, min_r=r)
            for sc in E_SCHEMES for cf in B_CONFIRMS for tg in D_TARGETS for st in D_STOPS for r in D_MIN_R]


def _shift(levels: Sequence[Any], value: Any) -> List[Any]:
    if value not in levels:
        return []
    i = list(levels).index(value)
    return [levels[j] for j in (i - 1, i + 1) if 0 <= j < len(levels)]


def neighbours(v: Variant) -> List[Variant]:
    """The same variant with ONE ordered axis moved one step: A's ATR stop
    multiple and minimum R; B's step, tier count and flat risk share; D's
    first tier, tier count, flat risk share, minimum R and ATR stop
    multiple. The unordered choices (hi24 / lo24, reference, confirmation,
    re-entry, ramp, target) have no neighbour."""
    out: List[Variant] = []
    if v.kind == "equity":      # E: the margin scheme, minimum R and ATR stop multiple
        return ([dataclasses.replace(v, scheme=sc) for sc in _shift(E_ORDER, v.scheme)]
                + [dataclasses.replace(v, min_r=r) for r in _shift(D_MIN_R, v.min_r)]
                + [dataclasses.replace(v, stop=st) for st in _shift(D_ATR_STOPS, v.stop)])
    if v.kind == "entry":
        out += [dataclasses.replace(v, stop=s) for s in _shift(A_ATR_STOPS, v.stop)]
        out += [dataclasses.replace(v, min_r=r) for r in _shift(A_MIN_R, v.min_r)]
    else:
        out += [dataclasses.replace(v, step=s) for s in _shift(B_STEPS, v.step)]
        out += [dataclasses.replace(v, tiers=n) for n in _shift(B_TIERS, v.tiers)]
        out += [dataclasses.replace(v, scheme=s) for s in _shift(B_FLAT, v.scheme)]
        if v.kind == "long":
            out += [dataclasses.replace(v, min_r=r) for r in _shift(D_MIN_R, v.min_r)]
            out += [dataclasses.replace(v, stop=st) for st in _shift(D_ATR_STOPS, v.stop)]
    return out


# ── sizing and trigger math (pure) ─────────────────────────────────────────

def tier_risk_frac(share: float, risk_frac: float = RISK_FRAC) -> float:
    """Equity fraction one tier may lose at the stop: `share` of the normal
    per-trade risk. "Risk 20%" = 0.2 x 6% = 1.2% of equity."""
    return share * risk_frac


def tier_levels(ref: float, step: float, n: int) -> List[float]:
    """The n sell levels: reference x (1 + k x step), k = 1..n."""
    return [ref * (1.0 + k * step) for k in range(1, n + 1)]


def ladder_stop(ref: float, step: float, n: int) -> float:
    """One stop for the whole ladder, one step above the last tier."""
    return ref * (1.0 + (n + 1) * step)


def long_levels(ref: float, first: float, n: int, gap: float = D_GAP) -> List[float]:
    """D's n buy levels above the thesis entry: +first, then +gap each."""
    return [ref * (1.0 + first + j * gap) for j in range(n)]


def long_stop(rule: str, px: float, lo24: float, atr: float, buffer: float) -> float:
    """D's stop for a first fill at `px`. "lo24": the live long rule's,
    `buffer` under the 24h low (under the fill when price is already below
    it). "atrN": N x the 1h ATR14 under the fill, a stop that fits 20x
    where the structural one usually does not from this far above the low."""
    if rule == "lo24":
        return min(lo24, px) - buffer
    if rule.startswith("atr"):
        return px - float(rule[3:]) * atr
    raise ValueError(f"unknown stop rule {rule!r}")


def long_tier_fill(confirm: str, level: float, t_ms: int, o: float, h: float,
                   close_1h: Optional[float]) -> Optional[float]:
    """The price a D tier buys at on this 15m bar, else None. Always a
    taker. "touch": a buy stop at the level, filled at the level when the
    bar's high reaches it; a bar that opens at or above it buys the open.
    eod modes: the same daily-close rule as `tier_fill`."""
    if confirm == "touch":
        if o >= level:
            return o
        return level if h >= level else None
    got = tier_fill(confirm, level, t_ms, o, h, close_1h)
    return got[0] if got is not None else None


def reopen_tick(t_ms: int, step_ms: int = FIFTEEN_MS) -> bool:
    """The first tick after the oil weekend: the bar before it was inside
    the closed window. Its open is the perp's weekend price, seconds before
    the futures reprice it; a fill there is not a fill the desk gets."""
    return weekend_closed(t_ms - step_ms) and not weekend_closed(t_ms)


def tier_notional(equity: float, params: Params, leverage: Optional[float], entry: float, stop: float,
                  share: float, open_notional: float = 0.0, risk_frac: float = RISK_FRAC) -> float:
    """The notional one tier opens: the desk's own sizing
    (`risk_capped_notional`: price to the stop, the taker fee both ways and
    the stop slippage cost the tier's risk share of equity), capped by what
    is left of the full-port size at this leverage after the tiers already
    open. Never negative."""
    p = dataclasses.replace(params, risk_cap_frac=tier_risk_frac(share, risk_frac))
    want = risk_capped_notional(equity, p, leverage, entry, stop)
    room = _full_size_notional(equity, params, leverage) - open_notional
    return max(0.0, min(want, room))


def equity_tier_notional(equity: float, params: Params, leverage: Optional[float], share: float,
                         open_notional: float = 0.0) -> float:
    """E's tier size: `share` of equity posted as margin (net of the opening
    taker fee, as place_bracket does) times the position's leverage, capped
    by what is left of the full-port size. 20% of equity at 20x is a
    notional of about 4x equity. The stop does not enter the size; the
    risk is whatever that notional loses at the stop (`stop_risk_pct`)."""
    lev = leverage or params.leverage
    margin = share * equity / (1.0 + lev * params.taker_fee)
    room = _full_size_notional(equity, params, lev) - open_notional
    return max(0.0, min(margin * lev, room))


def stop_risk_pct(notional: float, entry: float, stop: float, equity: float, params: Params) -> float:
    """What a tier loses at its stop, percent of equity: price to the stop,
    the taker fee both ways and the stop slippage, the same per-dollar cost
    `risk_capped_notional` sizes the live trade by."""
    if equity <= 0 or entry <= 0:
        return 0.0
    return notional * (abs(entry - stop) / entry + 2.0 * params.taker_fee + params.slippage) / equity * 100.0


def reopen_fills(c15: Sequence[Candle], c5: Sequence[Candle], floor_slip: float,
                 delay_ms: int = REOPEN_DELAY_MS) -> Dict[int, Dict[str, Any]]:
    """The fill the desk really gets on the first tick after a weekend, per
    reopen tick t: {"px", "high", "low", "slip", "source", "open_px"}.

    The cron runs at :03/:18/:33/:48 and a tick takes a minute or two, so
    the earliest order lands about five minutes after the reopen, not on
    the reopen bar's open print. Measured ("5m", where the venue still
    serves 5m candles, its last 5,000): the mid is the OPEN of the 5m bar
    at t + 5 min, the rest of the 15m bar is the high/low of the 5m bars
    from there on, and the slippage is half that 5m bar's high-low range
    (the uncertainty of where in it a market order fills), never under
    `floor_slip`. Older reopens ("next_tick"): the order is taken at the
    15m bar's close, i.e. the next tick, with the median measured slippage
    of this coin. Pure."""
    five = {c.t: c for c in c5}
    reopens = [b for b in sorted(c15, key=lambda c: c.t) if reopen_tick(b.t)]
    measured: Dict[int, Dict[str, Any]] = {}
    for b in reopens:
        first = five.get(b.t + delay_ms)
        if first is None or first.o <= 0:
            continue
        rest = [c for c in (five.get(b.t + delay_ms + k * FIVE_MS) for k in range((FIFTEEN_MS - delay_ms) // FIVE_MS))
                if c is not None]
        measured[b.t] = {"px": first.o, "high": max(c.h for c in rest), "low": min(c.l for c in rest),
                         "slip": max(floor_slip, (first.h - first.l) / first.o / 2.0), "source": "5m", "open_px": b.o}
    fallback = statistics.median(m["slip"] for m in measured.values()) if measured else floor_slip
    out: Dict[int, Dict[str, Any]] = {}
    for b in reopens:
        out[b.t] = measured.get(b.t) or {"px": b.c, "high": b.c, "low": b.c, "slip": fallback, "source": "next_tick",
                                         "open_px": b.o}
    return out


def apply_reopen(ticks: Sequence["Tick"], fills: Dict[int, Dict[str, Any]]) -> List["Tick"]:
    """`ticks` with every reopen tick repriced to its realistic fill: the
    mid, high and low a walker sees are those of `reopen_fills`, and market
    fills on that tick pay at least its slippage. Other ticks untouched."""
    out: List[Tick] = []
    for tk in ticks:
        f = fills.get(tk.t) if tk.reopen else None
        out.append(dataclasses.replace(tk, o=f["px"], h=f["high"], l=f["low"], slip=f["slip"]) if f else tk)
    return out


def waiting_for_dip(side: Optional[str], mid: float, ceiling: Optional[float]) -> bool:
    """The desk holds a LONG thesis and the mid is above its entry: the live
    rule refuses the long here ("never chase") and waits."""
    return side == "long" and ceiling is not None and mid > ceiling


def entry_stop(rule: str, mid: float, hi24: float, atr: float, buffer: float) -> float:
    """A's stop. "hi24": `buffer` above the 24h high (above the mid when
    price is already over it), the live long's structural stop mirrored.
    "atrN": N x the 1h ATR14 above the mid."""
    if rule == "hi24":
        return max(hi24, mid) + buffer
    if rule.startswith("atr"):
        return mid + float(rule[3:]) * atr
    raise ValueError(f"unknown stop rule {rule!r}")


def planned_r(entry: float, stop: float, target: float) -> float:
    """Reward over risk of a short from the three prices, before costs."""
    return (entry - target) / (stop - entry) if stop > entry else 0.0


def is_eod(t_ms: int, hour_utc: int) -> bool:
    """`t_ms` is exactly the daily close at `hour_utc`:00Z."""
    return t_ms % DAY_MS == hour_utc * HOUR_MS


def tier_fill(confirm: str, level: float, t_ms: int, o: float, h: float,
              close_1h: Optional[float]) -> Optional[Tuple[float, bool]]:
    """(price, is_maker) when a tier at `level` fills on this 15m bar, else
    None. "touch": a resting limit sell, filled at the level when the bar's
    high reaches it (maker); a bar that OPENS at or above the level sells
    the open as a taker. "eod00"/"eod21": only on the bar that opens at
    that daily close, only when the 1h bar that just closed did so strictly
    ABOVE the level; the fill is that bar's open, as a taker. An intraday
    spike over the level is not a signal in the eod modes."""
    if confirm == "touch":
        if o >= level:
            return o, False
        if h >= level:
            return level, True
        return None
    if confirm not in EOD_HOURS:
        raise ValueError(f"unknown confirmation {confirm!r}")
    if is_eod(t_ms, EOD_HOURS[confirm]) and close_1h is not None and close_1h > level:
        return o, False
    return None


def vwap(bars: Sequence[Candle]) -> Optional[float]:
    """Volume-weighted typical price ((h+l+c)/3) of `bars`; the plain mean
    when the bars carry no volume; None when empty."""
    if not bars:
        return None
    vol = sum(b.v for b in bars)
    if vol <= 0:
        return sum((b.h + b.l + b.c) / 3.0 for b in bars) / len(bars)
    return sum((b.h + b.l + b.c) / 3.0 * b.v for b in bars) / vol


# ── per-tick context ───────────────────────────────────────────────────────

@dataclass(slots=True)
class Tick:
    t: int
    o: float
    h: float
    l: float
    c: float
    ok: bool = False                   # 24 closed 1h bars behind this tick
    hi24: float = 0.0
    lo24: float = 0.0
    atr: float = 0.0
    vwap24: Optional[float] = None
    close_1h: Optional[float] = None   # the close of the 1h bar that closes exactly at t
    weekend: bool = False
    side: Optional[str] = None         # the thesis in force: long | short | flat | None
    ceiling: Optional[float] = None    # a long thesis's entry (the planned buy level)
    thesis_id: Optional[str] = None
    fund: Optional[float] = None       # the funding rate settling at t
    target: Optional[float] = None     # a long thesis's first target
    invalid: Optional[float] = None    # its 1h-close invalidation price
    reopen: bool = False               # the first tick after the weekend
    slip: float = 0.0                  # minimum slippage for a market fill on this tick (realistic reopen fills)


ThesisAt = Callable[[int], Optional[Tuple[str, Optional[float], str, Optional[float], Optional[float]]]]


def thesis_lookup(theses: Sequence[D.ThesisDirection], scale: Optional[Dict[int, float]] = None) -> ThesisAt:
    """t -> (side, long entry or None, thesis id, first target, invalidation)
    of the thesis in force. `scale` (thesis created_at_ms -> factor) carries
    a Brent-priced entry and target to another instrument (a thesis with no
    factor has neither there); the invalidation price is dropped there, as
    `trend_gate_test.side_only` drops it for the live book."""
    ordered = sorted(theses, key=lambda th: th.created_at_ms)
    times = [th.created_at_ms for th in ordered]

    def at(t: int) -> Optional[Tuple[str, Optional[float], str, Optional[float], Optional[float]]]:
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return None
        th = ordered[i]
        ceiling: Optional[float] = None
        target: Optional[float] = None
        invalid = th.invalidation_px
        if th.side == "long":
            k = 1.0 if scale is None else scale.get(th.created_at_ms)
            if k and th.entry_px:
                ceiling = float(th.entry_px) * k
            if k and th.targets:
                target = float(th.targets[0]) * k
            if scale is not None:
                invalid = None
        return th.side, ceiling, str(th.created_at_ms), target, invalid
    return at


def price_ratio(theses: Sequence[D.ThesisDirection], base_1h: Sequence[Candle],
                other_1h: Sequence[Candle]) -> Dict[int, float]:
    """thesis created_at_ms -> other/base price, each the last 1h close at
    or before the thesis was written."""
    def last_close(bars: Sequence[Candle], times: List[int], t: int) -> Optional[float]:
        i = bisect.bisect_right(times, t) - 1
        return bars[i].c if i >= 0 else None
    b = sorted(base_1h, key=lambda c: c.t)
    o = sorted(other_1h, key=lambda c: c.t)
    bt, ot = [c.t + HOUR_MS for c in b], [c.t + HOUR_MS for c in o]
    out: Dict[int, float] = {}
    for th in theses:
        pb, po = last_close(b, bt, th.created_at_ms), last_close(o, ot, th.created_at_ms)
        if pb and po:
            out[th.created_at_ms] = po / pb
    return out


def build_ticks(c15: Sequence[Candle], c1h: Sequence[Candle], thesis_at: ThesisAt,
                funding: Optional[Sequence[Dict[str, Any]]] = None) -> List[Tick]:
    """One Tick per 15m bar, with everything a variant reads off closed data
    only: the 24h high, ATR14 and VWAP of the 1h bars closed at or before
    the tick, the thesis in force, the weekend flag, the funding row."""
    bars = sorted(c15, key=lambda c: c.t)
    hours = sorted(c1h, key=lambda c: c.t)
    by_close = {c.t + HOUR_MS: c for c in hours}
    fund_sorted = sorted((int(r["time"]), float(r["fundingRate"])) for r in (funding or [])
                         if r.get("time") is not None and r.get("fundingRate") is not None)
    out: List[Tick] = []
    n_closed, cached_n = 0, -1
    lv: Optional[Dict[str, Any]] = None
    vw: Optional[float] = None
    for bar in bars:
        while n_closed < len(hours) and hours[n_closed].t + HOUR_MS <= bar.t:
            n_closed += 1
        if n_closed != cached_n:
            window = hours[max(0, n_closed - 24):n_closed]
            lv = _levels(window, 24, 24)
            vw = vwap(window) if len(window) >= 24 else None
            cached_n = n_closed
        th = thesis_at(bar.t)
        c1 = by_close.get(bar.t)
        tk = Tick(t=bar.t, o=bar.o, h=bar.h, l=bar.l, c=bar.c, weekend=weekend_closed(bar.t),
                  reopen=reopen_tick(bar.t),
                  close_1h=c1.c if c1 is not None else None,
                  fund=_funding_rate_at(fund_sorted, bar.t) if c1 is not None else None)
        if lv is not None and lv["target_high"] is not None:
            tk.ok, tk.hi24, tk.lo24, tk.atr, tk.vwap24 = True, lv["target_high"], lv["target_low"], lv["atr14"], vw
        if th is not None:
            tk.side, tk.ceiling, tk.thesis_id, tk.target, tk.invalid = th
        out.append(tk)
    return out


# ── the short walker ───────────────────────────────────────────────────────

def _fill(pos: Dict[str, Any], k: int, px: float, notional: float, fee_rate: float, t: int,
          risk_pct: Optional[float] = None) -> float:
    """Add one tier to the position; returns the entry fee paid."""
    if risk_pct is not None:
        pos.setdefault("risk_pcts", []).append(round(risk_pct, 2))
    size = notional / px
    total = pos["size"] + size
    pos["avg"] = (pos["avg"] * pos["size"] + px * size) / total
    pos["size"] = total
    fee = fee_rate * px * size
    pos["fees"] += fee
    pos["risk"] += abs(pos["stop"] - px) * size
    pos["tiers"].append(k)
    pos["last_fill"] = t
    return fee


def _close(pos: Dict[str, Any], exit_px: float, t: Optional[int], reason: str, fee_rate: float) -> Dict[str, Any]:
    gross = pos.get("sign", -1.0) * (exit_px - pos["avg"]) * pos["size"]
    exit_fee = fee_rate * exit_px * pos["size"]
    net = gross - pos["fees"] - exit_fee - pos["funding"]
    return {"entry_ms": pos["opened"], "exit_ms": t, "entry": _iso(pos["opened"]), "exit": _iso(t),
            "entry_px": round(pos["avg"], 4), "exit_px": round(exit_px, 4), "stop_px": round(pos["stop"], 4),
            "target_px": round(pos["target"], 4), "size": pos["size"], "tiers": len(pos["tiers"]),
            "leverage": pos["lev"], "reason": reason, "gross_usd": gross, "exit_fee_usd": exit_fee,
            "fees_usd": pos["fees"] + exit_fee, "funding_usd": pos["funding"], "net_usd": net,
            "risk_usd": pos["risk"], "r": net / pos["risk"] if pos["risk"] > 1e-12 else None,
            "thesis_id": pos["thesis_id"], "reopen": bool(pos.get("reopen")),
            "tier_risk_pct": list(pos.get("risk_pcts", []))}


def walk(ticks: Sequence[Tick], v: Variant, params: Params, start_equity: float,
         long_curve: Optional[Sequence[float]] = None, long_open: Optional[Sequence[bool]] = None,
         long_entry: Optional[Sequence[bool]] = None, risk_frac: float = RISK_FRAC) -> Dict[str, Any]:
    """Run one short variant over `ticks`. Alone (no `long_curve`): sized off
    `start_equity` plus its own P&L. Combined (`long_curve` = the live long
    book's marked equity per tick, `long_open`/`long_entry` its masks): no
    new short or tier while the long is open, an open short bought back at
    the open of the tick the long enters, sized off the long book's equity
    plus the shorts' own P&L.

    Per 15m bar, in order: funding on a 1h close; closes at the open (long
    entry, weekend flat, and for A the thesis no longer long or the mid at
    or under its entry); new entries / tier fills; then the stop (a bar that
    opens above it fills at the open) and the target (a resting limit, a
    trade-through, maker fee; never on a bar the position was filled on).
    A position open at the end is marked at the last close, no exit fee.

    Returns {"trades", "curve" (the shorts' marked P&L per tick), "refused"}."""
    combined = long_curve is not None
    pnl = 0.0
    pos: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    refused: Counter = Counter()
    curve: List[float] = []
    done_ids: set = set()
    armed = True
    last_exit_t = -1
    n_tiers = len(v.risk)
    cands = params.entry_fit_leverage or ()
    is_entry = v.kind == "entry"

    def buy_back(px: float) -> float:
        return px * (1.0 + _market_exit_slip(params, pos["size"] * px))

    def book(row: Dict[str, Any]) -> None:
        nonlocal pnl, pos, last_exit_t, armed
        pnl += row["gross_usd"] - row["exit_fee_usd"]
        trades.append(row)
        last_exit_t = row["exit_ms"]
        if row["reason"] == "stop" and not is_entry:
            armed = False               # the ladder re-arms only once price is back at the reference
        pos = None

    for i, tk in enumerate(ticks):
        t, mid = tk.t, tk.o
        # 1. funding (a short receives a positive rate)
        if pos is not None and tk.fund and tk.close_1h is not None:
            pay = -tk.fund * pos["size"] * tk.close_1h
            pnl -= pay
            pos["funding"] += pay
        # 2. closes at the open
        if pos is not None and pos["opened"] < t:
            reason = None
            if combined and long_entry[i]:
                reason = "long_entry"
            elif tk.weekend:
                reason = "weekend"
            elif is_entry and (tk.side != "long" or tk.ceiling is None):
                reason = "thesis_change"
            elif is_entry and mid <= tk.ceiling:
                reason = "at_entry"
            if reason is not None:
                book(_close(pos, buy_back(mid), t, reason, params.taker_fee))
        # 3. entries
        if tk.ok and not tk.weekend and t > last_exit_t and not (combined and long_open[i]):
            eq = (long_curve[i] if combined else start_equity) + pnl
            if is_entry:
                if pos is None and waiting_for_dip(tk.side, mid, tk.ceiling) \
                        and (v.reentry or tk.thesis_id not in done_ids):
                    stop = entry_stop(v.stop, mid, tk.hi24, tk.atr, params.stop_buffer)
                    lev, gate = NT.fit_leverage(mid, stop, params.leverage, params.max_leverage, cands)
                    if planned_r(mid, stop, tk.ceiling) < v.min_r - EPS:
                        refused["min_r"] += 1
                    elif not gate.get("ok"):
                        refused["leverage"] += 1
                    else:
                        notional = tier_notional(eq, params, lev, mid, stop, v.risk[0], 0.0, risk_frac)
                        px = mid * (1.0 - max(_market_exit_slip(params, notional), tk.slip))
                        pos = {"opened": t, "avg": 0.0, "size": 0.0, "stop": stop, "target": tk.ceiling, "ref": tk.ceiling,
                               "lev": lev, "fees": 0.0, "funding": 0.0, "risk": 0.0, "tiers": [], "last_fill": t,
                               "thesis_id": tk.thesis_id, "reopen": tk.reopen}
                        pnl -= _fill(pos, 0, px, notional, params.taker_fee, t)
                        done_ids.add(tk.thesis_id)
            else:
                if pos is not None:
                    ref: Optional[float] = pos["ref"]
                else:
                    ref = (tk.ceiling if tk.side == "long" else None) if v.ref == "thesis" else tk.vwap24
                if ref is not None and (pos is not None or armed):
                    stop = ladder_stop(ref, v.step, n_tiers)
                    for k, level in enumerate(tier_levels(ref, v.step, n_tiers)):
                        if pos is not None and k in pos["tiers"]:
                            continue
                        got = tier_fill(v.confirm, level, t, tk.o, tk.h, tk.close_1h)
                        if got is None:
                            continue
                        px, maker = got
                        if px >= stop:
                            refused["past_stop"] += 1
                            continue
                        if pos is None:
                            lev, gate = NT.fit_leverage(px, stop, params.leverage, params.max_leverage, cands)
                            if not gate.get("ok"):
                                refused["leverage"] += 1
                                break
                        else:
                            lev = pos["lev"]
                        open_notional = pos["size"] * px if pos is not None else 0.0
                        notional = tier_notional(eq, params, lev, px, stop, v.risk[k], open_notional, risk_frac)
                        if notional <= 0:
                            refused["margin"] += 1
                            continue
                        if not maker:
                            px = px * (1.0 - max(_market_exit_slip(params, notional), tk.slip))
                        if pos is None:
                            pos = {"opened": t, "avg": 0.0, "size": 0.0, "stop": stop, "target": ref, "ref": ref,
                                   "lev": lev, "fees": 0.0, "funding": 0.0, "risk": 0.0, "tiers": [], "last_fill": t,
                                   "thesis_id": tk.thesis_id, "reopen": tk.reopen}
                        pnl -= _fill(pos, k, px, notional, params.maker_fee if maker else params.taker_fee, t)
        # 4. stop, then target
        if pos is not None:
            if tk.h >= pos["stop"]:
                trig = max(pos["stop"], tk.o) if pos["opened"] < t else pos["stop"]
                book(_close(pos, buy_back(trig), t, "stop", params.taker_fee))
            elif pos["last_fill"] < t and tk.l <= pos["target"] - params.tick_size + 1e-9:
                book(_close(pos, pos["target"], t, "target", params.maker_fee))
        # the ladder re-arms after a stop once a bar trades back to the reference
        if not is_entry and not armed and pos is None:
            ref_now = (tk.ceiling if tk.side == "long" else None) if v.ref == "thesis" else tk.vwap24
            if ref_now is not None and tk.l <= ref_now:
                armed = True
        curve.append(pnl + ((pos["avg"] - tk.c) * pos["size"] if pos is not None else 0.0))
    if pos is not None and ticks:
        row = _close(pos, ticks[-1].c, None, "open", 0.0)
        trades.append(row)
    return {"trades": trades, "curve": curve, "refused": dict(refused)}


def walk_long(ticks: Sequence[Tick], v: Variant, params: Params, start_equity: float,
              long_curve: Optional[Sequence[float]] = None, long_open: Optional[Sequence[bool]] = None,
              long_entry: Optional[Sequence[bool]] = None, risk_frac: float = RISK_FRAC) -> Dict[str, Any]:
    """D, the long ladder: while a LONG thesis is in force, tier j buys at
    thesis entry x (1 + first + j x 0.5%), each at its share of the normal
    risk. The live rule never buys up there ("never chase"); this is the
    part of the move it sits out. One ladder per thesis. Stop
    (`long_stop`): the live rule's, `stop_buffer` under the 24h low, or N x
    ATR14 under the first fill; set at the first fill, one stop for every
    tier. Target: the thesis's first target, or the live rule's (the
    24h high less the target offset) at the first fill. A tier is refused
    when it has no room to the target, pays under `min_r`, or (the first)
    its stop does not fit 20x or 15x.

    Alone / combined as `walk`: combined takes no tier while the live long
    is open and sells the ladder at the open of the tick the live long
    enters (the desk holds one position; the dip entry is the live trade).

    Per 15m bar: funding on a 1h close; closes at the open (live entry,
    weekend flat, thesis no longer long, a 1h close at or under the
    thesis's invalidation); tier fills (all takers); the stop (a bar that
    opens under it fills at the open), then the target (resting limit,
    trade-through, maker; never on a bar a tier filled on)."""
    combined = long_curve is not None
    pnl = 0.0
    pos: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    refused: Counter = Counter()
    curve: List[float] = []
    done_ids: set = set()
    last_exit_t = -1
    n_tiers = len(v.risk)
    cands = params.entry_fit_leverage or ()

    by_equity = v.kind == "equity"

    def sell(px: float) -> float:
        return px * (1.0 - _market_exit_slip(params, pos["size"] * px))

    def book(row: Dict[str, Any]) -> None:
        nonlocal pnl, pos, last_exit_t
        pnl += row["gross_usd"] - row["exit_fee_usd"]
        trades.append(row)
        last_exit_t = row["exit_ms"]
        pos = None

    for i, tk in enumerate(ticks):
        t, mid = tk.t, tk.o
        if pos is not None and tk.fund and tk.close_1h is not None:
            pay = tk.fund * pos["size"] * tk.close_1h          # a long pays a positive rate
            pnl -= pay
            pos["funding"] += pay
        if pos is not None and pos["opened"] < t:
            reason = None
            if combined and long_entry[i]:
                reason = "live_entry"
            elif tk.weekend:
                reason = "weekend"
            elif tk.side != "long":
                reason = "thesis_change"
            elif tk.close_1h is not None and tk.invalid is not None and tk.close_1h <= tk.invalid:
                reason = "invalidation"
            if reason is not None:
                book(_close(pos, sell(mid), t, reason, params.taker_fee))
        if tk.ok and not tk.weekend and t > last_exit_t and not (combined and long_open[i]) \
                and tk.side == "long" and (pos is not None or (tk.ceiling is not None
                                                                 and tk.thesis_id not in done_ids)):
            eq = (long_curve[i] if combined else start_equity) + pnl
            ref = pos["ref"] if pos is not None else tk.ceiling
            if pos is not None:
                target = pos["target"]
            else:
                target = tk.target if v.target == "thesis" else offset_target(tk.hi24, "long", params.target_offset_frac)
            for k, level in enumerate(long_levels(ref, v.step, n_tiers)):
                if pos is not None and k in pos["tiers"]:
                    continue
                px = long_tier_fill(v.confirm, level, t, tk.o, tk.h, tk.close_1h)
                if px is None:
                    continue
                stop = pos["stop"] if pos is not None else long_stop(v.stop, px, tk.lo24, tk.atr, params.stop_buffer)
                if target is None or px >= target - params.tick_size or px <= stop:
                    refused["no_room"] += 1
                    continue
                if (target - px) / (px - stop) < v.min_r - EPS:
                    refused["min_r"] += 1
                    continue
                if pos is None:
                    lev, gate = NT.fit_leverage(px, stop, params.leverage, params.max_leverage, cands)
                    if not gate.get("ok"):
                        refused["leverage"] += 1
                        break
                else:
                    lev = pos["lev"]
                open_notional = pos["size"] * px if pos is not None else 0.0
                if by_equity:
                    notional = equity_tier_notional(eq, params, lev, v.risk[k], open_notional)
                else:
                    notional = tier_notional(eq, params, lev, px, stop, v.risk[k], open_notional, risk_frac)
                if notional <= 0:
                    refused["margin"] += 1
                    continue
                at_stop = stop_risk_pct(notional, px, stop, eq, params)
                px = px * (1.0 + max(_market_exit_slip(params, notional), tk.slip))
                if pos is None:
                    pos = {"opened": t, "avg": 0.0, "size": 0.0, "stop": stop, "target": target, "ref": ref,
                           "lev": lev, "fees": 0.0, "funding": 0.0, "risk": 0.0, "tiers": [], "last_fill": t,
                           "thesis_id": tk.thesis_id, "reopen": tk.reopen, "sign": 1.0}
                    done_ids.add(tk.thesis_id)
                pnl -= _fill(pos, k, px, notional, params.taker_fee, t, at_stop)
        if pos is not None:
            if tk.l <= pos["stop"]:
                trig = min(pos["stop"], tk.o) if pos["opened"] < t else pos["stop"]
                book(_close(pos, sell(trig), t, "stop", params.taker_fee))
            elif pos["last_fill"] < t and tk.h >= pos["target"] + params.tick_size - 1e-9:
                book(_close(pos, pos["target"], t, "target", params.maker_fee))
        curve.append(pnl + ((tk.c - pos["avg"]) * pos["size"] if pos is not None else 0.0))
    if pos is not None and ticks:
        trades.append(_close(pos, ticks[-1].c, None, "open", 0.0))
    return {"trades": trades, "curve": curve, "refused": dict(refused)}


def missed_theses(theses: Sequence[D.ThesisDirection], c15: Sequence[Candle], end_ms: int
                  ) -> List[Dict[str, Any]]:
    """Every LONG thesis with an entry, over its own life (its creation to
    the next thesis, or the data end): `touched` = a 15m low reached the
    entry (the live rule could buy); `right` = the last close of its life is
    above the first open (the direction was right); `hit_target` = a high
    reached its first target; `run_up_pct` = the highest high over the
    first open. A thesis the live rule MISSED is right and never touched."""
    ordered = sorted(theses, key=lambda th: th.created_at_ms)
    bars = sorted(c15, key=lambda c: c.t)
    times = [c.t for c in bars]
    out: List[Dict[str, Any]] = []
    for i, th in enumerate(ordered):
        if th.side != "long" or not th.entry_px:
            continue
        stop_ms = ordered[i + 1].created_at_ms if i + 1 < len(ordered) else end_ms
        life = bars[bisect.bisect_left(times, th.created_at_ms):bisect.bisect_left(times, min(stop_ms, end_ms))]
        if not life:
            continue
        entry = float(th.entry_px)
        hi = max(c.h for c in life)
        touched = min(c.l for c in life) <= entry
        right = life[-1].c > life[0].o
        out.append({"thesis_id": str(th.created_at_ms), "created": _iso(th.created_at_ms), "entry_px": entry,
                    "open_px": life[0].o, "gap_pct": round((life[0].o / entry - 1.0) * 100.0, 2),
                    "run_up_pct": round((hi / life[0].o - 1.0) * 100.0, 2), "touched": touched, "right": right,
                    "hit_target": bool(th.targets) and hi >= float(th.targets[0]),
                    "missed": right and not touched})
    return out


def caught(missed: Sequence[Dict[str, Any]], trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Of the theses the live rule missed, how many a variant traded, and
    what those trades made."""
    ids = {m["thesis_id"] for m in missed if m["missed"]}
    hit = [t for t in trades if t["thesis_id"] in ids]
    return {"missed": len(ids), "caught": len({t["thesis_id"] for t in hit}),
            "caught_wins": sum(1 for t in hit if t["net_usd"] > 0),
            "caught_net_usd": round(sum(t["net_usd"] for t in hit), 2)}


# ── summaries (pure) ───────────────────────────────────────────────────────

def max_dd_pct(equity: Sequence[float], start: float) -> float:
    """Max peak-to-trough drop of the marked equity, percent of the peak."""
    peak, dd = start, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = max(dd, (peak - e) / peak)
    return round(dd * 100.0, 1)


def summarize_short(run: Dict[str, Any], base_equity: Sequence[float]) -> Dict[str, Any]:
    """n, wins, net, R and max drawdown of one walk. `base_equity` is what
    the shorts' P&L sits on, per tick: the start equity (alone) or the live
    long book's marked equity (combined)."""
    trades = run["trades"]
    rs = [t["r"] for t in trades if t["r"] is not None]
    exits: Dict[str, int] = {}
    for t in trades:
        exits[t["reason"]] = exits.get(t["reason"], 0) + 1
    n = len(trades)
    wins = sum(1 for t in trades if t["net_usd"] > 0)
    net = run["curve"][-1] if run["curve"] else 0.0
    start = base_equity[0] if len(base_equity) else 0.0
    reopen = [t for t in trades if t.get("reopen")]
    reopen_usd = sum(t["net_usd"] for t in reopen)
    pcts = [t["tier_risk_pct"] for t in trades if t.get("tier_risk_pct")]
    depth = max((len(p) for p in pcts), default=0)
    by_tier = [[p[i] for p in pcts if len(p) > i] for i in range(depth)]
    return {"reopen_n": len(reopen), "reopen_usd": round(reopen_usd, 2), "ex_reopen_usd": round(net - reopen_usd, 2),
            "tier_risk_pct_mean": [round(sum(x) / len(x), 2) for x in by_tier],
            "tier_risk_pct_max": [round(max(x), 2) for x in by_tier],
            "trade_risk_pct_max": round(max((sum(p) for p in pcts), default=0.0), 2),
            "n": n, "wins": wins, "win_rate": round(wins / n * 100.0, 1) if n else None,
            "net_usd": round(net, 2), "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "total_r": round(sum(rs), 2),
            "max_dd_pct": max_dd_pct([b + c for b, c in zip(base_equity, run["curve"])], start),
            "tiers_filled": sum(t["tiers"] for t in trades), "exits": dict(sorted(exits.items())),
            "refused": run["refused"]}


def long_intervals(run: Run) -> List[Tuple[int, Optional[int]]]:
    """(entry, exit) of every live long; exit None for one still open at the end."""
    out: List[Tuple[int, Optional[int]]] = [(t.entry_time, t.exit_time) for t in run.trades]
    lv = run.open_position_levels
    if lv is not None:
        out.append((lv["opened_at"], None))
    return sorted(out)


def long_masks(times: Sequence[int], intervals: Sequence[Tuple[int, Optional[int]]]
               ) -> Tuple[List[bool], List[bool]]:
    """Per tick: is a live long open (its entry tick through its exit tick,
    both included), and does a live long enter on this tick."""
    entries = {e for e, _ in intervals}
    open_, enter = [], []
    for t in times:
        open_.append(any(e <= t and (x is None or t <= x) for e, x in intervals))
        enter.append(t in entries)
    return open_, enter


def collisions(short_trades: Sequence[Dict[str, Any]], intervals: Sequence[Tuple[int, Optional[int]]],
               end_ms: int) -> Dict[str, Any]:
    """How the ALONE run's shorts sit against the live long book: `overlap`
    = shorts open at any moment a long was open; `long_entered_inside` = a
    live long entry fell inside the short (the one-position rule would have
    made the desk choose); `opened_while_long` = the short's own entry fell
    inside a long."""
    n = len(short_trades)
    overlap = inside = opened = 0
    for s in short_trades:
        s0, s1 = s["entry_ms"], s["exit_ms"] if s["exit_ms"] is not None else end_ms
        ivs = [(e, x if x is not None else end_ms) for e, x in intervals]
        overlap += any(e <= s1 and x >= s0 for e, x in ivs)
        inside += any(s0 < e <= s1 for e, _ in ivs)
        opened += any(e <= s0 <= x for e, x in ivs)
    return {"n": n, "overlap": overlap, "long_entered_inside": inside, "opened_while_long": opened,
            "rate_pct": round(overlap / n * 100.0, 1) if n else None}


def drop_best_day(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Net by exit day (an open trade counts on its entry day); the best day removed."""
    by_day: Dict[str, float] = {}
    for t in trades:
        day = (t["exit"] or t["entry"] or "")[:10]
        by_day[day] = by_day.get(day, 0.0) + t["net_usd"]
    total = sum(by_day.values())
    if not by_day:
        return {"best_day": None, "best_day_usd": 0.0, "rest_usd": 0.0, "pass": False}
    best = max(by_day, key=lambda d: by_day[d])
    rest = total - by_day[best]
    return {"best_day": best, "best_day_usd": round(by_day[best], 2), "rest_usd": round(rest, 2), "pass": rest > EPS}


def judge(row: Dict[str, Any], neighbour_nets: Sequence[Optional[float]], primary: str = PRIMARY,
          min_n: int = MIN_N, reopen_leg: bool = True) -> Dict[str, Any]:
    """The bar on one variant. `row["datasets"][ds]` holds "combined" (the
    short sleeve beside the live book), "live" ({"net_usd", "max_dd_pct"})
    and "combined_dd_pct"; `row["halves"]` the sleeve's net in each half of
    the primary; `row["drop_best_day"]` the primary's. `neighbour_nets` are
    the neighbours' sleeve nets on the primary. Pure.

    The last leg is this test's own: every dataset's net and each half's
    must stay positive with the trades ENTERED on the first tick after a
    weekend removed (`reopen_tick`: a fill at that open is not a fill the
    desk gets). `repo_bar_without_reopen_leg` says whether the variant
    would pass on the five older legs alone, so the effect of the added
    leg is visible. `reopen_leg=False` judges on those five only: the
    reading for a run whose reopen ticks already carry realistic fills
    (`apply_reopen`), where there is nothing left to remove.

    Beating live = the sleeve adds money beside the live book (the combined
    net is the live net plus the sleeve's). PASS needs every leg and at
    least `min_n` trades on the primary. Under `min_n`: LEANING when every
    money leg holds, NOISE when one does not. Never rounded up."""
    ds = row["datasets"]
    beats = {k: d["combined"]["net_usd"] > EPS for k, d in ds.items()}
    dd = {k: d["combined_dd_pct"] <= d["live"]["max_dd_pct"] + EPS for k, d in ds.items()}
    halves = [h > EPS for h in row["halves"]]
    plateau = bool(neighbour_nets) and all(n is not None and n > EPS for n in neighbour_nets)
    ex = [d["combined"].get("ex_reopen_usd", d["combined"]["net_usd"]) > EPS for d in ds.values()] + [
        h > EPS for h in row.get("halves_ex_reopen", row["halves"])]
    legs = {"beats_live_every_dataset": bool(beats) and all(beats.values()),
            "dd_no_worse_every_dataset": bool(dd) and all(dd.values()),
            "walk_forward_both_halves": len(halves) == 2 and all(halves),
            "plateau": plateau, "drop_best_day": bool(row["drop_best_day"]["pass"]),
            "holds_without_reopen_ticks": all(ex)}
    n = ds[primary]["combined"]["n"] if primary in ds else 0
    money = all(ok for k, ok in legs.items() if reopen_leg or k != "holds_without_reopen_ticks")
    if n >= min_n:
        verdict = "PASS" if money else "FAIL"
    else:
        verdict = "LEANING" if money else "NOISE"
    repo_bar = all(ok for k, ok in legs.items() if k != "holds_without_reopen_ticks")
    return {"legs": legs, "beats": beats, "dd": dd, "n_primary": n, "verdict": verdict,
            "repo_bar_without_reopen_leg": repo_bar and n >= min_n, "reopen_leg": reopen_leg}


VERDICT_RANK = {"PASS": 0, "LEANING": 1, "FAIL": 2, "NOISE": 2}


def best_of(rows: Dict[str, Dict[str, Any]], kind: str, primary: str = PRIMARY) -> Optional[str]:
    """The kind's best variant: one that traded beside the live book before
    one that never did (a variant with no trade is not a result), a PASS
    first, then a LEANING, then the highest combined sleeve net on the
    primary, the name breaking ties."""
    names = [n for n, r in rows.items() if r["kind"] == kind and primary in r["datasets"]]
    if not names:
        return None
    return min(names, key=lambda n: (rows[n]["datasets"][primary]["combined"]["n"] == 0,
                                     VERDICT_RANK[rows[n]["judge"]["verdict"]],
                                     -rows[n]["datasets"][primary]["combined"]["net_usd"], n))


def operator_short(entry: float, stop: float, target: float, params: Params) -> Dict[str, Any]:
    """The operator's own short, costed: planned R before and after fees
    (taker in, maker out at the target, taker plus slippage out at the
    stop) and the win rate it needs to break even."""
    r = planned_r(entry, stop, target)
    win = (entry - target) - params.taker_fee * entry - params.maker_fee * target
    stop_fill = stop * (1.0 + params.slippage)
    loss = (stop_fill - entry) + params.taker_fee * entry + params.taker_fee * stop_fill
    r_net = win / loss if loss > 0 else 0.0
    return {"entry": entry, "stop": stop, "target": target, "planned_r": round(r, 3),
            "r_after_costs": round(r_net, 3),
            "breakeven_win_rate_pct": round(1.0 / (1.0 + r_net) * 100.0, 1) if r_net > -1 else None}


# ── runs ───────────────────────────────────────────────────────────────────

def _window(c15: Sequence[Candle], c1h: Sequence[Candle], start_ms: int, end_ms: int):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def prepare(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, thesis_at: ThesisAt, bp: Params,
            funding: Optional[Sequence[Dict[str, Any]]], start_ms: int, end_ms: int) -> Dict[str, Any]:
    """One dataset: the live long book's run and everything the walker needs."""
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    run = simulate(w15, w1h, direction_at, bp, funding)
    ticks = build_ticks(w15, w1h, thesis_at, funding)
    by_t = dict(run.equity_curve)
    curve, last = [], bp.starting_equity
    for tk in ticks:
        last = by_t.get(tk.t, last)
        curve.append(last)
    ivs = long_intervals(run)
    open_, enter = long_masks([tk.t for tk in ticks], ivs)
    live = summarize(run, bp.starting_equity)
    return {"ticks": ticks, "long_curve": curve, "long_open": open_, "long_entry": enter, "intervals": ivs,
            "live": {"trades": live["trades"], "wins": live["wins"], "net_usd": live["net_usd"],
                     "max_dd_pct": live["max_dd_pct"]},
            "start_ms": start_ms, "end_ms": end_ms, "params": bp}


def run_variant(v: Variant, ctx: Dict[str, Any], risk_frac: float = RISK_FRAC, keep_trades: bool = False
                ) -> Dict[str, Any]:
    """One variant on one prepared dataset: alone, combined, collisions."""
    bp: Params = ctx["params"]
    start = bp.starting_equity
    ticks = ctx["ticks"]
    w = walk_long if v.kind in ("long", "equity") else walk
    alone = w(ticks, v, bp, start, risk_frac=risk_frac)
    comb = w(ticks, v, bp, start, ctx["long_curve"], ctx["long_open"], ctx["long_entry"], risk_frac)
    s_alone = summarize_short(alone, [start] * len(ticks))
    s_comb = summarize_short(comb, ctx["long_curve"])
    out = {"alone": s_alone, "combined": s_comb, "combined_dd_pct": s_comb["max_dd_pct"], "live": ctx["live"],
           "combined_total_usd": round(ctx["live"]["net_usd"] + s_comb["net_usd"], 2),
           "collisions": collisions(alone["trades"], ctx["intervals"], ctx["end_ms"]),
           "drop_best_day": drop_best_day(comb["trades"])}
    if ctx.get("missed") is not None:
        out["caught"] = {"alone": caught(ctx["missed"], alone["trades"]),
                         "combined": caught(ctx["missed"], comb["trades"])}
    if keep_trades:
        out["trades_alone"], out["trades_combined"] = alone["trades"], comb["trades"]
    return out


def evaluate(variants: Sequence[Variant], contexts: Dict[str, Dict[str, Any]], halves: Sequence[Dict[str, Any]],
             risk_frac: float = RISK_FRAC, log: Optional[Callable[[str], None]] = None,
             primary: str = PRIMARY, reopen_leg: bool = True) -> Dict[str, Dict[str, Any]]:
    """Every variant on every dataset and both halves of the primary, judged."""
    rows: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    for i, v in enumerate(variants, 1):
        ds = {name: run_variant(v, ctx, risk_frac) for name, ctx in contexts.items()}
        hv = [run_variant(v, h, risk_frac) for h in halves]
        rows[v.name] = {"name": v.name, "kind": v.kind, "variant": dataclasses.asdict(v),
                        "risk_shares": list(v.risk), "datasets": ds,
                        "halves": [h["combined"]["net_usd"] for h in hv],
                        "halves_ex_reopen": [h["combined"]["ex_reopen_usd"] for h in hv],
                        "halves_alone": [h["alone"]["net_usd"] for h in hv],
                        "halves_n": [h["combined"]["n"] for h in hv],
                        "drop_best_day": ds[primary]["drop_best_day"] if primary in ds
                        else {"pass": False, "rest_usd": 0.0}}
        if log is not None and (i % 40 == 0 or i == len(variants)):
            pct = i / len(variants) * 100.0
            eta = (time.time() - t0) / i * (len(variants) - i)
            log(f"{pct:.0f}% done, about {eta:.0f}s left; {i}/{len(variants)} variants")
    for v in variants:
        nets = [rows[n.name]["datasets"][primary]["combined"]["net_usd"] if n.name in rows and primary in
                rows[n.name]["datasets"] else None for n in neighbours(v)]
        rows[v.name]["neighbour_nets"] = nets
        rows[v.name]["judge"] = judge(rows[v.name], nets, primary, reopen_leg=reopen_leg)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    default_theses = os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else "")
    ap = argparse.ArgumentParser(
        description="Long ladder (D), short-to-entry (A) and tiered short ladder (B) backtest on the oil desk's "
                    "thesis datasets, each beside the live long book (C). "
                    "Research only: reads candles and the journal, never trades or writes config.",
        epilog="Exit 0: a variant passes the bar. 1: none does. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=default_theses,
                    help="READ ONLY: the live oil journal's theses (default: $PATHIEL_STATE_DIR/strategies/oil/theses)")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses",
                    help="the 30-thesis replay set; skipped when missing")
    ap.add_argument("--cache-dir", default="/tmp/short-ladder/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/short-ladder")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--risk-frac", type=float, default=RISK_FRAC, help="the normal per-trade risk (default 0.06)")
    ap.add_argument("--only", choices=("A", "B", "D", "E"), default=None, help="run one study only")
    ap.add_argument("--no-realistic", action="store_true",
                    help="skip the second pass with realistic weekend-reopen fills")
    ap.add_argument("--no-wti", action="store_true", help="skip the WTI cross-check")
    ap.add_argument("--op-short", type=float, nargs=3, metavar=("ENTRY", "STOP", "TARGET"),
                    default=(92.714, 93.80, 92.48), help="the operator's own short, costed in the report")
    ap.add_argument("--detail", nargs="*", default=[], metavar="NAME",
                    help="variant names whose trades to keep in the report beside the best of each study")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} short-ladder: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        return c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, end, a.cache_dir)

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    b15, b1h, b_end, b_fund = load(BRENT)
    bp = base_params(a.start_equity, a.risk_frac)
    start = max(min(th.created_at_ms for th in theses), b15[0].t)
    contexts: Dict[str, Dict[str, Any]] = {}
    contexts[PRIMARY] = prepare(b15, b1h, journaled_direction(theses), thesis_lookup(theses), bp, b_fund, start, b_end)
    missed = missed_theses(theses, [c for c in b15 if start <= c.t < b_end], b_end)
    contexts[PRIMARY]["missed"] = missed
    meta = {PRIMARY: {"n_theses": len(theses), "window": [_iso(start), _iso(b_end)]}}
    replay = D.load_journaled_theses(a.oil_replay_dir) if a.oil_replay_dir else []
    if replay:
        r_start = max(min(th.created_at_ms for th in replay), b15[0].t)
        r_end = min(b_end, max(th.created_at_ms for th in replay) + DAY_MS)
        contexts["brent_replay30"] = prepare(b15, b1h, journaled_direction(replay), thesis_lookup(replay), bp, b_fund,
                                             r_start, r_end)
        meta["brent_replay30"] = {"n_theses": len(replay), "window": [_iso(r_start), _iso(r_end)]}
    else:
        log(f"brent_replay30: no theses at {a.oil_replay_dir!r}, skipped")
    if not a.no_wti:
        w15, w1h, w_end, w_fund = load(WTI)
        ratio = price_ratio(theses, b1h, w1h)
        w_start, w_stop = max(start, w15[0].t), min(b_end, w_end)
        contexts["wti_journal"] = prepare(w15, w1h, side_only(journaled_direction(theses)),
                                          thesis_lookup(theses, ratio), bp, w_fund, w_start, w_stop)
        meta["wti_journal"] = {"n_theses": len(theses), "window": [_iso(w_start), _iso(w_stop)]}
    mid = start + (b_end - start) // 2
    mid -= mid % FIFTEEN_MS
    halves = [prepare(b15, b1h, journaled_direction(theses), thesis_lookup(theses), bp, b_fund, s, e)
              for s, e in ((start, mid), (mid, b_end))]
    variants = ((equity_variants() if a.only in (None, "E") else []) + (long_variants() if a.only in (None, "D") else [])
                + (entry_variants() if a.only in (None, "A") else []) + (ladder_variants() if a.only in (None, "B") else []))
    log(f"{len(theses)} theses {_iso(start)} -> {_iso(b_end)}; datasets {list(contexts)}; halves split at {_iso(mid)}; "
        f"{len(variants)} variants; live {({k: c['live']['net_usd'] for k, c in contexts.items()})}")
    by_name = {v.name: v for v in variants}
    rows = evaluate(variants, contexts, halves, a.risk_frac, log)
    study = summarize_study(rows, contexts)
    wanted = list(dict.fromkeys(n for n in list(study["best"].values()) + [E_PRIMARY] + list(a.detail)
                                if n and n in by_name))
    detail = {n: {ds: run_variant(by_name[n], ctx, a.risk_frac, keep_trades=True) for ds, ctx in contexts.items()}
              for n in wanted}
    missed_summary = {"long_theses": len(missed), "touched": sum(1 for m in missed if m["touched"]),
                      "never_touched": sum(1 for m in missed if not m["touched"]),
                      "missed_right_never_touched": sum(1 for m in missed if m["missed"]),
                      "missed_and_hit_target": sum(1 for m in missed if m["missed"] and m["hit_target"]),
                      "rows": missed}
    csv_path = os.path.join(a.out_dir, "variants.csv")
    write_variants_csv(csv_path, rows, list(contexts))

    realistic: Optional[Dict[str, Any]] = None
    if not a.no_realistic:
        # second pass: the same grid with the fill the desk really gets on the first tick after a weekend
        fills = {BRENT: reopen_fills(b15, D.fetch_candles_cached(BRENT, "5m", 5000, a.cache_dir), bp.slippage)}
        if not a.no_wti:
            fills[WTI] = reopen_fills(w15, D.fetch_candles_cached(WTI, "5m", 5000, a.cache_dir), bp.slippage)

        def coin_of(name: str) -> str:
            return WTI if name.startswith("wti") else BRENT
        r_ctx = {name: {**ctx, "ticks": apply_reopen(ctx["ticks"], fills[coin_of(name)])}
                 for name, ctx in contexts.items()}
        r_halves = [{**h, "ticks": apply_reopen(h["ticks"], fills[BRENT])} for h in halves]
        log(f"second pass, realistic reopen fills: {({c: Counter(f['source'] for f in fl.values()) for c, fl in fills.items()})}")
        r_rows = evaluate(variants, r_ctx, r_halves, a.risk_frac, log, reopen_leg=False)
        r_study = summarize_study(r_rows, r_ctx)
        r_wanted = list(dict.fromkeys(wanted + [n for n in r_study["best"].values() if n]))
        realistic = {
            "model": "first fill 5 min after the reopen: the open of the 5m bar at +5 min, slippage half that bar's "
                     "high-low range; where the venue no longer serves 5m candles, the next 15m tick at the median "
                     "measured slippage. Judged on the five older legs (nothing is removed).",
            "fills": {coin: [{"reopen": _iso(t), "open_px": f["open_px"], "fill_px": f["px"],
                              "open_to_fill_pct": round((f["px"] / f["open_px"] - 1.0) * 100.0, 2),
                              "slip_bps": round(f["slip"] * 1e4, 1), "source": f["source"]}
                             for t, f in sorted(fl.items())] for coin, fl in fills.items()},
            **r_study, "reopen_profit": reopen_profit(rows, r_rows, list(contexts)), "rows": r_rows,
            "best_trades": {n: {ds: run_variant(by_name[n], ctx, a.risk_frac, keep_trades=True)
                                for ds, ctx in r_ctx.items()} for n in r_wanted},
        }
        write_variants_csv(os.path.join(a.out_dir, "variants_realistic.csv"), r_rows, list(contexts))

    trades_path = os.path.join(a.out_dir, "best_trades.csv")
    with open(trades_path, "w", newline="") as fh:
        fields = ["fills", "variant", "dataset", "mode", "entry", "exit", "entry_px", "exit_px", "stop_px", "target_px",
                  "tiers", "leverage", "reason", "net_usd", "r", "risk_usd", "tier_risk_pct", "fees_usd", "funding_usd",
                  "reopen"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for label, block in (("open_print", detail), ("realistic", realistic["best_trades"] if realistic else {})):
            for name, per in block.items():
                for ds, d in per.items():
                    for mode in ("alone", "combined"):
                        for t in d[f"trades_{mode}"]:
                            w.writerow({"fills": label, "variant": name, "dataset": ds, "mode": mode, **t})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "start_equity": a.start_equity, "risk_frac": a.risk_frac,
        "entry_fit_leverage": bp.entry_fit_leverage, "datasets": meta, "halves_split": _iso(mid),
        "live": {k: c["live"] for k, c in contexts.items()},
        "live_halves": [h["live"] for h in halves],
        "n_variants": len(variants), **study,
        "missed_theses": missed_summary, "operator_short": operator_short(*a.op_short, bp),
        "equity_primary": E_PRIMARY, "rows": rows, "best_trades": detail, "realistic": realistic,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nvariants: {csv_path}\nbest trades: {trades_path}")
    return 0 if study["passing"] or (realistic and realistic["passing"]) else 1


def _fmt(r: Dict[str, Any], datasets: Sequence[str]) -> str:
    parts = [f"  {r['name']:<40} {r['judge']['verdict']:<8}"]
    for ds in datasets:
        d = r["datasets"][ds]
        al, cb = d["alone"], d["combined"]
        wr = f"{cb['win_rate']:.0f}%" if cb["win_rate"] is not None else "-"
        ar = f"{cb['avg_r']:+.2f}" if cb["avg_r"] is not None else "-"
        coll = f"{d['collisions']['rate_pct']:.0f}%" if d["collisions"]["rate_pct"] is not None else "-"
        parts.append(f"| alone {al['n']:>3} {al['net_usd']:>+8.2f} dd {al['max_dd_pct']:>4.1f} | beside live {cb['n']:>3} "
                     f"{wr:>4} {cb['net_usd']:>+8.2f} {ar:>6}R dd {cb['max_dd_pct']:>4.1f} vs {d['live']['max_dd_pct']:>4.1f} "
                     f"coll {coll:>4}")
    parts.append(f"| halves {r['halves'][0]:+.2f} / {r['halves'][1]:+.2f} n {r['halves_n']}")
    cg = r["datasets"][PRIMARY].get("caught", {}).get("combined")
    if cg and r["kind"] in ("long", "equity"):
        parts.append(f"| caught {cg['caught']}/{cg['missed']} missed, {cg['caught_net_usd']:+.2f}")
    return " ".join(parts)


KINDS = (("E", "equity"), ("D", "long"), ("A", "entry"), ("B", "ladder"))
LABELS = {"equity": "E. long ladder sized in equity (20% at +0.5%, 10% at +1%)", "long": "D. long ladder above the entry",
          "entry": "A. short to the entry", "ladder": "B. tiered short ladder"}


def summarize_study(rows: Dict[str, Dict[str, Any]], contexts: Dict[str, Any]) -> Dict[str, Any]:
    """Verdict counts, how many variants add money per dataset, who passes, and the best of each study."""
    kinds = [kind for _, kind in KINDS if any(r["kind"] == kind for r in rows.values())]
    return {
        "verdicts": {kind: dict(Counter(r["judge"]["verdict"] for r in rows.values() if r["kind"] == kind))
                     for kind in kinds},
        "positive_beside_live": {kind: {ds: sum(1 for r in rows.values() if r["kind"] == kind
                                                and r["datasets"][ds]["combined"]["net_usd"] > EPS)
                                        for ds in contexts} for kind in kinds},
        "passing": sorted(n for n, r in rows.items() if r["judge"]["verdict"] == "PASS"),
        "passing_without_reopen_leg": sorted(n for n, r in rows.items() if r["judge"]["repo_bar_without_reopen_leg"]),
        "best": {k: best_of(rows, kind) for k, kind in KINDS if kind in kinds},
    }


def reopen_profit(open_rows: Dict[str, Dict[str, Any]], real_rows: Dict[str, Dict[str, Any]],
                  datasets: Sequence[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per study and dataset, summed over the grid beside the live book: the
    net and the part made by trades entered on a reopen tick, on the open
    print and on the realistic fill, and the share of the old reopen profit
    that survives."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for _, kind in KINDS:
        names = [n for n, r in open_rows.items() if r["kind"] == kind and n in real_rows]
        if not names:
            continue
        out[kind] = {}
        for ds in datasets:
            def tot(rows: Dict[str, Dict[str, Any]], key: str) -> float:
                return sum(rows[n]["datasets"][ds]["combined"][key] for n in names)
            old, new = tot(open_rows, "reopen_usd"), tot(real_rows, "reopen_usd")
            out[kind][ds] = {"variants": len(names), "net_open_print_usd": round(tot(open_rows, "net_usd"), 2),
                             "net_realistic_usd": round(tot(real_rows, "net_usd"), 2),
                             "reopen_open_print_usd": round(old, 2), "reopen_realistic_usd": round(new, 2),
                             "reopen_trades_open_print": int(tot(open_rows, "reopen_n")),
                             "reopen_trades_realistic": int(tot(real_rows, "reopen_n")),
                             "survives_pct": round(new / old * 100.0, 1) if old > EPS else None}
    return out


def write_variants_csv(path: str, rows: Dict[str, Dict[str, Any]], datasets: Sequence[str]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        head = ["name", "kind", "verdict", "n_primary", "h1_usd", "h2_usd", "drop_best_day_rest_usd",
                "ex_reopen_usd", "missed_caught", "missed_caught_net_usd", "tier_risk_pct_mean", "tier_risk_pct_max"]
        for ds in datasets:
            head += [f"{ds}:{c}" for c in ("alone_n", "alone_wins", "alone_net", "alone_avg_r", "alone_dd", "comb_n",
                                           "comb_wins", "comb_net", "comb_avg_r", "comb_dd", "live_net", "live_dd",
                                           "collision_pct", "long_entered_inside")]
        w.writerow(head)
        for n in sorted(rows, key=lambda n: (rows[n]["kind"], -rows[n]["datasets"][PRIMARY]["combined"]["net_usd"], n)):
            r = rows[n]
            cg = r["datasets"][PRIMARY].get("caught", {}).get("combined", {})
            al0 = r["datasets"][PRIMARY]["alone"]
            line = [n, r["kind"], r["judge"]["verdict"], r["judge"]["n_primary"], r["halves"][0], r["halves"][1],
                    r["drop_best_day"]["rest_usd"], r["datasets"][PRIMARY]["combined"]["ex_reopen_usd"],
                    cg.get("caught"), cg.get("caught_net_usd"), "/".join(str(x) for x in al0["tier_risk_pct_mean"]),
                    "/".join(str(x) for x in al0["tier_risk_pct_max"])]
            for ds in datasets:
                d = r["datasets"][ds]
                al, cb = d["alone"], d["combined"]
                line += [al["n"], al["wins"], al["net_usd"], al["avg_r"], al["max_dd_pct"], cb["n"], cb["wins"],
                         cb["net_usd"], cb["avg_r"], cb["max_dd_pct"], d["live"]["net_usd"], d["live"]["max_dd_pct"],
                         d["collisions"]["rate_pct"], d["collisions"]["long_entered_inside"]]
            w.writerow(line)


def _print_study(rep: Dict[str, Any], rows: Dict[str, Dict[str, Any]], study: Dict[str, Any], datasets: Sequence[str]) -> None:
    for _, kind in KINDS:
        names = sorted((n for n, r in rows.items() if r["kind"] == kind),
                       key=lambda n: -rows[n]["datasets"][PRIMARY]["combined"]["net_usd"])
        if not names:
            continue
        print(f"\n{LABELS[kind]}: {len(names)} variants, verdicts {study['verdicts'][kind]}, "
              f"positive beside live {study['positive_beside_live'][kind]}")
        print(f"  top 8 and bottom 3 by net beside live on {PRIMARY} (columns per dataset: {', '.join(datasets)})")
        for n in names[:8] + names[-3:]:
            print(_fmt(rows[n], datasets))
        if kind == "equity" and rep["equity_primary"] in rows:
            r = rows[rep["equity_primary"]]
            print("  the operator's tiers:")
            print(_fmt(r, datasets))
            for ds in datasets:
                al = r["datasets"][ds]["alone"]
                print(f"    {ds}: risk at the stop per tier, % of equity: mean {al['tier_risk_pct_mean']}, "
                      f"max {al['tier_risk_pct_max']}, worst trade {al['trade_risk_pct_max']}%")
    print(f"\nbest: {study['best']}")
    print(f"passing the bar: {study['passing'] or 'NONE'}")


def _print(rep: Dict[str, Any]) -> None:
    datasets = list(rep["live"])
    print(f"\nshort ladder test, Brent candles to {rep['brent_data_end']}, start ${rep['start_equity']}, "
          f"normal risk {rep['risk_frac'] * 100:g}%")
    for ds in datasets:
        lv = rep["live"][ds]
        print(f"  live long book, {ds}: {lv['trades']} trades, {lv['wins']} wins, net {lv['net_usd']:+.2f}, "
              f"max DD {lv['max_dd_pct']}%  ({rep['datasets'][ds]['window'][0]} -> {rep['datasets'][ds]['window'][1]})")
    ms = rep["missed_theses"]
    print(f"  long theses {ms['long_theses']}: entry touched {ms['touched']}, never touched {ms['never_touched']}, "
          f"of those right (the live rule's misses) {ms['missed_right_never_touched']}, "
          f"of those reaching the first target {ms['missed_and_hit_target']}")
    print("\n=== pass 1: reopen ticks filled at the 15m open print; the bar includes the no-reopen-fills leg ===")
    _print_study(rep, rep["rows"], rep, datasets)
    print(f"passing the five older legs only (weekend-reopen fills counted): {rep['passing_without_reopen_leg'] or 'NONE'}")
    real = rep.get("realistic")
    if real:
        print("\n=== pass 2: realistic reopen fills; judged on the five older legs ===")
        print(f"  {real['model']}")
        for coin, fl in real["fills"].items():
            for f in fl:
                print(f"  {coin} {f['reopen']}: open print {f['open_px']} -> fill {f['fill_px']} ({f['open_to_fill_pct']:+.2f}%), "
                      f"slip {f['slip_bps']} bps, {f['source']}")
        _print_study(rep, real["rows"], real, datasets)
        print("\n  reopen profit, summed over each grid beside live (open print -> realistic):")
        for kind, per in real["reopen_profit"].items():
            for ds, x in per.items():
                keep = f"{x['survives_pct']}% survives" if x["survives_pct"] is not None else "nothing to survive"
                print(f"    {kind:<7} {ds:<15} reopen trades {x['reopen_open_print_usd']:>+9.2f} -> {x['reopen_realistic_usd']:>+9.2f} "
                      f"({keep}); grid net {x['net_open_print_usd']:>+10.2f} -> {x['net_realistic_usd']:>+10.2f}")
    op = rep["operator_short"]
    print(f"\noperator's short {op['entry']} -> {op['target']}, stop {op['stop']}: {op['planned_r']}R planned, "
          f"{op['r_after_costs']}R after costs, needs {op['breakeven_win_rate_pct']}% wins to break even")


if __name__ == "__main__":
    raise SystemExit(main())
