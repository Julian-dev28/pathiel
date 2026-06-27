"""B7 trend_ensemble_lookbacks — ensemble of TSMOM lookbacks {7,14,30,90}d voting vs best
single lookback. Claim: ensemble is smoother (better Sharpe/OOS). Random baseline for excess."""
import statistics, random
import alpha_lib as A

d = A.load_dataset()
coins = d["coins"]
random.seed(3)
LBS = [7, 14, 30, 90]
HOR, STOP = 5, 0.25
MINL = max(LBS)

def run(mode):
    trades=[]
    for c in coins:
        cd=A.candles(d,c,"1d")
        if len(cd)<MINL+HOR+2: continue
        cl=[b[A.C] for b in cd]
        i=MINL
        while i<len(cd)-HOR-1:
            votes=[1 if cl[i]/cl[i-L]-1>0 else -1 for L in LBS]
            if mode=="ensemble":
                s=sum(votes)
                if s==0: i+=1; continue
                side="long" if s>0 else "short"
            elif mode.startswith("lb"):
                L=int(mode[2:]); side="long" if cl[i]/cl[i-L]-1>0 else "short"
            else:
                side=random.choice(["long","short"])
            px=cd[i+1][A.O]
            r=A.sweep_stop(px,side,cd[i+1:],[STOP],HOR)[STOP]
            trades.append({"t":cd[i+1][A.T],"ret":r})
            i+=HOR
    return trades

modes=["ensemble"]+[f"lb{L}" for L in LBS]+["random"]
print(f"{'mode':9s} {'n':>5s} {'EV12%':>7s} {'win':>5s} {'sharpe':>6s} {'h1':>7s} {'h2':>7s} verdict")
res={}
for m in modes:
    s=A.summarize(run(m)); res[m]=s; sl,oos=s["slip12"],s["oos_12bps"]
    print(f"{m:9s} {s['n']:5d} {sl['mean_ret_pct']:7.3f} {sl['win_rate']:5.3f} {sl['sharpe_like']:6.3f} "
          f"{str(oos['first_half_mean_pct']):>7s} {str(oos['second_half_mean_pct']):>7s}  {s['verdict']}")
def ev(m): return res[m]["slip12"]["mean_ret_pct"]
def sh(m): return res[m]["slip12"]["sharpe_like"]
best=max([f"lb{L}" for L in LBS], key=sh)
print(f"\nensemble Sharpe {sh('ensemble'):.3f} vs best single {best} {sh(best):.3f} -> lift {sh('ensemble')-sh(best):+.3f}")
print(f"ensemble EV {ev('ensemble'):.3f} vs {best} {ev(best):.3f} ; vs random {ev('ensemble')-ev('random'):+.3f}")
