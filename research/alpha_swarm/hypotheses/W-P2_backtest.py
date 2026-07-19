"""W-P2 event study: scheduled policy binaries -> post-resolution BTC/ETH drift.

Everything here executes the spec pre-registered in
findings/W-P2_scheduled_catalyst.md (incl. amendments 1-5). No parameter was
chosen after seeing these outputs.

Inputs: W-P2_events.json, W-P2_cache_polymarket.json, W-P2_cache_1h_binance.json
Output: W-P2_results.json + printed tables.
"""
from __future__ import annotations

import json
import os
from bisect import bisect_left
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0025                 # 25 bps RT, primary
SLIP_TIERS = [0.0, 0.0006, 0.0012, 0.0025, 0.0050]
HORIZONS = [1, 4, 24, 72]
N_NULL = 2000
RNG = np.random.default_rng(20260719)

events = json.load(open(os.path.join(HERE, "W-P2_events.json")))["events"]
pm = json.load(open(os.path.join(HERE, "W-P2_cache_polymarket.json")))
bn = json.load(open(os.path.join(HERE, "W-P2_cache_1h_binance.json")))

opens, times = {}, {}
for coin in ("BTC", "ETH"):
    bars = bn[coin]
    t = np.array([b["t"] for b in bars], dtype=np.int64)
    o = np.array([b["o"] for b in bars])
    assert np.all(np.diff(t) == 3_600_000), f"{coin}: bars not contiguous"
    times[coin], opens[coin] = t, o
assert np.array_equal(times["BTC"], times["ETH"])
T = times["BTC"]
N = len(T)

def leg_ret(coin: str, i: int, h: int) -> float | None:
    j = i + h
    if j >= N:
        return None
    return opens[coin][j] / opens[coin][i] - 1.0


def bucket_of(p: float | None) -> str:
    if p is None:
        return "NA"
    if p > 0.85:
        return "priced"
    if p >= 0.40:
        return "contested"
    return "upset"


rows = []
for e in events:
    rec = pm.get(e["id"], {})
    ts_ms = int(datetime.fromisoformat(e["ts_utc"]).timestamp() * 1000)
    if rec.get("pin_ts"):
        ts_ms = int(rec["pin_ts"]) * 1000
    i = bisect_left(T, ts_ms)          # first bar open >= ts (bar containing ts skipped)
    if i >= N:
        raise RuntimeError(f"{e['id']}: no bar after ts")
    vol = float(rec.get("volume") or 0)
    row = {
        "id": e["id"], "name": e["name"], "sign": e["sign"], "ts_ms": ts_ms,
        "entry_idx": i, "entry_delay_min": (int(T[i]) - ts_ms) / 60000.0,
        "p24": rec.get("p_t24h"), "p1": rec.get("p_t1h"),
        "bucket": bucket_of(rec.get("p_t24h")),
        "qual_surprise": e["outcome"] in ("fail", "blocked"),
        "importance": e["stage"] + e["scope"] + (1 if vol >= 1e6 else 0),
        "allow_1h": e["allow_1h"],
    }
    for h in HORIZONS:
        if h == 1 and not e["allow_1h"]:
            row[f"r{h}"] = None
            continue
        legs = [leg_ret(c, i, h) for c in ("BTC", "ETH")]
        if any(x is None for x in legs):
            row[f"r{h}"] = None
        else:
            row[f"r{h}"] = e["sign"] * float(np.mean(legs))   # raw signed basket, pre-cost
            row[f"r{h}_btc"] = e["sign"] * legs[0]
            row[f"r{h}_eth"] = e["sign"] * legs[1]
    rows.append(row)

rows.sort(key=lambda r: r["ts_ms"])


def cell(sel, h, cost=COST):
    xs = [r[f"r{h}"] - cost for r in sel if r.get(f"r{h}") is not None]
    if not xs:
        return None
    return {"n": len(xs), "ev": float(np.mean(xs)), "win": float(np.mean([x > 0 for x in xs])),
            "med": float(np.median(xs))}


