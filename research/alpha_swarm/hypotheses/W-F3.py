"""W-F3 funding_settlement_micro — HL funding settles HOURLY. Is there systematic
price drift into / out of a settlement whose payment is extreme, conditional on the
funding rate? (NOT the refuted unconditional 22:00 time-of-day — this is conditional
on the funding state.)

Mechanism candidates: longs de-risk just before paying an extreme positive rate
(sell pressure INTO settlement, relief pop OUT of it); shorts mirror on extreme
negative rates.

Lookahead-safe: conditioning signal = the rate SETTLED at hour t-1 (published then;
HL hourly funding is highly persistent so it proxies the upcoming payment at t).
Windows measured on 1h candles: INTO = bar [t-1,t) open->close, OUT = bar [t,t+1)
open->close, OUT3 = open(t) -> close(t+2).
Events: per-coin z of the hourly rate vs its own trailing 30d hourly distribution,
|z| >= 3 (and rate above a floor to dodge the 1.25e-05 default). Consecutive extreme
hours are ONE episode (first hour only). Null: mc_null vs the all-eligible-bars pool
of the same window, both directions. 3000 iters.
"""
from __future__ import annotations
import bisect, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import alpha_lib as al
import funding_lib as fl
import mc_null

d = al.load_dataset()
f = fl.load_funding()
HOUR = 3_600_000

COINS = [c for c in d["coins"] if fl.rows(f, c) and al.candles(d, c, "1h")]

# hour-aligned structures
CH = {}
for c in COINS:
    CH[c] = {b[al.T]: b for b in al.candles(d, c, "1h")}
