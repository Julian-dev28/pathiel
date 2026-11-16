"""Thesis-range test, 2026-11-16. Research only.

Operator, 2026-11-16, after the stop-and-reverse test failed on desk fills
and found that the winning 93.709 target was the desk THESIS target: "find
the edge that makes the mechanical version hold up, it's actually working
you know, I always been using the desk's thesis". They trade by hand around
the thesis's own levels: the thesis entry (the dip-buy level) as support,
the thesis target as resistance. One deterministic script, no LLM call, no
order, no config write.

The map: every journal thesis gives side, entry, stop, first target. While
a LONG thesis is in force the range is [thesis entry, thesis target]; a new
thesis moves it (short and flat theses have no range). A level is written
as a position in the range: 0 = the thesis entry, 1 = the thesis target.

Two rule families (every choice is a declared axis or a named constant).

PRIMARY, the operator's ladder (`Ladder`, `walk_ladder`), their spec of
2026-11-16 and the matrix around it:

  1 short    Toward the thesis entry, once per thesis: a market order at a
             desk tick with the close 0.75% over the thesis entry (the
             trigger that reproduced their night), or a resting sell limit
             at range position 0.33 / 0.5 / 0.75. Target = the thesis
             entry. Margin 5 / 10 / 20% of equity at 20x.
  2 stop     "5%" of the position's margin = 0.25% of price at 20x; also
             0.36% (the night's actual), 0.375% (7.5%), 0.5% (10%) and the
             4h swing high. Every later leg uses the mirror of this stop.
  3 flip     Stopped: long toward the thesis high (the thesis target), 10 /
             20 / 30% margin, entered by a RESTING buy-stop at the short's
             stop or by a market order on the next desk tick. Ratchet: none,
             break-even once 50% / 75% of the way is covered, or a trail of
             1 / 2 x ATR14(1h), moved at the desk tick.
  4 bounce   At the thesis high: short back to the thesis entry, then long
             again from it, until the thesis is rewritten or a stop breaks
             the range. On or off.
  filters    On the first short: thesis age (3), range width in ATR (2),
             session (3), each alone, or none.

  4 entries x 3 short sizes x 5 stops x 3 long sizes x 5 ratchets x 2 fills
  x 2 bounce x 9 filter settings = 32,400 ladder variants. `OPERATOR` is the
  exact spec (market short, 20% / 0.25% / 20%, break-even at half way, by
  hand on the next tick, bounce on) and is reported on its own row.

SECOND, the range rule (`Variant`, `walk`): resting limits at the thesis
levels with no short-first sequence.

  long leg   A resting buy limit at range position b (0 / 0.1 / 0.25).
             Target: all or half of the way from the fill to the thesis
             target. Stop: the thesis stop, 0.36% under the fill, or
             1 x ATR14(1h). b = 0, full target, thesis stop is the BASELINE
             (roughly the live dip-buy rule).
  short leg  A resting sell limit at range position a (0.33 / 0.5 / 0.75 /
             0.9 / 1.0 = the thesis target). Target: all or half of the way
             from the fill back to the thesis entry. Stop: 0.36% over the
             fill, 1 x ATR14(1h), or just over the range top ("top").
  flip up    When the short's stop fills, a buy-stop RESTING at the same
             price takes a long to the thesis target; its stop sits just
             under the short's entry ("sentry") or 1 ATR under the fill.
  flip down  When the long's stop fills, a sell-stop resting at the same
             price takes a short for one measured move (the long's own
             risk), stop just over the long's entry.
  Modes L, S, LS (3,366), then 19 condition filters one at a time on the
  single-leg variants and the 20 best two-leg ones, then the best three
  filters combined. 10% margin on every leg.

Both: one position at a time, one first entry per leg per thesis. A bracket
is frozen at its fill: a later thesis moves new orders, not an open
position's target (the ladder's ratchet is the one thing that moves a stop).

Execution, desk-real (a search axis only as the ladder's flip fill):
  - Orders are placed and pulled by the 15m desk tick only. On 5m bars the
    cron of :03/:18/:33/:48 has its orders live from the bar of :05/:20/
    :35/:50 and reads the thesis written by then; on 15m bars an order is
    live from the bar after the cron. A limit is only placed on the far
    side of the last close (a resting order, never a marketable one), and
    after a close nothing new rests until the next tick.
  - A limit entry fills at its price on a trade-through by one tick, maker.
    On its fill bar the stop counts on the whole bar (price crossed the
    entry on the way to it) and the target never fills.
  - A flip is a resting stop-entry: taker, the sim's stop slippage, on the
    stop's own bar. On that bar only the CLOSE can stop the new leg; the
    audit reruns the chosen rule with the whole bar counted.
  - A stop is a taker fill with slippage (a bar that opens beyond it fills
    at its open); a target is a maker limit that needs a trade-through.
    Stop and target on one bar is a loss. Fees are the account's measured
    xyz rates (`intraday_5m_test.measured_fees`, about 0.90 / 0.31 bps).
    Funding on the hour. Weekend flat: a position is closed at the market
    on the first weekend bar (half that bar's range as slippage), orders
    are pulled, and the reopen gap is paid by whatever stop it jumps.
Conditions (an order rests only while the condition held at its tick):
thesis age, range width in ATR, session, thesis confidence, thesis written
just after a real close, the EIA blackout (the headline blackout is not
replayable from candles), the 1h and 4h trend, and how far the thesis entry
sat from the price when it was written. Every stage reads the SEARCH set
only.

Overfitting guard (the main job):
  - `holdout_split` fixes the split from the journal alone, before any
    bar is read: the most recent 25% of theses are the HOLDOUT, and so is
    every WTI bar. `select` picks ONE rule on the search set; the holdout
    walks run after that pick is logged.
  - Deflated view: best of N against the median, the rank correlation of
    search and holdout nets over the whole grid, and what the search's top
    50 did on the holdout.
  - Random-level baseline, 200 seeded draws, everything else held: a range
    variant has each thesis's limit drawn uniformly inside the same range;
    a ladder has each thesis's whole range displaced by up to one width.
  - A rule HOLDS only if it makes money on the Brent holdout, on WTI (5m
    and 15m), in both halves of the search set, and beats the 95th
    percentile of its random-level baseline on the holdout data. The
    repo's adoption bar (`short_ladder_test.judge`, beside the live book)
    is reported next to it.

Also scored: the account's own Brent trades from the review ledger, each
labelled by where it entered in the thesis range.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.thesis_range_test \\
        --cache-dir <dir> --out-dir <dir>

Exit 0 when the chosen rule holds on every leg above, 1 when it does not,
2 with no journal.
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
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest import oil_calendar as OC
from services.strategies.backtest.desk_tick_sim import Params, _market_exit_slip
from services.strategies.evals import intraday_5m_test as IN
from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals import stop_reverse_test as SR
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.stop_cap_test import _iso, closed_only
from services.strategies.evals.trend_gate_test import base_params, side_only

BRENT = NT.BRENT
WTI = "xyz:CL"
FIVE_MS = 300_000
FIFTEEN_MS = 900_000
HOUR_MS = 3_600_000
START = SL.START
LEVERAGE = 20.0
MARGIN_SHARE = 0.10            # every leg: 10% of equity as margin at 20x
EPS = 1e-9

HOLDOUT_FRAC = 0.25            # the most recent quarter of theses, fixed before any search
MIN_N = 10                     # under this many trades a variant is not selectable (search) or LEANING / NOISE (bar)
BASELINE_DRAWS = 200           # random-level draws per baseline
BASELINE_PCT = 95.0            # the chosen rule must beat this percentile of its baseline on the holdout data
TOP_K = 50                     # the deflated view follows the search's best this many
CARRIERS_LS = 20               # stage 2 carries the single-leg variants plus this many best two-leg ones
COMBINE = 3                    # stage 3 combines the best this many filters

MIN_WIDTH = 0.002              # a range under 0.2% wide is no range
MIN_ROOM = 0.001               # a target closer than 10 bps to the fill is no trade
MIN_STOP = 0.001               # a stop closer than 10 bps to the fill is refused
MAX_STOP = 0.025               # and one wider than 2.5% (the live rule: half the way to liquidation at 20x)
TOP_PAD = 0.0025               # "top": the short's stop this far over the thesis target
ENTRY_PAD = 0.0005             # a flip's stop this far beyond the stopped leg's entry
CRON_LAG_5M_MS = 2 * 60_000    # the :03 cron's orders are live from the :05 bar
CRON_LAG_15M_MS = 12 * 60_000  # on 15m bars: the :03 cron of the bar before
POST_CLOSE_MS = HOUR_MS        # "post_close": a thesis written within this long after a real close
NEAR_WRITE = 0.005             # "near_write": the thesis entry within 0.5% of the price when written
EIA_HOURS = 2.0                # the live EIA blackout, either side
SWING_HOURS = 4                # the ladder's "swing" stop sits beyond this many hours' extreme

B_GRID = (0.0, 0.1, 0.25)
L_TARGETS = (1.0, 0.5)
L_STOPS = ("thesis", "p0.36", "atr1.0")
FLIP_DN = (False, True)
A_GRID = (0.33, 0.5, 0.75, 0.9, 1.0)
S_TARGETS = (1.0, 0.5)
S_STOPS = ("p0.36", "atr1.0", "top")
FLIP_UP = ("off", "sentry", "atr1.0")
MODES = ("L", "S", "LS")
FILTERS = ("age_lt2h", "age_2to8h", "age_ge8h", "wid_lt4atr", "wid_ge4atr", "sess_asia", "sess_london", "sess_ny",
           "conf_ge50", "conf_lt50", "post_close", "not_post_close", "eia_clear", "t1h_up", "t1h_down", "t4h_up",
           "t4h_down", "near_write", "far_write")
LEGS = ("short", "flip", "bshort", "blong", "long", "flip_up", "flip_dn")

SEARCH = "brent_5m_search"                     # the ONLY segment `select` and the filter staging read
SEARCH_SEGS = (SEARCH, "brent_15m_search")     # brent_15m_search is a coarser cross-check, never a selector
HOLDOUT_SEGS = ("brent_5m_holdout", "brent_15m_holdout", "wti_5m", "wti_15m")
HOLD_MONEY = ("brent_5m_holdout", "wti_5m", "wti_15m")     # the segments a rule must make money on
BASE_POOL = ("brent_5m_holdout", "wti_5m")                 # the holdout data the baseline is read on


# ── the map: theses and the split ──────────────────────────────────────────

@dataclass(frozen=True)
class Th:
    """One journal thesis as the range reads it (prices already carried to
    the instrument)."""
    created_ms: int
    side: str
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    conf: Optional[float] = None
    post_close: bool = False
    px_at_write: Optional[float] = None

    @property
    def tid(self) -> str:
        return str(self.created_ms)


def holdout_split(theses: Sequence[Any], frac: float = HOLDOUT_FRAC) -> Optional[int]:
    """The time the holdout starts: `created_at_ms` of the first of the
    most recent `frac` of theses (rounded up to a whole thesis). Reads the
    journal's timestamps and nothing else: no candle, no result. Pure."""
    times = sorted(int(th.created_at_ms) for th in theses)
    if not times:
        return None
    k = max(1, math.ceil(len(times) * frac))
    return times[len(times) - k]


