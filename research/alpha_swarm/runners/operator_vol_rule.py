#!/usr/bin/env python3
"""Test the OPERATOR's exact rule on 5m runner-universe data (180 small-cap movers):
  ENTRY: a GREEN 5m candle whose volume >= Bx * trailing-mean volume (volume influx).
  EXIT:  sell when a RED candle's volume >= Rx * the LAST GREEN candle's volume
         (the reversal showing up in volume), else a hard stop / max hold.
Lookahead-safe: decide entry on bar i (complete) -> enter i+1 open; exit decision on a
complete bar -> fill next open. Net of 12bps round-trip. OOS both halves + runner capture.
"""
import json, statistics
from pathlib import Path
SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
d = json.loads((SCR/"movers_5m.json").read_text())
O,H,L,C,V = 1,2,3,4,5
FEE = 0.0012
def mean(xs): return sum(xs)/len(xs) if xs else 0.0

def run(Bx=1.5, Rx=0.8, Kvol=6, stop=0.15, maxhold=96, require_green=True):
    """Bx=entry vol mult, Rx=red/green exit ratio, Kvol=trailing window for vol-mean,
    stop=hard stop, maxhold=max bars (96*5m=8h)."""
    trades = []
    for coin in d["meta"]["coins"]:
        bars = d["candles"].get(coin, {}).get("5m", [])
        if len(bars) < Kvol + maxhold + 2: continue
        i = Kvol
        while i < len(bars) - maxhold - 2:
            vmean = mean([b[V] for b in bars[i-Kvol:i]]) or 1e-9
            green = bars[i][C] > bars[i][O]
            if bars[i][V] >= Bx*vmean and (green or not require_green):
                entry = bars[i+1][O]
                if entry <= 0: i += 1; continue
                last_green_vol = bars[i][V] if green else vmean
                ret = None
                exit_reason = "maxhold"
                for j in range(i+1, min(i+1+maxhold, len(bars)-1)):
                    b = bars[j]
                    if b[L] <= entry*(1-stop):
                        ret = -stop; exit_reason = "stop"; break
                    red = b[C] < b[O]
                    if red and b[V] >= Rx*last_green_vol:
                        ret = bars[j+1][O]/entry - 1; exit_reason = "vol_reversal"; break
                    if b[C] > b[O]:
                        last_green_vol = max(last_green_vol, b[V])  # update last green vol
                if ret is None:
                    ret = bars[min(i+1+maxhold, len(bars)-1)][C]/entry - 1
                trades.append({"ret": ret-FEE, "t": bars[i][0],
                               "mfe": max(b[H] for b in bars[i+1:i+1+maxhold])/entry-1,
                               "reason": exit_reason})
                i += maxhold//2  # cooldown
            else:
                i += 1
    return trades

def report(trades, label):
    if len(trades) < 20:
        print(f"  {label:<30} n={len(trades)} (too few)"); return
    trades.sort(key=lambda x: x["t"]); h = len(trades)//2
    m = mean([t["ret"] for t in trades]); win = sum(1 for t in trades if t["ret"]>0)/len(trades)
    run20 = sum(1 for t in trades if t["mfe"]>=0.20)/len(trades)
    h1 = mean([t["ret"] for t in trades[:h]]); h2 = mean([t["ret"] for t in trades[h:]])
    med = statistics.median([t["ret"] for t in trades])
    fl = "  +EV both" if h1>0 and h2>0 else ("  -EV both" if h1<0 and h2<0 else "  mixed")
    print(f"  {label:<30} n={len(trades):<5} EV {100*m:+.2f}% med {100*med:+.2f}% win {100*win:.0f}% "
          f"run>=20% {100*run20:.0f}% OOS {100*h1:+.2f}/{100*h2:+.2f}{fl}")

print("# OPERATOR RULE: 1.5x green-vol entry, exit when red vol >= 0.8x last green vol (5m, 180 movers, net 12bps)")
report(run(1.5, 0.8), "1.5x entry / 0.8x exit")
print("\n# entry-threshold sweep (exit fixed 0.8x red/green):")
for Bx in (1.5, 2.0, 3.0, 5.0):
    report(run(Bx, 0.8), f"{Bx}x entry / 0.8x exit")
print("\n# exit-ratio sweep (entry fixed 1.5x):")
for Rx in (0.6, 0.8, 1.0, 1.5):
    report(run(1.5, Rx), f"1.5x entry / {Rx}x exit")
print("\n# vs a simple tight-floor exit (same 1.5x entry) for comparison:")
def run_floor(Bx=1.5, gb=0.10, stop=0.15, maxhold=96, Kvol=6):
    trades=[]
    for coin in d["meta"]["coins"]:
        bars=d["candles"].get(coin,{}).get("5m",[])
        if len(bars)<Kvol+maxhold+2: continue
        i=Kvol
        while i<len(bars)-maxhold-2:
            vmean=mean([b[V] for b in bars[i-Kvol:i]]) or 1e-9
            if bars[i][V]>=Bx*vmean and bars[i][C]>bars[i][O]:
                entry=bars[i+1][O]
                if entry<=0: i+=1; continue
                peak=entry; armed=False; ret=None
                for j in range(i+1,min(i+1+maxhold,len(bars))):
                    b=bars[j]; peak=max(peak,b[H]); g=peak/entry-1
                    if b[L]<=entry*(1-stop): ret=-stop; break
                    if g>=0.01: armed=True
                    if armed and b[L]<=entry*(1+g*(1-gb)): ret=g*(1-gb); break
                if ret is None: ret=bars[min(i+1+maxhold,len(bars)-1)][C]/entry-1
                trades.append({"ret":ret-FEE,"t":bars[i][0],"mfe":max(b[H] for b in bars[i+1:i+1+maxhold])/entry-1,"reason":"floor"})
                i+=maxhold//2
            else: i+=1
    return trades
report(run_floor(1.5), "1.5x entry / tight-floor")
report(run_floor(3.0), "3.0x entry / tight-floor")
