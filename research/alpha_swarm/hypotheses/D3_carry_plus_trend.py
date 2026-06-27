"""D3 carry_plus_trend — combine funding signal with price MOMENTUM into one market-neutral
book; does the combo beat either alone? Three books, all inv-vol, gross-1/net-0:
  MOM   : score = trailing price return (long winners / short losers) — the live XS factor.
  FUND  : score = -trailing funding (long low-funding / short high-funding) — D1/D2 direction.
  COMBO : score = z(mom) + z(fund).
Return = price + carry (carry=-Sum w*fund). Lookahead-safe, OOS halves, slippage, correlation.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as al, funding_lib as fl

d = al.load_dataset(); f = fl.load_funding()
DAY = 86_400_000
COINS = [c for c in d["coins"] if fl.rows(f, c)]
cand = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
fs = min(fl.rows(f, c)[0][0] for c in COINS); fe = max(fl.rows(f, c)[-1][0] for c in COINS)
days = sorted(b[al.T] for b in al.candles(d, "BTC", "1d") if fs <= b[al.T] <= fe)

def vol(c, t, lb=20):
    cs = [x for x in sorted(cand[c]) if x < t][-(lb + 1):]
    if len(cs) < 6: return None
    r = [(cand[c][b][al.C] - cand[c][a][al.C]) / cand[c][a][al.C]
         for a, b in zip(cs, cs[1:]) if cand[c][a][al.C]]
    return statistics.pstdev(r) + 1e-9 if r else None

def trail_ret(c, t, Ld):
    cs = [x for x in sorted(cand[c]) if x < t][-(Ld + 1):]
    if len(cs) < 2: return None
    a, b = cand[c][cs[0]][al.C], cand[c][cs[-1]][al.C]
    return (b - a) / a if a else None

def day_data(t, Lmom_d, Lfund_h, h):
    row = {}
    for c in COINS:
        o = cand[c].get(t)
        tn = cand[c].get(t + (h - 1) * DAY)
        if not o or not o[al.O] or not tn: continue
        pr = (tn[al.C] - o[al.O]) / o[al.O]
        fdcarry = fl.cum_funding(f, c, t - 1, t + h * DAY - 1)  # carry over hold
        mom = trail_ret(c, t, Lmom_d); fnd = fl.trailing_funding(f, c, t, Lfund_h)
        v = vol(c, t)
        if mom is None or fnd is None or v is None: continue
        row[c] = {"pr": pr, "carry": fdcarry, "mom": mom, "fund": fnd, "iv": 1.0 / v}
    return row

def zscore(vals):
    m = statistics.mean(vals); s = statistics.pstdev(vals) + 1e-12
    return {k: (v - m) / s for k, v in zip(range(len(vals)), vals)}

def book(kind, Lmom_d=14, Lfund_h=168, h=3, K=8):
    prev = {}; series = []
    for t in days:
        row = day_data(t, Lmom_d, Lfund_h, h)
        if len(row) < 2 * K: continue
        cs = list(row)
        if kind == "mom":
            score = {c: row[c]["mom"] for c in cs}
        elif kind == "fund":
            score = {c: -row[c]["fund"] for c in cs}
        else:
            mm = statistics.mean(row[c]["mom"] for c in cs); ms = statistics.pstdev([row[c]["mom"] for c in cs]) + 1e-12
            fm = statistics.mean(row[c]["fund"] for c in cs); fsd = statistics.pstdev([row[c]["fund"] for c in cs]) + 1e-12
            score = {c: (row[c]["mom"] - mm) / ms + (-(row[c]["fund"] - fm) / fsd) for c in cs}
        ranked = sorted(cs, key=lambda c: score[c])
        longs, shorts = ranked[-K:], ranked[:K]
        w = {}
        for grp, sgn in [(longs, 1.0), (shorts, -1.0)]:
            s = sum(row[c]["iv"] for c in grp)
            for c in grp: w[c] = sgn * 0.5 * row[c]["iv"] / s
        g = sum(w[c] * row[c]["pr"] for c in w) - sum(w[c] * row[c]["carry"] for c in w)
        tn = sum(abs(w.get(c, 0) - prev.get(c, 0)) for c in set(w) | set(prev))
        series.append((t, g, tn)); prev = w
    return series

def rep(series):
    n = len(series); rets = [x[1] for x in series]
    out = {"n": n, "turn": round(statistics.mean(x[2] for x in series), 3)}
    for bps in [0, 12, 25]:
        net = [r - (bps / 1e4) * tu for _, r, tu in series]
        mu = statistics.mean(net); sd = statistics.pstdev(net) + 1e-12
        out[f"s{bps}"] = (round(1e4 * mu, 2), round(mu / sd * 365 ** .5, 2))
    mid = sorted(x[0] for x in series)[n // 2]
    h1 = [r - .0012 * tu for t, r, tu in series if t <= mid]
    h2 = [r - .0012 * tu for t, r, tu in series if t > mid]
    out["oos12"] = (round(1e4 * statistics.mean(h1), 2), round(1e4 * statistics.mean(h2), 2))
    return out

if __name__ == "__main__":
    print("=== D3 carry_plus_trend (price+carry, K=8, Lmom=14d Lfund=168h h=3d) ===")
    bm = book("mom"); bf = book("fund"); bc = book("combo")
    for name, b in [("MOM", bm), ("FUND", bf), ("COMBO", bc)]:
        r = rep(b)
        print(f"{name:6s}: n={r['n']} turn={r['turn']} | net0={r['s0']} net12={r['s12']} "
              f"net25={r['s25']} (bps/d,Sharpe_ann) | OOS12 {r['oos12']}")
    # correlation between mom and fund book returns (diversification)
    mr = {t: g for t, g, _ in bm}; fr = {t: g for t, g, _ in bf}
    com = [(mr[t], fr[t]) for t in mr if t in fr]
    mm = statistics.mean(x for x, _ in com); fm = statistics.mean(y for _, y in com)
    cov = statistics.mean((x - mm) * (y - fm) for x, y in com)
    sx = statistics.pstdev([x for x, _ in com]); sy = statistics.pstdev([y for _, y in com])
    print(f"\ncorr(MOM, FUND) daily returns = {cov / (sx * sy + 1e-12):.3f}  (n={len(com)})")
    print("Lmom sweep on COMBO:")
    for Lm in [7, 14, 30]:
        r = rep(book("combo", Lmom_d=Lm))
        print(f"  Lmom={Lm}d: net12={r['s12']} OOS {r['oos12']}")