def range_theses(theses: Sequence[D.ThesisDirection], c1h: Sequence[Any], scale: Optional[Dict[int, float]] = None,
                 close_times: Sequence[int] = ()) -> List[Th]:
    """The journal as `Th` rows, oldest first. `scale` (created_at_ms ->
    factor) carries Brent prices to another instrument; a thesis with no
    factor has no levels there. `close_times` are the real closes of the
    account (ms): a thesis written within POST_CLOSE_MS after one is
    "post_close". `px_at_write` is the instrument's last 1h close at or
    before the thesis. Pure."""
    hours = sorted(c1h, key=lambda c: c.t)
    hclose = [c.t + HOUR_MS for c in hours]
    closes = sorted(close_times)
    out: List[Th] = []
    for th in sorted(theses, key=lambda x: x.created_at_ms):
        k = 1.0 if scale is None else scale.get(th.created_at_ms)

        def px(v: Any) -> Optional[float]:
            try:
                return float(v) * k if k and v is not None else None
            except (TypeError, ValueError):
                return None
        j = bisect.bisect_right(hclose, th.created_at_ms) - 1
        i = bisect.bisect_right(closes, th.created_at_ms) - 1
        out.append(Th(created_ms=th.created_at_ms, side=th.side, entry=px(th.entry_px), stop=px(th.stop_px),
                      target=px(th.targets[0]) if th.targets else None, conf=th.confidence,
                      post_close=i >= 0 and th.created_at_ms - closes[i] <= POST_CLOSE_MS,
                      px_at_write=hours[j].c if j >= 0 else None))
    return out


def thesis_range(th: Optional[Th]) -> Optional[Tuple[float, float, Optional[float]]]:
    """(entry, target, stop) of a LONG thesis whose target sits at least
    MIN_WIDTH over its entry; None for any other thesis. Pure."""
    if th is None or th.side != "long" or not th.entry or not th.target:
        return None
    if th.target <= th.entry * (1.0 + MIN_WIDTH):
        return None
    return th.entry, th.target, th.stop


def level_at(entry: float, target: float, pos: float) -> float:
    """The price at range position `pos` (0 = thesis entry, 1 = target)."""
    return entry + pos * (target - entry)


def range_pos(px: float, entry: float, target: float) -> Optional[float]:
    return (px - entry) / (target - entry) if abs(target - entry) > EPS else None


# ── variants ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    mode: str = "LS"               # MODES
    b: float = 0.0                 # long limit, range position
    ltf: float = 1.0               # long target: this share of the way from the fill to the thesis target
    lstop: str = "thesis"          # L_STOPS
    flip_dn: bool = False
    a: float = 1.0                 # short limit, range position
    stf: float = 1.0               # short target: this share of the way from the fill to the thesis entry
    sstop: str = "p0.36"           # S_STOPS
    flip_up: str = "off"           # FLIP_UP
    filt: Tuple[str, ...] = ()     # FILTERS, all must hold at the order's tick

    @property
    def name(self) -> str:
        parts = [self.mode]
        if "L" in self.mode:
            parts.append(f"b{self.b:g}_lt{self.ltf:g}_{self.lstop}_fd{int(self.flip_dn)}")
        if "S" in self.mode:
            parts.append(f"a{self.a:g}_st{self.stf:g}_{self.sstop}_fu-{self.flip_up}")
        return "_".join(parts) + "".join("+" + f for f in self.filt)

    @property
    def family(self) -> str:
        flips = ("S" in self.mode and self.flip_up != "off") or ("L" in self.mode and self.flip_dn)
        return self.mode + ("_flip" if flips else "") + ("_filtered" if self.filt else "")


BASELINE = Variant(mode="L")       # long at the thesis entry to the thesis target, the thesis stop


def base_variants() -> List[Variant]:
    longs = [dict(b=b, ltf=t, lstop=s, flip_dn=f) for b in B_GRID for t in L_TARGETS for s in L_STOPS
             for f in FLIP_DN]
    shorts = [dict(a=a, stf=t, sstop=s, flip_up=f) for a in A_GRID for t in S_TARGETS for s in S_STOPS
              for f in FLIP_UP]
    return ([Variant(mode="L", **lo) for lo in longs] + [Variant(mode="S", **sh) for sh in shorts]
            + [Variant(mode="LS", **lo, **sh) for lo in longs for sh in shorts])


def _step(levels: Sequence[Any], value: Any) -> List[Any]:
    i = levels.index(value)
    return [levels[k] for k in (i - 1, i + 1) if 0 <= k < len(levels)]


def neighbours(v: Variant) -> List[Variant]:
    """One step on each ordered axis the variant uses: the two limit
    positions and the two target shares. Everything else held."""
    out: List[Variant] = []
    if "L" in v.mode:
        out += [dataclasses.replace(v, b=x) for x in _step(B_GRID, v.b)]
        out += [dataclasses.replace(v, ltf=x) for x in _step(L_TARGETS, v.ltf)]
    if "S" in v.mode:
        out += [dataclasses.replace(v, a=x) for x in _step(A_GRID, v.a)]
        out += [dataclasses.replace(v, stf=x) for x in _step(S_TARGETS, v.stf)]
    return out


# ── per-bar series (pure) ──────────────────────────────────────────────────

@dataclass
class Series:
    """Bars plus, per bar, the state its ORDERS were placed on: everything
    below `gi` is read at the governing desk tick, not at the bar."""
    step_ms: int
    t: List[int]
    o: List[float]
    h: List[float]
    l: List[float]
    c: List[float]
    weekend: List[bool]
    fund: List[Optional[float]]
    gi: List[int]                       # index of the bar the governing tick's orders went live on; -1 before the first
    entry: List[Optional[float]]        # the long thesis's entry at the governing tick (None: no range)
    target: List[Optional[float]]
    stop: List[Optional[float]]
    tid: List[Optional[str]]
    atr: List[float]                    # ATR14(1h) at the governing tick; 0 when unknown
    pc: List[Optional[float]]           # the last close before the governing tick
    swing_hi: List[Optional[float]]     # highest high / lowest low of the SWING_HOURS before the bar
    swing_lo: List[Optional[float]]
    flt: Dict[str, List[bool]]          # filter name -> held at the governing tick


def tick_bar(t_ms: int, step_ms: int) -> bool:
    """Is this the first bar a desk tick's orders are live on. 5m bars: the
    :05/:20/:35/:50 bar. 15m bars: every bar."""
    return step_ms != FIVE_MS or t_ms % FIFTEEN_MS == FIVE_MS


