#!/usr/bin/env python
"""W-P1: fetch + cache everything the EDGAR-latency event study needs.

Phases (run selectively via argv; caches are idempotent — a coin/CIK already
in a cache is skipped, so re-runs only fill gaps):

  universe  ->  W-P1_cache_universe.json   xyz metaAndAssetCtxs snapshot
  cikmap    ->  W-P1_cache_cik_map.json    ticker->CIK coverage table
  edgar     ->  W-P1_cache_filings.json    8-K/6-K rows per covered CIK
  candles   ->  W-P1_cache_1h.json         full-life 1h candles per covered coin

Downstream analysis (`W-P1_edgar_backtest.py`) reads the caches ONLY.

Rate discipline: HL calls go through pathia.client.hl_client._http_post
(token-bucket metered) plus an explicit sleep; SEC calls carry a User-Agent and
are throttled to ~2/s (SEC allows ~10/s; we stay far under).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, _REPO)

import requests  # noqa: E402

# Load .env.local (proxy vars etc.) without hard-depending on python-dotenv.
_ENV_LOCAL = os.path.join(_REPO, ".env.local")
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_LOCAL)
except Exception:
    if os.path.exists(_ENV_LOCAL):
        for _line in open(_ENV_LOCAL):
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from pathia.client.hl_client import _http_post  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UNI_CACHE = os.path.join(HERE, "W-P1_cache_universe.json")
CIK_CACHE = os.path.join(HERE, "W-P1_cache_cik_map.json")
FIL_CACHE = os.path.join(HERE, "W-P1_cache_filings.json")
HR_CACHE = os.path.join(HERE, "W-P1_cache_1h.json")

SEC_UA = {"User-Agent": "pathia-research team.recoin@gmail.com"}
SEC_SLEEP = 0.5          # ~2 req/s, well under SEC's ~10/s ceiling
HL_SLEEP = 2.5           # on top of the shared token bucket

# Hand map for xyz tickers whose symbol does not resolve via company_tickers.json
# exact match, or resolves to the WRONG company. Underlyings verified against
# docs.trade.xyz/consolidated-resources/specification-index (fetched 2026-07-19).
HAND_MAP: dict[str, int] = {
    # trade.xyz ticker PURRDAT = Hyperliquid Strategies Inc common stock;
    # SEC lists it under Nasdaq ticker PURR.
    "PURRDAT": 2078856,
}
HAND_EXCLUDE: dict[str, str] = {
    # commodity / FX / index perps — no issuer. CL would otherwise FALSE-MATCH
    # Colgate-Palmolive (NYSE:CL); GOLD would false-match legacy Barrick.
    "CL": "WTI crude (collides with Colgate NYSE:CL)",
    "BRENTOIL": "Brent crude", "GOLD": "gold (collides with legacy Barrick)",
    "SILVER": "silver", "COPPER": "copper", "NATGAS": "natural gas",
    "PLATINUM": "platinum", "PALLADIUM": "palladium",
    "EUR": "FX", "GBP": "FX", "JPY": "FX",
    "JP225": "index", "KR200": "index", "SP500": "index", "XYZ100": "index",
    # ETFs — funds do not file 8-K/6-K corporate events
    "DRAM": "Roundhill Memory ETF", "EWJ": "iShares Japan ETF",
    "EWT": "iShares Taiwan ETF", "EWY": "iShares Korea ETF",
    "EWZ": "iShares Brazil ETF", "SMH": "VanEck Semi ETF",
    "XLE": "SPDR Energy ETF", "URNM": "Sprott Uranium ETF",
    # foreign issuers with NO EDGAR filing obligation (no sponsored US listing)
    "KIOXIA": "Kioxia (TSE, no EDGAR)", "SKHX": "SK Hynix ordinary (KRX)",
    "SKHY": "SK Hynix unsponsored ADS (no EDGAR)", "SMSN": "Samsung GDR (LSE)",
    "HYUNDAI": "Hyundai Motor (KRX)", "SOFTBANK": "SoftBank Group (TSE)",
    "CXMT": "ChangXin Memory (CN)", "MINIMAX": "MiniMax Group (HK)",
    "ZHIPU": "Knowledge Atlas / Zhipu H share (HK)",
    # private — no periodic EDGAR record
    "SPCX": "SpaceX (private)",
    # identity mismatch: trade.xyz says NewBird AI; SEC BIRD = 'Smartbird, Inc.'
    # Cannot confirm same entity -> excluded rather than risk wrong-CIK events.
    "BIRD": "NewBird AI vs SEC 'Smartbird, Inc.' — unconfirmed identity",
    # duplicate CIK: STRC is Strategy Inc preferred; MSTR already carries the CIK
    "STRC": "Strategy Inc preferred (CIK dup of MSTR)",
}


def _load(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


# ── phase: universe ──────────────────────────────────────────────────────────

def fetch_universe():
    data = _http_post("/info", {"type": "metaAndAssetCtxs", "dex": "xyz"})
    if not (isinstance(data, list) and len(data) >= 2):
        raise SystemExit("xyz metaAndAssetCtxs fetch failed")
    meta, ctx = data[0], data[1]
    rows = []
    for i, u in enumerate(meta.get("universe", [])):
        c = ctx[i] if i < len(ctx) else {}
        name = u["name"]                      # comes back already namespaced: "xyz:AAPL"
        rows.append({
            "coin": name if ":" in name else f"xyz:{name}",
            "ticker": name.split(":")[-1],
            "maxLeverage": u.get("maxLeverage"),
            "isDelisted": u.get("isDelisted", False),
            "dayNtlVlm": float(c.get("dayNtlVlm") or 0),
            "openInterest": float(c.get("openInterest") or 0),
            "markPx": float(c.get("markPx") or 0),
        })
    _save(UNI_CACHE, rows)
    print(f"xyz universe: {len(rows)} markets -> {UNI_CACHE}")
    for r in sorted(rows, key=lambda x: -x["dayNtlVlm"]):
        print(f"  {r['ticker']:<10} vol24h=${r['dayNtlVlm']:>14,.0f} "
              f"lev={r['maxLeverage']}x delisted={r['isDelisted']}")


# ── phase: cikmap ────────────────────────────────────────────────────────────

def build_cik_map():
    uni = _load(UNI_CACHE, None)
    if uni is None:
        raise SystemExit("run `universe` phase first")
    print("downloading company_tickers.json ...")
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=SEC_UA, timeout=30)
    r.raise_for_status()
    time.sleep(SEC_SLEEP)
    by_ticker = {}
    for row in r.json().values():
        by_ticker.setdefault(row["ticker"].upper(), (row["cik_str"], row["title"]))
    out = {}
    for m in uni:
        t = m["ticker"].upper()
        if m.get("isDelisted"):
            out[t] = {"cik": None, "title": None, "source": "delisted-excluded",
                      "reason": "not in today's tradeable set"}
        elif t in HAND_EXCLUDE:
            out[t] = {"cik": None, "title": None, "source": "hand-exclude",
                      "reason": HAND_EXCLUDE[t]}
        elif t in HAND_MAP:
            out[t] = {"cik": HAND_MAP[t], "title": None, "source": "hand-map"}
        elif t in by_ticker:
            cik, title = by_ticker[t]
            out[t] = {"cik": int(cik), "title": title, "source": "company_tickers"}
        else:
            out[t] = {"cik": None, "title": None, "source": "unmatched"}
    _save(CIK_CACHE, out)
    n_cov = sum(1 for v in out.values() if v["cik"])
    print(f"cik map: {n_cov}/{len(out)} covered -> {CIK_CACHE}")
    for t, v in sorted(out.items()):
        print(f"  {t:<10} {v['source']:<16} cik={v['cik']} {v.get('title') or v.get('reason') or ''}")


# ── phase: edgar ─────────────────────────────────────────────────────────────

def fetch_filings():
    cmap = _load(CIK_CACHE, None)
    if cmap is None:
        raise SystemExit("run `cikmap` phase first")
    cache = _load(FIL_CACHE, {})
    todo = {t: v["cik"] for t, v in cmap.items() if v["cik"] and t not in cache}
    print(f"edgar: {len(todo)} CIKs to fetch ({len(cache)} cached)")
    for i, (t, cik) in enumerate(sorted(todo.items())):
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        try:
            r = requests.get(url, headers=SEC_UA, timeout=30)
            r.raise_for_status()
            sub = r.json()
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {t}: FETCH FAIL {e}")
            time.sleep(SEC_SLEEP)
            continue
        rec = sub.get("filings", {}).get("recent", {})
        rows = []
        forms = rec.get("form", [])
        for j, form in enumerate(forms):
            if form not in ("8-K", "6-K"):     # amendments (/A) excluded by spec
                continue
            rows.append({
                "form": form,
                "acceptanceDateTime": rec["acceptanceDateTime"][j],
                "filingDate": rec["filingDate"][j],
                "accessionNumber": rec["accessionNumber"][j],
                "items": rec.get("items", [""] * len(forms))[j],
                "primaryDocDescription":
                    rec.get("primaryDocDescription", [""] * len(forms))[j],
            })
        cache[t] = {"cik": cik, "name": sub.get("name"),
                    "n_recent_total": len(forms), "filings": rows}
        print(f"  [{i+1}/{len(todo)}] {t} ({sub.get('name')}): "
              f"{len(rows)} 8-K/6-K of {len(forms)} recent filings")
        _save(FIL_CACHE, cache)
        time.sleep(SEC_SLEEP)
    print(f"filings cache: {len(cache)} CIKs -> {FIL_CACHE}")


# ── phase: candles ───────────────────────────────────────────────────────────

def fetch_candles():
    cmap = _load(CIK_CACHE, None)
    if cmap is None:
        raise SystemExit("run `cikmap` phase first")
    covered = [t for t, v in cmap.items() if v["cik"]]
    cache = _load(HR_CACHE, {})
    todo = [t for t in covered if f"xyz:{t}" not in cache]
    print(f"candles: {len(todo)} coins to fetch ({len(cache)} cached)")
    now_ms = int(time.time() * 1000)
    for i, t in enumerate(sorted(todo)):
        coin = f"xyz:{t}"
        rows: list[list] = []
        end = now_ms
        for _page in range(3):                       # 3 x 5000 h ~= 2y, ample
            payload = {"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": "1h",
                               "startTime": end - 5000 * 3_600_000,
                               "endTime": end}}
            raw = _http_post("/info", payload)
            time.sleep(HL_SLEEP)
            if not isinstance(raw, list):
                print(f"    {coin}: transient fetch failure page {_page}, retrying once")
                time.sleep(5)
                raw = _http_post("/info", payload)
                time.sleep(HL_SLEEP)
            if not isinstance(raw, list) or not raw:
                break
            page = [[c["t"], float(c["o"]), float(c["h"]), float(c["l"]),
                     float(c["c"]), float(c.get("v", "0"))] for c in raw]
            rows = page + rows
            if len(raw) < 5000:
                break
            end = page[0][0] - 1
        rows.sort(key=lambda r: r[0])
        # de-dupe on t (page boundaries can overlap)
        dedup, seen = [], set()
        for r_ in rows:
            if r_[0] not in seen:
                seen.add(r_[0])
                dedup.append(r_)
        cache[coin] = dedup
        print(f"  [{i+1}/{len(todo)}] {coin}: {len(dedup)} 1h bars "
              f"({(dedup and (now_ms - dedup[0][0]) / 86_400_000) or 0:.0f}d)")
        _save(HR_CACHE, cache)
    print(f"1h cache: {len(cache)} coins -> {HR_CACHE}")


if __name__ == "__main__":
    phases = sys.argv[1:] or ["universe", "cikmap", "edgar", "candles"]
    for ph in phases:
        {"universe": fetch_universe, "cikmap": build_cik_map,
         "edgar": fetch_filings, "candles": fetch_candles}[ph]()
