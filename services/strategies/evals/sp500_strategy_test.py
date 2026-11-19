"""S&P 500 desk strategy test, 2026-11-19 (docs/strategy/SP500_BACKTEST_2026-11-19.md).

Operator, 2026-11-19: "ADD A S&P500 MARKET STRATEGY 50X 5% LONG OR SHORT".
Before xyz:SP500 gets a desk, three candidate rules run on its own public
candles, long and short, and the leverage question is answered with the
venue's own margin math: can a stop that sits inside half the distance to
liquidation at 50x (0.505% of price) survive the S&P's normal noise?

Rules (families):
  edge24    the oil desk's rule moved over: market entry off the 24h range,
            stop beyond the 24h edge, target the far 24h edge, 2R gate, no
            entry after a break. The gates are the LIVE ones: every entry
            is `services.strategies.next_trade.next_trade` itself.
  pull8     trend filter plus pullback: side = sign of the 120h (or 24h)
            return, entry off the 8h edge (stop beyond it), target the far
            24h edge. Same live gates.
  orb       session-open range: the first 30 minutes of the US cash session
            (09:30 New York), first 15m close outside it, stop at the far
            side or the middle, fixed R target, flat by 15:45 New York.
            Needs 15m bars, so it runs on the 15m dataset only.

There is no journal of S&P theses yet, so the side comes from the rule:
`both` (long near the 24h low, short near the 24h high), `trend120` /
`trend24` (only with the sign of that many hours' return), `long`, `short`.

Datasets (one pinned snapshot, `services.strategies.backtest.snapshot`):
  1h    the venue's whole 1h history (about 208 days), hourly ticks.
  15m   the venue's whole 15m history (about 52 days), 15-minute ticks.
Each is split 70/30 by time: a variant is picked on the first 70% and its
row on the last 30% (the held-out tail) is reported beside it.

Per row: trades, trades a week, win rate, mean R, net R, max drawdown in R,
long and short split, the share of trades whose stop fits 50x. R is net of
fees, stop and entry slippage and funding. Costs: 0.9 bps a side (what the
desk pays on xyz HIP-3 growth-mode fills, measured on its own Brent ledger)
and 1 bp slippage; the stress row uses 4.5 bps and 3 bps.

Verdict (written before the first run), per family, on its picked variant:
  EDGE     30 or more trades on the 1h dataset (5 or more in its tail),
           net R above zero on the whole sample AND the held-out tail of
           BOTH datasets, above zero under the cost stress, above zero
           without its two best trades, and at least half of its grid
           neighbours above zero.
  LEANING  net R above zero on the whole 1h sample and its held-out tail
           (5 or more tail trades), above zero without its two best trades,
           and half its neighbours above zero; a 15m or cost-stress leg
           fails. (The tail-trades leg and the two fluke checks were added
           to LEANING after the first run called a 2-trade tail LEANING.)
  NOISE    anything else.
The opening-range family has no 1h dataset: its 15m rows are judged in the
1h slots, so it can reach LEANING at most.

    python -m services.strategies.backtest.snapshot --coin xyz:SP500 --days-back 400 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-sp500
    python -m services.strategies.evals.sp500_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261119-sp500

Writes /tmp/sp500-strategy/report.json and summary.csv. Read only: no
account, no live config, no pathiel MCP tool, no LLM call. Exit code 0
when the live rule's verdict is EDGE or LEANING, 1 on NOISE.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import check_leverage, liq_estimate, max_leverage_for_stop
from pathiel.models.types import Candle
from services.strategies import next_trade as NT
from services.strategies.weekend import NEW_YORK, weekend_closed

COIN = "xyz:SP500"
VENUE_MAX_LEVERAGE = 50.0        # metaAndAssetCtxs dex=xyz, 2026-11-19: one margin tier, maxLeverage 50
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
STEP_MS = {"15m": 900_000, "1h": HOUR_MS}
HOLDOUT_FRAC = 0.30              # the last 30% of each dataset, by time
MIN_TRADES_EDGE = 30
MIN_TRADES_PICK = 15             # a variant needs this many train trades to be picked
MIN_TRADES_TAIL = 5              # a held-out tail under this many trades proves nothing
LEVERAGES: Tuple[float, ...] = (50.0, 40.0, 30.0, 25.0, 20.0)
OPEN_NY_MIN = 9 * 60 + 30        # US cash open, New York minutes of day
ORB_RANGE_MIN = 30
ORB_LAST_ENTRY_NY_MIN = 15 * 60
ORB_FLAT_NY_MIN = 15 * 60 + 45


@dataclass(frozen=True)
class Costs:
    fee: float = 0.00009         # per side, fraction of notional
    slip: float = 0.0001         # market entry and stop exit, fraction of price


MEASURED = Costs()
STRESS = Costs(fee=0.00045, slip=0.0003)


@dataclass(frozen=True)
class Tick:
    """What one decision reads: the bar's close as the mid, and the levels
    off CLOSED 1h candles at that moment (the levels script's own inputs)."""
    t: int                       # decision time: the tick bar's close, ms
    mid: float
    low8: float
    high8: float
    low24: float
    high24: float
    atr: float                   # ATR14 on closed 1h bars (levels.atr)
    ret24: Optional[float]
    ret120: Optional[float]
    weekend: bool
    ny_min: int                  # New York minutes of day at t
    ny_day: int                  # New York date ordinal at t
    ny_wd: int                   # New York weekday at t (Mon 0)


@dataclass(frozen=True)
class Order:
    side: str
    stop: float
    tp: Optional[float]
    flat_at_ms: Optional[int] = None   # close at market at the first bar close at or after this


@dataclass
class Trade:
    side: str
    entry_t: int
    entry_px: float
    stop: float
    tp: Optional[float]
    exit_t: int = 0
    exit_px: float = 0.0
    reason: str = ""
    r: float = 0.0               # net of fees, slippage and funding
    stop_frac: float = 0.0       # planned stop distance / entry
    max_lev_ok: int = 0          # highest leverage whose half-liquidation line holds this stop


# ── levels per tick ──────────────────────────────────────────────────────

def _ny(t_ms: int) -> Tuple[int, int, int]:
    lt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(NEW_YORK)
    return lt.hour * 60 + lt.minute, lt.date().toordinal(), lt.weekday()


def build_ticks(bars: Sequence[Candle], step_ms: int, c1h: Sequence[Candle]) -> List[Optional[Tick]]:
    """One Tick per bar of `bars` (the decision is at that bar's close), or
    None while fewer than 25 closed 1h bars exist. Ranges and ATR read only
    1h bars whose close is at or before the decision time. Pure."""
    closes_t = [c.t + HOUR_MS for c in c1h]
    out: List[Optional[Tick]] = []
    for b in bars:
        t = b.t + step_ms
        k = bisect.bisect_right(closes_t, t)
        if k < 25:
            out.append(None)
            continue
        w24, w8, w15 = c1h[k - 24:k], c1h[k - 8:k], c1h[k - 15:k]
        trs = [max(w15[i].h - w15[i].l, abs(w15[i].h - w15[i - 1].c), abs(w15[i].l - w15[i - 1].c))
               for i in range(1, 15)]
        last = c1h[k - 1].c
        ny_min, ny_day, ny_wd = _ny(t)
        out.append(Tick(
            t=t, mid=b.c, low8=min(c.l for c in w8), high8=max(c.h for c in w8),
            low24=min(c.l for c in w24), high24=max(c.h for c in w24), atr=sum(trs) / 14.0,
            ret24=(last / c1h[k - 25].c - 1.0) if k >= 25 else None,
            ret120=(last / c1h[k - 121].c - 1.0) if k >= 121 else None,
            weekend=weekend_closed(t), ny_min=ny_min, ny_day=ny_day, ny_wd=ny_wd))
    return out


# ── rules ────────────────────────────────────────────────────────────────

# rule(i, ticks, bars, state, flat): called on EVERY tick so a stateful rule (the opening range) keeps
# building while a trade is on; it may only answer an Order when `flat`.
Rule = Callable[[int, Sequence[Optional[Tick]], Sequence[Candle], Dict[str, Any], bool], Optional[Order]]


def sides_for(mode: str, tk: Tick) -> Tuple[str, ...]:
    """The sides a tick may trade under side mode `mode`. A trend mode with
    no return yet, or a flat one, trades nothing. Pure."""
    if mode == "both":
        return ("long", "short")
    if mode in ("long", "short"):
        return (mode,)
    ret = tk.ret120 if mode == "trend120" else tk.ret24 if mode == "trend24" else None
    if ret is None or ret == 0:
        return ()
    return ("long",) if ret > 0 else ("short",)


def edge_rule(*, k_atr: float, floor_frac: float, offset_frac: float, leverage: float,
              sides: str = "both", entry_lookback_h: int = 24, min_r: float = 2.0,
              fit: Sequence[float] = (), max_leverage: float = VENUE_MAX_LEVERAGE) -> Rule:
    """The 24h-range rule through the live `next_trade.next_trade`: stop
    max(k_atr x ATR14, floor_frac x mid) beyond the entry edge, target the
    far 24h edge less `offset_frac`, `min_r`, the half-liquidation line at
    `leverage` (or the best of `fit`), no entry after a break.
    `entry_lookback_h` 8 puts the stop beyond the 8h edge instead (pull8)."""
    def rule(i: int, ticks: Sequence[Optional[Tick]], bars: Sequence[Candle], st: Dict[str, Any],
             flat: bool = True) -> Optional[Order]:
        if not flat:
            return None
        tk = ticks[i]
        buf = max(k_atr * tk.atr, floor_frac * tk.mid)
        for side in sides_for(sides, tk):
            lo = tk.low8 if (entry_lookback_h == 8 and side == "long") else tk.low24
            hi = tk.high8 if (entry_lookback_h == 8 and side == "short") else tk.high24
            nt = NT.next_trade(side, tk.mid, lo, hi, thesis_entry=None, leverage=leverage,
                               coin_max_leverage=max_leverage, stop_buffer=buf, min_r=min_r,
                               offset_frac=offset_frac, leverage_fit=fit)
            if nt.get("ok"):
                return Order(side=side, stop=float(nt["stop_px"]), tp=float(nt["tp_px"]))
        return None
    return rule


def orb_rule(*, stop_mode: str, target_r: float, leverage: float,
             max_leverage: float = VENUE_MAX_LEVERAGE) -> Rule:
    """Opening-range breakout on the US cash session. Range: the bars whose
    close falls in (09:30, 10:00] New York. Entry: the first later bar that
    closes outside it before 15:00, one trade a day, weekdays. Stop: the far
    side of the range (`far`) or its middle (`mid`). Target: `target_r` x
    the risk. Flat at 15:45. A stop too far for `leverage` is skipped."""
    def rule(i: int, ticks: Sequence[Optional[Tick]], bars: Sequence[Candle], st: Dict[str, Any],
             flat: bool = True) -> Optional[Order]:
        tk = ticks[i]
        if tk.ny_wd > 4:
            return None
        day = st.setdefault("orb", {}).setdefault(tk.ny_day, {"hi": None, "lo": None, "done": False})
        if OPEN_NY_MIN < tk.ny_min <= OPEN_NY_MIN + ORB_RANGE_MIN:
            day["hi"] = bars[i].h if day["hi"] is None else max(day["hi"], bars[i].h)
            day["lo"] = bars[i].l if day["lo"] is None else min(day["lo"], bars[i].l)
            return None
        if not flat or day["done"] or day["hi"] is None or not (OPEN_NY_MIN + ORB_RANGE_MIN < tk.ny_min <= ORB_LAST_ENTRY_NY_MIN):
            return None
        hi, lo = day["hi"], day["lo"]
        side = "long" if tk.mid > hi else "short" if tk.mid < lo else None
        if side is None:
            return None
        day["done"] = True
        mid_rng = (hi + lo) / 2.0
        stop = (lo if stop_mode == "far" else mid_rng) if side == "long" else (hi if stop_mode == "far" else mid_rng)
        risk = abs(tk.mid - stop)
        if risk <= 0 or not check_leverage(tk.mid, stop, leverage, max_leverage).get("ok"):
            return None
        tp = tk.mid + target_r * risk if side == "long" else tk.mid - target_r * risk
        return Order(side=side, stop=stop, tp=tp, flat_at_ms=tk.t + (ORB_FLAT_NY_MIN - tk.ny_min) * 60_000)
    return rule


# ── the simulator ────────────────────────────────────────────────────────

def funding_index(rows: Sequence[Dict[str, Any]]) -> Tuple[List[int], List[float]]:
    """(times, running sum of the hourly funding rate), oldest first. Pure."""
    times: List[int] = []
    cum: List[float] = []
    total = 0.0
    for r in sorted(rows, key=lambda r: int(r["time"])):
        total += float(r["fundingRate"])
        times.append(int(r["time"]))
        cum.append(total)
    return times, cum


def funding_between(fund: Tuple[List[int], List[float]], t0: int, t1: int) -> float:
    """Sum of the funding rates paid in (t0, t1]: what a long pays. Pure."""
    times, cum = fund
    a, b = bisect.bisect_right(times, t0), bisect.bisect_right(times, t1)
    return (cum[b - 1] if b else 0.0) - (cum[a - 1] if a else 0.0)


def in_windows(windows: Sequence[Tuple[int, int]], t: int) -> bool:
    """True when `t` sits in one of the sorted [start, end) windows. Pure."""
    i = bisect.bisect_right([w[0] for w in windows], t) - 1
    return i >= 0 and windows[i][0] <= t < windows[i][1]


def close_trade(tr: Trade, exit_t: int, raw_exit_px: float, reason: str, costs: Costs,
                fund: Tuple[List[int], List[float]]) -> Trade:
    """Fill the exit and score the trade in R, net. The entry was a market
    order (slippage against us); a stop or a timed close is a market order
    too; a target is a resting limit (no slippage). Every leg pays the fee.
    Funding: a long pays a positive rate. R is judged against the planned
    risk, mid to stop. Pure."""
    d = 1.0 if tr.side == "long" else -1.0
    slip = 0.0 if reason == "target" else costs.slip
    exit_px = raw_exit_px * (1.0 - d * slip)
    entry_fill = tr.entry_px * (1.0 + d * costs.slip)
    risk = abs(tr.entry_px - tr.stop)
    gross = d * (exit_px - entry_fill)
    fees = costs.fee * (entry_fill + exit_px)
    funding = d * funding_between(fund, tr.entry_t, exit_t) * tr.entry_px
    tr.exit_t, tr.exit_px, tr.reason = exit_t, exit_px, reason
    tr.r = (gross - fees - funding) / risk
    return tr


def simulate(ticks: Sequence[Optional[Tick]], bars: Sequence[Candle], step_ms: int, rule: Rule,
             costs: Costs = MEASURED, fund: Tuple[List[int], List[float]] = ([], []), *,
             weekend: str = "trade", blackouts: Sequence[Tuple[int, int]] = ()) -> List[Trade]:
    """One position at a time. A decision at bar i's close; the path starts
    at bar i+1. Within a bar the stop is checked before the target (the
    worse order), a bar that opens through the stop fills at its open, and a
    target needs a trade through it. `weekend`: "trade", "no_entry" (no new
    entry in the futures weekend) or "flat" (and close at its open).
    `blackouts`: sorted [start, end) windows with no new entry. Pure."""
    trades: List[Trade] = []
    st: Dict[str, Any] = {}
    pos: Optional[Trade] = None
    order: Optional[Order] = None
    for i, tk in enumerate(ticks):
        if tk is None:
            continue
        bar = bars[i]
        if pos is not None:
            long = pos.side == "long"
            hit_stop = bar.l <= pos.stop if long else bar.h >= pos.stop
            hit_tp = pos.tp is not None and (bar.h > pos.tp if long else bar.l < pos.tp)
            if hit_stop:
                px = min(pos.stop, bar.o) if long else max(pos.stop, bar.o)
                trades.append(close_trade(pos, tk.t, px, "stop", costs, fund))
                pos = None
            elif hit_tp:
                trades.append(close_trade(pos, tk.t, pos.tp, "target", costs, fund))
                pos = None
            elif order.flat_at_ms is not None and tk.t >= order.flat_at_ms:
                trades.append(close_trade(pos, tk.t, bar.c, "time", costs, fund))
                pos = None
            elif weekend == "flat" and tk.weekend:
                trades.append(close_trade(pos, tk.t, bar.c, "weekend", costs, fund))
                pos = None
        new = rule(i, ticks, bars, st, pos is None)
        if new is None or pos is not None or i == len(ticks) - 1:
            continue
        if (weekend != "trade" and tk.weekend) or in_windows(blackouts, tk.t):
            continue
        order = new
        risk_frac = abs(tk.mid - new.stop) / tk.mid
        pos = Trade(side=new.side, entry_t=tk.t, entry_px=tk.mid, stop=new.stop, tp=new.tp, stop_frac=risk_frac,
                    max_lev_ok=max_leverage_for_stop(tk.mid, new.stop, VENUE_MAX_LEVERAGE))
    return trades


# ── scoring ──────────────────────────────────────────────────────────────

def max_drawdown_r(rs: Sequence[float]) -> float:
    """Largest peak-to-trough fall of the running sum of R. Pure."""
    peak = total = dd = 0.0
    for r in rs:
        total += r
        peak = max(peak, total)
        dd = max(dd, peak - total)
    return dd


def summarize(trades: Sequence[Trade], start_ms: int, end_ms: int) -> Dict[str, Any]:
    """The row a variant gets over [start_ms, end_ms), by entry time. Pure."""
    ts = sorted((t for t in trades if start_ms <= t.entry_t < end_ms), key=lambda t: t.exit_t)
    weeks = max((end_ms - start_ms) / (7.0 * DAY_MS), 1e-9)
    rs = [t.r for t in ts]
    n = len(ts)
    longs = [t.r for t in ts if t.side == "long"]
    shorts = [t.r for t in ts if t.side == "short"]
    stops = sorted(t.stop_frac for t in ts)
    return {
        "trades": n, "per_week": round(n / weeks, 2),
        "win_rate": round(sum(1 for r in rs if r > 0) / n, 3) if n else None,
        "mean_r": round(sum(rs) / n, 3) if n else None,
        "net_r": round(sum(rs), 2), "max_dd_r": round(max_drawdown_r(rs), 2),
        "net_r_ex_best2": round(sum(sorted(rs)[:-2]), 2) if n > 2 else round(min(sum(rs), 0.0), 2),
        "long": {"trades": len(longs), "net_r": round(sum(longs), 2)},
        "short": {"trades": len(shorts), "net_r": round(sum(shorts), 2)},
        "median_stop_pct": round(stops[n // 2] * 100, 3) if n else None,
        "fits_50x": round(sum(1 for t in ts if t.max_lev_ok >= 50) / n, 3) if n else None,
        "exits": {k: sum(1 for t in ts if t.reason == k) for k in ("stop", "target", "time", "weekend")},
    }


def split_ms(start_ms: int, end_ms: int, holdout_frac: float = HOLDOUT_FRAC) -> int:
    """Where the held-out tail starts. Pure."""
    return int(end_ms - (end_ms - start_ms) * holdout_frac)


def equity_path(trades: Sequence[Trade], leverage: float, risk_pct: float) -> Dict[str, Any]:
    """The desk slice's own curve: each trade risks `risk_pct` of the slice
    at its stop, or less when full size at `leverage` cannot carry that much
    (the live sizing: services.risk.size.size_trade). Compounded. Pure."""
    eq = peak = 1.0
    dd = 0.0
    for t in sorted(trades, key=lambda t: t.exit_t):
        risk = min(risk_pct / 100.0, t.stop_frac * leverage)
        eq *= max(0.0, 1.0 + t.r * risk)
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak if peak else 0.0)
    return {"final_multiple": round(eq, 3), "max_dd_pct": round(dd * 100, 1)}


# ── the 50x question ─────────────────────────────────────────────────────

def leverage_budget(leverage: float, max_leverage: float = VENUE_MAX_LEVERAGE, px: float = 100.0) -> Dict[str, float]:
    """Distance to liquidation and the widest stop the desk allows (half of
    it) at `leverage`, as a percent of price, from the venue's maintenance
    margin (1 / (2 x max leverage)). The long side; the short is within a
    few hundredths of a percent. Pure."""
    liq = liq_estimate(px, leverage, max_leverage, True)
    dist = (px - liq) / px * 100.0
    return {"leverage": leverage, "liq_dist_pct": round(dist, 3), "max_stop_pct": round(dist / 2.0, 3)}


def noise_table(c1h: Sequence[Candle], c15: Sequence[Candle]) -> Dict[str, Any]:
    """ATR14 and the 24h range as a percent of price, at their quantiles."""
    def atr_pct(bars: Sequence[Candle]) -> List[float]:
        tr = [max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)) / p.c * 100.0 for p, b in zip(bars, bars[1:])]
        return [sum(tr[i - 14:i]) / 14.0 for i in range(14, len(tr) + 1)]

    def q(xs: Sequence[float]) -> Dict[str, float]:
        s = sorted(xs)
        return {k: round(s[min(len(s) - 1, int(p * len(s)))], 3) for k, p in
                (("p50", 0.5), ("p75", 0.75), ("p90", 0.9), ("p99", 0.99))} if s else {}

    r24 = [(max(b.h for b in c1h[i - 24:i]) - min(b.l for b in c1h[i - 24:i])) / c1h[i - 1].c * 100.0
           for i in range(24, len(c1h) + 1)]
    return {"atr14_1h_pct": q(atr_pct(c1h)), "atr14_15m_pct": q(atr_pct(c15)), "range_24h_pct": q(r24)}


