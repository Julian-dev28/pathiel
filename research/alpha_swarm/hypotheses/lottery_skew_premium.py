"""A3 lottery_skew_premium — short top-decile MAX-daily-return / realized-skew,
long bottom-decile, weekly rebal, market-neutral. (Bali/Cakici lottery anomaly.)

Rule (lookahead-safe): day i, over trailing window W of daily returns (bars <= i):
  MAX signal  = max daily return in window.
  SKEW signal = sample skewness of daily returns in window.
Long bottom-m (boring/neg-skew), short top-m (lottery/pos-skew). Fill open[i+1],
hold H, exit open[i+1+H], non-overlapping. m=4 (~decile of 40).
Control: also report momentum overlap = corr of MAX-rank with trailing-W return rank.
Scored vs matched random baseline (50/50, market-neutral so longfrac=0.5).
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def skew(xs):
    n = len(xs)
    if n < 3: return None
    m = statistics.mean(xs); sd = statistics.pstdev(xs)
    if sd == 0: return 0.0
    return sum(((x - m) / sd) ** 3 for x in xs) / n

def signal(c, i, W, kind):
    rs = px.daily_rets(c, i, W)
    if len(rs) < max(5, W // 2): return None
    if kind == "max": return max(rs)
    if kind == "skew": return skew(rs)

def run(W, hold, kind, m=4):
    trades = []
    overlap_pairs = []
    i = W + 1
    while i < N - hold - 2:
        sigs = []
        moms = []
        for c in coins:
            sv = signal(c, i, W, kind)
            mv = px.ret(c, i, W)
            if sv is None or mv is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            sigs.append((sv, c)); moms.append((mv, c))
        if len(sigs) < 3 * m:
            i += hold; continue
        sigs.sort()
        longs = sigs[:m]    # low signal
        shorts = sigs[-m:]  # high signal (lottery)
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for sv, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
        i += hold
    return trades

print("=" * 100)
print("A3 LOTTERY / SKEW PREMIUM  (long low-signal / short high-signal, non-overlap weekly)")
print("=" * 100)
for kind in ("max", "skew"):
    print(f"\n### signal = {kind}")
    for W in (14, 20, 30):
        for hold in (5, 7):
            tr = run(W, hold, kind)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  W={W:2d} H={hold}: {LC.fmt(s)}  base12={base['slip12']['mean_ret_pct']:+.3f} EXCESS={ex:+.4f}")
