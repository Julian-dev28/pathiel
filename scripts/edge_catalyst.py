#!/usr/bin/env python3
"""Catalyst edge backtest — do NEWS surges predict price moves? (FREE data: GDELT)

The mover autopsy (edge_movers.py) showed the big runs have NO price precursor — they're
catalyst-driven. This tests the catalyst directly, lookahead-safe + cost-aware, using GDELT's
free historical news-coverage timeline (no paid feed, no X API, no Brave).

Per coin: GDELT TimelineVol (daily % of world news mentioning the asset) + TimelineTone (daily
average sentiment), aligned to daily price candles. At day i (using only news ≤ i):
  surge   = vol[i] >= SURGE_X × median(vol[i-BASE:i])      (an attention spike = a catalyst)
Entry at day i+1 open; forward return over a 5-bar hold AND a 12%-trail ride; net of cost; OOS.
Also splits by tone (positive-tone surge = good news → long bias) and by surge magnitude.

RESILIENCE: GDELT responses cached to disk; 429/timeout → exponential backoff + retry (never
switch to a paid source). Re-runs read cache (free).
"""
import os, sys, json, ssl, time, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

_SSL = ssl.create_default_context(); _SSL.check_hostname = False; _SSL.verify_mode = ssl.CERT_NONE
_GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".catalyst_gdelt_cache.json")
START, END = "20260315000000", "20260622000000"      # ~3 months of free daily history
SURGE_X = 2.5
BASE = 14
HOLD_N = 5
TRAIL, HARD_STOP, MAXHOLD = 0.12, 0.10, 20
COST_BPS = 10.0
WARMUP = BASE + 1

# Major coins with real GDELT news coverage (microcaps have none → excluded).
COIN_QUERY = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "Ripple XRP",
    "DOGE": "Dogecoin", "ADA": "Cardano", "AVAX": "Avalanche crypto", "LINK": "Chainlink",
    "DOT": "Polkadot", "LTC": "Litecoin", "UNI": "Uniswap", "AAVE": "Aave",
    "SUI": "Sui crypto", "APT": "Aptos crypto", "ARB": "Arbitrum crypto", "NEAR": "NEAR Protocol",
    "ATOM": "Cosmos crypto", "FIL": "Filecoin", "INJ": "Injective protocol", "TIA": "Celestia crypto",
}

_cache = {}
if os.path.exists(_CACHE_FILE):
    try: _cache = json.load(open(_CACHE_FILE))
    except Exception: _cache = {}


def _get(url, tries=8):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-research/1.0"})
            with urllib.request.urlopen(req, timeout=25, context=_SSL) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            wait = min(180, 15 * (2 ** k))
            es = str(e)
            if "429" in es or "Too Many" in es or "timed out" in es or "reset" in es:
                print(f"    rate/timeout (try {k+1}/{tries}), backoff {wait}s…")
            else:
                print(f"    err (try {k+1}/{tries}): {es[:70]}")
            time.sleep(wait)
    return None


def gdelt_timeline(query, mode):
    """Daily {YYYYMMDD: value} for TimelineVol or TimelineTone (cached)."""
    ck = f"{mode}:{query}:{START}:{END}"
    if ck in _cache:
        return _cache[ck]
    if os.environ.get("CATALYST_CACHE_ONLY"):
        return {}                                        # workaround: no live GDELT call (429-proof)
    q = urllib.parse.quote(f'"{query}"' if " " not in query else query)
    url = f"{_GDELT}?query={q}&mode={mode}&format=json&startdatetime={START}&enddatetime={END}&timelinesmooth=0"
    txt = _get(url)
    out = {}
    if txt:
        try:
            tl = (json.loads(txt) or {}).get("timeline") or []
            for p in (tl[0].get("data") if tl else []):
                d = (p.get("date") or "")[:8]
                if d:
                    out[d] = float(p.get("value") or 0)
        except Exception:
            pass
    _cache[ck] = out
    json.dump(_cache, open(_CACHE_FILE, "w"))
    time.sleep(float(os.environ.get("GDELT_SLEEP","0.35")))  # polite; env-tunable for slow backfill
    return out


def _exit_hold1(bars, j):
    """FRESHEST horizon: the next-24h move (enter day i+1 open, exit day i+1 close). A news
    catalyst's edge decays fast — if it's not here, the move was same-day and we're already late."""
    if j >= len(bars): return None
    e = candle_val(bars[j], "o"); x = candle_val(bars[j], "c")
    return (x - e) / e if e > 0 else None


def _exit_hold5(bars, j):
    if j + HOLD_N >= len(bars): return None
    e = candle_val(bars[j], "o"); x = candle_val(bars[j + HOLD_N], "c")
    return (x - e) / e if e > 0 else None


