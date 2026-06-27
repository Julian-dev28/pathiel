"""B12 half_life_OU_sizing — demean the cross-section (strip market), build each coin's cumulative
residual, fit OU (AR1) for half-life + z-score, trade reversion (long neg-z / short pos-z). Test
(a) does residual reversion pay, (b) does sizing by 1/half-life beat equal sizing."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
W=30   # OU fit window
ZT=1.0

# demeaned residual returns (strip equal-weight market each day)
resid={c:[] for c in coins}
for t in range(RL):
    mkt=statistics.mean(ret[c][t] for c in coins)
    for c in coins: resid[c].append(ret[c][t]-mkt)
# cumulative residual spread per coin
S={c:[0.0]*(RL+1) for c in coins}
for c in coins:
    for t in range(RL): S[c][t+1]=S[c][t]+resid[c][t]

def ar1_halflife(series):
    """AR1 on series; return (z_latest, half_life). series length W+1, uses up to index -1."""
    x=series
    xm=statistics.mean(x)
    # b = cov(x_t-1, x_t)/var(x_t-1)
    xp=x[:-1]; xn=x[1:]
    mp=statistics.mean(xp)
    num=sum((xp[k]-mp)*(xn[k]-statistics.mean(xn)) for k in range(len(xp)))
    den=sum((xp[k]-mp)**2 for k in range(len(xp)))
    b=num/den if den else 0
    b=max(min(b,0.999),-0.999)
    hl = -math.log(2)/math.log(abs(b)) if 0<abs(b)<1 else 50.0
    sd=statistics.pstdev(x) or 1e-9
    z=(x[-1]-xm)/sd
    return z, min(hl,50.0)

def book(weighting):
    series=[]
    for t in range(W+1, RL):
        legs=[]  # (signal, weight, resid_next)
        for c in coins:
            seg=S[c][t-W:t]  # spread values up to index t-1 (decision)
            z,hl=ar1_halflife(seg)
            if abs(z)<ZT: continue
            sig = -1 if z>0 else 1   # revert
            w = 1.0 if weighting=="equal" else 1.0/max(hl,1.0)
            legs.append((sig,w,resid[c][t]))  # realize next-day residual
        if not legs: continue
        wsum=sum(w for _,w,_ in legs)
        r=sum(sig*w*rn for sig,w,rn in legs)/wsum
        series.append(r)
    return series

def sharpe(xs): return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365) if len(xs)>1 else 0
print(f"{'weighting':10s} {'n':>4s} {'annSharpe':>9s} {'meanRet%':>8s} {'h1Sh':>6s} {'h2Sh':>6s}")
for wt in ["equal","halflife"]:
    s=book(wt); mid=len(s)//2
    print(f"{wt:10s} {len(s):4d} {sharpe(s):9.3f} {statistics.mean(s)*100:8.4f} {sharpe(s[:mid]):6.2f} {sharpe(s[mid:]):6.2f}")
e=book("equal"); h=book("halflife")
print(f"\n(a) residual-reversion edge (equal): annSharpe {sharpe(e):.3f}, both halves {sharpe(e[:len(e)//2]):.2f}/{sharpe(e[len(e)//2:]):.2f}")
print(f"(b) half-life sizing lift: {sharpe(h)-sharpe(e):+.3f} Sharpe")
