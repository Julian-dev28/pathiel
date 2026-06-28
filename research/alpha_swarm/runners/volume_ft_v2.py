"""Volume follow-through on 5m SMALL-CAP movers. Does a 2nd-candle volume confirm separate
runners from pump-and-dumps? Sweep the breakout-vol (Bx) and confirm-vol (Cx) thresholds.
Enter at close of confirm candle -> next bar open (lookahead-safe). Tight-floor exit + raw fwd."""
import json, statistics
from pathlib import Path
SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
d = json.loads((SCR/"movers_5m.json").read_text())
O,H,L,C,V = 1,2,3,4,5; W=48
def mean(xs): return sum(xs)/len(xs) if xs else 0.0

def tight_floor(entry, fwd, gb=0.10, stop=0.20):
    peak=entry; armed=False
    for b in fwd:
        peak=max(peak,b[H]); g=peak/entry-1
        if b[L]<=entry*(1-stop): return -stop
        if g>=0.01: armed=True
        if armed and b[L]<=entry*(1+g*(1-gb)): return g*(1-gb)
    return fwd[-1][C]/entry-1

def run(Bx, Cx):
    conf, unconf = [], []
    for coin in d["meta"]["coins"]:
        bars=d["candles"].get(coin,{}).get("5m",[])
        if len(bars)<W+60: continue
        i=W
        while i<len(bars)-60:
            vmean=mean([b[V] for b in bars[i-W:i]]) or 1e-9
            hi=max(b[H] for b in bars[i-W:i])
            if bars[i][C]>hi and bars[i][V]>=Bx*vmean and bars[i][C]>bars[i][O]:
                confirm = bars[i+1][V]>=Cx*vmean
                entry=bars[i+2][O] if i+2<len(bars) else 0
                if entry>0:
                    fwd=bars[i+2:i+2+48]
                    rec={"mfe":max(b[H] for b in fwd)/entry-1,
                         "tf":tight_floor(entry,fwd)-0.0012,"t":bars[i][0]}
                    (conf if confirm else unconf).append(rec)
                i+=24
            else: i+=1
    return conf, unconf

def stats(g, label):
    if len(g)<10: print(f"  {label:<24} n={len(g)} (too few)"); return
    g=sorted(g,key=lambda x:x["t"]); half=len(g)//2
    run20=sum(1 for x in g if x["mfe"]>=0.20)/len(g); run50=sum(1 for x in g if x["mfe"]>=0.50)/len(g)
    ev=mean([x["tf"] for x in g]); h1=mean([x["tf"] for x in g[:half]]); h2=mean([x["tf"] for x in g[half:]])
    flag="✅" if h1>0 and h2>0 else "  "
    print(f"  {label:<24} n={len(g):<4} run>=20% {100*run20:4.1f}% run>=50% {100*run50:4.1f}% | "
          f"tight-floor EV {100*ev:+.2f}% OOS {100*h1:+.2f}/{100*h2:+.2f} {flag}")

for Bx in (2,3,4):
    for Cx in (1.0,1.5,2.0):
        conf,unconf=run(Bx,Cx)
        print(f"\n# breakout>={Bx}x vol, confirm next candle>={Cx}x vol:")
        stats(conf,   f"CONFIRMED ({Bx}x+{Cx}x)")
        stats(unconf, f"UNCONFIRMED ({Bx}x, <{Cx}x)")
