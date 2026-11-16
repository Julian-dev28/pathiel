"""Intraday 5-minute test, 2026-11-16. Research only.

Operator, 2026-11-15: "anyway you can test an intraday theory? i want 5 min
market wins too". The desk takes a few multi-hour dip-buy longs from a
thesis. This asks whether short-horizon trades on 5-minute candles (minutes
to two hours) add money beside that book. One deterministic script, no LLM
call, no order, no config write.

Five families, each on a small declared grid (208 variants in all):

1. Opening-range breakout ("orb"). Sessions: the Sunday reopen (the first
   bar after the oil weekend), London 07:00Z, New York 13:00Z, the EIA
   report and the API report (`oil_calendar`, holiday shifts included), and
   all five together. Range = the first 15 or 30 minutes; enter on the
   first 5m CLOSE outside it within 90 minutes; stop at the other side or
   the middle; target 1.5 / 2 / 3 R; flat two hours after the signal.
2. Session-VWAP reversion ("vwap"). When a 5m close stretches k x ATR14(5m)
   from the session VWAP (session = the CME day, 18:00 New York) and the
   1h trend is flat, fade it back to the VWAP. k 2 / 3 / 4, stop 1 or 2 ATR
   beyond the entry, flat after 60 or 120 minutes.
3. Trend pullback ("pb"). With the 1h or 4h trend up, after a new session
   high, buy the first 5m bar that dips to the 20-EMA and closes above it
   (mirrored for shorts). Stop 0.5 or 1.5 ATR under the pullback low,
   target 1.5 or 3 R, flat after two hours.
4. Big-bar momentum ("bb"). A 5m bar whose range is over m x ATR14: trade
   with it (continuation) or against it (fade). m 2 / 3 / 4, stop half the
   bar's range away, target 1.5 or 3 R, flat after 30 or 120 minutes.
5. Thesis-aligned ("th_"): every family 1 to 3 variant, keeping only the
   signals on the side of the journal thesis in force.

Costs, five declared scenarios (not a search axis; the verdict is read on
"watcher" only):

  free     next 5m open, no fee, no slippage: is there any edge at all
  bot      next 5m open, the account's measured xyz fee, book-curve
           slippage: the best the venue allows; needs an order bot the desk
           does not have
  watcher  PRIMARY. A watcher rule fires on the 5m close and the desk tick
           (one or two minutes) places the order: fill at the open of the
           5m bar one bar later, measured fee, slippage = half that bar's
           high-low range, never under the sim's 3 bps (the realistic-fill
           model of SHORT_LADDER_TEST_2026-11-16)
  tick15   no watcher rule: the order waits for the next 15-minute cron
           tick (:03/:18/:33/:48, filled at :05/:20/:35/:50), same costs
  repo_fee watcher with the sim's fee constants (4.5 / 1.5 bps)

The measured fee is read from the daily review's trade ledger
(`$PATHIEL_STATE_DIR/review/ledger.jsonl`, the account's own xyz fills):
fees over traded notional. Stops and targets rest on the venue (a stop is
a taker fill with the sim's stop slippage, a target a maker limit that
needs a trade-through). Funding is paid on the hour.

Sizing: `desk_tick_sim.risk_capped_notional` at 6% of equity at the stop
(the desk's risk) and at 1.2% (20% size), `next_trade.fit_leverage` 20/15.
One position at a time. Every variant runs alone and beside the live long
book (`short_ladder_test.prepare`: no sleeve trade while the live long is
open, an open one closed when the live long enters).

Datasets: Brent 5m (primary) and WTI 5m, each as far back as 5m candles
exist: the venue serves only its last 5,000 per interval, so older cached
snapshots (`--extra-5m-glob`) are merged in. Walk-forward: both halves of
the primary. The bar is `short_ladder_test.judge` (beat live on every
dataset, drawdown no worse, both halves, every grid neighbour, without the
best day) with at least 30 trades; under 30 is LEANING or NOISE, never
PASS.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.intraday_5m_test \\
        --cache-dir <dir> --out-dir <dir>

Exit 0 when a variant passes the bar, 1 when none does, 2 with no journal.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import glob
import json
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest import oil_calendar as OC
from services.strategies.backtest.desk_tick_sim import Params, _market_exit_slip, exit_slip_bps, risk_capped_notional
from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.stop_cap_test import _iso, closed_only
from services.strategies.evals.trend_gate_test import base_params, side_only
from services.strategies.weekend import NEW_YORK, weekend_closed

START = SL.START
RISK_FULL = SL.RISK_FRAC            # 6% of equity at the stop, the desk's risk
RISK_SMALL = 0.012                  # 1.2%: "20% size"
RISKS = (("full", RISK_FULL), ("small", RISK_SMALL))
BRENT, WTI = SL.BRENT, SL.WTI
FIVE_MS, FIFTEEN_MS, HOUR_MS, DAY_MS = 300_000, 900_000, 3_600_000, 86_400_000
MIN_N = 30                          # under this many trades an intraday variant is LEANING or NOISE
PRIMARY = "brent_5m"
EPS = 1e-9
ATR_N, EMA_N = 14, 20
SESSION_WARMUP = 12                 # 5m bars into the session before its VWAP / highs are read
TREND_BAND = 0.5                    # |1h close - EMA| under this many 1h ATR = flat
MIN_STOP_FRAC = 0.0005              # a stop closer than 5 bps to the fill is refused
ORB_WINDOW_MIN, ORB_HOLD_MIN, PB_HOLD_MIN = 90, 120, 120
CRON_OFFSET_MS = 3 * 60_000         # the desk cron runs at :03/:18/:33/:48
FALLBACK_TAKER, FALLBACK_MAKER = 0.00009, 0.00003   # 0.9 / 0.3 bps, PROFIT_AUDIT section 8

ORB_SESSIONS = ("reopen", "london", "ny", "eia", "api", "all")
ORB_RANGE = (15, 30)
ORB_STOP = ("opp", "mid")
ORB_R = (1.5, 2.0, 3.0)
VWAP_K = (2.0, 3.0, 4.0)
VWAP_STOP = (1.0, 2.0)
VWAP_HOLD = (60, 120)
PB_TREND = ("1h", "4h")
PB_STOP = (0.5, 1.5)
PB_R = (1.5, 3.0)
BB_M = (2.0, 3.0, 4.0)
BB_MODE = ("cont", "fade")
BB_R = (1.5, 3.0)
BB_HOLD = (30, 120)
FAMILIES = {1: "1. opening-range breakout", 2: "2. session-VWAP reversion", 3: "3. trend pullback to the 20-EMA",
            4: "4. big 5m bar, continuation or fade", 5: "5. thesis-aligned (families 1-3, thesis side only)"}


# ── cost scenarios ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Scenario:
    name: str
    delay: str          # next | watcher | tick15
    taker: float
    maker: float
    slip: str           # zero | curve | range


def scenarios(taker: float, maker: float, repo: Params) -> Dict[str, Scenario]:
    return {"free": Scenario("free", "next", 0.0, 0.0, "zero"),
            "bot": Scenario("bot", "next", taker, maker, "curve"),
            "watcher": Scenario("watcher", "watcher", taker, maker, "range"),
            "tick15": Scenario("tick15", "tick15", taker, maker, "range"),
            "repo_fee": Scenario("repo_fee", "watcher", repo.taker_fee, repo.maker_fee, "range")}


PRIMARY_SCENARIO = "watcher"


def measured_fees(ledger_path: str, coins: Sequence[str] = (BRENT, WTI), min_notional: float = 100.0
                  ) -> Dict[str, Any]:
    """The account's own fee per dollar traded on xyz oil, from the review
    ledger (one row per closed round trip, de-duplicated by id). A trip
    closed by a stop or by hand is taker both ways: the taker rate is the
    median of fees / traded notional over those. A target that filled as a
    resting limit pays taker in and maker out, so its rate sits under the
    taker rate and the maker rate is 2 x that median - taker. Falls back to
    0.9 / 0.3 bps (PROFIT_AUDIT section 8) when the ledger is missing or
    has no such trip. READ ONLY. Pure given the file."""
    rows: Dict[str, Dict[str, Any]] = {}
    try:
        with open(ledger_path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("id"):
                    rows[r["id"]] = r
    except OSError:
        rows = {}
    taker_rates, target_rates = [], []
    for r in rows.values():
        try:
            traded = float(r["notional_usd"]) + float(r.get("exit_px") or 0.0) * float(r["max_size"])
            fees = float(r["fees"])
        except (KeyError, TypeError, ValueError):
            continue
        if r.get("coin") not in coins or float(r["notional_usd"]) < min_notional or traded <= 0:
            continue
        (target_rates if r.get("exit_kind") == "target" else taker_rates).append(fees / traded)
    if not taker_rates:
        return {"taker": FALLBACK_TAKER, "maker": FALLBACK_MAKER, "source": "fallback", "n_taker": 0, "n_maker": 0}
    taker = statistics.median(taker_rates)
    resting = [x for x in target_rates if x < 0.85 * taker]
    maker = max(0.0, 2.0 * statistics.median(resting) - taker) if resting else FALLBACK_MAKER
    return {"taker": taker, "maker": maker, "source": "ledger", "n_taker": len(taker_rates), "n_maker": len(resting),
            "n_target_paid_taker": len(target_rates) - len(resting)}


# ── the session clock (pure) ───────────────────────────────────────────────

def session_key(t_ms: int) -> str:
    """The CME trading day a moment belongs to: it starts at 18:00 New York
    (23:00Z in winter, 22:00Z in summer), so the key is the New York date
    six hours on."""
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK) + timedelta(hours=6)
    return lt.strftime("%Y-%m-%d")


def reopen_bar(t_ms: int) -> bool:
    """The first 5m bar after the oil weekend."""
    return SL.reopen_tick(t_ms, FIVE_MS)


def session_anchors(times: Sequence[int]) -> Dict[str, List[int]]:
    """Session name -> the start of each opening range inside `times` (5m
    bar opens). London 07:00Z and New York 13:00Z on any day the oil
    weekend is not in force; the Sunday reopen; the EIA and API reports
    from `oil_calendar`."""
    if not times:
        return {s: [] for s in ORB_SESSIONS[:-1]}
    have = set(times)
    lo, hi = times[0], times[-1] + FIVE_MS
    out: Dict[str, List[int]] = {"reopen": [t for t in times if reopen_bar(t)], "london": [], "ny": []}
    day = lo - lo % DAY_MS
    while day < hi:
        for name, hour in (("london", 7), ("ny", 13)):
            t = day + hour * HOUR_MS
            if t in have and not weekend_closed(t):
                out[name].append(t)
        day += DAY_MS
    out["eia"] = [t for t, _ in OC.eia_report_times(lo, hi) if t in have]
    out["api"] = [t for t in OC.api_report_times(lo, hi) if t in have]
    return out


def fill_time(signal_close_ms: int, delay: str) -> int:
    """When an order for a signal known at `signal_close_ms` (a 5m close)
    fills. "next": that moment (a bot). "watcher": one 5m bar later (the
    watcher fires, the tick takes a minute or two). "tick15": the first
    cron tick at or after the signal (:03/:18/:33/:48), filled two minutes
    on, i.e. at :05/:20/:35/:50."""
    if delay == "next":
        return signal_close_ms
    if delay == "watcher":
        return signal_close_ms + FIVE_MS
    if delay == "tick15":
        k = -((signal_close_ms - CRON_OFFSET_MS) // -FIFTEEN_MS)      # ceil
        return k * FIFTEEN_MS + FIVE_MS
    raise ValueError(f"unknown delay {delay!r}")


# ── per-bar context ────────────────────────────────────────────────────────

@dataclass
class Ctx:
    t: List[int]
    o: List[float]
    h: List[float]
    l: List[float]
    c: List[float]
    atr: List[Optional[float]]          # ATR14 of the 5m bars closed through i
    ema: List[float]                    # EMA20 of the 5m closes through i
    vwap: List[float]                   # session VWAP through i
    sess_n: List[int]                   # bars into the session, this one included
    sess_hi_prev: List[Optional[float]]  # the session high BEFORE bar i
    sess_lo_prev: List[Optional[float]]
    trend1h: List[Optional[int]]        # +1 up, -1 down, 0 flat, None unknown; closed 1h bars only
    trend4h: List[Optional[int]]
    thesis: List[int]                   # the journal thesis in force at the bar's close: +1 long, -1 short, 0 none
    weekend: List[bool]
    idx: Dict[int, int]
    fund: Dict[int, float]


def _ema(values: Sequence[float], n: int) -> List[float]:
    out, k, e = [], 2.0 / (n + 1.0), None
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def _atr(bars: Sequence[Candle], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    trs: List[float] = []
    for i, b in enumerate(bars):
        pc = bars[i - 1].c if i else b.o
        trs.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
        out.append(sum(trs[-n:]) / n if len(trs) >= n else None)
    return out


def trend_state(close: float, ema: float, atr: Optional[float], band: float = TREND_BAND) -> Optional[int]:
    """+1 / -1 when the close sits more than `band` ATR above / below its
    EMA, 0 inside the band (flat), None without an ATR."""
    if not atr or atr <= 0:
        return None
    d = (close - ema) / atr
    return 1 if d > band else -1 if d < -band else 0


def build_ctx(c5: Sequence[Candle], c1h: Sequence[Candle], thesis_side_at: Optional[Callable[[int], Optional[str]]],
              funding: Optional[Sequence[Dict[str, Any]]] = None) -> Ctx:
    """Everything a signal reads, from closed data only: index i is known
    at bar i's close."""
    bars = sorted(c5, key=lambda b: b.t)
    hours = sorted(c1h, key=lambda b: b.t)
    hc = [b.c for b in hours]
    e20, e80, a1h = _ema(hc, 20), _ema(hc, 80), _atr(hours, ATR_N)
    hclose = [b.t + HOUR_MS for b in hours]
    atr = _atr(bars, ATR_N)
    ema = _ema([b.c for b in bars], EMA_N)
    vwap, sess_n, hi_prev, lo_prev, t1, t4, th, wk = [], [], [], [], [], [], [], []
    key, pv, vol, n, tp_sum = None, 0.0, 0.0, 0, 0.0
    shi: Optional[float] = None
    slo: Optional[float] = None
    for b in bars:
        k = session_key(b.t)
        if k != key:
            key, pv, vol, n, tp_sum, shi, slo = k, 0.0, 0.0, 0, 0.0, None, None
        hi_prev.append(shi)
        lo_prev.append(slo)
        tp = (b.h + b.l + b.c) / 3.0
        n += 1
        pv += tp * b.v
        vol += b.v
        tp_sum += tp
        vwap.append(pv / vol if vol > 0 else tp_sum / n)
        sess_n.append(n)
        shi = b.h if shi is None else max(shi, b.h)
        slo = b.l if slo is None else min(slo, b.l)
        close_t = b.t + FIVE_MS
        j = bisect.bisect_right(hclose, close_t) - 1
        t1.append(trend_state(hc[j], e20[j], a1h[j]) if j >= 20 else None)
        t4.append(trend_state(hc[j], e80[j], a1h[j]) if j >= 80 else None)
        side = thesis_side_at(close_t) if thesis_side_at else None
        th.append(1 if side == "long" else -1 if side == "short" else 0)
        wk.append(weekend_closed(b.t))
    fund = {int(r["time"]) - int(r["time"]) % HOUR_MS: float(r["fundingRate"]) for r in (funding or [])
            if r.get("time") is not None and r.get("fundingRate") is not None}
    return Ctx(
        t=[b.t for b in bars], o=[b.o for b in bars], h=[b.h for b in bars], l=[b.l for b in bars],
        c=[b.c for b in bars], atr=atr, ema=ema, vwap=vwap, sess_n=sess_n, sess_hi_prev=hi_prev,
        sess_lo_prev=lo_prev, trend1h=t1, trend4h=t4, thesis=th, weekend=wk,
        idx={b.t: i for i, b in enumerate(bars)}, fund=fund)


