#!/usr/bin/env python
"""W-Y3: fetch + cache data for the young_mover_short geometry sweep.

Population = every coin that ever hit the history_floor_preflight block in
logs/trading_loop.log (the EXACT live young_mover_short signal population).
Caches written next to this file (downstream analysis reads JSON only):

  W-Y3_cache_1h.json       {coin: [[t,o,h,l,c,v], ...]}   1h, up to 780 bars
                           (covers the full block-log span 2026-06-28..now)
  W-Y3_cache_funding.json  {coin: [[t, rate], ...]}       hourly funding,
                           paged fundingHistory from 2026-06-26
  W-Y3_episodes.json       [{coin, day, ts_ms, age}]      parsed block log
                           (PIT; first block ts per (coin, UTC day))

Run once. ~60 paced requests (0.5s spacing) — gentle on the shared IP.
Track B (longer-history proxy) reuses the existing W-Y_cache_xyz_daily.json
from W-Y0 (full listing history for every coin as of 2026-07-10); it is NOT
re-fetched here.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")

from hermes_trader.client.hl_client import (  # noqa: E402
    fetch_funding_history, fetch_hl_candles,
)

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = "/Users/julian_dev/Documents/code/hermes-trader/logs/trading_loop.log"
H1_CACHE = os.path.join(HERE, "W-Y3_cache_1h.json")
FUND_CACHE = os.path.join(HERE, "W-Y3_cache_funding.json")
EPS_FILE = os.path.join(HERE, "W-Y3_episodes.json")

# log line: "2026-06-28 10:12:19,562 INFO:__main__:xyz:CBRS: pre-research
#            history_floor_preflight (59d < 60d history) — skip AI research"
PAT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO:__main__:(.+?): "
    r"pre-research history_floor_preflight \((\d+)d < \d+d history\)")

FUND_START_MS = int(datetime(2026, 6, 26, tzinfo=timezone.utc).timestamp() * 1000)


def parse_episodes():
    eps = {}
    with open(LOG) as f:
        for line in f:
            m = PAT.match(line)
            if not m:
                continue
            # log timestamps are machine-LOCAL; convert to UTC via local tz
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            ts = ts.astimezone(timezone.utc)
            coin, age = m.group(2), int(m.group(3))
            key = (coin, ts.date().isoformat())
            if key not in eps:
                eps[key] = (int(ts.timestamp() * 1000), age)
    rows = [{"coin": c, "day": d, "ts_ms": t, "age": a}
            for (c, d), (t, a) in sorted(eps.items(), key=lambda kv: kv[1][0])]
    with open(EPS_FILE, "w") as f:
        json.dump(rows, f, indent=1)
    coins = sorted({r["coin"] for r in rows})
    print(f"episodes: {len(rows)}  coins: {len(coins)} -> {EPS_FILE}")
    return coins


def main():
    coins = parse_episodes()

    hourly = {}
    if os.path.exists(H1_CACHE):
        hourly = json.load(open(H1_CACHE))
    for i, coin in enumerate(coins):
        if coin in hourly:
            continue
        candles = fetch_hl_candles(coin, "1h", 780)
        hourly[coin] = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in candles]
        print(f"[1h {i+1}/{len(coins)}] {coin}: {len(hourly[coin])} bars")
        with open(H1_CACHE, "w") as f:
            json.dump(hourly, f)
        time.sleep(0.5)
    print(f"1h cache: {len(hourly)} coins -> {H1_CACHE}")

    funding = {}
    if os.path.exists(FUND_CACHE):
        funding = json.load(open(FUND_CACHE))
    for i, coin in enumerate(coins):
        if coin in funding:
            continue
        rows, start = [], FUND_START_MS
        for _page in range(6):                      # 6 pages x ~500 = plenty
            raw = fetch_funding_history(coin, start)
            if not raw:
                break
            rows.extend([[int(r["time"]), float(r["fundingRate"])] for r in raw])
            if len(raw) < 450:                      # last page
                break
            start = int(raw[-1]["time"]) + 1
            time.sleep(0.5)
        funding[coin] = rows
        print(f"[fund {i+1}/{len(coins)}] {coin}: {len(rows)} rates")
        with open(FUND_CACHE, "w") as f:
            json.dump(funding, f)
        time.sleep(0.5)
    print(f"funding cache: {len(funding)} coins -> {FUND_CACHE}")


if __name__ == "__main__":
    main()
