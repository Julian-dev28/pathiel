#!/usr/bin/env python
"""W-Y5 Task 3: PIT forward returns of EVERY preflight-blocked cohort in the
loop's own log (2026-06-26 .. now) — the same "the gate is right about
direction, take the other side" read that made young_mover_short live.

Cohorts (one episode per coin per UTC day, first block ts of the day):
  hist_xyz    history_floor_preflight, xyz: equities   (the LIVE book's base)
  hist_crypto history_floor_preflight, native crypto   (n-check for expansion)
  liq_xyz     liquidity_floor_preflight, xyz: equities (vol < $0.7M floor)
  liq_crypto  liquidity_floor_preflight, native crypto

For each episode: enter SHORT at the next 1h bar open after the block, grade
  (a) raw hold-to-24h / hold-to-72h close (no stop) — comparable to the n=126
      -2.71%-next-day study that armed the live book, and
  (b) the LIVE geometry: 6% stop path on 1h highs, 24h timeout.
Costs 25 bps/side. Null: 2000 same-coin random-entry-hour portfolios over the
coin's cached 1h window (same stop mechanics), one-sided p on mean net@25.

Data: W-Y5_cache_blocked_1h.json (W-Y5_fetch.py). No network.
"""
import json
import os
import random
import re
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = str(Path(__file__).resolve().parents[3] / "logs" / "trading_loop.log")
HOURLY = json.load(open(os.path.join(HERE, "W-Y5_cache_blocked_1h.json")))

MC_ITERS = 2000
SEED = 20260722
H_MS = 3_600_000
BPS = 25

_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO:__main__:(.+?): "
    r"pre-research (history_floor_preflight|liquidity_floor_preflight)")


def episodes_from_log():
    """{(coin, utc_day) -> {ts_ms, kind}} using the FIRST block of the day."""
    eps = {}
    with open(LOG, errors="replace") as fh:
        for line in fh:
            m = _RE.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            ts = ts.astimezone(timezone.utc)          # log is machine-local
            t_ms = int(ts.timestamp() * 1000)
            coin, kind = m.group(2), m.group(3)
            key = (coin, ts.strftime("%Y-%m-%d"))
            if key not in eps or t_ms < eps[key]["ts"]:
                eps[key] = {"ts": t_ms, "kind": kind}
    return eps


def cohort_of(coin, kind):
    k = "hist" if kind.startswith("history") else "liq"
    if coin.startswith("xyz:"):
        return f"{k}_xyz"
    if ":" in coin:
        return f"{k}_hip3other"
    return f"{k}_crypto"


def bar_index_at(rows, t_ms):
    """Index of the first 1h bar with open time >= t_ms (entry bar)."""
    for i, r in enumerate(rows):
        if r[0] >= t_ms:
            return i
    return None


def short_raw(rows, e, hours):
    if e + hours >= len(rows):
        return None
    entry = rows[e][1]
    if entry <= 0:
        return None
    return 1 - rows[e + hours - 1][4] / entry


def short_stopped(rows, e, hours, stop=0.06):
    """Live geometry: short at open(e), 6% stop on 1h highs, timeout at
    close(e+hours-1). Gap-through exits at the gapped open."""
    if e + hours >= len(rows):
        return None
    entry = rows[e][1]
    if entry <= 0:
        return None
    stop_px = entry * (1 + stop)
    for j in range(e, e + hours):
        o, h = rows[j][1], rows[j][2]
        if j > e and o >= stop_px:
            return 1 - o / entry
        if h >= stop_px:
            return 1 - stop_px / entry
    return 1 - rows[e + hours - 1][4] / entry


def net(g):
    return g - 2 * BPS / 10_000.0 if g is not None else None


