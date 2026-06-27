"""W-B3 semivol_risk_targeting — risk-target the XS book on DOWNSIDE semideviation
instead of total vol. Does penalizing only downside vol beat symmetric vol-scaling
on Sharpe/drawdown? (B4 found symmetric EWMA targeting inert.)"""
import statistics
import laneB2_common as B

K, HOLD, M = 14, 7, 6
base = B.xs_book(K, HOLD, M)
rets0 = [x["ret"] for x in base]
target = statistics.pstdev(rets0)  # full-sample vol = leverage-neutral target
LEV_CAP = 3.0

def downside_semidev(xs, mar=0.0):
    d = [min(0.0, x - mar) for x in xs]
    return (sum(v * v for v in d) / len(d)) ** 0.5 if d else 0.0

def retarget(series, Lv, mode):
    out = []
    for idx in range(len(series)):
        if idx < Lv:
            continue
        past = [series[j]["ret"] for j in range(idx - Lv, idx)]
        if mode == "total":
            risk = statistics.pstdev(past) + 1e-9
        else:  # semivol
            risk = downside_semidev(past) + 1e-9
        w = min(target / risk, LEV_CAP)
        out.append({**series[idx], "ret": w * series[idx]["ret"]})
    return out

print("=" * 105)
print("W-B3 SEMIVOL RISK-TARGETING on XS book (k=14,H=7,m=6). target=full-sample vol, lev cap 3x")
print("=" * 105)
o = B.report("raw (no targeting)", base, HOLD); B.pr(o); raw_sh = o["ann_sharpe"]; raw_dd = o["maxdd_pct"]
print()
results = {}
for Lv in (4, 6, 8):
    for mode in ("total", "semivol"):
        rt = retarget(base, Lv, mode)
        oo = B.report(f"{mode} Lv={Lv}", rt, HOLD); B.pr(oo)
        results[(mode, Lv)] = oo
    print()

print("-" * 105)
print("LIFT vs raw (annSharpe) and drawdown delta:")
for Lv in (4, 6, 8):
    t = results[("total", Lv)]; s = results[("semivol", Lv)]
    print(f"  Lv={Lv}: total annSh {t['ann_sharpe']:+.3f} (lift {t['ann_sharpe']-raw_sh:+.3f}, dd {t['maxdd_pct']-raw_dd:+.1f}pp) | "
          f"semivol annSh {s['ann_sharpe']:+.3f} (lift {s['ann_sharpe']-raw_sh:+.3f}, dd {s['maxdd_pct']-raw_dd:+.1f}pp) | "
          f"semi-vs-total {s['ann_sharpe']-t['ann_sharpe']:+.3f}")
