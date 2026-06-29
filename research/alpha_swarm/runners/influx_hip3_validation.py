#!/usr/bin/env python3
"""Validate whether the volume-influx books are RIGHT to EXCLUDE HIP-3 (xyz:*) markets.

Operator's "immediate" volume-influx LONG:
  ENTRY = a GREEN 5m candle whose volume >= 1.5x the PREVIOUS candle's volume -> long next bar open.
  EXIT  = tight profit-floor (gb=0.10), hard stop 0.15, max-hold 96 bars (8h). Net 12bps.
Lookahead-safe (decide on complete bar i, fill i+1 open). OOS both halves + matched random null.

Runs the SAME engine on crypto (movers_5m.json) and HIP-3 (hip3_5m.json) so the compare is
apples-to-apples. Also sweeps an absolute $-notional floor (close*vol) on the influx candle.
Quantifies "gameability": fraction of influx candles immediately reversed (next bar red).
SURVIVOR universe => upper bound. Read-only.
"""
import json, statistics
from pathlib import Path
import numpy as np

SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
O, H, L, C, V = 1, 2, 3, 4, 5
FEE = 0.0012
GB = 0.10
STOP = 0.15
MAXHOLD = 96
RNG = np.random.default_rng(13)

def mean(xs): return sum(xs)/len(xs) if xs else 0.0

def floor_exit(bars, i, entry):
    """tight profit-floor exit starting from entry at bar i+1 open. Returns net ret."""
    peak = entry; armed = False; ret = None
    for j in range(i+1, min(i+1+MAXHOLD, len(bars))):
        b = bars[j]; peak = max(peak, b[H]); g = peak/entry - 1
        if b[L] <= entry*(1-STOP): ret = -STOP; break
        if g >= 0.01: armed = True
        if armed and b[L] <= entry*(1+g*(1-GB)): ret = g*(1-GB); break
    if ret is None:
        ret = bars[min(i+1+MAXHOLD, len(bars)-1)][C]/entry - 1
    return ret - FEE

def events(d, dollar_floor=0.0):
    """All immediate-influx LONG entries (and forward stats) for a dataset."""
    rows = []
    for coin in d["meta"]["coins"]:
        bars = d["candles"].get(coin, {}).get("5m", [])
        if len(bars) < MAXHOLD + 3: continue
        i = 1
        while i < len(bars) - MAXHOLD - 2:
            b = bars[i]; pv = bars[i-1][V]
            green = b[C] > b[O]
            influx = b[V] >= 1.5*pv
            notl = b[C]*b[V]
            if green and influx and notl >= dollar_floor:
                entry = bars[i+1][O]
                if entry <= 0: i += 1; continue
                mfe = max(x[H] for x in bars[i+1:i+1+MAXHOLD])/entry - 1
                ret = floor_exit(bars, i, entry)
                # immediate reversal: next bar red AND closes below influx open (gives back the spike)
                nb = bars[i+1]
                reversed_ = (nb[C] < nb[O]) and (nb[C] < b[O])
                rows.append({"coin": coin, "t": b[0], "ret": ret, "mfe": mfe,
                             "notl": notl, "rev": reversed_})
                i += MAXHOLD//2  # cooldown (matches operator engine)
            else:
                i += 1
    return rows

def random_null(d, n_per_coin):
    """matched random-entry null: same coins, same approx count, same exit."""
    rets = []
    for coin in d["meta"]["coins"]:
        bars = d["candles"].get(coin, {}).get("5m", [])
        if len(bars) < MAXHOLD + 3: continue
        k = n_per_coin.get(coin, 0)
        if k == 0: continue
        hi = len(bars) - MAXHOLD - 2
        if hi <= 1: continue
        for _ in range(k):
            i = int(RNG.integers(1, hi))
            entry = bars[i+1][O]
            if entry <= 0: continue
            rets.append(floor_exit(bars, i, entry))
    return rets

def summ(rows, label):
    if len(rows) < 20:
        print(f"  {label:<34} n={len(rows)} (too few)"); return None
    r = [x["ret"] for x in rows]
    rows2 = sorted(rows, key=lambda x: x["t"]); h = len(rows2)//2
    h1 = mean([x["ret"] for x in rows2[:h]]); h2 = mean([x["ret"] for x in rows2[h:]])
    m = mean(r); med = statistics.median(r)
    win = sum(1 for x in r if x>0)/len(r)
    run10 = sum(1 for x in rows if x["mfe"]>=0.10)/len(rows)
    run20 = sum(1 for x in rows if x["mfe"]>=0.20)/len(rows)
    flag = "+EV both" if h1>0 and h2>0 else ("-EV both" if h1<0 and h2<0 else "mixed")
    print(f"  {label:<34} n={len(rows):<5} EV {100*m:+.3f}% med {100*med:+.2f}% win {100*win:.0f}% "
          f"run10 {100*run10:.0f}% run20 {100*run20:.0f}% OOS {100*h1:+.3f}/{100*h2:+.3f} [{flag}]")
    return {"n": len(rows), "ev": m, "win": win, "run10": run10, "run20": run20,
            "h1": h1, "h2": h2, "med": med}

def revfrac(rows):
    if not rows: return float("nan")
    return sum(1 for x in rows if x["rev"])/len(rows)

def run_universe(name, fn):
    d = json.loads((SCR/fn).read_text())
    nc = sum(1 for c in d["meta"]["coins"] if d["candles"].get(c,{}).get("5m"))
    print(f"\n=== {name}  ({nc} coins with data) ===")
    base = events(d, 0.0)
    npc = {}
    for x in base: npc[x["coin"]] = npc.get(x["coin"],0)+1
    null = random_null(d, npc)
    nullev = mean(null) if null else float("nan")
    print(f"  matched-random NULL EV: {100*nullev:+.3f}%  (n={len(null)})")
    s = summ(base, "no-floor (1.5x prev, green)")
    if s: print(f"      -> EXCESS over null: {100*(s['ev']-nullev):+.3f}%")
    print(f"  immediate-reversal frac (next bar red & < influx open): {100*revfrac(base):.1f}%")
    rows_by_floor = {0: base}
    for fl in (25_000, 50_000, 100_000, 250_000):
        rr = events(d, float(fl))
        rows_by_floor[fl] = rr
        summ(rr, f"$-floor >= {fl//1000}k")
    return {"name": name, "ncoins": nc, "null": nullev, "base": s,
            "rev": revfrac(base), "rows_by_floor": rows_by_floor}

if __name__ == "__main__":
    print("# IMMEDIATE volume-influx LONG (1.5x PREV-candle green vol), tight-floor exit, net 12bps")
    crypto = run_universe("CRYPTO (movers_5m, 180 main-perp movers)", "movers_5m.json")
    hip3 = run_universe("HIP-3 (hip3_5m, xyz tokenized stocks/commodities)", "hip3_5m.json")
