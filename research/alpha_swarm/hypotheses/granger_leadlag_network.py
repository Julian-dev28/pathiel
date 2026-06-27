"""A14 granger_leadlag_network — lead-lag graph across all 40 coins; trade
consistent followers of consistent leaders. Cost-brutal; report decay.

Rule (lookahead-safe): rolling window W ending at i. Lead-lag weight
  LL[L][F] = corr( r_L[t], r_F[t+1] )  over the window (bars <= i).
At day i, follower score_F = sum_L LL[L][F] * r_L(i)  (today's leader returns).
Rank coins by score, long top-m / short bottom-m. Fill open[i+1], hold H, exit open[i+1+H].
Daily overlap rebal. m=6. vs random 50/50 baseline. W{40,60} x H{1,2,3} shows decay.
"""
import statistics
import numpy as np
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N
ci = {c: k for k, c in enumerate(coins)}

# precompute daily returns matrix: rets[k][t] for coin k at bar t (None-safe -> nan)
R = np.full((len(coins), N), np.nan)
for k, c in enumerate(coins):
    for t in range(1, N):
        v = px.ret(c, t, 1)
        if v is not None:
            R[k, t] = v

def leadlag(i, W):
    # returns LL matrix [L,F] = corr(r_L[t], r_F[t+1]) over window [i-W+1 .. i]
    a = i - W + 1
    if a < 1: return None
    Lblock = R[:, a:i]       # r_L[t], t in [a..i-1]
    Fblock = R[:, a + 1:i + 1]  # r_F[t+1]
    # standardize rows
    def z(M):
        mu = np.nanmean(M, axis=1, keepdims=True)
        sd = np.nanstd(M, axis=1, keepdims=True) + 1e-12
        return (M - mu) / sd
    Lz = np.nan_to_num(z(Lblock)); Fz = np.nan_to_num(z(Fblock))
    n = Lblock.shape[1]
    LL = (Lz @ Fz.T) / n   # [L, F]
    return LL

def run(W, hold, m=6):
    trades = []
    for i in range(W + 2, N - hold - 2):
        LL = leadlag(i, W)
        if LL is None: continue
        rtoday = R[:, i]
        if np.all(np.isnan(rtoday)): continue
        rt = np.nan_to_num(rtoday)
        score = LL.T @ rt   # follower scores: sum_L LL[L,F]*r_L
        ranked = []
        for k, c in enumerate(coins):
            if np.isnan(rtoday[k]): continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            ranked.append((score[k], c))
        if len(ranked) < 3 * m: continue
        ranked.sort()
        longs = ranked[-m:]; shorts = ranked[:m]
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for sc, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
    return trades

print("=" * 100)
print("A14 GRANGER LEAD-LAG NETWORK  (long predicted-up followers / short predicted-down)")
print("=" * 100)
for W in (40, 60):
    for hold in (1, 2, 3):
        tr = run(W, hold)
        s = LC.summarize(tr)
        if s.get("n", 0) == 0: continue
        base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
        ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
        print(f"W={W} H={hold}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
