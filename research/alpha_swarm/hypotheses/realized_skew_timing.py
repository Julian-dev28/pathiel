"""B13 realized_skew_timing — market-level realized skew as a crash predictor: extreme-negative
aggregate skew -> de-risk longs / arm the fade. (1) skew tercile -> forward market behavior.
(2) does negative-skew regime improve the crash-fade-long edge?"""
import statistics
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
mkt=[statistics.mean(ret[c][t] for c in coins) for t in range(RL)]
W=20

def skew(xs):
    n=len(xs); m=statistics.mean(xs); s=statistics.pstdev(xs)
    if s<=0 or n<3: return 0
    return (sum((x-m)**3 for x in xs)/n)/s**3

sk=[None]*RL
for t in range(W,RL): sk[t]=skew(mkt[t-W:t])  # skew up to t-1 (known at t)

# equity proxy for forward market move
eq=[1.0]
for t in range(RL): eq.append(eq[-1]*(1+mkt[t]))

valid=[t for t in range(W,RL-5)]
sv=sorted(sk[t] for t in valid); q1=sv[len(sv)//3]; q2=sv[2*len(sv)//3]
def bk(t): return "neg" if sk[t]<q1 else ("mid" if sk[t]<q2 else "pos")
print("=== (1) market-skew tercile -> forward 5d market behavior ===")
print(f"{'skew':5s} {'n':>4s} {'fwd5d_ret%':>10s} {'fwd5d_min%':>10s} {'crash5%_freq':>12s}")
for b in ["neg","mid","pos"]:
    ts=[t for t in valid if bk(t)==b]
    fr=statistics.mean(eq[t+5]/eq[t+1]-1 for t in ts)  # ret from t+1 to t+5 (post-decision)
    mn=statistics.mean(min(eq[t+k]/eq[t+1]-1 for k in range(1,6)) for t in ts)
    crash=sum(1 for t in ts if min(eq[t+k]/eq[t+1]-1 for k in range(1,6))< -0.05)/len(ts)
    print(f"{b:5s} {len(ts):4d} {fr*100:10.3f} {mn*100:10.3f} {crash*100:11.1f}%")

# (2) does negative-skew regime arm the crash-fade (long after a -12% 1d coin crash)?
print("\n=== (2) crash-fade-long (-12% 1d) EV by market-skew regime at entry ===")
HOR,STOP=3,0.20
def fade_trades(regime):
    tr=[]
    for c in coins:
        cd=A.candles(d,c,"1d")
        clc=[b[A.C] for b in cd]
        for i in range(W+1,len(cd)-HOR-1):
            if sk[i] is None: continue
            if clc[i]/clc[i-1]-1 > -0.12: continue   # 1d crash
            reg = bk(i)
            if regime!="all" and reg!=regime: continue
            px=cd[i+1][A.O]
            tr.append({"t":cd[i+1][A.T],"ret":A.sweep_stop(px,"long",cd[i+1:],[STOP],HOR)[STOP]})
    return tr
for reg in ["all","neg","mid","pos"]:
    s=A.summarize(fade_trades(reg))
    if s["n"]==0: print(f"{reg}: no trades"); continue
    sl,oos=s["slip12"],s["oos_12bps"]
    print(f"{reg:4s} n={s['n']:3d} EV12={sl['mean_ret_pct']:6.3f} win={sl['win_rate']:.3f} h1={oos['first_half_mean_pct']} h2={oos['second_half_mean_pct']} {s['verdict']}")