# ── variants ─────────────────────────────────────────────────────────────

K_ATRS: Tuple[float, ...] = (0.5, 1.0, 1.3, 2.0)
FLOORS: Tuple[float, ...] = (0.0, 0.0025, 0.004)
OFFSETS: Tuple[float, ...] = (0.0, 0.001, 0.005)
SIDES: Tuple[str, ...] = ("both", "trend120", "trend24")


def edge_name(fam: str, k: float, floor: float, off: float, sides: str, lev: float) -> str:
    return f"{fam}_k{k:g}_f{floor * 100:g}_o{off * 100:g}_{sides}_{lev:g}x"


def variants(leverage: float = 50.0) -> Dict[str, Dict[str, Any]]:
    """name -> {family, rule kwargs} at `leverage`. edge24 and pull8 share
    one grid (stop buffer in ATR x floor x target offset x side mode); orb
    is stop mode x target."""
    out: Dict[str, Dict[str, Any]] = {}
    for fam, lookback in (("edge24", 24), ("pull8", 8)):
        for k in K_ATRS:
            for fl in FLOORS:
                for off in OFFSETS:
                    for sd in SIDES:
                        if fam == "pull8" and sd == "both":
                            continue          # pull8 is the trend-filtered family
                        out[edge_name(fam, k, fl, off, sd, leverage)] = {
                            "family": fam, "kind": "edge",
                            "kw": {"k_atr": k, "floor_frac": fl, "offset_frac": off, "sides": sd,
                                   "entry_lookback_h": lookback, "leverage": leverage}}
    for sm in ("far", "mid"):
        for tr in (1.5, 2.0, 3.0):
            out[f"orb_{sm}_{tr:g}r_{leverage:g}x"] = {
                "family": "orb", "kind": "orb", "kw": {"stop_mode": sm, "target_r": tr, "leverage": leverage}}
    return out


