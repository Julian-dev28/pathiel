"""Trace MANTA's market conditions before/at the +100% run, to extract a 'fingerprint'."""
import os, sys, statistics, datetime
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
for line in open("/Users/julian_dev/Documents/code/hermes-trader/.env.local"):
    line=line.strip()
    if line and not line.startswith("#") and "=" in line:
        k,_,v=line.partition("="); os.environ.setdefault(k.strip(),v.strip())
from hermes_trader.client.hl_client import fetch_hl_candles

def atr(bars, i, n=14):
    trs=[]
    for j in range(max(1,i-n+1), i+1):
        h,l,pc=bars[j].h,bars[j].l,bars[j-1].c
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(trs)/len(trs) if trs else 0

bars = fetch_hl_candles("MANTA","1h",600)
print(f"MANTA: {len(bars)} 1h bars, range {min(b.l for b in bars):.4f}..{max(b.h for b in bars):.4f}")

# find the breakout bar = the first bar with the biggest run start (largest 1h % with vol)
moves=[(i, bars[i].c/bars[i].o-1) for i in range(1,len(bars)) if bars[i].o>0]
bi = max(moves, key=lambda x: x[1])[0]   # biggest 1h up bar = the ignition
t=datetime.datetime.utcfromtimestamp(bars[bi].t/1000).strftime('%m-%d %H:%M')
print(f"\nIGNITION bar #{bi} @ {t}: {100*(bars[bi].c/bars[bi].o-1):+.1f}% (close {bars[bi].c:.4f})")

# pre-breakout conditions (the W=48h window BEFORE ignition)
W=48
pre=bars[bi-W:bi]
pre_hi=max(b.h for b in pre); pre_lo=min(b.l for b in pre); pre_close=bars[bi-1].c
base_range=(pre_hi-pre_lo)/pre_lo
atr_pre=atr(bars, bi-1); atr_pct_pre=atr_pre/pre_close
# vol compression: ATR over last 12h vs the 12h before that
atr_recent=atr(bars,bi-1,12); atr_older=atr(bars,bi-13,12)
compression=atr_recent/atr_older if atr_older>0 else None
# volume surge at ignition
vmean_pre=statistics.mean([b.v for b in pre]) or 1e-9
vol_surge=bars[bi].v/vmean_pre
# how far did it run from ignition close
peak=max(b.h for b in bars[bi:])
run=peak/bars[bi].c-1

print(f"\n--- MANTA FINGERPRINT (the {W}h before ignition) ---")
print(f"  base range (48h):        {100*base_range:.1f}%   (tight base = coiled)")
print(f"  ATR% pre-breakout:       {100*atr_pct_pre:.2f}%   (low = compressed)")
print(f"  vol-compression ratio:   {compression:.2f}   (<1 = vol contracting into the move)")
print(f"  volume surge at ignition:{vol_surge:.1f}x trailing avg")
print(f"  breakout vs 48h high:    {100*(bars[bi].c/pre_hi-1):+.1f}% above prior range high")
print(f"  -> forward run from ignition close: +{100*run:.0f}%")
