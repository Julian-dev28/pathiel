"""A6 vol_managed_momentum — scale the XS-momentum book exposure by inverse
realized strategy vol (Barroso momentum-crash protection). Metric: Sharpe lift.

Build XS-momentum portfolio: day i rank trailing-k return, long top-m/short bottom-m,
equal-weight, fill open[i+1], hold H non-overlapping -> raw portfolio return series.
Vol-manage: w_t = target / realized_vol(last Lv portfolio returns, known before t).
Compare Sharpe(raw) vs Sharpe(w*raw). Both OOS halves. Vol-scaling can't add EV per se,
only risk-adjust: the win is Sharpe + drawdown, so report both.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def xs_mom_series(k, hold, m=6):
    series = []  # list of {t, ret}
    i = max(k, 8) + 1
    while i < N - hold - 2:
        ranked = []
        for c in coins:
            r = px.ret(c, i, k)
            if r is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            ranked.append((r, c))
        if len(ranked) < 3 * m:
            i += hold; continue
        ranked.sort()
        longs = ranked[-m:]; shorts = ranked[:m]
        legs = []
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for r, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                legs.append(sign * (xo / eo - 1.0))
        if legs:
            series.append({"t": px.timeline[i + 1], "ret": statistics.mean(legs)})
        i += hold
    return series

def sharpe(rets):
    if len(rets) < 3: return None
    return statistics.mean(rets) / (statistics.pstdev(rets) + 1e-12)

def vol_manage(series, Lv, target):
    out = []
    for idx in range(len(series)):
        if idx < Lv:
            continue
        past = [series[j]["ret"] for j in range(idx - Lv, idx)]
        rv = statistics.pstdev(past) + 1e-9
        w = target / rv
        w = min(w, 3.0)  # leverage cap
        out.append({"t": series[idx]["t"], "ret": w * series[idx]["ret"]})
    return out

def half_sharpe(series):
    f, s = LC.alpha_lib.time_split(series) if hasattr(LC, "alpha_lib") else (None, None)
    import alpha_lib
    f, s = alpha_lib.time_split(series)
    return sharpe([x["ret"] for x in f]), sharpe([x["ret"] for x in s])

print("=" * 100)
print("A6 VOL-MANAGED MOMENTUM  (Sharpe lift over raw XS-momentum)")
print("=" * 100)
for k in (14, 30):
    for hold in (7,):
        raw = xs_mom_series(k, hold)
        if len(raw) < 10: continue
        sr_raw = sharpe([x["ret"] for x in raw])
        mean_raw = statistics.mean([x["ret"] for x in raw])
        h1r, h2r = half_sharpe(raw)
        print(f"\nk={k} H={hold}  RAW: n={len(raw)} mean={100*mean_raw:+.3f}% sharpe={sr_raw:+.3f} OOSsh {h1r:+.3f}/{h2r:+.3f}")
        target = statistics.pstdev([x["ret"] for x in raw])  # target = full-sample vol
        for Lv in (4, 8):
            vm = vol_manage(raw, Lv, target)
            sr_vm = sharpe([x["ret"] for x in vm])
            mean_vm = statistics.mean([x["ret"] for x in vm])
            h1, h2 = half_sharpe(vm)
            lift = (sr_vm - sr_raw) if (sr_vm and sr_raw) else None
            print(f"   VM Lv={Lv}: n={len(vm)} mean={100*mean_vm:+.3f}% sharpe={sr_vm:+.3f} "
                  f"OOSsh {h1:+.3f}/{h2:+.3f}  LIFT={lift:+.3f}")