def session_of(t_ms: int) -> str:
    """asia 22:00-07:00Z, london 07:00-13:00Z, ny 13:00-22:00Z."""
    hour = (t_ms // HOUR_MS) % 24
    return "london" if 7 <= hour < 13 else "ny" if 13 <= hour < 22 else "asia"


def filter_flags(th: Optional[Th], cron_ms: int, atr: float, trend1h: Optional[int], trend4h: Optional[int],
                 in_eia: bool) -> Dict[str, bool]:
    """Every FILTERS flag for one tick. A flag whose input is missing is
    False (the order does not rest). Pure."""
    rng = thesis_range(th)
    age = (cron_ms - th.created_ms) / HOUR_MS if th is not None else None
    wid = (rng[1] - rng[0]) / atr if rng and atr > 0 else None
    sess = session_of(cron_ms)
    conf = th.conf if th is not None else None
    near = (abs(th.entry - th.px_at_write) / th.px_at_write <= NEAR_WRITE
            if rng and th.px_at_write else None)                                     # type: ignore[union-attr]
    return {"age_lt2h": age is not None and age < 2, "age_2to8h": age is not None and 2 <= age < 8,
            "age_ge8h": age is not None and age >= 8,
            "wid_lt4atr": wid is not None and wid < 4, "wid_ge4atr": wid is not None and wid >= 4,
            "sess_asia": sess == "asia", "sess_london": sess == "london", "sess_ny": sess == "ny",
            "conf_ge50": conf is not None and conf >= 0.5, "conf_lt50": conf is not None and conf < 0.5,
            "post_close": th is not None and th.post_close, "not_post_close": th is not None and not th.post_close,
            "eia_clear": not in_eia,
            "t1h_up": trend1h == 1, "t1h_down": trend1h == -1, "t4h_up": trend4h == 1, "t4h_down": trend4h == -1,
            "near_write": near is True, "far_write": near is False}


def build_series(bars: Sequence[Any], c1h: Sequence[Any], theses: Sequence[Th], step_ms: int,
                 funding: Optional[Sequence[Dict[str, Any]]] = None,
                 eia_windows: Sequence[Tuple[int, int]] = ()) -> Series:
    """The bars with the state of the governing desk tick carried onto
    each: the thesis written by the cron's time, ATR14 and the trend of the
    1h bars closed by then, the filter flags. Pure."""
    ticks = SL.build_ticks(bars, c1h, lambda _t: None, funding)
    hours = sorted(c1h, key=lambda c: c.t)
    hc = [b.c for b in hours]
    e20, e80, a1h = IN._ema(hc, 20), IN._ema(hc, 80), IN._atr(hours, 14)
    hclose = [b.t + HOUR_MS for b in hours]
    th_times = [th.created_ms for th in theses]
    lag = CRON_LAG_5M_MS if step_ms == FIVE_MS else CRON_LAG_15M_MS
    n = len(ticks)
    s = Series(step_ms=step_ms, t=[k.t for k in ticks], o=[k.o for k in ticks], h=[k.h for k in ticks],
               l=[k.l for k in ticks], c=[k.c for k in ticks], weekend=[k.weekend for k in ticks],
               fund=[k.fund for k in ticks], gi=[-1] * n, entry=[None] * n, target=[None] * n, stop=[None] * n,
               tid=[None] * n, atr=[0.0] * n, pc=[None] * n, flt={f: [False] * n for f in FILTERS},
               swing_hi=SR.rolling_high_before([k.t for k in ticks], [k.h for k in ticks], SWING_HOURS * HOUR_MS),
               swing_lo=[None if x is None else -x for x in SR.rolling_high_before(
                   [k.t for k in ticks], [-k.l for k in ticks], SWING_HOURS * HOUR_MS)])
    g = -1
    state: Tuple[Any, ...] = (None, None, None, None, 0.0, None, {f: False for f in FILTERS})
    for i, tk in enumerate(ticks):
        if tick_bar(tk.t, step_ms) and i > 0:
            g = i
            cron = tk.t - lag
            k = bisect.bisect_right(th_times, cron) - 1
            th = theses[k] if k >= 0 else None
            j = bisect.bisect_right(hclose, cron) - 1
            atr = (a1h[j] or 0.0) if j >= 0 else 0.0
            t1 = IN.trend_state(hc[j], e20[j], a1h[j]) if j >= 20 else None
            t4 = IN.trend_state(hc[j], e80[j], a1h[j]) if j >= 80 else None
            rng = thesis_range(th)
            flags = filter_flags(th, cron, atr, t1, t4, any(lo <= cron < hi for lo, hi in eia_windows))
            state = ((rng[0], rng[1], rng[2], th.tid) if rng and th else (None, None, None, None)) + (
                atr, ticks[i - 1].c, flags)
        s.gi[i] = g
        if g >= 0:
            s.entry[i], s.target[i], s.stop[i], s.tid[i], s.atr[i], s.pc[i] = state[:6]
            for f, on in state[6].items():
                s.flt[f][i] = on
    return s


# ── levels (pure) ──────────────────────────────────────────────────────────

def short_stop(rule: str, fill: float, atr: float, top: float) -> Optional[float]:
    """A short's stop. "pN" = N percent over the fill; "atrK" = K x ATR
    over it; "top" = TOP_PAD over the thesis target, never closer than
    MIN_STOP."""
    if rule == "top":
        return max(top * (1.0 + TOP_PAD), fill * (1.0 + MIN_STOP))
    if rule.startswith("atr"):
        return fill + float(rule[3:]) * atr if atr > 0 else None
    return fill * (1.0 + float(rule[1:]) / 100.0)


def long_stop(rule: str, fill: float, atr: float, thesis_stop: Optional[float]) -> Optional[float]:
    """A long's stop. "thesis" = the thesis's own; "pN" / "atrK" as above,
    under the fill."""
    if rule == "thesis":
        return thesis_stop
    if rule.startswith("atr"):
        return fill - float(rule[3:]) * atr if atr > 0 else None
    return fill * (1.0 - float(rule[1:]) / 100.0)


def random_pos(seed: int, leg: str, tid: str) -> float:
    """The baseline's level for one thesis and leg: uniform in the range,
    the same for a given (seed, leg, thesis) on every run. Pure."""
    return random.Random(f"{seed}|{leg}|{tid}").random()


# ── the book: one position, the fills, the exits (shared by both walkers) ──

class Book:
    """One position at a time on a `Series`: sizing, fees, funding, forced
    closes and the stop / target check of a bar. The walkers decide what to
    open; the book prices it."""

    def __init__(self, s: Series, p: Params, start_equity: float, lo_i: int, live: Optional[SR.Live] = None,
                 midbar_worst: bool = False) -> None:
        self.s, self.p, self.start, self.live, self.worst = s, p, start_equity, live, midbar_worst
        self.pos: Optional[Dict[str, Any]] = None
        self.pnl = 0.0
        self.free_from = lo_i - 1            # a tick must be newer than this bar to place new orders
        self.trades: List[Dict[str, Any]] = []
        self.refused: Counter = Counter()
        self.curve: List[float] = []

    def equity(self, x: int) -> float:
        return (self.live.eq[x] if self.live is not None else self.start) + self.pnl

    def market_slip(self, x: int, notional: float) -> float:
        """A desk market order: the size curve, never under half the bar's range."""
        s = self.s
        base = _market_exit_slip(self.p, notional)
        return max(base, (s.h[x] - s.l[x]) / s.o[x] / 2.0) if s.o[x] > 0 else base

    def blocked(self, x: int) -> bool:
        return self.s.weekend[x] or (self.live is not None and self.live.long_open[x])

    def open(self, leg: str, side: int, x: int, ref: float, fill: float, stop: Optional[float],
             target: Optional[float], midbar: bool, maker: bool, share: float = MARGIN_SHARE, **extra: Any) -> bool:
        """Open `leg` at `fill` (ref = the price before slippage). Refused
        without a level, with a stop outside MIN_STOP..MAX_STOP or with
        under MIN_ROOM to the target. `midbar`: filled by a resting order
        inside the bar that closed the leg before it."""
        p, eq = self.p, self.equity(x)
        if eq <= 0 or fill <= 0 or stop is None or target is None:
            self.refused[leg + "_no_level"] += 1
            return False
        dist = side * (fill - stop)
        if not MIN_STOP - EPS <= dist / fill <= MAX_STOP + EPS:
            self.refused[leg + "_stop_range"] += 1
            return False
        if side * (target - fill) < MIN_ROOM * fill:
            self.refused[leg + "_no_room"] += 1
            return False
        notional = share * eq * p.leverage
        size = notional / fill
        self.pos = {"leg": leg, "side": side, "j": x, "ref": ref, "fill": fill, "stop": stop, "target": target,
                    "size": size, "notional": notional, "fees": (p.maker_fee if maker else p.taker_fee) * notional,
                    "funding": 0.0, "midbar": midbar, "eq": eq, "risk": dist * size, "best": fill,
                    "tid": None, "rpos": None, **extra}
        return True

    def close(self, x: int, ref: float, px: float, reason: str, maker: bool = False) -> Dict[str, Any]:
        s, p = self.s, self.p
        q, self.pos = self.pos, None
        assert q is not None
        self.free_from = x
        side, size = q["side"], q["size"]
        done = reason != "open"
        fees = q["fees"] + ((p.maker_fee if maker else p.taker_fee) * px * size if done else 0.0)
        raw = side * (ref - q["ref"]) * size
        gross = side * (px - q["fill"]) * size
        net = gross - fees - q["funding"]
        self.pnl += net
        exit_ms = s.t[x] if done else None
        self.trades.append({
            "leg": q["leg"], "side": "long" if side > 0 else "short", "tid": q["tid"], "pos": q["rpos"],
            "entry_ms": s.t[q["j"]], "exit_ms": exit_ms, "entry": _iso(s.t[q["j"]]), "exit": _iso(exit_ms),
            "entry_px": round(q["fill"], 4), "exit_px": round(px, 4), "stop_px": round(q["stop"], 4),
            "target_px": round(q["target"], 4), "size": size, "notional": q["notional"],
            "margin_pct": q["notional"] / p.leverage / q["eq"] * 100.0, "reason": reason, "raw_usd": raw,
            "slip_usd": raw - gross, "fees_usd": fees, "funding_usd": q["funding"], "net_usd": net,
            "risk_usd": q["risk"], "risk_pct": q["risk"] / q["eq"] * 100.0,
            "r": net / q["risk"] if q["risk"] > 1e-12 else None, "i": q["j"], "x": x,
            "hold_min": (s.t[x] - s.t[q["j"]]) / 60_000.0})
        return q

    def carry(self, x: int) -> Optional[str]:
        """Start of a bar: funding on the hour, then the forced closes at
        the open (the live long enters, the weekend starts). Returns the
        reason when it closed the position."""
        s, pos = self.s, self.pos
        if pos is None or x <= pos["j"]:
            return None
        if s.fund[x] is not None:
            pos["funding"] += pos["side"] * s.fund[x] * pos["size"] * s.o[x]
        why = ("live_long" if self.live is not None and self.live.long_entry[x]
               else "weekend" if s.weekend[x] else None)
        if why:
            self.close(x, s.o[x], s.o[x] * (1.0 - pos["side"] * self.market_slip(x, pos["notional"])), why)
        return why

    def step(self, x: int) -> Optional[Tuple[str, Dict[str, Any], float]]:
        """The open position against bar x: the stop first (a limit's own
        fill bar counts whole; a resting flip's fill bar only by its close,
        unless `midbar_worst`; a bar that opens beyond the stop fills at
        its open), then the target (a trade-through, never on the fill
        bar). Returns (reason, the closed position, the exit's reference
        price) or None when it stays open."""
        s, pos = self.s, self.pos
        assert pos is not None
        side, same = pos["side"], x == pos["j"]
        if same and pos["midbar"] and not self.worst:
            stopped = side * (s.c[x] - pos["stop"]) <= 0
        else:
            stopped = side * ((s.l[x] if side > 0 else s.h[x]) - pos["stop"]) <= 0
        if stopped:
            ref = pos["stop"] if same else (min(pos["stop"], s.o[x]) if side > 0 else max(pos["stop"], s.o[x]))
            q = self.close(x, ref, ref * (1.0 - side * _market_exit_slip(self.p, pos["size"] * ref)), "stop")
            return "stop", q, ref
        if not same and side * ((s.h[x] if side > 0 else s.l[x]) - pos["target"]) >= self.p.tick_size - 1e-9:
            q = self.close(x, pos["target"], pos["target"], "target", maker=True)
            return "target", q, q["target"]
        return None

    def mark(self, x: int) -> None:
        """End of a bar: the best price since the fill (the fill bar counts
        by its close only: its extreme may predate the fill) and the
        marked P&L."""
        s, pos = self.s, self.pos
        if pos is not None:
            hi, lo = (s.c[x], s.c[x]) if x == pos["j"] else (s.h[x], s.l[x])
            pos["best"] = max(pos["best"], hi) if pos["side"] > 0 else min(pos["best"], lo)
        self.curve.append(self.pnl + (pos["side"] * (s.c[x] - pos["fill"]) * pos["size"] if pos else 0.0))

    def finish(self, hi_i: int) -> Dict[str, Any]:
        if self.pos is not None:
            self.close(hi_i, self.s.c[hi_i], self.s.c[hi_i], "open")
            if self.curve:
                self.curve[-1] = self.pnl
        return {"trades": self.trades, "refused": dict(self.refused), "curve": self.curve}


def range_at(s: Series, x: int, rand_seed: Optional[int] = None
             ) -> Optional[Tuple[float, float, Optional[float], str]]:
    """(entry, target, stop, thesis id) governing bar x. With `rand_seed`
    the whole range is displaced by a seeded uniform draw of up to one
    range width either way: the ladder's random-level baseline (the same
    width, the same thesis clock, levels that are not the thesis's)."""
    e, tg = s.entry[x], s.target[x]
    if e is None or tg is None:
        return None
    st, tid = s.stop[x], s.tid[x]
    if rand_seed is not None:
        d = (2.0 * random_pos(rand_seed, "range", tid) - 1.0) * (tg - e)                 # type: ignore[arg-type]
        return e + d, tg + d, (st + d if st is not None else None), tid                 # type: ignore[return-value]
    return e, tg, st, tid                                                               # type: ignore[return-value]


# ── walker 1: the range rule (limits at the thesis levels) ─────────────────

def walk(s: Series, v: Variant, p: Params, start_equity: float, lo_i: int = 0, hi_i: Optional[int] = None,
         live: Optional[SR.Live] = None, rand_seed: Optional[int] = None, midbar_worst: bool = False
         ) -> Dict[str, Any]:
    """One range variant over bars [lo_i, hi_i], one position at a time.

    Per bar: (1) `Book.carry`. (2) Flat: the limits the governing tick
    placed. A tick places nothing before `lo_i`, on a weekend, while the
    live long is open, on a thesis this leg already traded, or on the near
    side of the last close; and after a close only the next tick places
    again. A sell limit fills on a trade-through up, a buy limit on one
    down; both on one bar is no trade. (3) `Book.step`. A stopped short
    starts the flip-up, a stopped long the flip-down, both resting
    stop-entries filled on that bar.

    `rand_seed` draws each thesis's limit position uniformly in the range
    (`random_pos`) instead of the variant's a / b: the random-level
    baseline. Returns {"trades", "refused", "curve"}."""
    h, l = s.h, s.l
    hi_i = len(s.t) - 1 if hi_i is None else hi_i
    tick = p.tick_size
    bk = Book(s, p, start_equity, lo_i, live, midbar_worst)
    used = {"long": set(), "short": set()}
    want_long, want_short = "L" in v.mode, "S" in v.mode

    def limits(x: int) -> Optional[Dict[str, Any]]:
        g = s.gi[x]
        e, tg = s.entry[x], s.target[x]
        if g < lo_i or g <= bk.free_from or e is None or tg is None or s.pc[x] is None:
            return None
        if s.weekend[g] or bk.blocked(x) or any(not s.flt[f][x] for f in v.filt):
            return None
        tid = s.tid[x]
        out: Dict[str, Any] = {}
        if want_short and tid not in used["short"]:
            a = random_pos(rand_seed, "short", tid) if rand_seed is not None else v.a          # type: ignore[arg-type]
            lvl = level_at(e, tg, a)
            if s.pc[x] < lvl - EPS:
                out["short"] = (lvl, a)
        if want_long and tid not in used["long"]:
            b = random_pos(rand_seed, "long", tid) if rand_seed is not None else v.b            # type: ignore[arg-type]
            lvl = level_at(e, tg, b)
            if s.pc[x] > lvl + EPS:
                out["long"] = (lvl, b)
        return out or None

    for x in range(lo_i, hi_i + 1):
        bk.carry(x)
        if bk.pos is None:
            orders = limits(x)
            if orders:
                e, tg, st, tid = s.entry[x], s.target[x], s.stop[x], s.tid[x]
                sell = "short" in orders and h[x] >= orders["short"][0] + tick - 1e-9
                buy = "long" in orders and l[x] <= orders["long"][0] - tick + 1e-9
                if sell and buy:
                    bk.refused["both_sides"] += 1
                elif sell:
                    lvl, a = orders["short"]
                    if bk.open("short", -1, x, lvl, lvl, short_stop(v.sstop, lvl, s.atr[x], tg),
                               lvl - v.stf * (lvl - e), False, True, tid=tid, rpos=a, top=tg, floor=e):
                        used["short"].add(tid)
                elif buy:
                    lvl, b = orders["long"]
                    if bk.open("long", 1, x, lvl, lvl, long_stop(v.lstop, lvl, s.atr[x], st),
                               lvl + v.ltf * (tg - lvl), False, True, tid=tid, rpos=b, top=tg, floor=e):
                        used["long"].add(tid)
        while bk.pos is not None:
            ev = bk.step(x)
            if ev is None or ev[0] != "stop" or s.weekend[x]:
                break
            _, q, ref = ev
            slip = _market_exit_slip(p, q["notional"])
            if q["leg"] == "short" and v.flip_up != "off":
                fill = ref * (1.0 + slip)
                stop = q["fill"] * (1.0 - ENTRY_PAD) if v.flip_up == "sentry" else long_stop(
                    v.flip_up, fill, s.atr[x], None)
                if not bk.open("flip_up", 1, x, ref, fill, stop, q["top"], True, False, tid=q["tid"],
                               rpos=range_pos(fill, q["floor"], q["top"]), top=q["top"], floor=q["floor"]):
                    break
            elif q["leg"] == "long" and v.flip_dn:
                fill = ref * (1.0 - slip)
                if not bk.open("flip_dn", -1, x, ref, fill, q["fill"] * (1.0 + ENTRY_PAD),
                               fill - (q["fill"] - q["stop"]), True, False, tid=q["tid"],
                               rpos=range_pos(fill, q["floor"], q["top"]), top=q["top"], floor=q["floor"]):
                    break
            else:
                break
        bk.mark(x)
    return bk.finish(hi_i)


# ── walker 2: the operator's ladder ────────────────────────────────────────

@dataclass(frozen=True)
class Ladder:
    """The operator's spec, 2026-11-16: (1) short toward the thesis entry;
    (2) stop "5%" of the position's margin; (3) stopped: long toward the
    thesis high, the stop ratcheted up; (4) at the thesis high: short back
    to the thesis entry and keep bouncing until the thesis is rewritten or
    a stop breaks the range."""
    entry: str = "mkt0.75"         # O_ENTRIES: "mktX" a market order at a tick with the close X% over the thesis
    #                                entry; "limA" a resting sell limit at range position A
    ssize: float = 0.20            # every short's margin, share of equity at 20x
    stop: str = "p0.25"            # O_STOPS: the stop width of every leg (the long's is the mirror)
    fsize: float = 0.20            # every long's margin
    ratchet: str = "be50"          # O_RATCHETS: the long's stop, moved at the desk tick
    fill: str = "tick"             # O_FILLS: how a flip or a bounce leg is entered
    bounce: bool = True            # step 4 on or off
    filt: Tuple[str, ...] = ()     # O_FILTERS on the first short

    @property
    def name(self) -> str:
        return (f"O_{self.entry}_s{self.ssize * 100:g}_{self.stop}_f{self.fsize * 100:g}_{self.ratchet}_"
                f"{self.fill}_{'bounce' if self.bounce else 'once'}" + "".join("+" + f for f in self.filt))

    @property
    def family(self) -> str:
        return f"O_{self.fill}" + ("_bounce" if self.bounce else "") + ("_filtered" if self.filt else "")


O_ENTRIES = ("mkt0.75", "lim0.33", "lim0.5", "lim0.75")
O_SIZES = (0.05, 0.10, 0.20)
O_STOPS = ("p0.25", "p0.36", "p0.375", "p0.50", "swing")     # 5 / 7.2 / 7.5 / 10% of margin at 20x, the 4h swing
O_FSIZES = (0.10, 0.20, 0.30)
O_RATCHETS = ("none", "be50", "be75", "trail1.0", "trail2.0")
O_FILLS = ("resting", "tick")
O_FILTERS = ("age_lt2h", "age_2to8h", "age_ge8h", "wid_lt4atr", "wid_ge4atr", "sess_asia", "sess_london",
             "sess_ny")
SWING_PAD = 0.0005
OPERATOR = Ladder()                # the exact spec: 0.25% stop, 20% long, break-even at half way, by hand, bounce


def ladder_variants() -> List[Ladder]:
    return [Ladder(e, ss, st, fs, r, f, b, flt) for flt in [()] + [(x,) for x in O_FILTERS] for e in O_ENTRIES
            for ss in O_SIZES for st in O_STOPS for fs in O_FSIZES for r in O_RATCHETS for f in O_FILLS
            for b in (False, True)]


def ladder_neighbours(v: Ladder) -> List[Ladder]:
    """One step on each ordered axis: the percent stops, the two sizes,
    the limit positions."""
    out = [dataclasses.replace(v, ssize=x) for x in _step(O_SIZES, v.ssize)]
    out += [dataclasses.replace(v, fsize=x) for x in _step(O_FSIZES, v.fsize)]
    if v.stop != "swing":
        out += [dataclasses.replace(v, stop=x) for x in _step(O_STOPS[:4], v.stop)]
    if v.entry.startswith("lim"):
        out += [dataclasses.replace(v, entry=x) for x in _step(O_ENTRIES[1:], v.entry)]
    return out


def leg_stop(rule: str, side: int, fill: float, swing: Optional[float]) -> Optional[float]:
    """A ladder leg's stop. "pN" = N percent beyond the fill (0.25% is 5%
    of the margin at 20x); "swing" = SWING_PAD beyond the 4h swing high
    (short) or low (long), never closer than MIN_STOP."""
    if rule == "swing":
        if swing is None:
            return None
        if side < 0:
            return max(swing * (1.0 + SWING_PAD), fill * (1.0 + MIN_STOP))
        return min(swing * (1.0 - SWING_PAD), fill * (1.0 - MIN_STOP))
    return fill * (1.0 - side * float(rule[1:]) / 100.0)


def ratchet_stop(rule: str, fill: float, target: float, stop: float, best: float, atr: float) -> float:
    """A long's stop after the ratchet, never lower than it was. "beN":
    break-even once the best price has covered N% of the way to the
    target; "trailK": K x ATR under the best price. Pure."""
    if rule.startswith("be"):
        return max(stop, fill) if best >= fill + float(rule[2:]) / 100.0 * (target - fill) - EPS else stop
    if rule.startswith("trail"):
        return max(stop, best - float(rule[5:]) * atr) if atr > 0 else stop
    return stop


def walk_ladder(s: Series, v: Ladder, p: Params, start_equity: float, lo_i: int = 0, hi_i: Optional[int] = None,
                live: Optional[SR.Live] = None, rand_seed: Optional[int] = None, midbar_worst: bool = False
                ) -> Dict[str, Any]:
    """The operator's ladder over bars [lo_i, hi_i], one position at a time.

    short     Once per thesis. "mktX": at a desk tick with the last close
              at least X% over the thesis entry and under the thesis
              target, a market order (the bar's open less half its range).
              "limA": a resting sell limit at range position A, as in
              `walk`. Target = the thesis entry, a resting limit.
    flip      The short's stop fills: long toward the thesis target in
              force. "resting": a buy-stop at the same price, same bar.
              "tick": a market order on the next desk tick. Its stop is
              the mirror of the short's; `v.ratchet` moves it at each
              later tick from the best price of the bars before.
    bounce    The long's target fills and the thesis is the same one:
              short at the thesis target back to the thesis entry
              ("resting": the sell limit at the target, maker; "tick":
              market on the next tick); that short's target fills: long
              from the entry to the target; and so on. A stop on any
              bounce leg, a new thesis, the weekend or the live long ends
              the cycle.

    Shorts are `v.ssize` of equity as margin, longs `v.fsize`. `rand_seed`
    displaces every range (`range_at`). Returns {"trades", "refused",
    "curve"}."""
    o = s.o
    hi_i = len(s.t) - 1 if hi_i is None else hi_i
    tick = p.tick_size
    bk = Book(s, p, start_equity, lo_i, live, midbar_worst)
    used: set = set()
    pending: Optional[Dict[str, Any]] = None
    market = v.entry.startswith("mkt")
    level = float(v.entry[3:])
    resting = v.fill == "resting"

    def next_tick(x: int) -> int:
        y = x + 1
        while y <= hi_i and s.gi[y] != y:
            y += 1
        return y

    def swing(x: int, side: int) -> Optional[float]:
        return s.swing_hi[x] if side < 0 else s.swing_lo[x]

    def first_short(x: int) -> None:
        g = s.gi[x]
        rng = range_at(s, x, rand_seed)
        if g < lo_i or g <= bk.free_from or rng is None or s.pc[x] is None or rng[3] in used:
            return
        if s.weekend[g] or bk.blocked(x) or any(not s.flt[f][x] for f in v.filt):
            return
        e, tg, _, tid = rng
        if market:
            if g != x or not e * (1.0 + level / 100.0) <= s.pc[x] < tg:
                return
            ref = o[x]
            fill = ref * (1.0 - bk.market_slip(x, v.ssize * max(bk.equity(x), 0.0) * p.leverage))
            maker = False
        else:
            ref = fill = level_at(e, tg, level)
            if not s.pc[x] < fill - EPS or s.h[x] < fill + tick - 1e-9:
                return
            maker = True
        if bk.open("short", -1, x, ref, fill, leg_stop(v.stop, -1, fill, swing(x, -1)), e, False, maker,
                   v.ssize, tid=tid, rpos=range_pos(fill, e, tg), floor=e, top=tg):
            used.add(tid)

    def follow(x: int, leg: str, side: int, ref: Optional[float], maker: bool, q: Dict[str, Any]) -> bool:
        """Open the leg after `q`. ref None = a market order at this bar's
        open; else a resting order filled at `ref` inside the bar."""
        midbar = ref is not None
        rng = range_at(s, x, rand_seed)
        if rng is None or (leg != "flip" and rng[3] != q["tid"]):
            bk.refused[leg + "_thesis_moved"] += 1
            return False
        if leg == "flip":
            floor, top, tid = rng[0], rng[1], rng[3]
        else:
            floor, top, tid = q["floor"], q["top"], q["tid"]
        share = v.fsize if side > 0 else v.ssize
        if midbar:
            fill = ref if maker else ref * (1.0 + side * _market_exit_slip(p, q["notional"]))
        else:
            if bk.blocked(x):
                bk.refused[leg + "_blocked"] += 1
                return False
            ref = o[x]
            fill = ref * (1.0 + side * bk.market_slip(x, share * max(bk.equity(x), 0.0) * p.leverage))
        return bk.open(leg, side, x, ref, fill, leg_stop(v.stop, side, fill, swing(x, side)),
                       top if side > 0 else floor, midbar, maker, share, tid=tid,
                       rpos=range_pos(fill, floor, top), floor=floor, top=top)

    for x in range(lo_i, hi_i + 1):
        if bk.carry(x):
            pending = None
        if pending is not None and pending["x"] == x:
            job, pending = pending, None
            follow(x, job["leg"], job["side"], None, False, job["from"])
        if bk.pos is None and pending is None:
            first_short(x)
        pos = bk.pos
        if pos is not None and pos["side"] > 0 and x > pos["j"] and s.gi[x] == x and v.ratchet != "none":
            pos["stop"] = ratchet_stop(v.ratchet, pos["fill"], pos["target"], pos["stop"], pos["best"], s.atr[x])
        while bk.pos is not None:
            ev = bk.step(x)
            if ev is None:
                break
            reason, q, ref = ev
            if reason == "stop" and q["leg"] == "short":
                nxt = ("flip", 1, False)
            elif reason == "target" and v.bounce and q["side"] > 0:
                nxt = ("bshort", -1, True)
            elif reason == "target" and v.bounce and q["leg"] == "bshort":
                nxt = ("blong", 1, True)
            else:
                break
            if s.weekend[x]:
                break
            if not resting:
                y = next_tick(x)
                if y <= hi_i:
                    pending = {"x": y, "leg": nxt[0], "side": nxt[1], "from": q}
                break
            if not follow(x, nxt[0], nxt[1], ref, nxt[2], q):
                break
        bk.mark(x)
    return bk.finish(hi_i)


def walk_any(s: Series, v: Any, p: Params, start_equity: float, lo_i: int = 0, hi_i: Optional[int] = None,
             **kw: Any) -> Dict[str, Any]:
    return (walk_ladder if isinstance(v, Ladder) else walk)(s, v, p, start_equity, lo_i, hi_i, **kw)


# ── segments, summaries, the search ────────────────────────────────────────

def brief(run: Dict[str, Any], start: float, mid_ms: Optional[int] = None) -> Dict[str, Any]:
    """What a variant did on one segment, small enough to keep for every
    variant: n, wins, net, max drawdown, average R, profit factor, the legs
    and (with `mid_ms`) the net of each half by entry time."""
    trades = run["trades"]
    sm = IN.summarize(trades, run["curve"], [start] * len(run["curve"]))
    legs = {}
    for leg in LEGS:
        ts = [tr for tr in trades if tr["leg"] == leg]
        if ts:
            legs[leg] = [len(ts), sum(1 for tr in ts if tr["net_usd"] > 0), round(sum(tr["net_usd"] for tr in ts), 2)]
    out = {"n": sm["n"], "wins": sm["wins"], "win_rate": sm["win_rate"], "net_usd": sm["net_usd"],
           "max_dd_pct": sm.get("max_dd_pct", 0.0), "avg_r": sm["avg_r"], "profit_factor": sm["profit_factor"],
           "legs": legs}
    if mid_ms is not None:
        out["halves"] = [round(sum(tr["net_usd"] for tr in trades if tr["entry_ms"] < mid_ms), 2),
                         round(sum(tr["net_usd"] for tr in trades if tr["entry_ms"] >= mid_ms), 2)]
    return out


def run_segment(v: Variant, seg: Dict[str, Any], rand_seed: Optional[int] = None, keep: bool = False,
                **kw: Any) -> Dict[str, Any]:
    p = seg["params"]
    run = walk_any(seg["series"], v, p, p.starting_equity, seg["lo"], seg["hi"], rand_seed=rand_seed, **kw)
    out = brief(run, p.starting_equity, seg.get("mid_ms"))
    if keep:
        out["trades"] = run["trades"]
    return out


_POOL: Dict[str, Any] = {}


def _pool_row(v: Variant) -> Tuple[str, Dict[str, Any]]:
    return v.name, {name: run_segment(v, _POOL["segs"][name]) for name in _POOL["names"]}


def evaluate(vs: Sequence[Variant], segs: Dict[str, Dict[str, Any]], names: Sequence[str], workers: int = 1,
             log: Any = None, label: str = "") -> Dict[str, Dict[str, Any]]:
    """name -> {segment -> brief} for `names` ONLY: a caller that passes the
    search segments cannot see a holdout number."""
    out: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    if workers > 1 and len(vs) > 32:
        _POOL.clear()
        _POOL.update({"segs": segs, "names": list(names)})
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            for k, (name, row) in enumerate(pool.imap(_pool_row, vs, chunksize=16), 1):
                out[name] = row
                if log and k % 500 == 0:
                    el = time.time() - t0
                    log(f"{label} {k}/{len(vs)} ({k / len(vs) * 100:.0f}%), {el:.0f}s in, "
                        f"about {el / k * (len(vs) - k):.0f}s left")
    else:
        for v in vs:
            out[v.name] = {name: run_segment(v, segs[name]) for name in names}
    return out


def selectable(row: Dict[str, Any]) -> bool:
    """The search-set gate: at least MIN_N trades and both halves above
    zero on the SEARCH segment."""
    d = row[SEARCH]
    return d["n"] >= MIN_N and all(x > EPS for x in d.get("halves", ()))


def select(rows: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The ONE rule: the highest net on the SEARCH segment among the
    selectable variants; without one, the highest net with MIN_N trades.
    The name breaks ties. Reads `rows[name][SEARCH]` and nothing else."""
    pool = [n for n, r in rows.items() if selectable(r)] or [n for n, r in rows.items() if r[SEARCH]["n"] >= MIN_N]
    if not pool:
        return None
    return min(pool, key=lambda n: (-rows[n][SEARCH]["net_usd"], n))


def carriers(base: Sequence[Variant], rows: Dict[str, Dict[str, Any]]) -> List[Variant]:
    """What the filters are tried on: every single-leg variant and the
    CARRIERS_LS best two-leg ones by search net."""
    two = sorted((v for v in base if v.mode == "LS"), key=lambda v: (-rows[v.name][SEARCH]["net_usd"], v.name))
    return [v for v in base if v.mode != "LS"] + two[:CARRIERS_LS]


def pooled(rows: Dict[str, Dict[str, Any]], names: Sequence[str], seg: str) -> Dict[str, Any]:
    """All the named variants' trades on one segment as one pool."""
    n = sum(rows[x][seg]["n"] for x in names)
    wins = sum(rows[x][seg]["wins"] for x in names)
    net = sum(rows[x][seg]["net_usd"] for x in names)
    return {"n": n, "win_rate": round(wins / n * 100.0, 1) if n else None, "net_usd": round(net, 2),
            "exp_usd": round(net / n, 3) if n else None,
            "above_zero": sum(1 for x in names if rows[x][seg]["net_usd"] > EPS), "variants": len(names)}


def rank_filters(rows: Dict[str, Dict[str, Any]], carrier_names: Sequence[str], filters: Sequence[str] = FILTERS,
                 thin: float = 3.0) -> List[str]:
    """Filters by the pooled expectancy per trade of the carriers under
    them on the SEARCH segment, best first (a filter that only thins trades
    leaves expectancy where it was, so this does not reward trading less).
    A filter whose pool averages under THIN trades per carrier is ranked
    last: too few trades to read."""
    def key(f: str) -> Tuple[bool, float, str]:
        pl = pooled(rows, [f"{c}+{f}" for c in carrier_names], SEARCH)
        return pl["n"] < thin * len(carrier_names), -(pl["exp_usd"] if pl["exp_usd"] is not None else -1e9), f
    return sorted(filters, key=key)


def combos(top: Sequence[str]) -> List[Tuple[str, ...]]:
    """Every pair and the triple of the top filters, skipping pairs from
    one group (two ages, two sessions: an empty set)."""
    def group(f: str) -> str:
        return f.split("_")[0] if not f.endswith("post_close") else "post"
    out: List[Tuple[str, ...]] = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            if group(top[i]) != group(top[j]):
                out.append((top[i], top[j]))
    if len(top) >= 3 and len({group(f) for f in top[:3]}) == 3:
        out.append(tuple(top[:3]))
    return out


# ── the deflated view and the baseline (pure) ──────────────────────────────

def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = sum((a - mx) ** 2 for a in rx)
    sy = sum((b - my) ** 2 for b in ry)
    if sx <= 0 or sy <= 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sx * sy), 3)


