#!/usr/bin/env python
"""W-STB1: cache 1h candles for the four HL stablecoin/USDC spot pairs.

  W-STB1_cache_1h.json  {"pairs": {name: coin}, "fetched_ms": int,
                         "candles": {name: [[t,o,h,l,c,v], ...]}}

5,000 bars is what candleSnapshot serves at 1h (~208 days).
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
OUT = os.path.join(HERE, "W-STB1_cache_1h.json")
PAIRS = {"USDT0": "@166", "USDE": "@150", "USDH": "@230", "FEUSD": "@153"}


def main():
    end = int(time.time() * 1000)
    start = end - 5000 * 3_600_000
    candles = {}
    for name, coin in PAIRS.items():
        raw = None
        for attempt in range(6):
            raw = _http_post("/info", {"type": "candleSnapshot", "req": {
                "coin": coin, "interval": "1h", "startTime": start, "endTime": end}}, timeout=30)
            if isinstance(raw, list):
                break
            time.sleep(2 ** attempt)
        candles[name] = [[c["t"], float(c["o"]), float(c["h"]), float(c["l"]),
                          float(c["c"]), float(c["v"])] for c in raw]
        print(f"{name} ({coin}): {len(candles[name])} 1h bars")
        time.sleep(0.4)
    with open(OUT, "w") as f:
        json.dump({"pairs": PAIRS, "fetched_ms": end, "candles": candles}, f)


if __name__ == "__main__":
    main()
