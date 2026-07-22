#!/usr/bin/env python3
"""W-GAP1 analysis — is the xyz 9:30-ET open-bar pop a real EXCESS edge or bull drift?

Reads W-GAP1_cache_1h.json (full xyz universe, 1h bars). PIT, no lookahead.

Open bar = the 1h bar whose ET-local start hour == 9 (covers 09:00-10:00 ET,
contains the 09:30 cash open). On a 24/7 perp adjacent 1h bars are contiguous
(prev_close == next_open), so there is NO inter-bar gap: the "open move" is the
o->c return realized DURING the 09:00-10:00 ET hour. This matches W-N1.

Bar return := c/o - 1 (intra-bar open->close).

Decisive test (test 2): open-bar return vs the SAME names' other hourly bars,
paired per (name, trading-day). Excess + t-stat + MC-null p decides real vs drift.
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

REPO = "/Users/julian_dev/Documents/code/hermes-trader"
CACHE = os.path.join(REPO, "research", "alpha_swarm", "hypotheses", "W-GAP1_cache_1h.json")
ET = ZoneInfo("America/New_York")
RNG = np.random.default_rng(20260722)

# Non-equity xyz (indices/commodities/fx/baskets) — verbatim from
# hermes_trader/agents/xs_xyz.py NON_EQUITY_XYZ. US-equity-open hypothesis
# targets SINGLE STOCKS; these are excluded from the core equity pool.
NON_EQUITY_XYZ = frozenset({
    "xyz:XYZ100", "xyz:SP500", "xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL", "xyz:CL",
    "xyz:NATGAS", "xyz:COPPER", "xyz:PLAT", "xyz:URAN", "xyz:EURUSD", "xyz:GBPUSD",
    "xyz:USDJPY", "xyz:USDCHF", "xyz:AUDUSD", "xyz:USDCAD", "xyz:NIKKEI", "xyz:DAX",
    "xyz:FTSE100", "xyz:HSI", "xyz:XAU", "xyz:XAG", "xyz:WTI", "xyz:BTCEQ",
    "xyz:PURRDAT", "xyz:DRAM",
})
US_INDEX_XYZ = frozenset({"xyz:XYZ100", "xyz:SP500"})

OPEN_ET_HOUR = 9          # bar 09:00-10:00 ET contains the 09:30 cash open
EQUITY_HOURS = set(range(9, 16))   # 09:00..15:00 ET starts => 09:00-16:00 ET session
MIN_OPEN_OBS = 30         # flag names below this many open-bar observations


def norm_sf(z):
    """One-sided upper-tail of standard normal via erf (no scipy)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_sided_p(t):
    return 2.0 * norm_sf(abs(t))


