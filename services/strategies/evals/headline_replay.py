"""Replay the 11-04 midterm drop: when would the price shock have woken the
desk, against when it actually reacted? Deterministic, no LLM calls; reads
public Hyperliquid 1h candles (scripts/oil_levels.fetch_candles).

Operator, 2026-11-05: "FIX THEM". On 2026-11-04 the midterm result took Brent
from 104.3 to 94.5 (about -9%). The desk's thesis rewrote at 10:25Z and
15:53Z and its stop printed at 13:37Z; nothing woke it for the headline
itself. services/intel/headline_watch.price_shock now alerts on a 1% mid
move inside 15 minutes (the headline we have not read yet). This eval asks
what that rule would have seen on the day, at the same threshold.

1h candles are coarse: the live rule reads the mid every minute. So two
bounds per bar, against the previous bar's close (the last mid the rule
would have held before the bar):
  - close to close: the move was certainly in by the bar's close. The rule
    fires NO LATER than that (a lower bound on how early).
  - the bar's high or low: price touched the threshold inside the bar, so the
    rule MAY have fired as early as some minute after the open (an upper
    bound; a 1h wick does not prove a 15-minute move).
Both are reported; the truth sits between them. The re-arm (one alert per
coin per 15 minutes) means every shock bar after the first counts too.

    python -m services.strategies.evals.headline_replay            # 2026-11-04, xyz:BRENTOIL
    python -m services.strategies.evals.headline_replay --day 2026-11-04 --coin xyz:BRENTOIL
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from services.intel import headline_watch as HW  # noqa: E402

HOUR_MS = 3_600_000

# What the desk actually did on 2026-11-04 (the journal and the fill log).
DESK_REACTIONS_2026_11_04 = (
    ("thesis rewrite", "2026-11-04T10:25Z"),
    ("stop filled", "2026-11-04T13:37Z"),
    ("thesis rewrite", "2026-11-04T15:53Z"),
)


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%MZ")


def _bars(candles: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    rows = [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"])}
            for c in candles]
    return sorted(rows, key=lambda r: r["t"])


def shock_bars(candles: Sequence[Dict[str, Any]], thr: float) -> List[Dict[str, Any]]:
    """Pure. Every 1h bar on which price_shock's threshold `thr` was crossed
    against the previous close. Per bar: `fired_by_ms` (the bar's close, when
    the close-to-close move is >= thr; None when only a wick crossed),
    `touched_in_bar` (high or low crossed), the moves, and the reference.
    Candles: Hyperliquid rows {t, o, h, l, c} (strings or floats), any order."""
    bars = _bars(candles)
    out = []
    for prev, bar in zip(bars, bars[1:]):
        ref = prev["c"]
        if not ref:
            continue
        close_move = (bar["c"] - ref) / ref
        ext = bar["l"] if abs(bar["l"] - ref) >= abs(bar["h"] - ref) else bar["h"]
        ext_move = (ext - ref) / ref
        by_close, in_bar = abs(close_move) >= thr, abs(ext_move) >= thr
        if not (by_close or in_bar):
            continue
        out.append({"bar_open_ms": bar["t"], "ref_close": ref, "close": bar["c"], "close_move": round(close_move, 4),
                    "extreme": ext, "extreme_move": round(ext_move, 4),
                    "fired_by_ms": bar["t"] + HOUR_MS if by_close else None, "touched_in_bar": in_bar})
    return out


def price_at(candles: Sequence[Dict[str, Any]], at_ms: int) -> Optional[float]:
    """Pure. The close of the last bar that CLOSED at or before `at_ms`, else
    the open of the bar that holds it (the best a 1h series can say)."""
    bars = _bars(candles)
    closed = [b for b in bars if b["t"] + HOUR_MS <= at_ms]
    if closed:
        return closed[-1]["c"]
    holding = [b for b in bars if b["t"] <= at_ms]
    return holding[-1]["o"] if holding else None


def replay(candles: Sequence[Dict[str, Any]], coin: str, reactions: Sequence[tuple],
           day_open_ms: int) -> Dict[str, Any]:
    """Pure. The shock bars at the coin's live threshold, the day's open and
    low, each actual reaction with price and move from the day's open, and
    the gap between the first shock and the first reaction."""
    thr = HW.PRICE_SHOCK[coin]
    shocks = shock_bars(candles, thr)
    bars = _bars(candles)
    day = [b for b in bars if day_open_ms <= b["t"] < day_open_ms + 24 * HOUR_MS]
    open_px = day[0]["o"] if day else None
    low = min((b["l"] for b in day), default=None)
    high = max((b["h"] for b in day), default=None)

    def move(px: Optional[float]) -> Optional[float]:
        return round((px - open_px) / open_px, 4) if px is not None and open_px else None

    rows = []
    for what, iso in reactions:
        at = int(datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
        px = price_at(candles, at)
        rows.append({"what": what, "at_ms": at, "price": px, "move_from_open": move(px)})
    first = next((s for s in shocks if s["fired_by_ms"] and s["fired_by_ms"] >= day_open_ms), None)
    first_reaction = min((r["at_ms"] for r in rows), default=None)
    lead_min = (round((first_reaction - first["fired_by_ms"]) / 60_000) if first and first_reaction else None)
    return {"coin": coin, "threshold": thr, "open": open_px, "low": low, "high": high,
            "move_open_to_low": move(low), "move_open_to_high": move(high), "shocks": shocks, "reactions": rows,
            "first_shock_fired_by_ms": first["fired_by_ms"] if first else None,
            "first_shock_move": first["close_move"] if first else None,
            "first_reaction_ms": first_reaction, "lead_minutes": lead_min}


def render(res: Dict[str, Any], day: str) -> str:
    thr = res["threshold"]
    lines = [f"{res['coin']} on {day}: open {res['open']:g}, low {res['low']:g} ({res['move_open_to_low']:+.1%}), "
             f"high {res['high']:g} ({res['move_open_to_high']:+.1%}). price_shock threshold {thr:.1%} in 15 min.",
             "1h candles: 'fired by' is the bar close (the move was in by then); 'touched' means a wick crossed "
             "the threshold inside the bar, so the live rule may have fired earlier in it.", "",
             f"{'bar':<7}{'ref close':>10}{'close':>9}{'move':>8}{'extreme':>9}{'move':>8}  fired by   touched"]
    for s in res["shocks"]:
        lines.append(f"{_utc(s['bar_open_ms']):<7}{s['ref_close']:>10g}{s['close']:>9g}{s['close_move']:>+8.2%}"
                     f"{s['extreme']:>9g}{s['extreme_move']:>+8.2%}  "
                     f"{_utc(s['fired_by_ms']) if s['fired_by_ms'] else 'wick only':<9}  "
                     f"{'yes' if s['touched_in_bar'] else 'no'}")
    if not res["shocks"]:
        lines.append("(no bar crossed the threshold)")
    lines += ["", f"{'desk reaction':<16}{'at':>7}{'price':>9}{'from open':>11}"]
    for r in res["reactions"]:
        px = f"{r['price']:g}" if r["price"] is not None else "?"
        mv = f"{r['move_from_open']:+.1%}" if r["move_from_open"] is not None else "?"
        lines.append(f"{r['what']:<16}{_utc(r['at_ms']):>7}{px:>9}{mv:>11}")
    if res["first_shock_fired_by_ms"]:
        lines += ["", f"First shock fired by {_utc(res['first_shock_fired_by_ms'])} ({res['first_shock_move']:+.2%} "
                  f"close to close); the desk's first reaction came {res['lead_minutes']} min later."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", default="2026-11-04", help="UTC day to replay")
    ap.add_argument("--coin", default="xyz:BRENTOIL")
    a = ap.parse_args(argv)
    spec = importlib.util.spec_from_file_location("oil_levels", os.path.join(ROOT, "scripts", "oil_levels.py"))
    OL = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(OL)
    day0 = datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_ms = int((day0 + timedelta(days=1)).timestamp() * 1000)
    candles = OL.fetch_candles(a.coin, "1h", 26, end_ms)      # two bars of the day before for the first reference
    if not candles:
        print(f"no 1h candles for {a.coin} on {a.day} (the venue keeps about 5000 bars)")
        return 1
    reactions = DESK_REACTIONS_2026_11_04 if a.day == "2026-11-04" else ()
    res = replay(candles, a.coin, reactions, int(day0.timestamp() * 1000))
    print(render(res, a.day))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
