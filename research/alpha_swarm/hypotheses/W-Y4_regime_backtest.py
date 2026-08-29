#!/usr/bin/env python
"""W-Y4: regime overlay on the young_mover_short cohort — validate/refute the
2026-07-20 retrospective slice (equity index up 7d -> young shorts +6.03%/85%;
down -> +0.18%/48%; n=55/71).

PRIMARY (pre-registered from the thesis, before running): GATE = xyz:SP500
7-calendar-day return > 0, computed PIT from COMPLETED daily closes strictly
before the block's UTC day. All other regime definitions are SECONDARY
descriptive variants (7/14/20d, level vs slope, XYZ100, BTC, the live 1h
EMA20/50 tag, intraday tape) and carry a multiple-testing haircut.

Mechanics (identical to W-Y2 / the live book's geometry):
  - Cohort: history_floor_preflight (coin, UTC day) episodes from the live
    log (W-Y4_episodes.json), first block ts of the day.
  - Entry: open of the first 1h bar at/after the block ts. SHORT.
  - Exit: close of the bar at entry+24h (tolerance 2h; incomplete -> dropped).
  - Costs: 25 bps/side (50 bps RT). Funding: real HL fundingHistory summed
    over the held window, short receives +rate (shadow_ledger convention).
  - Stop variant: live 6% backup stop from the 1h high path.
  - Null A: same-coin random-entry-1h-bar portfolios (2000 iters), one-sided
    p on mean net@25 of the gated subset. EXCESS = real - null mean.
  - Null B: same, but draws restricted to bars whose UTC day has the SAME
    regime state (up) -> does block-day timing add anything beyond regime?
  - OOS: gated subset split at its median entry time, both halves reported.
  - Independence: non-overlapping-episode variant (same-coin episodes inside
    a prior episode's 24h hold are dropped) + per-day clustering + top-coin
    exclusion.

Outputs W-Y4_results.json + console tables. No network.
"""
import json
import os
import random
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
EPIS = json.load(open(os.path.join(HERE, "W-Y4_episodes.json")))
H1 = json.load(open(os.path.join(HERE, "W-Y4_cache_1h.json")))
IDX = json.load(open(os.path.join(HERE, "W-Y4_cache_index.json")))
FUND = json.load(open(os.path.join(HERE, "W-Y4_cache_funding.json")))

HOUR = 3_600_000
DAY = 86_400_000
RT_COST = 0.0050          # 25 bps/side
STOP_LIVE = 0.06          # live young_mover_short backup stop
MC_ITERS = 2000
SEED = 20260722
CUTOFF_REPRO = "2026-07-20"   # reproduce the retrospective as-of date

EQ, X100, BTC = "xyz:SP500", "xyz:XYZ100", "BTC"


# ---------------------------------------------------------------- index PIT
def completed_daily(proxy, ts_ms):
    """Daily closes of bars fully completed before ts_ms (bar t + 1d <= ts)."""
    return [(r[0], r[4]) for r in IDX["1d"][proxy] if r[0] + DAY <= ts_ms]


def kday_ret(proxy, ts_ms, k):
    """prev completed close vs the close k calendar days earlier (nearest bar
    at or before the target grid slot). None if history too short."""
    bars = completed_daily(proxy, ts_ms)
    if len(bars) < k + 1:
        return None
    t_prev, c_prev = bars[-1]
    target = t_prev - k * DAY
    older = [b for b in bars if b[0] <= target]
    if not older or older[-1][1] <= 0:
        return None
    return c_prev / older[-1][1] - 1


def level20(proxy, ts_ms):
    bars = completed_daily(proxy, ts_ms)
    if len(bars) < 21:
        return None
    closes = [c for _, c in bars]
    return closes[-1] > st.mean(closes[-20:])


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def live_regime_1h(proxy, ts_ms):
    """Replica of market_regime._trend_from_closes on the last 100 completed
    1h closes before ts_ms: EMA20 vs EMA50 + 8-bar slope, +/-0.1% thresholds."""
    closes = [r[4] for r in IDX["1h"][proxy] if r[0] + HOUR <= ts_ms][-100:]
    if len(closes) < 50:
        return None
    fast, slow = ema(closes, 20), ema(closes, 50)
    f_now, s_now, f_prev = fast[-1], slow[-1], fast[-9]
    if f_prev == 0:
        return None
    slope = (f_now - f_prev) / abs(f_prev)
    if f_now > s_now and slope > 0.001:
        return "up"
    if f_now < s_now and slope < -0.001:
        return "down"
    return "neutral"


