"""C12 entropy_predictability_filter — permutation-entropy meta-filter on a Tier-1 edge.

Base edge = live extreme_fade: daily close-to-close return < -12% -> LONG, fill i+1 open,
20% stop, 3d horizon. Filter: at the decision bar, compute permutation entropy (PE, m=3,
delay=1) of the coin's last L daily returns (lookahead-safe). Hypothesis: only take the fade
on LOW-PE (predictable/structured) coins -> cuts duds. Measure EV/win/OOS of the base vs the
low-PE and high-PE subsets. A real meta-filter must LIFT EV and keep both OOS halves +.
"""
from __future__ import annotations
import math, itertools
import alpha_lib as A

PERMS = {p: k for k, p in enumerate(itertools.permutations(range(3)))}


def perm_entropy(returns, m=3):
    """normalized permutation entropy of a return window."""
    if len(returns) < m + 1:
        return None
    counts = [0] * math.factorial(m)
    n = 0
    for i in range(len(returns) - m + 1):
        window = returns[i:i + m]
        order = tuple(sorted(range(m), key=lambda k: window[k]))
        counts[PERMS[order]] += 1
        n += 1
    if n == 0:
        return None
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log(p)
    return ent / math.log(math.factorial(m))


def realize(entry, fwd, stop=0.20, horizon=3):
    return A.sweep_stop(entry, "long", fwd, [stop], horizon)[stop]


def run():
    d = A.load_dataset()
    L = 30
    trades = []
    for c in d["coins"]:
        cd = A.candles(d, c, "1d")
        if len(cd) < L + 10:
            continue
        rets = [A.pct(cd[k - 1][A.C], cd[k][A.C]) for k in range(1, len(cd))]  # rets[k-1] is ret at bar k
        last = -999
        for i in range(L + 1, len(cd) - 5):
            r1 = A.pct(cd[i - 1][A.C], cd[i][A.C])
            if r1 >= -0.12 or i - last < 3:
                continue
            last = i
            # PE on returns up to and including bar i (lookahead-safe): rets indices [i-L .. i-1]
            window = rets[i - L:i]
            pe = perm_entropy(window)
            if pe is None:
                continue
            entry = cd[i + 1][A.O]
            ret = realize(entry, cd[i + 2:])
            trades.append({"t": cd[i + 1][A.T], "ret": ret, "pe": pe})
    if len(trades) < 20:
        print("too few base trades", len(trades))
        return
    pes = sorted(t["pe"] for t in trades)
    med = pes[len(pes) // 2]
    lo_t = pes[len(pes) // 3]
    hi_t = pes[2 * len(pes) // 3]

    def show(name, sub):
        s = A.summarize(sub)
        print(f"{name:<22} n={s['n']:<4} ev12={s['slip12']['mean_ret_pct']:<7} "
              f"ev25={s['slip25']['mean_ret_pct']:<7} win={s['slip12']['win_rate']:<6} "
              f"h1={s['oos_12bps']['first_half_mean_pct']} h2={s['oos_12bps']['second_half_mean_pct']}")

    print(f"base extreme_fade trades n={len(trades)}, PE median={med:.3f}")
    show("BASE (all)", trades)
    show("LOW-PE (<=median)", [t for t in trades if t["pe"] <= med])
    show("HIGH-PE (>median)", [t for t in trades if t["pe"] > med])
    show("LOW-PE tercile", [t for t in trades if t["pe"] <= lo_t])
    show("HIGH-PE tercile", [t for t in trades if t["pe"] >= hi_t])

    # permutation test: is low-half minus high-half EV gap beyond chance?
    import random, statistics
    lo = [t["ret"] for t in trades if t["pe"] <= med]
    hi = [t["ret"] for t in trades if t["pe"] > med]
    obs = statistics.mean(lo) - statistics.mean(hi)
    allr = [t["ret"] for t in trades]
    k = len(lo)
    rng = random.Random(0)
    ge = 0
    NIT = 10000
    for _ in range(NIT):
        rng.shuffle(allr)
        diff = statistics.mean(allr[:k]) - statistics.mean(allr[k:])
        if diff >= obs:
            ge += 1
    print(f"\nlow-vs-high EV gap obs={obs*100:.3f}%  permutation p_one_sided={(ge+1)/(NIT+1):.4f}")


if __name__ == "__main__":
    run()