def percentile_of(value: float, sample: Sequence[float]) -> Optional[float]:
    """Share of `sample` strictly under `value`, percent."""
    return round(sum(1 for x in sample if x < value - EPS) / len(sample) * 100.0, 1) if sample else None


def quantile(sample: Sequence[float], pct: float) -> Optional[float]:
    if not sample:
        return None
    xs = sorted(sample)
    k = (len(xs) - 1) * pct / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def deflated(rows: Dict[str, Dict[str, Any]], chosen: str, top_k: int = TOP_K) -> Dict[str, Any]:
    """Best of N against the field. `rows` hold every segment. The search's
    best `top_k` (by search net among those with MIN_N trades) are followed
    onto the holdout; the rank correlation says whether search order
    carries over at all."""
    names = [n for n, r in rows.items() if r[SEARCH]["n"] >= MIN_N]
    snet = [rows[n][SEARCH]["net_usd"] for n in names]
    top = sorted(names, key=lambda n: (-rows[n][SEARCH]["net_usd"], n))[:top_k]
    out: Dict[str, Any] = {"variants": len(rows), "with_min_n_on_search": len(names),
                           "selectable": sum(1 for r in rows.values() if selectable(r)),
                           "search": {"chosen_usd": rows[chosen][SEARCH]["net_usd"],
                                      "median_usd": round(statistics.median(snet), 2) if snet else None,
                                      "above_zero": sum(1 for x in snet if x > EPS)},
                           "holdout": {}}
    for seg in HOLDOUT_SEGS:
        hnet = [rows[n][seg]["net_usd"] for n in names]
        tnet = [rows[n][seg]["net_usd"] for n in top]
        out["holdout"][seg] = {
            "chosen_usd": rows[chosen][seg]["net_usd"], "chosen_n": rows[chosen][seg]["n"],
            "chosen_percentile": percentile_of(rows[chosen][seg]["net_usd"], hnet),
            "median_usd": round(statistics.median(hnet), 2) if hnet else None,
            "above_zero_pct": round(sum(1 for x in hnet if x > EPS) / len(hnet) * 100.0, 1) if hnet else None,
            "top_k_median_usd": round(statistics.median(tnet), 2) if tnet else None,
            "top_k_above_zero_pct": round(sum(1 for x in tnet if x > EPS) / len(tnet) * 100.0, 1) if tnet else None,
            "spearman_search_vs_holdout": spearman(snet, hnet)}
    return out