# ── signals (pure) ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Signal:
    i: int                              # the signal bar; known at its close
    side: int                           # +1 long, -1 short
    stop: float
    expire_ms: int                      # the time stop
    target_r: Optional[float] = None    # target = fill + side x R x (fill - stop) ...
    target_px: Optional[float] = None   # ... or this price
    tag: str = ""


def orb_signals(ctx: Ctx, anchors: Sequence[Tuple[int, str]], range_min: int, stop_rule: str, target_r: float,
                window_min: int = ORB_WINDOW_MIN, hold_min: int = ORB_HOLD_MIN) -> List[Signal]:
    """One signal per anchor at most: the first 5m bar that CLOSES outside
    the range of the first `range_min` minutes, within `window_min` of the
    range's end. A range with a missing bar is skipped."""
    out: List[Signal] = []
    nb = range_min // 5
    for t0, name in sorted(anchors):
        i0 = ctx.idx.get(t0)
        if i0 is None or i0 + nb > len(ctx.t) or ctx.t[i0 + nb - 1] != t0 + (nb - 1) * FIVE_MS:
            continue
        hi, lo = max(ctx.h[i0:i0 + nb]), min(ctx.l[i0:i0 + nb])
        end = t0 + range_min * 60_000 + window_min * 60_000
        i = i0 + nb
        while i < len(ctx.t) and ctx.t[i] < end:
            side = 1 if ctx.c[i] > hi else -1 if ctx.c[i] < lo else 0
            if side:
                stop = (hi + lo) / 2.0 if stop_rule == "mid" else (lo if side > 0 else hi)
                out.append(Signal(i, side, stop, ctx.t[i] + FIVE_MS + hold_min * 60_000, target_r=target_r, tag=name))
                break
            i += 1
    return sorted(out, key=lambda s: s.i)


