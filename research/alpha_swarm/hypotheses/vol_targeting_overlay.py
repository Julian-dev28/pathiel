"""B4 vol_targeting_overlay — EWMA vol forecast scales total book exposure to constant
risk. Meta-overlay on the XS-momentum book; deciding numbers = Sharpe + maxDD vs flat."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins = [c for c in d["coins"] if len(A.candles(d, c, "1d")) == 301]
N = 301
cl = {c: [b[A.C] for b in A.candles(d, c, "1d")] for c in coins}
ret = {c: [cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0.0 for t in range(1, N)] for c in coins}
RL = N-1
L, NSIDE = 14, 8

def book_ret(t):
    if t-1-L < 0: return None
    sc = sorted((cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0.0, c) for c in coins)
    sh = [c for _, c in sc[:NSIDE]]; lo = [c for _, c in sc[-NSIDE:]]
    return statistics.mean(ret[c][t] for c in lo) - statistics.mean(ret[c][t] for c in sh)

series = []
for t in range(L+1, RL):
    b = book_ret(t)
    if b is not None: series.append(b)

def sharpe(xs): return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365) if len(xs)>1 else 0
def maxdd(xs):
    eq=peak=1.0; dd=0
    for x in xs:
        eq*=(1+x); peak=max(peak,eq); dd=min(dd,eq/peak-1)
    return dd

LAM = 0.94          # EWMA decay (RiskMetrics)
TARGET = statistics.pstdev(series)  # target daily vol = realized (apples-to-apples gross)
CAP = 3.0           # max leverage multiplier

# forecast var at t uses returns strictly before t
var = statistics.pvariance(series[:20]) or TARGET**2
scaled = []
mult_series = []
for k in range(len(series)):
    if k < 20:
        scaled.append(series[k]); mult_series.append(1.0);
        var = LAM*var + (1-LAM)*series[k]**2
        continue
    fvol = math.sqrt(var)
    mult = min(TARGET/(fvol+1e-12), CAP)
    scaled.append(series[k]*mult); mult_series.append(mult)
    var = LAM*var + (1-LAM)*series[k]**2   # update AFTER using (no lookahead)

print(f"n_days={len(series)}  target_dailyvol={TARGET*100:.2f}%  mult range {min(mult_series):.2f}..{max(mult_series):.2f}")
print(f"{'variant':10s} {'annSharpe':>9s} {'maxDD':>7s} {'realDailyVol%':>12s} {'meanRet%':>8s}")
for name, s in [("flat", series), ("voltgt", scaled)]:
    print(f"{name:10s} {sharpe(s):9.3f} {maxdd(s)*100:6.1f}% {statistics.pstdev(s)*100:11.2f} {statistics.mean(s)*100:8.4f}")
mid = len(series)//2
print(f"flat   h1Sh={sharpe(series[:mid]):.2f} h2Sh={sharpe(series[mid:]):.2f}")
print(f"voltgt h1Sh={sharpe(scaled[:mid]):.2f} h2Sh={sharpe(scaled[mid:]):.2f}")
print(f"Sharpe lift {sharpe(scaled)-sharpe(series):+.3f}  maxDD change {(maxdd(scaled)-maxdd(series))*100:+.1f}pp")
