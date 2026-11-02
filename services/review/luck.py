"""Luck check: was a trade's outcome the timing, or just the day?

For one closed trade, take `N_DRAWS` (500) random entry times on the same
UTC day, same side, same stop distance (1R in price), same target distance
and the same holding time, and walk each one on 5m bars: entry at a bar's
open; stop or target on the first bar that touches it (stop first when one
bar touches both: the conservative read); otherwise out at the close of the
last bar inside the holding time. Each draw's outcome is in R of price, no
fees, stops filled at the stop price.

The real trade is walked the same way from its own fill (its price, from
the first whole bar after the fill), so slippage, top-ups and a manual exit
do not count as timing. `luck_pct` is where that walk sits among the draws
(0-100, ties count half):

  ~50     the result was the day's market; any entry that day did as well
  >= 80   the timing beat most random entries that day
  <= 20   the timing was worse than most random entries that day

Seeded per trade (crc32 of its id), so the same trade always gets the same
draws. Pure on its inputs.
"""
from __future__ import annotations

import random
from bisect import bisect_left
from typing import Any, Dict, Optional, Sequence

from pathiel.models.types import Candle

from services.review import stats as S

N_DRAWS = 500
DAY_MS = 86_400_000
FIVE_MS = 300_000


def walk(bars: Sequence[Candle], i0: int, is_long: bool, r_unit: float, target_dist: Optional[float],
         end_ms: int, entry_px: Optional[float] = None) -> Optional[float]:
    """One bracket from bars[i0]'s open (or from `entry_px`, starting on
    bars[i0]) until `end_ms`: the outcome in R. None when no bar is inside."""
    if i0 >= len(bars) or r_unit <= 0:
        return None
    entry = bars[i0].o if entry_px is None else float(entry_px)
    sign = 1.0 if is_long else -1.0
    stop = entry - sign * r_unit
    tgt = entry + sign * target_dist if target_dist else None
    last = None
    for b in bars[i0:]:
        if b.t >= end_ms:
            break
        last = b
        if b.l <= stop if is_long else b.h >= stop:
            return -1.0
        if tgt is not None and (b.h >= tgt if is_long else b.l <= tgt):
            return round(target_dist / r_unit, 4)
    if last is None:
        return None
    return sign * (last.c - entry) / r_unit


def luck_check(trade: Dict[str, Any], bars5: Sequence[Candle], n_draws: int = N_DRAWS) -> Dict[str, Any]:
    """`luck_pct`, the real entry's walked R (`luck_actual_r`), the draws'
    median R and how many draws ran, for one closed trade (needs id,
    entry_time, exit_time, side, entry_px, stop_px; target_px optional).
    Empty fields when the day has no bars or the trade has no stop."""
    out: Dict[str, Any] = {"luck_pct": None, "luck_actual_r": None, "luck_median_r": None, "luck_n": 0}
    stop, entry = trade.get("stop_px"), trade.get("entry_px")
    if not stop or not entry or trade.get("exit_time") is None:
        return out
    r_unit = abs(float(entry) - float(stop))
    tgt = trade.get("target_px")
    target_dist = abs(float(tgt) - float(entry)) if tgt else None
    t0 = int(trade["entry_time"])
    hold = max(FIVE_MS, int(trade["exit_time"]) - t0)
    day0 = t0 - t0 % DAY_MS
    bars = sorted(bars5, key=lambda b: b.t)
    ts = [b.t for b in bars]
    lo, hi = bisect_left(ts, day0), bisect_left(ts, day0 + DAY_MS)
    if hi <= lo:
        return out
    is_long = trade["side"] == "long"
    actual = walk(bars, bisect_left(ts, t0 - t0 % FIVE_MS + FIVE_MS), is_long, r_unit, target_dist, t0 + hold,
                  entry_px=float(entry))
    if actual is None:
        return out
    rng = random.Random(S.sub_seed(S.SEED, str(trade.get("id") or t0)))
    draws = []
    for _ in range(n_draws):
        i0 = rng.randrange(lo, hi)
        r = walk(bars, i0, is_long, r_unit, target_dist, bars[i0].t + hold)
        if r is not None:
            draws.append(r)
    if not draws:
        return out
    out["luck_pct"] = round(S.percentile_of(actual, draws), 1)
    out["luck_actual_r"] = round(actual, 2)
    out["luck_median_r"] = round(S.quantile(sorted(draws), 0.5), 2)
    out["luck_n"] = len(draws)
    return out
