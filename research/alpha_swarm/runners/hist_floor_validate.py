#!/usr/bin/env python3
"""Validate the history floor: is a HIP-3 name's TA signal tradeable in its YOUNG
period (age between ~60 hourly bars [2.5d] and 60 daily bars [60d]) that the current
60-DAILY floor blocks? Bucket every breakout-momentum signal by coin-AGE-at-signal and
measure forward EV per bucket (lookahead-safe: decide on bars<=i, enter i+1 open).

If the <2.5d bucket is -EV noise but the 2.5-60d bucket has edge comparable to 60d+,
then a 60-HOURLY-bar floor safely admits young HIP-3 names sooner without trusting
garbage 5-bar TA. If the 2.5-60d bucket is ALSO -EV, keep the 60-daily floor.
"""
import json, os, sys, time, statistics
from pathlib import Path
_REPO = Path("/Users/julian_dev/Documents/code/hermes-trader"); sys.path.insert(0, str(_REPO))
for _l in (_REPO/".env.local").read_text().splitlines():
    _l=_l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k,_,v=_l.partition("="); os.environ.setdefault(k.strip(), v.strip())
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles

SCR = Path(__file__).resolve().parent
CACHE = SCR/"hip3_hourly.json"
HOUR_MS = 3_600_000
W = 24                 # trailing window for breakout-high + vol-mean (24h)
FEE = 0.0012           # ~12bps round-trip
HORIZON = 24           # forward hours to grade
def mean(xs): return sum(xs)/len(xs) if xs else 0.0

def build():
    uni = get_universe(force_refresh=True, include_hip3=True)
    xyz = [u["coin"] for u in uni if u.get("dex") == "xyz" and ":" in (u.get("coin") or "")]
    print(f"# {len(xyz)} xyz HIP-3 coins; fetching hourly...")
    data = {}
    for i, c in enumerate(xyz, 1):
        try:
            cs = fetch_hl_candles(c, "1h", 5000)
            data[c] = [[x.t, x.o, x.h, x.l, x.c, x.v] for x in cs]
        except Exception as e:
            data[c] = []; print(f"  WARN {c}: {e}")
        if i % 20 == 0: print(f"  {i}/{len(xyz)}")
        time.sleep(0.05)
    CACHE.write_text(json.dumps(data))
    return data

data = json.loads(CACHE.read_text()) if CACHE.exists() else build()
O,H,L,C,V = 1,2,3,4,5

# Age buckets in HOURS-since-first-bar. The boundaries map to the proposed floors:
#   <60h   = younger than a 60-HOURLY-bar floor would admit (the true garbage zone)
#   60-1440h (2.5-60d) = the band the 60-DAILY floor BLOCKS but a 60-hourly floor ADMITS
#   >1440h (60d+) = mature, currently admitted
BUCKETS = [("<60h (blocked by both)", 0, 60),
           ("60-1440h / 2.5-60d (the GAP)", 60, 1440),
           (">1440h / 60d+ (mature, live)", 1440, 10**9)]

def grade(coin, bars, lo, hi):
    """All breakout-momentum signals whose coin-age (hours since first bar) is in [lo,hi)."""
    rows = []
    if len(bars) < W + HORIZON + 2: return rows
    t0 = bars[0][0]
    for i in range(W, len(bars) - HORIZON - 1):
        age_h = (bars[i][0] - t0) / HOUR_MS
        if not (lo <= age_h < hi): continue
        win = bars[i-W:i]
        vmean = mean([b[V] for b in win]) or 1e-9
        hiwin = max(b[H] for b in win)
        # entry signal = new 24h high + green + volume confirm (the live breakout/burst shape)
        if not (bars[i][C] > hiwin and bars[i][C] > bars[i][O] and bars[i][V] >= 1.5*vmean):
            continue
        entry = bars[i+1][O]
        if entry <= 0: continue
        fwd = bars[i+1:i+1+HORIZON]
        ret = fwd[-1][C]/entry - 1 - FEE          # raw hold-to-horizon, net of cost
        mfe = max(b[H] for b in fwd)/entry - 1
        rows.append({"ret": ret, "mfe": mfe, "t": bars[i][0]})
    return rows

print(f"\n# breakout-momentum LONG on xyz HIP-3, forward {HORIZON}h, net {FEE*1e4:.0f}bps, bucketed by coin AGE:")
print(f"# {'bucket':<34} {'n':>5} {'meanRet':>9} {'win%':>6} {'run>=10%':>9} {'OOS h1/h2':>16}")
for label, lo, hi in BUCKETS:
    allrows = []
    for coin, bars in data.items():
        allrows += grade(coin, bars, lo, hi)
    if len(allrows) < 15:
        print(f"  {label:<34} {len(allrows):>5}  (too few)"); continue
    allrows.sort(key=lambda r: r["t"]); half = len(allrows)//2
    m = mean([r["ret"] for r in allrows]); win = sum(1 for r in allrows if r["ret"]>0)/len(allrows)
    run = sum(1 for r in allrows if r["mfe"]>=0.10)/len(allrows)
    h1 = mean([r["ret"] for r in allrows[:half]]); h2 = mean([r["ret"] for r in allrows[half:]])
    flag = "  +EV both" if h1>0 and h2>0 else ("  -EV both" if h1<0 and h2<0 else "  mixed")
    print(f"  {label:<34} {len(allrows):>5} {100*m:>+8.2f}% {100*win:>5.0f}% {100*run:>8.0f}% "
          f"{100*h1:>+6.2f}/{100*h2:>+6.2f}{flag}")
print(f"\n# coins by max age (history depth) — how many sit in the GAP band right now:")
ages = sorted(((b[-1][0]-b[0][0])/HOUR_MS/24, c) for c,b in data.items() if b)
young = [(a,c) for a,c in ages if a < 60]
print(f"  {len(young)} coins < 60d old (floor-blocked today): " + ", ".join(f"{c}={a:.0f}d" for a,c in young[:20]))