def _pool_draw(seed: int) -> Dict[str, Tuple[float, Optional[float], int]]:
    out = {}
    for name in _POOL["names"]:
        d = run_segment(_POOL["v"], _POOL["segs"][name], rand_seed=seed)
        out[name] = (d["net_usd"], d["avg_r"], d["n"])
    return out


def baseline(v: Any, segs: Dict[str, Dict[str, Any]], names: Sequence[str], draws: int = BASELINE_DRAWS,
             workers: int = 1) -> Dict[str, Any]:
    """The variant against itself at random levels: per segment the rule's
    net and average R, the baseline's median and BASELINE_PCT percentile
    over `draws` seeded draws (seeds 0..draws-1), and the rule's percentile
    among them. A range variant draws its limit positions (`random_pos`),
    a ladder has its whole range displaced (`range_at`). "pool" is
    BASE_POOL summed (the holdout data)."""
    real = {n: run_segment(v, segs[n]) for n in names}
    _POOL.clear()
    _POOL.update({"segs": segs, "names": list(names), "v": v})
    if workers > 1 and draws >= 16:
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            got = pool.map(_pool_draw, range(draws), chunksize=4)
    else:
        got = [_pool_draw(k) for k in range(draws)]
    out: Dict[str, Any] = {"draws": draws, "segments": {}}

    def cell(net: float, r: Optional[float], n: int, nets: Sequence[float], rs: Sequence[float], ns: Sequence[int]
             ) -> Dict[str, Any]:
        p95 = quantile(nets, BASELINE_PCT)
        assert p95 is not None
        return {"rule_usd": round(net, 2), "rule_avg_r": r, "rule_n": n,
                "baseline_median_usd": round(statistics.median(nets), 2), "baseline_p95_usd": round(p95, 2),
                "baseline_above_zero_pct": round(sum(1 for x in nets if x > EPS) / len(nets) * 100.0, 1),
                "baseline_median_avg_r": round(statistics.median(rs), 3) if rs else None,
                "baseline_median_n": statistics.median(ns),
                "rule_percentile": percentile_of(net, nets),
                "rule_r_percentile": percentile_of(r, rs) if r is not None and rs else None,
                "beats_p95": net > p95 + EPS}
    for n in names:
        out["segments"][n] = cell(real[n]["net_usd"], real[n]["avg_r"], real[n]["n"], [g[n][0] for g in got],
                                  [g[n][1] for g in got if g[n][1] is not None], [g[n][2] for g in got])
    pool_names = [n for n in BASE_POOL if n in names]
    if pool_names:
        out["pool"] = cell(sum(real[n]["net_usd"] for n in pool_names), None, sum(real[n]["n"] for n in pool_names),
                           [sum(g[n][0] for n in pool_names) for g in got], [],
                           [sum(g[n][2] for n in pool_names) for g in got])
        out["pool"]["segments"] = pool_names
    return out


