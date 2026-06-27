"""W-C2 engulf_leg_decomp — which leg is real, which is beta?

Split C9 into long-bullish-engulf-ONLY and short-bearish-engulf-ONLY. Score EACH leg
as EXCESS over a matched SAME-SIDE null:
  long leg  vs random-LONG  (and vs bigbar-LONG continuation)
  short leg vs random-SHORT (and vs bigbar-SHORT continuation)
The −44% tape flatters shorts, so a raw +EV short means nothing until it beats random-short.
Spec from W-C1: hz=1, classic full-body engulf br>=1.0, no gap, no vol filter, stop wide.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

HORIZON = 1
STOP = 0.40


def engulf(cd, i):
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def realize(entry, side, fwd, stop=STOP, horizon=HORIZON):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def leg_trades(series, want_side):
    out = []
    for c, cd in series.items():
        last = -999
        for i in range(2, len(cd) - HORIZON - 2):
            sig = engulf(cd, i)
            if sig == 0 or i - last < HORIZON:
                continue
            side = "long" if sig == 1 else "short"
            if side != want_side:
                continue
            last = i
            out.append({"t": cd[i + 1][A.T], "side": side,
                        "ret": realize(cd[i + 1][A.O], side, cd[i + 2:])})
    return out


def same_side_pool(d, side, mode, n=6000, seed=0):
    """mode='random': any bar, fixed `side`. mode='bigbar': enter `side` only when bar i
    is a same-direction range-expansion bar (body_i>body_prev) — strict continuation null."""
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 25:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(3, len(cd) - HORIZON - 2)
        if mode == "bigbar":
            o, c = cd[i][A.O], cd[i][A.C]
            if c == o:
                continue
            bar_side = "long" if c > o else "short"
            if bar_side != side:
                continue
            bcur = abs(c - o)
            bprev = abs(cd[i - 1][A.C] - cd[i - 1][A.O])
            if bprev <= 0 or bcur <= bprev:
                continue
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:]))
    return pool


def main():
    d = A.load_dataset()
    series = {c: A.candles(d, c, "1d") for c in d["coins"]
              if len(A.candles(d, c, "1d")) >= 60}
    for side in ("long", "short"):
        tr = leg_trades(series, side)
        s = A.summarize(tr)
        grp = [t["ret"] for t in tr]
        print(f"\n===== {side.upper()} leg (bullish-engulf longs / bearish-engulf shorts) =====")
        print(f"  n={len(tr)}  ev0={s['slip0']['mean_ret_pct']}  ev12={s['slip12']['mean_ret_pct']}"
              f"  ev25={s['slip25']['mean_ret_pct']}  ev50={s['slip50']['mean_ret_pct']}"
              f"  win={s['slip12']['win_rate']}")
        print(f"  OOS h1={s['oos_12bps']['first_half_mean_pct']}  h2={s['oos_12bps']['second_half_mean_pct']}")
        for mode in ("random", "bigbar"):
            pool = same_side_pool(d, side, mode)
            r = mc_null.shuffle_label_p(grp, pool, n_iter=8000, seed=1)
            print(f"  vs {mode:<7} same-side null: obs={r['obs_mean']:+.5f} null={r['null_mean']:+.5f}"
                  f" excess={r['excess']:+.5f} z={r['z']} p={r['p_one_sided']}")


if __name__ == "__main__":
    main()