def vwap_signals(ctx: Ctx, k: float, stop_atr: float, hold_min: int) -> List[Signal]:
    """Fade a fresh stretch: this close is k x ATR or more from the session
    VWAP, the bar before was not, the session is an hour old and the 1h
    trend is flat. Target the VWAP, stop `stop_atr` ATR beyond the close."""
    out: List[Signal] = []
    for i in range(1, len(ctx.t)):
        a, ap = ctx.atr[i], ctx.atr[i - 1]
        if not a or not ap or ctx.sess_n[i] < SESSION_WARMUP or ctx.trend1h[i] != 0:
            continue
        dev = ctx.c[i] - ctx.vwap[i]
        if abs(dev) < k * a or (ctx.sess_n[i] > 1 and abs(ctx.c[i - 1] - ctx.vwap[i - 1]) >= k * ap):
            continue
        side = -1 if dev > 0 else 1
        out.append(Signal(i, side, ctx.c[i] - side * stop_atr * a, ctx.t[i] + FIVE_MS + hold_min * 60_000,
                          target_px=ctx.vwap[i], tag="vwap"))
    return out


def pullback_signals(ctx: Ctx, trend: str, stop_atr: float, target_r: float, hold_min: int = PB_HOLD_MIN
                     ) -> List[Signal]:
    """Trend up (closed 1h bars): a new session high arms the long; the
    first later bar whose low reaches the 20-EMA and whose close holds
    above it buys. The arm drops when the trend does or the session ends.
    Mirrored for a down trend. Stop `stop_atr` ATR beyond the pullback
    bar's extreme."""
    tr = ctx.trend1h if trend == "1h" else ctx.trend4h
    out: List[Signal] = []
    armed = 0
    for i in range(len(ctx.t)):
        if ctx.sess_n[i] == 1:
            armed = 0
        a, d = ctx.atr[i], tr[i]
        if not a or not d or ctx.sess_n[i] < SESSION_WARMUP:
            if not d:
                armed = 0
            continue
        if armed and armed != d:
            armed = 0
        if d > 0 and ctx.sess_hi_prev[i] is not None and ctx.h[i] > ctx.sess_hi_prev[i]:
            armed = 1
        elif d < 0 and ctx.sess_lo_prev[i] is not None and ctx.l[i] < ctx.sess_lo_prev[i]:
            armed = -1
        elif armed > 0 and ctx.l[i] <= ctx.ema[i] < ctx.c[i]:
            out.append(Signal(i, 1, ctx.l[i] - stop_atr * a, ctx.t[i] + FIVE_MS + hold_min * 60_000,
                              target_r=target_r, tag="pb"))
            armed = 0
        elif armed < 0 and ctx.h[i] >= ctx.ema[i] > ctx.c[i]:
            out.append(Signal(i, -1, ctx.h[i] + stop_atr * a, ctx.t[i] + FIVE_MS + hold_min * 60_000,
                              target_r=target_r, tag="pb"))
            armed = 0
    return out


def bigbar_signals(ctx: Ctx, m: float, mode: str, target_r: float, hold_min: int) -> List[Signal]:
    """A 5m bar whose range is over m x the ATR14 before it. "cont" trades
    with its body, "fade" against it; the stop is half the bar's range from
    its close."""
    out: List[Signal] = []
    for i in range(1, len(ctx.t)):
        ap = ctx.atr[i - 1]
        rng = ctx.h[i] - ctx.l[i]
        if not ap or rng <= m * ap or ctx.c[i] == ctx.o[i]:
            continue
        d = 1 if ctx.c[i] > ctx.o[i] else -1
        side = d if mode == "cont" else -d
        out.append(Signal(i, side, ctx.c[i] - side * rng / 2.0, ctx.t[i] + FIVE_MS + hold_min * 60_000,
                          target_r=target_r, tag=mode))
    return out


def thesis_only(ctx: Ctx, signals: Sequence[Signal]) -> List[Signal]:
    """Only the signals on the side of the thesis in force at their close."""
    return [s for s in signals if ctx.thesis[s.i] == s.side]


# ── variants ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    kind: str                           # orb | vwap | pb | bb
    args: Tuple[Any, ...]
    thesis: bool = False

    @property
    def family(self) -> int:
        return 5 if self.thesis else {"orb": 1, "vwap": 2, "pb": 3, "bb": 4}[self.kind]

    @property
    def name(self) -> str:
        a = self.args
        base = {"orb": lambda: f"orb_{a[0]}_r{a[1]}_{a[2]}_t{a[3]:g}",
                "vwap": lambda: f"vwap_k{a[0]:g}_s{a[1]:g}_h{a[2]}",
                "pb": lambda: f"pb_{a[0]}_s{a[1]:g}_t{a[2]:g}",
                "bb": lambda: f"bb_m{a[0]:g}_{a[1]}_t{a[2]:g}_h{a[3]}"}[self.kind]()
        return ("th_" if self.thesis else "") + base


# the ordered axes of each kind: (position in args, its levels). Categorical axes have no neighbour.
AXES: Dict[str, Tuple[Tuple[int, Tuple[Any, ...]], ...]] = {
    "orb": ((1, ORB_RANGE), (3, ORB_R)), "vwap": ((0, VWAP_K), (1, VWAP_STOP), (2, VWAP_HOLD)),
    "pb": ((1, PB_STOP), (2, PB_R)), "bb": ((0, BB_M), (2, BB_R), (3, BB_HOLD))}


def variants() -> List[Variant]:
    base = ([Variant("orb", (s, r, st, t)) for s in ORB_SESSIONS for r in ORB_RANGE for st in ORB_STOP for t in ORB_R]
            + [Variant("vwap", (k, s, h)) for k in VWAP_K for s in VWAP_STOP for h in VWAP_HOLD]
            + [Variant("pb", (tr, s, t)) for tr in PB_TREND for s in PB_STOP for t in PB_R])
    bb = [Variant("bb", (m, md, t, h)) for m in BB_M for md in BB_MODE for t in BB_R for h in BB_HOLD]
    return base + bb + [dataclasses.replace(v, thesis=True) for v in base]