FR = {}  # coin -> {hour_ts: rate}   (row stamped t+~33ms belongs to settlement t)
for c in COINS:
    FR[c] = {}
    for r in fl.rows(f, c):
        hr = (r[0] // HOUR) * HOUR
        FR[c][hr] = r[1]

RATE_FLOOR = 5e-5  # 4x the 1.25e-05 baseline; ~0.12%/day — a rate someone would act on

def events_and_pool(z_thresh=3.0):
    ev = {"pos": [], "neg": []}
    pool = {"into": [], "out": [], "out3": []}
    for c in COINS:
        hours = sorted(set(CH[c]) & set(FR[c]))
        rates = FR[c]
        # trailing z: rolling window of prior 720 rates (30d)
        hist: list[float] = []
        in_ep = False
        for t in hours:
            r_prev = rates.get(t - HOUR)
            b_into = CH[c].get(t - HOUR)
            b_out = CH[c].get(t)
            b_out3 = CH[c].get(t + 2 * HOUR)
            if r_prev is not None:
                hist.append(r_prev)
                if len(hist) > 720:
                    hist.pop(0)
            if (r_prev is None or b_into is None or b_out is None or b_out3 is None
                    or not b_into[al.O] or not b_out[al.O] or len(hist) < 240):
                continue
            w = {"into": al.pct(b_into[al.O], b_into[al.C]),
                 "out": al.pct(b_out[al.O], b_out[al.C]),
                 "out3": al.pct(b_out[al.O], b_out3[al.C])}
            for k in pool:
                pool[k].append(w[k])
            m = statistics.mean(hist)
            s = statistics.pstdev(hist) + 1e-12
            z = (r_prev - m) / s
            extreme = abs(z) >= z_thresh and abs(r_prev) >= RATE_FLOOR
            if not extreme:
                in_ep = False
                continue
            if in_ep:
                continue
            in_ep = True
            ev["pos" if r_prev > 0 else "neg"].append({"c": c, "t": t, **w})
    return ev, pool

def test(obs, pool, label):
    if len(obs) < 8:
        print(f"   {label}: n={len(obs)} thin")
        return
    mu = statistics.mean(obs)
    up = mc_null.shuffle_label_p(obs, pool, n_iter=3000, seed=13)
    dn = mc_null.shuffle_label_p([-x for x in obs], [-x for x in pool],
                                 n_iter=3000, seed=14)
    print(f"   {label}: n={len(obs)} mean={1e4*mu:+.1f}bps "
          f"pool={1e4*statistics.mean(pool):+.1f}bps "
          f"p_up={up['p_one_sided']} p_down={dn['p_one_sided']}")

def cluster_report(z_thresh=3.0):
    """Cross-coin clustering check: same market hour firing many coins is ONE bet,
    not many. Collapse events to per-hour clusters (mean across coins in the hour)
    and per-UTC-day clusters, and re-run the null on the collapsed series for the
    tradeable windows. Also a realistic combined trade: long at t-1 open (right
    after the extreme negative settlement), exit at close of bar t (2h hold),
    + funding collected at t (a long on negative funding RECEIVES |rate|)."""
    ev, pool = events_and_pool(z_thresh)
    es = ev["neg"]
    print(f"--- cluster check, neg side, |z|>={z_thresh}: raw n={len(es)}")
    hours = {}
    days = {}
    for e in es:
        hours.setdefault(e["t"], []).append(e)
        days.setdefault(e["t"] // (24 * HOUR), []).append(e)
    print(f"    distinct hours={len(hours)} distinct UTC days={len(days)} "
          f"max coins in one hour={max(len(v) for v in hours.values())} "
          f"max events in one day={max(len(v) for v in days.values())}")
    # combined 2h trade per event: into+out compounded + funding received at t
    def combo(e):
        fr = FR[e["c"]].get(e["t"], 0.0)
        return (1 + e["into"]) * (1 + e["out"]) - 1 + max(0.0, -fr)
    pool_combo = [(1 + a) * (1 + b) - 1 for a, b in zip(pool["into"], pool["out"])]
    for label, series in (
        ("per-event combo", [combo(e) for e in es]),
        ("per-HOUR-cluster combo", [statistics.mean(combo(e) for e in v)
                                    for v in hours.values()]),
        ("per-DAY-cluster combo", [statistics.mean(combo(e) for e in v)
                                   for v in days.values()]),
    ):
        if len(series) < 8:
            print(f"    {label}: n={len(series)} thin")
            continue
        res = mc_null.shuffle_label_p(series, pool_combo, n_iter=3000, seed=17)
        print(f"    {label}: n={len(series)} mean={1e4*statistics.mean(series):+.1f}bps"
              f" net12={1e4*statistics.mean(series)-12:+.1f} net25={1e4*statistics.mean(series)-25:+.1f}"
              f" p={res['p_one_sided']}")
    # OOS halves on day-clusters (time-ordered)
    dk = sorted(days)
    dser = [statistics.mean(combo(e) for e in days[k]) for k in dk]
    half = len(dser) // 2
    if half >= 4:
        print(f"    day-cluster OOS: h1={1e4*statistics.mean(dser[:half]):+.1f}bps "
              f"h2={1e4*statistics.mean(dser[half:]):+.1f}bps")

if __name__ == "__main__":
    for zt in (2.5, 3.0):
        ev, pool = events_and_pool(zt)
        print(f"=== W-F3 settlement micro, |z|>={zt}, rate floor {RATE_FLOOR} ===")
        print(f" positive-funding events (longs pay): n={len(ev['pos'])}")
        for k in ("into", "out", "out3"):
            test([e[k] for e in ev["pos"]], pool[k], f"pos {k:5}")
        print(f" negative-funding events (shorts pay): n={len(ev['neg'])}")
        for k in ("into", "out", "out3"):
            test([e[k] for e in ev["neg"]], pool[k], f"neg {k:5}")
        # OOS halves for any window that looks alive gets checked in the findings.
        for side in ("pos", "neg"):
            es = sorted(ev[side], key=lambda e: e["t"])
            if len(es) >= 16:
                half = len(es) // 2
                for k in ("into", "out", "out3"):
                    h1 = 1e4 * statistics.mean([e[k] for e in es[:half]])
                    h2 = 1e4 * statistics.mean([e[k] for e in es[half:]])
                    print(f"   OOS {side} {k:5}: h1={h1:+.1f}bps h2={h2:+.1f}bps")
        print()
    for zt in (2.5, 3.0):
        cluster_report(zt)
