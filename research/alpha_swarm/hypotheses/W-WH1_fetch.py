#!/usr/bin/env python
"""W-WH1: fetch + cache the full public record of wallet 0xe282...df29.

Operator brought this wallet (hyperdash.com/address/0xe2823659...) as a
"never lost a trade" trader to reverse-engineer. Everything the analysis
needs is public on the Hyperliquid info API, so it is pulled once here and
the analysis (W-WH1_reverse_engineer.py) reads JSON only.

Caches written next to this file (gitignored by the *_cache_* rule):

  W-WH1_cache_fills.json    every fill HL still serves (userFillsByTime,
                            paged forward; HL keeps the 10,000 most recent)
  W-WH1_cache_funding.json  every hourly funding payment (userFunding, paged)
  W-WH1_cache_ledger.json   deposits / withdrawals / transfers
  W-WH1_cache_state.json    clearinghouse (main + every HIP-3 dex with a
                            position), spot, portfolio curves, open orders
  W-WH1_cache_4h.json       {coin: [[t,o,h,l,c], ...]} 4h candles, 5000 bars
                            (~833 days) for every perp coin the wallet traded

Run once. Paced 0.4s between requests.
"""
import json
import os
import sys
import time
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, _REPO)

from pathiel.client.hl_client import _http_post  # noqa: E402

USER = "0xe2823659be02e0f48a4660e4da008b5e1abfdf29"
HERE = os.path.dirname(os.path.abspath(__file__))
PACE_S = 0.4


def out(name):
    return os.path.join(HERE, f"W-WH1_cache_{name}.json")


def post(payload, timeout=30):
    for attempt in range(6):
        raw = _http_post("/info", payload, timeout=timeout)
        time.sleep(PACE_S)
        if raw is not None:
            return raw
        time.sleep(2 ** attempt)
    raise RuntimeError(f"info request failed 6 times: {payload.get('type')}")


def page_forward(kind, time_key="time"):
    """Page a startTime/endTime endpoint forward until it returns nothing new."""
    rows, seen, start = [], set(), 0
    end = int(time.time() * 1000)
    while True:
        batch = post({"type": kind, "user": USER, "startTime": start, "endTime": end})
        fresh = []
        for r in batch:
            key = json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                fresh.append(r)
        if not fresh:
            break
        rows.extend(fresh)
        start = max(r[time_key] for r in batch)
        print(f"  {kind}: {len(rows)} rows, through {start}")
        if len(batch) < 500:
            break
    rows.sort(key=lambda r: r[time_key])
    return rows


def main():
    fills = page_forward("userFillsByTime")
    json.dump(fills, open(out("fills"), "w"))

    funding = page_forward("userFunding")
    json.dump(funding, open(out("funding"), "w"))

    ledger = post({"type": "userNonFundingLedgerUpdates", "user": USER,
                   "startTime": 0, "endTime": int(time.time() * 1000)})
    json.dump(ledger, open(out("ledger"), "w"))

    dexes = [d["name"] for d in post({"type": "perpDexs"}) if d]
    state = {
        "fetched_ms": int(time.time() * 1000),
        "clearinghouse": {"": post({"type": "clearinghouseState", "user": USER})},
        "spot": post({"type": "spotClearinghouseState", "user": USER}),
        "portfolio": post({"type": "portfolio", "user": USER}),
        "open_orders": post({"type": "frontendOpenOrders", "user": USER}),
    }
    traded_dexes = {f["coin"].split(":")[0] for f in fills if ":" in f["coin"]}
    for dex in sorted(set(dexes) & traded_dexes):
        state["clearinghouse"][dex] = post(
            {"type": "clearinghouseState", "user": USER, "dex": dex})
    json.dump(state, open(out("state"), "w"))

    # Perp coins only: spot pairs are "@123" / "PURR/USDC", outcome tokens "#123".
    coins = sorted({f["coin"] for f in fills
                    if not f["coin"].startswith(("@", "#")) and "/" not in f["coin"]})
    end = int(time.time() * 1000)
    start = end - 5000 * 4 * 3600 * 1000
    candles = {}
    for coin in coins:
        raw = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "4h", "startTime": start, "endTime": end}})
        candles[coin] = [[c["t"], float(c["o"]), float(c["h"]),
                          float(c["l"]), float(c["c"])] for c in raw]
        print(f"  4h {coin}: {len(candles[coin])} bars")
    json.dump(candles, open(out("4h"), "w"))

    print(f"fills {len(fills)}  funding {len(funding)}  ledger {len(ledger)}  "
          f"coins {len(coins)}  dexes {list(state['clearinghouse'])}")


if __name__ == "__main__":
    main()