def oil_as_is(leverage: float, fit: Sequence[float] = ()) -> Dict[str, Any]:
    """The oil and gold formula untouched: 1.3 x ATR with the 0.8% floor,
    the 0.5% target offset, both sides."""
    return {"family": "oil_as_is", "kind": "edge",
            "kw": {"k_atr": NT.ATR_BUFFER_MULT, "floor_frac": NT.STOP_BUFFER_FLOOR_FRAC, "offset_frac": 0.005,
                   "sides": "both", "entry_lookback_h": 24, "leverage": leverage, "fit": tuple(fit)}}


def live_variant() -> Dict[str, Any]:
    """The rule as the live code has it for xyz:SP500 (next_trade's own
    constants), so this eval scores what the desk trades."""
    p = NT.SP500_RULE
    return {"family": "live", "kind": "edge",
            "kw": {"k_atr": p["k_atr"], "floor_frac": p["floor_frac"], "offset_frac": p["target_offset_frac"],
                   "sides": p["backtest_sides"], "entry_lookback_h": 24, "leverage": NT.SP500_LEVERAGE,
                   "fit": tuple(NT.leverage_fit_for(COIN))}}


def make_rule(v: Dict[str, Any]) -> Rule:
    return edge_rule(**v["kw"]) if v["kind"] == "edge" else orb_rule(**v["kw"])