def grade(name, eps, rng):
    rows_out = []
    for (coin, day), meta in sorted(eps.items()):
        bars = HOURLY.get(coin) or []
        if len(bars) < 30:
            continue
        e = bar_index_at(bars, meta["ts"])
        if e is None or e + 1 >= len(bars):
            continue
        r24 = short_raw(bars, e, 24)
        r72 = short_raw(bars, e, 72)
        live = short_stopped(bars, e, 24, 0.06)
        rows_out.append({"coin": coin, "day": day, "e": e,
                         "r24": r24, "r72": r72, "live": live})
    graded = [r for r in rows_out if r["r24"] is not None]
    if not graded:
        print(f"{name:<12} n=0 (no gradeable episodes)")
        return None
    n = len(graded)
    coins_n = len({r['coin'] for r in graded})
    ev24 = st.mean(net(r["r24"]) for r in graded)
    lives = [net(r["live"]) for r in graded if r["live"] is not None]
    ev_live = st.mean(lives) if lives else None
    r72s = [net(r["r72"]) for r in graded if r["r72"] is not None]
    ev72 = st.mean(r72s) if r72s else None
    win = sum(1 for r in graded if net(r["r24"]) > 0) / n
    graded.sort(key=lambda r: r["day"])
    half = n // 2
    h1 = st.mean(net(r["r24"]) for r in graded[:half]) if half else None
    h2 = st.mean(net(r["r24"]) for r in graded[half:])

    # matched same-coin random-entry-hour null on the RAW 24h short
    real = ev24
    ge = n_eff = 0
    null_means = []
    pools = {}
    for r in graded:
        c = r["coin"]
        if c not in pools:
            bars = HOURLY[c]
            pools[c] = [i for i in range(1, len(bars) - 25)]
    for _ in range(MC_ITERS):
        vals = []
        for r in graded:
            pool = pools[r["coin"]]
            if not pool:
                continue
            g = short_raw(HOURLY[r["coin"]], rng.choice(pool), 24)
            if g is not None:
                vals.append(net(g))
        if vals:
            n_eff += 1
            m = st.mean(vals)
            null_means.append(m)
            if m >= real:
                ge += 1
    mc_p = (ge + 1) / (n_eff + 1) if n_eff else None
    excess = real - st.mean(null_means) if null_means else None

    print(f"{name:<12} n={n:>3} ({coins_n:>2}c) SHORT ev24={ev24*100:+6.2f}% "
          f"ev72={(ev72*100 if ev72 is not None else float('nan')):+6.2f}% "
          f"live6%={(ev_live*100 if ev_live is not None else float('nan')):+6.2f}% "
          f"win={win*100:3.0f}% oosT={h1*100 if h1 else 0:+.2f}/{h2*100:+.2f} "
          f"xs={excess*100 if excess is not None else float('nan'):+.2f} "
          f"p={mc_p if mc_p is None else format(mc_p, '.4f')}")
    return {"name": name, "n": n, "n_coins": coins_n, "ev24": ev24,
            "ev72": ev72, "ev_live6": ev_live, "win": win, "oos_h1": h1,
            "oos_h2": h2, "excess": excess, "mc_p": mc_p,
            "episodes": [{k: r[k] for k in ("coin", "day", "r24", "r72", "live")}
                         for r in graded]}


def main():
    rng = random.Random(SEED)
    eps = episodes_from_log()
    print(f"log episodes (coin-days): {len(eps)}")
    by_cohort = {}
    for key, meta in eps.items():
        by_cohort.setdefault(cohort_of(key[0], meta["kind"]), {})[key] = meta
    out = {}
    print(f"\n{'cohort':<12} {'':>14} SHORT-side forward (net {2*BPS}bps RT)")
    for name in ("hist_xyz", "hist_crypto", "hist_hip3other",
                 "liq_xyz", "liq_crypto", "liq_hip3other"):
        if name in by_cohort:
            res = grade(name, by_cohort[name], rng)
            if res:
                out[name] = res
    with open(os.path.join(HERE, "W-Y5_log_cohorts.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"\nresults -> W-Y5_log_cohorts.json")


if __name__ == "__main__":
    main()
