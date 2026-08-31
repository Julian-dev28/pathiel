#!/usr/bin/env python
"""W-Y0: fetch + cache xyz-dex daily history (Task 1) and 1h history for
log-blocked coins (Task 2 counterfactual). Run once; downstream scripts read
the JSON caches only (SWARM-RULES: never re-fetch in analysis scripts).

Caches written next to this file:
  W-Y_cache_xyz_daily.json   {coin: [[t,o,h,l,c,v], ...]}  (1d, up to 400 bars)
  W-Y_cache_blocked_1h.json  {coin: [[t,o,h,l,c,v], ...]}  (1h, up to 400 bars)
  W-Y_cache_universe.json    [{coin, dayNtlVlm, ...}] for xyz coins (snapshot)
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pathia.client.hl_client import fetch_hl_candles  # noqa: E402
from pathia.client.universe import get_universe  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY_CACHE = os.path.join(HERE, "W-Y_cache_xyz_daily.json")
HOURLY_CACHE = os.path.join(HERE, "W-Y_cache_blocked_1h.json")
UNI_CACHE = os.path.join(HERE, "W-Y_cache_universe.json")

# Coins seen in history_floor_preflight log lines (Task 2). Recomputed by the
# counterfactual script from the log; listed here only to fetch 1h data.
BLOCKED_COINS = [
    "xyz:ARM", "xyz:BB", "xyz:BE", "xyz:BOT", "xyz:CBRS", "xyz:DELL",
    "xyz:DRAM", "xyz:KIOXIA", "xyz:LITE", "xyz:MINIMAX", "xyz:MRVL",
    "xyz:NBIS", "xyz:NOK", "xyz:PURRDAT", "xyz:QNT", "xyz:RKLB",
    "xyz:SHAZ", "xyz:SPCX", "xyz:STRC", "xyz:WDC", "xyz:ZHIPU",
]


def candles_to_rows(candles):
    return [[c.t, c.o, c.h, c.l, c.c, c.v] for c in candles]


def main() -> None:
    uni = get_universe(include_hip3=True)
    xyz = [m for m in uni if m["coin"].startswith("xyz:")]
    print(f"universe: {len(uni)} markets, {len(xyz)} on xyz dex")
    with open(UNI_CACHE, "w") as f:
        json.dump(xyz, f)

    daily = {}
    if os.path.exists(DAILY_CACHE):
        daily = json.load(open(DAILY_CACHE))
    for i, m in enumerate(xyz):
        coin = m["coin"]
        if coin in daily:
            continue
        rows = candles_to_rows(fetch_hl_candles(coin, "1d", 400))
        daily[coin] = rows
        print(f"[{i+1}/{len(xyz)}] {coin}: {len(rows)} daily bars")
        if (i + 1) % 5 == 0:
            with open(DAILY_CACHE, "w") as f:
                json.dump(daily, f)
            time.sleep(1.0)  # modest batch pacing
        else:
            time.sleep(0.25)
    with open(DAILY_CACHE, "w") as f:
        json.dump(daily, f)
    print(f"daily cache: {len(daily)} coins -> {DAILY_CACHE}")

    hourly = {}
    if os.path.exists(HOURLY_CACHE):
        hourly = json.load(open(HOURLY_CACHE))
    for i, coin in enumerate(BLOCKED_COINS):
        if coin in hourly:
            continue
        rows = candles_to_rows(fetch_hl_candles(coin, "1h", 400))
        hourly[coin] = rows
        print(f"[{i+1}/{len(BLOCKED_COINS)}] {coin}: {len(rows)} hourly bars")
        time.sleep(0.4)
    with open(HOURLY_CACHE, "w") as f:
        json.dump(hourly, f)
    print(f"hourly cache: {len(hourly)} coins -> {HOURLY_CACHE}")


if __name__ == "__main__":
    main()
