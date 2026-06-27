"""B9 vol_of_vol_regime — does a vol-of-vol spike precede a regime change / trend break?
(1) predictive test: vov spike -> forward BTC abs-move/vol. (2) de-risk overlay on XS book."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
VW, VOVW = 5, 10
L, NSIDE = 14, 8

# market vol series (avg coin 5d realized vol), index t in ret-space
mvol=[None]*RL
for t in range(VW, RL):
    mvol[t]=statistics.mean(statistics.pstdev(ret[c][t-VW:t]) for c in coins)
# vol-of-vol = stdev of last VOVW mvol values (known at t)
vov=[None]*RL
for t in range(VW+VOVW, RL):
    seg=[mvol[k] for k in range(t-VOVW,t)]
    vov[t]=statistics.pstdev(seg)

# (1) predictive: classify vov tercile at t, look at forward 5d BTC abs return + realized vol
btc=cl["BTC"]
valid=[t for t in range(VW+VOVW, RL-5) if vov[t] is not None]
vv=sorted(vov[t] for t in valid); q1=vv[len(vv)//3]; q2=vv[2*len(vv)//3]
def bk(t): return "low" if vov[t]<q1 else ("mid" if vov[t]<q2 else "high")
print("=== (1) vov tercile -> forward 5d BTC behavior ===")
print(f"{'tercile':8s} {'n':>4s} {'fwd5d_absRet%':>13s} {'fwd5d_RV%':>10s} {'fwd5d_minRet%':>13s}")
for b in ["low","mid","high"]:
    ts=[t for t in valid if bk(t)==b]
    absr=statistics.mean(abs(btc[t+5]/btc[t]-1) for t in ts)
    fv=statistics.mean(statistics.pstdev(ret["BTC"][t:t+5]) for t in ts)
    minr=statistics.mean(min(btc[t+k]/btc[t]-1 for k in range(1,6)) for t in ts)
    print(f"{b:8s} {len(ts):4d} {absr*100:13.2f} {fv*100:10.2f} {minr*100:13.2f}")

# (2) de-risk overlay on XS book
def book_ret(t):
    if t-1-L<0: return None
    sc=sorted((cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0,c) for c in coins)
    sh=[c for _,c in sc[:NSIDE]]; lo=[c for _,c in sc[-NSIDE:]]
    return statistics.mean(ret[c][t] for c in lo)-statistics.mean(ret[c][t] for c in sh)
rows=[(book_ret(t),vov[t]) for t in range(VW+VOVW+1,RL) if book_ret(t) is not None and vov[t] is not None]
book=[r[0] for r in rows]; bvov=[r[1] for r in rows]
def sharpe(xs): return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365) if len(xs)>1 else 0
def maxdd(xs):
    eq=peak=1.0;dd=0
    for x in xs: eq*=(1+x);peak=max(peak,eq);dd=min(dd,eq/peak-1)
    return dd
sv=sorted(bvov); t2=sv[2*len(sv)//3]
print("\n=== (2) de-risk overlay on XS book (0.5x when vov in top tercile) ===")
flat=book; derisk=[book[k]*(0.5 if bvov[k]>=t2 else 1.0) for k in range(len(book))]
mid=len(book)//2
for name,s in [("flat",flat),("derisk_vov",derisk)]:
    print(f"{name:11s} annSharpe={sharpe(s):.3f} maxDD={maxdd(s)*100:.1f}% meanRet%={statistics.mean(s)*100:.4f} h1={sharpe(s[:mid]):.2f} h2={sharpe(s[mid:]):.2f}")
print(f"Sharpe lift {sharpe(derisk)-sharpe(flat):+.3f}  maxDD change {(maxdd(derisk)-maxdd(flat))*100:+.1f}pp")
