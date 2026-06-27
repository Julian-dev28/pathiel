"""A7 momentum_of_momentum — rank by the SLOPE/acceleration of own momentum;
long accelerating trends / short decelerating.

Rule (lookahead-safe): day i, accel_c = ret(i,k) - ret(i-k,k) (recent k-window return
minus prior k-window return; the change in momentum). Long top-m accel, short bottom-m.
Fill open[i+1], hold H, non-overlapping. m=6. vs random 50/50 baseline.
Also a regression-slope-of-slope variant: fit line to last 2k cum-returns, use d(slope).
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def accel(c, i, k):
    r_now = px.ret(c, i, k)
    r_prev = px.ret(c, i - k, k)
    if r_now is None or r_prev is None: return None
    return r_now - r_prev

def run(k, hold, side_dir, m=6):
    """side_dir=+1: long accelerating/short decelerating; -1 opposite."""
    trades = []
    i = 2 * k + 1
    while i < N - hold - 2:
        accs = []
        for c in coins:
            a = accel(c, i, k)
            if a is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            accs.append((a, c))
        if len(accs) < 3 * m:
            i += hold; continue
        accs.sort()
        decel = accs[:m]; accel_grp = accs[-m:]
        if side_dir > 0:
            longs, shorts = accel_grp, decel
        else:
            longs, shorts = decel, accel_grp
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for a, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
        i += hold
    return trades

print("=" * 100)
print("A7 MOMENTUM-OF-MOMENTUM  (long accelerating / short decelerating)")
print("=" * 100)
for sd, lbl in ((+1, "ACCEL+"), (-1, "ACCEL-")):
    print(f"\n### {lbl} (long {'accelerating' if sd>0 else 'decelerating'})")
    for k in (7, 14, 30):
        for hold in (5, 7, 14):
            tr = run(k, hold, sd)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  k={k:2d} H={hold:2d}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
