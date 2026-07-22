#!/usr/bin/env python
"""W-Y5 phase-2 fetch: hourly funding history for young-episode coins only.

Bounded: coins with an IN-SAMPLE listing (first bar inside the daily fetch
window) in the crypto / xyz_equity classes that have at least one young day
(age 2-59) above the $250k dvol floor. Window fetched: [first_bar, first_bar
+ 62d], paged (HL caps ~500 entries/response). Paced 0.35s/request.

Writes W-Y5_cache_funding.json {coin: [{time, fundingRate}, ...]}. Resumable.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.client.hl_client import fetch_funding_history  # noqa: E402

import importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("wy5lib", os.path.join(HERE, "W-Y5_lib.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

OUT = os.path.join(HERE, "W-Y5_cache_funding.json")
DAY_MS = 86_400_000


def targets():
    coins = L.load_daily()
    ws = L.window_start(coins)
    picks = []
    for coin, rows in coins.items():
        cls = L.asset_class(coin)
        if cls not in ("crypto", "xyz_equity"):
            continue
        if not L.listed_in_sample(rows, ws):
            continue
        has_young = any(rows[i][5] * rows[i][4] >= 250_000.0
                        for i in range(2, min(59, len(rows) - 2) + 1))
        if has_young:
            picks.append((coin, rows[0][0]))
    return picks


def fetch_window(coin, t0, t1):
    out, cur = [], t0
    for _ in range(12):                      # hard page cap
        rows = fetch_funding_history(coin, cur, t1)
        time.sleep(0.35)
        if not rows:
            break
        out.extend({"time": int(r["time"]),
                    "fundingRate": float(r["fundingRate"])} for r in rows)
        nxt = max(int(r["time"]) for r in rows) + 1
        if nxt >= t1 or nxt <= cur:
            break
        cur = nxt
    return out


def main():
    cache = json.load(open(OUT)) if os.path.exists(OUT) else {}
    picks = targets()
    print(f"funding targets: {len(picks)} coins")
    for k, (coin, t0) in enumerate(picks):
        if coin in cache:
            continue
        cache[coin] = fetch_window(coin, t0, t0 + 62 * DAY_MS)
        if (k + 1) % 10 == 0:
            with open(OUT, "w") as f:
                json.dump(cache, f)
            print(f"  [{k+1}/{len(picks)}] {coin}: {len(cache[coin])} pts",
                  flush=True)
    with open(OUT, "w") as f:
        json.dump(cache, f)
    print(f"funding cache: {len(cache)} coins -> {OUT}")


if __name__ == "__main__":
    main()
