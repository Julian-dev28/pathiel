"""W-B4 efficiency_ratio_gate — Kaufman ER (net move / path length) as a trend-quality
gate on XS-momentum legs; cut choppy-path names. Measure dud-rate cut + Sharpe lift,
compare to the ADX>25 gate (B6)."""
import statistics
import laneB2_common as B
px = B.px; coins = B.coins; N = B.N
K, HOLD, M = 14, 7, 6

def kaufman_er(c, i, k=K):
    """net move / sum abs single-bar moves over last k bars, known at i."""
    a, b = px.close(c, i - k), px.close(c, i)
    if a is None or b is None: return None
    path = 0.0
    for j in range(i - k + 1, i + 1):
        x, y = px.close(c, j - 1), px.close(c, j)
        if x is None or y is None: return None
        path += abs(y - x)
    if path == 0: return None
    return abs(b - a) / path

def book(er_min=None, adx_min=None):
    """returns (rebal_series, leg_returns). leg gating by ER and/or ADX at bar i."""
    series, legs_all = [], []
    i = max(K, 2*14) + 1
    while i < N - HOLD - 2:
        ranked = []
        for c in coins:
            r = px.ret(c, i, K)
            if r is None: continue
            if not (px.open(c, i+1) and px.open(c, i+1+HOLD)): continue
            ranked.append((r, c))
        if len(ranked) < 3*M:
            i += HOLD; continue
        ranked.sort()
        legs = []
        for side, grp in (("long", ranked[-M:]), ("short", ranked[:M])):
            sign = 1.0 if side == "long" else -1.0
            for r, c in grp:
                if er_min is not None:
                    er = kaufman_er(c, i)
                    if er is None or er < er_min: continue
                if adx_min is not None:
                    a = B.adx(c, i)
                    if a is None or a < adx_min: continue
                eo, xo = px.open(c, i+1), px.open(c, i+1+HOLD)
                if not eo: continue
                lr = sign * (xo/eo - 1.0)
                legs.append(lr); legs_all.append(lr)
        if legs:
            series.append({"t": px.timeline[i+1], "ret": statistics.mean(legs), "i": i})
        i += HOLD
    return series, legs_all

def dud(legs):
    return sum(1 for r in legs if r <= 0) / len(legs) if legs else None

print("=" * 100)
print("W-B4 EFFICIENCY-RATIO GATE on XS-momentum legs (k=14,H=7,m=6)")
print("=" * 100)
configs = [("raw", {}), ("ADX>25 (B6)", {"adx_min": 25}),
           ("ER>=0.30", {"er_min": 0.30}), ("ER>=0.40", {"er_min": 0.40}),
           ("ER>=0.50", {"er_min": 0.50})]
res = {}
for name, kw in configs:
    s, legs = book(**kw)
    o = B.report(name, s, HOLD); res[name] = (o, legs)
    print(f"  {name:<14} n_rebal={o['n']:3d} n_legs={len(legs):4d} dud={dud(legs):.3f} "
          f"annSh={o['ann_sharpe']:+.3f} maxDD={o['maxdd_pct']:+.1f}% OOS sh {o['h1_sh']}/{o['h2_sh']}")

raw_o, raw_legs = res["raw"]
adx_o, _ = res["ADX>25 (B6)"]
print("\n" + "-" * 100)
print(f"raw: dud={dud(raw_legs):.3f} annSh={raw_o['ann_sharpe']:+.3f}")
print(f"ADX gate lift: annSh {adx_o['ann_sharpe']-raw_o['ann_sharpe']:+.3f}, dud {dud(res['ADX>25 (B6)'][1])-dud(raw_legs):+.3f}")
for name in ("ER>=0.30", "ER>=0.40", "ER>=0.50"):
    o, legs = res[name]
    print(f"{name} lift vs raw: annSh {o['ann_sharpe']-raw_o['ann_sharpe']:+.3f}, "
          f"dud {dud(legs)-dud(raw_legs):+.3f}, vs ADX annSh {o['ann_sharpe']-adx_o['ann_sharpe']:+.3f}")