def _exit_trail(bars, j):
    e = candle_val(bars[j], "o")
    if e <= 0: return None
    peak = e
    for k in range(j, min(j + MAXHOLD, len(bars))):
        hi, lo, cl = candle_val(bars[k], "h"), candle_val(bars[k], "l"), candle_val(bars[k], "c")
        if lo <= e * (1 - HARD_STOP): return -HARD_STOP
        peak = max(peak, cl)
        if cl <= peak * (1 - TRAIL): return (cl - e) / e
    return (candle_val(bars[min(j + MAXHOLD - 1, len(bars) - 1)], "c") - e) / e


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def main():
    print(f"# Catalyst edge | GDELT free news ({START[:8]}–{END[:8]}) | surge>= {SURGE_X}x base{BASE} | "
          f"cost {COST_BPS:.0f}bps | lookahead-safe, OOS")
    cost = COST_BPS / 1e4
    rows = {"surge_all": {"h1": [], "h5": [], "tr": []}, "surge_postone": {"h1": [], "h5": [], "tr": []},
            "surge_negtone": {"h1": [], "h5": [], "tr": []}}
    big = {1: [], 3: [], 5: []}   # NEXT-24h return bucketed by surge magnitude
    n_coins = 0
    for coin, query in COIN_QUERY.items():
        vol = gdelt_timeline(query, "TimelineVol")
        tone = {} if os.environ.get("CATALYST_NO_TONE") else gdelt_timeline(query, "TimelineTone")
        if len(vol) < WARMUP + 10:
            continue
        try:
            bars = fetch_hl_candles(coin, "1d", 260)
        except Exception:
            continue
        if len(bars) < 40:
            continue
        n_coins += 1
        by_ymd = {}
        for idx, b in enumerate(bars):
            by_ymd[_ymd(int(candle_val(b, "t")))] = idx
        days = sorted(vol.keys())
        for di in range(BASE, len(days) - 1):
            d = days[di]
            window = [vol[x] for x in days[di - BASE:di]]            # strictly BEFORE d (lookahead-safe)
            base = statistics.median(window) if window else 0
            v = vol[d]
            if base <= 0 or v < SURGE_X * base:
                continue
            mag = v / base
            pi = by_ymd.get(d)                                        # the price bar for catalyst day d
            if pi is None or pi + 1 >= len(bars):
                continue
            j = pi + 1                                                # enter NEXT day's open
            h1, h5, tr = _exit_hold1(bars, j), _exit_hold5(bars, j), _exit_trail(bars, j)
            t = tone.get(d, 0.0)
            for store in ("surge_all", "surge_postone" if t >= 0 else "surge_negtone"):
                if store == "surge_negtone" and t >= 0: continue
                if store == "surge_postone" and t < 0: continue
                if h1 is not None: rows[store]["h1"].append(h1 - cost)
                if h5 is not None: rows[store]["h5"].append(h5 - cost)
                if tr is not None: rows[store]["tr"].append(tr - cost)
            if h1 is not None:
                bk = 5 if mag >= 5 else 3 if mag >= 3 else 1
                big[bk].append(h1 - cost)

    print(f"# {n_coins} coins with GDELT coverage + price\n")

    def rep(name, arr):
        if not arr: print(f"  {name:20} n=0"); return
        n = len(arr); w = sum(1 for r in arr if r > 0); mid = n // 2
        h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
        h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
        rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
        print(f"  {name:20} n={n:>3} win {w/n*100:>3.0f}%  mean {statistics.mean(arr)*100:>+6.2f}%  "
              f"med {statistics.median(arr)*100:>+5.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}")

    print("# news-VOLUME surge → forward return (HEADLINE = next-24h, the freshness-relevant horizon):")
    rep("surge · NEXT-24h", rows["surge_all"]["h1"])
    rep("surge · hold5", rows["surge_all"]["h5"]); rep("surge · trail", rows["surge_all"]["tr"])
    print("\n# split by GDELT TONE on the surge day (positive = good news), next-24h:")
    rep("pos-tone · 24h", rows["surge_postone"]["h1"]); rep("pos-tone · trail", rows["surge_postone"]["tr"])
    rep("neg-tone · 24h", rows["surge_negtone"]["h1"]); rep("neg-tone · trail", rows["surge_negtone"]["tr"])
    print("\n# by surge magnitude (NEXT-24h, net) — does a bigger media spike → bigger move?:")
    for bk, lab in ((1, "2.5–3x"), (3, "3–5x"), (5, ">=5x")):
        rep(f"surge {lab}", big[bk])


if __name__ == "__main__":
    main()
