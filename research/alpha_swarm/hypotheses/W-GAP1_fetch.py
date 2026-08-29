#!/usr/bin/env python3
"""W-GAP1 data fetch — 1h candles for the FULL xyz tokenized-equity universe.

Read-only, throttled. Enumerates the xyz dex universe from HL meta (NOT the
ledgers — avoids the selection bias that inflated W-N1's open-gap number).

xyz-dex meta returns ALREADY-namespaced names ("xyz:XYZ100"); use them as-is
(prepending another "xyz:" yields an invalid coin -> HL 500 + retry backoff).

Output: research/alpha_swarm/hypotheses/W-GAP1_cache_1h.json  (written INCREMENTALLY
per coin so a mid-run kill never loses progress).
  meta.universe : [{coin, dayNtlVlm, maxLeverage}] all xyz markets
  candles[coin] : [[t,o,h,l,c,v], ...] 1h bars (t = bar OPEN ms, UTC-aligned)
"""
import json
import os
import sys
import time
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "research", "alpha_swarm", "hypotheses", "W-GAP1_cache_1h.json")

try:
    for line in open(os.path.join(REPO, ".env.local")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
except FileNotFoundError:
    pass

# Keep retry/backoff SHORT so a bad/illiquid coin can't stall the whole run.
os.environ.setdefault("HERMES_CANDLE_RETRIES", "2")
os.environ.setdefault("HERMES_CANDLE_BACKOFF_CAP_S", "2")
os.environ["HERMES_CANDLE_CACHE_TTL_S"] = "0"   # no dedup cache for a bulk pull

from hermes_trader.client.hl_client import _http_post, fetch_hl_candles  # noqa: E402

COUNT = 5000  # 1h bars -> up to ~208 days (coins return fewer if younger)


def enumerate_xyz():
    data = _http_post("/info", {"type": "metaAndAssetCtxs", "dex": "xyz"})
    if not (isinstance(data, list) and len(data) >= 2):
        print("FATAL: could not fetch xyz meta", file=sys.stderr)
        sys.exit(1)
    meta, ctx = data[0], data[1]
    out = []
    for i, u in enumerate(meta.get("universe", [])):
        name = u.get("name")          # ALREADY "xyz:SYMBOL"
        if not name:
            continue
        c = ctx[i] if i < len(ctx) else {}
        out.append({"coin": name, "dayNtlVlm": float(c.get("dayNtlVlm") or 0),
                    "maxLeverage": u.get("maxLeverage")})
    out.sort(key=lambda x: x["dayNtlVlm"], reverse=True)
    return out


def main():
    universe = enumerate_xyz()
    print(f"xyz universe: {len(universe)} markets", flush=True)
    cache = {"meta": {"universe": universe, "fetched_at": int(time.time() * 1000),
                      "count_requested": COUNT}, "candles": {}}
    for j, m in enumerate(universe):
        coin = m["coin"]
        try:
            candles = fetch_hl_candles(coin, "1h", COUNT)
        except Exception as e:
            print(f"  {coin}: ERROR {e}", flush=True)
            candles = []
        cache["candles"][coin] = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in candles]
        print(f"  [{j+1}/{len(universe)}] {coin}: {len(candles)} bars "
              f"(vlm ${m['dayNtlVlm']:,.0f})", flush=True)
        with open(OUT, "w") as f:      # incremental write — survive a kill
            json.dump(cache, f)
        time.sleep(0.5)
    nb = [len(v) for v in cache["candles"].values()]
    print(f"\nwrote {OUT}\nbars/coin: min={min(nb)} med={sorted(nb)[len(nb)//2]} "
          f"max={max(nb)}", flush=True)


if __name__ == "__main__":
    main()
