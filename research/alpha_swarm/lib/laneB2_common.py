"""Lane B2 shared substrate: the live XS-momentum book + per-day metadata so
overlays (skew arm, turbulence upsize, ADX gate, vol-targeting) can be measured
as a Sharpe/drawdown LIFT over the un-overlaid book. Lookahead-safe: every
decision at bar i uses bars <= i, fills at open[i+1].
"""
import statistics, math
import laneA_common as LC
import alpha_lib

px = LC.Px("1d")
coins = px.coins
N = px.N

def sharpe(rets):
    if len(rets) < 3: return None
    return statistics.mean(rets) / (statistics.pstdev(rets) + 1e-12)

def ann_sharpe(rets, hold):
    s = sharpe(rets)
    if s is None: return None
    # rebal every `hold` days -> ~ (365/hold) periods per yr
    return s * math.sqrt(365.0 / hold)

def max_dd(rets):
    eq = 1.0; peak = 1.0; mdd = 0.0
    for r in rets:
        eq *= (1 + r); peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return mdd

def market_ret(i):
    """equal-weight avg single-bar coin return ending at i (known at i)."""
    rs = [px.dret(c, i) for c in coins]
    rs = [r for r in rs if r is not None]
    return statistics.mean(rs) if rs else None

def market_skew(i, W=20):
    rs = [market_ret(j) for j in range(i - W + 1, i + 1)]
    rs = [r for r in rs if r is not None]
    if len(rs) < W // 2: return None
    m = statistics.mean(rs); sd = statistics.pstdev(rs)
    if sd == 0: return 0.0
    m3 = statistics.mean([(r - m) ** 3 for r in rs])
    return m3 / (sd ** 3)

def btc_vol(i, k=10):
    return px.vol("BTC", i, k)

def adx(c, i, k=14):
    """Wilder ADX from daily bars up to i. Lookahead-safe."""
    if i - k - k < 0: return None
    trs, pdm, ndm = [], [], []
    for j in range(i - 2 * k, i + 1):
        b, pb = px.bar(c, j), px.bar(c, j - 1)
        if b is None or pb is None: return None
        up = b[alpha_lib.H] - pb[alpha_lib.H]
        dn = pb[alpha_lib.L] - b[alpha_lib.L]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr = max(b[alpha_lib.H] - b[alpha_lib.L],
                 abs(b[alpha_lib.H] - pb[alpha_lib.C]),
                 abs(b[alpha_lib.L] - pb[alpha_lib.C]))
        trs.append(tr)
    # Wilder smoothing
    def wilder(xs):
        v = sum(xs[:k])
        out = [v]
        for x in xs[k:]:
            v = v - v / k + x
            out.append(v)
        return out
    atr = wilder(trs); pdi_s = wilder(pdm); ndi_s = wilder(ndm)
    dxs = []
    for a, p, n in zip(atr, pdi_s, ndi_s):
        if a == 0: continue
        pdi = 100 * p / a; ndi = 100 * n / a
        s = pdi + ndi
        if s == 0: continue
        dxs.append(100 * abs(pdi - ndi) / s)
    if len(dxs) < k: return None
    return statistics.mean(dxs[-k:])

def xs_book(k=14, hold=7, m=6, adx_min=None, adx_k=14):
    """Daily-rebal market-neutral XS-momentum book. Returns list of per-rebal dicts:
      {t, ret, i, skew, btcvol, n_legs}
    adx_min: if set, only include coins whose ADX(adx_k) at decision bar i >= adx_min.
    """
    series = []
    i = max(k, 2 * adx_k if adx_min else 8) + 1
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
                if adx_min is not None:
                    a = adx(c, i, adx_k)
                    if a is None or a < adx_min: continue
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if not eo or eo == 0: continue
                legs.append(sign * (xo / eo - 1.0))
        if legs:
            series.append({"t": px.timeline[i + 1], "ret": statistics.mean(legs),
                           "i": i, "skew": market_skew(i), "btcvol": btc_vol(i),
                           "n_legs": len(legs)})
        i += hold
    return series

def report(name, series, hold=7):
    rets = [x["ret"] for x in series]
    f, s = alpha_lib.time_split(series)
    out = {
        "name": name, "n": len(series),
        "mean_pct": round(100 * statistics.mean(rets), 4) if rets else None,
        "sharpe": round(sharpe(rets), 3) if len(rets) >= 3 else None,
        "ann_sharpe": round(ann_sharpe(rets, hold), 3) if len(rets) >= 3 else None,
        "maxdd_pct": round(100 * max_dd(rets), 2),
        "h1_sh": round(sharpe([x["ret"] for x in f]), 3) if len(f) >= 3 else None,
        "h2_sh": round(sharpe([x["ret"] for x in s]), 3) if len(s) >= 3 else None,
        "h1_mean": round(100 * statistics.mean([x["ret"] for x in f]), 4) if f else None,
        "h2_mean": round(100 * statistics.mean([x["ret"] for x in s]), 4) if s else None,
    }
    return out

def pr(o):
    print(f"  {o['name']:<24} n={o['n']:3d} mean={o['mean_pct']:+.4f}% "
          f"sh={o['sharpe']:+.3f} annSh={o['ann_sharpe']:+.3f} maxDD={o['maxdd_pct']:+.1f}% "
          f"| OOS sh {o['h1_sh']}/{o['h2_sh']} mean {o['h1_mean']}/{o['h2_mean']}")
