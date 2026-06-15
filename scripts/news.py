#!/usr/bin/env python3
"""Free news-catalyst scan (our UW/Twitter "breaking headline" replacement).

Usage: python3 scripts/news.py "Iran peace deal"        # GDELT catalyst scan
       python3 scripts/news.py "Iran peace deal" --rss   # add RSS wire headlines
       python3 scripts/news.py NVDA --timespan 3h
"""
import sys
from hermes_trader.agents.news_catalyst import catalyst_scan, rss_headlines


def main():
    timespan = "1h"
    skip = set()
    if "--timespan" in sys.argv:
        i = sys.argv.index("--timespan")
        timespan = sys.argv[i + 1]
        skip = {i, i + 1}            # drop the flag AND its value from positionals
    args = [a for n, a in enumerate(sys.argv[1:], start=1)
            if not a.startswith("--") and n not in skip]
    query = " ".join(args) or "Iran"

    r = catalyst_scan(query, timespan=timespan)
    print(f"\n=== GDELT catalyst: '{query}' (last {timespan}) ===")
    if not r:
        print("  no GDELT response")
    else:
        flag = "  ⚡ BREAKING" if r.breaking else ""
        print(f"  {r.n_recent} articles | coverage surge {r.surge_x}x{flag}")
        if r.note:
            print(f"  {r.note}")
        for a in r.headlines[:10]:
            ts = a.seen.strftime("%H:%MZ") if a.seen else "  ?  "
            print(f"   [{ts}] {a.domain:20.20} {a.title[:90]}")

    if "--rss" in sys.argv:
        kws = args
        print(f"\n=== RSS wires (filtered: {kws or 'none'}) ===")
        for a in rss_headlines(keywords=kws, limit=12):
            ts = a.seen.strftime("%H:%MZ") if a.seen else "  ?  "
            print(f"   [{ts}] {a.source:20.20} {a.title[:90]}")


if __name__ == "__main__":
    main()
