"""W-A5 rank_weighting_schemes — implementation alpha on the LIVE xs-momentum book.

Live book: ranking=pct_k (14d channel), k=8/leg, hold=10, market-neutral. Hold the SAME
selections, vary only the within-leg WEIGHTS:
  EQUAL  — current (1/8 each).
  RANK   — linear rank weights (strongest signal gets most), normalized within leg.
  INVVOL — weight ∝ 1/realized_vol_20d (bars<=i), normalized within leg.
Book per-rebal return = sum(w_i_long*fwd_i) - sum(w_i_short*fwd_i). Report EV, OOS both
halves, Sharpe, avg coin-level turnover, and a turnover-fee-adjusted Sharpe (12bps * turnover).
Lookahead-safe: rank/vol on bars<=i, fill open[i+1]->[i+1+H].
"""
import statistics
import alpha_lib as al
from alpha_lib import O, H as HI, L, C

d = al.load_dataset()
SER={c:al.candles(d,c,"1d") for c in d["coins"] if len(al.candles(d,c,"1d"))>=60}
N=min(len(b) for b in SER.values()); ARR={c:SER[c][-N:] for c in SER}

def pctk(bars,i,n=14):
    seg=bars[i-n+1:i+1]
    if len(seg)<n: return None
    hi=max(b[HI] for b in seg); lo=min(b[L] for b in seg); cur=bars[i][C]
    return (cur-lo)/(hi-lo)-0.5 if hi>lo else None
def rvol(bars,i,w=20):
    rs=[bars[j][C]/bars[j-1][C]-1 for j in range(i-w+1,i+1) if bars[j-1][C]>0]
    return statistics.pstdev(rs) if len(rs)>=8 else None
def fwd(c,i,hold):
    bc=ARR[c]; e=i+1; x=i+1+hold
    if x>=len(bc) or bc[e][O]<=0: return None
    return bc[x][O]/bc[e][O]-1.0
def sharpe(xs):
    if len(xs)<3: return float('nan')
    sd=statistics.pstdev(xs); return statistics.mean(xs)/sd if sd>0 else float('nan')

def weights(scheme, members, sigs, i):
    k=len(members)
    if scheme=="equal":
        w=[1.0]*k
    elif scheme=="rank":
        order=sorted(range(k), key=lambda j: sigs[j], reverse=True)  # strongest first
        rk=[0]*k
        for pos,j in enumerate(order): rk[j]=k-pos   # k..1
        w=[float(x) for x in rk]
    else:  # invvol
        w=[]
        for c in members:
            v=rvol(ARR[c],i)
            w.append(1.0/v if (v and v>0) else 0.0)
        if sum(w)==0: w=[1.0]*k
    s=sum(w); return [x/s for x in w]

def run(hold, step, k=8):
    series={"equal":[],"rank":[],"invvol":[]}
    turn={"equal":[],"rank":[],"invvol":[]}
    prevw={"equal":{}, "rank":{}, "invvol":{}}
    start=32; i=start
    while i<N-hold-2:
        sc=[(c,pctk(ARR[c],i,14)) for c in ARR]; sc=[(c,v) for c,v in sc if v is not None]
        if len(sc)<2*k: i+=step; continue
        sc.sort(key=lambda x:x[1],reverse=True)
        longs=[c for c,_ in sc[:k]]; lsig=[v for _,v in sc[:k]]
        shorts=[c for c,_ in sc[-k:]]; ssig=[v for _,v in sc[-k:]]
        for scheme in series:
            wl=weights(scheme,longs,lsig,i); ws=weights(scheme,shorts,[-v for v in ssig],i)
            lr=[(c,fwd(c,i,hold),w) for c,w in zip(longs,wl)]
            sr=[(c,fwd(c,i,hold),w) for c,w in zip(shorts,ws)]
            if any(x[1] is None for x in lr+sr): continue
            ret=sum(w*r for _,r,w in lr)-sum(w*r for _,r,w in sr)
            series[scheme].append(0.5*ret)  # per-leg scale to match other findings
            # turnover at coin-signed-weight level
            cur={('L',c):w for c,_,w in lr}; cur.update({('S',c):w for c,_,w in sr})
            pv=prevw[scheme]
            keys=set(cur)|set(pv)
            t=sum(abs(cur.get(kk,0)-pv.get(kk,0)) for kk in keys)/2.0
            turn[scheme].append(t); prevw[scheme]=cur
        i+=step
    print(f"\n==== hold={hold} step={step} k={k} ====")
    for scheme in ("equal","rank","invvol"):
        r=series[scheme]; mid=len(r)//2; to=statistics.mean(turn[scheme]) if turn[scheme] else 0
        net=[x-0.0012*t for x,t in zip(r,turn[scheme])]
        print(f"  {scheme:7} n={len(r):3} EV={statistics.mean(r)*100:+.3f}% Sh full={sharpe(r):+.3f} h1={sharpe(r[:mid]):+.3f} h2={sharpe(r[mid:]):+.3f} | net12 EV={statistics.mean(net)*100:+.3f}% Sh={sharpe(net):+.3f} | turnover={to:.2f}")

run(hold=10,step=10); run(hold=7,step=7)
run(hold=10,step=1); run(hold=7,step=1)
