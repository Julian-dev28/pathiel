"""A2 tsmom — time-series (absolute) momentum. Each coin long if own trailing-L
return>0 else short. Distinct factor from the live cross-sectional book.

Rule (lookahead-safe): at day i, signal_c = sign(close[i]/close[i-L]-1), decided
on bars <= i. Fill open[i+1], hold H, exit open[i+1+H]. Each coin = one signed leg.
Rebalance every H days (non-overlapping) to keep legs ~independent.
Vol-scaled portfolio: leg weight 1/realized_vol(L), reported separately.
Baseline: matched random side mix = realized long-fraction, same hold.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def run(L, hold):
    trades = []
    port = []
    longfrac_num = 0; longfrac_den = 0
    start = L + 1
    i = start
    while i < N - hold - 2:
        legs = []
        for c in coins:
            r = px.ret(c, i, L)
            if r is None: continue
            eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
            if not eo or not xo: continue
            sign = 1.0 if r > 0 else -1.0
            longfrac_den += 1; longfrac_num += (1 if sign > 0 else 0)
            gross = sign * (xo / eo - 1.0)
            vol = px.vol(c, i, L)
            w = (1.0 / vol) if (vol and vol > 0) else 0.0
            trades.append({"t": px.timeline[i + 1], "ret": gross})
            legs.append((gross, w))
        if legs:
            tw = sum(w for _, w in legs)
            if tw > 0:
                port.append({"t": px.timeline[i + 1], "ret": sum(g * w for g, w in legs) / tw})
        i += hold  # non-overlapping
    lf = longfrac_num / longfrac_den if longfrac_den else 0.5
    return trades, port, lf

print("=" * 110)
print("A2 TSMOM  (per-coin long if trailing-L>0 else short, non-overlapping rebal=H)")
print("=" * 110)
results = []
for L in (7, 14, 30, 60):
    for hold in (3, 7, 14):
        tr, port, lf = run(L, hold)
        s = LC.summarize(tr); sp = LC.summarize(port)
        if s.get("n", 0) == 0: continue
        base = LC.baseline_random(px, lf, hold, n_samp=4000)
        excess = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
        print(f"L={L:2d} H={hold:2d} longfrac={lf:.2f}")
        print(f"  perleg : {LC.fmt(s)}")
        print(f"  volport: {LC.fmt(sp)}")
        print(f"  base@longfrac EV12={base['slip12']['mean_ret_pct']:+.4f}  -> EXCESS(perleg)={excess:+.4f}")
        results.append((excess, L, hold, s, sp, base))

results.sort(reverse=True)
print("\nTOP by excess-over-baseline (perleg EV12):")
for excess, L, hold, s, sp, base in results[:4]:
    print(f"  L={L} H={hold} excess={excess:+.4f} perlegEV12={s['slip12']['mean_ret_pct']:+.4f} "
          f"volportEV12={sp['slip12']['mean_ret_pct']:+.4f} OOS={s['oos_12bps']['first_half_mean_pct']}/{s['oos_12bps']['second_half_mean_pct']}")
