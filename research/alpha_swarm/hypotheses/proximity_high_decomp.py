"""W-A2 proximity_high_decomp — which HALF of A13 carries the edge?

A13 = long top-6 NEAREST-50d-high / short bottom-6 DEEPEST-drawdown, market-neutral, H=7.
Decompose: long-leg-only vs short-leg-only vs combined L/S. Score EACH leg as EXCESS over a
matched random-entry baseline (same side, same horizon) via mc_null. Tells us which half
(long proximity-to-high, short deep-drawdown, or only the spread) to wire.

Lookahead-safe: rank on bars<=i, fill open[i+1], exit open[i+1+H].
"""
import statistics
import alpha_lib as al
from alpha_lib import O, H as HI, L, C
import mc_null

d = al.load_dataset()
SER = {c: al.candles(d, c, "1d") for c in d["coins"] if len(al.candles(d, c, "1d")) >= 60}
N = min(len(b) for b in SER.values())
ARR = {c: SER[c][-N:] for c in SER}

def rsdd(bars, i, n=50):
    seg = bars[i-n+1:i+1]
    if len(seg) < n: return None
    mx = max(b[HI] for b in seg)
    return bars[i][C]/mx - 1.0 if mx > 0 else None

def fwd(c, i, hold):
    bars = ARR[c]; e=i+1; x=i+1+hold
    if x >= len(bars) or bars[e][O] <= 0: return None
    return bars[x][O]/bars[e][O] - 1.0

def run(hold=7, k=6, N50=50):
    near_long, deep_short, near_short, deep_long = [], [], [], []
    pool_long, pool_short = [], []   # random-entry baseline pools (every coin, every eligible day)
    start = N50 + 2
    for i in range(start, N - hold - 2):
        rs = [(c, rsdd(ARR[c], i, N50)) for c in ARR]
        rs = [(c,v) for c,v in rs if v is not None]
        if len(rs) < 2*k: continue
        rs.sort(key=lambda x: x[1], reverse=True)   # nearest-high first (dd~0), deepest last
        nh = [c for c,_ in rs[:k]]; dp = [c for c,_ in rs[-k:]]
        t = ARR[nh[0]][i][0]
        for c in nh:
            r = fwd(c,i,hold)
            if r is not None:
                near_long.append({"t":t,"ret":r}); near_short.append({"t":t,"ret":-r})
        for c in dp:
            r = fwd(c,i,hold)
            if r is not None:
                deep_long.append({"t":t,"ret":r}); deep_short.append({"t":t,"ret":-r})
        for c,_ in rs:   # baseline pool: any coin you could have entered this day
            r = fwd(c,i,hold)
            if r is not None:
                pool_long.append(r); pool_short.append(-r)
    print(f"\n==== hold={hold} k={k} N50={N50}  (n_long_pool={len(pool_long)}) ====")
    for name, trades, pool in [
        ("NEAR-long (proximity-to-high)", near_long, pool_long),
        ("DEEP-short (short deepest-dd)",  deep_short, pool_short),
        ("DEEP-long (sanity, expect -)",   deep_long, pool_long),
        ("NEAR-short (sanity, expect -)",  near_short, pool_short),
    ]:
        s = al.summarize(trades)
        oos = s["oos_12bps"]
        mc = mc_null.shuffle_label_p([x["ret"] for x in trades], pool, n_iter=4000, seed=1)
        print(f" {name:34} n={s['n']:4} EV0={s['slip0']['mean_ret_pct']:+.3f} EV12={s['slip12']['mean_ret_pct']:+.3f} EV25={s['slip25']['mean_ret_pct']:+.3f} EV50={s['slip50']['mean_ret_pct']:+.3f} | OOS h1={oos['first_half_mean_pct']:+.3f}/h2={oos['second_half_mean_pct']:+.3f} | excess={mc['excess']*100:+.3f}% z={mc['z']:+.2f} p={mc['p_one_sided']}")
    # combined L/S per-leg (pool both legs)
    comb = near_long + deep_short
    s = al.summarize(comb); oos = s["oos_12bps"]
    print(f" {'COMBINED L/S (per-leg)':34} n={s['n']:4} EV0={s['slip0']['mean_ret_pct']:+.3f} EV12={s['slip12']['mean_ret_pct']:+.3f} EV25={s['slip25']['mean_ret_pct']:+.3f} EV50={s['slip50']['mean_ret_pct']:+.3f} | OOS h1={oos['first_half_mean_pct']:+.3f}/h2={oos['second_half_mean_pct']:+.3f}")

run(hold=7, k=6)
run(hold=5, k=6)
run(hold=7, k=8)