def neighbours(name: str, named: Dict[str, Dict[str, Any]]) -> List[str]:
    """The grid cells one step away on one knob (same family, same side
    mode and leverage). Pure."""
    v = named[name]
    if v["kind"] != "edge":
        return [n for n, o in named.items() if o["family"] == v["family"] and n != name]
    out = []
    for n, o in named.items():
        if n == name or o["family"] != v["family"]:
            continue
        a, b = v["kw"], o["kw"]
        if a["sides"] != b["sides"] or a["leverage"] != b["leverage"]:
            continue
        steps = [abs(grid.index(a[key]) - grid.index(b[key]))
                 for key, grid in (("k_atr", K_ATRS), ("floor_frac", FLOORS), ("offset_frac", OFFSETS))]
        if sorted(steps) == [0, 0, 1]:
            out.append(n)
    return out


# ── verdict ──────────────────────────────────────────────────────────────

def verdict(row_1h: Dict[str, Any], row_15m: Optional[Dict[str, Any]], neighbour_net: Sequence[float]) -> Dict[str, Any]:
    """EDGE / LEANING / NOISE from one variant's rows (the module docstring
    has the bar). `row_*`: {"full", "tail", "stress"} summaries. Pure."""
    def pos(row: Optional[Dict[str, Any]], key: str) -> bool:
        return bool(row) and row[key]["trades"] > 0 and row[key]["net_r"] > 0

    legs = {
        "trades_1h_30": row_1h["full"]["trades"] >= MIN_TRADES_EDGE,
        "tail_trades_5": row_1h["tail"]["trades"] >= MIN_TRADES_TAIL,
        "full_1h": pos(row_1h, "full"), "tail_1h": pos(row_1h, "tail"),
        "full_15m": pos(row_15m, "full"), "tail_15m": pos(row_15m, "tail"),
        "stress_1h": pos(row_1h, "stress"),
        "ex_best2_1h": row_1h["full"]["net_r_ex_best2"] > 0,
        "neighbours": bool(neighbour_net) and sum(1 for x in neighbour_net if x > 0) * 2 >= len(neighbour_net),
    }
    if all(legs.values()):
        v = "EDGE"
    elif legs["full_1h"] and legs["tail_1h"] and legs["ex_best2_1h"] and legs["neighbours"] and legs["tail_trades_5"]:
        v = "LEANING"
    else:
        v = "NOISE"
    return {"verdict": v, "legs": legs, "failed": [k for k, ok in legs.items() if not ok]}


