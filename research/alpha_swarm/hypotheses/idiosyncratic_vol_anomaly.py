"""A5 idiosyncratic_vol_anomaly — rank by residual vol after stripping BTC beta;
low-idio-vol long / high-idio-vol short (the IVOL anomaly).

Rule (lookahead-safe): day i, over trailing W daily rets, beta_c to BTC, residual
e = r_c - beta*r_btc, idio_vol = std(e). Long bottom-m idio_vol, short top-m.
Fill open[i+1], hold H, non-overlapping. m=6. vs random 50/50 baseline.
Also report total-vol variant (no beta strip) to see if idio adds over raw vol.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def idio_vol(c, i, W, strip=True):
    rc = []; rb = []
    for j in range(i - W + 1, i + 1):
        a = px.ret(c, j, 1); b = px.ret("BTC", j, 1)
        if a is None or b is None: continue
        rc.append(a); rb.append(b)
    if len(rc) < max(5, W // 2): return None
    if not strip:
        return statistics.pstdev(rc)
    vb = statistics.pvariance(rb)
    if vb == 0: return statistics.pstdev(rc)
    mb = statistics.mean(rb); mc = statistics.mean(rc)
    cov = sum((x - mc) * (y - mb) for x, y in zip(rc, rb)) / len(rc)
    beta = cov / vb
    resid = [x - beta * y for x, y in zip(rc, rb)]
    return statistics.pstdev(resid)

def run(W, hold, strip, m=6):
    trades = []
    i = W + 1
    while i < N - hold - 2:
        vs = []
        for c in coins:
            v = idio_vol(c, i, W, strip)
            if v is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            vs.append((v, c))
        if len(vs) < 3 * m:
            i += hold; continue
        vs.sort()
        longs = vs[:m]; shorts = vs[-m:]
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for v, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
        i += hold
    return trades

print("=" * 100)
print("A5 IDIOSYNCRATIC-VOL ANOMALY  (long low-idio-vol / short high-idio-vol)")
print("=" * 100)
for strip in (True, False):
    print(f"\n### strip_btc_beta = {strip}  ({'idio' if strip else 'total'} vol)")
    for W in (20, 40, 60):
        for hold in (5, 7, 14):
            tr = run(W, hold, strip)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  W={W:2d} H={hold:2d}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