def tstat(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float("nan"), n, float("nan"), float("nan")
    m = x.mean()
    se = x.std(ddof=1) / math.sqrt(n)
    t = m / se if se > 0 else float("nan")
    return m, n, t, se


def load():
    with open(CACHE) as f:
        return json.load(f)


def bar_meta_index(all_ts):
    """Map each unique bar-open ms -> (et_hour, et_weekday, et_date_str). ~5000 uniq."""
    idx = {}
    for t in all_ts:
        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(ET)
        idx[t] = (dt.hour, dt.weekday(), dt.strftime("%Y-%m-%d"))
    return idx


def main():
    cache = load()
    universe = cache["meta"]["universe"]
    candles = cache["candles"]

    # union of timestamps for the ET index
    all_ts = set()
    for rows in candles.values():
        for r in rows:
            all_ts.add(int(r[0]))
    tmeta = bar_meta_index(all_ts)

    # classify names
    equities = [m["coin"] for m in universe
                if m["coin"] not in NON_EQUITY_XYZ and candles.get(m["coin"])]
    vol_by = {m["coin"]: m["dayNtlVlm"] for m in universe}

    # ---- build per-bar returns tagged by (coin, date, hour, weekday) ----
    # rec[coin] = list of (date, hour, weekday, ret, ts)
    rec = defaultdict(list)
    for coin, rows in candles.items():
        for r in rows:
            t, o, h, l, c, v = int(r[0]), r[1], r[2], r[3], r[4], r[5]
            if o <= 0 or c <= 0:
                continue
            hour, wd, date = tmeta[t]
            ret = c / o - 1.0
            rec[coin].append((date, hour, wd, ret, t))

    # global date range for the half-split
    all_dates = sorted({d for coin in equities for (d, h, wd, r, t) in rec[coin]
                        if wd < 5 and h == OPEN_ET_HOUR})
    if not all_dates:
        print("no open-bar data"); return
    mid_date = all_dates[len(all_dates) // 2]

    # ------------------------------------------------------------------
    # collect pools
    # ------------------------------------------------------------------
    open_rets, all_rets, eqhour_rets = [], [], []          # pooled o->c returns
    open_by_coin = defaultdict(list)
    # paired per (coin, date): open vs mean(other bars that day)
    paired_all, paired_eq = [], []
    # for null: per coin, list of (date, is_open, hour, ret) restricted to weekday equity hrs
    per_coin_day = defaultdict(lambda: defaultdict(dict))   # coin -> date -> hour -> ret
    # post-open fade: hour 10 return, and open-bar return keyed for same day
    postopen_rets = []
    # index/EW construction: date -> {coin: open_ret}
    open_ret_by_date = defaultdict(dict)
    # sub-sample: half tag per open obs
    open_h1, open_h2 = [], []
    paired_all_h1, paired_all_h2 = [], []

    for coin in equities:
        rows = rec[coin]
        for (d, h, wd, r, t) in rows:
            if wd >= 5:
                continue
            per_coin_day[coin][d][h] = r
            if h == OPEN_ET_HOUR:
                open_rets.append(r); open_by_coin[coin].append(r)
                open_ret_by_date[d][coin] = r
                if d < mid_date:
                    open_h1.append(r)
                else:
                    open_h2.append(r)
            if h in EQUITY_HOURS and h != OPEN_ET_HOUR:   # OTHER equity hrs (10-16 ET)
                eqhour_rets.append((coin, d, h, r))
            all_rets.append(r)

    # paired diffs
    for coin in equities:
        for d, hours in per_coin_day[coin].items():
            if OPEN_ET_HOUR not in hours:
                continue
            o = hours[OPEN_ET_HOUR]
            others_all = [rr for hh, rr in hours.items() if hh != OPEN_ET_HOUR]
            others_eq = [rr for hh, rr in hours.items()
                         if hh != OPEN_ET_HOUR and hh in EQUITY_HOURS]
            if others_all:
                diff = o - np.mean(others_all)
                paired_all.append(diff)
                (paired_all_h1 if d < mid_date else paired_all_h2).append(diff)
            if others_eq:
                paired_eq.append(o - np.mean(others_eq))
            if 10 in hours:
                postopen_rets.append(hours[10])

    open_rets = np.array(open_rets)
    all_rets_arr = np.array(all_rets)
    eqhour_arr = np.array([x[3] for x in eqhour_rets])

    # ------------------------------------------------------------------
    print("=" * 78)
    print("W-GAP1  xyz 9:30-ET open-bar gap — full-universe test")
    print("=" * 78)
    print(f"xyz universe total markets : {len(universe)}")
    print(f"equity single-names (core) : {len(equities)}  (excl {len(NON_EQUITY_XYZ)} non-equity)")
    print(f"trading-day span           : {all_dates[0]} .. {all_dates[-1]}  "
          f"({len(all_dates)} weekdays)  half-split @ {mid_date}")
    thin = [(c, len(open_by_coin[c])) for c in equities if len(open_by_coin[c]) < MIN_OPEN_OBS]
    print(f"names with <{MIN_OPEN_OBS} open obs (flagged): {len(thin)} "
          f"-> {sorted([c for c,_ in thin])[:8]}{'...' if len(thin)>8 else ''}")

    # ---- TEST 1: open-bar return ----
    m, n, t, se = tstat(open_rets)
    print("\n" + "-" * 78)
    print("TEST 1 — open-bar (09:00-10:00 ET) o->c return, pooled equity single-names")
    print("-" * 78)
    med = float(np.median(open_rets))
    trim = float(np.mean(np.sort(open_rets)[int(0.01*n):n-int(0.01*n)])) if n > 100 else med
    hit = float((open_rets > 0).mean())
    print(f"n={n}  mean={m*100:+.3f}%  median={med*100:+.3f}%  trim1%={trim*100:+.3f}%")
    print(f"std={open_rets.std()*100:.3f}%  hitrate={hit*100:.1f}%  t={t:.2f}  p={two_sided_p(t):.2g}")
    for rt in (0.0012, 0.0025):
        print(f"  net of {rt*1e4:.0f}bps round-trip: mean={ (m-rt)*100:+.3f}%  "
              f"per-trade Sharpe(net)={ (m-rt)/open_rets.std():.3f}")

    # ---- TEST 2: EXCESS vs other bars (decisive) ----
    print("\n" + "-" * 78)
    print("TEST 2 — EXCESS at the open vs same names' other bars  [THE VERDICT TEST]")
    print("-" * 78)
    mA, nA, _, _ = tstat(all_rets_arr)
    mE, nE, _, _ = tstat(eqhour_arr)
    print(f"baseline A (all 24 weekday hrs)  mean = {mA*100:+.4f}%  (n={nA})")
    print(f"baseline B (OTHER equity hrs 10-16 ET) mean = {mE*100:+.4f}%  (n={nE})")
    print(f"open-bar mean                         = {m*100:+.4f}%  (n={n})")
    print(f"  EXCESS vs all-24h (unpaired)  = {(m-mA)*100:+.4f}%")
    print(f"  EXCESS vs equity-hrs(unpaired)= {(m-mE)*100:+.4f}%")
    # paired per (name,day)
    for label, arr in (("paired vs all-other-bars-that-day", paired_all),
                       ("paired vs equity-other-bars-that-day", paired_eq)):
        pm, pn, pt, pse = tstat(arr)
        print(f"  {label}: excess={pm*100:+.4f}%  n={pn}  t={pt:.2f}  p={two_sided_p(pt):.2g}")

    # MC null for paired-all: shuffle which hour is "open" within each (coin,day)
    # observed = mean(paired_all). null: for each (coin,day) with >=2 hours pick a
    # RANDOM hour as pseudo-open, diff vs mean of the rest; 2000 draws.
    # Vectorized: diff for a picked hour = (n*pick - rowsum)/(n-1). Group cells by
    # their bar-count n so each group is a dense (cells_g, n) matrix; draw one
    # column per row per draw. Preserves equal per-(coin,day) weighting.
    from collections import defaultdict as _dd
    groups = _dd(list)   # n -> list of value-lists
    for coin in equities:
        for d, hours in per_coin_day[coin].items():
            if OPEN_ET_HOUR in hours and len(hours) >= 2:
                groups[len(hours)].append(list(hours.values()))
    total_cells = sum(len(v) for v in groups.values())
    obs = float(np.mean(paired_all))
    NDRAW = 2000
    draw_sums = np.zeros(NDRAW)
    for nlen, vlists in groups.items():
        M = np.array(vlists, float)                 # (g, nlen)
        rowsum = M.sum(axis=1, keepdims=True)       # (g,1)
        g = M.shape[0]
        for i in range(NDRAW):
            cols = RNG.integers(nlen, size=g)
            pick = M[np.arange(g), cols]
            diff = (nlen * pick - rowsum[:, 0]) / (nlen - 1)
            draw_sums[i] += diff.sum()
    null_means = draw_sums / total_cells
    p_mc = float((null_means >= obs).mean())
    print(f"  MC-null (random-hour-as-open, 2000 draws): obs excess={obs*100:+.4f}%  "
          f"null mean={null_means.mean()*100:+.4f}%  null sd={null_means.std()*100:.4f}%  "
          f"p(one-sided)={p_mc:.4f}")

    # ---- index-level / beta test ----
    print("\n" + "-" * 78)
    print("BETA CHECK — is the open pop just market beta? (index open-excess + market-neutral)")
    print("-" * 78)
    for idx in ("xyz:XYZ100", "xyz:SP500"):
        if idx not in rec:
            continue
        idx_open, idx_other = [], []
        by_day = defaultdict(dict)
        for (d, h, wd, r, t) in rec[idx]:
            if wd >= 5:
                continue
            by_day[d][h] = r
        idx_paired = []
        for d, hours in by_day.items():
            if OPEN_ET_HOUR in hours:
                idx_open.append(hours[OPEN_ET_HOUR])
                oth = [rr for hh, rr in hours.items() if hh != OPEN_ET_HOUR]
                if oth:
                    idx_paired.append(hours[OPEN_ET_HOUR] - np.mean(oth))
        im, iN, it, _ = tstat(idx_open)
        pm, pN, pt, _ = tstat(idx_paired)
        print(f"  {idx}: open-bar mean={im*100:+.3f}% (n={iN}, t={it:.2f}); "
              f"paired excess={pm*100:+.3f}% (n={pN}, t={pt:.2f})")
    # market-neutral: name open - EW-of-names open (same day)
    mn = []
    for d, cmap in open_ret_by_date.items():
        if len(cmap) < 5:
            continue
        vals = list(cmap.values())
        ew = np.mean(vals)
        for c, rr in cmap.items():
            mn.append(rr - ew)
    mnm, mnn, mnt, _ = tstat(mn)
    print(f"  market-neutral (name open - EW-names open, same day): mean={mnm*100:+.4f}% "
          f"n={mnn} t={mnt:.2f}  -> ~0 means the pop is 100% common/beta, not per-name")

    # ---- TEST 3: persistence + regime ----
    print("\n" + "-" * 78)
    print("TEST 3 — persistence (half-split) + down-day regime")
    print("-" * 78)
    for lab, arr in (("H1 open mean", open_h1), ("H2 open mean", open_h2)):
        mm, nn, tt, _ = tstat(arr)
        print(f"  {lab}: {mm*100:+.3f}%  n={nn}  t={tt:.2f}")
    for lab, arr in (("H1 paired-excess", paired_all_h1), ("H2 paired-excess", paired_all_h2)):
        mm, nn, tt, _ = tstat(arr)
        print(f"  {lab}: {mm*100:+.4f}%  n={nn}  t={tt:.2f}  p={two_sided_p(tt):.2g}")
    # down-day regime: EW of names' open on day d; split name-open obs by sign of EW-open
    up_obs, down_obs = [], []
    for d, cmap in open_ret_by_date.items():
        ew = np.mean(list(cmap.values()))
        for c, rr in cmap.items():
            (up_obs if ew >= 0 else down_obs).append(rr)
    for lab, arr in (("open on UP-tape days (EW-open>=0)", up_obs),
                     ("open on DOWN-tape days (EW-open<0)", down_obs)):
        mm, nn, tt, _ = tstat(arr)
        print(f"  {lab}: {mm*100:+.3f}%  n={nn}  t={tt:.2f}")
    # prior-day regime (tradeable): sign of name's own prior trading-day full move?
    # use index prior-day: XYZ100 daily close-to-close proxy via 09:00..next 09:00 hard;
    # simpler tradeable regime: EW-open sign is contemporaneous (not a filter). Reported above.

    # ---- TEST 5: post-open fade / direction ----
    print("\n" + "-" * 78)
    print("TEST 5 — direction: post-open (10:00-11:00 ET) fade after the open pop?")
    print("-" * 78)
    pm, pn, pt, _ = tstat(postopen_rets)
    print(f"  post-open-hour o->c: mean={pm*100:+.3f}%  n={pn}  t={pt:.2f}  p={two_sided_p(pt):.2g}")
    # conditional fade: on days the open popped >0, what does next hour do?
    fade_after_up, fade_after_dn = [], []
    for coin in equities:
        for d, hours in per_coin_day[coin].items():
            if OPEN_ET_HOUR in hours and 10 in hours:
                (fade_after_up if hours[OPEN_ET_HOUR] > 0 else fade_after_dn).append(hours[10])
    for lab, arr in (("next-hr after UP open", fade_after_up),
                     ("next-hr after DOWN open", fade_after_dn)):
        mm, nn, tt, _ = tstat(arr)
        print(f"  {lab}: {mm*100:+.3f}%  n={nn}  t={tt:.2f}")

    # ---- TEST 4: tradeable structure — entry timing sweep + hold sweep ----
    print("\n" + "-" * 78)
    print("TEST 4 — tradeable structure: entry lead + hold sweep (net of 25bps)")
    print("-" * 78)
    # cumulative returns from k hours before open through the open bar, and holding j hrs
    # reconstruct contiguous hourly series per coin/day using ET-hour->ret map already
    # entry lead: hours 6,7,8 (ET) -> open at 9; hold: through hour 9,10,11
    def cum(coin, d, hstart, hend):
        hours = per_coin_day[coin][d]
        rr = 1.0
        for hh in range(hstart, hend + 1):
            if hh not in hours:
                return None
            rr *= (1 + hours[hh])
        return rr - 1
    for lead, name_l in ((0, "enter 09:00(open)"), (1, "enter 08:00(-1h)"), (2, "enter 07:00(-2h)")):
        for hold_end, name_h in ((9, "exit 10:00"), (10, "exit 11:00"), (11, "exit 12:00")):
            hstart = OPEN_ET_HOUR - lead
            if hstart > hold_end:
                continue
            vals = []
            for coin in equities:
                for d in per_coin_day[coin]:
                    c = cum(coin, d, hstart, hold_end)
                    if c is not None:
                        vals.append(c)
            if len(vals) < 50:
                continue
            mm, nn, tt, _ = tstat(vals)
            net = mm - 0.0025
            sh = net / np.std(vals) if np.std(vals) > 0 else float("nan")
            print(f"  {name_l} -> {name_h}: gross={mm*100:+.3f}% net25={net*100:+.3f}% "
                  f"n={nn} t={tt:.2f} Sharpe(net)={sh:.3f}")

    # per-name breadth: how many names have +EV open bar net of 25bps
    print("\n" + "-" * 78)
    print("BREADTH — per-name open-bar mean (net 25bps), names >= %d obs" % MIN_OPEN_OBS)
    print("-" * 78)
    pos = 0; tot = 0
    rows = []
    for c in equities:
        arr = np.array(open_by_coin[c])
        if len(arr) < MIN_OPEN_OBS:
            continue
        tot += 1
        net = arr.mean() - 0.0025
        if net > 0:
            pos += 1
        rows.append((c, len(arr), arr.mean()*100, net*100, vol_by.get(c, 0)))
    rows.sort(key=lambda x: -x[2])
    print(f"  names with net25>0 open bar: {pos}/{tot}")
    print("  top 8 by gross open mean:")
    for c, nn, g, net, vl in rows[:8]:
        print(f"    {c:14s} n={nn:3d} gross={g:+.3f}% net25={net:+.3f}% vlm=${vl:,.0f}")
    print("  bottom 5:")
    for c, nn, g, net, vl in rows[-5:]:
        print(f"    {c:14s} n={nn:3d} gross={g:+.3f}% net25={net:+.3f}% vlm=${vl:,.0f}")


if __name__ == "__main__":
    main()
