#!/usr/bin/env python3
"""W-E0 — Lane E data builder: xyz HIP-3 tokenized-equity candles + funding/premium.

The xyz dex trades tokenized US equities 24/7 while the underlyings close nights
and weekends. Lane E tests that structural mismatch (weekend gap-fade, RTH-open
reversal, off-hours crypto lead-lag, mark-vs-oracle premium dislocation).

Fetches via hermes_trader.client.hl_client.fetch_hl_candles (the sanctioned path,
short-TTL cached, rate-limit aware) with modest sleeps, and caches EVERYTHING to
W-E_dataset.json next to this script so the four W-E hypothesis scripts never
re-hit the network. Idempotent: re-run only refreshes if the cache is absent.

Universe: hand-tagged US-RTH names on xyz (equities, US-listed ADRs/ETFs, the two
index perps) with dayNtlVlm >= $700k at build time (the live liquidity floor).
Commodities / FX / Asian-session names are EXCLUDED — their underlying session
structure differs, which would poison the open/close windows.

Cache shape:
  {"built_utc": ..., "coins": [...], "volumes": {coin: dayNtlVlm},
   "candles_1h": {coin: [[t,o,h,l,c,v], ...]},          # up to 5000 bars (~208d)
   "candles_15m": {coin: [[t,o,h,l,c,v], ...]},         # 6 core names (~52d)
   "funding": {coin: [[t, fundingRate, premium], ...]}} # 12 liquid names, ~120d

NOTE for orchestrator: W-E_dataset.json is DATA (~15MB) — add to .gitignore like
dataset.json (research agents cannot touch .gitignore).
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from hermes_trader.client.hl_client import fetch_hl_candles, fetch_funding_history

OUT = Path(__file__).resolve().parent / "W-E_dataset.json"

# Hand-tagged: underlying trades US RTH (NYSE/Nasdaq 9:30-16:00 ET) only.
US_RTH_WHITELIST = [
    "xyz:SP500", "xyz:XYZ100",                              # index perps
    "xyz:MU", "xyz:SNDK", "xyz:SPCX", "xyz:NVDA", "xyz:INTC", "xyz:MRVL",
    "xyz:CRCL", "xyz:AMD", "xyz:MSTR", "xyz:TSLA", "xyz:CBRS", "xyz:NBIS",
    "xyz:ORCL", "xyz:META", "xyz:HOOD", "xyz:MSFT", "xyz:GOOGL", "xyz:AAPL",
    "xyz:AMZN", "xyz:BABA", "xyz:BB", "xyz:BE", "xyz:CRWV", "xyz:PLTR",
    "xyz:COIN", "xyz:RKLB", "xyz:TSM", "xyz:ARM", "xyz:AVGO", "xyz:WDC",
    "xyz:DELL", "xyz:LITE", "xyz:SMH", "xyz:LLY", "xyz:EWY", "xyz:EWJ",
]
CRYPTO = ["BTC", "ETH"]
CORE_15M = ["xyz:SP500", "xyz:XYZ100", "xyz:NVDA", "xyz:MU", "xyz:TSLA", "xyz:MSTR"]
FUNDING_N = 12          # top-N by volume get funding/premium history
FUNDING_DAYS = 120
VOL_FLOOR = 700_000.0   # live liquidity floor
SLEEP_S = 1.1           # candleSnapshot weight 20; 1200/min budget -> stay well under


def _xyz_volumes() -> dict:
    import requests
    r = requests.post("https://api.hyperliquid.xyz/info",
                      json={"type": "metaAndAssetCtxs", "dex": "xyz"}, timeout=15)
    r.raise_for_status()
    meta, ctxs = r.json()
    return {u["name"]: float(c.get("dayNtlVlm", 0.0))
            for u, c in zip(meta["universe"], ctxs)}


def _candles(coin: str, iv: str, count: int) -> list:
    cs = fetch_hl_candles(coin, iv, count)
    time.sleep(SLEEP_S)
    return [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]


def _funding(coin: str, days: int) -> list:
    end = int(time.time() * 1000)
    cur = end - days * 86_400_000
    rows, seen_last = [], -1
    for _ in range(30):
        # fetch_funding_history returns [] on transient 429 — retry with backoff
        # before trusting an empty batch (a 429 storm zeroed 9/12 coins on run 1).
        batch = []
        for attempt in range(6):
            batch = fetch_funding_history(coin, cur, end)
            if batch:
                break
            time.sleep(min(3.0 * (2 ** attempt), 30.0))
        time.sleep(SLEEP_S * 3)
        if not batch:
            break
        for r in batch:
            try:
                rows.append([int(r["time"]), float(r["fundingRate"]),
                             float(r.get("premium", 0.0))])
            except Exception:
                continue
        last_t = int(batch[-1]["time"])
        if last_t <= seen_last or last_t >= end:
            break
        seen_last, cur = last_t, last_t + 1
    dedup = {r[0]: r for r in rows}
    return [dedup[t] for t in sorted(dedup)]


def repair_funding():
    """Refill funding rows for any coin with a thin/empty series (429 fallout)."""
    d = json.loads(OUT.read_text())
    fund_coins = sorted(d["coins"], key=lambda c: -d["volumes"].get(c, 0.0))[:FUNDING_N]
    need_rows = FUNDING_DAYS * 24 * 0.5   # accept >=50% coverage
    for c in fund_coins:
        have = d["funding"].get(c, [])
        if len(have) >= need_rows:
            print(f"  fund {c}: keep {len(have)}")
            continue
        d["funding"][c] = _funding(c, FUNDING_DAYS)
        print(f"  fund {c}: refetched {len(d['funding'][c])} rows")
        OUT.write_text(json.dumps(d))
    print("funding repair done")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--funding-only":
        repair_funding()
        return
    if OUT.is_file() and not os.environ.get("WE_FORCE_REFRESH"):
        d = json.loads(OUT.read_text())
        print(f"cache hit: {OUT} ({len(d.get('coins', []))} coins, built {d.get('built_utc')})")
        return
    vols = _xyz_volumes()
    coins = [c for c in US_RTH_WHITELIST if vols.get(c, 0.0) >= VOL_FLOOR]
    print(f"universe: {len(coins)} US-RTH xyz names above ${VOL_FLOOR:,.0f}")
    d = {"built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "coins": coins, "volumes": {c: vols.get(c, 0.0) for c in coins},
         "candles_1h": {}, "candles_15m": {}, "funding": {}}
    for c in coins + CRYPTO:
        d["candles_1h"][c] = _candles(c, "1h", 5000)
        print(f"  1h  {c}: {len(d['candles_1h'][c])} bars")
    for c in CORE_15M + CRYPTO:
        d["candles_15m"][c] = _candles(c, "15m", 5000)
        print(f"  15m {c}: {len(d['candles_15m'][c])} bars")
    fund_coins = sorted(coins, key=lambda c: -vols.get(c, 0.0))[:FUNDING_N]
    for c in fund_coins:
        d["funding"][c] = _funding(c, FUNDING_DAYS)
        print(f"  fund {c}: {len(d['funding'][c])} rows")
    OUT.write_text(json.dumps(d))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
