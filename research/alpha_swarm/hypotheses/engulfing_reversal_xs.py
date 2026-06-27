"""C9 engulfing_reversal_xs — engulfing candle as a cross-sectional signed signal.

Bullish engulfing (green bar whose body engulfs the prior red body) -> long;
bearish engulfing -> short. Daily, decide on bar i close, fill i+1 open, hold horizon.
Per-coin signed trade ret = side * forward return. Market-neutral read = mean of signed
returns. Null = same trades with RANDOM side (mc_null) -> isolates the candle's info from
the tape. Stop sweep (reversal). High prior of refute.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def engulf(cd, i):
    """+1 bullish, -1 bearish, 0 none, using bars i-1 and i (decided at i close)."""
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def build_pool(d, stop, horizon, mode="random", n=4000, seed=0):
    """mode='random': random side. mode='directional': side = color of bar i
    (continuation of any candle). mode='bigbar': same but only top-tercile-range bars."""
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 10:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(3, len(cd) - horizon - 2)
        if mode == "random":
            side = "long" if rng.random() < 0.5 else "short"
        else:
            o, c = cd[i][A.O], cd[i][A.C]
            if c == o:
                continue
            if mode == "bigbar":
                rng_i = (cd[i][A.H] - cd[i][A.L]) / cd[i][A.C]
                rng_prev = (cd[i - 1][A.H] - cd[i - 1][A.L]) / cd[i - 1][A.C]
                if rng_i <= rng_prev:   # require a range-expansion bar like an engulf
                    continue
            side = "long" if c > o else "short"
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    series = {c: A.candles(d, c, "1d") for c in d["coins"] if len(A.candles(d, c, "1d")) >= 60}
    results, best = [], None
    for horizon in (1, 3, 5):
        for stop in STOPS:
            trades = []
            for c, cd in series.items():
                last = -999
                for i in range(2, len(cd) - horizon - 2):
                    sig = engulf(cd, i)
                    if sig == 0 or i - last < horizon:
                        continue
                    last = i
                    side = "long" if sig == 1 else "short"
                    trades.append({"t": cd[i + 1][A.T], "side": side,
                                   "ret": realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon)})
            if len(trades) < 40:
                continue
            s = A.summarize(trades)
            h1 = s["oos_12bps"]["first_half_mean_pct"]
            h2 = s["oos_12bps"]["second_half_mean_pct"]
            rec = {"horizon": horizon, "stop": stop, "n": len(trades), "trades": trades,
                   "ev12": s["slip12"]["mean_ret_pct"], "ev25": s["slip25"]["mean_ret_pct"],
                   "ev50": s["slip50"]["mean_ret_pct"], "win": s["slip12"]["win_rate"],
                   "h1": h1, "h2": h2}
            results.append(rec)
            robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
            score = min(h1, h2) if (h1 and h2) else -99
            if robust and (best is None or score > best.get("score", -99)):
                rec["score"] = score
                best = rec
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== all cells by EV@25bps ===")
    print("hz stop  n     ev12   ev25   ev50  win   h1     h2")
    for r in results:
        print(f"{r['horizon']:<2} {r['stop']:<5} {r['n']:<5} {r['ev12']:<6} {r['ev25']:<6} "
              f"{r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
    cell = best or (results[0] if results else None)
    if cell:
        tag = "BEST robust" if best else "top-EV (no robust cell)"
        print(f"\n{tag}:", {k: cell[k] for k in ("horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
        grp = [t["ret"] for t in cell["trades"]]
        for mode in ("random", "directional", "bigbar"):
            pool = build_pool(d, cell["stop"], cell["horizon"], mode=mode)
            print(f"mc_null vs {mode:<11}:", mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1))


if __name__ == "__main__":
    run()
