"""Stop-and-reverse around the desk's own levels, 2026-11-16. Research only.

Operator, 2026-11-16: "I SHORTED TO OUR ENTRY, GOT CALLED ON 5% LOSS, LONGED
TO HIGHER RANGE THAT I JUST HIT ... SHORT 5% ON A TRIP DOWN TO ENTRY, IF
TRIPPED AT 5% ST, LONG THAT ENTRY TO LOCAL HIGH". What they traded that
night is `OPERATOR_TRADES` below. One deterministic script, no LLM call, no
order, no config write.

The rule, as tested (every choice is a declared axis or a named constant):

  short leg   While the journal thesis is long and a closed bar sits at
              least X above the thesis long entry (X 0.25 / 0.5 / 0.75%),
              short at the market. Target = the thesis entry (resting
              limit). Stop = +0.25% ("5% of the position's margin" at 20x),
              +0.36% (the operator's), +0.5%, or just over the 4h swing
              high. One cycle per thesis.
  reverse leg When that stop fills, go long. "resting": a buy-stop at the
              same price fills with it. "tick": the desk reacts, a market
              order one bar later. Target = the local high (highest high of
              the last 4 / 8 / 12 hours, PRIMARY), the 24h high less 0.5%
              (the live rule's offset), or the thesis's first target. Stop =
              0.25% under the fill (the operator's), just under the short's
              entry, or 1 / 2 x ATR14(1h). No room to the target = no flip.
  third leg   Optional. When the long's target fills, short again, target
              the long's own entry ("flip") or the thesis entry in force
              ("thesis"), same stop rule as the short leg. No further flip.

Sizing: margin share of equity at 20x (5 / 10 / 20% on every leg), 1% of
equity at risk at the stop ("r1"), and the operator's own mix ("op": short
20%, reverse long 60%, third short 5%).

Fills (the model of INTRADAY_5M_TEST_2026-11-16, never a search axis): a
market order placed by the desk fills at the open one 5m bar late and pays
half that bar's range, never under the sim's 3 bps; a resting stop is a
taker fill with the sim's stop slippage; a target is a maker limit that
needs a trade-through by one tick and never fills on its own fill bar; a
stop and a target on one bar is a loss. Fees are the account's measured xyz
rates (`intraday_5m_test.measured_fees`, about 0.90 / 0.31 bps). Funding on
the hour. Weekend flat.

Every variant runs alone and beside the live dip-buy book
(`short_ladder_test.prepare`): nothing opens while the live long is open,
and an open sleeve position is closed when the live long enters. Datasets:
Brent 5m (primary), WTI 5m, Brent 15m and WTI 15m (the longer journal
window, coarser bars). Walk-forward: both halves of the primary by entry
time. The bar is `short_ladder_test.judge` with at least 10 trades beside
live on the primary; under 10 is LEANING or NOISE, never PASS.

The replay: `replay_tonight` runs the rule on 2026-11-15 23:00Z to
2026-11-16 07:00Z twice. "pinned" starts from the operator's own short
(92.563, stop 92.90, target 91.81) and lets the engine run the stop, the
flip and the third leg; "mechanical" lets the rule find its own entries.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.stop_reverse_test \\
        --cache-dir <dir> --out-dir <dir>

The verdict is read on the fills the desk can get today (`desk_fill`: the
short leg alone, and flips with the "tick" fill). "resting" rows are the
better order's reading, reported beside it. Whatever passes or leans is
audited (`audit`): its net alone, its twin under the other fill, and its
net without the night the rule was written from.

Exit 0 when a desk-fill variant passes the bar, 1 when none does, 2 with no
journal.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import dataclasses
import json
import multiprocessing
import os
import statistics
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies import next_trade as NT
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, _market_exit_slip
from services.strategies.evals import intraday_5m_test as IN
from services.strategies.evals import short_ladder_test as SL
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
MIN_N = 10                     # under this many trades beside live a variant is LEANING or NOISE
PRIMARY = "brent_5m"
EPS = 1e-9

MIN_ROOM = 0.001               # a target closer than 10 bps to the fill is no trade
MIN_STOP = 0.001               # a stop closer than 10 bps to the fill is refused
MAX_STOP = 0.02                # and one wider than 2% (half the way to liquidation at 20x)
SWING_HOURS = 4                # the "swing" short stop sits over this many hours' high
SWING_PAD = 0.0005
ENTRY_PAD = 0.0005             # "sentry": the long's stop this far under the short's entry
HI24_OFFSET = 0.005            # the live rule's target offset inside the 24h edge

X_GRID = (0.0025, 0.005, 0.0075)
SHORT_STOPS = ("p0.25", "p0.36", "p0.50", "swing")
LONG_TARGETS = ("loc4", "loc8", "loc12", "hi24", "thesis")
LONG_STOPS = ("p0.25", "sentry", "atr1.0", "atr2.0")
FILLS = ("resting", "tick")
THIRDS = ("off", "flip", "thesis")
SIZES = ("m05", "m10", "m20", "r1", "op")
MARGIN_SHARE = {"m05": 0.05, "m10": 0.10, "m20": 0.20}
OP_SHARE = {"short": 0.20, "flip": 0.60, "third": 0.05}     # the operator's own night, as shares of equity
RISK_R1 = 0.01
LOC_HOURS = (4, 8, 12)

FAMILIES = {"S": "short leg alone (test A, tight stops)",
            "SR_local": "short + reverse long to the local high (4/8/12h)",
            "SR_hi24": "short + reverse long to the 24h high less 0.5%",
            "SR_thesis": "short + reverse long to the thesis target",
            "SR3_local": "three legs, long to the local high",
            "SR3_hi24": "three legs, long to the 24h high less 0.5%",
            "SR3_thesis": "three legs, long to the thesis target"}

REPLAY_START = int(datetime(2026, 11, 15, 23, tzinfo=timezone.utc).timestamp() * 1000)
REPLAY_END = int(datetime(2026, 11, 16, 7, tzinfo=timezone.utc).timestamp() * 1000)
REPLAY_EQUITY = 214.0
PIN_BAR = int(datetime(2026, 11, 16, 4, 45, tzinfo=timezone.utc).timestamp() * 1000)
PIN_SHORT = (92.563, 92.90, 91.81)          # the operator's short: fill, stop, target
OPERATOR_TRADES = (
    {"leg": "short", "entry": "2026-11-16 04:46Z", "entry_px": 92.563, "size": 9.27, "stop_px": 92.90,
     "target_px": 91.81, "exit": "2026-11-16 05:47Z", "exit_px": 92.925, "reason": "stop", "net_usd": -3.36},
    {"leg": "flip", "entry": "2026-11-16 06:00Z", "entry_px": 92.96, "size": 26.84, "stop_px": 92.695,
     "target_px": 93.709, "exit": "2026-11-16 06:32Z", "exit_px": 93.722, "reason": "target", "net_usd": 20.39},
    {"leg": "third", "entry": "2026-11-16 06:38Z", "entry_px": 93.808, "size": 2.46, "stop_px": 94.045,
     "target_px": 92.84, "exit": None, "exit_px": None, "reason": "open", "net_usd": None})
MATCH_TOL = {"short": 0.05, "flip": 0.10, "third": 0.15}    # $ on the exit (short, flip) or the entry (third)


# ── variants ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    x: float                   # arm: a closed bar at or over thesis entry * (1 + x)
    sstop: str                 # the short's stop rule (SHORT_STOPS)
    flip: bool = True          # False = the short leg alone
    ltarget: str = "loc8"      # the reverse long's target (LONG_TARGETS)
    lstop: str = "p0.25"       # its stop (LONG_STOPS)
    fill: str = "tick"         # how the flip and the third leg fill (FILLS)
    third: str = "off"         # THIRDS
    size: str = "m20"          # SIZES

    @property
    def name(self) -> str:
        head = f"x{self.x * 100:.2f}_{self.sstop}"
        if not self.flip:
            return f"S_{head}_{self.size}"
        return f"SR_{head}_{self.ltarget}_{self.lstop}_{self.fill}_3{self.third}_{self.size}"

    @property
    def family(self) -> str:
        if not self.flip:
            return "S"
        group = "local" if self.ltarget.startswith("loc") else self.ltarget
        return ("SR_" if self.third == "off" else "SR3_") + group


def variants() -> List[Variant]:
    out = [Variant(x, s, flip=False, size=z) for x in X_GRID for s in SHORT_STOPS for z in SIZES]
    out += [Variant(x, s, True, lt, ls, f, th, z) for x in X_GRID for s in SHORT_STOPS for lt in LONG_TARGETS
            for ls in LONG_STOPS for f in FILLS for th in THIRDS for z in SIZES]
    return out


OPERATOR_VARIANT = Variant(0.0075, "p0.36", True, "thesis", "p0.25", "tick", "flip", "op")
PRIMARY_VARIANT = dataclasses.replace(OPERATOR_VARIANT, ltarget="loc8")


def _step(levels: Sequence[Any], value: Any) -> List[Any]:
    i = levels.index(value)
    return [levels[k] for k in (i - 1, i + 1) if 0 <= k < len(levels)]


def neighbours(v: Variant) -> List[Variant]:
    """One step on each ordered axis: X, the percent stops, the local-high
    hours, the ATR stops. Everything else held."""
    out = [dataclasses.replace(v, x=x) for x in _step(X_GRID, v.x)]
    if v.sstop != "swing":
        out += [dataclasses.replace(v, sstop=s) for s in _step(SHORT_STOPS[:3], v.sstop)]
    if v.flip and v.ltarget.startswith("loc"):
        out += [dataclasses.replace(v, ltarget=t) for t in _step(LONG_TARGETS[:3], v.ltarget)]
    if v.flip and v.lstop.startswith("atr"):
        out += [dataclasses.replace(v, lstop=s) for s in _step(LONG_STOPS[2:], v.lstop)]
    return out


# ── per-bar series (pure) ──────────────────────────────────────────────────

@dataclass
class Series:
    step_ms: int
    late: int                           # bars between a decision and the desk's market fill (5m: 1, 15m: 0)
    t: List[int]
    o: List[float]
    h: List[float]
    l: List[float]
    c: List[float]
    ok: List[bool]
    weekend: List[bool]
    entry: List[Optional[float]]        # the long thesis's entry in force at the bar's open
    target: List[Optional[float]]       # its first target
    tid: List[Optional[str]]
    hi24: List[float]
    atr: List[float]                    # ATR14 of closed 1h bars
    fund: List[Optional[float]]
    loc: Dict[int, List[Optional[float]]]   # hours -> highest high of the bars in that window BEFORE this bar


@dataclass
class Live:
    eq: List[float]
    long_open: List[bool]
    long_entry: List[bool]


def rolling_high_before(t: Sequence[int], h: Sequence[float], window_ms: int) -> List[Optional[float]]:
    """Per bar: the highest high of the bars that opened inside the
    `window_ms` before it; None until one full window lies behind."""
    out: List[Optional[float]] = []
    dq: deque = deque()
    for i in range(len(t)):
        while dq and t[dq[0]] < t[i] - window_ms:
            dq.popleft()
        out.append(h[dq[0]] if dq and t[0] <= t[i] - window_ms else None)
        while dq and h[dq[-1]] <= h[i]:
            dq.pop()
        dq.append(i)
    return out


def build_series(ticks: Sequence[SL.Tick], step_ms: int, late: int) -> Series:
    t, h = [k.t for k in ticks], [k.h for k in ticks]
    return Series(step_ms=step_ms, late=late, t=t, o=[k.o for k in ticks], h=h, l=[k.l for k in ticks],
                  c=[k.c for k in ticks], ok=[k.ok for k in ticks], weekend=[k.weekend for k in ticks],
                  entry=[k.ceiling if k.side == "long" else None for k in ticks],
                  target=[k.target if k.side == "long" else None for k in ticks],
                  tid=[k.thesis_id for k in ticks], hi24=[k.hi24 for k in ticks], atr=[k.atr for k in ticks],
                  fund=[k.fund for k in ticks],
                  loc={n: rolling_high_before(t, h, n * HOUR_MS) for n in sorted(set(LOC_HOURS) | {SWING_HOURS})})


def live_on(times: Sequence[int], prep: Dict[str, Any], start_equity: float) -> Live:
    """The live long book of `short_ladder_test.prepare` mapped onto bars."""
    tick_t = [tk.t for tk in prep["ticks"]]
    ivs = sorted((e, x if x is not None else prep["end_ms"]) for e, x in prep["intervals"])
    entries = {e for e, _ in ivs}
    eq, opn, ent = [], [], []
    for t in times:
        k = bisect.bisect_right(tick_t, t) - 1
        eq.append(prep["long_curve"][k] if k >= 0 else start_equity)
        opn.append(any(e <= t <= x for e, x in ivs))
        ent.append(t in entries)
    return Live(eq, opn, ent)


# ── levels and sizing (pure) ───────────────────────────────────────────────

def short_stop(rule: str, fill: float, swing_high: Optional[float]) -> Optional[float]:
    """The stop of a short filled at `fill`. "pN" = N percent over the
    fill; "swing" = just over the swing high, never closer than MIN_STOP."""
    if rule == "swing":
        if swing_high is None:
            return None
        return max(swing_high * (1.0 + SWING_PAD), fill * (1.0 + MIN_STOP))
    return fill * (1.0 + float(rule[1:]) / 100.0)


def long_stop(rule: str, fill: float, short_entry: float, atr: float) -> Optional[float]:
    """The reverse long's stop. "pN" = N percent under the fill; "sentry" =
    just under the short's entry; "atrK" = K x ATR14(1h) under the fill."""
    if rule == "sentry":
        return short_entry * (1.0 - ENTRY_PAD)
    if rule.startswith("atr"):
        return fill - float(rule[3:]) * atr if atr > 0 else None
    return fill * (1.0 - float(rule[1:]) / 100.0)


