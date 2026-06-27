"""A8 dispersion_mean_reversion — when cross-sectional return dispersion hits an
extreme percentile, trade convergence (long laggards / short leaders).

Rule (lookahead-safe): day i, trailing-k return per coin; dispersion D = cross-sectional
stdev of those returns (bars <= i). If D >= pctl-threshold (computed from PAST D values
only, expanding), enter convergence: long bottom-m (laggards), short top-m (leaders).
Fill open[i+1], hold H, exit open[i+1+H]. Non-overlapping. m=6. vs random 50/50 baseline.
Sweep k{3,5,7} x H{3,5,7} x gate-pctl{66,80}.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def disp(i, k):
    rs = [px.ret(c, i, k) for c in coins]
    rs = [r for r in rs if r is not None]
    if len(rs) < 12: return None, None
    return statistics.pstdev(rs), rs

def run(k, hold, pctl, m=6):
    trades = []
    hist_D = []  # past dispersion values (expanding, lookahead-safe)
    i = max(k, 8) + 1
    while i < N - hold - 2:
        D, rs = disp(i, k)
        if D is None:
            i += 1; continue
        thresh = None
        if len(hist_D) >= 30:
            sd = sorted(hist_D)
            thresh = sd[int(len(sd) * pctl / 100)]
        gated = thresh is not None and D >= thresh
        hist_D.append(D)
        if not gated:
            i += 1; continue
        ranked = [(px.ret(c, i, k), c) for c in coins]
        ranked = [(r, c) for r, c in ranked if r is not None
                  and px.open(c, i + 1) and px.open(c, i + 1 + hold)]
        if len(ranked) < 3 * m:
            i += 1; continue
        ranked.sort()
        longs = ranked[:m]   # laggards
        shorts = ranked[-m:] # leaders
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for r, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
        i += hold  # non-overlap after an entry
    return trades

print("=" * 100)
print("A8 DISPERSION MEAN-REVERSION  (gated convergence: long laggards/short leaders)")
print("=" * 100)
for pctl in (66, 80):
    print(f"\n### dispersion gate >= P{pctl}")
    for k in (3, 5, 7):
        for hold in (3, 5, 7):
            tr = run(k, hold, pctl)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  k={k} H={hold}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
