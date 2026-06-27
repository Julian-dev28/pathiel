"""W-C5 entropy_on_engulf — bolt C12 permutation-entropy filter onto the engulf SHORT edge
(the real leg per W-C2/W-C4). Does restricting to LOW-PE (predictable) names lift EV / cut
duds? Measure EV + dud-rate + OOS halves per PE bucket, plus the low-vs-high gap permutation
p and the short-leg excess vs same-side null within the low-PE bucket.
"""
from __future__ import annotations
import math, itertools, random, statistics
import alpha_lib as A
import mc_null

HZ, STOP, L = 1, 0.40, 30
PERMS = {p: k for k, p in enumerate(itertools.permutations(range(3)))}


def perm_entropy(returns, m=3):
    if len(returns) < m + 1:
        return None
    counts = [0] * math.factorial(m); n = 0
    for i in range(len(returns) - m + 1):
        w = returns[i:i + m]
        order = tuple(sorted(range(m), key=lambda k: w[k]))
        counts[PERMS[order]] += 1; n += 1
    if n == 0:
        return None
    ent = 0.0
    for c in counts:
        if c:
            p = c / n; ent -= p * math.log(p)
    return ent / math.log(math.factorial(m))


def engulf(cd, i):
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def realize(entry, side, fwd):
    return A.sweep_stop(entry, side, fwd, [STOP], HZ)[STOP]


def short_pool(d, n=6000, seed=0):
    rng = random.Random(seed); coins = d["coins"]; pool, tries = [], 0
    while len(pool) < n and tries < n * 25:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(3, len(cd) - HZ - 2)
        pool.append(realize(cd[i + 1][A.O], "short", cd[i + 2:]))
    return pool


def run():
    d = A.load_dataset()
    trades = []   # bearish-engulf SHORT trades, with PE
    for c in d["coins"]:
        cd = A.candles(d, c, "1d")
        if len(cd) < L + 10:
            continue
        rets = [A.pct(cd[k - 1][A.C], cd[k][A.C]) for k in range(1, len(cd))]
        last = -999
        for i in range(L + 1, len(cd) - HZ - 2):
            if engulf(cd, i) != -1 or i - last < HZ:
                continue
            last = i
            pe = perm_entropy(rets[i - L:i])
            if pe is None:
                continue
            trades.append({"t": cd[i + 1][A.T], "side": "short",
                           "ret": realize(cd[i + 1][A.O], "short", cd[i + 2:]), "pe": pe})
    print(f"bearish-engulf SHORT trades with PE: n={len(trades)}")
    pes = sorted(t["pe"] for t in trades)
    med = pes[len(pes) // 2]; lo_t = pes[len(pes) // 3]; hi_t = pes[2 * len(pes) // 3]

    def dud(sub):
        return round(sum(1 for t in sub if t["ret"] <= 0) / len(sub), 3) if sub else None

    def show(name, sub):
        if not sub:
            print(f"{name:<20} (empty)"); return
        s = A.summarize(sub)
        print(f"{name:<20} n={s['n']:<4} ev0={s['slip0']['mean_ret_pct']:<7} "
              f"ev25={s['slip25']['mean_ret_pct']:<7} win={s['slip12']['win_rate']:<6} "
              f"dud={dud(sub)} h1={s['oos_12bps']['first_half_mean_pct']} "
              f"h2={s['oos_12bps']['second_half_mean_pct']}")

    print(f"PE median={med:.3f}")
    show("BASE all", trades)
    show("LOW-PE <=med", [t for t in trades if t["pe"] <= med])
    show("HIGH-PE >med", [t for t in trades if t["pe"] > med])
    show("LOW-PE tercile", [t for t in trades if t["pe"] <= lo_t])
    show("HIGH-PE tercile", [t for t in trades if t["pe"] >= hi_t])

    lo = [t["ret"] for t in trades if t["pe"] <= med]
    hi = [t["ret"] for t in trades if t["pe"] > med]
    obs = statistics.mean(lo) - statistics.mean(hi)
    allr = [t["ret"] for t in trades]; k = len(lo); rng = random.Random(0); ge = 0; NIT = 10000
    for _ in range(NIT):
        rng.shuffle(allr)
        if statistics.mean(allr[:k]) - statistics.mean(allr[k:]) >= obs:
            ge += 1
    print(f"\nlow-vs-high EV gap obs={obs*100:+.3f}%  permutation p={(ge+1)/(NIT+1):.4f}")
    # excess of LOW-PE short bucket vs same-side random short
    pool = short_pool(d)
    r = mc_null.shuffle_label_p(lo, pool, n_iter=8000, seed=1)
    print("LOW-PE bucket vs random-short null:", {kk: r[kk] for kk in ("obs_mean", "null_mean", "excess", "z", "p_one_sided")})


if __name__ == "__main__":
    run()
