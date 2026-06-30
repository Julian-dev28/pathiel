"""STEP 1: fetch 1h candles for the xyz HIP-3 universe (tokenized stocks/commodities).

Output: scratchpad/hip3_1h.json  shape:
  {"meta":{"coins":[...], "fetched_at":...}, "candles":{coin:[[t,o,h,l,c,v],...]}}
"""
import json, time, os
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles

OUT = os.path.join(os.path.dirname(__file__), "hip3_1h.json")

u = get_universe(force_refresh=True, include_hip3=True)
coins = [x["coin"] for x in u if x.get("dex") == "xyz" and ":" in x["coin"]]
coins = sorted(set(coins))
print(f"xyz HIP-3 coins to fetch: {len(coins)}")

candles = {}
full = []
for i, coin in enumerate(coins):
    try:
        cs = fetch_hl_candles(coin, "1h", 5000)
    except Exception as e:
        print(f"  {coin}: ERROR {e}")
        cs = []
    rows = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]
    candles[coin] = rows
    if rows:
        span_days = (rows[-1][0] - rows[0][0]) / 86400000.0
        if span_days >= 180:
            full.append(coin)
        print(f"  [{i+1}/{len(coins)}] {coin}: {len(rows)} bars, {span_days:.0f}d")
    else:
        print(f"  [{i+1}/{len(coins)}] {coin}: EMPTY")
    time.sleep(0.08)

meta = {"coins": coins, "fetched_at": int(time.time()),
        "full_history_coins": full, "n_full_history": len(full)}
with open(OUT, "w") as f:
    json.dump({"meta": meta, "candles": candles}, f)
print(f"\nSaved {OUT}")
print(f"coins with >=180d history: {len(full)}")
