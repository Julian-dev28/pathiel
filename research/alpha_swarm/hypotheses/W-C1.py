"""W-C1 engulf_spec — pin C9 into a shadow-wire spec.

Search the engulf definition (body-ratio threshold, prior-overlap strictness, optional
gap), the hold {1,2}d, and a volume-confirm filter. Output the exact entry rule + MC
p-value (vs the strict BIGBAR null) at the chosen spec. Cross-sectional signed framing:
per-coin signed trade ret = side*fwd_ret, market-neutral read = mean of signed returns.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null


def body(cd, i):
    return abs(cd[i][A.C] - cd[i][A.O])


def engulf(cd, i, body_ratio=1.0, require_full=True, require_gap=False):
    """+1 bullish / -1 bearish / 0 none. Variants:
    require_full: o<=prev_close & c>=prev_open (classic full-body engulf).
                  if False: only require close beyond prior open (looser).
    body_ratio:   body_i / body_prev >= body_ratio.
    require_gap:  open gaps against prior close (o<prev_close for bull, o>prev_close bear).
    """
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    bprev = abs(pc - po)
    bcur = abs(c - o)
    if bprev <= 0 or bcur <= 0:
        return 0
    if bcur / bprev < body_ratio:
        return 0
    # bullish: green now, red prior
    if c > o and pc < po:
        ok = (c >= po) if not require_full else (o <= pc and c >= po)
        if require_gap and not (o < pc):
            ok = False
        if ok:
            return 1
    # bearish: red now, green prior
    if c < o and pc > po:
        ok = (c <= po) if not require_full else (o >= pc and c <= po)
        if require_gap and not (o > pc):
            ok = False
        if ok:
            return -1
    return 0


def vol_confirm(cd, i, mode):
    if mode == "none":
        return True
    v = cd[i][A.V]
    if mode == "gt_prev":
        return v > cd[i - 1][A.V]
    if mode == "gt_ma5":
        prev = [cd[j][A.V] for j in range(max(0, i - 5), i)]
        return bool(prev) and v > sum(prev) / len(prev)
    return True


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def build_trades(series, body_ratio, require_full, require_gap, vmode, stop, horizon):
    trades = []
    for c, cd in series.items():
        last = -999
        for i in range(2, len(cd) - horizon - 2):
            sig = engulf(cd, i, body_ratio, require_full, require_gap)
            if sig == 0 or i - last < horizon:
                continue
            if not vol_confirm(cd, i, vmode):
                continue
            last = i
            side = "long" if sig == 1 else "short"
            trades.append({"t": cd[i + 1][A.T], "side": side,
                           "ret": realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon)})
    return trades


def bigbar_pool(d, stop, horizon, body_ratio, n=5000, seed=0):
    """Strict control: enter in the direction of a range-expansion / big-body bar of the
    same body-ratio magnitude, but WITHOUT the engulf-of-opposite-body condition."""
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 20:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(3, len(cd) - horizon - 2)
        o, c = cd[i][A.O], cd[i][A.C]
        if c == o:
            continue
        bcur = abs(c - o)
        bprev = abs(cd[i - 1][A.C] - cd[i - 1][A.O])
        if bprev <= 0 or bcur / bprev < body_ratio:
            continue
        side = "long" if c > o else "short"
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon))
    return pool


def main():
    d = A.load_dataset()
    series = {c: A.candles(d, c, "1d") for c in d["coins"]
              if len(A.candles(d, c, "1d")) >= 60}
    print(f"coins with >=60d = {len(series)}")

    # ---- spec grid (fix stop family per C9: report 0.08 + 0.40) ----
    rows = []
    for horizon in (1, 2):
        for body_ratio in (1.0, 1.25, 1.5):
            for require_full in (True, False):
                for require_gap in (False, True):
                    for vmode in ("none", "gt_prev", "gt_ma5"):
                        for stop in (0.08, 0.40):
                            tr = build_trades(series, body_ratio, require_full,
                                              require_gap, vmode, stop, horizon)
                            if len(tr) < 40:
                                continue
                            s = A.summarize(tr)
                            h1 = s["oos_12bps"]["first_half_mean_pct"]
                            h2 = s["oos_12bps"]["second_half_mean_pct"]
                            rows.append({
                                "hz": horizon, "br": body_ratio, "full": require_full,
                                "gap": require_gap, "vol": vmode, "stop": stop,
                                "n": len(tr), "ev12": s["slip12"]["mean_ret_pct"],
                                "ev25": s["slip25"]["mean_ret_pct"],
                                "ev50": s["slip50"]["mean_ret_pct"],
                                "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2,
                                "trades": tr,
                            })
    # robust = both halves + and ev25>0
    def robust(r):
        return r["h1"] and r["h2"] and r["h1"] > 0 and r["h2"] > 0 and r["ev25"] > 0
    rows.sort(key=lambda r: r["ev25"], reverse=True)
    print("\n=== all spec cells by EV@25bps (robust marked *) ===")
    print("hz br   full gap   vol      stop n     ev12   ev25   ev50  win   h1     h2")
    for r in rows:
        mk = "*" if robust(r) else " "
        print(f"{mk}{r['hz']:<2} {r['br']:<4} {str(r['full'])[0]}    "
              f"{str(r['gap'])[0]}     {r['vol']:<8} {r['stop']:<4} {r['n']:<5} "
              f"{r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")

    # ---- pick the recommended SHADOW spec: prefer simplest robust cell with high min(h1,h2)
    cands = [r for r in rows if robust(r)]
    # simplicity preference: br=1.0, full=True, gap=False, vol=none, hz=1, wide stop
    def simplicity(r):
        return (r["br"] == 1.0) + (r["full"] is True) + (not r["gap"]) + (r["vol"] == "none") + (r["hz"] == 1)
    if cands:
        cands.sort(key=lambda r: (round(min(r["h1"], r["h2"]), 3), simplicity(r)), reverse=True)
        best = cands[0]
        # also show the simplest-clean baseline (the C9 original spec) for reference
        base = next((r for r in rows if r["hz"] == 1 and r["br"] == 1.0 and r["full"]
                     and not r["gap"] and r["vol"] == "none" and r["stop"] == 0.40), None)
        print("\n=== RECOMMENDED robust spec ===")
        for k in ("hz", "br", "full", "gap", "vol", "stop", "n", "ev12", "ev25", "ev50", "win", "h1", "h2"):
            print(f"  {k}: {best[k]}")
        grp = [t["ret"] for t in best["trades"]]
        pool = bigbar_pool(d, best["stop"], best["hz"], best["br"])
        print("  MC vs BIGBAR null:", mc_null.shuffle_label_p(grp, pool, n_iter=8000, seed=1))
        if base:
            print("\n=== C9 ORIGINAL baseline (hz1 br1.0 full gap-F vol-none stop.40) ===")
            for k in ("n", "ev12", "ev25", "ev50", "win", "h1", "h2"):
                print(f"  {k}: {base[k]}")
            gb = [t["ret"] for t in base["trades"]]
            pb = bigbar_pool(d, base["stop"], base["hz"], base["br"])
            print("  MC vs BIGBAR null:", mc_null.shuffle_label_p(gb, pb, n_iter=8000, seed=1))
    else:
        print("\nNO robust spec cell found.")


if __name__ == "__main__":
    main()
