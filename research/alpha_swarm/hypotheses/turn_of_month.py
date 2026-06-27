"""B14 turn_of_month — turn-of-month / first-N-days effect (institutional flows). Calendar.
Multiple-comparison gate: compare the ToM window vs a null of random day-of-month windows."""
import statistics, random, datetime
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ts=[b[A.T] for b in A.candles(d,coins[0],"1d")]
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
mkt=[statistics.mean(ret[c][t] for c in coins) for t in range(RL)]
random.seed(1)
# day-of-month for the bar that REALIZES ret[t] (= bar t+1, ts index t+1)
dom=[datetime.datetime.utcfromtimestamp(ts[t+1]/1000).day for t in range(RL)]

TOM=set([28,29,30,31,1,2,3])
def window_ev(days):
    sel=[mkt[t] for t in range(RL) if dom[t] in days]
    return statistics.mean(sel), len(sel)

tom_ev,tom_n=window_ev(TOM)
rest=[mkt[t] for t in range(RL) if dom[t] not in TOM]
print(f"ToM window (days {sorted(TOM)}): mean {tom_ev*100:.4f}%  n={tom_n}")
print(f"rest-of-month: mean {statistics.mean(rest)*100:.4f}%  n={len(rest)}")

# OOS both halves for ToM (treat each ToM day as a long-market trade)
tom_trades=[{"t":ts[t+1],"ret":mkt[t]} for t in range(RL) if dom[t] in TOM]
s=A.summarize(tom_trades); sl,oos=s["slip12"],s["oos_12bps"]
print(f"ToM long-market: EV12={sl['mean_ret_pct']:.4f} win={sl['win_rate']:.3f} "
      f"h1={oos['first_half_mean_pct']} h2={oos['second_half_mean_pct']} {s['verdict']}")

# multiple-comparison null: random contiguous 7-day-of-month windows
nwin=2000
better=0
for _ in range(nwin):
    start=random.randint(1,31)
    days=set(((start-1+k)%31)+1 for k in range(7))
    ev,_=window_ev(days)
    if ev>=tom_ev: better+=1
print(f"\nnull: {better}/{nwin} random 7-DoM windows >= ToM mean -> empirical p={better/nwin:.3f}")
