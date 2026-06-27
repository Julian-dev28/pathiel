"""Fast permutation null for D1: precompute per-day (coin -> sig, price_ret, fund, invvol)
ONCE, then shuffle the long/short assignment many times. Compares the REAL funding-ranked
neutral inv-vol book's mean daily gross return vs random neutral inv-vol books."""
from __future__ import annotations
import statistics, random
import D1_funding_carry as m

# precompute table: list over days of dict coin->(sig168, sig72, sig24, pr, fd, invvol)
DAY = m.DAY
days = sorted(m.btc_days)
TABLE = []
for t in days:
    row = {}
    for c in m.COINS:
        pr = m.price_ret_day(c, t); fd = m.funding_day(c, t)
        v = m.realized_vol(c, t, 20)
        if pr is None or fd is None or v is None:
            continue
        row[c] = {"pr": pr, "fd": fd, "iv": 1.0 / v,
                  "s24": m.trailing_fund(c, t, 24),
                  "s72": m.trailing_fund(c, t, 72),
                  "s168": m.trailing_fund(c, t, 168)}
    TABLE.append(row)

def book_gross(K, sigkey, perm=None, rng=None):
    """Daily gross series. If perm: random K-long/K-short; else funding-ranked."""
    series = []
    for row in TABLE:
        names = [c for c in row if row[c][sigkey] is not None]
        if len(names) < 2 * K:
            continue
        if perm:
            rng.shuffle(names)
            longs, shorts = names[:K], names[K:2 * K]
        else:
            ranked = sorted(names, key=lambda c: row[c][sigkey])
            longs, shorts = ranked[:K], ranked[-K:]
        w = {}
        for grp, sign in [(longs, +1.0), (shorts, -1.0)]:
            s = sum(row[c]["iv"] for c in grp)
            for c in grp:
                w[c] = sign * 0.5 * row[c]["iv"] / s
        gross = sum(w[c] * (row[c]["pr"] - row[c]["fd"]) for c in w)
        series.append(gross)
    return series

def run(K, sigkey, n_iter=3000, seed=1):
    real = book_gross(K, sigkey)
    rm = statistics.mean(real)
    rng = random.Random(seed); ge = 0; nm = []
    for _ in range(n_iter):
        s = book_gross(K, sigkey, perm=True, rng=rng)
        mu = statistics.mean(s); nm.append(mu)
        if mu >= rm: ge += 1
    null_mu = statistics.mean(nm); null_sd = statistics.pstdev(nm) + 1e-12
    return {"K": K, "sig": sigkey, "real_bps": round(1e4 * rm, 3),
            "null_bps": round(1e4 * null_mu, 3),
            "z": round((rm - null_mu) / null_sd, 2),
            "p": round((ge + 1) / (n_iter + 1), 4)}

if __name__ == "__main__":
    for K in [5, 8, 10]:
        print(run(K, "s168", n_iter=3000))
    print("-- compare signal horizons K=8 --")
    for sk in ["s24", "s72", "s168"]:
        print(run(8, sk, n_iter=3000))
