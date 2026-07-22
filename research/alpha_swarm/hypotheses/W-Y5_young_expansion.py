#!/usr/bin/env python
"""W-Y5 Tasks 2+4: crypto young-listing short backtest + overlay EV ranking.

Pre-registered design (before results):

BASE (per asset class: crypto / xyz_equity / hip3_other):
  Population: coins LISTED IN-SAMPLE (first bar inside the 400-bar fetch
  window, so the whole young life is visible). Signal day i = daily-bar age
  in [2, 59] with day dollar volume >= $250k (the live young-book floor).
  Action: SHORT at open(i+1), hold {1,2,3}d, stop {6%, 15%} on the daily H/L
  path. Costs 25 bps/side headline (40 stress). One open episode per coin.

BASE benchmark (is "young" the edge, or just tape?): matched SAME-CALENDAR-DAY
  mature baseline — for every episode day, a random same-class coin with age
  >= 60 and dvol >= $250k shorted with identical mechanics; 2000 portfolio
  draws give mc_p = P(mature-baseline mean >= young mean) and the excess.

AGE BANDS: EV by age {2-9, 10-19, 20-39, 40-59} plus 60-89 (beyond-floor
  sanity) — answers "does crypto need a different age threshold".

DVOL floors {250k, 1M, 3M} on the crypto BASE — the liquidity-threshold
  question.

OVERLAYS (on the h=1 stop=6% BASE of each class, each graded three ways:
  subset EV, label-permutation lift p vs BASE, and same-coin random-young-day
  entry null):
  O1  pumped:   day-i return >= +5% (and >= +8% variant)
  O1b notcrash: day-i return > -8% (H2-short refuted on crash days — exclude)
  O2  funding:  prior-day (day i) summed hourly funding > 0 (crowded long);
                needs W-Y5_cache_funding.json, skipped gracefully if absent
  O4  rvol:     dvol(i)/mean(dvol(i-7..i-1)) < 0.7 (low) and >= 2.0 (high)
  O3  news:     UNTESTABLE historically (GDELT blind on these names — cache
                W-N_cache_gdelt.json returns empty for small caps; the
                news_surge_short ledger only starts 2026-07-20). Reported as
                such; forward path = join young book vs news book coin-days.

Funding-net EV additionally reported wherever funding data exists: a SHORT
earns the (positive) funding sum over its hold window.

Data: W-Y5_cache_daily.json (+ optional W-Y5_cache_funding.json). No network.
"""
import importlib.util
import json
import os
import random
import statistics as st
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("wy5lib", os.path.join(HERE, "W-Y5_lib.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

SEED = 20260722
DVOL_FLOOR = 250_000.0
HOLDS = (1, 2, 3)
STOPS = (0.06, 0.15)
AGE_BANDS = ((2, 9), (10, 19), (20, 39), (40, 59), (60, 89))

coins = L.load_daily()
FUND = L.load_funding()
DAY_MS = L.DAY_MS


def dvol(rows, i):
    return rows[i][5] * rows[i][4]


def day_ret(rows, i):
    pc = rows[i - 1][4]
    return rows[i][4] / pc - 1 if pc > 0 else None


def rvol(rows, i):
    if i < 8:
        return None
    base = st.mean(dvol(rows, j) for j in range(i - 7, i))
    return dvol(rows, i) / base if base > 0 else None


def fund_prior_day(coin, rows, i):
    """Summed hourly funding over day i (known before the open(i+1) entry)."""
    fr = FUND.get(coin)
    if not fr:
        return None
    return L.funding_sum(fr, rows[i][0], rows[i][0] + DAY_MS)


def fund_hold(coin, rows, i, hold):
    """Funding earned by the short over the hold window (entry open(i+1))."""
    fr = FUND.get(coin)
    if not fr:
        return None
    return L.funding_sum(fr, rows[i + 1][0], rows[i + 1][0] + hold * DAY_MS)


# ---------------------------------------------------------------- populations
classes = {}
for coin, rows in coins.items():
    cls = L.asset_class(coin)
    classes.setdefault(cls, {})[coin] = rows

WS = L.window_start(coins)
in_sample = {cls: {c: r for c, r in cc.items() if L.listed_in_sample(r, WS)}
             for cls, cc in classes.items()}


def base_signals(cc, lo=2, hi=59, floor=DVOL_FLOOR):
    sig = []
    for coin, rows in cc.items():
        for i in range(max(lo, 1), min(hi, len(rows) - 2) + 1):
            if dvol(rows, i) >= floor:
                sig.append((coin, i))
    return sig


def mature_pool_by_day(cls, floor=DVOL_FLOOR):
    """{utc_day_ts -> [(coin, i)]} of same-class mature (age>=60) coin-days
    above the dvol floor — uses ALL cached coins of the class (not just
    in-sample listings)."""
    pool = {}
    for coin, rows in classes.get(cls, {}).items():
        for i in range(60, len(rows) - 2):
            if dvol(rows, i) >= floor:
                pool.setdefault(rows[i][0], []).append((coin, i))
    return pool


def matched_mature_null(cls, trades, hold, stop, rng, iters=L.MC_ITERS):
    """P(mature same-day baseline mean >= young mean), short side."""
    if not trades:
        return None, None, 0
    mp = mature_pool_by_day(cls)
    days = []
    for coin, i, _ in trades:
        t = in_sample[cls][coin][i][0]
        if mp.get(t):
            days.append(t)
    if len(days) < len(trades) * 0.7 or not days:
        return None, None, len(days)
    real = st.mean(L.net(g) for _, _, g in trades)
    ge = n_eff = 0
    means = []
    for _ in range(iters):
        vals = []
        for t in days:
            c, i = rng.choice(mp[t])
            g = L.simulate(classes[cls][c], i, -1, hold, stop)
            if g is not None:
                vals.append(L.net(g))
        if not vals:
            continue
        n_eff += 1
        m = st.mean(vals)
        means.append(m)
        if m >= real:
            ge += 1
    if not n_eff:
        return None, None, len(days)
    return (ge + 1) / (n_eff + 1), real - st.mean(means), len(days)


def young_pools(cc, floor=DVOL_FLOOR):
    return {c: [i for i in range(2, min(59, len(r) - 2) + 1)
                if dvol(r, i) >= floor]
            for c, r in cc.items()}


def listing_meta(cc):
    lt = {c: r[0][0] for c, r in cc.items()}
    med = sorted(lt.values())[len(lt) // 2] if lt else 0
    return lt, med


def run_class(cls, out):
    cc = in_sample.get(cls, {})
    if not cc:
        return
    rng = random.Random(SEED)
    lt, med = listing_meta(cc)
    n_coins = len(cc)
    print(f"\n=== {cls}: {n_coins} in-sample listings "
          f"(of {len(classes[cls])} cached) ===")

    # ---- BASE sweep
    sig = base_signals(cc)
    print(f"BASE young coin-days (age 2-59, dvol>=$250k): {len(sig)}")
    for hold in HOLDS:
        for stop in STOPS:
            trades = L.run_cell(cc, sig, -1, hold, stop)
            d = L.describe(trades, cc, lt, med)
            mm_p, mm_xs, mm_n = matched_mature_null(cls, trades, hold, stop, rng)
            name = f"BASE short h={hold} s={int(stop*100)}%"
            print(L.fmt_cell(name, d, mm_p, mm_xs) +
                  (f" [{mm_n} day-matched]" if mm_n else ""))
            out[f"{cls}|{name}"] = {**d, "mc_p_mature": mm_p,
                                    "excess_vs_mature": mm_xs}

    # ---- age bands (h=1 s=6%)
    print("-- age bands (h=1, s=6%):")
    for lo, hi in AGE_BANDS:
        sigb = base_signals(cc, lo, hi)
        trades = L.run_cell(cc, sigb, -1, 1, 0.06)
        d = L.describe(trades, cc, lt, med)
        print(L.fmt_cell(f"  age {lo}-{hi}", d))
        out[f"{cls}|age_{lo}_{hi}"] = d

    # ---- dvol floors (h=1 s=6%)
    print("-- dvol floors (h=1, s=6%):")
    for floor in (250_000.0, 1_000_000.0, 3_000_000.0):
        sigf = base_signals(cc, floor=floor)
        trades = L.run_cell(cc, sigf, -1, 1, 0.06)
        d = L.describe(trades, cc, lt, med)
        print(L.fmt_cell(f"  dvol>=${floor/1e6:.2f}M", d))
        out[f"{cls}|floor_{int(floor/1000)}k"] = d

    # ---- overlays on BASE h=1 s=6%
    hold, stop = 1, 0.06
    base_trades = L.run_cell(cc, sig, -1, hold, stop)
    pools = young_pools(cc)
    feats = []
    for coin, i, g in base_trades:
        rows = cc[coin]
        feats.append({
            "ret0": day_ret(rows, i),
            "rvol": rvol(rows, i),
            "fund": fund_prior_day(coin, rows, i),
            "fhold": fund_hold(coin, rows, i, hold),
        })
    fund_cov = sum(1 for f in feats if f["fund"] is not None)
    print(f"-- overlays on BASE h=1 s=6% (n={len(base_trades)}; funding "
          f"coverage {fund_cov}/{len(feats)}):")

    overlays = [
        ("O1 pumped ret0>=+5%", lambda f: f["ret0"] is not None and f["ret0"] >= 0.05),
        ("O1 pumped ret0>=+8%", lambda f: f["ret0"] is not None and f["ret0"] >= 0.08),
        ("O1b not-crashed ret0>-8%", lambda f: f["ret0"] is not None and f["ret0"] > -0.08),
        ("O2 funding day-i > 0", lambda f: f["fund"] is not None and f["fund"] > 0),
        ("O2b funding day-i <= 0", lambda f: f["fund"] is not None and f["fund"] <= 0),
        ("O4 low rvol < 0.7", lambda f: f["rvol"] is not None and f["rvol"] < 0.7),
        ("O4b high rvol >= 2", lambda f: f["rvol"] is not None and f["rvol"] >= 2.0),
    ]
    for name, fn in overlays:
        mask = [fn(f) for f in feats]
        sub = [t for t, m in zip(base_trades, mask) if m]
        d = L.describe(sub, cc, lt, med)
        if d["n"] == 0:
            print(f"  {name:<32} n=0")
            out[f"{cls}|{name}"] = d
            continue
        lift_p = L.perm_lift_p(base_trades, mask, rng)
        mc_p, xs = L.mc_entry_null(cc, sub, pools, -1, hold, stop, rng)
        # funding-net EV where funding data exists on the subset
        fn_ev = [L.net(g) + f["fhold"] for (c, i, g), f, m
                 in zip(base_trades, feats, mask)
                 if m and f["fhold"] is not None]
        extra = (f" fundnet={st.mean(fn_ev)*100:+.2f}%({len(fn_ev)})"
                 if fn_ev else "")
        print("  " + L.fmt_cell(name, d, mc_p, xs) +
              f" liftp={lift_p if lift_p is None else format(lift_p, '.4f')}"
              + extra)
        out[f"{cls}|{name}"] = {**d, "mc_p": mc_p, "excess": xs,
                                "lift_p": lift_p,
                                "fund_net_ev": st.mean(fn_ev) if fn_ev else None,
                                "fund_net_n": len(fn_ev)}

    # BASE funding-net (whole base where covered)
    fn_ev = [L.net(g) + f["fhold"] for (c, i, g), f in zip(base_trades, feats)
             if f["fhold"] is not None]
    if fn_ev:
        print(f"  BASE funding-net ev25: {st.mean(fn_ev)*100:+.2f}% "
              f"(n={len(fn_ev)}) vs price-only "
              f"{st.mean(L.net(g) for _,_,g in base_trades)*100:+.2f}%")


def main():
    out = {}
    print(f"cached coins: {len(coins)}; classes: "
          f"{ {k: len(v) for k, v in classes.items()} }")
    print(f"in-sample listings: { {k: len(v) for k, v in in_sample.items()} }")
    for cls in ("xyz_equity", "crypto", "hip3_other"):
        run_class(cls, out)
    with open(os.path.join(HERE, "W-Y5_expansion_results.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\nresults -> W-Y5_expansion_results.json")


if __name__ == "__main__":
    main()
