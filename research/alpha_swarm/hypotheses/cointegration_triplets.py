"""A9 cointegration_triplets — 3-coin baskets, trade the residual's mean reversion.
Survivorship-hardened: trade ALL sampled triplets uniformly (no in-sample cherry-pick
of "the cointegrated ones"), rolling hedge ratios, OOS both-halves. The pairs edge was a
false positive from selecting survivors — we refuse to select.

Rule (lookahead-safe): triplet (a,b,c). day i: regress price_a on [1, price_b, price_c]
over trailing W days (bars <= i) -> betas, residual series, z = (resid_i - mean)/std.
Entry when |z| >= entry_z. Trade spread = a - bb*b - bc*c, position = -sign(z).
Fill at OPEN[i+1] spread, exit OPEN[i+1+H] spread. Return = pos*(spread_exit-spread_entry)
/ gross_notional where gross = |a|+|bb*b|+|bc*c| at entry. Pool across triplets.
NOTE: 3 legs => real cost ~3x the per-trade bps shown; read the 25/50bps tiers as the
realistic gate. Sample M random triplets (seeded), no selection.
"""
import statistics, random
import numpy as np
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def price(c, i):  # use close for level regression, open for fills
    return px.close(c, i)

def run(M, W, hold, entry_z, seed=1):
    rng = random.Random(seed)
    trips = set()
    while len(trips) < M:
        t = tuple(sorted(rng.sample(coins, 3)))
        trips.add(t)
    trades = []
    for (a, b, c) in trips:
        i = W + 1
        while i < N - hold - 2:
            # build level series over trailing window
            Pa = [price(a, j) for j in range(i - W + 1, i + 1)]
            Pb = [price(b, j) for j in range(i - W + 1, i + 1)]
            Pc = [price(c, j) for j in range(i - W + 1, i + 1)]
            if any(v is None for v in Pa + Pb + Pc):
                i += 1; continue
            Pa = np.array(Pa); Pb = np.array(Pb); Pc = np.array(Pc)
            X = np.column_stack([np.ones(W), Pb, Pc])
            beta, *_ = np.linalg.lstsq(X, Pa, rcond=None)
            resid = Pa - X @ beta
            sd = resid.std()
            if sd == 0:
                i += 1; continue
            z = (resid[-1] - resid.mean()) / sd
            if abs(z) < entry_z:
                i += 1; continue
            bb, bc = beta[1], beta[2]
            # fills at open i+1 and open i+1+hold
            oa1, ob1, oc1 = px.open(a, i + 1), px.open(b, i + 1), px.open(c, i + 1)
            oa2, ob2, oc2 = px.open(a, i + 1 + hold), px.open(b, i + 1 + hold), px.open(c, i + 1 + hold)
            if None in (oa1, ob1, oc1, oa2, ob2, oc2):
                i += 1; continue
            spread1 = oa1 - bb * ob1 - bc * oc1
            spread2 = oa2 - bb * ob2 - bc * oc2
            gross = abs(oa1) + abs(bb * ob1) + abs(bc * oc1)
            if gross == 0:
                i += 1; continue
            pos = -1.0 if z > 0 else 1.0  # rich spread -> short it
            ret = pos * (spread2 - spread1) / gross
            trades.append({"t": px.timeline[i + 1], "ret": ret})
            i += hold  # non-overlap per triplet
    return trades

print("=" * 100)
print("A9 COINTEGRATION TRIPLETS  (uniform over sampled triplets, rolling, no selection)")
print("NOTE: 3 legs -> real cost ~3x; weight the 25/50bps tiers")
print("=" * 100)
for W in (30, 40):
    for hold in (3, 5):
        for ez in (1.5, 2.0):
            tr = run(400, W, hold, ez)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            print(f"W={W} H={hold} z>={ez}: {LC.fmt(s)}")
