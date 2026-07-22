#!/usr/bin/env python
"""W-Y5 O3 (news overlay) — the only testable slice: join the live
young_mover_short ledger (starts 2026-07-20) with the news_surge_short
ledger's same-coin-same-UTC-day rows (n_recent + headline titles), classify
polarity with the production classifier, and grade each young short forward
24h from its own signal hour off the 1h cache. Descriptive only — n is way
under any evidence bar; this exists so the overlay has a forward harness.

No orders, no writes outside this directory. Reads ledgers read-only.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.agents.mover_recorders import classify_news_polarity  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ST = "/Users/julian_dev/Documents/code/hermes-trader/.state/shadow_ledger"
HOURLY = json.load(open(os.path.join(HERE, "W-Y5_cache_blocked_1h.json")))
BPS = 25


def rows(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def day_of(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).date().isoformat()


def fwd24_short(coin, t_ms):
    bars = HOURLY.get(coin) or []
    e = next((i for i, b in enumerate(bars) if b[0] >= t_ms), None)
    if e is None or e + 24 >= len(bars) or bars[e][1] <= 0:
        return None
    return 1 - bars[e + 24 - 1][4] / bars[e][1] - 2 * BPS / 10_000.0


def main():
    young = rows(os.path.join(ST, "young_mover_short.jsonl"))
    news = {}
    for r in rows(os.path.join(ST, "news_surge_short.jsonl")):
        key = (r["coin"], day_of(r["ts"]))
        meta = r.get("meta") or {}
        titles = " | ".join(meta.get("top3_titles") or [])
        news[key] = {"n_recent": meta.get("n_recent", 0), "titles": titles}

    buckets = {"negative": [], "positive": [], "neutral": [], "no_news_row": []}
    print(f"{'coin':<14} {'day':<11} {'news':>4} {'polarity':<9} {'fwd24 short net':>15}")
    for r in sorted(young, key=lambda x: x["ts"]):
        key = (r["coin"], day_of(r["ts"]))
        j = news.get(key)
        if j is None or not j["titles"]:
            pol = "no_news_row"
        else:
            pol, _src = classify_news_polarity(None, j["titles"])
        f = fwd24_short(r["coin"], r["ts"])
        buckets[pol].append(f)
        print(f"{r['coin']:<14} {key[1]:<11} "
              f"{(j or {}).get('n_recent', 0):>4} {pol:<9} "
              f"{'unresolved' if f is None else format(f*100, '+.2f')+'%':>15}")
    print("\npolarity buckets (resolved only):")
    for k, vs in buckets.items():
        vs = [v for v in vs if v is not None]
        if vs:
            print(f"  {k:<12} n={len(vs):>2} mean={sum(vs)/len(vs)*100:+.2f}%")
        else:
            print(f"  {k:<12} n= 0")


if __name__ == "__main__":
    main()
