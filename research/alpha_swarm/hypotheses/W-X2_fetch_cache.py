#!/usr/bin/env python3
"""W-X2 data fetch — daily candles for the widening cells (read-only, throttled).

Builds research/alpha_swarm/hypotheses/W-X2_cache_daily.json:
  meta.crypto_universe : top-50 tradeable main-dex perps by dayNtlVlm (>= $5M floor,
                         the live xs_momentum universe rule, survivor-biased by design)
  meta.xyz_universe    : ALL xyz-dex markets (tokenized equities/indices/commodities)
  candles[coin]        : [[t,o,h,l,c,v], ...] daily bars, up to 400d requested
Benchmarks always included: BTC, xyz:XYZ100, xyz:SP500.

Throttle: 0.5s sleep between candleSnapshot calls (live loop shares this IP).
"""
import json
import os
import sys
import time

REPO = "/Users/julian_dev/Documents/code/hermes-trader"
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "research", "alpha_swarm", "hypotheses", "W-X2_cache_daily.json")

# dotenv .env.local
for line in open(os.path.join(REPO, ".env.local")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from hermes_trader.client.hl_client import _http_post, fetch_hl_candles  # noqa: E402


def main() -> None:
    # ---- universes ----------------------------------------------------------
    main_meta = _http_post("/info", {"type": "metaAndAssetCtxs"})
    uni, ctxs = main_meta[0]["universe"], main_meta[1]
    # DECLARED AMENDMENT (2026-07-20, before any cell ran): the live $5M dayNtlVlm floor
    # leaves only ~16 names in this weekend snapshot (whole tape quiet). Research universe
    # = top-50 by dayNtlVlm RANK instead; ranks ~30-50 are thin today (<$2M) — caveat in
    # findings. The live floor still applies at execution time.
    crypto = []
    for m, c in zip(uni, ctxs):
        if m.get("isDelisted"):
            continue
        vol = float(c.get("dayNtlVlm") or 0)
        crypto.append({"coin": m["name"], "dayNtlVlm": vol})
    crypto.sort(key=lambda x: -x["dayNtlVlm"])
    crypto = crypto[:50]

    time.sleep(0.5)
    xyz_meta = _http_post("/info", {"type": "metaAndAssetCtxs", "dex": "xyz"})
    xuni, xctxs = xyz_meta[0]["universe"], xyz_meta[1]
    xyz = []
    for m, c in zip(xuni, xctxs):
        if m.get("isDelisted"):
            continue
        # HIP-3 dex meta already returns dex-prefixed names ("xyz:AAPL")
        name = m["name"] if m["name"].startswith("xyz:") else f"xyz:{m['name']}"
        xyz.append({"coin": name, "dayNtlVlm": float(c.get("dayNtlVlm") or 0)})

    coins = sorted({x["coin"] for x in crypto} | {x["coin"] for x in xyz}
                   | {"BTC", "xyz:XYZ100", "xyz:SP500"})
    print(f"crypto top-50: {len(crypto)}  xyz markets: {len(xyz)}  total fetch: {len(coins)}")

    candles = {}
    for i, coin in enumerate(coins):
        time.sleep(0.5)
        bars = fetch_hl_candles(coin, "1d", 400)
        candles[coin] = [[b.t, b.o, b.h, b.l, b.c, b.v] for b in bars]
        if i % 20 == 0:
            print(f"  {i}/{len(coins)} {coin}: {len(bars)} bars")

    out = {
        "meta": {
            "fetched_at": int(time.time() * 1000),
            "crypto_universe": crypto,
            "xyz_universe": xyz,
            "note": "daily candleSnapshot, 400d requested; survivor-biased (today's listings)",
        },
        "candles": candles,
    }
    with open(OUT, "w") as fh:
        json.dump(out, fh)
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
