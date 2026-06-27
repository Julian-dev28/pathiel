"""D1 funding_carry — market-neutral carry: short top-positive-funding / long
top-negative(least-positive)-funding, inverse-vol weighted, daily rebal.

Lookahead-safe: signal = trailing funding through start of day i (t_i). Position
held DURING day i, realize price_ret_i=(C_i-O_i)/O_i and funding_i=sum of hourly
rates over [t_i, t_i+1d). Fill at O_i = first bar after the decision. Document:
close-of-prior-day decision, open-fill, intraday realization — no peeking.

Book return per day = sum_i w_i*(price_ret_i - funding_i), w signed, sum|w|=1
(gross), sum w=0 (neutral). Carry harvested = -sum w_i*funding_i (short the
high-funding coins to COLLECT). Fees charged on turnover sum|Δw|.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as al, funding_lib as fl

d = al.load_dataset(); f = fl.load_funding()
COINS = [c for c in d["coins"] if fl.rows(f, c)]
DAY = 86_400_000

# Build per-coin daily series within funding window.
fund_start = min(fl.rows(f, c)[0][0] for c in COINS)
fund_end = max(fl.rows(f, c)[-1][0] for c in COINS)

# common daily grid from 1d candles, restricted to funding window
btc_days = [b[al.T] for b in al.candles(d, "BTC", "1d")
            if fund_start <= b[al.T] <= fund_end]

def day_bar(coin, t):
    for b in al.candles(coin if False else coin, "1d") if False else al.candles(d, coin, "1d"):
        if b[al.T] == t:
            return b
    return None

# index candles by t per coin
cand = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}

def funding_day(coin, t):
    return fl.cum_funding(f, coin, t - 1, t + DAY - 1)  # (t, t+1d] ~ that UTC day

def price_ret_day(coin, t):
    b = cand[coin].get(t)
    if not b or b[al.O] == 0:
        return None
    return (b[al.C] - b[al.O]) / b[al.O]

def trailing_fund(coin, t, hours):
    return fl.trailing_funding(f, coin, t, hours)  # mean hourly over (t-h, t]

def realized_vol(coin, t, lookback=20):
    # daily close-to-close vol over `lookback` days ending before t (lookahead-safe)
    cs = sorted(cand[coin].keys())
    cs = [x for x in cs if x < t][-(lookback + 1):]
    if len(cs) < 6:
        return None
    rets = []
    for a, b in zip(cs, cs[1:]):
        pa, pb = cand[coin][a][al.C], cand[coin][b][al.C]
        if pa: rets.append((pb - pa) / pa)
    return statistics.pstdev(rets) + 1e-9 if rets else None

def build_book(K, sig_hours, lookback_vol=20, invvol=True):
    """Returns list of per-day dicts: t, gross_ret, carry, price_pnl, turnover, weights."""
    days = sorted([t for t in btc_days])
    prev_w = {}
    out = []
    for t in days:
        # signal known at t (start of day i): trailing funding through t
        sig = {}
        for c in COINS:
            tf = trailing_fund(c, t, sig_hours)
            pr = price_ret_day(c, t)
            fd = funding_day(c, t)
            if tf is None or pr is None:
                continue
            sig[c] = (tf, pr, fd)
        if len(sig) < 2 * K:
            continue
        ranked = sorted(sig.keys(), key=lambda c: sig[c][0])
        longs = ranked[:K]          # lowest funding -> long (collect / pay least)
        shorts = ranked[-K:]        # highest funding -> short (collect)
        w = {}
        def assign(names, sign):
            if invvol:
                iv = {}
                for c in names:
                    v = realized_vol(c, t, lookback_vol)
                    iv[c] = 1.0 / v if v else None
                iv = {c: x for c, x in iv.items() if x}
                s = sum(iv.values())
                if not s:
                    return
                for c, x in iv.items():
                    w[c] = sign * 0.5 * x / s
            else:
                for c in names:
                    w[c] = sign * 0.5 / len(names)
        assign(longs, +1.0); assign(shorts, -1.0)
        # realize
        price_pnl = sum(w[c] * sig[c][1] for c in w)
        carry = -sum(w[c] * sig[c][2] for c in w)  # collect when short high-funding
        gross = price_pnl + carry
        turn = sum(abs(w.get(c, 0.0) - prev_w.get(c, 0.0))
                   for c in set(w) | set(prev_w))
        out.append({"t": t, "gross": gross, "carry": carry,
                    "price_pnl": price_pnl, "turnover": turn})
        prev_w = w
    return out

def report(book, label):
    if len(book) < 10:
        return {"label": label, "n": len(book), "verdict": "thin"}
    n = len(book)
    carry_mean = statistics.mean(b["carry"] for b in book)
    turn_mean = statistics.mean(b["turnover"] for b in book)
    res = {"label": label, "n": n,
           "carry_bps_day": round(1e4 * carry_mean, 3),
           "turnover_day": round(turn_mean, 3)}
    for bps in [0, 6, 12, 25, 50]:
        cost = bps / 1e4
        net = [b["gross"] - cost * b["turnover"] for b in book]
        mu = statistics.mean(net); sd = statistics.pstdev(net) + 1e-12
        wins = sum(1 for x in net if x > 0)
        res[f"slip{bps}"] = {
            "mean_bps_day": round(1e4 * mu, 3),
            "total_pct": round(100 * sum(net), 2),
            "sharpe_day": round(mu / sd, 3),
            "sharpe_ann": round((mu / sd) * (365 ** 0.5), 2),
            "win": round(wins / n, 3),
        }
    # OOS halves at 12bps
    mid = sorted(b["t"] for b in book)[n // 2]
    for tier in [12, 25]:
        cost = tier / 1e4
        h1 = [b["gross"] - cost * b["turnover"] for b in book if b["t"] <= mid]
        h2 = [b["gross"] - cost * b["turnover"] for b in book if b["t"] > mid]
        res[f"oos{tier}"] = {
            "h1_bps": round(1e4 * statistics.mean(h1), 3) if h1 else None,
            "h2_bps": round(1e4 * statistics.mean(h2), 3) if h2 else None,
            "n1": len(h1), "n2": len(h2)}
    return res

def perm_null(book_fn, K, sig_hours, n_iter=400, seed=0):
    """Null: shuffle the funding->coin mapping each day (random neutral inv-vol book),
    compare mean daily gross. Tests whether REAL funding ranking adds over random neutral."""
    rng = random.Random(seed)
    real = book_fn(K, sig_hours)
    real_mean = statistics.mean(b["gross"] for b in real)
    # random books: same structure but random K-long/K-short split
    days = sorted([t for t in btc_days])
    ge = 0; null_means = []
    for it in range(n_iter):
        prev_w = {}; tot = []
        for t in days:
            elig = []
            for c in COINS:
                pr = price_ret_day(c, t); fd = funding_day(c, t)
                v = realized_vol(c, t, 20)
                if pr is None or v is None:
                    continue
                elig.append((c, pr, fd, 1.0 / v))
            if len(elig) < 2 * K:
                continue
            rng.shuffle(elig)
            longs = elig[:K]; shorts = elig[K:2 * K]
            w = {}
            for grp, sign in [(longs, +1), (shorts, -1)]:
                s = sum(x[3] for x in grp)
                for c, pr, fd, ivv in grp:
                    w[c] = sign * 0.5 * ivv / s
            gross = sum(w[c] * dict((x[0], x[1]) for x in elig)[c] for c in w) \
                - sum(w[c] * dict((x[0], x[2]) for x in elig)[c] for c in w)
            tot.append(gross)
        m = statistics.mean(tot)
        null_means.append(m)
        if m >= real_mean:
            ge += 1
    mu = statistics.mean(null_means); sd = statistics.pstdev(null_means) + 1e-12
    return {"real_mean_bps": round(1e4 * real_mean, 3),
            "null_mean_bps": round(1e4 * mu, 3),
            "z": round((real_mean - mu) / sd, 2),
            "p": round((ge + 1) / (n_iter + 1), 4)}

if __name__ == "__main__":
    print("=== D1 funding_carry ===")
    print(f"coins={len(COINS)} days~{len(btc_days)} window={(fund_end-fund_start)/DAY:.0f}d")
    for K in [5, 8, 10]:
        for sh in [24, 72, 168]:
            r = report(build_book(K, sh), f"K={K} sig={sh}h invvol")
            s12 = r.get("slip12", {}); o = r.get("oos12", {})
            print(f"\n-- {r['label']}: n={r['n']} carry={r.get('carry_bps_day')}bps/d "
                  f"turn={r.get('turnover_day')}")
            for bps in [0, 12, 25]:
                x = r.get(f"slip{bps}")
                if x: print(f"   slip{bps}: mean={x['mean_bps_day']}bps/d "
                            f"sharpe_ann={x['sharpe_ann']} win={x['win']} tot={x['total_pct']}%")
            print(f"   OOS12: h1={o.get('h1_bps')} h2={o.get('h2_bps')} bps/d")
    print("\n-- equal-weight K=8 sig=72h --")
    r = report(build_book(8, 72, invvol=False), "K=8 eqw")
    print("   slip12:", r.get("slip12"), "\n   oos12:", r.get("oos12"))
    print("\n-- permutation null (best config K=8 sig=72h) --")
    print("  ", perm_null(build_book, 8, 72, n_iter=300))
