"""B8 momentum_12_1_reversal — classic 12-1 skip momentum: score = return over [t-L, t-skip],
skipping the most recent `skip` window to dodge short-term reversal. XS + TS variants.
Compare to no-skip (skip=0). Random baseline for the TS variant."""
import math, statistics, random
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
random.seed(5)

def sharpe(xs): return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365) if len(xs)>1 else 0

# ---- CROSS-SECTIONAL book (daily rebal, hold 1d) ----
def xs_book(L, skip, NSIDE=8):
    series=[]
    for t in range(L+1, RL):
        # score uses cl indices up to t-1-skip (known at decision t)
        end=t-1-skip; start=end-L
        if start<0: continue
        sc=sorted((cl[c][end]/cl[c][start]-1 if cl[c][start] else 0, c) for c in coins)
        sh=[c for _,c in sc[:NSIDE]]; lo=[c for _,c in sc[-NSIDE:]]
        series.append(statistics.mean(ret[c][t] for c in lo)-statistics.mean(ret[c][t] for c in sh))
    return series

print("=== XS variant (daily rebal book) ===")
print(f"{'L':>3s} {'skip':>4s} {'annSharpe':>9s} {'meanRet%':>8s} {'h1Sh':>6s} {'h2Sh':>6s}")
for L in [30,60,90]:
    for skip in [0,7,14]:
        s=xs_book(L,skip); mid=len(s)//2
        print(f"{L:3d} {skip:4d} {sharpe(s):9.3f} {statistics.mean(s)*100:8.4f} {sharpe(s[:mid]):6.2f} {sharpe(s[mid:]):6.2f}")

# ---- TIME-SERIES variant (per-coin directional) ----
HOR,STOP=5,0.25
def ts(L, skip, randomize=False):
    trades=[]
    for c in coins:
        cd=A.candles(d,c,"1d")
        clc=[b[A.C] for b in cd]
        i=L+1
        while i<len(cd)-HOR-1:
            end=i-skip; start=end-L
            if start<0: i+=1; continue
            r=clc[end]/clc[start]-1
            side="long" if (random.random()<0.5 if randomize else r>0) else "short"
            px=cd[i+1][A.O]
            rr=A.sweep_stop(px,side,cd[i+1:],[STOP],HOR)[STOP]
            trades.append({"t":cd[i+1][A.T],"ret":rr}); i+=HOR
    return trades

print("\n=== TS variant (per-coin, 5d hold) ===")
print(f"{'L':>3s} {'skip':>4s} {'n':>5s} {'EV12%':>7s} {'sharpe':>6s} {'h1':>7s} {'h2':>7s} verdict")
for L in [30,60,90]:
    for skip in [0,7,14]:
        s=A.summarize(ts(L,skip)); sl,oos=s["slip12"],s["oos_12bps"]
        print(f"{L:3d} {skip:4d} {s['n']:5d} {sl['mean_ret_pct']:7.3f} {sl['sharpe_like']:6.3f} "
              f"{str(oos['first_half_mean_pct']):>7s} {str(oos['second_half_mean_pct']):>7s}  {s['verdict']}")
rb=A.summarize(ts(60,7,randomize=True))["slip12"]["mean_ret_pct"]
print(f"random-side baseline (L60 days): EV {rb:.3f}")