def null_p(sel, h, cost=COST):
    """>=2000 draws; per event a uniform random entry bar, same sign/horizon,
    same timestamp both legs (basket preserved). One-sided MC p for the mean."""
    sel = [r for r in sel if r.get(f"r{h}") is not None]
    if not sel:
        return None, None
    max_i = N - 72 - 1
    signs = np.array([r["sign"] for r in sel])
    idx = RNG.integers(0, max_i, size=(N_NULL, len(sel)))
    basket = 0.5 * ((opens["BTC"][idx + h] / opens["BTC"][idx] - 1.0)
                    + (opens["ETH"][idx + h] / opens["ETH"][idx] - 1.0))
    null_means = (signs[None, :] * basket - cost).mean(axis=1)
    obs = np.mean([r[f"r{h}"] - cost for r in sel])
    p_signed = float((null_means >= obs).mean())
    # unsigned: does the event move price at all (pre-cost magnitudes)
    obs_abs = np.mean([abs(r[f"r{h}"]) for r in sel])
    null_abs = np.abs(basket).mean(axis=1)
    p_unsigned = float((null_abs >= obs_abs).mean())
    return p_signed, p_unsigned


def report(label, sel, hs=HORIZONS):
    out = {}
    for h in hs:
        c = cell(sel, h)
        if not c:
            continue
        ps, pu = null_p(sel, h)
        c["p_signed"] = ps
        c["p_unsigned"] = pu
        out[f"h{h}"] = c
        print(f"  {label:28s} +{h:>2}h n={c['n']:>2} EV={c['ev']*100:+.2f}% "
              f"win={c['win']:.2f} p_sig={ps:.3f} p_unsig={pu:.3f}")
    return out


print("== per-event (+24h basket net 25bps) ==")
for r in rows:
    r24 = r.get("r24")
    print(f"  {r['id']} {datetime.fromtimestamp(r['ts_ms']/1000, tz=timezone.utc):%Y-%m-%d %H:%M}Z "
          f"sign={r['sign']:+d} dly={r['entry_delay_min']:4.0f}m bkt={r['bucket']:9s} "
          f"imp={r['importance']} "
          f"r24={'None' if r24 is None else f'{(r24-COST)*100:+.2f}%'} "
          f"r1={'skip' if not r['allow_1h'] else f'{(r['r1']-COST)*100:+.2f}%'}")

results = {"n_events": len(rows), "rows": rows, "cells": {}}
print("\n== cells (net 25bps, basket) ==")
results["cells"]["all"] = report("ALL", rows)
results["cells"]["odds_covered"] = report("odds-covered", [r for r in rows if r["bucket"] != "NA"])
results["cells"]["contested_upset"] = report("PRIMARY contested+upset", [r for r in rows if r["bucket"] in ("contested", "upset")])
results["cells"]["priced"] = report("priced", [r for r in rows if r["bucket"] == "priced"])
results["cells"]["odds_na"] = report("unconditioned (odds NA)", [r for r in rows if r["bucket"] == "NA"])
results["cells"]["qual_surprise"] = report("EXPL qual-surprise (fails)", [r for r in rows if r["qual_surprise"]])
results["cells"]["qual_expected"] = report("EXPL passes", [r for r in rows if not r["qual_surprise"]])
results["cells"]["imp_high"] = report("importance HIGH (>=4)", [r for r in rows if r["importance"] >= 4])
results["cells"]["imp_mid"] = report("importance MID (=3)", [r for r in rows if r["importance"] == 3])
results["cells"]["imp_low"] = report("importance LOW (<=2)", [r for r in rows if r["importance"] <= 2])

print("\n== OOS halves (ALL, by time) ==")
half = len(rows) // 2
for tag, sel in (("H1(early)", rows[:half]), ("H2(late)", rows[half:])):
    results["cells"][f"oos_{tag}"] = report(tag, sel)

print("\n== per-instrument (ALL, +24h net) ==")
for coin in ("btc", "eth"):
    xs = [r[f"r24_{coin}"] - COST for r in rows if r.get(f"r24_{coin}") is not None]
    print(f"  {coin.upper()}: EV={np.mean(xs)*100:+.2f}% win={np.mean([x>0 for x in xs]):.2f} n={len(xs)}")
    results["cells"][f"instr_{coin}_h24"] = {"ev": float(np.mean(xs)), "n": len(xs)}

print("\n== slippage sweep (ALL, +24h) ==")
for s in SLIP_TIERS:
    xs = [r["r24"] - s for r in rows if r.get("r24") is not None]
    print(f"  {s*1e4:>4.0f}bps EV={np.mean(xs)*100:+.2f}%")

json.dump(results, open(os.path.join(HERE, "W-P2_results.json"), "w"), indent=1, default=float)
print("\nwrote W-P2_results.json")
