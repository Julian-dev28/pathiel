"""Strategy tournament plus a walk-forward round robin on real Brent candles.

Operator, 2026-11-10, after the dip-buy long gave back its October gain
in the November slide (112 -> 89 on Iran-deal hopes): "dont pause it, I
want you to find what works!!!! Try a round robin with strategies that
work." Research only: nothing here is wired live.

    python3 -m services.strategies.evals.strategy_tournament --cache-dir DIR [--json out.json]

Data: xyz:BRENTOIL 1h candles, the longest window the venue serves (5000
bars, about 208 days, 2026-04-16 to 2026-11-10), cached on disk by
`services.strategies.backtest.data.fetch_candles_paged_cached` so a rerun
reads the same bars. xyz:CL (WTI) is the out-of-sample second instrument.
The first WARMUP_BARS hours feed the slow features; every candidate is
scored on the same window after them.

One engine for every candidate (`run_strategy`): one decision per 1h bar
at its open, levels from CLOSED bars only (no lookahead), market entries
at the open, intrabar stop/target checks with the stop judged first when
both touch in one bar, a bar that opens through the stop fills at the
open. Same costs for all (`Costs`): 4.5 bps taker each way (the target is
charged as a taker too, the conservative read), 3 bps slippage on every
market fill, 6% of equity at risk per trade sized off the stop, leverage
fitted so the stop sits inside half the liquidation distance
(`pathiel.agents.bracket.max_leverage_for_stop`, 20x cap), one position
at a time, the oil weekend flat (`services.strategies.weekend`), start
equity $182.09.

Candidates (parameters fixed before the run; every value reported):
  A  baseline: the live dip-buy long, mechanical and always long (market
     entry inside the 24h range, stop 0.75 under the 24h low, target the
     24h high less 0.5%, 2R gate, leverage 20 or 15 as live). A_short is
     the mirror, the always-short control.
  B  trend filter both sides: A's long only while the last close is above
     a slow EMA (48h, 120h) or the 5-day return is positive; the mirror
     short at the 24h high only while below / negative.
  C  Donchian breakout both sides: a 1h close over the prior N-hour high
     (N = 24, 48, 96) buys at the next open with a 2 x ATR14 stop and a
     3 x ATR14 trailing stop off the best close; the mirror sells.
  D  momentum after the desk's own price alarms (headline_watch: drift
     1.75% in 4h, shock 1% in 15m, read here as 1% in one 1h bar): trade
     WITH the move at the next open, stop 0.75 beyond the last 4 closed
     bars' extreme against it, target 2R or 3R, out after 24 bars.
  E  mean reversion both sides, wider stop: a mid in the bottom (top)
     quarter of the 24h range buys (sells) with the stop k x ATR14 beyond
     the edge (k = 1.5, 2.0), the far edge less 0.5% as target, 1R gate.
     The 6% risk rule makes the size smaller by itself.
  F  time-series momentum: long while the 120h (240h) close-to-close
     return is positive, short while negative, flipped at the bar close
     where the sign changes, 2 x ATR14 stop. The most documented
     commodity-futures edge, never tested in this repo.
  G  flat: the floor.

Round robin (`walk_forward`): every REBALANCE days (7 and 3) pick the
candidate with the best trailing net over the previous LOOKBACK days
(7 and 14) and trade only its entries until the next rebalance, out of
sample by construction. `equal_split` runs every candidate on an equal
slice of equity; `hindsight_best` is the best single candidate over the
whole window, labelled hindsight, not tradeable.

Pass bar, stated before the run (`passes`): a candidate or selector
"works" only if, on Brent, net $ > the baseline's net AND > 0, positive in
at least 2 of 3 equal sub-windows, max drawdown under 35%, at least 15
trades, and net on WTI >= 0. Ranked by net $ with drawdown alongside.
Exit code 1 when nothing passes.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import max_leverage_for_stop, offset_target
from pathiel.models.types import Candle
from services.strategies.weekend import weekend_closed

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

BRENT = "xyz:BRENTOIL"
WTI = "xyz:CL"
# The fetch window the cache was written with (2026-11-10 16:26Z; the venue
# served its newest 5000 1h bars, 2026-04-16 06:00Z onward). DATA_END drops
# the bar still open at fetch time. A refetch on a later day gets a later
# 5000-bar window: keep the cache directory to reproduce this run.
FETCH_START_MS = 1794441600000 - 400 * DAY_MS
FETCH_END_MS = 1794441600000
DATA_END_MS = 1794326400000          # 2026-11-10T16:00Z
WARMUP_BARS = 240                    # 10 days: the slowest feature (240h return)
START_EQUITY = 182.09
PASS_MAX_DD = 0.35
PASS_MIN_TRADES = 15
LIVE_LEV_FIT = (20.0, 15.0)
WIDE_LEV_FIT = (20.0, 15.0, 10.0, 8.0, 5.0)


# ── costs and sizing ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Costs:
    taker_fee: float = 0.00045
    slippage: float = 0.0003
    risk_frac: float = 0.06
    max_leverage: float = 20.0
    max_stop_frac_of_liq: float = 0.5
    margin_use: float = 0.95          # share of equity a position's margin may use
    min_notional: float = 10.0
    start_equity: float = START_EQUITY


def fit_leverage(entry: float, stop: float, costs: Costs, candidates: Sequence[float]) -> Optional[float]:
    """The highest candidate leverage (<= the cap) at which the stop sits
    inside `max_stop_frac_of_liq` of the liquidation distance; None when
    even the lowest candidate fails."""
    ok = max_leverage_for_stop(entry, stop, costs.max_leverage, costs.max_stop_frac_of_liq)
    for lev in sorted((c for c in candidates if c <= costs.max_leverage), reverse=True):
        if lev <= ok:
            return float(lev)
    return None


def size_for(equity: float, entry: float, stop: float, lev: float, costs: Costs) -> Tuple[float, float]:
    """(size in units, notional $): `risk_frac` of equity lost at the stop
    including the fee both ways and the stop's slippage, capped by the
    margin the leverage allows. (0, 0) under the venue minimum."""
    per_unit = abs(entry - stop) + entry * (2.0 * costs.taker_fee + costs.slippage)
    if per_unit <= 0 or equity <= 0:
        return 0.0, 0.0
    notional = costs.risk_frac * equity / per_unit * entry
    notional = min(notional, costs.margin_use * equity * lev)
    if notional < costs.min_notional:
        return 0.0, 0.0
    return notional / entry, notional


# ── features from closed bars ────────────────────────────────────────────────

EMA_SPANS = (48, 120)
# 120 and 240 are the pre-registered spans; 72, 96 and 168 feed only the
# post-hoc neighbour check (`post_hoc_candidates`).
RET_SPANS = (72, 96, 120, 168, 240)
DONCHIAN_SPANS = (24, 48, 96)
ATR_N = 14


@dataclass
class Features:
    """Per bar index i, everything computed from bars[:i] (the closed bars
    before bar i opens). Index len(bars) holds the state after the last
    bar closed. None where the history is too short."""
    atr: List[Optional[float]]
    hi24: List[Optional[float]]
    lo24: List[Optional[float]]
    hi4: List[Optional[float]]
    lo4: List[Optional[float]]
    ema: Dict[int, List[Optional[float]]]
    ret: Dict[int, List[Optional[float]]]
    donch_hi: Dict[int, List[Optional[float]]]
    donch_lo: Dict[int, List[Optional[float]]]
    last_close: List[Optional[float]]
    last_move: List[Optional[float]]     # (c - o) / o of the last closed bar
    drift4: List[Optional[float]]        # the largest move of the last close vs the 4 closes before it


def _window_max(vals: Sequence[float], end: int, n: int) -> Optional[float]:
    if end - n < 0:
        return None
    return max(vals[end - n:end])


def _window_min(vals: Sequence[float], end: int, n: int) -> Optional[float]:
    if end - n < 0:
        return None
    return min(vals[end - n:end])


def compute_features(bars: Sequence[Candle]) -> Features:
    n = len(bars)
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]
    trs = [0.0] * n
    for i in range(n):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr: List[Optional[float]] = [None] * (n + 1)
    hi24: List[Optional[float]] = [None] * (n + 1)
    lo24: List[Optional[float]] = [None] * (n + 1)
    hi4: List[Optional[float]] = [None] * (n + 1)
    lo4: List[Optional[float]] = [None] * (n + 1)
    last_close: List[Optional[float]] = [None] * (n + 1)
    last_move: List[Optional[float]] = [None] * (n + 1)
    drift4: List[Optional[float]] = [None] * (n + 1)
    ema: Dict[int, List[Optional[float]]] = {s: [None] * (n + 1) for s in EMA_SPANS}
    ret: Dict[int, List[Optional[float]]] = {s: [None] * (n + 1) for s in RET_SPANS}
    donch_hi: Dict[int, List[Optional[float]]] = {s: [None] * (n + 1) for s in DONCHIAN_SPANS}
    donch_lo: Dict[int, List[Optional[float]]] = {s: [None] * (n + 1) for s in DONCHIAN_SPANS}
    ema_state: Dict[int, Optional[float]] = {s: None for s in EMA_SPANS}
    for i in range(1, n + 1):
        c = closes[i - 1]
        last_close[i] = c
        o = bars[i - 1].o
        last_move[i] = (c - o) / o if o else 0.0
        # ATR14 over the 14 closed bars ending at i-1 (the sim/levels formula
        # needs 15 bars: the 14 true ranges use the close before each).
        if i >= ATR_N + 1:
            atr[i] = sum(trs[i - ATR_N:i]) / ATR_N
        hi24[i] = _window_max(highs, i, 24)
        lo24[i] = _window_min(lows, i, 24)
        hi4[i] = _window_max(highs, i, 4)
        lo4[i] = _window_min(lows, i, 4)
        for s in EMA_SPANS:
            prev = ema_state[s]
            k = 2.0 / (s + 1.0)
            cur = c if prev is None else prev + k * (c - prev)
            ema_state[s] = cur
            ema[s][i] = cur if i >= s else None
        for s in RET_SPANS:
            if i - 1 - s >= 0:
                base = closes[i - 1 - s]
                ret[s][i] = c / base - 1.0 if base else None
        for s in DONCHIAN_SPANS:
            # the N bars BEFORE the last closed bar: the level the last close broke
            donch_hi[s][i] = _window_max(highs, i - 1, s)
            donch_lo[s][i] = _window_min(lows, i - 1, s)
        if i - 1 - 4 >= 0:
            moves = [c / closes[i - 1 - k] - 1.0 for k in range(1, 5)]
            drift4[i] = max(moves, key=abs)
    return Features(atr=atr, hi24=hi24, lo24=lo24, hi4=hi4, lo4=lo4, ema=ema, ret=ret, donch_hi=donch_hi,
                    donch_lo=donch_lo, last_close=last_close, last_move=last_move, drift4=drift4)


# ── candidates ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Setup:
    side: int                              # +1 long, -1 short
    stop_px: float
    target_px: Optional[float] = None      # None: no target (trail / flip / time exit only)
    trail_atr: Optional[float] = None      # trailing stop distance in ATR14 off the best close
    max_hold_bars: Optional[int] = None
    lev_fit: Tuple[float, ...] = WIDE_LEV_FIT


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str
    params: Dict[str, Any]
    setup: Callable[[int, float, Features], Optional[Setup]]
    # Evaluated at a bar's close while a position is open (features at i+1):
    # True closes the position at that close ("flip").
    exit_signal: Optional[Callable[[int, Features, int], bool]] = None


def range_setup(side: int, mid: float, lo: float, hi: float, buffer: float, min_r: float,
                lev_fit: Tuple[float, ...], offset: float = 0.005) -> Optional[Setup]:
    """The live 24h-range rule for one side: no entry past the range's own
    edge (NO_BREAK_ENTRY), stop `buffer` beyond the edge, target the far
    edge `offset` inside, `min_r` gate. Pure."""
    if side > 0:
        if mid < lo or mid >= hi:
            return None
        stop = lo - buffer
        tp = offset_target(hi, "long", offset)
        if tp <= mid or stop >= mid:
            return None
        r = (tp - mid) / (mid - stop)
    else:
        if mid > hi or mid <= lo:
            return None
        stop = hi + buffer
        tp = offset_target(lo, "short", offset)
        if tp >= mid or stop <= mid:
            return None
        r = (mid - tp) / (stop - mid)
    if r + 1e-9 < min_r:
        return None
    return Setup(side=side, stop_px=stop, target_px=tp, lev_fit=lev_fit)


def make_baseline(side: int, buffer: float = 0.75, min_r: float = 2.0) -> Strategy:
    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        if f.lo24[i] is None:
            return None
        return range_setup(side, mid, f.lo24[i], f.hi24[i], buffer, min_r, LIVE_LEV_FIT)
    name = "A_long" if side > 0 else "A_short"
    return Strategy(name, "A", {"side": side, "buffer": buffer, "min_r": min_r, "lev_fit": "20/15"}, setup)


def make_trend_filter(kind: str, span: int, buffer: float = 0.75, min_r: float = 2.0) -> Strategy:
    def trend(i: int, f: Features) -> int:
        c = f.last_close[i]
        if kind == "ema":
            e = f.ema[span][i]
            if c is None or e is None:
                return 0
            return 1 if c > e else (-1 if c < e else 0)
        r = f.ret[span][i]
        if r is None:
            return 0
        return 1 if r > 0 else (-1 if r < 0 else 0)

    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        if f.lo24[i] is None:
            return None
        side = trend(i, f)
        if side == 0:
            return None
        return range_setup(side, mid, f.lo24[i], f.hi24[i], buffer, min_r, LIVE_LEV_FIT)
    return Strategy(f"B_{kind}{span}", "B", {"trend": kind, "span_h": span, "buffer": buffer, "min_r": min_r,
                                             "lev_fit": "20/15"}, setup)


def make_donchian(span: int, stop_atr: float = 2.0, trail_atr: float = 3.0) -> Strategy:
    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        c, hi, lo, a = f.last_close[i], f.donch_hi[span][i], f.donch_lo[span][i], f.atr[i]
        if c is None or hi is None or a is None or a <= 0:
            return None
        if c > hi:
            return Setup(side=1, stop_px=mid - stop_atr * a, trail_atr=trail_atr)
        if c < lo:
            return Setup(side=-1, stop_px=mid + stop_atr * a, trail_atr=trail_atr)
        return None
    return Strategy(f"C_donch{span}", "C", {"span_h": span, "stop_atr": stop_atr, "trail_atr": trail_atr,
                                            "lev_fit": "20/15/10/8/5"}, setup)


DRIFT = 0.0175      # headline_watch.PRICE_DRIFT[BRENT], 4h
SHOCK = 0.010       # headline_watch.PRICE_SHOCK[BRENT], 15m; read on a 1h bar here


def alarm_direction(i: int, f: Features) -> int:
    """+1 / -1 when the last closed bar fired the desk's price alarm in that
    direction (a 1% bar, or a 1.75% move against any of the 4 closes before
    it), else 0."""
    mv, dr = f.last_move[i], f.drift4[i]
    if mv is not None and abs(mv) >= SHOCK:
        return 1 if mv > 0 else -1
    if dr is not None and abs(dr) >= DRIFT:
        return 1 if dr > 0 else -1
    return 0


def make_alarm_momentum(target_r: float, buffer: float = 0.75, max_hold: int = 24) -> Strategy:
    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        d = alarm_direction(i, f)
        if d == 0 or f.lo4[i] is None:
            return None
        if d > 0:
            stop = min(f.lo4[i], mid) - buffer
            tp = mid + target_r * (mid - stop)
        else:
            stop = max(f.hi4[i], mid) + buffer
            tp = mid - target_r * (stop - mid)
        return Setup(side=d, stop_px=stop, target_px=tp, max_hold_bars=max_hold)
    return Strategy(f"D_alarm_r{target_r:g}", "D", {"target_r": target_r, "buffer": buffer, "max_hold_h": max_hold,
                                                     "drift": DRIFT, "shock": SHOCK, "lev_fit": "20/15/10/8/5"}, setup)


def make_mean_reversion(stop_atr: float, zone: float = 0.25, min_r: float = 1.0) -> Strategy:
    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        lo, hi, a = f.lo24[i], f.hi24[i], f.atr[i]
        if lo is None or a is None or hi <= lo:
            return None
        pos = (mid - lo) / (hi - lo)
        if 0.0 <= pos <= zone:
            return range_setup(1, mid, lo, hi, stop_atr * a, min_r, WIDE_LEV_FIT)
        if 1.0 - zone <= pos <= 1.0:
            return range_setup(-1, mid, lo, hi, stop_atr * a, min_r, WIDE_LEV_FIT)
        return None
    return Strategy(f"E_mr_atr{stop_atr:g}", "E", {"stop_atr": stop_atr, "zone": zone, "min_r": min_r,
                                                    "lev_fit": "20/15/10/8/5"}, setup)


def make_tsmom(span: int, stop_atr: float = 2.0) -> Strategy:
    def sign(i: int, f: Features) -> int:
        r = f.ret[span][i]
        if r is None:
            return 0
        return 1 if r > 0 else (-1 if r < 0 else 0)

    def setup(i: int, mid: float, f: Features) -> Optional[Setup]:
        s, a = sign(i, f), f.atr[i]
        if s == 0 or a is None or a <= 0:
            return None
        return Setup(side=s, stop_px=mid - s * stop_atr * a)

    def exit_signal(i_next: int, f: Features, side: int) -> bool:
        s = sign(i_next, f)
        return s != 0 and s != side
    return Strategy(f"F_tsmom{span}", "F", {"span_h": span, "stop_atr": stop_atr, "lev_fit": "20/15/10/8/5"},
                    setup, exit_signal)


FLAT = Strategy("G_flat", "G", {}, lambda i, mid, f: None)


def candidates() -> List[Strategy]:
    return [
        make_baseline(1), make_baseline(-1),
        make_trend_filter("ema", 48), make_trend_filter("ema", 120), make_trend_filter("ret", 120),
        make_donchian(24), make_donchian(48), make_donchian(96),
        make_alarm_momentum(2.0), make_alarm_momentum(3.0),
        make_mean_reversion(1.5), make_mean_reversion(2.0),
        make_tsmom(120), make_tsmom(240),
        FLAT,
    ]


BASELINE = "A_long"


def post_hoc_candidates() -> List[Strategy]:
    """Neighbours of the two return-sign candidates, run AFTER the results
    were seen (`--post-hoc`): a robustness read on B_ret120 / F_tsmom120,
    never a candidate. A span that only wins at one value is a fluke."""
    return [make_trend_filter("ret", 72), make_trend_filter("ret", 96), make_trend_filter("ret", 168),
            make_trend_filter("ret", 240), make_tsmom(72), make_tsmom(96), make_tsmom(168)]


# ── engine ────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    strategy: str
    side: int
    entry_t: int
    exit_t: int
    entry_px: float
    exit_px: float
    stop_px: float
    target_px: Optional[float]
    leverage: float
    notional: float
    pnl: float
    frac: float          # pnl / equity at entry
    worst_frac: float    # the worst mark-to-market point / equity at entry (fees in)
    r: float             # pnl / dollars at risk
    reason: str

    def row(self) -> Dict[str, Any]:
        return {"strategy": self.strategy, "side": "long" if self.side > 0 else "short",
                "entry": _iso(self.entry_t), "exit": _iso(self.exit_t), "entry_px": round(self.entry_px, 4),
                "exit_px": round(self.exit_px, 4), "stop_px": round(self.stop_px, 4),
                "target_px": None if self.target_px is None else round(self.target_px, 4),
                "leverage": self.leverage, "notional": round(self.notional, 2), "pnl": round(self.pnl, 2),
                "frac": round(self.frac, 5), "worst_frac": round(self.worst_frac, 5), "r": round(self.r, 2),
                "reason": self.reason}


@dataclass
class _Pos:
    side: int
    size: float
    entry_px: float
    stop_px: float
    target_px: Optional[float]
    trail_atr: Optional[float]
    max_hold: Optional[int]
    leverage: float
    notional: float
    entry_t: int
    equity_before: float
    risk_usd: float
    entry_fee: float
    best_close: float
    worst_unreal: float = 0.0
    bars_held: int = 0


@dataclass
class RunResult:
    strategy: str
    trades: List[Trade]
    final_equity: float
    refused: Dict[str, int] = field(default_factory=dict)
    start_t: int = 0
    end_t: int = 0


def _iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ms / 1000))


def run_strategy(bars: Sequence[Candle], strat: Strategy, costs: Costs, start_i: int = WARMUP_BARS,
                 feats: Optional[Features] = None) -> RunResult:
    """Bar-by-bar replay of `strat` on `bars` from index `start_i`. Pure."""
    f = feats or compute_features(bars)
    equity = costs.start_equity
    pos: Optional[_Pos] = None
    trades: List[Trade] = []
    refused: Dict[str, int] = {}
    n = len(bars)

    def close_at(px: float, t: int, reason: str, market: bool) -> None:
        nonlocal equity, pos
        assert pos is not None
        fill = px * (1.0 - pos.side * costs.slippage) if market else px
        gross = pos.side * (fill - pos.entry_px) * pos.size
        fee = fill * pos.size * costs.taker_fee
        pnl = gross - pos.entry_fee - fee
        equity += pnl
        worst = min(pos.worst_unreal, gross - pos.entry_fee - fee)
        trades.append(Trade(strat.name, pos.side, pos.entry_t, t, pos.entry_px, fill, pos.stop_px, pos.target_px,
                            pos.leverage, pos.notional, pnl, pnl / pos.equity_before, worst / pos.equity_before,
                            pnl / pos.risk_usd if pos.risk_usd else 0.0, reason))
        pos = None

    for i in range(start_i, n):
        b = bars[i]
        t = b.t
        closed_t = t + HOUR_MS
        if weekend_closed(t):
            if pos is not None:
                close_at(b.o, t, "weekend", True)
            continue
        if pos is None and equity >= costs.min_notional:
            s = strat.setup(i, b.o, f)
            if s is not None:
                lev = fit_leverage(b.o, s.stop_px, costs, s.lev_fit)
                if lev is None:
                    refused["leverage"] = refused.get("leverage", 0) + 1
                else:
                    fill = b.o * (1.0 + s.side * costs.slippage)
                    size, notional = size_for(equity, fill, s.stop_px, lev, costs)
                    if size <= 0:
                        refused["size"] = refused.get("size", 0) + 1
                    else:
                        fee = notional * costs.taker_fee
                        risk = abs(fill - s.stop_px) * size + notional * (2.0 * costs.taker_fee + costs.slippage)
                        pos = _Pos(s.side, size, fill, s.stop_px, s.target_px, s.trail_atr, s.max_hold_bars, lev,
                                   notional, t, equity, risk, fee, best_close=fill)
        if pos is None:
            continue
        # intrabar exits on this bar: stop first (the conservative read)
        if pos.side > 0:
            hit_stop, hit_tgt = b.l <= pos.stop_px, pos.target_px is not None and b.h >= pos.target_px
            stop_fill = min(b.o, pos.stop_px)
        else:
            hit_stop, hit_tgt = b.h >= pos.stop_px, pos.target_px is not None and b.l <= pos.target_px
            stop_fill = max(b.o, pos.stop_px)
        adverse = b.l if pos.side > 0 else b.h
        pos.worst_unreal = min(pos.worst_unreal, pos.side * (adverse - pos.entry_px) * pos.size - pos.entry_fee)
        if hit_stop:
            close_at(stop_fill, closed_t, "stop", True)
            continue
        if hit_tgt:
            close_at(float(pos.target_px), closed_t, "target", False)
            continue
        pos.bars_held += 1
        if pos.max_hold is not None and pos.bars_held >= pos.max_hold:
            close_at(b.c, closed_t, "time", True)
            continue
        if strat.exit_signal is not None and strat.exit_signal(i + 1, f, pos.side):
            close_at(b.c, closed_t, "flip", True)
            continue
        if pos.trail_atr is not None:
            pos.best_close = max(pos.best_close, b.c) if pos.side > 0 else min(pos.best_close, b.c)
            a = f.atr[i + 1]
            if a:
                new = pos.best_close - pos.side * pos.trail_atr * a
                pos.stop_px = max(pos.stop_px, new) if pos.side > 0 else min(pos.stop_px, new)
    if pos is not None:
        close_at(bars[-1].c, bars[-1].t + HOUR_MS, "end", True)
    return RunResult(strat.name, trades, equity, refused, bars[start_i].t if n > start_i else 0,
                     bars[-1].t + HOUR_MS if n else 0)


# ── metrics ───────────────────────────────────────────────────────────────────

def equity_events(trades: Sequence[Trade], start_equity: float) -> List[Tuple[int, float]]:
    """(time, equity) points: the trough inside each trade, then its exit.
    Compounds `frac` so the same trade list can be replayed from any
    starting equity (the selector and the equal split do)."""
    eq = start_equity
    out: List[Tuple[int, float]] = [(0, eq)]
    for tr in sorted(trades, key=lambda x: (x.exit_t, x.entry_t)):
        out.append((tr.exit_t - 1, eq * (1.0 + tr.worst_frac)))
        eq *= 1.0 + tr.frac
        out.append((tr.exit_t, eq))
    return out


def max_drawdown(points: Sequence[Tuple[int, float]]) -> float:
    peak, dd = -math.inf, 0.0
    for _, e in points:
        peak = max(peak, e)
        if peak > 0:
            dd = max(dd, (peak - e) / peak)
    return dd


def thirds(trades: Sequence[Trade], start_t: int, end_t: int, start_equity: float) -> List[float]:
    """Net $ by exit time in three equal time slices of [start_t, end_t),
    compounding from `start_equity`."""
    cut = [start_t + (end_t - start_t) * k / 3 for k in range(4)]
    eq = start_equity
    out = [0.0, 0.0, 0.0]
    for tr in sorted(trades, key=lambda x: (x.exit_t, x.entry_t)):
        k = min(2, max(0, next((j for j in range(3) if tr.exit_t < cut[j + 1]), 2)))
        pnl = eq * tr.frac
        out[k] += pnl
        eq += pnl
    return [round(x, 2) for x in out]


def summarize(name: str, trades: Sequence[Trade], start_t: int, end_t: int, start_equity: float,
              refused: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    pts = equity_events(trades, start_equity)
    final = pts[-1][1]
    wins = sum(1 for t in trades if t.pnl > 0)
    return {"name": name, "trades": len(trades), "wins": wins,
            "win_rate": round(wins / len(trades), 3) if trades else None,
            "net": round(final - start_equity, 2), "final_equity": round(final, 2),
            "max_dd": round(max_drawdown(pts), 4), "thirds": thirds(trades, start_t, end_t, start_equity),
            "avg_r": round(sum(t.r for t in trades) / len(trades), 3) if trades else None,
            "longs": sum(1 for t in trades if t.side > 0), "shorts": sum(1 for t in trades if t.side < 0),
            "exits": _count(t.reason for t in trades), "refused": dict(refused or {})}


def _count(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def passes(row: Dict[str, Any], baseline_net: float, wti_net: Optional[float],
           max_dd: float = PASS_MAX_DD, min_trades: int = PASS_MIN_TRADES) -> Dict[str, Any]:
    """The pass bar stated in the module docstring. `wti_net` None = no WTI
    run (fails that leg)."""
    checks = {
        "net_gt_baseline": row["net"] > baseline_net,
        "net_positive": row["net"] > 0,
        "thirds_2_of_3": sum(1 for x in row["thirds"] if x > 0) >= 2,
        "max_dd_under": row["max_dd"] < max_dd,
        "min_trades": row["trades"] >= min_trades,
        "wti_not_negative": wti_net is not None and wti_net >= 0,
    }
    return {"pass": all(checks.values()), "checks": checks}


# ── round robin ───────────────────────────────────────────────────────────────

def trailing_score(trades: Sequence[Trade], t0: int, t1: int) -> float:
    """Compounded return of the trades that EXITED in [t0, t1)."""
    g = 1.0
    for tr in trades:
        if t0 <= tr.exit_t < t1:
            g *= 1.0 + tr.frac
    return g - 1.0


def walk_forward(runs: Dict[str, Sequence[Trade]], start_t: int, end_t: int, rebalance_days: int,
                 lookback_days: int, start_equity: float, names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """At every rebalance from `start_t + lookback` on, pick the candidate
    with the best trailing score over the previous `lookback_days` (ties:
    the first in `names` order; a best score <= 0 picks flat) and follow
    its entries until the next rebalance. A trade open across a rebalance
    runs to its own exit; the next pick's entries wait until flat. The
    first lookback window is flat (no history yet). Pure."""
    names = list(names or runs.keys())
    reb, look = rebalance_days * DAY_MS, lookback_days * DAY_MS
    picks: List[Dict[str, Any]] = []
    taken: List[Trade] = []
    busy_until = 0
    w0 = start_t + look
    while w0 < end_t:
        w1 = min(w0 + reb, end_t)
        scores = {nm: trailing_score(runs[nm], w0 - look, w0) for nm in names}
        best = max(names, key=lambda nm: scores[nm])
        pick = best if scores[best] > 0 else "G_flat"
        picks.append({"from": _iso(w0), "to": _iso(w1), "pick": pick, "score": round(scores[best], 4)})
        if pick in runs:
            for tr in sorted(runs[pick], key=lambda x: x.entry_t):
                if w0 <= tr.entry_t < w1 and tr.entry_t >= busy_until:
                    taken.append(replace(tr, strategy=f"{pick}"))
                    busy_until = tr.exit_t
        w0 = w1
    row = summarize(f"RR_{rebalance_days}d_look{lookback_days}d", taken, start_t, end_t, start_equity)
    row["picks"] = picks
    row["pick_counts"] = _count(p["pick"] for p in picks)
    row["trade_rows"] = [t.row() for t in taken]
    return row


def equal_split(runs: Dict[str, Sequence[Trade]], start_t: int, end_t: int, start_equity: float,
                names: Sequence[str]) -> Dict[str, Any]:
    """Every candidate on an equal slice of equity, compounding on its own."""
    k = len(names)
    slice_eq = start_equity / k
    curves = {nm: equity_events(runs[nm], slice_eq) for nm in names}
    times = sorted({t for pts in curves.values() for t, _ in pts})
    total: List[Tuple[int, float]] = []
    idx = {nm: 0 for nm in names}
    for t in times:
        s = 0.0
        for nm in names:
            pts = curves[nm]
            while idx[nm] + 1 < len(pts) and pts[idx[nm] + 1][0] <= t:
                idx[nm] += 1
            s += pts[idx[nm]][1]
        total.append((t, s))
    final = total[-1][1] if total else start_equity
    all_trades = [tr for nm in names for tr in runs[nm]]
    cut = [start_t + (end_t - start_t) * j / 3 for j in range(4)]
    th = [0.0, 0.0, 0.0]
    for nm in names:
        for a, b in zip(thirds(runs[nm], start_t, end_t, slice_eq), range(3)):
            th[b] += a
    _ = cut
    return {"name": "EQUAL_SPLIT", "trades": len(all_trades),
            "wins": sum(1 for t in all_trades if t.pnl > 0), "net": round(final - start_equity, 2),
            "final_equity": round(final, 2), "max_dd": round(max_drawdown(total), 4),
            "thirds": [round(x, 2) for x in th], "slices": k}


# ── data ──────────────────────────────────────────────────────────────────────

def load_bars(coin: str, cache_dir: str, fetch_page=None) -> List[Candle]:
    from services.strategies.backtest.data import fetch_candles_paged_cached
    bars = fetch_candles_paged_cached(coin, "1h", FETCH_START_MS, FETCH_END_MS, cache_dir, fetch_page)
    return [b for b in bars if b.t + HOUR_MS <= DATA_END_MS]


# ── the tournament ────────────────────────────────────────────────────────────

REBALANCE_DAYS = (7, 3)
LOOKBACK_DAYS = (7, 14)


def tournament(brent: Sequence[Candle], wti: Sequence[Candle], costs: Costs = Costs(),
               strategies: Optional[Sequence[Strategy]] = None, start_i: int = WARMUP_BARS) -> Dict[str, Any]:
    strategies = list(strategies or candidates())
    fb, fw = compute_features(brent), compute_features(wti) if wti else None
    runs_b: Dict[str, RunResult] = {s.name: run_strategy(brent, s, costs, start_i, fb) for s in strategies}
    runs_w: Dict[str, RunResult] = ({s.name: run_strategy(wti, s, costs, start_i, fw) for s in strategies}
                                    if wti else {})
    any_run = next(iter(runs_b.values()))
    start_t, end_t = any_run.start_t, any_run.end_t
    base_net = summarize(BASELINE, runs_b[BASELINE].trades, start_t, end_t, costs.start_equity)["net"]
    rows = []
    for s in strategies:
        r = summarize(s.name, runs_b[s.name].trades, start_t, end_t, costs.start_equity, runs_b[s.name].refused)
        r["family"] = s.family
        r["params"] = s.params
        if s.name in runs_w:
            w = runs_w[s.name]
            r["wti"] = summarize(s.name, w.trades, w.start_t, w.end_t, costs.start_equity, w.refused)
        wti_net = r["wti"]["net"] if "wti" in r else None
        r.update(passes(r, base_net, wti_net))
        rows.append(r)
    rows.sort(key=lambda r: -r["net"])
    names = [s.name for s in strategies if s.name != "G_flat"]
    tb = {nm: runs_b[nm].trades for nm in runs_b}
    tw = {nm: runs_w[nm].trades for nm in runs_w}
    selectors = []
    for reb in REBALANCE_DAYS:
        for look in LOOKBACK_DAYS:
            sel = walk_forward(tb, start_t, end_t, reb, look, costs.start_equity, names)
            if tw:
                wsel = walk_forward(tw, runs_w[BASELINE].start_t, runs_w[BASELINE].end_t, reb, look,
                                    costs.start_equity, names)
                sel["wti"] = {k: wsel[k] for k in ("trades", "wins", "net", "max_dd", "thirds", "pick_counts")}
            sel.update(passes(sel, base_net, sel["wti"]["net"] if "wti" in sel else None))
            selectors.append(sel)
    eq = equal_split(tb, start_t, end_t, costs.start_equity, names)
    if tw:
        eq["wti"] = equal_split(tw, runs_w[BASELINE].start_t, runs_w[BASELINE].end_t, costs.start_equity, names)
    eq.update(passes(eq, base_net, eq["wti"]["net"] if "wti" in eq else None))
    hind = max((r for r in rows if r["name"] != "G_flat"), key=lambda r: r["net"])
    return {
        "coin": BRENT, "wti": WTI if wti else None, "bars": len(brent), "wti_bars": len(wti),
        "window": {"from": _iso(start_t), "to": _iso(end_t), "days": round((end_t - start_t) / DAY_MS, 1)},
        "costs": costs.__dict__, "baseline": BASELINE, "baseline_net": base_net,
        "pass_bar": {"net_gt_baseline_and_positive": True, "thirds_positive_min": 2, "max_dd_under": PASS_MAX_DD,
                     "min_trades": PASS_MIN_TRADES, "wti_net_min": 0.0},
        "candidates": rows, "selectors": selectors, "equal_split": eq,
        "hindsight_best": {"name": hind["name"], "net": hind["net"], "label": "hindsight, not tradeable"},
        "any_pass": any(r["pass"] for r in rows) or any(s["pass"] for s in selectors) or eq["pass"],
        "trades": {nm: [t.row() for t in rr.trades] for nm, rr in runs_b.items()},
    }


def markdown_tables(out: Dict[str, Any]) -> str:
    lines = ["| Candidate | Trades | Wins | Net $ | Max DD | Thirds $ | WTI net $ (n) | Pass |", "|---|---|---|---|---|---|---|---|"]
    for r in out["candidates"]:
        w = r.get("wti") or {}
        lines.append(f"| {r['name']} | {r['trades']} | {r['wins']} | {r['net']:+.2f} | {r['max_dd']:.1%} | "
                     f"{' / '.join(f'{x:+.0f}' for x in r['thirds'])} | "
                     f"{w.get('net', float('nan')):+.2f} ({w.get('trades', 0)}) | {'PASS' if r['pass'] else 'fail'} |")
    lines += ["", "| Selector | Trades | Wins | Net $ | Max DD | Thirds $ | WTI net $ | Picks | Pass |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in out["selectors"]:
        w = s.get("wti") or {}
        pk = ", ".join(f"{k} x{v}" for k, v in s["pick_counts"].items())
        lines.append(f"| {s['name']} | {s['trades']} | {s['wins']} | {s['net']:+.2f} | {s['max_dd']:.1%} | "
                     f"{' / '.join(f'{x:+.0f}' for x in s['thirds'])} | {w.get('net', float('nan')):+.2f} | {pk} | "
                     f"{'PASS' if s['pass'] else 'fail'} |")
    e = out["equal_split"]
    w = e.get("wti") or {}
    lines.append(f"| EQUAL_SPLIT ({e['slices']} slices) | {e['trades']} | {e['wins']} | {e['net']:+.2f} | "
                 f"{e['max_dd']:.1%} | {' / '.join(f'{x:+.0f}' for x in e['thirds'])} | "
                 f"{w.get('net', float('nan')):+.2f} | all | {'PASS' if e['pass'] else 'fail'} |")
    h = out["hindsight_best"]
    lines.append(f"| HINDSIGHT best single ({h['name']}) | | | {h['net']:+.2f} | | | | {h['label']} | n/a |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", required=True, help="candle cache directory (fetched once, then reused)")
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--no-wti", action="store_true")
    ap.add_argument("--risk", type=float, default=Costs.risk_frac,
                    help="equity at risk per trade (0.06 is the bar; a smaller value is a sensitivity run)")
    ap.add_argument("--post-hoc", action="store_true",
                    help="also run the post-hoc neighbour spans (reported under post_hoc, never ranked)")
    a = ap.parse_args(argv)
    brent = load_bars(BRENT, a.cache_dir)
    wti = [] if a.no_wti else load_bars(WTI, a.cache_dir)
    costs = Costs(risk_frac=a.risk)
    out = tournament(brent, wti, costs)
    if a.post_hoc:
        ph = tournament(brent, wti, costs, strategies=[make_baseline(1)] + post_hoc_candidates() + [FLAT])
        out["post_hoc"] = [{k: r[k] for k in ("name", "trades", "wins", "net", "max_dd", "thirds", "wti", "pass")}
                           for r in ph["candidates"] if r["name"] not in (BASELINE, "G_flat")]
        for r in out["post_hoc"]:
            r["wti"] = {"net": r["wti"]["net"], "trades": r["wti"]["trades"]} if r.get("wti") else None
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1)
    slim = {k: v for k, v in out.items() if k != "trades"}
    for r in slim["candidates"]:
        r.pop("exits", None)
    for s in slim["selectors"]:
        s.pop("trade_rows", None)
        s.pop("picks", None)
    print(json.dumps(slim, indent=1))
    print(markdown_tables(out), file=sys.stderr)
    return 0 if out["any_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
