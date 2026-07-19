"""W-P2: fetch BTC + ETH 1h candles 2024-01-01 -> now from Hyperliquid.

Standalone (does NOT import live-loop code). Pages candleSnapshot with
explicit startTime/endTime (~5000-bar cap per request). Throttled hard:
this IP is shared with the live trading loop.

Cache: W-P2_cache_1h.json  {coin: [{t,o,h,l,c,v}, ...]} sorted, deduped.
"""
from __future__ import annotations

import json
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "W-P2_cache_1h.json")

API = "https://api.hyperliquid.xyz/info"
HOUR_MS = 3_600_000
PAGE_BARS = 4500                      # under the ~5000 cap
START_MS = 1704067200000              # 2024-01-01T00:00:00Z
SLEEP_S = 2.5                         # shared IP with live loop — stay slow


def fetch_coin(coin: str) -> list[dict]:
    out: dict[int, dict] = {}
    t0 = START_MS
    end_now = int(time.time() * 1000)
    while t0 < end_now:
        t1 = min(t0 + PAGE_BARS * HOUR_MS, end_now)
        payload = {"type": "candleSnapshot",
                   "req": {"coin": coin, "interval": "1h",
                           "startTime": t0, "endTime": t1}}
        raw = None
        for attempt in range(5):
            try:
                r = requests.post(API, json=payload, timeout=30)
                if r.ok:
                    raw = r.json()
                    if isinstance(raw, list):
                        break
            except Exception:
                pass
            time.sleep(3.0 * (attempt + 1))
        if not isinstance(raw, list):
            raise RuntimeError(f"{coin}: page {t0} failed after retries")
        for c in raw:
            out[int(c["t"])] = {"t": int(c["t"]), "o": float(c["o"]),
                                "h": float(c["h"]), "l": float(c["l"]),
                                "c": float(c["c"]), "v": float(c["v"])}
        print(f"  {coin}: page {t0} -> {t1} got {len(raw)} (total {len(out)})", flush=True)
        t0 = t1
        time.sleep(SLEEP_S)
    return [out[k] for k in sorted(out)]


# ── Binance fallback (primary for the study span) ─────────────────────
# DISCOVERED AT FETCH TIME, before any event scoring: HL prunes 1h candles
# beyond ~5000 bars (~208d) — 2024 windows return empty. Binance spot
# BTCUSDT/ETHUSDT 1h covers the full span. Costs in the backtest stay 25bps
# RT (perp execution assumption); HL tail kept for overlap sanity.

CACHE_BN = os.path.join(HERE, "W-P2_cache_1h_binance.json")
BN = "https://api.binance.com/api/v3/klines"


def fetch_binance(symbol: str) -> list[dict]:
    out: dict[int, dict] = {}
    t0 = START_MS
    end_now = int(time.time() * 1000)
    while t0 < end_now:
        for attempt in range(5):
            try:
                r = requests.get(BN, params={"symbol": symbol, "interval": "1h",
                                             "startTime": t0, "limit": 1000},
                                 headers={"User-Agent": "hermes-research"}, timeout=30)
                if r.ok:
                    raw = r.json()
                    break
            except Exception:
                pass
            time.sleep(2.0 * (attempt + 1))
        else:
            raise RuntimeError(f"{symbol}: page {t0} failed")
        if not raw:
            break
        for k in raw:
            out[int(k[0])] = {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                              "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
        t0 = int(raw[-1][0]) + HOUR_MS
        time.sleep(0.5)
    bars = [out[k] for k in sorted(out)]
    print(f"  {symbol}: {len(bars)} bars [{bars[0]['t']} .. {bars[-1]['t']}]", flush=True)
    return bars


def main() -> None:
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for coin in ("BTC", "ETH"):
        if coin in cache and len(cache[coin]) > 4000:
            print(f"{coin}: HL cached {len(cache[coin])} bars, skip")
            continue
        cache[coin] = fetch_coin(coin)
        json.dump(cache, open(CACHE, "w"))
    json.dump(cache, open(CACHE, "w"))

    bn = json.load(open(CACHE_BN)) if os.path.exists(CACHE_BN) else {}
    for coin, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        if coin in bn and len(bn[coin]) > 20000:
            print(f"{coin}: binance cached {len(bn[coin])} bars, skip")
            continue
        bn[coin] = fetch_binance(sym)
        json.dump(bn, open(CACHE_BN, "w"))
    json.dump(bn, open(CACHE_BN, "w"))


if __name__ == "__main__":
    main()
