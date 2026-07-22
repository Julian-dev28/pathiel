#!/usr/bin/env python3
"""W-MC1 — first-order Markov chain on discretised returns: is there tradeable
transition structure?

PRE-REGISTERED SPEC (written before any return was scored)
----------------------------------------------------------
Hypothesis: the state of a coin's return today predicts the SIGN of tomorrow's
return beyond the unconditional base rate, by enough to trade after fees. This is
a first-order Markov chain on discretised close-to-close returns.

Data: dataset.json cached OHLCV, 40 crypto coins. PRIMARY interval 1d (~300 bars/coin);
robustness on 1h. Row = [t_ms, o, h, l, c, v]. r_t = c_t / c_{t-1} - 1.

States (pre-registered):
  - PRIMARY  = 3 terciles of the coin's OWN return distribution -> {0:LOW,1:MID,2:HIGH}
  - variants = 2-state sign split, 5-state quintiles
  Breakpoints are fit on the TRAIN half ONLY and frozen for the TEST half (no lookahead).

Estimation / trading (strict OOS by construction):
  - TRAIN = first 60% of each coin's bars. Fit tercile breakpoints and, per state s,
    mu(s) = mean forward 1d return given state_t = s (pooled across coins).
  - TEST  = last 40%. At each day t observe state_t via FROZEN train breakpoints; take
    side = sign(mu(s) - grand_train_mean). Enter close_t, exit close_{t+1}. One position
    per coin-day, non-overlapping is automatic (daily).
  - EV per trade = side * r_{t+1} - fee. Fees 0 / 25 / 50 bps round trip.

Null (pre-registered): 2000x label permutation WITHIN the test set — shuffle the state
vector against the forward-return vector, recompute mean signed EV25. Destroys any
state->forward association while preserving both marginals. One-sided p in the direction
of the observed mean. Seed 20260723.

Verdict gate (locked): ROBUST = net25 EV > 0 AND perm p < 0.05 AND net25 > 0 in BOTH
halves of the TEST set. MARGINAL = net25 > 0 with p < 0.10 failing a clause. else REFUTED.

Caveats locked: survivor-biased 40-coin set (positives are upper bounds); daily
close-to-close ignores intrabar; one tape; funding not modelled; daily turnover makes
25bps a heavy but REAL cost — a Markov day-trader pays it every bar.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import alpha_lib as A  # noqa: E402

SEED = 20260723
TRAIN_FRAC = 0.60
FEES = {"ev0": 0.0, "ev25": 0.0025, "ev50": 0.0050}


def returns(d, coin, iv):
    cs = A.candles(d, coin, iv)
    out = []
    for i in range(1, len(cs)):
        c0, c1 = cs[i - 1][4], cs[i][4]
        if c0 and c1:
            out.append((cs[i][0], c1 / c0 - 1.0))
    return out  # [(t_ms, r)]


def breakpoints(vals, n_states):
    """n_states-quantile cut points from a sorted sample (train only)."""
    s = sorted(vals)
    if len(s) < n_states * 3:
        return None
    return [s[int(len(s) * q / n_states)] for q in range(1, n_states)]


def state_of(r, bps):
    lo = 0
    for b in bps:
        if r <= b:
            return lo
        lo += 1
    return lo


def run(d, iv="1d", n_states=3):
    coins = d["meta"]["coins"]
    # ---- TRAIN: fit breakpoints per coin, collect (state, fwd) pooled ----
    train_pairs, all_train_bps = [], {}
    grand_train = []
    for coin in coins:
        rs = returns(d, coin, iv)
        if len(rs) < 40:
            continue
        cut = int(len(rs) * TRAIN_FRAC)
        tr = rs[:cut]
        bps = breakpoints([r for _, r in tr], n_states)
        if bps is None:
            continue
        all_train_bps[coin] = bps
        # state_t from r_t, fwd = r_{t+1}
        for i in range(len(tr) - 1):
            train_pairs.append((state_of(tr[i][1], bps), tr[i + 1][1]))
            grand_train.append(tr[i][1])
    grand_mean = st.fmean(grand_train) if grand_train else 0.0
    mu = {}
    for s in range(n_states):
        fwd = [f for st_, f in train_pairs if st_ == s]
        mu[s] = st.fmean(fwd) if fwd else 0.0
    side = {s: (1 if mu[s] - grand_mean > 0 else -1) for s in range(n_states)}

    # transition matrix P(next_state | state) for display
    nxt = {s: [0] * n_states for s in range(n_states)}
    prev = None
    # rebuild per-coin to avoid cross-coin bleed
    trans = {s: [0] * n_states for s in range(n_states)}
    for coin in coins:
        if coin not in all_train_bps:
            continue
        rs = returns(d, coin, iv)
        cut = int(len(rs) * TRAIN_FRAC)
        bps = all_train_bps[coin]
        seq = [state_of(r, bps) for _, r in rs[:cut]]
        for i in range(len(seq) - 1):
            trans[seq[i]][seq[i + 1]] += 1
    trans_p = {}
    for s in range(n_states):
        tot = sum(trans[s]) or 1
        trans_p[s] = [round(x / tot, 3) for x in trans[s]]

    # ---- TEST: trade the frozen policy ----
    test_states, test_fwd = [], []
    for coin in coins:
        if coin not in all_train_bps:
            continue
        rs = returns(d, coin, iv)
        cut = int(len(rs) * TRAIN_FRAC)
        te = rs[cut:]
        bps = all_train_bps[coin]
        for i in range(len(te) - 1):
            test_states.append(state_of(te[i][1], bps))
            test_fwd.append(te[i + 1][1])

    def ev_series(states, fwd, fee):
        return [side[s] * f - fee for s, f in zip(states, fwd)]

    n = len(test_fwd)
    res = {"iv": iv, "n_states": n_states, "n_test_trades": n,
           "grand_train_mean_pct": round(grand_mean * 100, 4),
           "mu_by_state_pct": {s: round(mu[s] * 100, 4) for s in mu},
           "side_by_state": side,
           "transition_matrix": trans_p, "cells": {}}
    ev25_series = ev_series(test_states, test_fwd, FEES["ev25"])
    for key, fee in FEES.items():
        ser = ev_series(test_states, test_fwd, fee)
        res["cells"][key] = round(st.fmean(ser) * 100, 4) if ser else 0.0

    # OOS split of the TEST set
    half = n // 2
    res["test_h1_ev25_pct"] = round(st.fmean(ev25_series[:half]) * 100, 4) if half else 0.0
    res["test_h2_ev25_pct"] = round(st.fmean(ev25_series[half:]) * 100, 4) if n - half else 0.0

    # ---- permutation null on EV25 ----
    rnd = random.Random(SEED)
    obs = st.fmean(ev25_series) if ev25_series else 0.0
    ge = 0
    perm_states = list(test_states)
    for _ in range(2000):
        rnd.shuffle(perm_states)
        pev = st.fmean(ev_series(perm_states, test_fwd, FEES["ev25"]))
        if (pev >= obs) if obs >= 0 else (pev <= obs):
            ge += 1
    res["perm_p"] = round((1 + ge) / (1 + 2000), 4)

    # verdict
    ev25 = res["cells"]["ev25"]
    both_halves = res["test_h1_ev25_pct"] > 0 and res["test_h2_ev25_pct"] > 0
    if ev25 > 0 and res["perm_p"] < 0.05 and both_halves:
        res["verdict"] = "ROBUST"
    elif ev25 > 0 and res["perm_p"] < 0.10:
        res["verdict"] = "MARGINAL"
    else:
        res["verdict"] = "REFUTED"
    return res


def show(r):
    print(f"\n=== Markov {r['n_states']}-state on {r['iv']}  (n_test={r['n_test_trades']}) ===")
    print("  transition P(next|state):")
    for s, row in r["transition_matrix"].items():
        print(f"    state {s} -> {row}")
    print(f"  grand train mean {r['grand_train_mean_pct']:+.3f}%   mu|state {r['mu_by_state_pct']}")
    print(f"  policy side|state {r['side_by_state']}")
    print(f"  TEST EV  gross={r['cells']['ev0']:+.3f}%  net25={r['cells']['ev25']:+.3f}%  net50={r['cells']['ev50']:+.3f}%")
    print(f"  TEST halves net25  h1={r['test_h1_ev25_pct']:+.3f}%  h2={r['test_h2_ev25_pct']:+.3f}%")
    print(f"  perm null p={r['perm_p']}   -> VERDICT: {r['verdict']}")


if __name__ == "__main__":
    d = A.load_dataset()
    out = {}
    for iv in ("1d", "1h"):
        for ns in (2, 3, 5):
            r = run(d, iv=iv, n_states=ns)
            show(r)
            out[f"{iv}_{ns}state"] = r
    import json
    Path(__file__).with_name("W-MC1_results.json").write_text(json.dumps(out, indent=2))
    print("\nwrote W-MC1_results.json")
