"""D2 funding_momentum — does the funding TREND predict the next PRICE move?
Cross-sectional, market-neutral, inv-vol. Signal = trailing mean hourly funding over L days.
Measures PRICE return only (carry stripped — that's D1). Tests CONTRARIAN (long low-funding /
short high-funding = fade the crowded side) vs MOMENTUM (opposite). Lookahead-safe, null-scored.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as al, funding_lib as fl

d = al.load_dataset(); f = fl.load_funding()
DAY = 86_400_000
COINS = [c for c in d["coins"] if fl.rows(f, c)]
cand = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
fund_start = min(fl.rows(f, c)[0][0] for c in COINS)
fund_end = max(fl.rows(f, c)[-1][0] for c in COINS)
days = [b[al.T] for b in al.candles(d, "BTC", "1d") if fund_start <= b[al.T] <= fund_end]
days = sorted(days)
btc_ret = {}
for t in days:
    b = cand["BTC"].get(t)
    btc_ret[t] = (b[al.C] - b[al.O]) / b[al.O] if b and b[al.O] else 0.0

def vol(c, t, lb=20):
    cs = [x for x in sorted(cand[c]) if x < t][-(lb + 1):]
    if len(cs) < 6: return None
    r = [(cand[c][b][al.C] - cand[c][a][al.C]) / cand[c][a][al.C]
         for a, b in zip(cs, cs[1:]) if cand[c][a][al.C]]
    return statistics.pstdev(r) + 1e-9 if r else None

def fwd_price_ret(c, t, h):
    """price return over [t, t+h days): open of day t to close of day t+h-1. Lookahead-safe
    given signal decided at start of day t."""
    o = cand[c].get(t)
    if not o or not o[al.O]: return None
    tn = t + (h - 1) * DAY
    e = cand[c].get(tn)
    if not e: return None
    return (e[al.C] - o[al.O]) / o[al.O]

# precompute per-day table
def build_table(L_hours, h):
    tab = []
    for t in days:
        row = {}
        for c in COINS:
            tf = fl.trailing_funding(f, c, t, L_hours)
            pr = fwd_price_ret(c, t, h)
            v = vol(c, t)
            if tf is None or pr is None or v is None:
                continue
            row[c] = {"sig": tf, "pr": pr, "iv": 1.0 / v}
        if row:
            tab.append((t, row))
    return tab

def book(tab, K, side="contrarian", perm=None, rng=None):
    """Returns (gross_series, turnover_series, btc_corr_pairs). side contrarian=long low-funding."""
    prev = {}; gross = []; turn = []; bpairs = []
    for t, row in tab:
        names = list(row)
        if len(names) < 2 * K:
            continue
        if perm:
            rng.shuffle(names); longs, shorts = names[:K], names[K:2 * K]
        else:
            ranked = sorted(names, key=lambda c: row[c]["sig"])
            if side == "contrarian":   # long LOW funding, short HIGH funding
                longs, shorts = ranked[:K], ranked[-K:]
            else:                       # momentum: long HIGH funding
                longs, shorts = ranked[-K:], ranked[:K]
        w = {}
        for grp, sgn in [(longs, 1.0), (shorts, -1.0)]:
            s = sum(row[c]["iv"] for c in grp)
            for c in grp: w[c] = sgn * 0.5 * row[c]["iv"] / s
        g = sum(w[c] * row[c]["pr"] for c in w)
        tn = sum(abs(w.get(c, 0) - prev.get(c, 0)) for c in set(w) | set(prev))
        gross.append((t, g)); turn.append(tn); bpairs.append((g, btc_ret[t]))
        prev = w
    return gross, turn, bpairs

def report(tab, K, side):
    g, turn, bp = book(tab, K, side)
    if len(g) < 10: return {"n": len(g)}
    n = len(g); rets = [x[1] for x in g]; tmean = statistics.mean(turn)
    # beta to BTC
    gm = statistics.mean(rets); bm = statistics.mean(v for _, v in bp)
    cov = statistics.mean((gi - gm) * (bi - bm) for gi, bi in bp)
    bvar = statistics.pvariance([bi for _, bi in bp]) + 1e-12
    beta = cov / bvar
    out = {"n": n, "turn": round(tmean, 3), "beta_btc": round(beta, 3)}
    for bps in [0, 12, 25]:
        net = [ri - (bps / 1e4) * tu for ri, tu in zip(rets, turn)]
        mu = statistics.mean(net); sd = statistics.pstdev(net) + 1e-12
        out[f"s{bps}"] = {"bps_d": round(1e4 * mu, 2), "shrp_ann": round(mu / sd * 365 ** .5, 2),
                          "win": round(sum(1 for x in net if x > 0) / n, 3)}
    mid = sorted(x[0] for x in g)[n // 2]
    h1 = [ri - .0012 * tu for (t, ri), tu in zip(g, turn) if t <= mid]
    h2 = [ri - .0012 * tu for (t, ri), tu in zip(g, turn) if t > mid]
    out["oos12"] = {"h1": round(1e4 * statistics.mean(h1), 2),
                    "h2": round(1e4 * statistics.mean(h2), 2)}
    return out

def null(tab, K, side, n_iter=3000, seed=1):
    real, _, _ = book(tab, K, side); rm = statistics.mean(x[1] for x in real)
    rng = random.Random(seed); ge = 0; nm = []
    for _ in range(n_iter):
        g, _, _ = book(tab, K, side, perm=True, rng=rng)
        m = statistics.mean(x[1] for x in g); nm.append(m)
        if m >= rm: ge += 1
    mu = statistics.mean(nm); sd = statistics.pstdev(nm) + 1e-12
    return {"real_bps": round(1e4 * rm, 2), "null_bps": round(1e4 * mu, 2),
            "z": round((rm - mu) / sd, 2), "p": round((ge + 1) / (n_iter + 1), 4)}

if __name__ == "__main__":
    print("=== D2 funding_momentum (PRICE prediction, carry stripped) ===")
    for L in [72, 168]:
        for h in [1, 3, 5]:
            tab = build_table(L, h)
            for side in ["contrarian", "momentum"]:
                r = report(tab, 8, side)
                print(f"L={L}h h={h}d K=8 {side:10s}: n={r['n']} turn={r['turn']} "
                      f"beta={r['beta_btc']} | net12={r['s12']['bps_d']}bps shrp={r['s12']['shrp_ann']} "
                      f"win={r['s12']['win']} | OOS {r['oos12']['h1']}/{r['oos12']['h2']}")
    print("\n-- nulls on the promising configs --")
    for L, h, side in [(168, 1, "contrarian"), (168, 3, "contrarian"), (72, 1, "contrarian"),
                       (168, 1, "momentum")]:
        tab = build_table(L, h)
        print(f"L={L} h={h} {side}:", null(tab, 8, side))
