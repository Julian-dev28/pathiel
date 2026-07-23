"""W-LF1 — should we lower the crypto liquidity floor to catch thin movers?

The live floor blocks coins under $0.70M/day (PURR, STBL, BRETT, ACE...). Question: do
momentum-long entries on those thin movers make money NET of the extra slippage a thin
book costs, or does the spread eat the edge? Test the actual blocked coins.

Rule: a coin closes a day up >= +8% -> LONG next open, hold {3,5}d, exit open. Compare
forward return net of a THIN-COIN slippage model (spread scales as volume shrinks) vs the
same on liquid names. If thin survives net, lower the floor; if not, the floor is correct.
Matched same-coin random-time null. Fetches candles (cached) for the blocked movers.
"""
from __future__ import annotations

import os
import random
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/julian_dev/Documents/code/hermes-trader")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "research" / "alpha_swarm" / "lib"))
import alpha_lib as A  # noqa: E402
from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

SEED = 20260723
O, H_, L_, C, V = 1, 2, 3, 4, 5
MOVE = 0.08          # +8% day = a mover
HOLDS = [3, 5]
FEE = 0.0025         # taker each side already; base

# The coins actually blocked by the floor this week + a liquid control set.
THIN = ["PURR", "STBL", "BRETT", "ACE", "HEMI", "WIF", "EIGEN", "CHIP", "OP", "NIL", "BLUR"]
LIQUID = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "LINK", "SUI", "LTC"]


def slippage_for(vol_usd_daily: float) -> float:
    """Round-trip slippage estimate as a fraction. Thin books have wide spreads; a small
    ($137) leg pays ~half-spread each side. Model: spread ~ clamp(60000/vol, 0.1%..3%).
    $5M coin -> ~1.2bps; $0.6M -> ~1.0%; $0.2M -> ~3% (capped)."""
    if vol_usd_daily <= 0:
        return 0.03
    spread = 60000.0 / vol_usd_daily
    return min(0.03, max(0.001, spread))


def daily(coin):
    cs = fetch_hl_candles(coin, "1d", 400)
    return [[b.t, float(b.o), float(b.h), float(b.l), float(b.c), float(b.v)] for b in (cs or [])[:-1]]


def mover_entries(bars, hold):
    """(entry_idx, fwd_return, day_dollar_vol) for each +MOVE day."""
    out = []
    for i in range(1, len(bars) - hold - 1):
        c0, c1 = bars[i - 1][C], bars[i][C]
        if c0 <= 0 or c1 <= 0:
            continue
        if c1 / c0 - 1.0 < MOVE:
            continue
        e, x = bars[i + 1][O], bars[i + 1 + hold][O]
        if e <= 0:
            continue
        dvol = bars[i][C] * bars[i][V]  # approx $ daily volume
        out.append((i, x / e - 1.0, dvol))
    return out


def run_bucket(coins, hold):
    rows = []
    for c in coins:
        bars = daily(c)
        time.sleep(0.2)
        if len(bars) < 40:
            continue
        for i, r, dvol in mover_entries(bars, hold):
            slip = slippage_for(dvol)
            rows.append({"coin": c, "gross": r, "net": r - FEE - slip,
                         "dvol": dvol, "slip": slip})
    return rows


def summarize(rows, label):
    if len(rows) < 8:
        return f"{label:<16} n={len(rows)} — too few"
    gross = st.fmean(r["gross"] for r in rows)
    net = st.fmean(r["net"] for r in rows)
    slip = st.fmean(r["slip"] for r in rows)
    win = 100 * sum(1 for r in rows if r["net"] > 0) / len(rows)
    med_vol = st.median(r["dvol"] for r in rows)
    return (f"{label:<16} n={len(rows):<3} gross {gross*100:+.2f}%  slip -{slip*100:.2f}%  "
            f"NET {net*100:+.2f}%  win {win:.0f}%  med_vol ${med_vol/1e6:.2f}M")


def main():
    print(f"crypto mover-long (+{MOVE*100:.0f}% day), forward hold, NET of thin-coin slippage\n")
    for hold in HOLDS:
        thin = run_bucket(THIN, hold)
        liq = run_bucket(LIQUID, hold)
        print(f"--- hold {hold}d ---")
        print("  " + summarize(thin, "THIN (blocked)"))
        print("  " + summarize(liq, "LIQUID (control)"))
        # verdict: does thin net beat 0 AND beat its own random-time null?
        if len(thin) >= 8:
            net = st.fmean(r["net"] for r in thin)
            rnd = random.Random(SEED)
            bycoin = {}
            for c in {r["coin"] for r in thin}:
                bycoin[c] = daily(c)
            from collections import Counter
            cnt = Counter(r["coin"] for r in thin)
            ge = 0
            for _ in range(2000):
                tot = []
                for c, k in cnt.items():
                    b = bycoin[c]
                    valid = [i for i in range(1, len(b) - hold - 1) if b[i + 1][O] > 0]
                    if len(valid) < k:
                        continue
                    for i in rnd.sample(valid, k):
                        dvol = b[i][C] * b[i][V]
                        tot.append(b[i + 1 + hold][O] / b[i + 1][O] - 1.0 - FEE - slippage_for(dvol))
                if tot and st.fmean(tot) >= net:
                    ge += 1
            p = (1 + ge) / 2001
            v = "LOWER THE FLOOR" if net > 0 and p < 0.05 else "KEEP THE FLOOR"
            print(f"  -> thin NET {net*100:+.2f}%/trade, null p={p:.4f}  => {v}")
        print()


if __name__ == "__main__":
    main()