def intraday_tape(proxy, ts_ms):
    """Index return from prev completed daily close to last 1h close < ts."""
    daily = completed_daily(proxy, ts_ms)
    h = [r for r in IDX["1h"][proxy] if r[0] + HOUR <= ts_ms]
    if not daily or not h or daily[-1][1] <= 0:
        return None
    return h[-1][4] / daily[-1][1] - 1


# ---------------------------------------------------------------- trade sim
def funding_short(coin, start_ms, end_ms):
    tot = 0.0
    for r in FUND.get(coin, []):
        if start_ms < r["time"] <= end_ms:
            tot += r["fundingRate"]
    return tot  # short RECEIVES +rate


def sim_episode(coin, block_ts):
    """Returns dict with gross no-stop, gross stop6, funding, exit ts; or None."""
    bars = H1.get(coin, [])
    entry_bar = next((b for b in bars if b[0] >= block_ts), None)
    if entry_bar is None or entry_bar[1] <= 0:
        return None
    entry_t, entry = entry_bar[0], entry_bar[1]
    target = entry_t + 24 * HOUR
    if not bars or bars[-1][0] < target:
        return None                      # forward window incomplete
    path = [b for b in bars if entry_t <= b[0] <= target]
    exit_c = [b for b in path if b[0] <= target]
    if not exit_c or exit_c[-1][0] < target - 2 * HOUR:
        return None                      # gap at the exit
    exit_bar = exit_c[-1]
    gross = 1 - exit_bar[4] / entry
    # live 6% stop from the 1h high path
    stop_px = entry * (1 + STOP_LIVE)
    g_stop, t_end = gross, exit_bar[0]
    for b in path:
        if b[0] > entry_t and b[1] >= stop_px:
            g_stop, t_end = 1 - b[1] / entry, b[0]
            break
        if b[2] >= stop_px:
            g_stop, t_end = -STOP_LIVE, b[0]
            break
    return {"entry_t": entry_t, "entry": entry, "gross": gross,
            "gross_stop": g_stop, "stop_end_t": t_end,
            "fund": funding_short(coin, entry_t, target)}


# ------------------------------------------------------------- build trades
def build_trades():
    trades, dropped = [], 0
    for e in EPIS:
        r = sim_episode(e["coin"], e["block_ts_ms"])
        if r is None:
            dropped += 1
            continue
        ts = e["block_ts_ms"]
        t = dict(e)
        t.update(r)
        t["net25"] = r["gross"] - RT_COST
        t["net25f"] = r["gross"] - RT_COST + r["fund"]
        t["net25_stop"] = r["gross_stop"] - RT_COST
        # regime signals (all PIT at block ts)
        t["eq7"] = kday_ret(EQ, ts, 7)
        t["eq14"] = kday_ret(EQ, ts, 14)
        t["eq20"] = kday_ret(EQ, ts, 20)
        t["x100_7"] = kday_ret(X100, ts, 7)
        t["btc7"] = kday_ret(BTC, ts, 7)
        t["btc14"] = kday_ret(BTC, ts, 14)
        t["own7"] = t["eq7"] if t["is_xyz"] else t["btc7"]
        t["lvl20"] = level20(EQ, ts)
        t["live1h"] = live_regime_1h(EQ, ts)
        t["intraday"] = intraday_tape(EQ, ts)
        trades.append(t)
    return trades, dropped


def dedup_overlap(trades):
    out, busy = [], {}
    for t in sorted(trades, key=lambda x: x["entry_t"]):
        if t["entry_t"] < busy.get(t["coin"], -1):
            continue
        out.append(t)
        busy[t["coin"]] = t["entry_t"] + 24 * HOUR
    return out


# ---------------------------------------------------------------- reporting
def stats(sub, key="net25"):
    if not sub:
        return dict(n=0)
    vals = [t[key] for t in sub]
    return dict(n=len(vals), ev=st.mean(vals), med=st.median(vals),
                win=sum(1 for v in vals if v > 0) / len(vals))


def fmt(s, extra=""):
    if s["n"] == 0:
        return "n=0"
    return (f"n={s['n']:>3} ev={s['ev']*100:+6.2f}% med={s['med']*100:+6.2f}% "
            f"win={s['win']*100:3.0f}%{extra}")


