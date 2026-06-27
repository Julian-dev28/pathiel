"""B11 drawdown_state_machine — BTC drawdown states {peak,correction,bear,recovery} from the
BTC equity curve; tabulate which candidate edge pays in which state. Router, not signal."""
import statistics
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
btc=cl["BTC"]
L,NSIDE=14,8

# BTC drawdown state at decision t (uses prices up to t, ret-index aligns cl index = t)
peak=[0]*N; pk=btc[0]
for t in range(N):
    pk=max(pk,btc[t]); peak[t]=pk
def state(t):
    dd=btc[t]/peak[t]-1
    up5 = t>=5 and btc[t]/btc[t-5]-1>0.03
    if dd> -0.05: return "peak"
    if dd< -0.20: return "recovery" if up5 else "bear"
    return "recovery" if up5 else "correction"

def book_ret(t):
    if t-1-L<0: return None
    sc=sorted((cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0,c) for c in coins)
    sh=[c for _,c in sc[:NSIDE]]; lo=[c for _,c in sc[-NSIDE:]]
    return statistics.mean(ret[c][t] for c in lo)-statistics.mean(ret[c][t] for c in sh)

# accumulate edges by state
# edges: xs_book (neutral), longonly (equal-weight long all = beta), mom7 (TS lb7 directional, daily 1d hold)
from collections import defaultdict
acc=defaultdict(lambda: defaultdict(list))
for t in range(L+1, RL):
    st=state(t)
    b=book_ret(t)
    if b is not None: acc["xs_book"][st].append((t,b))
    acc["long_all"][st].append((t, statistics.mean(ret[c][t] for c in coins)))
    # TS mom7 directional daily: side from 7d trailing, realize next-day ret
    m=[ (1 if cl[c][t-1]/cl[c][t-1-7]-1>0 else -1)*ret[c][t] for c in coins if t-1-7>=0]
    if m: acc["mom7"][st].append((t, statistics.mean(m)))

states=["peak","correction","bear","recovery"]
print(f"{'edge':9s} {'state':11s} {'n':>4s} {'meanRet%':>8s} {'h1%':>7s} {'h2%':>7s}")
for edge in ["xs_book","long_all","mom7"]:
    for st in states:
        rows=acc[edge][st]
        if not rows: continue
        vals=[v for _,v in rows]; ts=[t for t,_ in rows]; mid=ts[len(ts)//2]
        h1=[v for t,v in rows if t<=mid]; h2=[v for t,v in rows if t>mid]
        m1=statistics.mean(h1)*100 if h1 else 0; m2=statistics.mean(h2)*100 if h2 else 0
        print(f"{edge:9s} {st:11s} {len(rows):4d} {statistics.mean(vals)*100:8.4f} {m1:7.3f} {m2:7.3f}")
    print()
# state frequencies
from collections import Counter
cnt=Counter(state(t) for t in range(L+1,RL))
print("state freq:", dict(cnt))
