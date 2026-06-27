"""B2 correlation_regime_gate — size the live XS-momentum book by inverse correlation
regime (dispersion dies when everything moves together). Meta-overlay: deciding number
is Sharpe lift over the un-gated book."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins = [c for c in d["coins"] if len(A.candles(d, c, "1d")) == 301]
N = 301
cl = {c: [b[A.C] for b in A.candles(d, c, "1d")] for c in coins}
# daily simple returns, index r[t] = close[t]/close[t-1]-1  (t=1..300)
ret = {c: [cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0.0 for t in range(1, N)] for c in coins}
RET_LEN = N - 1  # 300

L = 14        # momentum lookback (in daily ret index)
NSIDE = 8     # top/bottom k
CORRW = 20    # correlation window

def book_return_at(t):
    """Market-neutral XS-momentum book return realized on ret-index t (decided at t-1)."""
    # rank by trailing L-day return up to t-1 (known before t)
    if t - 1 - L < 0:
        return None
    scores = []
    for c in coins:
        r = cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0.0  # cl index aligns: ret idx t -> cl idx t
        scores.append((r, c))
    scores.sort()
    shorts = [c for _, c in scores[:NSIDE]]
    longs = [c for _, c in scores[-NSIDE:]]
    rl = statistics.mean(ret[c][t] for c in longs)
    rs = statistics.mean(ret[c][t] for c in shorts)
    return rl - rs  # equal gross long/short

def avg_pair_corr(t):
    """Avg pairwise corr over trailing CORRW returns ending at t-1 (known at decision)."""
    if t - CORRW < 0:
        return None
    mats = {c: ret[c][t-CORRW:t] for c in coins}  # uses up to t-1
    means = {c: statistics.mean(mats[c]) for c in coins}
    sds = {c: statistics.pstdev(mats[c]) for c in coins}
    cs = []
    cl_list = coins
    for a_ in range(len(cl_list)):
        for b_ in range(a_+1, len(cl_list)):
            ca, cb = cl_list[a_], cl_list[b_]
            sa, sb = sds[ca], sds[cb]
            if sa <= 0 or sb <= 0:
                continue
            cov = statistics.mean((mats[ca][k]-means[ca])*(mats[cb][k]-means[cb]) for k in range(CORRW))
            cs.append(cov/(sa*sb))
    return statistics.mean(cs) if cs else None

# build aligned series of (book_ret, corr) over valid t
rows = []
for t in range(max(L+1, CORRW), RET_LEN):
    br = book_return_at(t)
    cr = avg_pair_corr(t)
    if br is None or cr is None:
        continue
    rows.append((t, br, cr))

book = [r[1] for r in rows]
corr = [r[2] for r in rows]

def sharpe(xs):
    if len(xs) < 2: return 0.0
    return statistics.mean(xs)/(statistics.pstdev(xs)+1e-12)*math.sqrt(365)

def maxdd(xs):
    eq, peak, dd = 1.0, 1.0, 0.0
    for x in xs:
        eq *= (1+x); peak = max(peak, eq); dd = min(dd, eq/peak-1)
    return dd

# gate variants (multiplier known at decision time = uses corr[t] from trailing data)
med = statistics.median(corr)
q75 = sorted(corr)[int(0.75*len(corr))]
inv = [1.0/max(c, 0.05) for c in corr]
inv_norm = [x/statistics.mean(inv) for x in inv]  # mean-1 scaling (target const gross)

variants = {
    "ungated":      [1.0]*len(book),
    "inv_corr":     inv_norm,
    "gate_off_q75": [0.0 if c >= q75 else 1.0 for c in corr],
    "half_above_med":[0.5 if c >= med else 1.0 for c in corr],
}

print(f"n_days={len(book)}  raw book ann.Sharpe={sharpe(book):.3f}  corr range {min(corr):.2f}..{max(corr):.2f} med {med:.2f}")
print(f"{'variant':16s} {'annSharpe':>9s} {'maxDD':>7s} {'meanRet%':>8s}  Sharpe-lift")
base_sh = sharpe(book)
for name, mult in variants.items():
    g = [book[k]*mult[k] for k in range(len(book))]
    sh = sharpe(g)
    print(f"{name:16s} {sh:9.3f} {maxdd(g)*100:6.1f}% {statistics.mean(g)*100:8.4f}  {sh-base_sh:+.3f}")

# OOS: does the gate help in BOTH halves?
mid = len(book)//2
for name, mult in variants.items():
    g = [book[k]*mult[k] for k in range(len(book))]
    print(f"  {name:16s} h1Sharpe={sharpe(g[:mid]):.3f}  h2Sharpe={sharpe(g[mid:]):.3f}")
