#!/usr/bin/env python
"""W-WH2: cache full daily history + max leverage for the ladder backtest.

  W-WH2_cache_daily.json  {"max_leverage": {coin: int},
                           "candles": {coin: [[t,o,h,l,c], ...]}}

Hyperliquid serves daily bars from startTime=0 (BTC back to 2020-08).
"""
import json
import os
import sys
import time
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, _REPO)

from pathiel.client.hl_client import _http_post  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "W-WH2_cache_daily.json")

UNIVERSE_A = ["BTC", "ETH", "SOL", "BNB", "XRP"]
UNIVERSE_B = ["AAVE", "CRV", "GMX", "HYPE", "LINK", "NEAR", "ONDO",
              "RENDER", "RUNE", "UNI", "VVV", "WLD", "ZEC"]


def post(payload):
    for attempt in range(6):
        raw = _http_post("/info", payload, timeout=30)
        time.sleep(0.4)
        if raw is not None:
            return raw
        time.sleep(2 ** attempt)
    raise RuntimeError(f"info request failed: {payload}")


def main():
    meta = post({"type": "meta"})
    max_lev = {u["name"]: u["maxLeverage"] for u in meta["universe"]}
    end = int(time.time() * 1000)
    candles = {}
    for coin in UNIVERSE_A + UNIVERSE_B:
        raw = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": "1d", "startTime": 0, "endTime": end}})
        candles[coin] = [[c["t"], float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])]
                         for c in raw]
        print(f"{coin}: {len(candles[coin])} daily bars")
    with open(OUT, "w") as f:
        json.dump({"max_leverage": {c: max_lev[c] for c in candles}, "candles": candles}, f)


if __name__ == "__main__":
    main()
