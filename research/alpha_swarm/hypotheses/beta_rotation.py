"""A12 beta_rotation — high-beta basket in BTC-up regime, low-beta in BTC-down;
measure vs static.

Rule (lookahead-safe): day i, beta_c to BTC over trailing W. regime = BTC SMA(Nsma) trend.
ROTATE long-only: up -> long top-m beta ; down -> long bottom-m beta.
LS rotate: up -> long top/short bottom ; down -> long bottom/short top.
Fill open[i+1], hold H, non-overlapping. Compare to STATIC long-high, STATIC long-low.
m=8. Score per-leg EV both-halves + vs random baseline at matched longfrac.
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

def btc_sma_regime(i, nsma=20):
    cls = px.close("BTC", i)
    vals = [px.close("BTC", j) for j in range(i - nsma + 1, i + 1)]
    if cls is None or any(v is None for v in vals): return None
    return "up" if cls > statistics.mean(vals) else "down"

def run(mode, W, hold, nsma=20, m=8):
    trades = []
    nl = nd = 0
    i = W + 1
    while i < N - hold - 2:
        reg = btc_sma_regime(i, nsma)
        if reg is None: i += hold; continue
        bs = []
        for c in coins:
            bv = beta(c, i, W)
            if bv is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            bs.append((bv, c))
        if len(bs) < 3 * m: i += hold; continue
        bs.sort()
        low = bs[:m]; high = bs[-m:]
        longs, shorts = [], []
        if mode == "rot_long":
            longs = high if reg == "up" else low
        elif mode == "static_high":
            longs = high
        elif mode == "static_low":
            longs = low
        elif mode == "rot_ls":
            if reg == "up": longs, shorts = high, low
            else: longs, shorts = low, high
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for bv, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                if sign > 0: nl += 1
                else: nd += 1
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
        i += hold
    lf = nl / (nl + nd) if (nl + nd) else 1.0
    return trades, lf

print("=" * 100)
print("A12 BETA ROTATION  (rot vs static; long-only & L/S)")
print("=" * 100)
for W in (40, 60):
    for hold in (5, 7):
        print(f"\n### W={W} H={hold}")
        for mode in ("rot_long", "static_high", "static_low", "rot_ls"):
            tr, lf = run(mode, W, hold)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, lf, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  {mode:12s} lf={lf:.2f}: {LC.fmt(s)}  base12={base['slip12']['mean_ret_pct']:+.3f} EXCESS={ex:+.4f}")
