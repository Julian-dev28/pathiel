"""B5 realized_vol_mean_reversion — vol mean-reverts; the claim is a SIZING edge: size up
after a vol spike (vol about to fall, trends resolve), down into compression. Test by
conditioning XS-book performance on prior market-vol state + measuring sizing lift."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins = [c for c in d["coins"] if len(A.candles(d, c, "1d")) == 301]
N = 301
cl = {c: [b[A.C] for b in A.candles(d, c, "1d")] for c in coins}
ret = {c: [cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0.0 for t in range(1, N)] for c in coins}
RL = N-1
L, NSIDE, VW = 14, 8, 5

def book_ret(t):
    if t-1-L < 0: return None
    sc = sorted((cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0.0, c) for c in coins)
    sh=[c for _,c in sc[:NSIDE]]; lo=[c for _,c in sc[-NSIDE:]]
    return statistics.mean(ret[c][t] for c in lo)-statistics.mean(ret[c][t] for c in sh)

def mkt_vol(t):  # avg coin 5d realized vol over returns ending at t-1 (known at decision)
    if t-VW < 0: return None
    return statistics.mean(statistics.pstdev(ret[c][t-VW:t]) for c in coins)

rows=[]
for t in range(L+1, RL):
    b=book_ret(t); v=mkt_vol(t)
    if b is not None and v is not None: rows.append((b,v))
book=[r[0] for r in rows]; vol=[r[1] for r in rows]

# confirm vol mean reversion: corr(vol_t, vol_{t+1}) and does high vol -> lower next vol vs its mean
def sharpe(xs): return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365) if len(xs)>1 else 0
def maxdd(xs):
    eq=peak=1.0; dd=0
    for x in xs: eq*=(1+x); peak=max(peak,eq); dd=min(dd,eq/peak-1)
    return dd

# terciles of prior vol
sv=sorted(vol); q1=sv[len(sv)//3]; q2=sv[2*len(sv)//3]
def bucket(v): return "low" if v<q1 else ("mid" if v<q2 else "high")
print("book performance conditioned on PRIOR market-vol tercile:")
print(f"{'tercile':8s} {'n':>4s} {'meanRet%':>8s} {'annSharpe':>9s}")
for b in ["low","mid","high"]:
    xs=[book[k] for k in range(len(book)) if bucket(vol[k])==b]
    print(f"{b:8s} {len(xs):4d} {statistics.mean(xs)*100:8.4f} {sharpe(xs):9.3f}")

# sizing overlays (multiplier known at decision from prior vol)
def apply(mult_fn): return [book[k]*mult_fn(vol[k]) for k in range(len(book))]
variants={
 "flat":        lambda v:1.0,
 "up_after_spike": lambda v:1.5 if bucket(v)=="high" else (1.0 if bucket(v)=="mid" else 0.5),
 "down_after_spike":lambda v:0.5 if bucket(v)=="high" else (1.0 if bucket(v)=="mid" else 1.5),
}
print(f"\n{'variant':17s} {'annSharpe':>9s} {'maxDD':>7s} {'meanRet%':>8s} {'lift':>6s}")
base=sharpe(book)
mid=len(book)//2
for name,fn in variants.items():
    g=apply(fn)
    print(f"{name:17s} {sharpe(g):9.3f} {maxdd(g)*100:6.1f}% {statistics.mean(g)*100:8.4f} {sharpe(g)-base:+.3f}   h1={sharpe(g[:mid]):.2f} h2={sharpe(g[mid:]):.2f}")