def neighbours(v: Variant) -> List[Variant]:
    """One step along each ordered axis, everything else held."""
    out: List[Variant] = []
    for pos, levels in AXES[v.kind]:
        i = levels.index(v.args[pos])
        for j in (i - 1, i + 1):
            if 0 <= j < len(levels):
                out.append(dataclasses.replace(v, args=v.args[:pos] + (levels[j],) + v.args[pos + 1:]))
    return out


def signals_for(v: Variant, ctx: Ctx, anchors: Dict[str, List[int]]) -> List[Signal]:
    a = v.args
    if v.kind == "orb":
        names = ORB_SESSIONS[:-1] if a[0] == "all" else (a[0],)
        sig = orb_signals(ctx, [(t, n) for n in names for t in anchors.get(n, [])], a[1], a[2], a[3])
    elif v.kind == "vwap":
        sig = vwap_signals(ctx, a[0], a[1], a[2])
    elif v.kind == "pb":
        sig = pullback_signals(ctx, a[0], a[1], a[2])
    elif v.kind == "bb":
        sig = bigbar_signals(ctx, a[0], a[1], a[2], a[3])
    else:
        raise ValueError(v.kind)
    return thesis_only(ctx, sig) if v.thesis else sig


# ── the walker ─────────────────────────────────────────────────────────────

@dataclass
class Live:
    eq: List[float]                     # the live long book's marked equity per 5m bar
    long_open: List[bool]
    long_entry: List[bool]


def _slip(sc: Scenario, p: Params, notional: float, ctx: Ctx, x: int, what: str) -> float:
    """Fractional slippage of one market fill on bar x. "range" (the
    realistic-fill model): a market order placed by the desk pays half the
    bar's high-low range, never under the sim's own slippage; a resting
    stop pays the sim's slippage. "curve": the book-impact curve at this
    size. "zero": none."""
    if sc.slip == "zero":
        return 0.0
    if sc.slip == "curve":
        return exit_slip_bps(notional) / 1e4
    base = _market_exit_slip(p, notional)
    if what == "stop":
        return base
    return max(base, (ctx.h[x] - ctx.l[x]) / ctx.o[x] / 2.0) if ctx.o[x] > 0 else base


