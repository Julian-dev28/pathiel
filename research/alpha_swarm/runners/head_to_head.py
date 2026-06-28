"""Head-to-head: does the runner EQUATION add over the bot's EXISTING composite trigger?
Compute the REAL composite (actual trigger fns + weights) per breakout, then nested-model AUC:
  A) composite only   B) my features only   C) composite + my features.
If C ~= A, my equation is redundant (the bot already does it). If C >> A, it's additive."""
import json, sys
import numpy as np
from pathlib import Path
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.indicators import triggers as T
from hermes_trader.agents.config import TRIGGER_CONFIG
from hermes_trader.models.types import Candle

SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
d = json.loads((SCR/"movers_dataset.json").read_text())   # v1 (160 coins) for speed
W=48; FEE=0.0012; wt=TRIGGER_CONFIG["weights"]; th=TRIGGER_CONFIG["thresholds"]
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def uw(b): r=b[2]-b[3]; return (b[2]-max(b[1],b[4]))/r if r>0 else 0

def composite(cd):
    hits=[T.pct_move_spike(cd, th["sigmaThreshold"]), T.volume_spike(cd, th["sigmaThreshold"]),
          T.breakout(cd, th["breakoutLookback"]), T.shock_day(cd),
          T.range_compression(cd, th["bbLength"], th["bbStdDev"]), T.trend_strength(cd, th["adxPeriod"]),
          T.momentum_burst(cd, th["momentumLookback"], th["momentumPct"])]
    return T.composite_score(hits, wt)

X=[]; comp=[]; y=[]; rets=[]; ts=[]
for coin in d["meta"]["coins"]:
    bars=d["candles"].get(coin,{}).get("1h",[])
    if len(bars)<W+50: continue
    cds=[Candle(t=b[0],o=b[1],h=b[2],l=b[3],c=b[4],v=b[5]) for b in bars]
    i=W
    while i<len(bars)-50:
        hi=max(b[2] for b in bars[i-W:i]); vmean=mean([b[5] for b in bars[i-W:i]]) or 1e-9
        c0=bars[i-W][4]; ext=bars[i][4]/c0-1 if c0>0 else 9
        if bars[i][4]>hi and bars[i][5]>=1.5*vmean and bars[i][4]>bars[i][1] and ext<=0.30:
            f=[ mean([b[5] for b in bars[i-6:i]])/(mean([b[5] for b in bars[i-24:i-6]]) or 1e-9),
                bars[i][4]/bars[i-12][4]-1 if bars[i-12][4]>0 else 0,
                (bars[i][4]/bars[i-6][4]-1)-(bars[i-6][4]/bars[i-12][4]-1) if bars[i-6][4]>0 and bars[i-12][4]>0 else 0,
                1-mean([uw(bars[j]) for j in range(i-3,i+1)]), ext, bars[i][5]/vmean ]
            entry=bars[i+1][1]
            if entry>0:
                fwd=bars[i+1:i+1+48]; mfe=max(b[2] for b in fwd)/entry-1
                X.append(f); comp.append(composite(cds[:i+1])); y.append(1 if mfe>=0.5 else 0); ts.append(bars[i][0])
            i+=48
        else: i+=1

X=np.array(X); comp=np.array(comp).reshape(-1,1); y=np.array(y,float); ts=np.array(ts)
n=len(y); print(f"# {n} breakouts, {int(y.sum())} runners. composite range {comp.min():.0f}-{comp.max():.0f}, mean {comp.mean():.1f}")
def z(M): return (M-M.mean(0))/(M.std(0)+1e-9)
def fit(Xtr,ytr,it=3000,lr=0.3,l2=1.0):
    w=np.zeros(Xtr.shape[1]); b=0.
    for _ in range(it):
        p=1/(1+np.exp(-(Xtr@w+b))); w-=lr*(Xtr.T@(p-ytr)/len(ytr)+l2*w/len(ytr)); b-=lr*(p-ytr).mean()
    return w,b
def auc(yt,s):
    P=s[yt==1]; N=s[yt==0]
    return sum((p>N).sum()+0.5*(p==N).sum() for p in P)/(len(P)*len(N)) if len(P) and len(N) else .5

order=np.argsort(ts); h=n//2
Cz=z(comp); Xz=z(X); CX=np.hstack([Cz,Xz])
for name,M in (("A composite-only",Cz),("B my-features-only",Xz),("C composite+features",CX)):
    Mo=M[order]; w,b=fit(Mo[:h],y[order][:h]); s=Mo[h:]@w+b
    print(f"  {name:<22} OOS AUC {auc(y[order][h:],s):.3f}")
# does composite ALONE separate runners? (the bot's existing signal)
print(f"\n# OOS EV by COMPOSITE top-quartile (the bot's existing rank) vs my-equation top-quartile:")
yo=y[order];
for label,M in (("composite",Cz),("my-equation",Xz)):
    Mo=M[order]; w,b=fit(Mo[:h],yo[:h]); s=Mo[h:]@w+b; thr=np.quantile(s,0.75)
    rr_top=yo[h:][s>=thr].mean()*100; rr_rest=yo[h:][s<thr].mean()*100
    print(f"  {label:<12} top-q runner {rr_top:.1f}% vs rest {rr_rest:.1f}%")
