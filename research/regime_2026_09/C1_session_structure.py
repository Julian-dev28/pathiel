#!/usr/bin/env python3
"""Cycle 1 — where does an xyz perp actually move?

WHY THIS QUESTION, NOW
The account trades tokenized equity perps ONLY (enable_crypto false,
2026-09-06). That is not crypto with different tickers. A xyz:HOOD perp tracks
an instrument whose cash market is open roughly 14:30-21:00 UTC and shut the
rest of the time, while the perp itself trades 24/7.

So the perp spends most of its life pricing an asset that is not trading. That
is a structural feature crypto does not have, no crypto strategy accounts for
it, and it is the sort of thing that is obvious once stated and invisible if
you port a crypto book across unchanged.

Before any hypothesis: measure where the movement and the risk actually live.
Picking a strategy first and then looking is how you end up testing the trade
you already believed in.

MEASURED, no positions taken
  - share of daily range that forms while the cash market is shut
  - drift and volatility, regular hours vs overnight vs weekend
  - whether the overnight move predicts the regular-hours move (cycle 2's
    question, framed here only as a correlation to see if it is worth asking)

Source: the data_logger panel. No network.

    python research/regime_2026_09/C1_session_structure.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / ".state" / ".data_funding_oi.jsonl"

# US cash equities, in UTC. 14:30-21:00 covers the regular session for most of
# the year; the DST boundary shifts it an hour and is not worth modelling at
# 3-hourly panel resolution — it would move a handful of observations between
# adjacent buckets and change no conclusion below.
RTH_START_H, RTH_END_H = 14, 21


def load_panel():
    panel: Dict[str, Dict[int, dict]] = defaultdict(dict)
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        snap = json.loads(line)
        ts = int(snap.get("ts") or 0)
        if not ts:
            continue
        for row in snap.get("rows") or []:
            if row.get("c"):
                panel[row["c"]][ts] = row
    return panel


def session_of(ts_ms: int) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if d.weekday() >= 5:
        return "weekend"
    return "rth" if RTH_START_H <= d.hour < RTH_END_H else "overnight"


def _px(row: Optional[dict]) -> Optional[float]:
    try:
        v = float((row or {}).get("px"))
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def main() -> int:
    panel = load_panel()
    xyz = {c: s for c, s in panel.items() if ":" in c}
    print("C1 — session structure of tokenized equity perps\n")
    print(f"  {len(xyz)} HIP-3 markets in the panel "
          f"({len(panel) - len(xyz)} native crypto, ignored)\n")

    # Per-interval returns, bucketed by the session the interval SAT IN.
    buckets: Dict[str, List[float]] = defaultdict(list)
    per_coin_overnight: Dict[str, List[float]] = defaultdict(list)
    per_coin_rth: Dict[str, List[float]] = defaultdict(list)

    for coin, series in xyz.items():
        ts = sorted(series)
        for a, b in zip(ts, ts[1:]):
            p0, p1 = _px(series[a]), _px(series[b])
            if p0 is None or p1 is None:
                continue
            gap_h = (b - a) / 3_600_000
            if gap_h > 6:
                continue          # a gap in the panel, not a session
            r = (p1 / p0 - 1) * 100
            if abs(r) > 60:
                continue          # listing artefact
            s = session_of(b)
            buckets[s].append(r)
            if s == "overnight":
                per_coin_overnight[coin].append(r)
            elif s == "rth":
                per_coin_rth[coin].append(r)

    print(f"  {'session':<12}{'n':>8}{'mean%':>9}{'median%':>10}{'vol%':>9}"
          f"{'|move|%':>10}{'share of vol':>14}")
    total_var = sum(st.pstdev(v) ** 2 * len(v) for v in buckets.values() if len(v) > 2)
    for s in ("rth", "overnight", "weekend"):
        v = buckets.get(s) or []
        if len(v) < 3:
            continue
        vol = st.pstdev(v)
        share = (vol ** 2 * len(v)) / total_var * 100 if total_var else 0
        print(f"  {s:<12}{len(v):>8}{st.mean(v):>+9.3f}{st.median(v):>+10.3f}"
              f"{vol:>9.2f}{st.mean(abs(x) for x in v):>10.2f}{share:>13.0f}%")

    print("\n  READ")
    rth, on = buckets.get("rth") or [], buckets.get("overnight") or []
    if rth and on:
        if st.pstdev(on) > st.pstdev(rth):
            print("  More variance forms while the cash market is SHUT than while it")
            print("  is open. The perp is doing its own price discovery overnight,")
            print("  which is exactly where a mean-reversion edge would live.")
        else:
            print("  Variance concentrates in regular hours, as the underlying's own")
            print("  tape would suggest. Overnight is quieter and thinner.")

    # Does the overnight move predict the next regular session? Reported as a
    # correlation only — this is cycle 2's actual question, and answering it
    # here would be fitting the follow-up to the sample that suggested it.
    pairs = []
    for coin, series in xyz.items():
        ts = sorted(series)
        for i in range(1, len(ts) - 1):
            if session_of(ts[i]) != "overnight" or session_of(ts[i + 1]) != "rth":
                continue
            p0, p1, p2 = _px(series[ts[i - 1]]), _px(series[ts[i]]), _px(series[ts[i + 1]])
            if None in (p0, p1, p2):
                continue
            a, b = (p1 / p0 - 1) * 100, (p2 / p1 - 1) * 100
            if abs(a) < 60 and abs(b) < 60:
                pairs.append((a, b))
    if len(pairs) > 30:
        n = len(pairs)
        mx = sum(a for a, _ in pairs) / n
        my = sum(b for _, b in pairs) / n
        sxy = sum((a - mx) * (b - my) for a, b in pairs)
        sxx = sum((a - mx) ** 2 for a, _ in pairs)
        syy = sum((b - my) ** 2 for _, b in pairs)
        r = sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0
        print(f"\n  overnight -> next regular-hours correlation: {r:+.3f} (n={n})")
        print("  Negative would mean the overnight move gives back at the open —")
        print("  a gap fade. Cycle 2 tests it properly; this is only whether the")
        print("  question is worth asking.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
