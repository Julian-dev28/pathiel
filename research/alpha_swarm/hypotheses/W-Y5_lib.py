#!/usr/bin/env python
"""W-Y5 shared engine: young-listing expansion study (lib, no network).

Reused/adapted from W-Y1_young_backtest.py (same simulate/episode/null
discipline) so results stay comparable:
  - Lookahead-safe: signal at day-i close, fill at day-(i+1) open.
  - Costs: 25 bps/side (50 bps RT) headline, 40 bps/side stress.
  - One open episode per coin at a time.
  - Incomplete holds dropped unless the stop fired first.
  - MC null: same-coin same-window random entry bars, same mechanics,
    one-sided p on the mean net@25.
  - Overlay lift: label-permutation p (does conditioning beat random
    sub-sampling of the SAME base population?).
"""
import json
import os
import random
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY_CACHE = os.path.join(HERE, "W-Y5_cache_daily.json")
UNI_CACHE = os.path.join(HERE, "W-Y5_cache_universe.json")
FUND_CACHE = os.path.join(HERE, "W-Y5_cache_funding.json")

YOUNG_LO, YOUNG_HI = 2, 59
MC_ITERS = 2000
DAY_MS = 86_400_000


# ---------------------------------------------------------------- loading
def load_daily(min_bars=5):
    raw = json.load(open(DAILY_CACHE))
    coins = {}
    for coin, rows in raw.items():
        rows = [r for r in rows if r and r[4] and r[4] > 0]
        if len(rows) < min_bars:
            continue
        rows.sort(key=lambda r: r[0])
        coins[coin] = rows[:-1]          # drop in-progress last bar
    return coins


def asset_class(coin):
    if ":" not in coin:
        return "crypto"
    return "xyz_equity" if coin.startswith("xyz:") else "hip3_other"


def window_start(coins, fetch_bars=400):
    """Earliest first-bar ts across truncated (full-window) coins = the fetch
    window's left edge."""
    full = [r[0][0] for r in coins.values() if len(r) >= fetch_bars - 4]
    return min(full) if full else 0


def listed_in_sample(rows, ws, fetch_bars=400):
    """True when the coin's first bar is a real listing (not fetch truncation,
    not a gap-riddled old coin whose first cached bar sits at the window
    edge). Requires BOTH a short history AND a first bar clearly inside the
    window."""
    return (len(rows) < fetch_bars - 5
            and rows[0][0] > ws + 3 * DAY_MS)


# ------------------------------------------------------------- trade engine
def simulate(rows, sig_i, direction, hold, stop):
    """Gross return (before costs) or None when the hold can't complete.
    direction: +1 long, -1 short. Stop checked on daily H/L path; gap-through
    exits at the gapped open."""
    e = sig_i + 1
    if e >= len(rows):
        return None
    entry = rows[e][1]
    if entry <= 0:
        return None
    stop_px = entry * (1 - stop) if direction > 0 else entry * (1 + stop)
    last_exit = e + hold - 1
    for j in range(e, min(last_exit, len(rows) - 1) + 1):
        o, h, l = rows[j][1], rows[j][2], rows[j][3]
        if direction > 0:
            if j > e and o <= stop_px:
                return o / entry - 1
            if l <= stop_px:
                return stop_px / entry - 1
        else:
            if j > e and o >= stop_px:
                return 1 - o / entry
            if h >= stop_px:
                return 1 - stop_px / entry
    if last_exit > len(rows) - 1:
        return None
    c = rows[last_exit][4]
    return (c / entry - 1) if direction > 0 else (1 - c / entry)


def episode_filter(signals, hold):
    out, busy = [], {}
    for coin, i in sorted(signals, key=lambda x: (x[0], x[1])):
        if i + 1 <= busy.get(coin, -1):
            continue
        out.append((coin, i))
        busy[coin] = i + hold
    return out


def net(g, bps=25):
    return g - 2 * bps / 10_000.0


def run_cell(coins, signals, direction, hold, stop):
    """[(coin, sig_i, gross)] with one-open-episode-per-coin discipline."""
    trades = []
    for coin, i in episode_filter(signals, hold):
        g = simulate(coins[coin], i, direction, hold, stop)
        if g is not None:
            trades.append((coin, i, g))
    return trades


