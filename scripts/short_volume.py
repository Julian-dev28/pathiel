#!/usr/bin/env python3
"""Free FINRA short-volume report (our Unusual-Whales "short volume" replacement).

Usage: python3 scripts/short_volume.py HOOD
       python3 scripts/short_volume.py xyz:NVDA TSLA GME
"""
import sys
from hermes_trader.agents.short_volume import short_volume_signal


def main():
    for arg in sys.argv[1:] or ["HOOD"]:
        r = short_volume_signal(arg)
        if not r:
            print(f"{arg}: no free FINRA short-volume data (untracked symbol or weekend gap)")
            continue
        pct = ", ".join(f"{x*100:.0f}%" for x in r.series)
        print(f"\n=== {arg}  (symbol {r.symbol}, {r.date}) ===")
        print(f"  short volume ratio: {r.ratio*100:.1f}%  -> {r.regime}")
        print(f"  trend ({len(r.series)}d): {r.trend}   [{pct}]")
        if r.note:
            print(f"  {r.note}")


if __name__ == "__main__":
    main()
