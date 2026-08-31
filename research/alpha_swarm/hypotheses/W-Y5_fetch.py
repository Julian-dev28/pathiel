#!/usr/bin/env python
"""W-Y5: fetch + cache for the young-listing EXPANSION study.

Caches (written next to this file; downstream scripts NEVER re-fetch):
  W-Y5_cache_universe.json   [{coin, dex, dayNtlVlm, ...}] perp universe snapshot
  W-Y5_cache_daily.json      {coin: [[t,o,h,l,c,v], ...]}  1d, up to 400 bars,
                             EVERY perp (native crypto + every HIP-3 dex)
  W-Y5_cache_blocked_1h.json {coin: [[t,o,h,l,c,v], ...]}  1h, 900 bars, only
                             coins seen in history/liquidity preflight blocks
                             in logs/trading_loop.log (PIT cohort forward legs)

Pacing: 0.35s/request + 2s every 15 — ~150 req/min worst case, far under the
HL 1200/min weight budget the live loop shares. Resumable: reruns skip coins
already cached (delete a coin key to force a refetch).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, REPO)

from pathia.client.hl_client import fetch_hl_candles  # noqa: E402
from pathia.client.universe import get_universe  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UNI_CACHE = os.path.join(HERE, "W-Y5_cache_universe.json")
DAILY_CACHE = os.path.join(HERE, "W-Y5_cache_daily.json")
HOURLY_CACHE = os.path.join(HERE, "W-Y5_cache_blocked_1h.json")
LOG = os.path.join(REPO, "logs", "trading_loop.log")

_BLOCK_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2},\d+ INFO:__main__:(.+?): "
    r"pre-research (history_floor_preflight|liquidity_floor_preflight)")


def blocked_coins_from_log():
    coins = set()
    with open(LOG, errors="replace") as fh:
        for line in fh:
            m = _BLOCK_RE.match(line)
            if m:
                coins.add(m.group(2))
    return sorted(coins)


def rows(candles):
    return [[c.t, c.o, c.h, c.l, c.c, c.v] for c in candles]


def paced_fetch(targets, cache_path, interval, count):
    cache = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path))
    done = 0
    for i, coin in enumerate(targets):
        if coin in cache:
            continue
        try:
            cache[coin] = rows(fetch_hl_candles(coin, interval, count))
        except Exception as exc:
            print(f"  WARN {coin}: {exc}")
            cache[coin] = []
        done += 1
        if done % 15 == 0:
            with open(cache_path, "w") as f:
                json.dump(cache, f)
            print(f"  [{i+1}/{len(targets)}] {coin}: {len(cache[coin])} bars "
                  f"({interval})", flush=True)
            time.sleep(2.0)
        else:
            time.sleep(0.35)
    with open(cache_path, "w") as f:
        json.dump(cache, f)
    return cache


def main():
    uni = get_universe(include_hip3=True)
    perps = [m for m in uni if m.get("type", "perp") == "perp"]
    with open(UNI_CACHE, "w") as f:
        json.dump(perps, f)
    coins = [m["coin"] for m in perps]
    n_hip3 = sum(1 for c in coins if ":" in c)
    print(f"universe: {len(coins)} perps ({n_hip3} HIP-3, "
          f"{len(coins) - n_hip3} native crypto)")

    daily = paced_fetch(coins, DAILY_CACHE, "1d", 400)
    print(f"daily cache: {len(daily)} coins -> {DAILY_CACHE}")

    blocked = blocked_coins_from_log()
    print(f"log-blocked cohort: {len(blocked)} coins")
    hourly = paced_fetch(blocked, HOURLY_CACHE, "1h", 900)
    print(f"hourly cache: {len(hourly)} coins -> {HOURLY_CACHE}")


if __name__ == "__main__":
    main()
