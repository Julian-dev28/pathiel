"""W-C4 candle_pattern_family — is engulf special (overfit flag) or is the whole 2-bar
reversal family alive (real reversal effect)?

Test, daily cross-sectional, hz=1, stop 0.40, same framing as C9:
  engulf, harami, piercing/dark-cloud, hammer/shooting-star.
For each: symmetric signed EV + OOS halves + MC excess vs random-side null, AND (per W-C2
where the edge was the SHORT leg) the BEARISH/short leg excess vs a matched random-SHORT
null and the BULLISH/long leg vs random-LONG. If only engulf's short leg clears, that's an
overfitting flag; if the bearish-reversal family clears, it's a real effect.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

HZ, STOP = 1, 0.40


def realize(entry, side, fwd):
    return A.sweep_stop(entry, side, fwd, [STOP], HZ)[STOP]


def body(cd, i):
    return abs(cd[i][A.C] - cd[i][A.O])


def engulf(cd, i):
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def harami(cd, i):
    """Small body inside the prior LARGE opposite-color body."""
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    bprev, bcur = abs(pc - po), abs(c - o)
    if bprev <= 0 or bcur >= 0.6 * bprev:
        return 0
    hi_p, lo_p = max(po, pc), min(po, pc)
    inside = (max(o, c) <= hi_p and min(o, c) >= lo_p)
    if not inside:
        return 0
    if pc < po and c > o:        # prior red, current green -> bullish harami
        return 1
    if pc > po and c < o:        # prior green, current red -> bearish harami
        return -1
    return 0


def piercing(cd, i):
    """Piercing line (bull) / dark-cloud cover (bear)."""
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    mid = (po + pc) / 2.0
    if pc < po and c > o and o < pc and c > mid and c < po:     # bull piercing
        return 1
    if pc > po and c < o and o > pc and c < mid and c > po:     # bear dark-cloud
        return -1
    return 0


def hammer(cd, i):
    """Hammer (long lower wick -> bull) / shooting-star (long upper wick -> bear). 1-bar."""
    o, c, h, l = cd[i][A.O], cd[i][A.C], cd[i][A.H], cd[i][A.L]
    rng = h - l
    if rng <= 0:
        return 0
    b = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if b > 0.4 * rng:            # need a small body
        return 0
    if lower >= 2 * b and lower > 2 * upper:   # hammer
        return 1
    if upper >= 2 * b and upper > 2 * lower:   # shooting star
        return -1
    return 0


PATTERNS = {"engulf": engulf, "harami": harami, "piercing/cloud": piercing,
            "hammer/star": hammer}


def pattern_trades(series, fn, want=None):
    out = []
    for c, cd in series.items():
        last = -999
        for i in range(2, len(cd) - HZ - 2):
            sig = fn(cd, i)
            if sig == 0 or i - last < HZ:
                continue
            side = "long" if sig == 1 else "short"
            if want and side != want:
                continue
            last = i
            out.append({"t": cd[i + 1][A.T], "side": side,
                        "ret": realize(cd[i + 1][A.O], side, cd[i + 2:])})
    return out


def side_pool(d, side, n=6000, seed=0):
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 25:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(3, len(cd) - HZ - 2)
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:]))
    return pool


def main():
    d = A.load_dataset()
    series = {c: A.candles(d, c, "1d") for c in d["coins"]
              if len(A.candles(d, c, "1d")) >= 60}
    long_pool = side_pool(d, "long")
    short_pool = side_pool(d, "short")
    print("pattern         leg     n    ev0     ev25   win   h1      h2     excess(vs same-side rand)  z     p")
    for name, fn in PATTERNS.items():
        for leg, want, pool in (("SYM", None, None), ("LONG", "long", long_pool),
                                 ("SHORT", "short", short_pool)):
            tr = pattern_trades(series, fn, want)
            if len(tr) < 30:
                print(f"{name:<15} {leg:<6} n={len(tr)} (thin)")
                continue
            s = A.summarize(tr)
            h1 = s["oos_12bps"]["first_half_mean_pct"]; h2 = s["oos_12bps"]["second_half_mean_pct"]
            grp = [t["ret"] for t in tr]
            if leg == "SYM":
                ex = z = p = None
            else:
                r = mc_null.shuffle_label_p(grp, pool, n_iter=6000, seed=1)
                ex, z, p = r["excess"], r["z"], r["p_one_sided"]
            exs = f"{ex*100:+.3f}%" if ex is not None else "   -   "
            print(f"{name:<15} {leg:<6} {len(tr):<4} {s['slip0']['mean_ret_pct']:<7} "
                  f"{s['slip25']['mean_ret_pct']:<6} {s['slip12']['win_rate']:<5} "
                  f"{h1}  {h2}  {exs:<10} {z if z is not None else '':<6} {p if p is not None else ''}")
        print()


if __name__ == "__main__":
    main()