# ── the run ──────────────────────────────────────────────────────────────

@dataclass
class Dataset:
    name: str
    bars: List[Candle]
    step_ms: int
    ticks: List[Optional[Tick]] = field(default_factory=list)

    @property
    def start_ms(self) -> int:
        return next(t.t for t in self.ticks if t is not None)

    @property
    def end_ms(self) -> int:
        return self.bars[-1].t + self.step_ms


def event_windows(start_ms: int, end_ms: int, before_h: float = 2.0, after_h: float = 2.0) -> List[Tuple[int, int]]:
    """FOMC decisions and CPI releases over the sample, 2h either side: the
    windows the live event blackout holds (services/intel/calendar_us.py)."""
    from services.intel import calendar_us
    d0 = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date()
    out = []
    for fn in (calendar_us.fomc_releases, calendar_us.cpi_releases):
        for rel in fn(d0, d1):
            at = int(rel.at_utc.timestamp() * 1000)
            out.append((at - int(before_h * HOUR_MS), at + int(after_h * HOUR_MS)))
    return sorted(out)


def score(ds: Dataset, v: Dict[str, Any], fund: Tuple[List[int], List[float]], *, weekend: str = "trade",
          blackouts: Sequence[Tuple[int, int]] = (), with_stress: bool = True) -> Dict[str, Any]:
    """{"full", "train", "tail", "stress", "trades"} for one variant on one dataset."""
    trades = simulate(ds.ticks, ds.bars, ds.step_ms, make_rule(v), MEASURED, fund, weekend=weekend, blackouts=blackouts)
    cut = split_ms(ds.start_ms, ds.end_ms)
    out = {"full": summarize(trades, ds.start_ms, ds.end_ms), "train": summarize(trades, ds.start_ms, cut),
           "tail": summarize(trades, cut, ds.end_ms), "trades": trades}
    if with_stress:
        st = simulate(ds.ticks, ds.bars, ds.step_ms, make_rule(v), STRESS, fund, weekend=weekend, blackouts=blackouts)
        out["stress"] = summarize(st, ds.start_ms, ds.end_ms)
    return out