def long_target(rule: str, s: Series, x: int) -> Optional[float]:
    if rule == "hi24":
        return s.hi24[x] * (1.0 - HI24_OFFSET) if s.ok[x] else None
    if rule == "thesis":
        return s.target[x]
    return s.loc[int(rule[3:])][x]


def leg_share(size: str, leg: str) -> Optional[float]:
    """The leg's margin as a share of equity; None for risk sizing."""
    if size == "op":
        return OP_SHARE[leg]
    return MARGIN_SHARE.get(size)


def leg_notional(size: str, leg: str, equity: float, fill: float, stop: float, leverage: float = LEVERAGE) -> float:
    """Margin share x equity x leverage; "r1" = RISK_R1 of equity lost at
    the stop, never over the whole equity at `leverage`."""
    share = leg_share(size, leg)
    if share is not None:
        return share * equity * leverage
    dist = abs(fill - stop) / fill
    return min(RISK_R1 * equity / dist, equity * leverage) if dist > 0 else 0.0


# ── the walker ─────────────────────────────────────────────────────────────

def walk(s: Series, v: Variant, p: Params, start_equity: float, lo_i: int = 0, hi_i: Optional[int] = None,
         live: Optional[Live] = None, forced: Optional[Dict[int, Tuple[float, float, float]]] = None,
         midbar_worst: bool = False) -> Dict[str, Any]:
    """One variant over bars [lo_i, hi_i], one position at a time.

    Per bar: (1) funding on the hour, then forced closes at the open (the
    live long enters, the weekend starts; market, taker); (2) a pending
    market order fills at the open; (3) the bar's range: the stop first
    (a bar that opens beyond it fills at its open), then the target (a
    trade-through, never on the fill bar). A short's stop starts the
    reverse long, the long's target starts the third leg: "resting" fills
    inside the same bar at that price, and on that bar only its CLOSE can
    stop the new leg (the close is the one print known to come after the
    fill; `midbar_worst=True` is the harsh reading: the bar's whole range
    counts, as if its worst print came after the fill); "tick" queues a
    market order `1 + s.late` bars on. (4) flat with
    nothing pending, a closed bar over the thesis entry by `v.x` arms the
    short for the bar `1 + s.late` on, once per thesis.

    `forced` {bar time: (fill, stop, target)} opens those shorts instead of
    step (4): the pinned replay. Returns {"trades", "refused", "curve"}
    (the sleeve's marked P&L at each bar close)."""
    t, o, h, l, c = s.t, s.o, s.h, s.l, s.c
    hi_i = len(t) - 1 if hi_i is None else hi_i
    tick, late = p.tick_size, s.late
    pos: Optional[Dict[str, Any]] = None
    pending: Optional[Dict[str, Any]] = None
    pnl, cycle = 0.0, 0
    trades: List[Dict[str, Any]] = []
    refused: Counter = Counter()
    curve: List[float] = []
    used: set = set()

    def equity(x: int) -> float:
        return (live.eq[x] if live is not None else start_equity) + pnl

    def market_slip(x: int, leg: str) -> float:
        share = leg_share(v.size, leg) or 0.2
        base = _market_exit_slip(p, share * max(equity(x), 0.0) * p.leverage)
        return max(base, (h[x] - l[x]) / o[x] / 2.0) if o[x] > 0 else base

    def open_pos(leg: str, side: int, x: int, ref: float, fill: float, stop: Optional[float],
                 target: Optional[float], midbar: bool, maker: bool, **extra: Any) -> Optional[Dict[str, Any]]:
        eq = equity(x)
        if eq <= 0 or fill <= 0 or stop is None or target is None:
            refused[leg + "_no_level"] += 1
            return None
        dist = side * (fill - stop)
        if not MIN_STOP - EPS <= dist / fill <= MAX_STOP + EPS:
            refused[leg + "_stop_range"] += 1
            return None
        if side * (target - fill) < MIN_ROOM * fill:
            refused[leg + "_no_room"] += 1
            return None
        notional = leg_notional(v.size, leg, eq, fill, stop, p.leverage)
        size = notional / fill
        return {"leg": leg, "side": side, "j": x, "ref": ref, "fill": fill, "stop": stop, "target": target,
                "size": size, "notional": notional, "fees": (p.maker_fee if maker else p.taker_fee) * notional,
                "funding": 0.0, "midbar": midbar, "eq": eq, "risk": dist * size, **extra}

    def close_pos(x: int, ref: float, px: float, reason: str, maker: bool = False) -> Dict[str, Any]:
        nonlocal pos, pnl
        q, pos = pos, None
        side, size = q["side"], q["size"]
        done = reason != "open"
        fees = q["fees"] + ((p.maker_fee if maker else p.taker_fee) * px * size if done else 0.0)
        raw = side * (ref - q["ref"]) * size
        gross = side * (px - q["fill"]) * size
        net = gross - fees - q["funding"]
        pnl += net
        exit_ms = t[x] if done else None
        trades.append({"leg": q["leg"], "cycle": q["cycle"], "side": "long" if side > 0 else "short",
                       "entry_ms": t[q["j"]], "exit_ms": exit_ms, "entry": _iso(t[q["j"]]), "exit": _iso(exit_ms),
                       "entry_px": round(q["fill"], 4), "exit_px": round(px, 4), "stop_px": round(q["stop"], 4),
                       "target_px": round(q["target"], 4), "size": size, "notional": q["notional"],
                       "margin_pct": q["notional"] / p.leverage / q["eq"] * 100.0, "reason": reason,
                       "raw_usd": raw, "slip_usd": raw - gross, "fees_usd": fees, "funding_usd": q["funding"],
                       "net_usd": net, "risk_usd": q["risk"], "risk_pct": q["risk"] / q["eq"] * 100.0,
                       "r": net / q["risk"] if q["risk"] > 1e-12 else None, "i": q["j"], "x": x,
                       "hold_min": (t[x] - t[q["j"]]) / 60_000.0})
        return q

    def blocked(x: int, leg: str) -> bool:
        if s.weekend[x]:
            refused[leg + "_weekend"] += 1
            return True
        if live is not None and live.long_open[x]:
            refused[leg + "_live_long"] += 1
            return True
        return False

    def open_short(x: int) -> Optional[Dict[str, Any]]:
        e = s.entry[x]
        if blocked(x, "short"):
            return None
        if e is None or not s.ok[x] or o[x] < e * (1.0 + v.x):
            refused["short_moved"] += 1
            return None
        fill = o[x] * (1.0 - market_slip(x, "short"))
        got = open_pos("short", -1, x, o[x], fill, short_stop(v.sstop, fill, s.loc[SWING_HOURS][x]), e, False,
                       False, cycle=cycle + 1)
        if got is not None:
            used.add(s.tid[x])
        return got

    def open_flip(x: int, ref: Optional[float], short: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        midbar = ref is not None
        if not midbar and blocked(x, "flip"):
            return None
        if midbar:
            fill = ref * (1.0 + _market_exit_slip(p, short["notional"]))
        else:
            ref = o[x]
            fill = ref * (1.0 + market_slip(x, "flip"))
        return open_pos("flip", 1, x, ref, fill, long_stop(v.lstop, fill, short["fill"], s.atr[x]),
                        long_target(v.ltarget, s, x), midbar, False, cycle=short["cycle"])

    def open_third(x: int, ref: Optional[float], long_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        midbar = ref is not None
        if not midbar and blocked(x, "third"):
            return None
        if midbar:
            fill = ref                                  # the sell limit rests at the long's own target: maker
        else:
            ref = o[x]
            fill = ref * (1.0 - market_slip(x, "third"))
        target = long_["fill"] if v.third == "flip" else s.entry[x]
        return open_pos("third", -1, x, ref, fill, short_stop(v.sstop, fill, s.loc[SWING_HOURS][x]), target,
                        midbar, midbar, cycle=long_["cycle"])

    for x in range(lo_i, hi_i + 1):
        if pos is not None and x > pos["j"]:
            if s.fund[x] is not None:
                pos["funding"] += pos["side"] * s.fund[x] * pos["size"] * o[x]
            why = ("live_long" if live is not None and live.long_entry[x] else "weekend" if s.weekend[x] else None)
            if why:
                px = o[x] * (1.0 - pos["side"] * market_slip(x, pos["leg"]))
                close_pos(x, o[x], px, why)
                pending = None
        if pending is not None and pending["x"] == x:
            job, pending = pending, None
            if job["leg"] == "short":
                pos = open_short(x)
            elif job["leg"] == "flip":
                pos = open_flip(x, None, job["from"])
            else:
                pos = open_third(x, None, job["from"])
            if pos is not None and pos["leg"] == "short":
                cycle += 1
        if forced is not None and pos is None and t[x] in forced:
            fill, stop, target = forced[t[x]]
            pos = open_pos("short", -1, x, fill, fill, stop, target, False, False, cycle=cycle + 1)
            cycle += pos is not None
        while pos is not None:
            side, same = pos["side"], x == pos["j"]
            if same and pos["midbar"] and not midbar_worst:
                stopped = side * (c[x] - pos["stop"]) <= 0
            else:
                stopped = side * ((l[x] if side > 0 else h[x]) - pos["stop"]) <= 0
            if stopped:
                ref = pos["stop"] if same else (min(pos["stop"], o[x]) if side > 0 else max(pos["stop"], o[x]))
                q = close_pos(x, ref, ref * (1.0 - side * _market_exit_slip(p, pos["size"] * ref)), "stop")
                if q["leg"] == "short" and v.flip:
                    if v.fill == "resting":
                        pos = open_flip(x, ref, q)
                        continue
                    pending = {"leg": "flip", "x": x + 1 + late, "from": q}
                break
            if not same and side * ((h[x] if side > 0 else l[x]) - pos["target"]) >= tick - 1e-9:
                q = close_pos(x, pos["target"], pos["target"], "target", maker=True)
                if q["leg"] == "flip" and v.third != "off":
                    if v.fill == "resting":
                        pos = open_third(x, q["target"], q)
                        continue
                    pending = {"leg": "third", "x": x + 1 + late, "from": q}
            break
        if (pos is None and pending is None and forced is None and s.ok[x] and not s.weekend[x]
                and s.entry[x] is not None and s.tid[x] not in used and c[x] >= s.entry[x] * (1.0 + v.x)
                and not (live is not None and live.long_open[x])):
            pending = {"leg": "short", "x": x + 1 + late}
        curve.append(pnl + (pos["side"] * (c[x] - pos["fill"]) * pos["size"] if pos is not None else 0.0))
    if pos is not None:
        close_pos(hi_i, c[hi_i], c[hi_i], "open")
        if curve:
            curve[-1] = pnl
    return {"trades": trades, "refused": dict(refused), "curve": curve}


# ── summaries and the bar ──────────────────────────────────────────────────

def by_leg(trades: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for leg in ("short", "flip", "third"):
        ts = [tr for tr in trades if tr["leg"] == leg]
        rs = [tr["r"] for tr in ts if tr["r"] is not None]
        out[leg] = {"n": len(ts), "wins": sum(1 for tr in ts if tr["net_usd"] > 0),
                    "net_usd": round(sum(tr["net_usd"] for tr in ts), 2),
                    "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
                    "exits": dict(sorted(Counter(tr["reason"] for tr in ts).items()))}
    return out


def run_one(v: Variant, ds: Dict[str, Any], keep: bool = False) -> Dict[str, Any]:
    """One variant on one dataset: alone, and beside the live book."""
    s, p, lo, hi = ds["series"], ds["params"], ds["lo"], ds["hi"]
    start = p.starting_equity
    alone = walk(s, v, p, start, lo, hi)
    comb = walk(s, v, p, start, lo, hi, ds["live"])
    flat = [start] * (hi - lo + 1)
    s_alone = IN.summarize(alone["trades"], alone["curve"], flat, alone["refused"])
    s_comb = IN.summarize(comb["trades"], comb["curve"], ds["live"].eq[lo:hi + 1], comb["refused"])
    mid = ds.get("mid_ms")
    out = {"alone": s_alone, "combined": s_comb, "live": ds["live_sum"], "combined_dd_pct": s_comb.get("max_dd_pct"),
           "legs_alone": by_leg(alone["trades"]), "legs_combined": by_leg(comb["trades"]),
           "cycles_alone": len({tr["cycle"] for tr in alone["trades"]}),
           "collisions": SL.collisions(alone["trades"], ds["intervals"], ds["end_ms"]),
           "drop_best_day": SL.drop_best_day(comb["trades"])}
    if mid is not None:
        for key, ts in (("halves", comb["trades"]), ("halves_alone", alone["trades"])):
            out[key] = [round(sum(tr["net_usd"] for tr in ts if tr["entry_ms"] < mid), 2),
                        round(sum(tr["net_usd"] for tr in ts if tr["entry_ms"] >= mid), 2)]
    if keep:
        out["trades_alone"], out["trades_combined"] = alone["trades"], comb["trades"]
    return out


def make_row(v: Variant, datasets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    per = {name: run_one(v, ds) for name, ds in datasets.items()}
    prim = per[PRIMARY]
    return {"name": v.name, "family": v.family, "variant": dataclasses.asdict(v), "datasets": per,
            "halves": prim["halves"], "halves_alone": prim["halves_alone"], "drop_best_day": prim["drop_best_day"]}


def judge_rows(rows: Dict[str, Dict[str, Any]], vs: Sequence[Variant]) -> None:
    """`short_ladder_test.judge` on every row, its neighbours' sleeve nets
    beside live on the primary as the plateau. No reopen leg: the fills
    here are already one bar late with half-range slippage."""
    for v in vs:
        row = rows[v.name]
        nets = [rows[n.name]["datasets"][PRIMARY]["combined"]["net_usd"] for n in neighbours(v) if n.name in rows]
        row["judge"] = SL.judge(row, nets, PRIMARY, MIN_N, reopen_leg=False)


def alone_everywhere(row: Dict[str, Any]) -> bool:
    return all(d["alone"]["net_usd"] > EPS for d in row["datasets"].values())


def desk_fill(row: Dict[str, Any]) -> bool:
    """Is this a row the desk could trade today. The short leg alone is.
    A flip is only with the "tick" fill: "resting" needs a buy-stop (and a
    sell limit for the third leg) resting beside an open position, an order
    the desk's one-bracket-per-desk tooling does not place. As in
    INTRADAY_5M_TEST ("bot" against "watcher"), the verdict is read on the
    desk's own fill; the resting rows show what the better order would buy."""
    return not row["variant"]["flip"] or row["variant"]["fill"] == "tick"


def best_of(rows: Dict[str, Dict[str, Any]], names: Sequence[str]) -> Optional[str]:
    """A variant that traded beside live first, then by verdict, then by
    its sleeve net beside live on the primary, the name breaking ties."""
    if not names:
        return None
    return min(names, key=lambda n: (rows[n]["datasets"][PRIMARY]["combined"]["n"] == 0,
                                     SL.VERDICT_RANK[rows[n]["judge"]["verdict"]],
                                     -rows[n]["datasets"][PRIMARY]["combined"]["net_usd"], n))


def summarize_families(rows: Dict[str, Dict[str, Any]], ds_names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for fam in FAMILIES:
        names = [n for n, r in rows.items() if r["family"] == fam]
        if not names:
            continue
        verdicts = Counter(rows[n]["judge"]["verdict"] for n in names)
        desk = [n for n in names if desk_fill(rows[n])]
        rest = [n for n in names if not desk_fill(rows[n])]
        v_desk = Counter(rows[n]["judge"]["verdict"] for n in desk)
        v_rest = Counter(rows[n]["judge"]["verdict"] for n in rest)
        judged = [n for n in names if rows[n]["datasets"][PRIMARY]["alone"]["n"] >= MIN_N]
        winners = [n for n in judged if alone_everywhere(rows[n])]
        per = {}
        for ds in ds_names:
            comb = [rows[n]["datasets"][ds]["combined"]["net_usd"] for n in names]
            alone = [rows[n]["datasets"][ds]["alone"]["net_usd"] for n in names]
            per[ds] = {"median_combined_usd": round(statistics.median(comb), 2),
                       "median_alone_usd": round(statistics.median(alone), 2),
                       "best_alone_usd": max(alone), "worst_alone_usd": min(alone),
                       "above_zero_combined": sum(1 for x in comb if x > EPS),
                       "above_zero_alone": sum(1 for x in alone if x > EPS)}
        out[fam] = {"label": FAMILIES[fam], "variants": len(names), "verdicts": dict(verdicts),
                    "verdict": IN.family_verdict(list(v_desk), len([n for n in judged if n in set(desk)]),
                                                 len([n for n in winners if n in set(desk)])),
                    "verdicts_desk_fill": dict(v_desk), "verdicts_resting": dict(v_rest),
                    "verdict_resting": IN.family_verdict(list(v_rest), len([n for n in judged if n in set(rest)]),
                                                         len([n for n in winners if n in set(rest)])) if rest else None,
                    "best_desk_fill": best_of(rows, desk),
                    "alone_judged": len(judged), "alone_winners_every_dataset": len(winners),
                    "combined_winners_every_dataset": sum(
                        1 for n in names if all(d["combined"]["net_usd"] > EPS for d in rows[n]["datasets"].values())),
                    "per_dataset": per, "best": best_of(rows, names),
                    "best_alone": max(names, key=lambda n: (rows[n]["datasets"][PRIMARY]["alone"]["net_usd"], n))}
    return out


def axis_table(rows: Dict[str, Dict[str, Any]], axis: str, ds: str = PRIMARY) -> Dict[str, Dict[str, Any]]:
    """Per level of one axis, over the flip variants: mean and median net
    alone, share above zero, and the legs' summed nets."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows.values():
        if not r["variant"]["flip"]:
            continue
        out.setdefault(str(r["variant"][axis]), []).append(r)        # type: ignore[arg-type]
    table = {}
    for level, rs in out.items():
        nets = [r["datasets"][ds]["alone"]["net_usd"] for r in rs]    # type: ignore[index]
        legs = {leg: round(sum(r["datasets"][ds]["legs_alone"][leg]["net_usd"] for r in rs) / len(rs), 2)
                for leg in ("short", "flip", "third")}
        table[level] = {"variants": len(rs), "mean_alone_usd": round(sum(nets) / len(nets), 2),
                        "median_alone_usd": round(statistics.median(nets), 2),
                        "above_zero": sum(1 for x in nets if x > EPS), "mean_leg_usd": legs,
                        "flip_trades": sum(r["datasets"][ds]["legs_alone"]["flip"]["n"] for r in rs),
                        "flip_wins": sum(r["datasets"][ds]["legs_alone"]["flip"]["wins"] for r in rs),
                        "short_trades": sum(r["datasets"][ds]["legs_alone"]["short"]["n"] for r in rs),
                        "short_wins": sum(r["datasets"][ds]["legs_alone"]["short"]["wins"] for r in rs)}
    return table


def without_night(trades: Sequence[Dict[str, Any]], start_ms: int, end_ms: int) -> float:
    """Net of the trades NOT entered inside [start_ms, end_ms): the rule was
    written from that night, so it is in-sample."""
    return round(sum(tr["net_usd"] for tr in trades if not start_ms <= tr["entry_ms"] < end_ms), 2)


def audit(names: Sequence[str], rows: Dict[str, Dict[str, Any]], by_name: Dict[str, "Variant"],
          datasets: Dict[str, Dict[str, Any]], night: Tuple[int, int]) -> Dict[str, Dict[str, Any]]:
    """What stands behind each named row (the ones that pass or lean):
    its net alone on every dataset, its legs beside live, the same variant
    with the other fill model, and its net beside live without the night
    the rule was written from."""
    out: Dict[str, Dict[str, Any]] = {}
    for n in names:
        v, row = by_name[n], rows[n]
        twin = dataclasses.replace(v, fill="tick" if v.fill == "resting" else "resting") if v.flip else None
        per = {}
        for ds, d in datasets.items():
            got = run_one(v, d, keep=True)
            tw = rows[twin.name]["datasets"][ds] if twin is not None and twin.name in rows else None
            per[ds] = {"alone_usd": got["alone"]["net_usd"], "alone_n": got["alone"]["n"],
                       "combined_usd": got["combined"]["net_usd"], "combined_n": got["combined"]["n"],
                       "combined_without_night_usd": without_night(got["trades_combined"], *night),
                       "alone_without_night_usd": without_night(got["trades_alone"], *night),
                       "legs_combined": {k: (x["n"], x["wins"], x["net_usd"]) for k, x in got["legs_combined"].items()},
                       "combined_midbar_worst_usd": round(sum(tr["net_usd"] for tr in walk(
                           d["series"], v, d["params"], d["params"].starting_equity, d["lo"], d["hi"], d["live"],
                           midbar_worst=True)["trades"]), 2),
                       "twin_combined_usd": tw["combined"]["net_usd"] if tw else None,
                       "twin_alone_usd": tw["alone"]["net_usd"] if tw else None}
        out[n] = {"verdict": row["judge"]["verdict"], "desk_fill": desk_fill(row), "datasets": per,
                  "twin": twin.name if twin else None,
                  "twin_verdict": rows[twin.name]["judge"]["verdict"] if twin is not None and twin.name in rows else None,
                  "alone_above_zero_everywhere": all(x["alone_usd"] > EPS for x in per.values()),
                  "without_night_above_zero_everywhere": all(
                      x["combined_without_night_usd"] > EPS for x in per.values()),
                  "midbar_worst_above_zero_everywhere": all(
                      x["combined_midbar_worst_usd"] > EPS for x in per.values())}
    return out


# ── tonight's replay ───────────────────────────────────────────────────────

def match_tonight(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Does a walk's trade list read like the operator's night: a short
    stopped near 92.925, a long that hits a target near 93.722, a third
    short opened near 93.808 and still open. Pure."""
    legs = {tr["leg"]: tr for tr in trades}
    op = {tr["leg"]: tr for tr in OPERATOR_TRADES}
    out: Dict[str, Any] = {}
    sh, fl, th = legs.get("short"), legs.get("flip"), legs.get("third")
    out["short"] = bool(sh and sh["reason"] == "stop"
                        and abs(sh["exit_px"] - op["short"]["exit_px"]) <= MATCH_TOL["short"])
    out["flip"] = bool(fl and fl["reason"] == "target"
                       and abs(fl["exit_px"] - op["flip"]["exit_px"]) <= MATCH_TOL["flip"])
    out["third"] = bool(th and th["reason"] == "open"
                        and abs(th["entry_px"] - op["third"]["entry_px"]) <= MATCH_TOL["third"])
    out["all"] = out["short"] and out["flip"] and out["third"] and len(trades) == 3
    return out


def _slim(trades: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("leg", "side", "entry", "entry_px", "stop_px", "target_px", "exit", "exit_px", "reason", "size",
            "margin_pct", "net_usd", "r")
    return [{k: (round(tr[k], 4) if isinstance(tr[k], float) else tr[k]) for k in keys} for tr in trades]


def replay_tonight(s: Series, p: Params, equity: float = REPLAY_EQUITY, start_ms: int = REPLAY_START,
                   end_ms: int = REPLAY_END) -> Dict[str, Any]:
    """The night of 2026-11-15/16 on `s`. "pinned": the operator's short
    forced in, every flip variant at the operator's sizes. "mechanical":
    the rule finds its own entries, over the whole grid at those sizes."""
    lo = bisect.bisect_left(s.t, start_ms)
    hi = bisect.bisect_left(s.t, end_ms) - 1
    if hi < lo or PIN_BAR not in s.t[lo:hi + 1]:
        return {"available": False}
    pp = dataclasses.replace(p, starting_equity=equity)
    forced = {PIN_BAR: PIN_SHORT}
    pinned, mech = [], []
    for v in variants():
        if v.size != "op" or not v.flip or v.third == "off":
            continue
        if v.x == X_GRID[0]:                       # X plays no part once the short is forced
            run = walk(s, v, pp, equity, lo, hi, forced=forced)
            pinned.append({"name": v.name, "match": match_tonight(run["trades"]), "trades": _slim(run["trades"]),
                           "net_usd": round(sum(tr["net_usd"] for tr in run["trades"]), 2)})
        run = walk(s, v, pp, equity, lo, hi)
        first = next((tr for tr in run["trades"] if tr["leg"] == "short" and tr["entry_ms"] >= PIN_BAR - 2 * HOUR_MS),
                     None)
        cyc = [tr for tr in run["trades"] if first is not None and tr["cycle"] == first["cycle"]]
        mech.append({"name": v.name, "match": match_tonight(cyc), "n_trades": len(run["trades"]),
                     "net_usd": round(sum(tr["net_usd"] for tr in run["trades"]), 2), "trades": _slim(run["trades"])})
    by_name = {r["name"]: r for r in pinned}
    pin_op = by_name.get(dataclasses.replace(OPERATOR_VARIANT, x=X_GRID[0]).name)
    return {"available": True, "window": [_iso(s.t[lo]), _iso(s.t[hi] + s.step_ms)], "equity": equity,
            "operator": list(OPERATOR_TRADES),
            "local_high_at_flip": {f"loc{n}": s.loc[n][bisect.bisect_left(s.t, PIN_BAR + HOUR_MS)] for n in LOC_HOURS},
            "pinned": {"variants": len(pinned), "match_all": sum(1 for r in pinned if r["match"]["all"]),
                       "match_short": sum(1 for r in pinned if r["match"]["short"]),
                       "match_flip": sum(1 for r in pinned if r["match"]["flip"]),
                       "matching": [r["name"] for r in pinned if r["match"]["all"]], "operator_variant": pin_op,
                       "rows": pinned},
            "mechanical": {"variants": len(mech), "match_all": sum(1 for r in mech if r["match"]["all"]),
                           "matching": [r["name"] for r in mech if r["match"]["all"]],
                           "operator_variant": next((r for r in mech if r["name"] == OPERATOR_VARIANT.name), None),
                           "primary_variant": next((r for r in mech if r["name"] == PRIMARY_VARIANT.name), None),
                           "net_positive": sum(1 for r in mech if r["net_usd"] > EPS),
                           "net_median_usd": round(statistics.median(r["net_usd"] for r in mech), 2)}}


# ── datasets ───────────────────────────────────────────────────────────────

def make_dataset(bars: Sequence[Any], step_ms: int, late: int, c15: Sequence[Any], c1h: Sequence[Any],
                 direction_at: Any, thesis_at: SL.ThesisAt, bp: Params, funding: Any, start_ms: int, end_ms: int
                 ) -> Dict[str, Any]:
    """One dataset on [start_ms, end_ms): the bar series (built on every
    bar given, so the rolling highs are warm) and the live long book."""
    prep = SL.prepare(c15, c1h, direction_at, thesis_at, bp, funding, start_ms, end_ms)
    series = build_series(SL.build_ticks([b for b in bars if b.t < end_ms], c1h, thesis_at, funding), step_ms, late)
    live = live_on(series.t, prep, bp.starting_equity)
    lo = bisect.bisect_left(series.t, start_ms)
    hi = len(series.t) - 1
    return {"series": series, "live": live, "lo": lo, "hi": hi, "params": bp, "intervals": prep["intervals"],
            "start_ms": start_ms, "end_ms": end_ms,
            "live_sum": {"trades": prep["live"]["trades"], "wins": prep["live"]["wins"],
                         "net_usd": prep["live"]["net_usd"],
                         "max_dd_pct": SL.max_dd_pct(live.eq[lo:hi + 1], bp.starting_equity)}}


_POOL_DS: Dict[str, Dict[str, Any]] = {}


def _pool_row(v: Variant) -> Dict[str, Any]:
    return make_row(v, _POOL_DS)


def evaluate(vs: Sequence[Variant], datasets: Dict[str, Dict[str, Any]], workers: int = 1, log: Any = None
             ) -> Dict[str, Dict[str, Any]]:
    """Every variant on every dataset, then the bar. Forked workers read
    the datasets from the parent; `workers=1` runs in-process."""
    rows: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    if workers > 1:
        _POOL_DS.clear()
        _POOL_DS.update(datasets)
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            for k, row in enumerate(pool.imap(_pool_row, vs, chunksize=16), 1):
                rows[row["name"]] = row
                if log and k % 500 == 0:
                    el = time.time() - t0
                    log(f"grid {k}/{len(vs)} ({k / len(vs) * 100:.0f}%), {el:.0f}s in, "
                        f"about {el / k * (len(vs) - k):.0f}s left")
    else:
        for v in vs:
            rows[v.name] = make_row(v, datasets)
    judge_rows(rows, vs)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    ap = argparse.ArgumentParser(
        description="Stop-and-reverse backtest on Brent and WTI: short from above the thesis long entry down to "
                    "it, flip long when the short is stopped (target the local high), optionally short again at "
                    "that target. Each variant alone and beside the live dip-buy book, plus a replay of the "
                    "operator's night of 2026-11-16. Research only: reads candles, the journal and the review "
                    "ledger; never trades or writes config.",
        epilog="Exit 0: a variant the desk can fill passes the bar. 1: none does. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else ""),
        help="READ ONLY: the live oil journal's theses (default: $PATHIEL_STATE_DIR/strategies/oil/theses)")
    ap.add_argument("--ledger", default=os.path.join(state, "review", "ledger.jsonl") if state else "",
                    help="READ ONLY: the review's trade ledger, for the measured fee")
    ap.add_argument("--cache-dir", default="/tmp/stop-reverse/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/stop-reverse")
    ap.add_argument("--extra-5m-glob", nargs="*", default=["/tmp/review/*/cache"] + (
        [os.path.join(state, "pool", "work", "*", "cache")] if state else []),
        help="READ ONLY: directories holding older <coin>__candles_5m_5000.json snapshots to merge in")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--no-wti", action="store_true", help="skip the WTI datasets")
    ap.add_argument("--detail", nargs="*", default=[], metavar="NAME", help="variants whose trades to keep")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} stop-reverse: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        c5 = IN.contiguous_from(IN.load_5m(coin, a.cache_dir, a.extra_5m_glob))
        end = min(c15[-1].t + FIFTEEN_MS, c5[-1].t + FIVE_MS)
        end -= end % FIFTEEN_MS
        return c5, c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, c15[-1].t + FIFTEEN_MS, a.cache_dir)

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    fees = IN.measured_fees(a.ledger)
    bp = dataclasses.replace(base_params(a.start_equity, SL.RISK_FRAC), leverage=LEVERAGE)
    sp = dataclasses.replace(bp, taker_fee=fees["taker"], maker_fee=fees["maker"])   # the sleeve's measured fees
    dir_at, th_at = journaled_direction(theses), SL.thesis_lookup(theses)
    first = min(th.created_at_ms for th in theses)
    warm = max(LOC_HOURS) * HOUR_MS
    datasets: Dict[str, Dict[str, Any]] = {}
    meta: Dict[str, Any] = {}

    def add(name: str, bars, step, late, c15, c1h, d_at, t_at, fund, end) -> None:
        start = max(first, bars[0].t + warm, c15[0].t)
        start += -start % FIFTEEN_MS
        ds = make_dataset(bars, step, late, c15, c1h, d_at, t_at, bp, fund, start, end)
        ds["params"] = sp
        datasets[name] = ds
        meta[name] = {"window": [_iso(start), _iso(end)], "bars": ds["hi"] - ds["lo"] + 1,
                      "days": round((end - start) / SL.DAY_MS, 1), "live": ds["live_sum"]}

    b5, b15, b1h, b_end, b_fund = load(BRENT)
    add(PRIMARY, b5, FIVE_MS, 1, b15, b1h, dir_at, th_at, b_fund, b_end)
    add("brent_15m", b15, FIFTEEN_MS, 0, b15, b1h, dir_at, th_at, b_fund, b_end)
    if not a.no_wti:
        w5, w15, w1h, w_end, w_fund = load(WTI)
        w_th = SL.thesis_lookup(theses, SL.price_ratio(theses, b1h, w1h))
        w_stop = min(b_end, w_end)
        add("wti_5m", w5, FIVE_MS, 1, w15, w1h, side_only(dir_at), w_th, w_fund, w_stop)
        add("wti_15m", w15, FIFTEEN_MS, 0, w15, w1h, side_only(dir_at), w_th, w_fund, w_stop)
    prim = datasets[PRIMARY]
    mid = prim["start_ms"] + (prim["end_ms"] - prim["start_ms"]) // 2
    mid -= mid % FIFTEEN_MS
    prim["mid_ms"] = mid
    vs = variants()
    log(f"{len(theses)} theses; fee {fees}; datasets {meta}; halves split at {_iso(mid)}; {len(vs)} variants; "
        f"{a.workers} workers. Follow: tail -f {os.path.join(a.out_dir, 'progress.log')}")
    rows = evaluate(vs, datasets, a.workers, log)
    names = list(datasets)
    fams = summarize_families(rows, names)
    by_name = {v.name: v for v in vs}
    wanted = list(dict.fromkeys([OPERATOR_VARIANT.name, PRIMARY_VARIANT.name]
                                + [f[k] for f in fams.values() for k in ("best", "best_desk_fill", "best_alone") if f.get(k)]
                                + [n for n in a.detail if n in by_name]))
    detail = {n: {ds: run_one(by_name[n], d, keep=True) for ds, d in datasets.items()} for n in wanted}
    replay = replay_tonight(prim["series"], sp)
    axes = {ax: axis_table(rows, ax) for ax in ("x", "sstop", "ltarget", "lstop", "fill", "third", "size")}
    passing = sorted(n for n, r in rows.items() if r["judge"]["verdict"] == "PASS")
    leaning = sorted(n for n, r in rows.items() if r["judge"]["verdict"] == "LEANING")
    passing_desk = [n for n in passing if desk_fill(rows[n])]
    checked = audit(passing + leaning, rows, by_name, datasets, (REPLAY_START, REPLAY_END))

    csv_path = os.path.join(a.out_dir, "variants.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        head = ["variant", "family", "verdict", "half1_usd", "half2_usd"]
        for ds in names:
            head += [f"{ds}_{k}" for k in ("alone_n", "alone_win_pct", "alone_net", "alone_avg_r", "alone_pf",
                                           "alone_dd_pct", "comb_n", "comb_net", "comb_dd_pct", "live_dd_pct")]
        w.writerow(head)
        for n in sorted(rows):
            r = rows[n]
            line = [n, r["family"], r["judge"]["verdict"], *r["halves"]]
            for ds in names:
                d = r["datasets"][ds]
                al, cb = d["alone"], d["combined"]
                line += [al["n"], al["win_rate"], al["net_usd"], al["avg_r"], al["profit_factor"],
                         al.get("max_dd_pct"), cb["n"], cb["net_usd"], d["combined_dd_pct"], d["live"]["max_dd_pct"]]
            w.writerow(line)
    rep = {"generated": _iso(int(time.time() * 1000)), "fees": fees, "meta": meta, "halves_split": _iso(mid),
           "variants": len(vs), "verdicts": dict(Counter(r["judge"]["verdict"] for r in rows.values())),
           "passing": passing, "passing_desk_fill": passing_desk, "leaning": leaning, "audit": checked,
           "verdicts_desk_fill": dict(Counter(r["judge"]["verdict"] for r in rows.values() if desk_fill(r))),
           "families": fams, "axes": axes, "replay": replay,
           "detail": detail, "rows": {n: rows[n] for n in wanted}, "csv": csv_path}
    rep_path = os.path.join(a.out_dir, "report.json")
    with open(rep_path, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    _print(rep, rows)
    log(f"done in {time.time() - t0:.0f}s: {rep['verdicts']}; report {rep_path}; csv {csv_path}")
    return 0 if passing_desk else 1


def _cell(d: Dict[str, Any]) -> str:
    al, cb = d["alone"], d["combined"]
    return (f"alone n={al['n']} win={al['win_rate']}% net={al['net_usd']:+.2f} R={al['avg_r']} "
            f"PF={al['profit_factor']} DD={al.get('max_dd_pct')}% | beside live n={cb['n']} "
            f"net={cb['net_usd']:+.2f} DD={d['combined_dd_pct']}% vs {d['live']['max_dd_pct']}%")


def _print(rep: Dict[str, Any], rows: Dict[str, Dict[str, Any]]) -> None:
    print(f"\nfees {rep['fees']}\nverdicts {rep['verdicts']}  on the desk's fill {rep['verdicts_desk_fill']}  "
          f"passing {len(rep['passing'])} (desk fill {len(rep['passing_desk_fill'])})  leaning {len(rep['leaning'])}")
    for n, a in rep["audit"].items():
        print(f" audit {n} [{a['verdict']}] desk_fill={a['desk_fill']} twin {a['twin_verdict']} alone>0 everywhere "
              f"{a['alone_above_zero_everywhere']} without the night {a['without_night_above_zero_everywhere']} "
              f"midbar worst {a['midbar_worst_above_zero_everywhere']}")
        for ds, x in a["datasets"].items():
            print(f"    {ds}: alone {x['alone_usd']:+.2f} (n {x['alone_n']}) beside {x['combined_usd']:+.2f} "
                  f"(n {x['combined_n']}) beside w/o night {x['combined_without_night_usd']:+.2f} midbar worst "
                  f"{x['combined_midbar_worst_usd']:+.2f} twin beside "
                  f"{x['twin_combined_usd']} legs {x['legs_combined']}")
    for ds, m in rep["meta"].items():
        print(f"  {ds}: {m['window']} {m['days']}d live {m['live']}")
    for fam, f in rep["families"].items():
        print(f"\n== {fam}: {f['label']}: {f['variants']} variants, desk fill {f['verdict']} "
              f"{f['verdicts_desk_fill']}, resting {f['verdict_resting']} {f['verdicts_resting']}; alone 10+ "
              f"trades {f['alone_judged']}, of those above zero on every dataset {f['alone_winners_every_dataset']}; "
              f"beside live above zero everywhere {f['combined_winners_every_dataset']}")
        for ds, p in f["per_dataset"].items():
            print(f"   {ds}: median alone {p['median_alone_usd']:+.2f} beside {p['median_combined_usd']:+.2f}; "
                  f"above zero alone {p['above_zero_alone']} beside {p['above_zero_combined']}; "
                  f"alone range {p['worst_alone_usd']:+.2f}..{p['best_alone_usd']:+.2f}")
        for key in ("best", "best_desk_fill", "best_alone"):
            r = rows[f[key]]
            print(f"   {key}: {r['name']} [{r['judge']['verdict']}] halves {r['halves']} alone {r['halves_alone']} "
                  f"legs { {k: (x['n'], x['net_usd']) for k, x in r['datasets'][PRIMARY]['legs_alone'].items()} }")
            for ds, d in r["datasets"].items():
                print(f"      {ds}: {_cell(d)}")
    for name in (OPERATOR_VARIANT.name, PRIMARY_VARIANT.name):
        r = rows[name]
        print(f"\n== {name} [{r['judge']['verdict']}] halves {r['halves']} alone {r['halves_alone']} "
              f"legs {r['judge']['legs']}")
        for ds, d in r["datasets"].items():
            print(f"      {ds}: {_cell(d)}\n         legs {d['legs_alone']} collisions {d['collisions']}")
    for ax, tab in rep["axes"].items():
        print(f"\naxis {ax} (flip variants, {PRIMARY}, alone):")
        for level, x in tab.items():
            print(f"   {level}: mean {x['mean_alone_usd']:+.2f} median {x['median_alone_usd']:+.2f} above zero "
                  f"{x['above_zero']}/{x['variants']} legs {x['mean_leg_usd']} flips {x['flip_wins']}/{x['flip_trades']} "
                  f"shorts {x['short_wins']}/{x['short_trades']}")
    rp = rep["replay"]
    if rp.get("available"):
        print(f"\nreplay {rp['window']} at ${rp['equity']}: local highs at the flip {rp['local_high_at_flip']}")
        pin, mech = rp["pinned"], rp["mechanical"]
        print(f" pinned: {pin['match_all']}/{pin['variants']} variants reproduce all three legs "
              f"(short {pin['match_short']}, flip {pin['match_flip']})")
        for tr in (pin["operator_variant"] or {}).get("trades", []):
            print(f"   {tr}")
        print(f" mechanical: {mech['match_all']}/{mech['variants']} reproduce; net above zero "
              f"{mech['net_positive']}, median {mech['net_median_usd']:+.2f}")
        for key in ("operator_variant", "primary_variant"):
            print(f"  {key}: {(mech[key] or {}).get('name')} net {(mech[key] or {}).get('net_usd')}")
            for tr in (mech[key] or {}).get("trades", []):
                print(f"   {tr}")


if __name__ == "__main__":
    raise SystemExit(main())
