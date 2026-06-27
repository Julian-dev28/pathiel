"""A13 relative_strength_drawdown — long survivors trading X% off their N-day high
while BTC up (drawdown-recovery), cross-sectional.

Rule (lookahead-safe): day i, dd_c = close[i]/max(high[i-N+1..i]) - 1  (<=0). BTC-up gate
(close>SMA20). Long-only basket of m coins. Variants:
  NEAR  = smallest |dd| (nearest high / strongest RS).
  DIP   = moderate band: dd in [-band_hi, -band_lo] (pulled back but alive) -> buy the dip.
  DEEP  = largest |dd| (most beaten) -> control.
Fill open[i+1], hold H, non-overlap. m=6. Compare each to random-long baseline in up-regime.
N{20,50} x H{5,7}.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def dd(c, i, n):
    cl = px.close(c, i)
    highs = [px.high(c, j) for j in range(i - n + 1, i + 1)]
    if cl is None or any(v is None for v in highs): return None
    mx = max(highs)
    return cl / mx - 1.0 if mx else None

def btc_up(i):
    cls = px.close("BTC", i)
    vals = [px.close("BTC", j) for j in range(i - 19, i + 1)]
    if cls is None or any(v is None for v in vals): return False
    return cls > statistics.mean(vals)

def baseline_up_long(hold, n_samp=4000, seed=3):
    import random
    rng = random.Random(seed)
    tr = []
    ups = [i for i in range(25, N - hold - 2) if btc_up(i)]
    if not ups: return LC.summarize([])
    for _ in range(n_samp):
        i = rng.choice(ups); c = rng.choice(coins)
        eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
        if not eo or not xo: continue
        tr.append({"t": px.timeline[i + 1], "ret": xo / eo - 1.0})
    return LC.summarize(tr)

def run(variant, n, hold, m=6, band=(0.05, 0.20)):
    trades = []
    for i in range(max(n, 21), N - hold - 2):
        if not btc_up(i): continue
        ds = []
        for c in coins:
            v = dd(c, i, n)
            if v is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            ds.append((v, c))
        if len(ds) < 3 * m: continue
        ds.sort()  # ascending dd: most-beaten first (most negative), near-high last (~0)
        if variant == "NEAR":
            grp = ds[-m:]
        elif variant == "DEEP":
            grp = ds[:m]
        else:  # DIP band
            band_lo, band_hi = band
            cand = [(v, c) for v, c in ds if -band_hi <= v <= -band_lo]
            if len(cand) < m: continue
            grp = cand[-m:]  # shallower end of the dip band (strongest dippers)
        for v, c in grp:
            eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
            if eo == 0: continue
            trades.append({"t": px.timeline[i + 1], "ret": xo / eo - 1.0})
    return trades

print("=" * 100)
print("A13 RELATIVE-STRENGTH DRAWDOWN  (long-only, BTC-up gated)")
print("=" * 100)
for n in (20, 50):
    for hold in (5, 7):
        base = baseline_up_long(hold)
        bev = base["slip12"]["mean_ret_pct"]
        print(f"\n### N={n} H={hold}  (up-regime random-long base12={bev:+.3f})")
        for variant in ("NEAR", "DIP", "DEEP"):
            tr = run(variant, n, hold)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0:
                print(f"  {variant}: no trades"); continue
            ex = s["slip12"]["mean_ret_pct"] - bev
            print(f"  {variant:4s}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
