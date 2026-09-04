#!/usr/bin/env python3
"""What kind of market is this, right now?

Written before any strategy, on purpose. Picking a hypothesis first and then
looking at the tape is how you end up testing the strategy that worked in the
regime you remember rather than the one you are in.

Source is `.state/.data_funding_oi.jsonl`, the data_logger's own panel: funding,
open interest, mark price and volume for ~280 Hyperliquid coins, roughly every
three hours since 2026-06-26. Nothing else here needs the network, and no
strategy in the book currently reads any of it.

    python research/regime_2026_09/regime_read.py
    python research/regime_2026_09/regime_read.py --days 7
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / ".state" / ".data_funding_oi.jsonl"
DAY_MS = 86_400_000
MAJORS = ("BTC", "ETH", "SOL", "HYPE", "XRP")


def load_panel() -> Tuple[List[int], Dict[str, Dict[int, dict]]]:
    """(sorted timestamps, coin -> ts -> row). One pass, no network."""
    stamps: List[int] = []
    panel: Dict[str, Dict[int, dict]] = defaultdict(dict)
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        snap = json.loads(line)
        ts = int(snap.get("ts") or 0)
        if not ts:
            continue
        stamps.append(ts)
        for row in snap.get("rows") or []:
            c = row.get("c")
            if c:
                panel[c][ts] = row
    return sorted(set(stamps)), panel


def _at(series: Dict[int, dict], ts: int, field: str) -> Optional[float]:
    row = series.get(ts)
    if not row:
        return None
    try:
        v = float(row.get(field))
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Return from b to a. None-safe, and refuses a zero base rather than
    producing an infinity that would poison every aggregate downstream."""
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def window(stamps: List[int], days: float) -> Tuple[int, int]:
    end = stamps[-1]
    start = min((s for s in stamps if s >= end - days * DAY_MS), default=stamps[0])
    return start, end


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="regime_read")
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args(argv)

    if not LOG.exists():
        print(f"no panel at {LOG}")
        return 1
    stamps, panel = load_panel()
    t0, t1 = window(stamps, a.days)
    span_d = (t1 - t0) / DAY_MS
    print(f"PATHIA REGIME READ — last {span_d:.1f} days "
          f"({time.strftime('%Y-%m-%d', time.localtime(t0/1000))} → "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(t1/1000))})")
    print(f"{len(panel)} coins in the panel, {len([s for s in stamps if s >= t0])} snapshots\n")

    # ── majors ──────────────────────────────────────────────────────────────
    print("MAJORS")
    print(f"  {'coin':<8}{'ret%':>9}{'OI Δ%':>9}{'funding/h':>12}{'read':>26}")
    for c in MAJORS:
        s = panel.get(c) or {}
        r = pct(_at(s, t1, "px"), _at(s, t0, "px"))
        oi = pct(_at(s, t1, "oi"), _at(s, t0, "oi"))
        f = _at(s, t1, "f")
        if r is None:
            continue
        # Price and OI moving together means new money in the direction of the
        # move. Price up on falling OI is a squeeze, not accumulation.
        if oi is None:
            read = ""
        elif r > 0 and oi > 0:
            read = "new longs"
        elif r > 0 and oi < 0:
            read = "short covering"
        elif r < 0 and oi > 0:
            read = "new shorts"
        else:
            read = "long liquidation"
        print(f"  {c:<8}{r:>+9.2f}{oi:>+9.2f}{(f or 0)*100:>11.4f}%{read:>26}")

    # ── breadth ─────────────────────────────────────────────────────────────
    rets, ois, fund = [], [], []
    for c, s in panel.items():
        r = pct(_at(s, t1, "px"), _at(s, t0, "px"))
        if r is None or abs(r) > 500:      # a 5x in a week is a listing artefact
            continue
        rets.append(r)
        o = pct(_at(s, t1, "oi"), _at(s, t0, "oi"))
        if o is not None and abs(o) < 1000:
            ois.append(o)
        f = _at(s, t1, "f")
        if f is not None:
            fund.append(f)
    if not rets:
        print("\nno usable returns in the window")
        return 1

    up = sum(1 for r in rets if r > 0)
    print(f"\nBREADTH   {up}/{len(rets)} coins up ({100*up/len(rets):.0f}%)")
    print(f"  median return   {st.median(rets):+.2f}%")
    print(f"  mean return     {st.mean(rets):+.2f}%")
    print(f"  dispersion      {st.pstdev(rets):.2f}%  (cross-sectional sd)")
    q = sorted(rets)
    print(f"  decile spread   {q[int(len(q)*0.9)]:+.1f}% top / {q[int(len(q)*0.1)]:+.1f}% bottom")

    print(f"\nPOSITIONING")
    print(f"  median OI change    {st.median(ois):+.2f}%" if ois else "  no OI")
    pos = sum(1 for f in fund if f > 0)
    print(f"  funding > 0         {pos}/{len(fund)} coins ({100*pos/len(fund):.0f}%)")
    print(f"  median funding/h    {st.median(fund)*100:.4f}%"
          f"   (annualised {st.median(fund)*24*365*100:+.1f}%)")
    base = sum(1 for f in fund if abs(f - 1.25e-05) < 1e-9)
    print(f"  pinned at baseline  {base}/{len(fund)} coins ({100*base/len(fund):.0f}%)")

    # ── what a strategy would have to survive ───────────────────────────────
    print(f"\nWHAT THIS MEANS FOR A NEW BOOK")
    med = st.median(rets)
    disp = st.pstdev(rets)
    if abs(med) < 1.0 and disp > 8:
        print("  Directionless and dispersed: the index is going nowhere while")
        print("  individual names move a lot. That is a cross-sectional regime —")
        print("  long/short against the median pays, outright direction does not.")
    elif med > 2:
        print("  Broad rally. Shorting anything into it is fighting the tape;")
        print("  a short book needs a very specific catalyst to survive.")
    elif med < -2:
        print("  Broad selloff. Long mean-reversion books get run over here.")
    else:
        print("  Mixed. No regime edge on direction alone.")
    if ois and st.median(ois) < -2:
        print("  OI is contracting: positions are being closed, not opened.")
        print("  Squeeze risk is falling and so is follow-through.")
    elif ois and st.median(ois) > 2:
        print("  OI is building: crowding is increasing, and so is squeeze risk.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
