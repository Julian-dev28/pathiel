#!/usr/bin/env python
"""W-Y4 step 2: fetch + cache everything the regime backtest needs. Run once;
the analysis script reads caches only (SWARM-RULES). Gentle on the API:
~100 paced calls total, 0.35s spacing.

Caches written next to this file:
  W-Y4_cache_1h.json       {coin: [[t,o,h,l,c,v],...]} cohort coins, 1h,
                           700 bars (~29d) merged with W-Y_cache_blocked_1h
  W-Y4_cache_index.json    {"1h": {proxy: rows}, "1d": {proxy: rows}} for
                           xyz:SP500 (EQUITY_PROXY), xyz:XYZ100, BTC
  W-Y4_cache_funding.json  {coin: [{time, fundingRate}, ...]} cohort coins,
                           paged fundingHistory from 2026-06-26
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hermes_trader.client.hl_client import (  # noqa: E402
    fetch_funding_history, fetch_hl_candles,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EPISODES = json.load(open(os.path.join(HERE, "W-Y4_episodes.json")))
OLD_1H = os.path.join(HERE, "W-Y_cache_blocked_1h.json")
OUT_1H = os.path.join(HERE, "W-Y4_cache_1h.json")
OUT_IDX = os.path.join(HERE, "W-Y4_cache_index.json")
OUT_FUND = os.path.join(HERE, "W-Y4_cache_funding.json")

PACE_S = 0.35
FUND_START_MS = 1782432000000  # 2026-06-26 00:00 UTC


def rows_of(candles):
    return [[c.t, c.o, c.h, c.l, c.c, c.v] for c in candles]


def merge(old, new):
    by_t = {r[0]: r for r in old}
    by_t.update({r[0]: r for r in new})
    return [by_t[t] for t in sorted(by_t)]


def main():
    coins = sorted({e["coin"] for e in EPISODES})
    print(f"cohort coins: {len(coins)}")

    # -- 1h candles for cohort coins ----------------------------------------
    old = json.load(open(OLD_1H)) if os.path.exists(OLD_1H) else {}
    cache = json.load(open(OUT_1H)) if os.path.exists(OUT_1H) else {}
    for i, coin in enumerate(coins):
        if coin in cache:
            continue
        rows = rows_of(fetch_hl_candles(coin, "1h", 700))
        cache[coin] = merge(old.get(coin, []), rows)
        print(f"[1h {i+1}/{len(coins)}] {coin}: {len(rows)} fetched, "
              f"{len(cache[coin])} merged")
        if (i + 1) % 5 == 0:
            with open(OUT_1H, "w") as f:
                json.dump(cache, f)
        time.sleep(PACE_S)
    with open(OUT_1H, "w") as f:
        json.dump(cache, f)
    print(f"1h cache: {len(cache)} coins -> {OUT_1H}")

    # -- index proxies -------------------------------------------------------
    idx = json.load(open(OUT_IDX)) if os.path.exists(OUT_IDX) else {"1h": {}, "1d": {}}
    for proxy in ("xyz:SP500", "xyz:XYZ100", "BTC"):
        if proxy not in idx["1h"]:
            idx["1h"][proxy] = rows_of(fetch_hl_candles(proxy, "1h", 900))
            print(f"[idx 1h] {proxy}: {len(idx['1h'][proxy])} bars")
            time.sleep(PACE_S)
        if proxy not in idx["1d"]:
            idx["1d"][proxy] = rows_of(fetch_hl_candles(proxy, "1d", 400))
            print(f"[idx 1d] {proxy}: {len(idx['1d'][proxy])} bars")
            time.sleep(PACE_S)
    with open(OUT_IDX, "w") as f:
        json.dump(idx, f)
    print(f"index cache -> {OUT_IDX}")

    # -- funding history (paged) ---------------------------------------------
    fund = json.load(open(OUT_FUND)) if os.path.exists(OUT_FUND) else {}
    for i, coin in enumerate(coins):
        if coin in fund:
            continue
        rows, start = [], FUND_START_MS
        for _page in range(6):
            batch = fetch_funding_history(coin, start)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 450:
                break
            start = int(batch[-1].get("time", 0)) + 1
            time.sleep(PACE_S)
        fund[coin] = [{"time": int(r.get("time", 0)),
                       "fundingRate": float(r.get("fundingRate", 0.0) or 0.0)}
                      for r in rows]
        print(f"[fund {i+1}/{len(coins)}] {coin}: {len(fund[coin])} rows")
        if (i + 1) % 5 == 0:
            with open(OUT_FUND, "w") as f:
                json.dump(fund, f)
        time.sleep(PACE_S)
    with open(OUT_FUND, "w") as f:
        json.dump(fund, f)
    print(f"funding cache: {len(fund)} coins -> {OUT_FUND}")


if __name__ == "__main__":
    main()