def holds(row: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """The legs a rule must clear to count. Pure."""
    legs = {"search_both_halves": all(x > EPS for x in row[SEARCH].get("halves", ())) and bool(row[SEARCH].get("halves")),
            "search_min_n": row[SEARCH]["n"] >= MIN_N,
            **{f"{seg}_above_zero": row[seg]["net_usd"] > EPS for seg in HOLD_MONEY},
            "beats_random_levels": bool(base.get("pool", {}).get("beats_p95"))}
    return {"legs": legs, "holds": all(legs.values()),
            "holdout_trades": sum(row[seg]["n"] for seg in HOLD_MONEY)}


# ── the account's own trades against the thesis levels (pure) ──────────────

def label_entry(pos: Optional[float]) -> str:
    """Where a fill sat in the thesis range."""
    if pos is None:
        return "no_range"
    if pos < -0.1:
        return "below_entry"
    if pos <= 0.1:
        return "at_entry"
    if pos < 0.9:
        return "mid_range"
    if pos <= 1.1:
        return "at_target"
    return "above_target"


def slice_label(n: int, net: float, wins: int) -> str:
    """Small-n honesty: under MIN_N trades a slice is LEANING (net and most
    trades agree) or NOISE, never an edge."""
    if n == 0:
        return "NONE"
    agree = (net > 0 and wins * 2 > n) or (net < 0 and wins * 2 < n)
    if n < MIN_N:
        return ("LEANING " + ("+" if net > 0 else "-")) if agree else "NOISE"
    return ("EDGE?" if net > 0 else "LEAK?") if agree else "NOISE"


def score_hand_trades(rows: Sequence[Dict[str, Any]], theses: Sequence[Th], coin: str = BRENT) -> Dict[str, Any]:
    """Each closed `coin` trip of the review ledger against the thesis in
    force when it was entered: range position of the fill, the label, how
    far the exit got toward the thesis target (longs) or entry (shorts),
    return in bps of notional. Then the slices by label and side."""
    times = [th.created_ms for th in theses]
    seen: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if isinstance(r, dict) and r.get("id") and r.get("coin") == coin:
            seen[r["id"]] = r
    out: List[Dict[str, Any]] = []
    for r in sorted(seen.values(), key=lambda x: x["entry_ms"]):
        k = bisect.bisect_right(times, int(r["entry_ms"])) - 1
        th = theses[k] if k >= 0 else None
        rng = thesis_range(th)
        px = float(r.get("avg_entry_px") or r["entry_px"])
        pos = range_pos(px, rng[0], rng[1]) if rng else None
        side = r.get("side")
        ex = float(r.get("exit_px") or 0.0)
        goal = (rng[1] if side == "long" else rng[0]) if rng else None
        reach = ((ex - px) / (goal - px) if goal is not None and abs(goal - px) > EPS else None)
        notional = float(r.get("notional_usd") or 0.0)
        net = float(r.get("net_usd") or 0.0)
        out.append({"entry": r.get("entry"), "side": side, "entry_px": round(px, 3), "exit_px": round(ex, 3),
                    "exit_kind": r.get("exit_kind"), "thesis": _iso(th.created_ms) if th else None,
                    "thesis_entry": rng[0] if rng else None, "thesis_target": rng[1] if rng else None,
                    "pos": round(pos, 3) if pos is not None else None, "label": label_entry(pos),
                    "own_target_px": r.get("target_px"),
                    "target_is_thesis": bool(rng and r.get("target_px") is not None
                                             and abs(float(r["target_px"]) - rng[1]) <= 0.0005 * rng[1]),
                    "reach": round(reach, 2) if reach is not None else None,
                    "net_usd": round(net, 2), "notional_usd": round(notional, 0),
                    "net_bps": round(net / notional * 1e4, 1) if notional > 0 else None,
                    "r": r.get("r_mult"), "thesis_age_min": r.get("thesis_age_min")})
    slices: Dict[str, Dict[str, Any]] = {}
    for tr in out:
        for key in (f"{tr['side']}|{tr['label']}", f"{tr['side']}|all", "all|all"):
            d = slices.setdefault(key, {"n": 0, "wins": 0, "net_usd": 0.0, "notional_usd": 0.0})
            d["n"] += 1
            d["wins"] += tr["net_usd"] > 0
            d["net_usd"] += tr["net_usd"]
            d["notional_usd"] += tr["notional_usd"]
    for d in slices.values():
        d["net_bps"] = round(d["net_usd"] / d["notional_usd"] * 1e4, 1) if d["notional_usd"] > 0 else None
        d["net_usd"] = round(d["net_usd"], 2)
        d["label"] = slice_label(d["n"], d["net_usd"], d["wins"])
    return {"trades": out, "slices": dict(sorted(slices.items()))}


def read_ledger(path: str) -> List[Dict[str, Any]]:
    """READ ONLY: the review ledger's rows; [] when it is missing."""
    rows: List[Dict[str, Any]] = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        pass
    return rows


# ── the repo's adoption bar, beside the live book ──────────────────────────

def beside_live(v: Variant, full: Dict[str, Dict[str, Any]], keep: bool = False) -> Dict[str, Any]:
    """One variant on each whole dataset, alone and beside the live long
    book, in the shape `short_ladder_test.judge` reads."""
    per: Dict[str, Any] = {}
    for name, ds in full.items():
        s, p, lo, hi = ds["series"], ds["params"], ds["lo"], ds["hi"]
        start = p.starting_equity
        alone = walk_any(s, v, p, start, lo, hi)
        comb = walk_any(s, v, p, start, lo, hi, live=ds["live"])
        s_comb = IN.summarize(comb["trades"], comb["curve"], ds["live"].eq[lo:hi + 1], comb["refused"])
        per[name] = {"alone": IN.summarize(alone["trades"], alone["curve"], [start] * (hi - lo + 1), alone["refused"]),
                     "combined": s_comb, "live": ds["live_sum"], "combined_dd_pct": s_comb.get("max_dd_pct"),
                     "drop_best_day": SL.drop_best_day(comb["trades"])}
        mid = ds.get("mid_ms")
        if mid is not None:
            per[name]["halves"] = [round(sum(tr["net_usd"] for tr in comb["trades"] if tr["entry_ms"] < mid), 2),
                                   round(sum(tr["net_usd"] for tr in comb["trades"] if tr["entry_ms"] >= mid), 2)]
        if keep:
            per[name]["trades_alone"], per[name]["trades_combined"] = alone["trades"], comb["trades"]
    return per


def adoption_bar(v: Variant, full: Dict[str, Dict[str, Any]], primary: str = "brent_5m") -> Dict[str, Any]:
    """`short_ladder_test.judge` on the variant: beside live on every
    dataset, drawdown no worse, both halves of the primary, the neighbours
    above zero, the best day dropped."""
    per = beside_live(v, full)
    nets = [beside_live(n, {primary: full[primary]})[primary]["combined"]["net_usd"] for n in (ladder_neighbours(v) if isinstance(v, Ladder) else neighbours(v))]
    row = {"datasets": per, "halves": per[primary]["halves"], "drop_best_day": per[primary]["drop_best_day"]}
    return {"judge": SL.judge(row, nets, primary, MIN_N, reopen_leg=False), "neighbour_nets": nets,
            "datasets": {k: {"alone": d["alone"], "combined": d["combined"], "live": d["live"],
                             "combined_dd_pct": d["combined_dd_pct"]} for k, d in per.items()},
            "halves": per[primary]["halves"]}


# ── tables for the page (pure given rows) ──────────────────────────────────

def axis_table(vs: Sequence[Any], rows: Dict[str, Dict[str, Any]], axis: str, segs: Sequence[str]
               ) -> Dict[str, Dict[str, Any]]:
    """Per level of one axis over `vs`: the mean net, the share above zero
    and the win count on each segment."""
    groups: Dict[str, List[str]] = {}
    for v in vs:
        groups.setdefault(str(getattr(v, axis)), []).append(v.name)
    out: Dict[str, Dict[str, Any]] = {}
    for level, names in groups.items():
        out[level] = {"variants": len(names)}
        for seg in segs:
            nets = [rows[n][seg]["net_usd"] for n in names]
            out[level][seg] = {"mean_usd": round(sum(nets) / len(nets), 2),
                               "above_zero": sum(1 for x in nets if x > EPS),
                               "trades": sum(rows[n][seg]["n"] for n in names),
                               "wins": sum(rows[n][seg]["wins"] for n in names)}
    return out


def leg_table(names: Sequence[str], rows: Dict[str, Dict[str, Any]], segs: Sequence[str]) -> Dict[str, Any]:
    """Every leg of the named variants pooled, per segment: trades, wins,
    net (the legs of `brief`)."""
    out: Dict[str, Any] = {}
    for seg in segs:
        acc: Dict[str, List[float]] = {}
        for n in names:
            for leg, (k, w, net) in rows[n][seg]["legs"].items():
                a = acc.setdefault(leg, [0, 0, 0.0])
                a[0] += k
                a[1] += w
                a[2] += net
        out[seg] = {leg: {"n": int(a[0]), "win_rate": round(a[1] / a[0] * 100.0, 1) if a[0] else None,
                          "exp_usd": round(a[2] / a[0], 3) if a[0] else None} for leg, a in sorted(acc.items())}
    return out


def family_table(vs: Sequence[Variant], rows: Dict[str, Dict[str, Any]], segs: Sequence[str]) -> Dict[str, Any]:
    fams: Dict[str, List[str]] = {}
    for v in vs:
        fams.setdefault(v.family, []).append(v.name)
    out: Dict[str, Any] = {}
    for fam, names in sorted(fams.items()):
        out[fam] = {"variants": len(names), "selectable": sum(1 for n in names if selectable(rows[n])),
                    "best_on_search": min(names, key=lambda n: (-rows[n][SEARCH]["net_usd"], n))}
        for seg in segs:
            nets = [rows[n][seg]["net_usd"] for n in names]
            out[fam][seg] = {"median_usd": round(statistics.median(nets), 2), "best_usd": max(nets),
                             "above_zero": sum(1 for x in nets if x > EPS)}
    return out


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    ap = argparse.ArgumentParser(
        description="Thesis-range backtest on Brent and WTI: buy the thesis entry, sell the thesis target, flip on "
                    "a stop with a resting stop-entry, under 19 condition filters. One rule is picked on the "
                    "search set (the older 75%% of theses on Brent 5m) and read on the holdout (the newest 25%% "
                    "and all of WTI), against a random-level baseline. Research only: reads candles, the journal "
                    "and the review ledger; never trades or writes config.",
        epilog="Exit 0: the chosen rule holds on the holdout, on WTI, in both search halves and over its "
               "random-level baseline. 1: it does not. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else ""),
        help="READ ONLY: the live oil journal's theses (default: $PATHIEL_STATE_DIR/strategies/oil/theses)")
    ap.add_argument("--ledger", default=os.path.join(state, "review", "ledger.jsonl") if state else "",
                    help="READ ONLY: the review's trade ledger (measured fees, real closes, the account's trades)")
    ap.add_argument("--cache-dir", default="/tmp/thesis-range/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/thesis-range")
    ap.add_argument("--extra-5m-glob", nargs="*", default=["/tmp/review/*/cache"] + (
        [os.path.join(state, "pool", "work", "*", "cache")] if state else []),
        help="READ ONLY: directories holding older <coin>__candles_5m_5000.json snapshots to merge in")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--draws", type=int, default=BASELINE_DRAWS, help="random-level draws per baseline")
    ap.add_argument("--detail", nargs="*", default=[], metavar="NAME", help="more variants whose trades to keep")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} thesis-range: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    split = holdout_split(theses)                      # fixed here, from the journal alone, before any bar is read
    n_hold = sum(1 for th in theses if th.created_at_ms >= split)
    log(f"{len(theses)} theses; HOLDOUT fixed before the search: the newest {n_hold} (from {_iso(split)}) and all "
        f"of WTI. Follow: tail -f {os.path.join(a.out_dir, 'progress.log')}")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        c5 = IN.contiguous_from(IN.load_5m(coin, a.cache_dir, a.extra_5m_glob))
        end = min(c15[-1].t + FIFTEEN_MS, c5[-1].t + FIVE_MS)
        end -= end % FIFTEEN_MS
        return c5, c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, c15[-1].t + FIFTEEN_MS, a.cache_dir)

    ledger = read_ledger(a.ledger)
    fees = IN.measured_fees(a.ledger)
    closes = [int(r["exit_ms"]) for r in {r["id"]: r for r in ledger if r.get("id")}.values()
              if r.get("coin") in (BRENT, WTI) and r.get("exit_ms")]
    bp = dataclasses.replace(base_params(a.start_equity, SL.RISK_FRAC), leverage=LEVERAGE)
    sp = dataclasses.replace(bp, taker_fee=fees["taker"], maker_fee=fees["maker"])
    dir_at, th_at = journaled_direction(theses), SL.thesis_lookup(theses)
    first = min(th.created_at_ms for th in theses)
    full: Dict[str, Dict[str, Any]] = {}
    meta: Dict[str, Any] = {}

    def add(name: str, bars, step, c15, c1h, d_at, t_at, ths, fund, end) -> None:
        start = max(first, bars[0].t + 12 * HOUR_MS, c15[0].t)
        start += -start % FIFTEEN_MS
        ds = SR.make_dataset(bars, step, 1 if step == FIVE_MS else 0, c15, c1h, d_at, t_at, bp, fund, start, end)
        eia = OC.blackout_windows([t for t, _ in OC.eia_report_times(bars[0].t, end)], EIA_HOURS, EIA_HOURS)
        series = build_series([b for b in bars if b.t < end], c1h, ths, step, fund, eia)
        assert series.t == ds["series"].t
        full[name] = {"series": series, "live": ds["live"], "live_sum": ds["live_sum"], "lo": ds["lo"],
                      "hi": ds["hi"], "params": sp, "start_ms": start, "end_ms": end}
        meta[name] = {"window": [_iso(start), _iso(end)], "days": round((end - start) / SL.DAY_MS, 1),
                      "live": ds["live_sum"]}

    b5, b15, b1h, b_end, b_fund = load(BRENT)
    b_ths = range_theses(theses, b1h, None, closes)
    add("brent_5m", b5, FIVE_MS, b15, b1h, dir_at, th_at, b_ths, b_fund, b_end)
    add("brent_15m", b15, FIFTEEN_MS, b15, b1h, dir_at, th_at, b_ths, b_fund, b_end)
    w5, w15, w1h, w_end, w_fund = load(WTI)
    ratio = SL.price_ratio(theses, b1h, w1h)
    w_ths = range_theses(theses, w1h, ratio, closes)
    w_stop = min(b_end, w_end)
    add("wti_5m", w5, FIVE_MS, w15, w1h, side_only(dir_at), SL.thesis_lookup(theses, ratio), w_ths, w_fund, w_stop)
    add("wti_15m", w15, FIFTEEN_MS, w15, w1h, side_only(dir_at), SL.thesis_lookup(theses, ratio), w_ths, w_fund,
        w_stop)
    prim = full["brent_5m"]
    prim["mid_ms"] = prim["start_ms"] + (prim["end_ms"] - prim["start_ms"]) // 2
    prim["mid_ms"] -= prim["mid_ms"] % FIFTEEN_MS

    segs: Dict[str, Dict[str, Any]] = {}
    for name in ("brent_5m", "brent_15m"):
        ds = full[name]
        cut = bisect.bisect_left(ds["series"].t, split)
        segs[f"{name}_search"] = {**ds, "hi": cut - 1}
        segs[f"{name}_holdout"] = {**ds, "lo": cut}
        segs[f"{name}_holdout"].pop("mid_ms", None)
        meta[f"{name}_search"] = {"window": [_iso(ds["start_ms"]), _iso(split)],
                                  "days": round((split - ds["start_ms"]) / SL.DAY_MS, 1)}
        meta[f"{name}_holdout"] = {"window": [_iso(split), _iso(ds["end_ms"])],
                                   "days": round((ds["end_ms"] - split) / SL.DAY_MS, 1)}
    s_mid = full["brent_5m"]["start_ms"] + (split - full["brent_5m"]["start_ms"]) // 2
    s_mid -= s_mid % FIFTEEN_MS
    segs[SEARCH]["mid_ms"] = s_mid
    segs["brent_15m_search"].pop("mid_ms", None)
    segs["wti_5m"], segs["wti_15m"] = dict(full["wti_5m"]), dict(full["wti_15m"])
    log(f"fees {fees}; datasets {meta}; search halves split at {_iso(s_mid)}; {a.workers} workers")

    # The operator's ladder: the whole matrix. The range rule: stage 1 the grid, stage 2 each filter alone,
    # stage 3 the best three combined. Every pick below reads SEARCH only.
    ladder = ladder_variants()
    rows = evaluate(ladder, segs, SEARCH_SEGS, a.workers, log, "ladder (search)")
    base = base_variants()
    rows.update(evaluate(base, segs, SEARCH_SEGS, a.workers, log, "range stage 1 (search)"))
    carry = carriers(base, rows)
    stage2 = [dataclasses.replace(v, filt=(f,)) for v in carry for f in FILTERS]
    rows.update(evaluate(stage2, segs, SEARCH_SEGS, a.workers, log, "range stage 2 (search)"))
    ranked = rank_filters(rows, [v.name for v in carry])
    top_filters = ranked[:COMBINE]
    stage3 = [dataclasses.replace(v, filt=c) for v in carry for c in combos(top_filters)]
    rows.update(evaluate(stage3, segs, SEARCH_SEGS, a.workers, log, "range stage 3 (search)"))
    vs: List[Any] = ladder + base + stage2 + stage3
    by_name = {v.name: v for v in vs}
    assert len(by_name) == len(vs)
    chosen = select(rows)
    if chosen is None:
        log("no variant has MIN_N trades on the search set")
        return 1
    best_ladder = select({v.name: rows[v.name] for v in ladder}) or OPERATOR.name
    best_range = select({v.name: rows[v.name] for v in base + stage2 + stage3}) or BASELINE.name
    log(f"{len(vs)} variants on the search set (ladder {len(ladder)}, range {len(base)} + {len(stage2)} + "
        f"{len(stage3)}); range filters ranked {top_filters}; CHOSEN on the search set alone: {chosen} "
        f"{rows[chosen][SEARCH]}; best ladder {best_ladder}; best range {best_range}. Holdout walks start now.")

    hold_rows = evaluate(vs, segs, HOLDOUT_SEGS, a.workers, log, "holdout")
    for n, r in hold_rows.items():
        rows[n].update(r)
    all_segs = SEARCH_SEGS + HOLDOUT_SEGS
    cv = by_name[chosen]
    defl = deflated(rows, chosen)
    defl_ladder = deflated({v.name: rows[v.name] for v in ladder}, best_ladder)

    # the named rows: the spec, its resting twin, the picks, the baseline long, sell-the-target
    spec_twin = dataclasses.replace(OPERATOR, fill="resting")
    sell_target = Variant(mode="S", a=1.0, stf=1.0, sstop="p0.36", flip_up="off")
    named = list(dict.fromkeys([OPERATOR.name, spec_twin.name, chosen, best_ladder, best_range, BASELINE.name,
                                sell_target.name] + [n for n in a.detail if n in by_name]))
    baselines = {}
    for n in named:
        baselines[n] = baseline(by_name[n], segs, all_segs, a.draws, a.workers)
        log(f"baseline {n}: pool {baselines[n]['pool']['rule_usd']:+.2f} vs p95 "
            f"{baselines[n]['pool']['baseline_p95_usd']:+.2f} (percentile {baselines[n]['pool']['rule_percentile']})")
    verdicts = {n: holds(rows[n], baselines[n]) for n in named}
    verdict = verdicts[chosen]
    bar = {n: adoption_bar(by_name[n], full) for n in named}
    worst = {n: {s: run_segment(by_name[n], segs[s], midbar_worst=True)["net_usd"] for s in all_segs} for n in named}
    detail = {n: {s: run_segment(by_name[n], segs[s], keep=True) for s in all_segs} for n in named}
    spec_grid = {v.name: rows[v.name] for v in (dataclasses.replace(OPERATOR, ratchet=r, fill=f, bounce=b)
                                                for r in O_RATCHETS for f in O_FILLS for b in (True, False))}
    spec_stops = {v.name: rows[v.name] for v in (dataclasses.replace(OPERATOR, stop=st, fill=f)
                                                 for st in O_STOPS for f in O_FILLS)}

    plain = [v for v in ladder if not v.filt]
    plain_names = [v.name for v in plain]
    carry_names = [v.name for v in carry]
    conditions = {"ladder": {"none": {s: pooled(rows, plain_names, s) for s in all_segs}},
                  "range": {"none": {s: pooled(rows, carry_names, s) for s in all_segs}}}
    for f in O_FILTERS:
        conditions["ladder"][f] = {s: pooled(rows, [f"{c}+{f}" for c in plain_names], s) for s in all_segs}
    for f in FILTERS:
        conditions["range"][f] = {s: pooled(rows, [f"{c}+{f}" for c in carry_names], s) for s in all_segs}
    for c in combos(top_filters):
        conditions["range"]["+".join(c)] = {
            s: pooled(rows, [n + "".join("+" + f for f in c) for n in carry_names], s) for s in all_segs}
    axes = {"ladder": {ax: axis_table(plain, rows, ax, all_segs)
                       for ax in ("entry", "ssize", "stop", "fsize", "ratchet", "fill", "bounce")},
            "S": {ax: axis_table([v for v in base if v.mode == "S"], rows, ax, all_segs)
                  for ax in ("a", "stf", "sstop", "flip_up")},
            "L": {ax: axis_table([v for v in base if v.mode == "L"], rows, ax, all_segs)
                  for ax in ("b", "ltf", "lstop", "flip_dn")}}
    legs = {"ladder": leg_table(plain_names, rows, all_segs),
            "ladder_resting": leg_table([v.name for v in plain if v.fill == "resting"], rows, all_segs),
            "ladder_tick": leg_table([v.name for v in plain if v.fill == "tick"], rows, all_segs),
            "range": leg_table([v.name for v in base], rows, all_segs)}
    hand = score_hand_trades(ledger, b_ths)

    csv_path = os.path.join(a.out_dir, "variants.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variant", "family", "selectable", "chosen", "search_half1", "search_half2"]
                   + [f"{s}_{k}" for s in all_segs for k in ("n", "win_pct", "net", "dd_pct", "avg_r")])
        for n in sorted(rows):
            r = rows[n]
            w.writerow([n, by_name[n].family, int(selectable(r)), int(n == chosen), *r[SEARCH].get("halves", ("", ""))]
                       + [r[s][k] for s in all_segs for k in ("n", "win_rate", "net_usd", "max_dd_pct", "avg_r")])
    rep = {"generated": _iso(int(time.time() * 1000)), "fees": fees, "meta": meta,
           "split": {"holdout_from": _iso(split), "holdout_theses": n_hold, "theses": len(theses),
                     "search_halves_split": _iso(s_mid), "rule": f"newest {HOLDOUT_FRAC:.0%} of theses + all WTI"},
           "variants": {"total": len(vs), "ladder": len(ladder), "ladder_unfiltered": len(plain),
                        "range_stage1": len(base), "range_stage2": len(stage2), "range_stage3": len(stage3),
                        "range_carriers": len(carry), "range_filters_ranked": ranked, "top_filters": top_filters},
           "chosen": {"name": chosen, "variant": dataclasses.asdict(cv), "segments": rows[chosen], "holds": verdict},
           "operator_spec": {"name": OPERATOR.name, "variant": dataclasses.asdict(OPERATOR),
                             "segments": rows[OPERATOR.name], "holds": verdicts[OPERATOR.name],
                             "ratchet_fill_bounce": spec_grid, "stop_fill": spec_stops},
           "best_ladder": best_ladder, "best_range": best_range, "holds": verdicts, "midbar_worst_usd": worst,
           "deflated": defl, "deflated_ladder": defl_ladder, "baselines": baselines, "adoption_bar": bar,
           "named": {n: rows[n] for n in named}, "families": family_table(vs, rows, all_segs),
           "conditions": conditions, "axes": axes, "legs": legs, "hand": hand, "detail": detail, "csv": csv_path}
    rep_path = os.path.join(a.out_dir, "report.json")
    with open(rep_path, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    _print(rep)
    log(f"done in {time.time() - t0:.0f}s: chosen {chosen} holds={verdict['holds']}; the spec {OPERATOR.name} "
        f"holds={verdicts[OPERATOR.name]['holds']}; report {rep_path}; csv {csv_path}")
    return 0 if verdict["holds"] else 1


def _seg(d: Dict[str, Any]) -> str:
    return (f"n={d['n']} win={d['win_rate']}% net={d['net_usd']:+.2f} DD={d['max_dd_pct']}% R={d['avg_r']}"
            + (f" halves={d['halves']}" if "halves" in d else "") + f" legs={d['legs']}")


def _print(rep: Dict[str, Any]) -> None:
    print(f"\nsplit {rep['split']}\nvariants {rep['variants']}")
    print(f"CHOSEN {rep['chosen']['name']} | best ladder {rep['best_ladder']} | best range {rep['best_range']} | "
          f"spec {rep['operator_spec']['name']}")
    for n, segs in rep["named"].items():
        hv, b, j = rep["holds"][n], rep["baselines"][n], rep["adoption_bar"][n]["judge"]
        print(f"\n== {n}: holds={hv['holds']} {hv['legs']}")
        for s, d in segs.items():
            c = b["segments"][s]
            print(f"   {s}: {_seg(d)} | flip-bar worst {rep['midbar_worst_usd'][n][s]:+.2f} | random levels: median "
                  f"{c['baseline_median_usd']:+.2f} p95 {c['baseline_p95_usd']:+.2f} n {c['baseline_median_n']} "
                  f"-> percentile {c['rule_percentile']}")
        c = b["pool"]
        print(f"   POOL {'+'.join(c['segments'])}: rule {c['rule_usd']:+.2f} (n {c['rule_n']}) vs random median "
              f"{c['baseline_median_usd']:+.2f} p95 {c['baseline_p95_usd']:+.2f}: percentile {c['rule_percentile']} "
              f"beats p95 {c['beats_p95']}")
        print(f"   adoption bar: {j['verdict']} {j['legs']} halves {rep['adoption_bar'][n]['halves']}")
        for ds, d in rep["adoption_bar"][n]["datasets"].items():
            print(f"      {ds}: alone n={d['alone']['n']} net={d['alone']['net_usd']:+.2f} "
                  f"DD={d['alone'].get('max_dd_pct')}% | beside live n={d['combined']['n']} "
                  f"net={d['combined']['net_usd']:+.2f} DD={d['combined_dd_pct']}% vs {d['live']['max_dd_pct']}%")
    for key in ("ratchet_fill_bounce", "stop_fill"):
        print(f"\nthe spec, {key}:")
        for n, segs in rep["operator_spec"][key].items():
            print(f"   {n}: " + " | ".join(f"{s}: {d['n']}, {d['win_rate']}%, {d['net_usd']:+.2f}, DD {d['max_dd_pct']}%"
                                           for s, d in segs.items()))
    for key in ("deflated", "deflated_ladder"):
        d = rep[key]
        print(f"\n{key}: {d['variants']} variants, {d['with_min_n_on_search']} with {MIN_N}+ trades on search, "
              f"{d['selectable']} selectable; search {d['search']}")
        for s, x in d["holdout"].items():
            print(f"   {s}: {x}")
    print("\nfamilies (median / best / above zero per segment):")
    for fam, f in rep["families"].items():
        print(f" {fam}: {f['variants']} variants, selectable {f['selectable']}, best on search {f['best_on_search']}")
        print("    " + " | ".join(f"{s}: {f[s]['median_usd']:+.2f} / {f[s]['best_usd']:+.2f} / {f[s]['above_zero']}"
                                  for s in f if s.startswith(("brent", "wti"))))
    for fam, tab in rep["conditions"].items():
        print(f"\nconditions, {fam} (pooled: n, win%, $ per trade, variants above zero):")
        for f, c in tab.items():
            print(f" {f}: " + " | ".join(f"{s}: {d['n']}, {d['win_rate']}%, {d['exp_usd']}, "
                                          f"{d['above_zero']}/{d['variants']}" for s, d in c.items()))
    for fam, tab in rep["legs"].items():
        print(f"\nlegs, {fam} (n, win%, $ per trade):")
        for s, legs in tab.items():
            print(f"   {s}: " + " | ".join(f"{leg}: {d['n']}, {d['win_rate']}%, {d['exp_usd']}" for leg, d in legs.items()))
    for mode, tabs in rep["axes"].items():
        for ax, tab in tabs.items():
            print(f"\naxis {mode}.{ax}:")
            for level, x in tab.items():
                print(f"   {level}: " + " | ".join(
                    f"{s}: mean {d['mean_usd']:+.2f}, {d['above_zero']}/{x['variants']} up, {d['wins']}/{d['trades']} wins"
                    for s, d in x.items() if s != "variants"))
    print("\nthe account's own Brent trades against the thesis range:")
    for tr in rep["hand"]["trades"]:
        print(f"   {tr}")
    for k, d in rep["hand"]["slices"].items():
        print(f"   {k}: {d}")


if __name__ == "__main__":
    raise SystemExit(main())