def pick(rows: Dict[str, Dict[str, Any]], named: Dict[str, Dict[str, Any]], family: str) -> Optional[str]:
    """The family's variant with the highest net R on the TRAIN part of the
    dataset, among those with MIN_TRADES_PICK train trades. Ties: the name."""
    cands = [(rows[n]["train"]["net_r"], n) for n, v in named.items()
             if v["family"] == family and rows[n]["train"]["trades"] >= MIN_TRADES_PICK]
    return max(cands, key=lambda x: (x[0], x[1]))[1] if cands else None


def run(snapshot_dir: str, out_dir: str, risk_pct: float = 6.0) -> Dict[str, Any]:
    from services.strategies.backtest import snapshot as SNAP
    snap = SNAP.load_snapshot(snapshot_dir, COIN)
    if not snap.c1h or not snap.c15:
        raise SystemExit(f"no {COIN} candles in {snapshot_dir}: run services.strategies.backtest.snapshot first")
    fund = funding_index(snap.funding)
    d1h = Dataset("1h", snap.c1h, HOUR_MS)
    d15 = Dataset("15m", snap.c15, STEP_MS["15m"])
    d1h.ticks = build_ticks(d1h.bars, d1h.step_ms, snap.c1h)
    d15.ticks = build_ticks(d15.bars, d15.step_ms, snap.c1h)
    sets = {"1h": d1h, "15m": d15}

    report: Dict[str, Any] = {
        "coin": COIN, "snapshot_dir": snapshot_dir, "venue_max_leverage": VENUE_MAX_LEVERAGE,
        "costs": {"measured": MEASURED.__dict__, "stress": STRESS.__dict__},
        "windows": {k: {"start": _iso(d.start_ms), "end": _iso(d.end_ms), "tail_from": _iso(split_ms(d.start_ms, d.end_ms)),
                        "days": round((d.end_ms - d.start_ms) / DAY_MS, 1),
                        "move_pct": round((d.bars[-1].c / next(b.c for b, t in zip(d.bars, d.ticks) if t) - 1) * 100, 2)}
                    for k, d in sets.items()},
        "leverage_budget": [leverage_budget(lv) for lv in LEVERAGES + (15.0, 10.0)],
        "noise": noise_table(snap.c1h, snap.c15),
    }

    # 1. the grid at 50x, both datasets
    named = variants(50.0)
    rows: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in sets}
    for name, v in named.items():
        for k, d in sets.items():
            if v["kind"] == "orb" and k == "1h":
                continue
            rows[k][name] = score(d, v, fund, with_stress=False)
    csv_rows: List[Dict[str, Any]] = []
    for k in sets:
        for name, r in rows[k].items():
            for part in ("full", "train", "tail"):
                csv_rows.append({"dataset": k, "variant": name, "family": named[name]["family"], "part": part,
                                 **{f: r[part][f] for f in ("trades", "per_week", "win_rate", "mean_r", "net_r",
                                                            "max_dd_r", "median_stop_pct", "fits_50x")},
                                 "long_net_r": r[part]["long"]["net_r"], "short_net_r": r[part]["short"]["net_r"]})

    # 2. one pick per family (on the 1h train part; orb has only 15m), then judge it
    fams: Dict[str, Any] = {}
    for fam in ("edge24", "pull8", "orb"):
        base = "15m" if fam == "orb" else "1h"
        name = pick(rows[base], named, fam)
        grid = [n for n, v in named.items() if v["family"] == fam and n in rows[base]]
        above = sum(1 for n in grid if rows[base][n]["full"]["net_r"] > 0)
        entry: Dict[str, Any] = {"picked": name, "picked_on": f"{base} train", "variants": len(grid),
                                 "variants_net_positive_full": above}
        if name:
            v = named[name]
            full = {k: score(sets[k], v, fund) for k in sets if not (fam == "orb" and k == "1h")}
            nb = [rows[base][n]["full"]["net_r"] for n in neighbours(name, named) if n in rows[base]]
            if fam == "orb":
                # orb has no 1h dataset: judged on 15m alone, so it can never reach EDGE
                jd = verdict(full["15m"], None, nb)
            else:
                jd = verdict(full["1h"], full.get("15m"), nb)
            entry.update({"rows": {k: {p: r[p] for p in ("full", "train", "tail", "stress")} for k, r in full.items()},
                          "neighbours_net_r": nb, **jd})
        else:
            entry.update({"verdict": "NOISE", "failed": [f"no variant with {MIN_TRADES_PICK} train trades"]})
        fams[fam] = entry
    report["families"] = fams

    # 3. the oil formula as is, at 20x (fit 20/15) and at 50x
    report["oil_as_is"] = {}
    for label, lev, fit in (("20x_fit_20_15", 20.0, NT.LEVERAGE_FIT), ("50x", 50.0, ())):
        v = oil_as_is(lev, fit)
        report["oil_as_is"][label] = {k: {p: r[p] for p in ("full", "tail", "stress")}
                                      for k, r in ((k, score(sets[k], v, fund)) for k in sets)}

    # 4. the live rule: every leverage, weekend modes, the event blackout
    live = live_variant()
    lv_rows = {k: score(sets[k], live, fund) for k in sets}
    report["live"] = {
        "params": {**live["kw"], "fit": list(live["kw"]["fit"])},
        "rows": {k: {p: r[p] for p in ("full", "train", "tail", "stress")} for k, r in lv_rows.items()},
        "equity": {k: equity_path(r["trades"], max(live["kw"]["fit"] or (live["kw"]["leverage"],)), risk_pct)
                   for k, r in lv_rows.items()},
    }
    by_lev: Dict[str, Any] = {}
    for lev in LEVERAGES:
        v = {**live, "kw": {**live["kw"], "leverage": lev, "fit": ()}}
        by_lev[f"{lev:g}x"] = {}
        for k, d in sets.items():
            r = score(d, v, fund)
            by_lev[f"{lev:g}x"][k] = {"full": r["full"], "tail": r["tail"], "stress": r["stress"],
                                      "equity": equity_path(r["trades"], lev, risk_pct)}
    report["live"]["by_leverage"] = by_lev
    report["live"]["weekend"] = {
        mode: {k: {p: r[p] for p in ("full", "tail")} for k, r in
               ((k, score(sets[k], live, fund, weekend=mode, with_stress=False)) for k in sets)}
        for mode in ("trade", "no_entry", "flat")}
    report["live"]["event_blackout"] = {
        k: {p: r[p] for p in ("full", "tail")} for k, r in
        ((k, score(d, live, fund, blackouts=event_windows(d.start_ms, d.end_ms), with_stress=False))
         for k, d in sets.items())}
    live_grid = variants(50.0)
    live_name = edge_name("edge24", live["kw"]["k_atr"], live["kw"]["floor_frac"], live["kw"]["offset_frac"],
                          live["kw"]["sides"], 50.0)
    nb = [rows["1h"][n]["full"]["net_r"] for n in (neighbours(live_name, live_grid) if live_name in live_grid else [])]
    report["live"].update(verdict(lv_rows["1h"], lv_rows["15m"], nb))
    report["live"]["neighbours_net_r"] = nb
    report["risk_pct"] = risk_pct

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)
    return report


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _line(label: str, r: Dict[str, Any]) -> str:
    return (f"  {label:34s} n={r['trades']:4d} {r['per_week']:5.2f}/wk win={r['win_rate']} meanR={r['mean_r']} "
            f"netR={r['net_r']:+.2f} ddR={r['max_dd_r']:.2f} L={r['long']['net_r']:+.2f} S={r['short']['net_r']:+.2f} "
            f"stop%={r['median_stop_pct']} fits50x={r['fits_50x']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", default="/tmp/sp500-strategy")
    ap.add_argument("--risk-pct", type=float, default=6.0, help="risk per trade, percent of the desk slice")
    a = ap.parse_args(argv)
    rep = run(os.path.expanduser(a.snapshot_dir), a.out_dir, a.risk_pct)
    print(json.dumps({"windows": rep["windows"], "leverage_budget": rep["leverage_budget"], "noise": rep["noise"]}, indent=1))
    for fam, e in rep["families"].items():
        print(f"{fam}: picked {e['picked']} ({e.get('variants_net_positive_full')}/{e.get('variants')} variants "
              f"net positive) -> {e['verdict']} failed={e.get('failed')}")
        for k, r in (e.get("rows") or {}).items():
            for part in ("full", "tail", "stress"):
                print(_line(f"{k} {part}", r[part]))
    for label, sets in rep["oil_as_is"].items():
        for k, r in sets.items():
            print(_line(f"oil_as_is {label} {k} full", r["full"]))
    lv = rep["live"]
    print(f"live rule {lv['params']} -> {lv['verdict']} failed={lv['failed']}")
    for k, r in lv["rows"].items():
        for part in ("full", "tail", "stress"):
            print(_line(f"live {k} {part}", r[part]))
        print(f"  live {k} equity at {a.risk_pct:g}% risk: {lv['equity'][k]}")
    for lev, sets in lv["by_leverage"].items():
        for k, r in sets.items():
            print(_line(f"live fixed {lev} {k} full", r["full"]) + f" eq={r['equity']}")
    for mode, sets in lv["weekend"].items():
        for k, r in sets.items():
            print(_line(f"weekend={mode} {k} full", r["full"]))
    for k, r in lv["event_blackout"].items():
        print(_line(f"event blackout {k} full", r["full"]))
    print(f"report: {os.path.join(a.out_dir, 'report.json')}")
    return 0 if lv["verdict"] in ("EDGE", "LEANING") else 1


if __name__ == "__main__":
    raise SystemExit(main())