def walk5(ctx: Ctx, signals: Sequence[Signal], params: Params, risk_frac: float, sc: Scenario,
          start_equity: float, lo_ms: Optional[int] = None, hi_ms: Optional[int] = None,
          live: Optional[Live] = None) -> Dict[str, Any]:
    """One variant's signals over `ctx`, one position at a time.

    A signal from bar i is filled at the OPEN of the bar at
    `fill_time(close of i, sc.delay)`; skipped when that bar is missing, in
    the oil weekend, at or past the time stop, while a sleeve trade is
    still open, or (beside the live book) while the live long is open.
    From the fill bar on, per bar: funding on the hour; closes at the open
    (live long enters, weekend, time stop; market, taker); the stop (a bar
    that opens beyond it fills at its open; on the fill bar too); the
    target (resting limit, maker, a trade-through by one tick, never on the
    fill bar). A stop and a target on one bar is a loss. A trade open at
    the end of the window is marked at the last close, no exit fee.

    Returns {"trades", "refused"}."""
    n = len(ctx.t)
    lo = ctx.t[0] if lo_ms is None and n else (lo_ms or 0)
    last = (bisect.bisect_left(ctx.t, hi_ms) - 1) if hi_ms is not None else n - 1
    p = dataclasses.replace(params, taker_fee=sc.taker, maker_fee=sc.maker, risk_cap_frac=risk_frac,
                            slippage=0.0 if sc.slip == "zero" else params.slippage)
    cands = p.entry_fit_leverage or ()
    pnl, busy = 0.0, -1
    trades: List[Dict[str, Any]] = []
    refused: Counter = Counter()
    for s in signals:
        if ctx.t[s.i] < lo:
            continue
        j = ctx.idx.get(fill_time(ctx.t[s.i] + FIVE_MS, sc.delay))
        if j is None or j > last:
            refused["no_bar"] += 1
            continue
        if j <= busy:
            refused["busy"] += 1
            continue
        if ctx.weekend[j]:
            refused["weekend"] += 1
            continue
        if ctx.t[j] >= s.expire_ms:
            refused["late"] += 1
            continue
        if live is not None and live.long_open[j]:
            refused["live_long"] += 1
            continue
        eq = (live.eq[j] if live is not None else start_equity) + pnl
        ref, side = ctx.o[j], s.side
        if eq <= 0 or ref <= 0:
            refused["no_equity"] += 1
            continue
        slip = _slip(sc, p, risk_capped_notional(eq, p, p.leverage, ref, s.stop), ctx, j, "entry")
        fill = ref * (1.0 + side * slip)
        dist = side * (fill - s.stop)
        if dist / fill < MIN_STOP_FRAC:
            refused["past_stop"] += 1
            continue
        target = s.target_px if s.target_px is not None else fill + side * (s.target_r or 0.0) * dist
        if side * (target - fill) <= p.tick_size:
            refused["no_room"] += 1
            continue
        lev, gate = NT.fit_leverage(fill, s.stop, p.leverage, p.max_leverage, cands)
        if not gate.get("ok"):
            refused["leverage"] += 1
            continue
        notional = risk_capped_notional(eq, p, lev, fill, s.stop)
        size = notional / fill
        fees, funding = p.taker_fee * notional, 0.0
        exit_ref = exit_px = None
        reason, x_exit = "open", last
        for x in range(j, last + 1):
            if x > j:
                if ctx.t[x] % HOUR_MS == 0:
                    funding += side * ctx.fund.get(ctx.t[x], 0.0) * size * ctx.o[x]
                why = ("long_entry" if live is not None and live.long_entry[x] else "weekend" if ctx.weekend[x]
                       else "time" if ctx.t[x] >= s.expire_ms else None)
                if why:
                    exit_ref = ctx.o[x]
                    exit_px = exit_ref * (1.0 - side * _slip(sc, p, size * exit_ref, ctx, x, "entry"))
                    fees += p.taker_fee * exit_px * size
                    reason, x_exit = why, x
                    break
            ext = ctx.l[x] if side > 0 else ctx.h[x]
            if side * (ext - s.stop) <= 0:
                exit_ref = s.stop if x == j else (min(s.stop, ctx.o[x]) if side > 0 else max(s.stop, ctx.o[x]))
                exit_px = exit_ref * (1.0 - side * _slip(sc, p, size * exit_ref, ctx, x, "stop"))
                fees += p.taker_fee * exit_px * size
                reason, x_exit = "stop", x
                break
            far = ctx.h[x] if side > 0 else ctx.l[x]
            if x > j and side * (far - target) >= p.tick_size - 1e-9:
                exit_ref = exit_px = target
                fees += p.maker_fee * target * size
                reason, x_exit = "target", x
                break
        if exit_px is None:
            exit_ref = exit_px = ctx.c[last]
        raw = side * (exit_ref - ref) * size
        gross = side * (exit_px - fill) * size
        net = gross - fees - funding
        risk_usd = dist * size
        pnl += net
        busy = x_exit
        exit_ms = None if reason == "open" else ctx.t[x_exit]
        trades.append({"entry_ms": ctx.t[j], "exit_ms": exit_ms, "entry": _iso(ctx.t[j]), "exit": _iso(exit_ms),
                       "signal": _iso(ctx.t[s.i] + FIVE_MS), "side": "long" if side > 0 else "short",
                       "entry_px": round(fill, 4), "exit_px": round(exit_px, 4), "stop_px": round(s.stop, 4),
                       "target_px": round(target, 4), "size": size, "notional": notional, "leverage": lev,
                       "reason": reason, "raw_usd": raw, "slip_usd": raw - gross, "fees_usd": fees,
                       "funding_usd": funding, "net_usd": net, "risk_usd": risk_usd,
                       "risk_pct": risk_usd / eq * 100.0, "r": net / risk_usd if risk_usd > 1e-12 else None,
                       "tag": s.tag, "hour": (ctx.t[j] // HOUR_MS) % 24, "i": j, "x": x_exit,
                       "hold_min": (ctx.t[x_exit] - ctx.t[j]) / 60_000.0})
    return {"trades": trades, "refused": dict(refused)}


def pnl_curve(ctx: Ctx, trades: Sequence[Dict[str, Any]], lo_i: int, hi_i: int) -> List[float]:
    """The sleeve's marked P&L at each bar close in [lo_i, hi_i]: closed
    trades at their net, an open one at the bar's close less its entry fee
    share (the whole fee once closed)."""
    out: List[float] = []
    realized, k = 0.0, 0
    for i in range(lo_i, hi_i + 1):
        mark = 0.0
        while k < len(trades) and (trades[k]["x"] < i or (trades[k]["x"] == i and trades[k]["reason"] != "open")):
            realized += trades[k]["net_usd"]
            k += 1
        if k < len(trades) and trades[k]["i"] <= i:
            tr = trades[k]
            sign = 1.0 if tr["side"] == "long" else -1.0
            mark = sign * (ctx.c[i] - tr["entry_px"]) * tr["size"]
        out.append(realized + mark)
    if trades and trades[-1]["reason"] == "open" and out:
        out[-1] = sum(t["net_usd"] for t in trades)
    return out


def summarize(trades: Sequence[Dict[str, Any]], curve: Optional[Sequence[float]] = None,
              base: Optional[Sequence[float]] = None, refused: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """n, wins, net, R, profit factor, expectancy in $ and in bps of
    notional against the round-trip cost, and (with a curve) max drawdown
    of base + curve."""
    n = len(trades)
    wins = sum(1 for t in trades if t["net_usd"] > 0)
    net = sum(t["net_usd"] for t in trades)
    gain = sum(t["net_usd"] for t in trades if t["net_usd"] > 0)
    loss = -sum(t["net_usd"] for t in trades if t["net_usd"] < 0)
    rs = [t["r"] for t in trades if t["r"] is not None]
    notional = sum(t["notional"] for t in trades)
    cost = sum(t["fees_usd"] + t["slip_usd"] + t["funding_usd"] for t in trades)
    raw = sum(t["raw_usd"] for t in trades)
    exits = Counter(t["reason"] for t in trades)
    out = {"n": n, "wins": wins, "win_rate": round(wins / n * 100.0, 1) if n else None, "net_usd": round(net, 2),
           "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
           "profit_factor": round(gain / loss, 2) if loss > EPS else None,
           "exp_usd": round(net / n, 3) if n else None,
           "raw_bps": round(raw / notional * 1e4, 2) if notional else None,
           "cost_bps": round(cost / notional * 1e4, 2) if notional else None,
           "net_bps": round(net / notional * 1e4, 2) if notional else None,
           "avg_risk_pct": round(sum(t["risk_pct"] for t in trades) / n, 2) if n else None,
           "avg_hold_min": round(sum(t["hold_min"] for t in trades) / n, 1) if n else None,
           "exits": dict(sorted(exits.items())), "refused": dict(refused or {})}
    if curve is not None and base is not None and len(base):
        out["max_dd_pct"] = SL.max_dd_pct([b + c for b, c in zip(base, curve)], base[0])
    return out


def by_hour(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Net and count by UTC entry hour; the best and the worst hour."""
    hours: Dict[int, List[float]] = {}
    for t in trades:
        hours.setdefault(int(t["hour"]), []).append(t["net_usd"])
    rows = {h: {"n": len(v), "net_usd": round(sum(v), 2)} for h, v in sorted(hours.items())}
    if not rows:
        return {"hours": {}, "best": None, "worst": None}
    best = max(rows, key=lambda h: (rows[h]["net_usd"], -h))
    worst = min(rows, key=lambda h: (rows[h]["net_usd"], h))
    return {"hours": rows, "best": {"hour": best, **rows[best]}, "worst": {"hour": worst, **rows[worst]}}


# ── datasets ───────────────────────────────────────────────────────────────

def merge_candles(*sets: Sequence[Candle]) -> List[Candle]:
    """Union by bar time; a later set wins a tie (pass the freshest last)."""
    out: Dict[int, Candle] = {}
    for s in sets:
        for c in s:
            out[c.t] = c
    return [out[t] for t in sorted(out)]


def load_5m(coin: str, cache_dir: str, extra_globs: Sequence[str]) -> List[Candle]:
    """Every closed 5m candle we can get: the venue's last 5,000 (cached)
    merged with older cached snapshots matching `extra_globs`. The merged
    series is written to the cache, so a rerun on the same cache directory
    reads the same bars whatever the snapshots have become."""
    from pathiel.agents.atomic_io import read_json, write_json_atomic

    path = D._cache_path(cache_dir, coin, "merged_5m")
    cached = read_json(path)
    if isinstance(cached, list) and cached:
        return [Candle(**r) for r in cached]
    fresh = D.fetch_candles_cached(coin, "5m", 5000, cache_dir)
    asof = int(os.path.getmtime(D._cache_path(cache_dir, coin, "candles_5m_5000")) * 1000) if fresh else 0
    fresh = closed_only(fresh, FIVE_MS, asof)
    first = fresh[0].t if fresh else 0
    older: List[Candle] = []
    name = os.path.basename(D._cache_path("", coin, "candles_5m_5000"))
    for g in extra_globs:
        for f in sorted(glob.glob(os.path.join(os.path.expanduser(g), name))):
            raw = read_json(f)
            if isinstance(raw, list):
                older += [Candle(**r) for r in raw if isinstance(r, dict) and r.get("t", first) < first]
    merged = merge_candles(older, fresh)
    if merged:
        write_json_atomic(path, [c.model_dump() for c in merged])
    return merged


def contiguous_from(c5: Sequence[Candle], max_gap_ms: int = 6 * FIVE_MS) -> List[Candle]:
    """The longest tail of `c5` with no hole over `max_gap_ms`: older
    snapshots may not overlap, and a hole would fake a range or an ATR."""
    start = 0
    for i in range(1, len(c5)):
        if c5[i].t - c5[i - 1].t > max_gap_ms:
            start = i
    return list(c5[start:])


def make_dataset(c5: Sequence[Candle], c15: Sequence[Candle], c1h: Sequence[Candle], direction_at,
                 thesis_at: SL.ThesisAt, bp: Params, funding, start_ms: int, end_ms: int,
                 ctx: Optional[Ctx] = None) -> Dict[str, Any]:
    """One dataset on [start_ms, end_ms): the 5m context (built on every
    bar given, so indicators are warm at the start), and the live long
    book on that window (`short_ladder_test.prepare`) mapped onto the 5m
    bars."""
    live = SL.prepare(c15, c1h, direction_at, thesis_at, bp, funding, start_ms, end_ms)
    if ctx is None:
        ctx = build_ctx(c5, c1h, lambda t: (thesis_at(t) or (None,))[0], funding)
    tick_t = [tk.t for tk in live["ticks"]]
    eq, opn, ent = [], [], []
    starts = sorted(e for e, _ in live["intervals"])
    ivs = sorted((e, x if x is not None else end_ms) for e, x in live["intervals"])
    entries = set(starts)
    for t in ctx.t:
        k = bisect.bisect_right(tick_t, t) - 1
        eq.append(live["long_curve"][k] if k >= 0 else bp.starting_equity)
        opn.append(any(e <= t <= x for e, x in ivs))
        ent.append(t in entries)
    lo_i = bisect.bisect_left(ctx.t, start_ms)
    hi_i = bisect.bisect_left(ctx.t, end_ms) - 1
    live_dd = SL.max_dd_pct(eq[lo_i:hi_i + 1], bp.starting_equity)
    return {"ctx": ctx, "live": Live(eq, opn, ent), "intervals": live["intervals"], "start_ms": start_ms,
            "end_ms": end_ms, "lo_i": lo_i, "hi_i": hi_i, "params": bp, "anchors": session_anchors(ctx.t),
            "live_sum": {"trades": live["live"]["trades"], "wins": live["live"]["wins"],
                         "net_usd": live["live"]["net_usd"], "max_dd_pct": live_dd}}


def run_one(sig: Sequence[Signal], ds: Dict[str, Any], sc: Scenario, risk: float, curves: bool,
            keep: bool = False) -> Dict[str, Any]:
    """One variant's signals on one dataset under one scenario and risk:
    alone, and beside the live book."""
    ctx, bp = ds["ctx"], ds["params"]
    start = bp.starting_equity
    alone = walk5(ctx, sig, bp, risk, sc, start, ds["start_ms"], ds["end_ms"])
    comb = walk5(ctx, sig, bp, risk, sc, start, ds["start_ms"], ds["end_ms"], ds["live"])
    lo_i, hi_i = ds["lo_i"], ds["hi_i"]
    if curves:
        flat = [start] * (hi_i - lo_i + 1)
        s_alone = summarize(alone["trades"], pnl_curve(ctx, alone["trades"], lo_i, hi_i), flat, alone["refused"])
        s_comb = summarize(comb["trades"], pnl_curve(ctx, comb["trades"], lo_i, hi_i),
                           ds["live"].eq[lo_i:hi_i + 1], comb["refused"])
    else:
        s_alone, s_comb = summarize(alone["trades"]), summarize(comb["trades"])
    out = {"alone": s_alone, "combined": s_comb, "live": ds["live_sum"],
           "combined_dd_pct": s_comb.get("max_dd_pct"),
           "combined_total_usd": round(ds["live_sum"]["net_usd"] + s_comb["net_usd"], 2)}
    if curves:
        out["collisions"] = SL.collisions(alone["trades"], ds["intervals"], ds["end_ms"])
        out["drop_best_day"] = SL.drop_best_day(comb["trades"])
    if keep:
        out["trades_alone"], out["trades_combined"] = alone["trades"], comb["trades"]
    return out


def evaluate(vs: Sequence[Variant], datasets: Dict[str, Dict[str, Any]], halves: Sequence[Dict[str, Any]],
             scs: Dict[str, Scenario], log: Optional[Callable[[str], None]] = None, primary: str = PRIMARY
             ) -> Dict[str, Dict[str, Any]]:
    """Every variant at both risk sizes: the primary scenario in full on
    every dataset and both halves, judged; the other scenarios as net and
    trade count. Rows are keyed "<variant>@<risk>"."""
    rows: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    sc0 = scs[PRIMARY_SCENARIO]
    for i, v in enumerate(vs, 1):
        sig = {name: signals_for(v, ds["ctx"], ds["anchors"]) for name, ds in datasets.items()}
        hsig = [signals_for(v, h["ctx"], h["anchors"]) for h in halves]
        for label, risk in RISKS:
            dsr = {name: run_one(sig[name], ds, sc0, risk, True, keep=True) for name, ds in datasets.items()}
            hv = [run_one(hs, h, sc0, risk, False) for hs, h in zip(hsig, halves)]
            other = {sn: {name: {k: {"n": r[k]["n"], "net_usd": r[k]["net_usd"], "wins": r[k]["wins"]}
                                 for k in ("alone", "combined")}
                          for name, ds in datasets.items() for r in [run_one(sig[name], ds, sc, risk, False)]}
                     for sn, sc in scs.items() if sn != PRIMARY_SCENARIO}
            hours = by_hour(dsr[primary]["trades_alone"]) if primary in dsr else {}
            pooled = dsr[primary]["trades_alone"] if primary in dsr else []
            for d in dsr.values():
                d.pop("trades_alone"), d.pop("trades_combined")
            rows[f"{v.name}@{label}"] = {
                "name": v.name, "risk": label, "risk_frac": risk, "family": v.family, "kind": v.kind,
                "thesis": v.thesis, "args": list(v.args), "signals": {k: len(s) for k, s in sig.items()},
                "datasets": dsr, "halves": [h["combined"]["net_usd"] for h in hv],
                "halves_alone": [h["alone"]["net_usd"] for h in hv],
                "halves_n": [h["combined"]["n"] for h in hv], "halves_n_alone": [h["alone"]["n"] for h in hv],
                "scenarios": other, "hours": hours,
                "_hour_trades": [(t["hour"], t["net_usd"]) for t in pooled],
                "drop_best_day": dsr[primary]["drop_best_day"] if primary in dsr else {"pass": False, "rest_usd": 0.0}}
        if log is not None and (i % 26 == 0 or i == len(vs)):
            eta = (time.time() - t0) / i * (len(vs) - i)
            log(f"{i / len(vs) * 100.0:.0f}% done, about {eta:.0f}s left; {i}/{len(vs)} variants")
    for v in vs:
        for label, _ in RISKS:
            row = rows[f"{v.name}@{label}"]
            nets = [rows[f"{nb.name}@{label}"]["datasets"][primary]["combined"]["net_usd"]
                    if f"{nb.name}@{label}" in rows and primary in rows[f"{nb.name}@{label}"]["datasets"] else None
                    for nb in neighbours(v)]
            row["neighbour_nets"] = nets
            row["judge"] = SL.judge(row, nets, primary, min_n=MIN_N, reopen_leg=False)
    return rows


def family_verdict(verdicts: Sequence[str], alone_judged: int = 0, alone_winners: int = 0) -> str:
    """The bar first: PASS when a variant passes, else LEANING when one
    leans, else FAIL when one had the trades to be judged (and failed).
    With no variant at 30 trades beside the live book, the family is still
    a FAIL when variants with 30 or more trades ALONE exist
    (`alone_judged`) and none of them made money alone on every dataset
    (`alone_winners`); otherwise NOISE."""
    for v in ("PASS", "LEANING", "FAIL"):
        if v in verdicts:
            return v
    return "FAIL" if alone_judged and not alone_winners else "NOISE"


def best_of(rows: Dict[str, Dict[str, Any]], family: int, risk: str, primary: str = PRIMARY) -> Optional[str]:
    """The family's best row at one risk size: one that traded beside the
    live book first, then by verdict, then by the sleeve's net on the
    primary, the name breaking ties."""
    names = [k for k, r in rows.items() if r["family"] == family and r["risk"] == risk and primary in r["datasets"]]
    if not names:
        return None
    return min(names, key=lambda k: (rows[k]["datasets"][primary]["combined"]["n"] == 0,
                                     SL.VERDICT_RANK[rows[k]["judge"]["verdict"]],
                                     -rows[k]["datasets"][primary]["combined"]["net_usd"], k))


def summarize_families(rows: Dict[str, Dict[str, Any]], names: Sequence[str], scs: Sequence[str],
                       primary: str = PRIMARY) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for fam, title in FAMILIES.items():
        block: Dict[str, Any] = {"title": title}
        for risk, _ in RISKS:
            rs = [r for r in rows.values() if r["family"] == fam and r["risk"] == risk]
            if not rs:
                continue
            verdicts = Counter(r["judge"]["verdict"] for r in rs)
            per_ds = {}
            for ds in names:
                comb = [r["datasets"][ds]["combined"] for r in rs]
                alone = [r["datasets"][ds]["alone"] for r in rs]
                traded = [c for c in comb if c["n"]]
                per_ds[ds] = {
                    "traded": len(traded), "above_zero": sum(1 for c in traded if c["net_usd"] > EPS),
                    "median_net_usd": round(statistics.median(c["net_usd"] for c in traded), 2) if traded else None,
                    "trades": sum(c["n"] for c in comb), "wins": sum(c["wins"] for c in comb),
                    "sum_net_usd": round(sum(c["net_usd"] for c in comb), 2),
                    "alone_trades": sum(a["n"] for a in alone), "alone_wins": sum(a["wins"] for a in alone),
                    "alone_above_zero": sum(1 for a in alone if a["n"] and a["net_usd"] > EPS),
                    "alone_sum_net_usd": round(sum(a["net_usd"] for a in alone), 2),
                    "alone_median_net_usd": round(statistics.median(a["net_usd"] for a in alone), 2),
                    "n30_plus": sum(1 for c in comb if c["n"] >= MIN_N),
                    "collisions": sum(r["datasets"][ds]["collisions"]["overlap"] for r in rs),
                    "collision_n": sum(r["datasets"][ds]["collisions"]["n"] for r in rs)}
            sens = {sn: {ds: {"alone_above_zero": sum(1 for r in rs if r["scenarios"][sn][ds]["alone"]["net_usd"] > EPS),
                              "alone_sum_net_usd": round(sum(r["scenarios"][sn][ds]["alone"]["net_usd"] for r in rs), 2),
                              "alone_trades": sum(r["scenarios"][sn][ds]["alone"]["n"] for r in rs),
                              "combined_sum_net_usd": round(sum(r["scenarios"][sn][ds]["combined"]["net_usd"]
                                                                for r in rs), 2)}
                         for ds in names} for sn in scs if sn != PRIMARY_SCENARIO}
            pooled = [{"hour": h, "net_usd": u} for r in rs for h, u in r["_hour_trades"]]
            best = best_of(rows, fam, risk, primary)
            judged = [r for r in rs if r["datasets"][primary]["alone"]["n"] >= MIN_N]
            winners = [r for r in judged if all(r["datasets"][ds]["alone"]["net_usd"] > EPS for ds in names)]
            block[risk] = {"variants": len(rs), "verdicts": dict(verdicts), "alone_n30": len(judged),
                           "alone_n30_above_zero_every_dataset": len(winners),
                           "verdict": family_verdict(list(verdicts), len(judged), len(winners)),
                           "datasets": per_ds, "scenarios": sens,
                           "above_zero_every_dataset": sum(1 for r in rs if all(
                               r["datasets"][ds]["combined"]["net_usd"] > EPS for ds in names)),
                           "both_halves_positive": sum(1 for r in rs if all(h > EPS for h in r["halves"])),
                           "hours_pooled_alone": by_hour(pooled), "best": best}
        out[str(fam)] = block
    return out


def cost_table(ctx: Ctx, lo_i: int, hi_i: int, scs: Dict[str, Scenario], bp: Params, notional: float
               ) -> Dict[str, Any]:
    """The size of a 5m move against a round trip's cost, in bps: the
    median |close - open| and high - low of the window's weekday bars, and
    per scenario the cost of market-in / stop-out and market-in / target-out
    at the median bar."""
    idx = [i for i in range(lo_i, hi_i + 1) if not ctx.weekend[i] and ctx.o[i] > 0]
    body = statistics.median(abs(ctx.c[i] - ctx.o[i]) / ctx.o[i] for i in idx) * 1e4
    rng = statistics.median((ctx.h[i] - ctx.l[i]) / ctx.o[i] for i in idx) * 1e4
    atr = statistics.median(ctx.atr[i] / ctx.c[i] for i in idx if ctx.atr[i]) * 1e4
    out = {"bars": len(idx), "median_body_bps": round(body, 2), "median_range_bps": round(rng, 2),
           "median_atr14_bps": round(atr, 2), "scenarios": {}}
    for name, sc in scs.items():
        p = dataclasses.replace(bp, taker_fee=sc.taker, maker_fee=sc.maker)
        if sc.slip == "zero":
            ent = stp = 0.0
        elif sc.slip == "curve":
            ent = stp = exit_slip_bps(notional)
        else:
            stp = _market_exit_slip(p, notional) * 1e4
            ent = statistics.median(max(stp, (ctx.h[i] - ctx.l[i]) / ctx.o[i] / 2.0 * 1e4) for i in idx)
        out["scenarios"][name] = {
            "taker_bps": round(sc.taker * 1e4, 2), "maker_bps": round(sc.maker * 1e4, 2),
            "entry_slip_bps": round(ent, 2), "stop_slip_bps": round(stp, 2),
            "round_trip_stop_bps": round(2 * sc.taker * 1e4 + ent + stp, 2),
            "round_trip_target_bps": round((sc.taker + sc.maker) * 1e4 + ent, 2),
            "round_trip_stop_vs_median_range": round((2 * sc.taker * 1e4 + ent + stp) / rng, 2) if rng else None}
    return out


def latency_drift(c1m: Sequence[Candle], minutes: int = 2) -> Dict[str, Any]:
    """Measured on 1m candles: how far the price sits from a 5m bar's open
    `minutes` later (where a watcher-fired tick really fills), in bps."""
    by_t = {c.t: c for c in c1m}
    drift = []
    for c in c1m:
        if c.t % FIVE_MS or weekend_closed(c.t) or c.o <= 0:
            continue
        later = by_t.get(c.t + minutes * 60_000)
        if later is not None:
            drift.append(abs(later.o - c.o) / c.o * 1e4)
    if not drift:
        return {"n": 0}
    drift.sort()
    return {"n": len(drift), "minutes": minutes, "median_bps": round(statistics.median(drift), 2),
            "p90_bps": round(drift[int(0.9 * (len(drift) - 1))], 2)}


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    ap = argparse.ArgumentParser(
        description="Intraday 5-minute backtest on Brent and WTI: opening-range breakout, session-VWAP reversion, "
                    "trend pullback, big-bar momentum and thesis-aligned scalps, each alone and beside the live "
                    "long book, under five declared cost scenarios. Research only: reads candles, the journal and "
                    "the review ledger; never trades or writes config.",
        epilog="Exit 0: a variant passes the bar. 1: none does. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else ""),
        help="READ ONLY: the live oil journal's theses (default: $PATHIEL_STATE_DIR/strategies/oil/theses)")
    ap.add_argument("--ledger", default=os.path.join(state, "review", "ledger.jsonl") if state else "",
                    help="READ ONLY: the review's trade ledger, for the measured fee")
    ap.add_argument("--cache-dir", default="/tmp/intraday-5m/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/intraday-5m")
    ap.add_argument("--extra-5m-glob", nargs="*", default=["/tmp/review/*/cache"] + (
        [os.path.join(state, "pool", "work", "*", "cache")] if state else []),
        help="READ ONLY: directories holding older <coin>__candles_5m_5000.json snapshots to merge in")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--no-wti", action="store_true", help="skip the WTI dataset")
    ap.add_argument("--detail", nargs="*", default=[], metavar="NAME", help="variants whose trades to keep")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} intraday-5m: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        c5 = contiguous_from(load_5m(coin, a.cache_dir, a.extra_5m_glob))
        end = min(c15[-1].t + FIFTEEN_MS, c5[-1].t + FIVE_MS)
        end -= end % FIFTEEN_MS
        c1m = D.fetch_candles_cached(coin, "1m", 5000, a.cache_dir)
        return c5, c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, c15[-1].t + FIFTEEN_MS, a.cache_dir), c1m

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    fees = measured_fees(a.ledger)
    bp = base_params(a.start_equity, RISK_FULL)
    scs = scenarios(fees["taker"], fees["maker"], bp)
    b5, b15, b1h, b_end, b_fund, b1m = load(BRENT)
    warm = (ATR_N + EMA_N) * FIVE_MS
    start = max(min(th.created_at_ms for th in theses), b5[0].t + warm, b15[0].t)
    start += -start % FIFTEEN_MS
    dir_at, th_at = journaled_direction(theses), SL.thesis_lookup(theses)
    datasets = {PRIMARY: make_dataset(b5, b15, b1h, dir_at, th_at, bp, b_fund, start, b_end)}
    meta = {PRIMARY: {"window": [_iso(start), _iso(b_end)], "bars_5m": datasets[PRIMARY]["hi_i"] - datasets[PRIMARY]["lo_i"] + 1,
                      "days": round((b_end - start) / DAY_MS, 1), "first_5m": _iso(b5[0].t)}}
    mid = start + (b_end - start) // 2
    mid -= mid % FIFTEEN_MS
    halves = [make_dataset(b5, b15, b1h, dir_at, th_at, bp, b_fund, s, e, ctx=datasets[PRIMARY]["ctx"])
              for s, e in ((start, mid), (mid, b_end))]
    drift = {BRENT: latency_drift(b1m)}
    if not a.no_wti:
        w5, w15, w1h, w_end, w_fund, w1m = load(WTI)
        ratio = SL.price_ratio(theses, b1h, w1h)
        w_start = max(start, w5[0].t + warm, w15[0].t)
        w_start += -w_start % FIFTEEN_MS
        w_stop = min(b_end, w_end)
        datasets["wti_5m"] = make_dataset(w5, w15, w1h, side_only(dir_at), SL.thesis_lookup(theses, ratio), bp, w_fund,
                                          w_start, w_stop)
        meta["wti_5m"] = {"window": [_iso(w_start), _iso(w_stop)], "days": round((w_stop - w_start) / DAY_MS, 1),
                          "bars_5m": datasets["wti_5m"]["hi_i"] - datasets["wti_5m"]["lo_i"] + 1,
                          "first_5m": _iso(w5[0].t)}
        drift[WTI] = latency_drift(w1m)
    vs = variants()
    log(f"{len(theses)} theses; datasets {meta}; halves split at {_iso(mid)}; {len(vs)} variants x {len(RISKS)} "
        f"risk sizes; fee {fees}; live {({k: d['live_sum'] for k, d in datasets.items()})}")
    rows = evaluate(vs, datasets, halves, scs, log)
    names = list(datasets)
    fams = summarize_families(rows, names, list(scs))
    for r in rows.values():
        r.pop("_hour_trades", None)
    by_name = {v.name: v for v in vs}
    wanted = list(dict.fromkeys([rows[b]["name"] for f in fams.values() for risk, _ in RISKS
                                 for b in [f.get(risk, {}).get("best")] if b] + [n for n in a.detail if n in by_name]))
    detail = {f"{n}@{label}": {ds: run_one(signals_for(by_name[n], d["ctx"], d["anchors"]), d,
                                           scs[PRIMARY_SCENARIO], risk, True, keep=True)
                               for ds, d in datasets.items()} for n in wanted for label, risk in RISKS}
    passing = sorted(k for k, r in rows.items() if r["judge"]["verdict"] == "PASS")
    typical = RISK_FULL * a.start_equity / 0.003          # a 30 bps stop at 6% risk
    costs = {ds: cost_table(d["ctx"], d["lo_i"], d["hi_i"], scs, bp, typical) for ds, d in datasets.items()}
    anchors = {ds: {k: len([t for t in v if d["start_ms"] <= t < d["end_ms"]]) for k, v in d["anchors"].items()}
               for ds, d in datasets.items()}

    csv_path = os.path.join(a.out_dir, "variants.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        head = ["variant", "risk", "family", "verdict"]
        for ds in names:
            head += [f"{ds}_{m}_{k}" for m in ("alone", "beside") for k in
                     ("n", "wins", "net_usd", "avg_r", "pf", "max_dd_pct", "exp_usd", "raw_bps", "cost_bps")]
            head += [f"{ds}_live_dd_pct"] + [f"{ds}_{sn}_alone_net_usd" for sn in scs if sn != PRIMARY_SCENARIO]
        w.writerow(head + ["half1_usd", "half2_usd", "half1_n", "half2_n", "rest_without_best_day_usd"])
        for k in sorted(rows):
            r = rows[k]
            line = [r["name"], r["risk"], r["family"], r["judge"]["verdict"]]
            for ds in names:
                d = r["datasets"][ds]
                for m in ("alone", "combined"):
                    s = d[m]
                    line += [s["n"], s["wins"], s["net_usd"], s["avg_r"], s["profit_factor"], s.get("max_dd_pct"),
                             s["exp_usd"], s["raw_bps"], s["cost_bps"]]
                line += [d["live"]["max_dd_pct"]] + [r["scenarios"][sn][ds]["alone"]["net_usd"]
                                                      for sn in scs if sn != PRIMARY_SCENARIO]
            w.writerow(line + r["halves"] + r["halves_n"] + [r["drop_best_day"]["rest_usd"]])
    trades_path = os.path.join(a.out_dir, "best_trades.csv")
    with open(trades_path, "w", newline="") as fh:
        fields = ["variant", "dataset", "mode", "signal", "entry", "exit", "side", "entry_px", "exit_px", "stop_px",
                  "target_px", "leverage", "notional", "reason", "raw_usd", "slip_usd", "fees_usd", "funding_usd",
                  "net_usd", "r", "risk_pct", "tag", "hour", "hold_min"]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for name, per in detail.items():
            for ds, d in per.items():
                for mode in ("alone", "combined"):
                    for t in d[f"trades_{mode}"]:
                        w.writerow({"variant": name, "dataset": ds, "mode": mode, **t})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "start_equity": a.start_equity, "risks": dict(RISKS), "min_n": MIN_N, "datasets": meta,
        "halves_split": _iso(mid), "fees": fees, "scenarios": {k: dataclasses.asdict(s) for k, s in scs.items()},
        "primary_scenario": PRIMARY_SCENARIO, "n_variants": len(vs), "n_judged_rows": len(rows),
        "live": {k: d["live_sum"] for k, d in datasets.items()}, "live_halves": [h["live_sum"] for h in halves],
        "anchors": anchors, "costs": costs, "latency_drift_1m": drift, "families": fams, "passing": passing,
        "verdicts": dict(Counter(r["judge"]["verdict"] for r in rows.values())), "rows": rows, "best_trades": detail,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nvariants: {csv_path}\nbest trades: {trades_path}")
    return 0 if passing else 1


def _print(rep: Dict[str, Any]) -> None:
    names = list(rep["datasets"])
    print(f"\nIntraday 5m test: {rep['n_variants']} variants x 2 risk sizes, scenario '{rep['primary_scenario']}', "
          f"fee {rep['fees']['taker'] * 1e4:.2f}/{rep['fees']['maker'] * 1e4:.2f} bps ({rep['fees']['source']})")
    for ds in names:
        c = rep["costs"][ds]
        s = c["scenarios"][rep["primary_scenario"]]
        print(f"  {ds} {rep['datasets'][ds]['window']}: live {rep['live'][ds]}; median 5m body {c['median_body_bps']} "
              f"bps, range {c['median_range_bps']} bps; round trip (stop) {s['round_trip_stop_bps']} bps")
    print(f"  verdicts: {rep['verdicts']}; passing: {rep['passing'] or 'none'}")
    for fam, block in rep["families"].items():
        print(f"\n{block['title']}")
        for risk in rep["risks"]:
            b = block.get(risk)
            if not b:
                continue
            print(f"  {risk}: {b['variants']} variants, verdict {b['verdict']} {b['verdicts']}; above zero on every "
                  f"dataset {b['above_zero_every_dataset']}; 30+ trades alone {b['alone_n30']}, of those above "
                  f"zero alone on every dataset {b['alone_n30_above_zero_every_dataset']}")
            for ds in names:
                d = b["datasets"][ds]
                print(f"    {ds}: alone {d['alone_trades']} trades, {d['alone_above_zero']} variants above zero, "
                      f"sum {d['alone_sum_net_usd']:+.2f}; beside live {d['trades']} trades, {d['above_zero']}/"
                      f"{d['traded']} above zero, median {d['median_net_usd']}")
            r = rep["rows"].get(b["best"]) if b["best"] else None
            if r:
                p = r["datasets"][names[0]]["combined"]
                print(f"    best {b['best']}: n {p['n']}, win {p['win_rate']}%, net {p['net_usd']:+.2f}, DD "
                      f"{p.get('max_dd_pct')}%, halves {r['halves']}, {r['judge']['verdict']}")


if __name__ == "__main__":
    raise SystemExit(main())
