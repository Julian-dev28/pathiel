"""A4 low_beta_anomaly (BAB) — estimate beta-to-BTC, long low-beta / short high-beta.

Rule (lookahead-safe): day i, beta_c = cov(r_c, r_btc)/var(r_btc) over trailing W
daily returns (bars <= i). Rank. Long bottom-m (low beta), short top-m (high beta).
Fill open[i+1], hold H, non-overlapping. m=6.
Two variants: RAW equal-weight legs; BETA-NEUTRAL = scale long legs by 1/beta_long_avg
and short legs by 1/beta_short_avg so the book is ~beta-neutral (the actual BAB).
Scored vs matched random 50/50 baseline.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def beta(c, i, W):
    rc = []; rb = []
    for j in range(i - W + 1, i + 1):
        a = px.ret(c, j, 1); b = px.ret("BTC", j, 1)
        if a is None or b is None: continue
        rc.append(a); rb.append(b)
    if len(rc) < max(5, W // 2): return None
    vb = statistics.pvariance(rb)
    if vb == 0: return None
    mb = statistics.mean(rb); mc = statistics.mean(rc)
    cov = sum((x - mc) * (y - mb) for x, y in zip(rc, rb)) / len(rc)
    return cov / vb

def run(W, hold, m=6):
    trades = []
    port = []  # beta-neutral portfolio per rebalance
    i = W + 1
    while i < N - hold - 2:
        bs = []
        for c in coins:
            bv = beta(c, i, W)
            if bv is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            bs.append((bv, c))
        if len(bs) < 3 * m:
            i += hold; continue
        bs.sort()
        longs = bs[:m]; shorts = bs[-m:]
        bl = statistics.mean([abs(b) for b, _ in longs]) or 1.0
        bsh = statistics.mean([abs(b) for b, _ in shorts]) or 1.0
        legs = []
        for side, grp, bavg in (("long", longs, bl), ("short", shorts, bsh)):
            sign = 1.0 if side == "long" else -1.0
            for bv, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                g = sign * (xo / eo - 1.0)
                trades.append({"t": px.timeline[i + 1], "ret": g})
                # beta-neutral weight: long up by 1/bl, short down by 1/bsh
                w = (1.0 / bavg)
                legs.append((g, w))
        if legs:
            tw = sum(w for _, w in legs)
            if tw > 0:
                port.append({"t": px.timeline[i + 1], "ret": sum(g * w for g, w in legs) / tw})
        i += hold
    return trades, port

print("=" * 100)
print("A4 LOW-BETA ANOMALY / BAB  (long low-beta / short high-beta vs BTC)")
print("=" * 100)
for W in (20, 40, 60):
    for hold in (5, 7, 14):
        tr, port = run(W, hold)
        s = LC.summarize(tr); sp = LC.summarize(port)
        if s.get("n", 0) == 0: continue
        base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
        ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
        print(f"W={W:2d} H={hold:2d}: RAW {LC.fmt(s)}")
        print(f"          BNEUT {LC.fmt(sp)}  base12={base['slip12']['mean_ret_pct']:+.3f} EXCESS(raw)={ex:+.4f}")
