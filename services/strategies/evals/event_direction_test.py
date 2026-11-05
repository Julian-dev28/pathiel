"""What-if: trade a direction through scheduled events instead of sitting out.

Operator, 2026-11-05: "what if we shorted 10% until bullish again?" then
"no one said to short just to see what the results are". This answers it on
real Brent and silver 1h candles; it changes nothing live.

    python3 -m services.strategies.evals.event_direction_test [--days 120]

For every past event that blocks entries (impact 3 in
services/intel/scheduled_events.py, the generated FOMC/CPI/OPEC+, and the
weekly EIA report on Brent), it opens at the blackout's start (the last price
the desk could trade before the window) and closes `--hold-h` hours after the
event (default 24). Size: 10% of equity as margin at 20x, so equity moves
2 x the price move. No stop: this measures the event's direction, not a
strategy. Rows: short, long, and the blackout (flat, 0).

Read only. Public Hyperliquid candles.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MARGIN_FRAC = 0.10
LEVERAGE = 20.0
EQUITY_MULT = MARGIN_FRAC * LEVERAGE        # 2x equity notional


def _close_at(bars: Sequence[Dict[str, Any]], t_ms: int) -> Optional[float]:
    """Close of the last 1h bar that ended at or before t_ms (bars: oldest
    first, HL rows with t (open ms) and c)."""
    best = None
    for b in bars:
        if int(b["t"]) + HOUR_MS <= t_ms:
            best = float(b["c"])
        else:
            break
    return best


def score_event(bars: Sequence[Dict[str, Any]], open_ms: int, close_ms: int) -> Optional[Dict[str, float]]:
    """Price move from open_ms to close_ms, and what a 10% x 20x short and
    long would have done to equity. None when the candles do not cover it."""
    p0, p1 = _close_at(bars, open_ms), _close_at(bars, close_ms)
    if p0 is None or p1 is None or not bars or int(bars[-1]["t"]) + HOUR_MS < close_ms:
        return None
    move = (p1 - p0) / p0
    return {"p0": p0, "p1": p1, "move_pct": round(100 * move, 2),
            "short_equity_pct": round(-100 * move * EQUITY_MULT, 2),
            "long_equity_pct": round(100 * move * EQUITY_MULT, 2)}


def past_events(coin: str, start_ms: int, end_ms: int) -> List[Tuple[str, int, int]]:
    """(title, blackout start ms, event ms) for every blocking event in range."""
    from services.intel import scheduled_events as S
    from services.strategies.backtest.oil_calendar import eia_report_times
    from services.strategies.event_blackout import BEFORE_H, OIL_COINS
    out = []
    for r in S.market_events(start_ms, end_ms, coin):
        if r["window"] and r["at_ms"] < end_ms:
            out.append((r["title"], r["window"]["start_ms"], r["at_ms"]))
    if coin in OIL_COINS:
        for rep_ms, _why in eia_report_times(start_ms, end_ms):
            out.append(("EIA weekly report", rep_ms - int(BEFORE_H * HOUR_MS), rep_ms))
    return sorted(out, key=lambda e: e[2])


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    shorts = [r["short_equity_pct"] for r in rows]
    return {"n": len(rows), "short_wins": sum(1 for x in shorts if x > 0),
            "short_total_pct": round(sum(shorts), 2), "long_total_pct": round(-sum(shorts), 2),
            "short_worst_pct": min(shorts), "short_best_pct": max(shorts),
            "blackout_total_pct": 0.0}


def run(coin: str, bars: Sequence[Dict[str, Any]], hold_h: float, start_ms: int, end_ms: int) -> Dict[str, Any]:
    rows = []
    for title, open_ms, ev_ms in past_events(coin, start_ms, end_ms):
        sc = score_event(bars, open_ms, ev_ms + int(hold_h * HOUR_MS))
        if sc is None:
            continue
        rows.append({"event": title, "at": datetime.fromtimestamp(ev_ms / 1000, tz=timezone.utc)
                     .strftime("%Y-%m-%d %H:%MZ"), **sc})
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_kind.setdefault("EIA weekly" if r["event"].startswith("EIA") else "other big events", []).append(r)
    return {"coin": coin, "hold_h": hold_h, "rows": rows, "all": summarize(rows),
            "by_kind": {k: summarize(v) for k, v in by_kind.items()}}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--hold-h", type=float, default=24.0)
    a = ap.parse_args(argv)
    sys.path.insert(0, "scripts")
    import oil_levels
    now = int(time.time() * 1000)
    start = now - a.days * DAY_MS
    out = {}
    for coin in ("xyz:BRENTOIL", "xyz:SILVER"):
        bars = oil_levels.fetch_candles(coin, "1h", a.days * 24 + 48, now)
        bars = sorted(bars, key=lambda b: int(b["t"]))
        first = int(bars[0]["t"]) if bars else now
        out[coin] = run(coin, bars, a.hold_h, max(start, first), now - int(a.hold_h * HOUR_MS))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
