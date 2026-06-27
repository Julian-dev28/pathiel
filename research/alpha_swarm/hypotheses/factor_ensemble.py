"""A16 factor_ensemble — combine the SURVIVING Lane-A factors into one vol-weighted
market-neutral book; test diversification lift over the best single factor.

Survivors fed in (skew/low-beta refuted, carry data-blocked):
  MOM   = trailing-30d return (the live XS-momentum factor)
  ACCEL = ret(i,7)-ret(i-7,7)            (A7 momentum-of-momentum)
  RSDD  = close/max(50d high)-1          (A13 relative-strength drawdown, the ROBUST one)
Each: cross-sectional rank -> long top-m / short bottom-m. Shared non-overlapping grid
(rebal=H), per-rebalance portfolio return. Composite = equal-weight z-rank sum.
Report each factor's EV/Sharpe, pairwise corr (overlap), and composite vs best single.
Fill open[i+1], hold H, m=6.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def mom(c, i):  return px.ret(c, i, 30)
def accel(c, i):
    a = px.ret(c, i, 7); b = px.ret(c, i - 7, 7)
    return None if (a is None or b is None) else a - b
def rsdd(c, i):
    cl = px.close(c, i); highs = [px.high(c, j) for j in range(i - 49, i + 1)]
    if cl is None or any(v is None for v in highs): return None
    mx = max(highs); return cl / mx - 1.0 if mx else None

FACTORS = {"MOM": mom, "ACCEL": accel, "RSDD": rsdd}

def zrank(vals):
    mu = statistics.mean(vals); sd = statistics.pstdev(vals) + 1e-12
    return [(v - mu) / sd for v in vals]

def build(H, m=6):
    """Return dict factor->list[(t,ret)] portfolio series + composite series, shared grid."""
    series = {f: [] for f in FACTORS}
    series["COMPOSITE"] = []
    i = 51
    while i < N - H - 2:
        # gather coins valid for all factors + tradeable
        rows = []
        for c in coins:
            vals = {f: FACTORS[f](c, i) for f in FACTORS}
            if any(v is None for v in vals.values()): continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + H)): continue
            fwd = px.open(c, i + 1 + H) / px.open(c, i + 1) - 1.0
            rows.append((c, vals, fwd))
        if len(rows) < 3 * m:
            i += H; continue
        # per-factor z-rank
        comp = {c: 0.0 for c, _, _ in rows}
        for f in FACTORS:
            vlist = [r[1][f] for r in rows]
            zs = zrank(vlist)
            order = sorted(range(len(rows)), key=lambda k: vlist[k])
            longs = set(order[-m:]); shorts = set(order[:m])
            legs = []
            for k, idx in enumerate(range(len(rows))):
                if idx in longs: legs.append(rows[idx][2])
                elif idx in shorts: legs.append(-rows[idx][2])
            if legs:
                series[f].append({"t": px.timeline[i + 1], "ret": statistics.mean(legs)})
            for k in range(len(rows)):
                comp[rows[k][0]] += zs[k]
        # composite book
        corder = sorted(rows, key=lambda r: comp[r[0]])
        clongs = corder[-m:]; cshorts = corder[:m]
        legs = [r[2] for r in clongs] + [-r[2] for r in cshorts]
        if legs:
            series["COMPOSITE"].append({"t": px.timeline[i + 1], "ret": statistics.mean(legs)})
        i += H
    return series

def sharpe(rets): return statistics.mean(rets) / (statistics.pstdev(rets) + 1e-12) if len(rets) >= 3 else None
def corr(a, b):
    if len(a) != len(b) or len(a) < 3: return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = (sum((x - ma) ** 2 for x in a)) ** 0.5; db = (sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / (da * db + 1e-12)

print("=" * 100)
print("A16 FACTOR ENSEMBLE  (surviving Lane-A factors, market-neutral)")
print("=" * 100)
for H in (7, 14):
    S = build(H)
    print(f"\n### H={H}")
    rets = {f: [x["ret"] for x in S[f]] for f in S}
    for f in ["MOM", "ACCEL", "RSDD", "COMPOSITE"]:
        s = LC.summarize(S[f])
        sr = sharpe(rets[f])
        print(f"  {f:10s}: {LC.fmt(s)}  sharpe={sr:+.3f}")
    print("  pairwise corr:")
    keys = ["MOM", "ACCEL", "RSDD"]
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            print(f"    {keys[a]}-{keys[b]}: {corr(rets[keys[a]], rets[keys[b]]):+.2f}")
    best = max(keys, key=lambda f: sharpe(rets[f]) or -9)
    lift = (sharpe(rets['COMPOSITE']) or 0) - (sharpe(rets[best]) or 0)
    print(f"  best single = {best} (sharpe {sharpe(rets[best]):+.3f}); COMPOSITE sharpe {sharpe(rets['COMPOSITE']):+.3f}; LIFT={lift:+.3f}")
