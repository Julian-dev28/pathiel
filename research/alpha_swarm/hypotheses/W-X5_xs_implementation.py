#!/usr/bin/env python3
"""W-X5 — xs implementation frontier. PRE-REGISTERED before first run (swarm rules).

The signal is SETTLED (pct_k14 rank; meme-excluded eligible set per W-X4 b02276b =
the live baseline as of 18622d3). Nothing here proposes a new signal — every cell is
about EXPRESSION: depth, capital, tranching, turnover, and the xyz book's parameters.

DATA: W-X2_cache_daily.json (fetched 2026-07-20, 401 daily bars, top-50 crypto by
dayNtlVlm + 87 xyz markets) — REUSED, not refetched. Shared W-X2 engine imported
(decide bar i, fill open[i+1], exit open[i+1+H], non-overlapping, equal weight,
>=61-bar eligibility, start=66). seed=42, N_NULL=2000 matched random books.
Costs: 0/12/25/50bps ROUND-TRIP per replaced position, turnover-scaled. OOS =
first/second half of rebalances (count halves — engine convention; calendar-
approximate across different H). Survivor-biased cache: every + is an upper bound.
W-X5_cache_meta.json: one-shot HL meta (per-coin maxLeverage), cached 2026-07-20.

BASELINE for every crypto cell = the live config at 18622d3: pct_k14, k=4/leg,
H=10, top-50 minus the 20 declared SECTOR_MAP MEME names (W-X2_xs_widening.py),
ungated sim. Reproduction gate: must match W-X4 PRIMARY to the digit
(gross +3.88 / net25 +3.68, n=33) before any cell is scored.

DOMINANCE CONVENTIONS (pre-registered)
  same-H vs baseline : W-X4 strict dominance — net25 EV AND net25 Sharpe-like
                       (mean/pstdev), BOTH count-halves. 4/4 or keep the live config.
  cross-H vs baseline: per-DAY net25 EV (net25/H) AND ANNUALIZED Sharpe
                       (Sh_rebal × sqrt(365/H)), BOTH halves. Same 4/4 bar.
  Dominance and significance are SEPARATE: nulls (2000 matched draws, gross) are
  reported per cell as the reality check on the book itself; the wire decision is
  dominance vs baseline. A dominant cell that is not expressible at current equity
  (capital table) is a growth-ladder entry, NOT a wire-now.

CELL 1 — depth x hold frontier: {k4,k6,k8} x {H10,H20}, meme-excluded, pct_k14.
  W-X2-D scored this UNfiltered at k8/H20 (+4.28% gross); re-score on the W-X4 set.
  $-lines at FIXED margin envelope (util 0.8 x E x mean effective leverage of the
  eligible set) so depth doesn't fake $-growth: $/wk = net25 x G_env x 7/H.
  CAPITAL TABLE (deterministic, from live mechanics — executor.py:609,635):
    per-leg notional = frac x E x lev_eff(coin), lev_eff = min(12, HL maxLeverage);
    per-leg MARGIN   = frac x E exactly (leverage cancels);
    to express 2k legs at util budget U=0.8 (the live k4 envelope): frac_k = 0.4/k;
    min-order $10.50 binds FIRST on the lowest-cap eligible coins (lev_min=3:
    ACE/AZTEC/SUSHI/VVV class) => E_min_strict(k) = 10.50 x k / (0.4 x lev_min).
    Leg-quality tiers (declared): STRICT = every leg >= $10.50; CLEAN = every leg
    >= $21.00 (2x min order — rounding/precision headroom). 2-tranche variants
    halve frac => double both thresholds. Deliverable: E_min per (k, tranches),
    leg sizes at E in {65,150,300}, and a pre-committed growth ladder.
  Honesty note logged, not tested: live legs are LEVERAGE-CAP-WEIGHTED (a 3x-cap
    coin gets 0.3x the notional of a 12x coin at equal margin), while all sims
    (W-X2/X3/X4 and this file) score EQUAL-WEIGHT books. Divergence is structural
    in the current executor path and applies to the live book TODAY.

CELL 2 — tranche staggering (structural variance, not signal): split the book into
  2 tranches, each the FULL recipe (meme-excluded pct_k14 k4 H10) at HALF notional,
  rebalanced offset by H/2=5 bars (A at 66,76,...; B at 71,81,...).
  Valuation on the 5-bar grid: each rebalance's H-period fwd is split EXACTLY on
  entry-notional basis: sub1 = mark(i+6)/open(i+1) - 1, sub2 = fwd - sub1 (identity
  asserted); mark = open of bar at i+6, else close of latest bar <= that day
  (fallback counted). Fees at each tranche's own rebalance, 0.5-weighted.
  Grid trimmed to periods where BOTH schemes are fully deployed.
  Metrics: per-H net25 EV (drag vs lump), grid-series Sharpe (both schemes, same
  grid), worst single rebalance (per-tranche net25, full-notional units), worst
  single grid period, maxDD of the cumulative net25 curve. WIRE-WORTHY iff grid
  Sharpe higher in BOTH halves AND |EV drag| <= 10% of lump per-H net25 AND maxDD
  not worse AND expressible at current E (capital table; tranche legs halve).

CELL 3 — rank-hysteresis turnover buffer: incumbent legs keep their slot until they
  exit the top/bottom (k+b) ranks, b in {1,2,4}; vacated slots filled by the best-
  ranked non-incumbents. b=0 must equal the baseline engine EXACTLY (asserted on
  synthetic AND real data). Same rebalance dates as baseline => same-H dominance
  gate applies directly: PRIMARY gate = net25 strict dominance (EV+Sharpe, both
  halves); net50 reported (a net50-only winner = MARGINAL, fee-regime-dependent).
  Report turnover reduction % per b. Side-flips allowed (a fallen long may be
  picked as a new short — engine behavior); long/short disjointness asserted.

CELL 4 — xyz book hardening (live book: xs_xyz_equities, resid7/k5/H5/XYZ100 per
  .agent-config.json): sensitivity sweep hold {3,5,10} x k {3,5} x benchmark
  {xyz:XYZ100, EW_UNIV} on the SAME cached xyz data, score = 7d residual momentum.
  EW_UNIV = synthetic equal-weight index of ALL declared xyz equity names (pre-
  eligibility): for consecutive grid days, members = names with closes on both
  days; daily ret = mean member ret; >=5 members else flat; index starts 100;
  injected as a virtual coin (bars: open=prev close, close=index). Cross-H
  dominance convention as above, PLUS the neighbor itself must have null p<0.05.
  If a neighbor dominates the live cell 4/4 => spec-change PROPOSAL (W-X4 bar),
  never an automatic change.

Read-only research. No live wiring. One script, findings/W-X5_<cell>.md x4.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = str(Path(__file__).resolve().parents[3])
HYP = os.path.join(REPO, "research", "alpha_swarm", "hypotheses")

_spec = importlib.util.spec_from_file_location("wx2", os.path.join(HYP, "W-X2_xs_widening.py"))
wx2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx2)

T, O, H_, L_, C, V = wx2.T, wx2.O, wx2.H_, wx2.L_, wx2.C, wx2.V

MEMES = {c for c, s in wx2.SECTOR_MAP.items() if s == "MEME"}
LIVE_K, LIVE_H = 4, 10
MIN_ORDER = 10.50
UTIL = 0.8                # live k4 envelope: 2k x 0.10 = 0.8 of funding-dex equity
E_GRID = [65.0, 150.0, 300.0]
META_CACHE = os.path.join(HYP, "W-X5_cache_meta.json")

# W-X4 PRIMARY reproduction targets (findings/W-X4_meme_exclusion.md)
REPRO_GROSS, REPRO_NET25 = 3.88, 3.68


# ---------------------------------------------------------------------------- shared
def crypto_world(d: dict):
    w, coins = wx2.make_crypto_world(d)
    no_meme = [c for c in coins if c not in MEMES]
    return w, coins, no_meme


def net25_series(recs: List[dict]) -> List[float]:
    return [r["ev"] - 0.0025 * r["turnover"] for r in recs]


def netX_series(recs: List[dict], tier: float) -> List[float]:
    return [r["ev"] - tier * r["turnover"] for r in recs]


def ev_sh(xs: List[float]) -> Tuple[float, float]:
    return statistics.mean(xs), statistics.mean(xs) / (statistics.pstdev(xs) + 1e-12)


def halves(xs: List[float]) -> Tuple[List[float], List[float]]:
    h = len(xs) // 2
    return xs[:h], xs[h:]


def dom_metrics(recs: List[dict], hold: int, tier: float = 0.0025) -> dict:
    """Per-half {EV/rebal, EV/day, Sh/rebal, Sh_annualized} for the cross-H gate."""
    s = netX_series(recs, tier)
    out = {"n": len(s)}
    for name, part in (("full", s),) + tuple(zip(("h1", "h2"), halves(s))):
        e, sh = ev_sh(part)
        out[name] = {"ev": e, "ev_day": e / hold, "sh": sh,
                     "sh_ann": sh * math.sqrt(365.0 / hold)}
    return out


def cross_h_dominance(var: dict, base: dict) -> Tuple[bool, List[str]]:
    """4/4: per-day EV AND annualized Sharpe beat baseline in BOTH halves."""
    checks, ok = [], True
    for half in ("h1", "h2"):
        for key, label in (("ev_day", "EV/day"), ("sh_ann", "Sh_ann")):
            win = var[half][key] > base[half][key]
            ok &= win
            checks.append(f"{half} {label}: {'WIN' if win else 'LOSE'} "
                          f"({var[half][key]:+.4f} vs {base[half][key]:+.4f})")
    return ok, checks


# ---------------------------------------------------------------------------- capital
def load_lev(top50: List[str]) -> Dict[str, int]:
    m = json.load(open(META_CACHE))["maxLeverage"]
    missing = [c for c in top50 if c not in m]
    assert not missing, f"meta cache missing {missing}"
    return {c: int(m[c]) for c in top50}


def capital_table(top50: List[str]) -> dict:
    lev = load_lev(top50)
    elig = [c for c in top50 if c not in MEMES]
    eff = {c: min(12, lev[c]) for c in elig}
    lev_min = min(eff.values())
    mean_lev = statistics.mean(eff.values())
    rows = []
    for k in (4, 6, 8):
        for tranches in (1, 2):
            frac = UTIL / (2 * k) / tranches          # per-leg margin fraction of E
            e_strict = MIN_ORDER / (frac * lev_min)
            e_clean = 2 * MIN_ORDER / (frac * lev_min)
            legs = {E: (frac * E * lev_min, frac * E * 10, frac * E * 12) for E in E_GRID}
            rows.append({"k": k, "tranches": tranches, "frac": frac,
                         "E_min_strict": e_strict, "E_min_clean": e_clean,
                         "legs": legs, "margin_util": UTIL,
                         "max_gross_x": frac * 2 * k * tranches * 12})
    return {"lev_min": lev_min, "mean_lev": mean_lev, "eff": eff, "rows": rows,
            "low_cap": sorted((v, c) for c, v in eff.items())[:8]}


def print_capital(cap: dict) -> None:
    print(f"\n----- CAPITAL TABLE (executor mechanics: leg = frac x E x min(12,cap); "
          f"margin/leg = frac x E) -----")
    print(f"eligible (meme-excluded) lev_eff: min {cap['lev_min']}x, "
          f"mean {cap['mean_lev']:.2f}x; lowest caps: "
          + ", ".join(f"{c} {v}x" for v, c in cap["low_cap"]))
    print(f"util budget U={UTIL} (live k4 envelope). frac_k = {UTIL}/2k/tranches.")
    print(f"{'k':<3}{'trn':<4}{'frac':<8}{'E_min strict':<14}{'E_min clean':<13}"
          f"{'legs@$65 (3x/10x/12x)':<24}{'legs@$150':<22}{'legs@$300':<22}{'maxGross'}")
    for r in cap["rows"]:
        legs = {E: "/".join(f"${x:.0f}" for x in v) for E, v in r["legs"].items()}
        print(f"{r['k']:<3}{r['tranches']:<4}{r['frac']:<8.4f}"
              f"${r['E_min_strict']:<13.2f}${r['E_min_clean']:<12.2f}"
              f"{legs[65.0]:<24}{legs[150.0]:<22}{legs[300.0]:<22}"
              f"{r['max_gross_x']:.1f}x")


# ---------------------------------------------------------------------------- cell 1
def cell1(d: dict) -> dict:
    w, coins, no_meme = crypto_world(d)
    pk = lambda c, i: wx2.pctk(w, c, i, 14)
    elig = wx2.elig_std(w, no_meme, 61)
    cap = capital_table(coins)
    g_env = UTIL * 65.0 * cap["mean_lev"]     # fixed margin envelope at current E
    out = {"rows": [], "cap": cap, "g_env": g_env}
    books = {}
    for k in (4, 6, 8):
        for hold in (10, 20):
            res = wx2.run_book(w, elig, pk, k, hold)
            books[(k, hold)] = res
            s = wx2.summarize_book(res, k, hold, f"C1 memex pct_k14 k{k} H{hold}")
            s["usd_week_env"] = round(statistics.mean(net25_series(res["recs"]))
                                      * g_env * 7 / hold, 2)
            s["dom"] = dom_metrics(res["recs"], hold)
            out["rows"].append(s)
            print(wx2.fmt(s) + f"  $env {s['usd_week_env']:+.2f}/wk")
    base = books[(LIVE_K, LIVE_H)]
    s0 = [r for r in out["rows"] if r["k"] == LIVE_K and r["hold"] == LIVE_H][0]
    assert abs(s0["gross_pct"] - REPRO_GROSS) <= 0.01 and \
        abs(s0["net25"] - REPRO_NET25) <= 0.01, \
        f"W-X4 reproduction FAILED: {s0['gross_pct']}/{s0['net25']}"
    print(f"  reproduction check vs W-X4 PRIMARY (+{REPRO_GROSS}/+{REPRO_NET25}): OK")
    bdom = dom_metrics(base["recs"], LIVE_H)
    print("\n----- dominance vs baseline k4/H10 (per-day EV + annualized Sharpe, both halves) -----")
    out["dominance"] = {}
    for s in out["rows"]:
        if s["k"] == LIVE_K and s["hold"] == LIVE_H:
            continue
        ok, checks = cross_h_dominance(s["dom"], bdom)
        out["dominance"][(s["k"], s["hold"])] = (ok, checks)
        print(f"  k{s['k']}/H{s['hold']}: {'STRICT DOMINANCE 4/4' if ok else 'FAILS'}")
        for c_ in checks:
            print(f"    {c_}")
    # POST-HOC DIAGNOSTIC (declared as such): offset-0 is the baseline's most
    # flattering phase (cell 2 sweep). Phase-mean per-day net25 over a full phase
    # cycle per cell = the phase-robust frontier. Gate stays offset-0 (convention).
    print("\n----- post-hoc: phase-mean net25 per cell (offsets 0..H-1, no nulls) -----")
    out["phase"] = {}
    for k in (4, 6, 8):
        for hold in (10, 20):
            vals = []
            for o in range(hold):
                r = wx2.run_book(w, elig, pk, k, hold, start=66 + o)
                vals.append(statistics.mean(net25_series(r["recs"])))
            pm = statistics.mean(vals)
            out["phase"][(k, hold)] = vals
            print(f"  k{k}/H{hold}: phase-mean {100*pm:+.2f}%/rebal "
                  f"({100*pm/hold:+.3f}%/day)  min {100*min(vals):+.2f}  "
                  f"max {100*max(vals):+.2f}  offset0 {100*vals[0]:+.2f}")
    # POST-HOC DIAGNOSTIC (declared as such): the LIVE book is not the equal-weight
    # book the sims score — executor legs are lev-cap-weighted (leg = frac x E x
    # lev_eff). Score that replica: within each side, weights proportional to
    # lev_eff. Same names, same fills, same turnover; only the weights differ.
    eff = cap["eff"]
    ew_v, lw_v = [], []
    for o in range(LIVE_H):
        r = wx2.run_book(w, elig, pk, LIVE_K, LIVE_H, start=66 + o)
        ew_v.append(statistics.mean(net25_series(r["recs"])))
        lws = []
        for rec in r["recs"]:
            def wmean(cs):
                tot = sum(eff[c] for c in cs)
                return sum(eff[c] * rec["fwd"][c] for c in cs) / tot
            ev = 0.5 * wmean(rec["longs"]) - 0.5 * wmean(rec["shorts"])
            lws.append(ev - 0.0025 * rec["turnover"])
        lw_v.append(statistics.mean(lws))
    print(f"\n----- post-hoc: equal-weight sim vs lev-cap-weighted LIVE replica "
          f"(k4/H10, net25 phase-mean over 10 offsets) -----")
    print(f"  equal-weight {100*statistics.mean(ew_v):+.2f}%/rebal   "
          f"lev-weighted {100*statistics.mean(lw_v):+.2f}%/rebal   "
          f"delta {100*(statistics.mean(lw_v)-statistics.mean(ew_v)):+.3f}%  "
          f"(lev-weighted wins {sum(1 for a,b in zip(lw_v,ew_v) if a>b)}/10 offsets)")
    out["lev_weight"] = {"ew": ew_v, "lw": lw_v}
    print_capital(cap)
    return out


# ---------------------------------------------------------------------------- cell 2
def sub_split(w, rec: dict, half: int) -> Tuple[dict, dict, int]:
    """Exact entry-notional split of each leg's fwd at the H/2 mark. Returns
    (sub1 per coin, sub2 per coin, fallback count)."""
    i = rec["i"]
    s1, s2, fb = {}, {}, 0
    for c in set(rec["longs"]) | set(rec["shorts"]):
        o1 = w.bar(c, i + 1)[O]
        bm = w.bar(c, i + 1 + half)
        if bm is not None:
            mark = bm[O]
        else:
            mark = w.closes_upto(c, i + 1 + half, 1)[0]
            fb += 1
        r1 = mark / o1 - 1.0
        s1[c] = r1
        s2[c] = rec["fwd"][c] - r1          # exact: sub1 + sub2 == fwd
    return s1, s2, fb


def book_ev(rec: dict, per_coin: Dict[str, float]) -> float:
    return 0.5 * statistics.mean([per_coin[c] for c in rec["longs"]]) \
        - 0.5 * statistics.mean([per_coin[c] for c in rec["shorts"]])


def tranche_scheme(w, elig_fn, score_fn, k: int, hold: int, tier: float = 0.0025,
                   start: int = 66) -> dict:
    half = hold // 2
    A = wx2.run_book(w, elig_fn, score_fn, k, hold, start=start)
    B = wx2.run_book(w, elig_fn, score_fn, k, hold, start=start + half)
    grid: Dict[int, dict] = {}          # decision-day index -> contributions
    fb_tot = 0

    def add(res, key):
        nonlocal fb_tot
        for rec in res["recs"]:
            s1, s2, fb = sub_split(w, rec, half)
            fb_tot += fb
            e1, e2 = book_ev(rec, s1), book_ev(rec, s2)
            assert abs((e1 + e2) - rec["ev"]) < 1e-12
            fee = tier * rec["turnover"]
            grid.setdefault(rec["i"], {})[key] = e1 - fee
            grid.setdefault(rec["i"] + half, {})[key + "_c"] = e2
    add(A, "A")
    add(B, "B")
    days = sorted(grid)
    # fully-deployed window: from B's first rebalance to the last grid point that
    # still has an active contribution from both tranches
    b_first = B["recs"][0]["i"]
    a_last_end = A["recs"][-1]["i"] + half
    b_last_end = B["recs"][-1]["i"] + half
    lo, hi = b_first, min(a_last_end, b_last_end)
    days = [g for g in days if lo <= g <= hi]
    lump, comb = [], []
    for g in days:
        cell = grid[g]
        a = cell.get("A", 0.0) + cell.get("A_c", 0.0)
        b = cell.get("B", 0.0) + cell.get("B_c", 0.0)
        lump.append(a)                    # lump = tranche A at full notional
        comb.append(0.5 * a + 0.5 * b)
    return {"A": A, "B": B, "grid_days": days, "lump": lump, "comb": comb,
            "fallbacks": fb_tot}


def maxdd(series: List[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for x in series:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def cell2(d: dict) -> dict:
    w, coins, no_meme = crypto_world(d)
    pk = lambda c, i: wx2.pctk(w, c, i, 14)
    elig = wx2.elig_std(w, no_meme, 61)
    ts = tranche_scheme(w, elig, pk, LIVE_K, LIVE_H)
    A, B = ts["A"], ts["B"]
    nA, nB = net25_series(A["recs"]), net25_series(B["recs"])
    print(f"tranche A: n={len(nA)} rebals, net25 {100*statistics.mean(nA):+.2f}%/rebal")
    print(f"tranche B: n={len(nB)} rebals, net25 {100*statistics.mean(nB):+.2f}%/rebal "
          f"(offset +{LIVE_H//2} bars)")
    print(f"grid: {len(ts['lump'])} x {LIVE_H//2}d periods, mid-mark fallbacks {ts['fallbacks']}")
    out = {"nA": nA, "nB": nB}
    for name, series in (("LUMP (A@full)", ts["lump"]), ("TRANCHE 0.5A+0.5B", ts["comb"])):
        e, sh = ev_sh(series)
        (h1, h2) = halves(series)
        e1, sh1 = ev_sh(h1)
        e2, sh2 = ev_sh(h2)
        dd = maxdd(series)
        worst = min(series)
        print(f"{name:<20} per-H net25 {100*2*e:+.3f}%  gridSh {sh:+.3f} "
              f"(h1 {sh1:+.3f}/h2 {sh2:+.3f})  worst5d {100*worst:+.2f}%  maxDD {100*dd:.2f}pp")
        out[name] = {"perH": 2 * e, "sh": sh, "sh_h": (sh1, sh2), "worst": worst,
                     "dd": dd, "perH_h": (2 * e1, 2 * e2)}
    wA = min(nA)
    wB = min(nB)
    print(f"worst single rebalance net25: lump {100*wA:+.2f}%  "
          f"tranches A {100*wA:+.2f}% / B {100*wB:+.2f}% (full-notional units)")
    l, t = out["LUMP (A@full)"], out["TRANCHE 0.5A+0.5B"]
    drag = t["perH"] - l["perH"]
    rel = drag / abs(l["perH"]) if l["perH"] else float("inf")
    sh_win = t["sh_h"][0] > l["sh_h"][0] and t["sh_h"][1] > l["sh_h"][1]
    dd_ok = t["dd"] <= l["dd"]
    ok = sh_win and abs(rel) <= 0.10 and dd_ok if drag < 0 else sh_win and dd_ok
    print(f"EV drag {100*drag:+.3f}%/H ({100*rel:+.1f}% rel)  "
          f"Sharpe both halves {'WIN' if sh_win else 'LOSE'}  maxDD {'WIN/TIE' if dd_ok else 'LOSE'}"
          f"  => {'WIRE-WORTHY (pre-registered gate)' if ok else 'KEEP LUMP'}")
    out["gate"] = {"drag": drag, "rel": rel, "sh_win": sh_win, "dd_ok": dd_ok, "ok": ok}
    # POST-HOC DIAGNOSTIC (declared as such, not a gate input): phase sensitivity of
    # the baseline book — same recipe started at every offset 0..9. Quantifies how
    # much of the +3.68% point estimate is rebalance-phase luck.
    print("\n----- post-hoc: phase-offset sweep (start=66+o, same recipe) -----")
    for o in range(LIVE_H):
        r = wx2.run_book(w, elig, pk, LIVE_K, LIVE_H, start=66 + o)
        g = statistics.mean(x["ev"] for x in r["recs"])
        n25 = statistics.mean(net25_series(r["recs"]))
        print(f"  offset +{o}: n={len(r['recs'])} gross {100*g:+.2f}% net25 {100*n25:+.2f}%")
    return out


# ---------------------------------------------------------------------------- cell 3
def run_book_hyst(w, eligible_fn, score_fn, k: int, hold: int, b: int,
                  start: int = 66) -> dict:
    """wx2.run_book + rank-hysteresis: incumbents keep their slot while inside the
    top/bottom (k+b) ranks. b=0 == wx2.run_book exactly (asserted in selftest and
    on real data)."""
    recs = []
    prev_book: set = set()
    prev_longs: List[str] = []
    prev_shorts: List[str] = []
    i = start
    while i + 1 + hold < len(w.days):
        elig = eligible_fn(i)
        scored = []
        fwd_all = {}
        for c in elig:
            b1, b2 = w.bar(c, i + 1), w.bar(c, i + 1 + hold)
            if b1 is None or b2 is None or b1[O] <= 0:
                continue
            s = score_fn(c, i)
            if s is None:
                continue
            scored.append((c, s))
            fwd_all[c] = b2[O] / b1[O] - 1.0
        if len(scored) < 2 * k:
            i += hold
            continue
        scored.sort(key=lambda x: -x[1])
        order = [c for c, _ in scored]
        rank = {c: j for j, c in enumerate(order)}
        n = len(order)
        keep_l = [c for c in prev_longs if c in rank and rank[c] < k + b]
        longs = list(keep_l)
        for c in order:
            if len(longs) == k:
                break
            if c not in longs:
                longs.append(c)
        keep_s = [c for c in prev_shorts
                  if c in rank and (n - 1 - rank[c]) < k + b and c not in longs]
        shorts = list(keep_s)
        for c in reversed(order):
            if len(shorts) == k:
                break
            if c not in shorts and c not in longs:
                shorts.append(c)
        assert len(longs) == k and len(shorts) == k
        assert not set(longs) & set(shorts)
        ev = 0.5 * statistics.mean([fwd_all[c] for c in longs]) \
            - 0.5 * statistics.mean([fwd_all[c] for c in shorts])
        book = {(c, "L") for c in longs} | {(c, "S") for c in shorts}
        turn = 1.0 if not prev_book else len(book - prev_book) / len(book)
        prev_book, prev_longs, prev_shorts = book, longs, shorts
        recs.append({"i": i, "t": w.days[i], "ev": ev, "turnover": turn,
                     "longs": longs, "shorts": shorts, "elig": list(fwd_all),
                     "fwd": fwd_all})
        i += hold
    return {"recs": recs}


def cell3(d: dict) -> dict:
    w, coins, no_meme = crypto_world(d)
    pk = lambda c, i: wx2.pctk(w, c, i, 14)
    elig = wx2.elig_std(w, no_meme, 61)
    base_engine = wx2.run_book(w, elig, pk, LIVE_K, LIVE_H)
    base = run_book_hyst(w, elig, pk, LIVE_K, LIVE_H, 0)
    # b=0 must equal the shared engine exactly, on the real data
    for ra, rb in zip(base["recs"], base_engine["recs"]):
        assert ra["t"] == rb["t"] and abs(ra["ev"] - rb["ev"]) < 1e-12 \
            and ra["turnover"] == rb["turnover"] \
            and set(ra["longs"]) == set(rb["longs"]) \
            and set(ra["shorts"]) == set(rb["shorts"])
    print("b=0 == shared engine on real data: OK")
    out = {"rows": {}}
    rows = {}
    for b in (0, 1, 2, 4):
        res = base if b == 0 else run_book_hyst(w, elig, pk, LIVE_K, LIVE_H, b)
        s = wx2.summarize_book(res, LIVE_K, LIVE_H, f"C3 hysteresis b={b}")
        rows[b] = (res, s)
        out["rows"][b] = s
        print(wx2.fmt(s))
    base_turn = statistics.mean(r["turnover"] for r in rows[0][0]["recs"])
    print("\n----- dominance vs b=0 (net25 EV + Sharpe, both halves; net50 reported) -----")
    out["dominance"] = {}
    for b in (1, 2, 4):
        res, s = rows[b]
        turn = statistics.mean(r["turnover"] for r in res["recs"])
        red = 100 * (1 - turn / base_turn)
        d25v = dom_metrics(res["recs"], LIVE_H, 0.0025)
        d25b = dom_metrics(rows[0][0]["recs"], LIVE_H, 0.0025)
        ok25, checks = cross_h_dominance(d25v, d25b)   # same H => reduces to W-X4 gate
        d50v = dom_metrics(res["recs"], LIVE_H, 0.0050)
        d50b = dom_metrics(rows[0][0]["recs"], LIVE_H, 0.0050)
        ok50, _ = cross_h_dominance(d50v, d50b)
        # paired diagnostics vs b=0 (same rebalance dates by construction)
        s_v, s_b = netX_series(res["recs"], 0.0025), netX_series(rows[0][0]["recs"], 0.0025)
        deltas = [a - c for a, c in zip(s_v, s_b)]
        md = statistics.mean(deltas)
        tstat = md / (statistics.stdev(deltas) / math.sqrt(len(deltas)) + 1e-12)
        ndiff = 0
        legdiff = 0
        for rv, rb0 in zip(res["recs"], rows[0][0]["recs"]):
            bv = {(c, "L") for c in rv["longs"]} | {(c, "S") for c in rv["shorts"]}
            bb = {(c, "L") for c in rb0["longs"]} | {(c, "S") for c in rb0["shorts"]}
            if bv != bb:
                ndiff += 1
                legdiff += len(bv - bb)
        out["dominance"][b] = {"turn_red": red, "ok25": ok25, "ok50": ok50,
                               "delta": md, "t": tstat, "ndiff": ndiff, "legdiff": legdiff}
        print(f"  b={b}: turnover {turn:.3f} ({red:+.1f}% vs b=0)  "
              f"net25 gate {'DOMINANT 4/4' if ok25 else 'FAILS'}  "
              f"net50 gate {'DOMINANT' if ok50 else 'fails'}  "
              f"paired d {100*md:+.3f}%/rebal t={tstat:+.2f}  "
              f"books differ {ndiff}/{len(deltas)} rebals ({legdiff} legs)")
        for c_ in checks:
            print(f"    {c_}")
    # POST-HOC DIAGNOSTIC (declared as such): the offset-0 grid is the flattering
    # phase (see cell 2 sweep). Does the b=2 paired delta survive at ALL 10 phase
    # offsets, or is it offset-0 overfit? Gate stays offset-0 (convention); the
    # wire recommendation must weigh this.
    print("\n----- post-hoc: paired net25 delta (b vs b=0) across phase offsets -----")
    out["phase"] = {}
    for b in (1, 2, 4):
        ds = []
        for o in range(LIVE_H):
            r0 = run_book_hyst(w, elig, pk, LIVE_K, LIVE_H, 0, start=66 + o)
            rb = run_book_hyst(w, elig, pk, LIVE_K, LIVE_H, b, start=66 + o)
            s0, sb = netX_series(r0["recs"], 0.0025), netX_series(rb["recs"], 0.0025)
            ds.append(statistics.mean(x - y for x, y in zip(sb, s0)))
        pos = sum(1 for x in ds if x > 0)
        print(f"  b={b}: mean-of-offsets {100*statistics.mean(ds):+.3f}%/rebal, "
              f"positive at {pos}/10 offsets, "
              f"per-offset [" + ", ".join(f"{100*x:+.2f}" for x in ds) + "]")
        out["phase"][b] = ds
    return out


# ---------------------------------------------------------------------------- cell 4
def build_ew_index(w, members: List[str]) -> None:
    """Inject EW_UNIV synthetic index bars into the world (grid days, close = index)."""
    idx = 100.0
    prev_day = None
    bars = {}
    for day in w.days:
        if prev_day is not None:
            rets = []
            for c in members:
                b0, b1 = w.bars.get(c, {}).get(prev_day), w.bars.get(c, {}).get(day)
                if b0 is not None and b1 is not None and b0[C] > 0:
                    rets.append(b1[C] / b0[C] - 1.0)
            if len(rets) >= 5:
                new = idx * (1.0 + statistics.mean(rets))
            else:
                new = idx
        else:
            new = idx
        bars[day] = [day, idx, max(idx, new), min(idx, new), new, 0.0]
        idx = new
        prev_day = day
    w.bars["EW_UNIV"] = bars


def cell4(d: dict) -> dict:
    w, eq = wx2.make_xyz_world(d)
    build_ew_index(w, eq)
    elig = wx2.elig_std(w, eq, 61, 250_000.0)
    out = {"rows": [], "dom": {}}
    books = {}
    for bench in ("xyz:XYZ100", "EW_UNIV"):
        score = lambda c, i, b=bench: wx2.residual_ret(w, c, i, 7, b)
        for k in (3, 5):
            for hold in (3, 5, 10):
                res = wx2.run_book(w, elig, score, k, hold)
                s = wx2.summarize_book(res, k, hold,
                                       f"C4 xyz resid7 k{k} H{hold} {bench.replace('xyz:','')}")
                books[(bench, k, hold)] = res
                s["cfg"] = (bench, k, hold)
                out["rows"].append(s)
                print(wx2.fmt(s))
    live = books[("xyz:XYZ100", 5, 5)]
    ldom = dom_metrics(live["recs"], 5)
    print("\n----- frontier: neighbors vs LIVE resid7/k5/H5/XYZ100 "
          "(per-day EV + ann Sharpe both halves, + null p<0.05) -----")
    for s in out["rows"]:
        cfg = s["cfg"]
        if cfg == ("xyz:XYZ100", 5, 5):
            continue
        if "verdict" in s and str(s.get("verdict", "")).startswith("BLOCKED"):
            continue
        res = books[cfg]
        vdom = dom_metrics(res["recs"], cfg[2])
        ok, checks = cross_h_dominance(vdom, ldom)
        p_ok = s.get("null_p_gross", 1.0) < 0.05
        verdict = "DOMINATES 4/4 + p<0.05" if (ok and p_ok) else \
            ("dominates 4/4 but null p FAILS" if ok else "no")
        out["dom"][cfg] = (ok, p_ok)
        if ok or cfg[2] == 5:
            print(f"  {cfg}: {verdict}")
            if ok:
                for c_ in checks:
                    print(f"    {c_}")
    # POST-HOC DIAGNOSTIC (declared as such): phase-mean net25 per cell over a
    # full phase cycle (offsets 0..H-1) — is the live cell's estimate phase-lucky?
    print("\n----- post-hoc: phase-mean net25 per cell (offsets 0..H-1, no nulls) -----")
    out["phase"] = {}
    for bench in ("xyz:XYZ100", "EW_UNIV"):
        score = lambda c, i, b=bench: wx2.residual_ret(w, c, i, 7, b)
        for k in (3, 5):
            for hold in (3, 5, 10):
                vals = []
                for o in range(hold):
                    r = wx2.run_book(w, elig, score, k, hold, start=66 + o)
                    if len(r["recs"]) >= 4:
                        vals.append(statistics.mean(net25_series(r["recs"])))
                pm = statistics.mean(vals)
                out["phase"][(bench, k, hold)] = vals
                print(f"  {bench.replace('xyz:',''):<7} k{k}/H{hold}: "
                      f"phase-mean {100*pm:+.2f}%/rebal ({100*pm/hold:+.3f}%/day)  "
                      f"min {100*min(vals):+.2f}  max {100*max(vals):+.2f}  "
                      f"offset0 {100*vals[0]:+.2f}")
    return out


# ---------------------------------------------------------------------------- selftest
def _mk(drift, days=200, vol=1_000_000.0, px0=100.0, day0=1_700_000_000_000,
        switch_day=None, drift2=None):
    rows, px = [], px0
    for t in range(days):
        g = drift if (switch_day is None or t < switch_day) else drift2
        o = px
        px = px * (1 + g)
        rows.append([day0 + t * 86_400_000, o, max(o, px), min(o, px), px, vol])
    return rows


def selftest() -> None:
    # ---- hysteresis: b=0 equals shared engine; b=1 retains a demoted incumbent
    candles = {"P0": _mk(0.02), "P1": _mk(0.015, switch_day=100, drift2=0.008),
               "P2": _mk(0.012), "P3": _mk(-0.012),
               "P4": _mk(-0.015, switch_day=100, drift2=-0.008), "P5": _mk(-0.02)}
    w = wx2.World(candles, sorted(candles))
    coins = sorted(candles)
    score = lambda c, i: wx2.trailing_ret(w, c, i, 7)
    e = lambda i: coins
    eng = wx2.run_book(w, e, score, 2, 5)
    h0 = run_book_hyst(w, e, score, 2, 5, 0)
    assert len(eng["recs"]) == len(h0["recs"]) > 10
    for ra, rb in zip(h0["recs"], eng["recs"]):
        assert abs(ra["ev"] - rb["ev"]) < 1e-15 and ra["turnover"] == rb["turnover"]
        assert set(ra["longs"]) == set(rb["longs"]) and set(ra["shorts"]) == set(rb["shorts"])
    h1 = run_book_hyst(w, e, score, 2, 5, 1)
    post = [r for r in h1["recs"] if (r["t"] - candles["P0"][0][T]) / 86_400_000 > 110]
    b0post = [r for r in h0["recs"] if (r["t"] - candles["P0"][0][T]) / 86_400_000 > 110]
    assert all(set(r["longs"]) == {"P0", "P1"} and set(r["shorts"]) == {"P4", "P5"}
               for r in post), "b=1 must retain demoted incumbents inside the buffer"
    assert any(set(r["longs"]) == {"P0", "P2"} for r in b0post), "b=0 must swap P1 -> P2"
    t1 = sum(r["turnover"] for r in h1["recs"])
    t0 = sum(r["turnover"] for r in h0["recs"])
    assert t1 < t0, (t1, t0)

    # ---- tranches: exact sub-splits, offset dates, drift-equality of means
    candles2 = {}
    for j in range(4):
        candles2[f"U{j}"] = _mk(0.01)
        candles2[f"D{j}"] = _mk(-0.01)
        candles2[f"F{j}"] = _mk(0.0)
    w2 = wx2.World(candles2, sorted(candles2))
    ts = tranche_scheme(w2, lambda i: sorted(candles2),
                        lambda c, i: wx2.trailing_ret(w2, c, i, 7), 2, 10)
    A, B = ts["A"], ts["B"]
    assert [r["i"] for r in B["recs"]][:3] == [r["i"] + 5 for r in A["recs"]][:3]
    for rec in A["recs"] + B["recs"]:
        s1, s2, fb = sub_split(w2, rec, 5)
        assert fb == 0
        for c in set(rec["longs"]) | set(rec["shorts"]):
            assert abs(s1[c] + s2[c] - rec["fwd"][c]) < 1e-15
    mA = statistics.mean(r["ev"] for r in A["recs"])
    mB = statistics.mean(r["ev"] for r in B["recs"])
    assert abs(mA - mB) < 1e-9, "constant drift => same GROSS EV either offset"
    # combined mean can differ from lump only via B's own fee schedule, bounded by fees
    assert abs(statistics.mean(ts["lump"]) - statistics.mean(ts["comb"])) < 0.0025
    assert maxdd([0.05, -0.02, -0.04, 0.03]) - 0.06 < 1e-15

    # ---- EW index: identical drifts => index ret == drift, residual == 0
    candles3 = {f"E{j}": _mk(0.01) for j in range(6)}
    candles3["E9"] = _mk(0.02)
    w3 = wx2.World(candles3, sorted(candles3))
    build_ew_index(w3, sorted(candles3))
    bars = w3.bars["EW_UNIV"]
    days = w3.days
    for j in range(1, 5):
        r = bars[days[j]][C] / bars[days[j - 1]][C] - 1.0
        exp = statistics.mean([0.01] * 6 + [0.02])
        assert abs(r - exp) < 1e-12
    candles4 = {f"E{j}": _mk(0.01) for j in range(6)}
    w4 = wx2.World(candles4, sorted(candles4))
    build_ew_index(w4, sorted(candles4))
    assert abs(wx2.residual_ret(w4, "E0", 100, 7, "EW_UNIV")) < 1e-9
    w3b = w3
    assert wx2.residual_ret(w3b, "E9", 100, 7, "EW_UNIV") > 0.02

    # ---- capital arithmetic (declared constants)
    lev_min, U = 3, UTIL
    for k, e_strict in ((4, 35.0), (6, 52.5), (8, 70.0)):
        f = U / (2 * k)
        assert abs(MIN_ORDER / (f * lev_min) - e_strict) < 1e-9
    f4 = U / 8
    assert abs(f4 * 65 * lev_min - 19.5) < 1e-9 and abs(f4 * 65 * 12 - 78.0) < 1e-9

    print("W-X5 selftest OK: hysteresis b0==engine + retention/turnover, tranche "
          "splits exact + offset + drift-equal means, maxdd, EW index exact + "
          "residual sanity, capital arithmetic")


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default="all", choices=["1", "2", "3", "4", "all"])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    d = wx2.load_cache()
    import datetime as dt
    last = max(rows[-1][T] for rows in d["candles"].values() if rows)
    print(f"cache: {len(d['candles'])} coins, last bar "
          f"{dt.datetime.fromtimestamp(last/1000, dt.timezone.utc):%Y-%m-%d}")
    cells = {"1": cell1, "2": cell2, "3": cell3, "4": cell4}
    todo = ["1", "2", "3", "4"] if args.cell == "all" else [args.cell]
    for name in todo:
        print(f"\n===== CELL {name} =====")
        cells[name](d)


if __name__ == "__main__":
    main()
