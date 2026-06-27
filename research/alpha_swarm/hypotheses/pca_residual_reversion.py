"""A1 pca_residual_reversion — strip top PCs (market+sector) from the daily
return matrix, mean-revert the idiosyncratic residuals (Avellaneda-Lee).

Rule (lookahead-safe):
  At day t, take window of L daily returns ending at t (bars <= t).
  PCA on the standardized return matrix -> top n_pc eigenportfolio factor returns F.
  Regress each coin's returns on F over the window; residual series e_c.
  s-score = (cumsum(e_c)[-1] - mean) / std of the residual cumulative process.
  Long bottom-m s-score (most beaten-down idio), short top-m. Market-neutral.
  FILL open[t+1], hold H, exit open[t+1+H]. Each leg = one signed trade.
Compare to matched random-entry baseline (50/50 side).
"""
import numpy as np, statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def factor_resid_sscore(i, L, n_pc):
    """Return dict coin->s-score using window of L daily rets ending at bar i."""
    # build return matrix days x coins over [i-L+1 .. i]
    rows = []
    valid = list(coins)
    mat = {c: [] for c in coins}
    days = list(range(i - L + 1, i + 1))
    for j in days:
        for c in coins:
            r = px.ret(c, j, 1)
            mat[c].append(r)
    # keep coins with full data
    keep = [c for c in coins if all(v is not None for v in mat[c])]
    if len(keep) < 3 * 6 + n_pc:
        return None
    R = np.array([mat[c] for c in keep], dtype=float).T  # days x coins
    # standardize per coin
    mu = R.mean(axis=0); sd = R.std(axis=0) + 1e-12
    Rs = (R - mu) / sd
    # PCA via SVD on standardized matrix (days x coins)
    U, S, Vt = np.linalg.svd(Rs, full_matrices=False)
    F = U[:, :n_pc] * S[:n_pc]   # factor return time series, days x n_pc
    out = {}
    for ci, c in enumerate(keep):
        y = Rs[:, ci]
        # regress y on F (+ intercept)
        X = np.column_stack([np.ones(len(F)), F])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        X_cum = np.cumsum(resid)
        s = (X_cum[-1] - X_cum.mean()) / (X_cum.std() + 1e-12)
        out[c] = s
    return out

def run(L, n_pc, hold, m=6):
    trades = []
    start = L + 1
    for i in range(start, N - hold - 2):
        sc = factor_resid_sscore(i, L, n_pc)
        if not sc:
            continue
        ranked = sorted(sc.items(), key=lambda kv: kv[1])  # ascending s-score
        ranked = [(c, s) for c, s in ranked
                  if px.open(c, i + 1) and px.open(c, i + 1 + hold)]
        if len(ranked) < 3 * m:
            continue
        longs = ranked[:m]      # most negative s-score -> expect reversion up
        shorts = ranked[-m:]    # most positive -> expect reversion down
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for c, _ in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
    return trades

print("=" * 110)
print("A1 PCA RESIDUAL REVERSION  (long bottom-m / short top-m idio s-score)")
print("=" * 110)
best = None
for L in (20, 40, 60):
    for n_pc in (1, 2, 3):
        for hold in (1, 2, 3):
            tr = LC.run if False else run(L, n_pc, hold)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0:
                continue
            ev12 = s["slip12"]["mean_ret_pct"]
            print(f"L={L:2d} n_pc={n_pc} hold={hold}: {LC.fmt(s)}")
            if best is None or ev12 > best[0]:
                best = (ev12, L, n_pc, hold, s)

print("\n--- matched random-entry baselines (50/50 side) ---")
for hold in (1, 2, 3):
    b = LC.baseline_random(px, 0.5, hold, n_samp=4000)
    print(f"hold={hold}: base EV0={b['slip0']['mean_ret_pct']:+.4f} EV12={b['slip12']['mean_ret_pct']:+.4f}")

if best:
    ev12, L, n_pc, hold, s = best
    print(f"\nBEST: L={L} n_pc={n_pc} hold={hold}  EV12/leg={ev12:+.4f}%  OOS={s['oos_12bps']}")
