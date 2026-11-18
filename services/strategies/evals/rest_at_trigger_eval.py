"""Eval: market-only entries vs resting the trigger (operator, 2026-11-18).

"When the desk is flat and the live rule says it would enter at a price the
market has not reached yet... REST that exact bracket as a limit order now
instead of waiting for a 15-minute tick to catch the mid at the level. Same
entry price, same stop, same target, same sizing, same 2R gate: only the
mechanism changes, so a touch between ticks fills."

Replays the last 30 days of real xyz:BRENTOIL 15m candles (Hyperliquid's
public, read-only `/info` endpoint, no key) against the journaled oil
theses at `$PATHIEL_STATE_DIR/strategies/oil/theses` (read only; never
written here), and compares two entry mechanisms under the SAME live rule
(`services.strategies.next_trade.next_trade`: 24h range, 0.75 stop buffer,
2R minimum, leverage fit, never-chase, no-entry-after-a-break):

  (a) market_only   a market order only when the bar's OPEN price (the mid
                     a tick sampled every 15 minutes would read) already
                     satisfies the rule (`next_trade(...).ok`) -- the
                     system as it ran before 2026-11-18.
  (b) rest_at_trigger  everything (a) does, PLUS: when the rule is blocked
                     on distance alone (`next_trade(...).rest` is not
                     None), rest a limit at the trigger that can fill on
                     any INTRABAR touch of a later 15m candle (its low for
                     a long, its high for a short), not only at a sampled
                     tick. Two variants of (b):
                       before   the resting order ignores blackout windows
                                entirely once placed (the pre-fix behavior
                                the operator flagged: a touch during a
                                blackout fills).
                       after    `event_blackout.resting_entry_blackout`
                                cancels the resting order the bar before a
                                blackout opens or while one is active, and
                                it is free to re-rest once the window ends
                                (the shipped rule, `next_trade.
                                resting_entry_action`'s "cancel"/
                                "cancel_and_rest").

A NEW market or resting order is refused in both (a) and (b) alike while
`event_blackout.event_blackout` is active, exactly as `place_bracket` (via
the MCP server) already refuses one live -- that part is unchanged by this
feature and is not what "before vs after" compares.

Scope: a position's exit here is its stop or target ONLY -- this eval
compares the two ENTRY mechanisms, so both series close a trade the exact
same way; the live tick's extra close rules (a thesis invalidation on a
closed 1h candle, a `what_changes_my_mind` headline, the weekend flatten)
are not replayed, and apply identically to whichever mechanism opened the
trade, so they would not change the comparison between (a) and (b).

Reports, per series: trades taken, wins, losses, net R (the sum of each
trade's R multiple: -1.0 on a stop, the realized R on a target -- size-
and leverage-independent, so no equity or dollar P&L needs modelling), and
(rest_at_trigger only) how many of its fills landed inside an ACTIVE
blackout window.

    python3 -m services.strategies.evals.rest_at_trigger_eval \
        --theses-dir "$PATHIEL_STATE_DIR/strategies/oil/theses" \
        [--days 30] [--cache-dir /tmp/rest-at-trigger-eval/cache] [--out /tmp/rest-at-trigger-eval/report.json]

Network: only `fetch_candles_paged_cached`'s public Hyperliquid reads
(cached to disk after the first run); no key, no account, no MCP tool.
Reads the thesis journal read-only through `services.strategies.backtest.
data.load_journaled_theses`, never writes to it. The simulator itself
(`simulate`) is pure (no I/O); it is gate-tested offline on synthetic
candles in `services/strategies/evals/tests/test_rest_at_trigger_eval.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pathiel.agents import bracket as _bracket
from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection, load_journaled_theses
from services.strategies.event_blackout import event_blackout, resting_entry_blackout
from services.strategies.next_trade import LEVERAGE_FIT, RESTING_LEVEL_TOLERANCE, next_trade, stop_buffer_for

COIN = "xyz:BRENTOIL"
LEVERAGE = 20.0
COIN_MAX_LEVERAGE = 20.0
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
BAR_MS = 900_000       # 15m, the tick cadence
LOOKBACK_H = 48        # extra closed 1h history fetched before the window, for a real 24h range at t=0


@dataclass
class Trade:
    side: str
    entry_time: int
    entry_px: float
    stop_px: float
    tp_px: float
    exit_time: int
    exit_px: float
    exit_reason: str      # "stop" | "target"
    r: float
    source: str            # "market" | "rest"
    blackout_fill: bool = False


@dataclass
class Result:
    series: str
    trades: List[Trade] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.r > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.r <= 0)

    @property
    def net_r(self) -> float:
        return round(sum(t.r for t in self.trades), 4)

    @property
    def blackout_fills(self) -> int:
        return sum(1 for t in self.trades if t.blackout_fill)

    def summary(self) -> Dict[str, Any]:
        return {"series": self.series, "trades": self.n, "wins": self.wins, "losses": self.losses,
                "net_r": self.net_r, "blackout_fills": self.blackout_fills}


def active_thesis(theses: Sequence[ThesisDirection], t_ms: int) -> Optional[ThesisDirection]:
    """The journaled thesis in force at `t_ms`: the newest one created at or
    before it. None before the first thesis. Pure (theses is oldest-first,
    as `load_journaled_theses` returns it)."""
    chosen = None
    for th in theses:
        if th.created_at_ms <= t_ms:
            chosen = th
        else:
            break
    return chosen


def closed_1h_as_of(c1h: Sequence[Candle], t_ms: int) -> List[Candle]:
    """CLOSED 1h candles at `t_ms`: a bar closes HOUR_MS after it opens, the
    same "closed_only" discipline `services.strategies.levels` uses (a
    still-open bar's high/low must never anchor a stop). `c1h` must be
    sorted oldest-first."""
    return [c for c in c1h if c.t + HOUR_MS <= t_ms]


def range_24h(closed: Sequence[Candle]) -> Optional[Dict[str, float]]:
    last24 = closed[-24:]
    if not last24:
        return None
    return {"low": min(c.l for c in last24), "high": max(c.h for c in last24)}


def rule_at(side: Optional[str], mid: float, closed: Sequence[Candle], thesis_entry: Optional[float]) -> Dict[str, Any]:
    """next_trade() at `mid` off the CLOSED 1h candles known at this bar.
    `{"ok": False, "reason": ...}` when there is no side or no 24h range
    yet (the first day of the window, before 24 closed bars exist)."""
    if side not in ("long", "short"):
        return {"ok": False, "reason": "no thesis side"}
    r24 = range_24h(closed)
    if r24 is None:
        return {"ok": False, "reason": "fewer than 24 closed 1h candles"}
    buf = stop_buffer_for(COIN, None, mid)        # Brent's buffer is fixed; never reads ATR
    return next_trade(side, mid, r24["low"], r24["high"], thesis_entry=thesis_entry, leverage=LEVERAGE,
                      coin_max_leverage=COIN_MAX_LEVERAGE, stop_buffer=buf, leverage_fit=LEVERAGE_FIT)


def _offset_tp(rest: Dict[str, Any], side: str) -> float:
    """place_bracket applies TARGET_OFFSET_FRAC to a `rest`/market `tp_px`
    that is still structural; this mirrors that one step for the sim."""
    return round(_bracket.offset_target(rest["tp_px"], side, _bracket.TARGET_OFFSET_FRAC), 6)


def _check_exit(side: str, stop_px: float, tp_px: float, bar: Candle) -> Optional[tuple]:
    """(exit_px, "stop"|"target") if this bar's range reaches either level,
    stop checked first (conservative: a bar that reaches both inside one
    15m candle is scored as the loss). None: the position is still open."""
    if side == "long":
        if bar.l <= stop_px:
            return stop_px, "stop"
        if bar.h >= tp_px:
            return tp_px, "target"
    else:
        if bar.h >= stop_px:
            return stop_px, "stop"
        if bar.l <= tp_px:
            return tp_px, "target"
    return None


def _r_multiple(side: str, entry_px: float, stop_px: float, exit_px: float) -> float:
    risk = abs(entry_px - stop_px)
    if risk <= 0:
        return 0.0
    moved = (exit_px - entry_px) if side == "long" else (entry_px - exit_px)
    return round(moved / risk, 4)


def simulate(c15: Sequence[Candle], c1h: Sequence[Candle], theses: Sequence[ThesisDirection], *,
            variant: str) -> Result:
    """One full replay over `c15` (oldest first). `variant`:
      "market_only"       (a) -- market entries at the bar open only.
      "rest_before_fix"   (b), no blackout cancel -- a resting order can
                           fill inside an active blackout.
      "rest_after_fix"    (b), the shipped rule -- a resting order is
                           cancelled ahead of a blackout and re-rests once
                           it ends.
    Pure: no I/O, no clock (every timestamp comes from the candles)."""
    assert variant in ("market_only", "rest_before_fix", "rest_after_fix")
    uses_rest = variant != "market_only"
    cancels_for_blackout = variant == "rest_after_fix"

    result = Result(series=variant)
    position: Optional[Dict[str, Any]] = None
    resting: Optional[Dict[str, Any]] = None

    for bar in c15:
        t = bar.t
        closed = closed_1h_as_of(c1h, t)

        if position is not None:
            hit = _check_exit(position["side"], position["stop_px"], position["tp_px"], bar)
            if hit is not None:
                exit_px, reason = hit
                r = _r_multiple(position["side"], position["entry_px"], position["stop_px"], exit_px)
                result.trades.append(Trade(
                    side=position["side"], entry_time=position["entry_time"], entry_px=position["entry_px"],
                    stop_px=position["stop_px"], tp_px=position["tp_px"], exit_time=t, exit_px=exit_px,
                    exit_reason=reason, r=r, source=position["source"], blackout_fill=position["blackout_fill"]))
                position = None
            continue   # one decision per bar, same as the live tick

        th = active_thesis(theses, t)
        side = th.side if th is not None and th.side in ("long", "short") else None
        thesis_entry = th.entry_px if th is not None else None

        if uses_rest and resting is not None:
            if cancels_for_blackout and resting_entry_blackout(COIN, resting["side"], t) is not None:
                resting = None
            else:
                fill = (bar.l <= resting["entry_px"]) if resting["side"] == "long" else (bar.h >= resting["entry_px"])
                if fill:
                    was_blackout = event_blackout(COIN, t, side=resting["side"]) is not None
                    position = {**resting, "entry_time": t, "source": "rest", "blackout_fill": was_blackout}
                    resting = None
                    continue

        nt = rule_at(side, bar.o, closed, thesis_entry)
        blocked_by_calendar = side is not None and event_blackout(COIN, t, side=side) is not None

        if nt.get("ok") and not blocked_by_calendar:
            resting = None
            position = {"side": nt["side"], "entry_px": bar.o, "stop_px": nt["stop_px"], "tp_px": nt["tp_px"],
                        "entry_time": t, "source": "market", "blackout_fill": False}
            continue

        if uses_rest:
            rest = nt.get("rest")
            if rest is None or blocked_by_calendar:
                resting = None
            else:
                tp = _offset_tp(rest, side)
                if resting is None or resting["side"] != side or \
                        abs(resting["entry_px"] - rest["entry_px"]) > RESTING_LEVEL_TOLERANCE:
                    resting = {"side": side, "entry_px": rest["entry_px"], "stop_px": rest["stop_px"], "tp_px": tp}

    return result


# ------------------------------------------------------------------ I/O

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def load_window(days: int, cache_dir: str, now_ms: Optional[int] = None) -> Dict[str, List[Candle]]:
    """The last `days` of 15m candles plus LOOKBACK_H extra hours of 1h
    candles (so the window's first bars still see a real 24h range), via
    the public, cached, paged fetcher every other eval in this repo uses."""
    from services.strategies.backtest.data import fetch_candles_paged_cached
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    start_ms = now_ms - days * DAY_MS
    c15 = fetch_candles_paged_cached(COIN, "15m", start_ms, now_ms, cache_dir)
    c1h = fetch_candles_paged_cached(COIN, "1h", start_ms - LOOKBACK_H * HOUR_MS, now_ms, cache_dir)
    return {"c15": sorted(c15, key=lambda c: c.t), "c1h": sorted(c1h, key=lambda c: c.t),
           "start_ms": start_ms, "end_ms": now_ms}


def run(theses_dir: str, days: int, cache_dir: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
    win = load_window(days, cache_dir, now_ms)
    theses = load_journaled_theses(theses_dir)
    out: Dict[str, Any] = {
        "coin": COIN, "window": {"start": _iso(win["start_ms"]), "end": _iso(win["end_ms"]), "days": days},
        "bars_15m": len(win["c15"]), "bars_1h": len(win["c1h"]), "theses": len(theses),
        "thesis_sides": {s: sum(1 for t in theses if t.side == s) for s in ("long", "short", "flat")},
        "series": {},
    }
    for variant in ("market_only", "rest_before_fix", "rest_after_fix"):
        res = simulate(win["c15"], win["c1h"], theses, variant=variant)
        out["series"][variant] = res.summary()
        out["series"][variant]["trades_detail"] = [
            {"side": tr.side, "source": tr.source, "entry": _iso(tr.entry_time), "entry_px": tr.entry_px,
             "stop_px": tr.stop_px, "tp_px": tr.tp_px, "exit": _iso(tr.exit_time), "exit_px": tr.exit_px,
             "reason": tr.exit_reason, "r": tr.r, "blackout_fill": tr.blackout_fill}
            for tr in res.trades]
    a, b_before, b_after = out["series"]["market_only"], out["series"]["rest_before_fix"], out["series"]["rest_after_fix"]
    delta = round(b_after["net_r"] - a["net_r"], 4)
    out["verdict"] = {
        "extra_trades_from_resting": b_after["trades"] - a["trades"],
        "net_r_delta_vs_market_only": delta,
        "net_r_verdict": ("WORSE on net R: say so plainly" if delta < 0 else
                          "BETTER on net R" if delta > 0 else "TIED on net R"),
        "blackout_fills_before_fix": b_before["blackout_fills"],
        "blackout_fills_after_fix": b_after["blackout_fills"],
        "cancel_rule_removed_every_blackout_fill": b_after["blackout_fills"] == 0,
    }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theses-dir", default=None, help="default $PATHIEL_STATE_DIR/strategies/oil/theses, READ ONLY")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--cache-dir", default="/tmp/rest-at-trigger-eval/cache")
    ap.add_argument("--out", default="/tmp/rest-at-trigger-eval/report.json")
    a = ap.parse_args(argv)
    theses_dir = a.theses_dir or os.path.join(
        os.environ.get("PATHIEL_STATE_DIR", os.path.expanduser("~/.pathiel")), "strategies", "oil", "theses")
    os.makedirs(a.cache_dir, exist_ok=True)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    out = run(theses_dir, a.days, a.cache_dir)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps({"window": out["window"], "theses": out["theses"], "thesis_sides": out["thesis_sides"],
                      "series": {k: {kk: vv for kk, vv in v.items() if kk != "trades_detail"}
                                 for k, v in out["series"].items()},
                      "verdict": out["verdict"], "report": a.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