def split_table(trades, name, keyfn):
    ups = [t for t in trades if keyfn(t) is True]
    dns = [t for t in trades if keyfn(t) is False]
    oth = [t for t in trades if keyfn(t) is None]
    su, sd = stats(ups), stats(dns)
    gap = (su.get("ev", 0) - sd.get("ev", 0)) * 100 if su["n"] and sd["n"] else float("nan")
    print(f"  {name:<12} UP  {fmt(su)}")
    print(f"  {'':<12} DOWN{fmt(sd)}   gap={gap:+.2f}pp"
          + (f"  (excluded/None: {len(oth)})" if oth else ""))
    return {"name": name, "up": su, "down": sd, "gap_pp": gap, "n_none": len(oth)}


# ------------------------------------------------------------------- nulls
def day_of(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()


def build_null_pools(trades, regime_up_days):
    """Per coin: candidate 1h entry bars inside the study window with a
    complete 24h forward path. Pool B additionally requires the bar's UTC
    day to be an eq7-UP day."""
    t_lo = min(t["block_ts_ms"] for t in trades) - 12 * HOUR
    pools_a, pools_b = {}, {}
    for coin in {t["coin"] for t in trades}:
        bars = H1.get(coin, [])
        last_ok = bars[-1][0] - 24 * HOUR if bars else 0
        cand = [b[0] for b in bars if t_lo <= b[0] <= last_ok]
        pools_a[coin] = cand
        pools_b[coin] = [ts for ts in cand if day_of(ts) in regime_up_days]
    return pools_a, pools_b


def null_p(trades, pools, rng, real_ev):
    means, ge = [], 0
    for _ in range(MC_ITERS):
        vals = []
        for t in trades:
            pool = pools.get(t["coin"])
            if not pool:
                continue
            r = sim_episode(t["coin"], rng.choice(pool))
            if r is not None:
                vals.append(r["gross"] - RT_COST)
        if not vals:
            continue
        m = st.mean(vals)
        means.append(m)
        if m >= real_ev:
            ge += 1
    if not means:
        return None, None
    return (ge + 1) / (len(means) + 1), st.mean(means)


# -------------------------------------------------------------------- main
def main():
    rng = random.Random(SEED)
    trades, dropped = build_trades()
    xyz = [t for t in trades if t["is_xyz"]]
    bare = [t for t in trades if not t["is_xyz"]]
    print(f"episodes: {len(EPIS)}  simulated: {len(trades)} "
          f"(dropped {dropped}: no data / incomplete fwd window)")
    print(f"  xyz: {len(xyz)}   crypto: {len(bare)}")
    print(f"  ALL xyz ungated: {fmt(stats(xyz))}  gross={st.mean([t['gross'] for t in xyz])*100:+.2f}%"
          f"  net+funding={st.mean([t['net25f'] for t in xyz])*100:+.2f}%")

    results = {"n_episodes": len(EPIS), "n_sim": len(trades), "dropped": dropped}

    # (a) reproduction of the 2026-07-20 retrospective (xyz-only, <= cutoff)
    repro = [t for t in xyz if t["day"] <= CUTOFF_REPRO]
    print(f"\n== (a) REPRODUCTION: xyz-only, day <= {CUTOFF_REPRO} (n={len(repro)}) "
          f"vs claimed +6.03%/85% up (n=55) / +0.18%/48% down (n=71) ==")
    results["repro_eq7"] = split_table(repro, "eq7>0", lambda t: None if t["eq7"] is None else t["eq7"] > 0)
    results["repro_eq7_gross"] = {
        "up": stats([t for t in repro if t["eq7"] and t["eq7"] > 0], "gross"),
        "down": stats([t for t in repro if t["eq7"] is not None and t["eq7"] <= 0], "gross")}
    su, sd = results["repro_eq7_gross"]["up"], results["repro_eq7_gross"]["down"]
    print(f"  (gross)      UP  {fmt(su)}\n  {'':<12} DOWN{fmt(sd)}")

    # (b) regime-definition variants, full xyz sample
    print(f"\n== (b) VARIANTS, full xyz sample (n={len(xyz)}), net@25 no-stop ==")
    variants = [
        ("eq7>0", lambda t: None if t["eq7"] is None else t["eq7"] > 0),
        ("eq14>0", lambda t: None if t["eq14"] is None else t["eq14"] > 0),
        ("eq20>0", lambda t: None if t["eq20"] is None else t["eq20"] > 0),
        ("x100_7>0", lambda t: None if t["x100_7"] is None else t["x100_7"] > 0),
        ("btc7>0", lambda t: None if t["btc7"] is None else t["btc7"] > 0),
        ("btc14>0", lambda t: None if t["btc14"] is None else t["btc14"] > 0),
        ("lvl20", lambda t: t["lvl20"]),
        ("live1h=up", lambda t: None if t["live1h"] is None else t["live1h"] == "up"),
        ("intraday>0", lambda t: None if t["intraday"] is None else t["intraday"] > 0),
    ]
    results["variants"] = [split_table(xyz, n, f) for n, f in variants]

    # live1h three-state detail (what the ledger meta actually records)
    l_up = [t for t in xyz if t["live1h"] == "up"]
    l_dn = [t for t in xyz if t["live1h"] == "down"]
    l_nt = [t for t in xyz if t["live1h"] == "neutral"]
    print(f"  live1h 3-state: up {fmt(stats(l_up))} | neutral {fmt(stats(l_nt))} | down {fmt(stats(l_dn))}")
    results["live1h_3state"] = {"up": stats(l_up), "neutral": stats(l_nt), "down": stats(l_dn)}

    # (c/d) THE GATE: eq7>0 on xyz episodes (primary, pre-registered)
    gated = [t for t in xyz if t["eq7"] is not None and t["eq7"] > 0]
    blocked = [t for t in xyz if t["eq7"] is not None and t["eq7"] <= 0]
    print(f"\n== (c) GATE eq7>0, xyz-only, full sample ==")
    print(f"  taken   {fmt(stats(gated))}   net+funding ev={st.mean([t['net25f'] for t in gated])*100:+.2f}%"
          f"   live-6%-stop ev={st.mean([t['net25_stop'] for t in gated])*100:+.2f}%")
    print(f"  forgone {fmt(stats(blocked))}   net+funding ev={st.mean([t['net25f'] for t in blocked])*100:+.2f}%")

    # OOS halves of the gated subset
    gated_sorted = sorted(gated, key=lambda t: t["entry_t"])
    half = len(gated_sorted) // 2
    h1s, h2s = stats(gated_sorted[:half]), stats(gated_sorted[half:])
    print(f"  OOS halves: H1 {fmt(h1s)} | H2 {fmt(h2s)}")

    # independence: non-overlapping episodes
    g_ind = dedup_overlap(gated)
    print(f"  non-overlapping: {fmt(stats(g_ind))} (dropped {len(gated)-len(g_ind)} overlaps)")

    # coin concentration
    bycoin = {}
    for t in gated:
        bycoin.setdefault(t["coin"], []).append(t["net25"])
    ranked = sorted(bycoin.items(), key=lambda kv: -sum(kv[1]))
    top = ranked[0]
    ex_top = [t for t in gated if t["coin"] != top[0]]
    print(f"  top coin {top[0]}: sum {sum(top[1])*100:+.1f}pp over {len(top[1])} eps; "
          f"ex-top: {fmt(stats(ex_top))}  coins={len(bycoin)}")

    # day clustering + day-equal-weight EV
    byday = {}
    for t in gated:
        byday.setdefault(t["day"], []).append(t["net25"])
    daymeans = [st.mean(v) for v in byday.values()]
    print(f"  gated days: {len(byday)}  max eps/day: {max(len(v) for v in byday.values())}  "
          f"day-equal-weight ev: {st.mean(daymeans)*100:+.2f}%")

    # per-day table over ALL xyz episodes (regime sign + realized short EV)
    print("\n  per-day (xyz): day | eq7 | n | mean net25 | coins")
    allday = {}
    for t in xyz:
        allday.setdefault(t["day"], []).append(t)
    for dstr in sorted(allday):
        sub = allday[dstr]
        sign = ("UP" if sub[0]["eq7"] and sub[0]["eq7"] > 0 else
                "dn" if sub[0]["eq7"] is not None else "??")
        print(f"    {dstr}  {sign}  n={len(sub):>2}  {st.mean([t['net25'] for t in sub])*100:+7.2f}%  "
              f"{','.join(sorted({t['coin'].split(':')[-1] for t in sub}))[:60]}")

    # day-cluster permutation: are the ACTUAL up-days better than random
    # same-size day subsets of the observed block days? (robust to within-day
    # correlation, unlike nulls A/B)
    day_keys = sorted(allday)
    k_up = len(byday)
    real_daylevel = st.mean([t["net25"] for d in byday for t in allday[d]])
    ge = 0
    for _ in range(MC_ITERS):
        pick = rng.sample(day_keys, k_up)
        vals = [t["net25"] for d in pick for t in allday[d]]
        if vals and st.mean(vals) >= real_daylevel:
            ge += 1
    perm_p = (ge + 1) / (MC_ITERS + 1)
    print(f"  day-cluster permutation (choose {k_up} of {len(day_keys)} block days): p={perm_p:.4f}")

    # nulls
    up_days = {day_of(t["block_ts_ms"]) for t in gated}
    # regime-up days over the whole window (from the daily series, day-level)
    all_days = sorted({t["day"] for t in xyz})
    d0 = datetime.strptime(all_days[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(all_days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    regime_up_days = set()
    d = d0
    while d <= d1:
        r = kday_ret(EQ, int(d.timestamp() * 1000) + 12 * HOUR, 7)
        if r is not None and r > 0:
            regime_up_days.add(d.date())
        d = datetime.fromtimestamp(d.timestamp() + 86400, tz=timezone.utc)
    pools_a, pools_b = build_null_pools(xyz, regime_up_days)
    real_ev = st.mean([t["net25"] for t in gated])
    pa, ma = null_p(gated, pools_a, rng, real_ev)
    pb, mb = null_p(gated, pools_b, rng, real_ev)
    print(f"  null A (any-time same-coin):    mc_p={pa:.4f}  null-mean={ma*100:+.2f}%  excess={(real_ev-ma)*100:+.2f}pp")
    print(f"  null B (up-regime-time only):   mc_p={pb:.4f}  null-mean={mb*100:+.2f}%  excess={(real_ev-mb)*100:+.2f}pp")

    # flow: trades per week taken vs forgone
    weeks = max(1e-9, (d1 - d0).days + 1) / 7
    print(f"  flow: {len(gated)/weeks:.1f} taken/wk vs {len(blocked)/weeks:.1f} forgone/wk "
          f"({(d1-d0).days+1} days)")

    results["gate"] = {
        "taken": stats(gated), "taken_net_funding": st.mean([t["net25f"] for t in gated]),
        "taken_live_stop": st.mean([t["net25_stop"] for t in gated]),
        "forgone": stats(blocked),
        "oos_h1": h1s, "oos_h2": h2s,
        "nonoverlap": stats(g_ind),
        "ex_top_coin": stats(ex_top), "top_coin": top[0],
        "n_days": len(byday), "day_equal_ev": st.mean(daymeans),
        "null_a": {"p": pa, "mean": ma}, "null_b": {"p": pb, "mean": mb},
        "per_week_taken": len(gated) / weeks, "per_week_forgone": len(blocked) / weeks,
    }

    # crypto side (descriptive only, n tiny)
    if bare:
        print(f"\n  crypto (bare) episodes, descriptive: {fmt(stats(bare))} "
              f"| btc7-up subset: {fmt(stats([t for t in bare if t['btc7'] and t['btc7'] > 0]))}")

    # forward ledger cross-check (young_mover_short.jsonl, wired 2026-07-20)
    led_path = str(Path(__file__).resolve().parents[3] / ".state" / "shadow_ledger" / "young_mover_short.jsonl")
    if os.path.exists(led_path):
        print("\n== forward ledger cross-check (young_mover_short book) ==")
        rows = [json.loads(l) for l in open(led_path) if l.strip()]
        graded = []
        for r in rows:
            s = sim_episode(r["coin"], r["ts"])
            if s:
                eq7 = kday_ret(EQ, r["ts"], 7)
                graded.append({"coin": r["coin"], "net25": s["gross"] - RT_COST,
                               "day": day_of(r["ts"]).isoformat(),
                               "regime": (r.get("meta") or {}).get("macro_regime"),
                               "eq7_up": None if eq7 is None else eq7 > 0})
        for tag in ("up", "neutral", "down", None):
            sub = [g for g in graded if g["regime"] == tag]
            if sub:
                print(f"  macro_regime={tag}: {fmt(stats(sub))}")
        # what would the PROPOSED gate (eq7>0) have done to these rows?
        gt = [g for g in graded if g["eq7_up"] is True]
        gb = [g for g in graded if g["eq7_up"] is False]
        print(f"  proposed gate eq7>0 on forward rows: TAKEN {fmt(stats(gt))} | "
              f"BLOCKED {fmt(stats(gb))}")
        for g in sorted(graded, key=lambda x: x["day"]):
            print(f"    {g['day']} {g['coin']:<14} eq7_up={g['eq7_up']} "
                  f"tag={g['regime']} net={g['net25']*100:+6.2f}%")
        results["forward_ledger"] = {"n_rows": len(rows), "n_graded": len(graded),
                                     "all": stats(graded),
                                     "gate_taken": stats(gt), "gate_blocked": stats(gb)}

    with open(os.path.join(HERE, "W-Y4_results.json"), "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(f"\nresults -> {os.path.join(HERE, 'W-Y4_results.json')}")


if __name__ == "__main__":
    main()
