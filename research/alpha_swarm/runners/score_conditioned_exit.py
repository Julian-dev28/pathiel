"""THE decisive test: continuation score -> conditioned exit.
Agent2 found a 3x runner-density score; agent3 found wide trails lose on ALL breakouts.
Untested cell: a WIDE trail on ONLY the HIGH-score subset (3x denser tail). Does it pay?
"""
import json, sys, statistics
from pathlib import Path
SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
ds = SCR/"movers_v2.json"
if not ds.exists() or "MOVERS v2 done" not in (Path("/tmp/movers-v2/progress.log").read_text() if Path("/tmp/movers-v2/progress.log").exists() else ""):
    ds = SCR/"movers_dataset.json"  # fall back to v1 if v2 not done
d = json.loads(ds.read_text())
print(f"# data: {ds.name}, {len(d['meta']['coins'])} coins")
O,H,L,C,V = 1,2,3,4,5
W=48; FEE=0.0012

def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def upper_wick(b):
    rng=b[H]-b[L]; return (b[H]-max(b[O],b[C]))/rng if rng>0 else 0

# collect early-breakout events with features + forward path
events=[]
for coin in d["meta"]["coins"]:
    bars=d["candles"].get(coin,{}).get("1h",[])
    if len(bars)<W+50: continue
    i=W
    while i<len(bars)-50:
        hi=max(b[H] for b in bars[i-W:i]); vmean=mean([b[V] for b in bars[i-W:i]]) or 1e-9
        c0=bars[i-W][C]
        ext=bars[i][C]/c0-1 if c0>0 else 9   # extension off base
        if bars[i][C]>hi and bars[i][V]>=1.5*vmean and bars[i][C]>bars[i][O] and ext<=0.30:
            # features (lookahead-safe, bars<=i)
            vol_trend = mean([b[V] for b in bars[i-6:i]])/(mean([b[V] for b in bars[i-24:i-6]]) or 1e-9)
            mom = bars[i][C]/bars[i-12][C]-1 if bars[i-12][C]>0 else 0
            accel = (bars[i][C]/bars[i-6][C]-1) - (bars[i-6][C]/bars[i-12][C]-1) if bars[i-6][C]>0 and bars[i-12][C]>0 else 0
            lowwick = 1 - mean([upper_wick(bars[j]) for j in range(i-3,i+1)])
            entry=bars[i+1][O]
            if entry>0:
                fwd=bars[i+1:i+1+48]
                events.append({"t":bars[i][0],"entry":entry,"fwd":fwd,
                               "vt":vol_trend,"mom":mom,"acc":accel,"lw":lowwick})
            i+=48
        else:
            i+=1

n=len(events)
# rank-normalize each feature -> composite score
for key in ("vt","mom","acc","lw"):
    order=sorted(range(n), key=lambda k: events[k][key])
    for rank,k in enumerate(order): events[k].setdefault("score",0); events[k]["score"]+=rank/n
print(f"# {n} early-breakout events")

def sim(side_entry, fwd, gb, arm=0.01, stop=0.35):
    """long trail: arm at +arm, give back gb of peak gain; hard stop at -stop. exit at horizon end else."""
    peak=side_entry; armed=False
    for b in fwd:
        peak=max(peak,b[H]); gain=peak/side_entry-1
        if b[L] <= side_entry*(1-stop): return -stop
        if gain>=arm: armed=True
        if armed and b[L] <= side_entry*(1+gain*(1-gb)): return gain*(1-gb)
    return fwd[-1][C]/side_entry-1

def report(label, subset):
    if len(subset)<20: print(f"  {label}: n={len(subset)} (too few)"); return
    subset=sorted(subset,key=lambda e:e["t"]); half=len(subset)//2
    for gb,name in ((0.10,"tight gb.10"),(0.35,"mid gb.35"),(0.65,"WIDE gb.65")):
        rets=[sim(e["entry"],e["fwd"],gb)-FEE for e in subset]
        h1=rets[:half]; h2=rets[half:]
        runner=sum(1 for e in subset if max(b[H] for b in e["fwd"])/e["entry"]-1>=0.5)/len(subset)
        flag="✅" if mean(h1)>0 and mean(h2)>0 else "  "
        print(f"  {label:<16}{name:<12} n={len(subset):<4} EV {100*mean(rets):+.2f}% win {sum(1 for r in rets if r>0)/len(rets):.2f} "
              f"runner {100*runner:.1f}% OOS {100*mean(h1):+.2f}/{100*mean(h2):+.2f} {flag}")

scores=sorted(e["score"] for e in events); q75=scores[int(0.75*n)]; q90=scores[int(0.90*n)]
print(f"\n# exit policy x score bucket (runner = fwd MFE>=50%, net 12bps):")
report("ALL breakouts", events)
report("HIGH score q75", [e for e in events if e["score"]>=q75])
report("TOP score q90", [e for e in events if e["score"]>=q90])
report("LOW score <q75", [e for e in events if e["score"]<q75])
