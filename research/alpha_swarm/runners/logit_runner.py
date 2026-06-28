"""Find the EQUATION: logistic regression P(runner) = sigmoid(w.x + b) on breakout features.
Standardized coefficients = which params matter + how much. Time-OOS: fit H1, validate H2.
Then: does trading the top-decile-by-fitted-prob beat taking all breakouts (tight-floor exit)?"""
import json, statistics
import numpy as np
from pathlib import Path
SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
prog = Path("/tmp/movers-v2/progress.log")
ds = SCR/"movers_v2.json" if (SCR/"movers_v2.json").exists() and prog.exists() and "MOVERS v2 done" in prog.read_text() else SCR/"movers_dataset.json"
d = json.loads(ds.read_text())
O,H,L,C,V = 1,2,3,4,5; W=48; FEE=0.0012
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def uw(b): r=b[H]-b[L]; return (b[H]-max(b[O],b[C]))/r if r>0 else 0

X=[]; y=[]; rets=[]; ts=[]
FEATS=["vol_trend","momentum","accel","low_wick","ext","vol_surge","base_range"]
for coin in d["meta"]["coins"]:
    bars=d["candles"].get(coin,{}).get("1h",[])
    if len(bars)<W+50: continue
    i=W
    while i<len(bars)-50:
        hi=max(b[H] for b in bars[i-W:i]); vmean=mean([b[V] for b in bars[i-W:i]]) or 1e-9
        c0=bars[i-W][C]; ext=bars[i][C]/c0-1 if c0>0 else 9
        if bars[i][C]>hi and bars[i][V]>=1.5*vmean and bars[i][C]>bars[i][O] and ext<=0.30:
            f=[ mean([b[V] for b in bars[i-6:i]])/(mean([b[V] for b in bars[i-24:i-6]]) or 1e-9),
                bars[i][C]/bars[i-12][C]-1 if bars[i-12][C]>0 else 0,
                (bars[i][C]/bars[i-6][C]-1)-(bars[i-6][C]/bars[i-12][C]-1) if bars[i-6][C]>0 and bars[i-12][C]>0 else 0,
                1-mean([uw(bars[j]) for j in range(i-3,i+1)]),
                ext,
                bars[i][V]/vmean,
                (hi-min(b[L] for b in bars[i-W:i]))/(min(b[L] for b in bars[i-W:i]) or 1e-9) ]
            entry=bars[i+1][O]
            if entry>0:
                fwd=bars[i+1:i+1+48]; mfe=max(b[H] for b in fwd)/entry-1
                # tight-floor realized return (gb.10)
                peak=entry; armed=False; r=None
                for b in fwd:
                    peak=max(peak,b[H]); g=peak/entry-1
                    if b[L]<=entry*0.65: r=-0.35; break
                    if g>=0.01: armed=True
                    if armed and b[L]<=entry*(1+g*0.9): r=g*0.9; break
                if r is None: r=fwd[-1][C]/entry-1
                X.append(f); y.append(1 if mfe>=0.5 else 0); rets.append(r-FEE); ts.append(bars[i][0])
            i+=48
        else: i+=1

X=np.array(X); y=np.array(y,float); rets=np.array(rets); ts=np.array(ts)
n=len(y); print(f"# {ds.name}: {n} breakouts, {int(y.sum())} runners ({100*y.mean():.1f}%)")
mu=X.mean(0); sd=X.std(0)+1e-9; Xs=(X-mu)/sd     # standardize

def fit(Xtr,ytr,iters=4000,lr=0.3,l2=1.0):
    w=np.zeros(Xtr.shape[1]); b=0.0
    for _ in range(iters):
        p=1/(1+np.exp(-(Xtr@w+b)))
        gw=Xtr.T@(p-ytr)/len(ytr)+l2*w/len(ytr); gb=(p-ytr).mean()
        w-=lr*gw; b-=lr*gb
    return w,b
def auc(yt,sc):
    pos=sc[yt==1]; neg=sc[yt==0]
    if len(pos)==0 or len(neg)==0: return 0.5
    return sum((p>neg).sum()+0.5*(p==neg).sum() for p in pos)/(len(pos)*len(neg))

# full-sample equation (for interpretation)
w,b=fit(Xs,y)
print("\n# THE EQUATION  P(runner)=sigmoid(z): standardized coefficients (bigger |w| = more predictive)")
for f,wi in sorted(zip(FEATS,w), key=lambda t:-abs(t[1])):
    print(f"   {f:<12} {wi:+.3f}")
print(f"   intercept    {b:+.3f}   |  full-sample AUC {auc(y,Xs@w+b):.3f}")

# TIME-OOS: fit first half, validate second half
order=np.argsort(ts); Xs,y2,rets2=Xs[order],y[order],rets[order]; h=n//2
wt,bt=fit(Xs[:h],y2[:h]); sc=Xs[h:]@wt+bt
print(f"\n# OOS (fit H1 -> predict H2): AUC {auc(y2[h:],sc):.3f}  (0.5=coin flip)")
oos_r=rets2[h:]; thr=np.quantile(sc,0.75)
top=oos_r[sc>=thr]; rest=oos_r[sc<thr]
print(f"  OOS top-quartile-by-model breakouts: n={len(top)} EV {100*top.mean():+.2f}% runner {100*(y2[h:][sc>=thr]).mean():.1f}%")
print(f"  OOS rest:                            n={len(rest)} EV {100*rest.mean():+.2f}% runner {100*(y2[h:][sc<thr]).mean():.1f}%")
print(f"  OOS all:                             n={len(oos_r)} EV {100*oos_r.mean():+.2f}%")