# ------------------------------------------------------------------- nulls
def mc_entry_null(coins, trades, pools, direction, hold, stop, rng,
                  iters=MC_ITERS):
    """Matched same-coin random-entry-time null. `pools[coin]` = candidate
    signal indices (the coin's comparable window). One-sided p on mean net@25
    (>= for the traded direction's mean)."""
    if not trades:
        return None, None
    real = st.mean(net(g) for _, _, g in trades)
    ge = n_eff = 0
    null_means = []
    for _ in range(iters):
        vals = []
        for coin, _, _ in trades:
            pool = pools.get(coin)
            if not pool:
                continue
            g = simulate(coins[coin], rng.choice(pool), direction, hold, stop)
            if g is not None:
                vals.append(net(g))
        if not vals:
            continue
        n_eff += 1
        m = st.mean(vals)
        null_means.append(m)
        if m >= real:
            ge += 1
    if not n_eff:
        return None, None
    return (ge + 1) / (n_eff + 1), real - st.mean(null_means)


def perm_lift_p(base_trades, subset_mask, rng, iters=MC_ITERS):
    """Label-permutation p for an overlay: P(random subset of the same size
    from the base population has mean >= the overlay subset mean)."""
    sub = [t for t, m in zip(base_trades, subset_mask) if m]
    if not sub or len(sub) == len(base_trades):
        return None
    real = st.mean(net(g) for _, _, g in sub)
    k = len(sub)
    vals = [net(g) for _, _, g in base_trades]
    ge = 0
    for _ in range(iters):
        s = rng.sample(vals, k)
        if st.mean(s) >= real:
            ge += 1
    return (ge + 1) / (iters + 1)


# ------------------------------------------------------------------ report
def describe(trades, coins, listing_ts, med_listing, bps=25):
    """Standard cell stats: n, ev, win, OOS calendar halves + listing cohorts."""
    if not trades:
        return {"n": 0}
    vals = [net(g, bps) for _, _, g in trades]
    n = len(vals)
    by_t = sorted(trades, key=lambda t: coins[t[0]][t[1]][0])
    half = n // 2
    h1 = [net(g, bps) for _, _, g in by_t[:half]]
    h2 = [net(g, bps) for _, _, g in by_t[half:]]
    early = [net(g, bps) for c, _, g in trades if listing_ts[c] < med_listing]
    late = [net(g, bps) for c, _, g in trades if listing_ts[c] >= med_listing]
    return {
        "n": n,
        "n_coins": len({c for c, _, _ in trades}),
        "ev": st.mean(vals),
        "ev40": st.mean(net(g, 40) for _, _, g in trades),
        "win": sum(1 for v in vals if v > 0) / n,
        "oos_h1": st.mean(h1) if h1 else None,
        "oos_h2": st.mean(h2) if h2 else None,
        "ev_early": st.mean(early) if early else None,
        "n_early": len(early),
        "ev_late": st.mean(late) if late else None,
        "n_late": len(late),
    }


def fmt_cell(name, d, mc_p=None, excess=None):
    if d["n"] == 0:
        return f"{name:<34} n=0"
    p = f"{mc_p:.4f}" if mc_p is not None else "  --  "
    ex = f"{excess*100:+.2f}" if excess is not None else "  -- "
    return (f"{name:<34} n={d['n']:>4} ({d['n_coins']:>3}c) "
            f"ev25={d['ev']*100:+6.2f}% ev40={d['ev40']*100:+6.2f}% "
            f"win={d['win']*100:3.0f}% "
            f"oosT={_p(d['oos_h1'])}/{_p(d['oos_h2'])} "
            f"oosL={_p(d['ev_early'])}({d['n_early']})/"
            f"{_p(d['ev_late'])}({d['n_late']}) "
            f"xs={ex} p={p}")


def _p(x):
    return f"{x*100:+.2f}" if isinstance(x, float) else "--"


# ------------------------------------------------------------- funding util
def load_funding():
    if not os.path.exists(FUND_CACHE):
        return {}
    return json.load(open(FUND_CACHE))


def funding_sum(fund_rows, t_from, t_to):
    """Sum of hourly funding rates with time in [t_from, t_to). Positive sum
    means longs paid shorts over the window (short EARNS it). None when the
    window has no funding points (missing data)."""
    if not fund_rows:
        return None
    s, k = 0.0, 0
    for r in fund_rows:
        t = int(r["time"])
        if t_from <= t < t_to:
            s += float(r["fundingRate"])
            k += 1
    return s if k else None
