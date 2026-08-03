#!/usr/bin/env python3
"""W-Z2 fetch: Nasdaq earnings surprise + forward calendar, HL 1h top-up,
xyz funding + asset-ctx snapshot. Idempotent — every cache is skipped if
present. Gentle on HL: <= 6 candle calls + 1 metaAndAssetCtxs + 3
fundingHistory. Nasdaq throttled ~1.5/s.

Read-only on live code. Spec: findings/W-Z2_xyz_earnings.md (pre-registered).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

HYP = Path(__file__).resolve().parent
sys.path.insert(0, str(HYP.parent.parent.parent))  # repo root

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json",
}
HL_API = "https://api.hyperliquid.xyz"

CACHE_SURPRISE = HYP / "W-Z2_cache_surprise.json"
CACHE_FWD = HYP / "W-Z2_cache_fwd.json"
CACHE_TOPUP = HYP / "W-Z2_cache_1h_topup.json"
CACHE_FUNDING = HYP / "W-Z2_cache_funding.json"
CACHE_CTXS = HYP / "W-Z2_cache_ctxs.json"


def nasdaq_get(url: str) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def fetch_surprise() -> None:
    if CACHE_SURPRISE.exists():
        print("surprise cache exists, skip")
        return
    ev = json.load(open(HYP / "W-P4_results.json"))["events_detail"]
    tickers = sorted({e["ticker"] for e in ev})
    out = {}
    for t in tickers:
        j = nasdaq_get(f"https://api.nasdaq.com/api/company/{t}/earnings-surprise")
        rows = (((j or {}).get("data") or {}).get("earningsSurpriseTable") or {}).get(
            "rows"
        )
        out[t] = rows or []
        print(f"  surprise {t}: {len(out[t])} rows")
        time.sleep(0.7)
    json.dump(out, open(CACHE_SURPRISE, "w"))
    print(f"surprise: {len(out)} tickers -> {CACHE_SURPRISE.name}")


def fetch_forward() -> None:
    if CACHE_FWD.exists():
        print("fwd cache exists, skip")
        return
    cik_map = json.load(open(HYP / "W-P1_cache_cik_map.json"))
    # US-consensus-coverable tickers: any with a CIK (52 covered set)
    tickers = sorted(t for t, v in cik_map.items() if v.get("cik"))
    out = {}
    for t in tickers:
        j = nasdaq_get(f"https://api.nasdaq.com/api/analyst/{t}/earnings-date")
        txt = (((j or {}).get("data") or {}).get("reportText")) or ""
        out[t] = txt
        time.sleep(0.7)
    json.dump(out, open(CACHE_FWD, "w"))
    print(f"fwd: {len(out)} tickers -> {CACHE_FWD.name}")


def fetch_topup() -> None:
    if CACHE_TOPUP.exists():
        print("topup cache exists, skip")
        return
    from hermes_trader.client.hl_client import fetch_hl_candles

    ev = json.load(open(HYP / "W-P4_results.json"))["events_detail"]
    coins = sorted(
        {"xyz:" + e["ticker"] for e in ev if e["acc_iso"] >= "2026-07-01"}
    )
    out = {}
    for c in coins:
        candles = fetch_hl_candles(c, interval="1h", count=200)
        out[c] = [
            [int(k.t), float(k.o), float(k.h), float(k.l), float(k.c), float(k.v)]
            for k in candles
        ]
        print(f"  topup {c}: {len(out[c])} bars")
        time.sleep(1.0)
    json.dump(out, open(CACHE_TOPUP, "w"))
    print(f"topup: {len(out)} coins -> {CACHE_TOPUP.name}")


def fetch_hl_info(payload: dict) -> object:
    r = requests.post(f"{HL_API}/info", json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_ctxs() -> None:
    if CACHE_CTXS.exists():
        print("ctxs cache exists, skip")
        return
    j = fetch_hl_info({"type": "metaAndAssetCtxs", "dex": "xyz"})
    json.dump({"fetched_ms": int(time.time() * 1000), "data": j}, open(CACHE_CTXS, "w"))
    print(f"ctxs -> {CACHE_CTXS.name}")


def fetch_funding() -> None:
    if CACHE_FUNDING.exists():
        print("funding cache exists, skip")
        return
    now = int(time.time() * 1000)
    start = now - 30 * 86400_000
    out = {}
    for c in ["xyz:NVDA", "xyz:NFLX", "xyz:RKLB"]:
        out[c] = fetch_hl_info(
            {"type": "fundingHistory", "coin": c, "startTime": start}
        )
        print(f"  funding {c}: {len(out[c])} rows")
        time.sleep(1.0)
    json.dump(out, open(CACHE_FUNDING, "w"))
    print(f"funding -> {CACHE_FUNDING.name}")


if __name__ == "__main__":
    fetch_surprise()
    fetch_forward()
    fetch_topup()
    fetch_ctxs()
    fetch_funding()
    print("ALL FETCHES DONE")
