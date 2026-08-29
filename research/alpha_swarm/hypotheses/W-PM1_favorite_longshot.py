"""W-PM1 — favorite-longshot bias on Polymarket: are longshots overpriced?

The classic prediction-market edge: markets priced at low probability (longshots) tend to
resolve NO more often than their price implies (overpriced), while favorites are underpriced.
If real, you systematically FADE longshots (buy NO on low-p markets / buy YES on high-p).

Test: pull RESOLVED markets, bin by the market's last YES price, compare the price (implied
prob) to the actual resolution rate (did YES win?). Calibration gap = the edge. Then compute
the EV of a simple rule: buy the favorite side (side priced > 0.5) at its price, hold to
resolution. Fees ~0 on Polymarket (gas only). Read-only, uses resolved outcomes as truth.
"""
from __future__ import annotations

import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from hermes_trader.client import polymarket_client as pm  # noqa: E402


def main():
    print("pulling resolved Polymarket markets (calibration truth)...")
    rows = []
    for page in range(6):                        # paginate for a bigger sample
        raw = pm._get(pm.GAMMA, "/markets",
                      {"limit": 500, "closed": "true", "order": "volume",
                       "ascending": "false", "offset": page * 500})
        batch = raw if isinstance(raw, list) else (raw or {}).get("data", [])
        if not batch:
            break
        for m in batch:
            p = pm.parse_market(m)
            if p and p["resolved"] and p["winner"] is not None and p["volume"] > 5000:
                rows.append(p)
        time.sleep(0.3)
    print(f"resolved markets w/ >$5k vol: {len(rows)}\n")
    if len(rows) < 50:
        print("too few resolved markets for a clean calibration read."); return

    # Calibration: bin by YES price, compare to actual YES-win rate.
    bins = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.4), (0.4, 0.6),
            (0.6, 0.75), (0.75, 0.9), (0.9, 1.01)]
    print(f"{'price bin':<14}{'n':<6}{'implied p':<11}{'actual win':<12}{'gap':<9}edge")
    print("-" * 62)
    for lo, hi in bins:
        b = [r for r in rows if lo <= r["yes_price"] < hi]
        if len(b) < 8:
            continue
        implied = st.fmean(r["yes_price"] for r in b)
        actual = st.fmean(r["winner"] for r in b)
        gap = actual - implied
        # longshots overpriced => actual < implied (gap negative) at low prices
        edge = "YES overpriced (fade)" if gap < -0.03 else ("YES underpriced (buy)" if gap > 0.03 else "~fair")
        print(f"{lo:.2f}-{hi:.2f}     {len(b):<6}{implied:<11.3f}{actual:<12.3f}{gap:+.3f}   {edge}")

    # Simple tradeable rule: buy the FAVORITE side (priced>0.5) at its price, hold to resolve.
    # payoff = 1 if that side won else 0; cost = its price. EV per $1 staked = win_rate/price - 1.
    fav = []
    for r in rows:
        fav_price = max(r["yes_price"], 1 - r["yes_price"])
        fav_is_yes = r["yes_price"] >= 0.5
        won = (r["winner"] == 1) if fav_is_yes else (r["winner"] == 0)
        if fav_price >= 0.99:                     # skip near-certain (no edge, no liquidity)
            continue
        fav.append((won, fav_price))
    if fav:
        roi = st.fmean((1.0 if w else 0.0) / p - 1.0 for w, p in fav)
        wr = st.fmean(1.0 if w else 0.0 for w, p in fav)
        avg_p = st.fmean(p for w, p in fav)
        print(f"\nRULE: buy the favorite (>0.5) at price, hold to resolution — n={len(fav)}")
        print(f"  favorite win rate {wr*100:.1f}%  avg price {avg_p:.3f}  ROI/trade {roi*100:+.2f}%")
        print("  (positive ROI = favorites underpriced = the tradeable side of the bias)")

    # And the longshot short: buy NO on low-price YES markets (price<0.25)
    ls = [r for r in rows if r["yes_price"] < 0.25]
    if len(ls) >= 8:
        # buy NO at (1 - yes_price); wins if YES lost
        roi = st.fmean((1.0 if r["winner"] == 0 else 0.0) / (1 - r["yes_price"]) - 1.0 for r in ls)
        print(f"\nLONGSHOT FADE: buy NO on YES<0.25 markets — n={len(ls)}  ROI/trade {roi*100:+.2f}%")


if __name__ == "__main__":
    main()
