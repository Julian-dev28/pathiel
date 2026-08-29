#!/usr/bin/env python3
"""W-X3 — liquid_majors_xs. PRE-REGISTERED before first run (swarm rules).

ORIGIN: W-X2 cell B side-finding (findings/W-X2_xs_sector_buckets.md): within-L1 raw7 k2 H5
gave +1.50% net25 (p=0.000, both halves +) and beat the same-k all-universe control — flagged
as "liquid-majors cleanliness, not sector alpha; needs its own pre-registered cell". This is
that cell. KEY QUESTION: is a liquid-majors-only xs book ADDITIVE to the live top-50 book,
or the same trades?

DATA: W-X2_cache_daily.json (fetched 2026-07-20, HL daily candleSnapshot, top-50 crypto by
dayNtlVlm, 401 bars). Survivor-biased: today's listings => positive results are upper bounds.
No network. seed=42.

UNIVERSE RULE (declared FIRST, mechanical, no hand-picking)
  At each decision bar i, from the 50-coin crypto set: eligible = coins with >=61 prior bars;
  rank eligible by trailing 30d MEDIAN daily notional (median of close*volume over the grid
  days j in [i-29, i] where the coin has a bar; >=15 observations required, else excluded);
  universe(i) = top-N, N in {10, 15, 20}. Point-in-time, recomputed every rebalance.
  MEDIAN (not mean) so a single wash/spike day cannot buy a coin into the majors set.

CELLS (shared W-X2 engine, imported: decide bar i, fill open[i+1], exit open[i+1+H],
non-overlapping, equal weight, start=66)
  Score: raw 7d trailing return — the exact signal of the cell B side-finding.
  Sweep: N in {10,15,20} x k in {2,3} x H in {5,10}  (12 cells).
  PRIMARY CELL: N=15, k=3, H=10 (H10 = the live book's new hold, W-X2-D + hot config).
  Every other cell is labeled SENSITIVITY.
  Declared extra sensitivity (not part of the sweep grid): pct_k(14) ranker at the primary
  geometry only — does the live ranker transfer to the majors universe?
  CONTROL (concentration confound, as in cell B): all-universe top-50 raw7 at the primary
  k=3 H=10 — the liquid-majors restriction must beat the same-k unrestricted book to mean
  anything.

COSTS / NULL / OOS — identical to the W-X2 engine
  Tiers 0/12/25/50 bps round-trip per replaced position, turnover-scaled. NULL = 2000 seeded
  matched random books (same rebalance dates, same eligible top-N sets, same fills), p on
  gross EV — the null tests selection skill WITHIN the declared universe; the value of the
  universe restriction itself is judged against the CONTROL book, not the null. OOS = first/
  second half of rebalances. Sharpe-like on net25. $-line: $76.8/leg (0.10 equity-frac x 12x
  on ~$64 main-dex equity), book gross = 2k x 76.8, $/wk = net25 x gross x 7/H.

VERDICT GATES (same as W-X2, pre-registered)
  ROBUST   : net25 > 0 AND both OOS halves > 0 AND null p < 0.05 AND n >= 15.
  MARGINAL : net25 > 0 AND (one OOS half <= 0 OR 0.05 <= p < 0.15 OR n < 15).
  REFUTED  : net25 <= 0 OR (OOS sign-flip AND p >= 0.15).

ADDITIVITY vs THE LIVE BOOK (W-A1 / W-C3 method, gate pre-registered)
  LIVE COMPARATOR: the live recipe as configured 2026-07-20 (.agent-config.json + committed
  config_store defaults + rank_universe): ranking pct_k(14) (residual flag ignored under
  pct_k), k=4/leg, hold 10, universe = full top-50 (>=61 bars). Simulated on the same engine,
  same grid, UNGATED (no BTC-vol gate) — declared: if wired, a majors book would sit behind
  the same gate, so the gate zeroes both books together; the ungated comparison is the
  STRICTER same-trades test (gating can only lower measured corr). Reproduction check: must
  match W-X2-D "pct_k14 k4 H10" (gross +3.46%, net25 +3.25%, n=33) to 0.01pp.
  (1) LEG OVERLAP: per common rebalance, |X3 legs ∩ live legs| / (2*k_X3), legs = (coin,side);
      report mean, and long/short legs separately; coin-any-side overlap as diagnostic.
  (2) RETURN CORR: Pearson corr of per-rebalance GROSS EV series on common dates.
  (3) RESIDUAL ALPHA: OLS x3 = alpha + beta*live on gross EV; alpha + t(alpha), FULL + halves.
  ADDITIVE iff corr < 0.5 AND residual alpha > 0 in BOTH halves. Diagnostic (not a gate):
  50/50 combined-book Sharpe vs best single (W-A1 convention).
  Additivity block runs for the PRIMARY cell; overlap lines also reported for N=10/N=20 at
  k3 H10 and for the pct_k sensitivity as context.

Read-only research. No live wiring. One script, one findings file.
"""
from __future__ import annotations

import importlib.util
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = str(Path(__file__).resolve().parents[3])
HYP = os.path.join(REPO, "research", "alpha_swarm", "hypotheses")

# ---- import the shared W-X2 engine (same harness, by construction) ----------------------
_spec = importlib.util.spec_from_file_location("wx2", os.path.join(HYP, "W-X2_xs_widening.py"))
wx2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx2)

T, O, H_, L_, C, V = wx2.T, wx2.O, wx2.H_, wx2.L_, wx2.C, wx2.V

N_SWEEP = (10, 15, 20)
K_SWEEP = (2, 3)
H_SWEEP = (5, 10)
PRIMARY = (15, 3, 10)          # (N, k, H) — pre-registered primary cell
LIVE_K, LIVE_H = 4, 10         # live comparator geometry (pct_k14, top-50)
MIN_NTL_OBS = 15


# ---------------------------------------------------------------------------- universe rule
def median_notional_30d(w: "wx2.World", coin: str, i: int) -> Optional[float]:
    """Median close*volume over trailing 30 grid days (days the coin traded). None if <15 obs."""
    vals = []
    for j in range(max(0, i - 29), i + 1):
        b = w.bars.get(coin, {}).get(w.days[j])
        if b is not None:
            vals.append(b[C] * b[V])
    if len(vals) < MIN_NTL_OBS:
        return None
    return statistics.median(vals)


def elig_topN(w: "wx2.World", coins: List[str], n_top: int,
              min_hist: int = 61) -> Callable[[int], List[str]]:
    """Point-in-time top-N by 30d median daily notional among >=min_hist-bar coins."""
    def f(i: int) -> List[str]:
        ranked = []
        for c in coins:
            if w.history_len(c, i) < min_hist:
                continue
            m = median_notional_30d(w, c, i)
            if m is None:
                continue
            ranked.append((c, m))
        ranked.sort(key=lambda x: (-x[1], x[0]))     # deterministic: notional desc, name asc
        return [c for c, _ in ranked[:n_top]]
    return f


# ---------------------------------------------------------------------------- additivity
def ols_alpha(y: List[float], x: List[float]) -> Optional[dict]:
    """OLS y = alpha + beta*x; returns alpha, beta, t(alpha), corr."""
    n = len(y)
    if n <= 2 or len(x) != n:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    beta = sxy / sxx
    alpha = my - beta * mx
    resid = [yi - (alpha + beta * xi) for xi, yi in zip(x, y)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se_a = math.sqrt(s2 * (1.0 / n + mx * mx / sxx))
    return {"alpha": alpha, "beta": beta,
            "t_alpha": alpha / se_a if se_a > 0 else float("nan"),
            "corr": sxy / math.sqrt(sxx * syy), "n": n}


def leg_overlap(recs_a: List[dict], recs_b: List[dict]) -> Optional[dict]:
    """Mean fraction of book-A legs (coin,side) also held by book B, on common dates."""
    mb = {r["t"]: r for r in recs_b}
    tot = tot_l = tot_s = tot_coin = 0.0
    n = 0
    for ra in recs_a:
        rb = mb.get(ra["t"])
        if rb is None:
            continue
        a_l, a_s = set(ra["longs"]), set(ra["shorts"])
        b_l, b_s = set(rb["longs"]), set(rb["shorts"])
        legs = len(a_l) + len(a_s)
        if legs == 0:
            continue
        tot += (len(a_l & b_l) + len(a_s & b_s)) / legs
        tot_l += len(a_l & b_l) / max(1, len(a_l))
        tot_s += len(a_s & b_s) / max(1, len(a_s))
        tot_coin += len((a_l | a_s) & (b_l | b_s)) / legs
        n += 1
    if n == 0:
        return None
    return {"n": n, "legs": round(tot / n, 3), "long": round(tot_l / n, 3),
            "short": round(tot_s / n, 3), "coin_any_side": round(tot_coin / n, 3)}


def common_series(recs_a: List[dict], recs_b: List[dict]) -> Tuple[List[float], List[float]]:
    mb = {r["t"]: r["ev"] for r in recs_b}
    ya, yb = [], []
    for ra in recs_a:
        if ra["t"] in mb:
            ya.append(ra["ev"])
            yb.append(mb[ra["t"]])
    return ya, yb


def sharpe_like(xs: List[float]) -> float:
    return statistics.mean(xs) / (statistics.pstdev(xs) + 1e-12)


def additivity_block(x3: dict, live: dict, label: str) -> List[str]:
    out = [f"-- additivity: {label} vs LIVE pct_k14 k4 H10 top-50 --"]
    ov = leg_overlap(x3["recs"], live["recs"])
    out.append(f"  overlap  legs {ov['legs']:.1%}  long {ov['long']:.1%}  short {ov['short']:.1%}"
               f"  coin-any-side {ov['coin_any_side']:.1%}  (n={ov['n']})")
    ya, yl = common_series(x3["recs"], live["recs"])
    full = ols_alpha(ya, yl)
    h = len(ya) // 2
    h1 = ols_alpha(ya[:h], yl[:h])
    h2 = ols_alpha(ya[h:], yl[h:])
    out.append(f"  corr(gross EV) = {full['corr']:+.3f}   beta = {full['beta']:+.3f}   n={full['n']}")
    out.append(f"  residual alpha FULL {100*full['alpha']:+.3f}%/rebal (t={full['t_alpha']:+.2f})"
               f"   h1 {100*h1['alpha']:+.3f}% (t={h1['t_alpha']:+.2f})"
               f"   h2 {100*h2['alpha']:+.3f}% (t={h2['t_alpha']:+.2f})")
    comb = [(a + b) / 2 for a, b in zip(ya, yl)]
    out.append(f"  Sharpe(gross): x3 {sharpe_like(ya):+.3f}  live {sharpe_like(yl):+.3f}"
               f"  50/50 {sharpe_like(comb):+.3f}  (diagnostic)")
    additive = (full["corr"] < 0.5 and h1["alpha"] > 0 and h2["alpha"] > 0)
    out.append(f"  ADDITIVE GATE (corr<0.5 AND resid alpha>0 both halves): "
               f"{'PASS -> ADDITIVE' if additive else 'FAIL -> NOT ADDITIVE'}")
    return out


# ---------------------------------------------------------------------------- diagnostics
def diagnostics(res: dict, w: "wx2.World", label: str) -> List[str]:
    recs = res["recs"]
    out = [f"-- diagnostics: {label} --"]
    long_evs, short_evs = [], []
    cnt_l: Dict[str, int] = {}
    cnt_s: Dict[str, int] = {}
    depth = []
    prev_univ: Optional[set] = None
    jac = []
    for r in recs:
        long_evs.append(statistics.mean([r["fwd"][c] for c in r["longs"]]))
        short_evs.append(-statistics.mean([r["fwd"][c] for c in r["shorts"]]))
        for c in r["longs"]:
            cnt_l[c] = cnt_l.get(c, 0) + 1
        for c in r["shorts"]:
            cnt_s[c] = cnt_s.get(c, 0) + 1
        depth.append(len(r["elig"]))
        u = set(r["elig"])
        if prev_univ is not None:
            jac.append(len(u & prev_univ) / len(u | prev_univ))
        prev_univ = u
    out.append(f"  leg split (signed, per rebal): long {100*statistics.mean(long_evs):+.2f}%"
               f"  short {100*statistics.mean(short_evs):+.2f}%")
    top_l = sorted(cnt_l.items(), key=lambda x: -x[1])[:6]
    top_s = sorted(cnt_s.items(), key=lambda x: -x[1])[:6]
    out.append(f"  most-longed  : {', '.join(f'{c}({n})' for c, n in top_l)}")
    out.append(f"  most-shorted : {', '.join(f'{c}({n})' for c, n in top_s)}")
    out.append(f"  eligible depth min/med/max: {min(depth)}/{int(statistics.median(depth))}/{max(depth)}"
               f"   universe stability (mean Jaccard consecutive): "
               f"{statistics.mean(jac):.2f}" if jac else "  n/a")
    return out


# ---------------------------------------------------------------------------- selftest
def selftest() -> None:
    # 1) the shared engine's own selftest (selection/EV/turnover/null exact)
    wx2.selftest()

    day0 = 1_700_000_000_000

    def mk(vol_fn, px_drift=0.0, days=200, px0=100.0):
        rows, px = [], px0
        for t in range(days):
            o = px
            px = px * (1 + px_drift)
            rows.append([day0 + t * 86_400_000, o, max(o, px), min(o, px), px, vol_fn(t)])
        return rows

    # 2) universe rule: MEDIAN beats a spike; point-in-time regime change is tracked
    candles = {
        "F0": mk(lambda t: 1000.0), "F1": mk(lambda t: 900.0), "F2": mk(lambda t: 800.0),
        "F3": mk(lambda t: 700.0), "F4": mk(lambda t: 600.0), "F5": mk(lambda t: 10.0),
        # F6: tiny book with one wash-spike day at t=75 — mean-inflated, median-immune
        "F6": mk(lambda t: 1_000_000.0 if t == 75 else 10.0),
        # F7: tiny until t=100, then a real liquidity regime change
        "F7": mk(lambda t: 5000.0 if t >= 100 else 10.0),
    }
    w = wx2.World(candles, sorted(candles))
    top4 = elig_topN(w, sorted(candles), 4)
    u80 = top4(80)
    assert u80 == ["F0", "F1", "F2", "F3"], u80          # spike day inside the 30d window
    # prove the spike WOULD fool a mean: F6's 30d mean notional at t=80 is book-topping
    assert w.mean_notional_30d("F6", 80) > w.mean_notional_30d("F0", 80)
    assert "F6" not in u80                                # ...but the MEDIAN keeps it out
    u140 = top4(140)
    assert u140[0] == "F7" and "F7" not in u80, u140      # PIT: regime change tracked, no lookback leak
    # deterministic tie-break: equal notional -> name asc
    cand2 = {"TIE_B": mk(lambda t: 500.0), "TIE_A": mk(lambda t: 500.0), "Z": mk(lambda t: 1.0)}
    w2 = wx2.World(cand2, sorted(cand2))
    assert elig_topN(w2, sorted(cand2), 2)(80) == ["TIE_A", "TIE_B"]

    # 3) liquidity-restricted momentum book: only liquid trenders are tradeable
    candles3 = {}
    for j in range(4):
        candles3[f"U{j}"] = mk(lambda t: 1000.0, px_drift=+0.01)   # liquid up-trenders
        candles3[f"D{j}"] = mk(lambda t: 1000.0, px_drift=-0.01)   # liquid down-trenders
        candles3[f"u{j}"] = mk(lambda t: 1.0, px_drift=+0.02)      # ILLIQUID stronger trenders
        candles3[f"d{j}"] = mk(lambda t: 1.0, px_drift=-0.02)
    w3 = wx2.World(candles3, sorted(candles3))
    res = wx2.run_book(w3, elig_topN(w3, sorted(candles3), 8),
                       lambda c, i: wx2.trailing_ret(w3, c, i, 7), 2, 5, start=66)
    assert res["recs"], "no rebalances"
    for r in res["recs"]:
        assert set(r["elig"]) == {f"U{j}" for j in range(4)} | {f"D{j}" for j in range(4)}
        assert set(r["longs"]) <= {f"U{j}" for j in range(4)}, r["longs"]
        assert set(r["shorts"]) <= {f"D{j}" for j in range(4)}, r["shorts"]

    # 4) additivity math: exact alpha recovery + overlap on hand-built recs
    x = [0.01, 0.02, -0.01, 0.03, 0.00, 0.015, -0.005, 0.025]
    y = [0.005 + 2.0 * xi for xi in x]
    o = ols_alpha(y, x)
    assert abs(o["alpha"] - 0.005) < 1e-12 and abs(o["beta"] - 2.0) < 1e-12
    assert abs(o["corr"] - 1.0) < 1e-9
    ra = [{"t": 1, "ev": 0.0, "longs": ["A", "B"], "shorts": ["C", "D"]}]
    rb = [{"t": 1, "ev": 0.0, "longs": ["A", "X"], "shorts": ["D", "C"]}]
    ov = leg_overlap(ra, rb)
    assert ov["legs"] == 0.75 and ov["long"] == 0.5 and ov["short"] == 1.0
    ident = leg_overlap(ra, ra)
    assert ident["legs"] == 1.0 and ident["coin_any_side"] == 1.0
    print("W-X3 selftest OK: engine exact, median-vs-spike, PIT universe, tie-break, "
          "liquidity-restricted selection, OLS/overlap exact")


# ---------------------------------------------------------------------------- main
def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    d = wx2.load_cache()
    w, coins = wx2.make_crypto_world(d)
    import datetime as dt
    last = max(rows[-1][T] for rows in d["candles"].values() if rows)
    print(f"cache: {len(d['candles'])} coins, last bar "
          f"{dt.datetime.fromtimestamp(last/1000, dt.timezone.utc):%Y-%m-%d}")

    raw7 = lambda c, i: wx2.trailing_ret(w, c, i, 7)
    pk14 = lambda c, i: wx2.pctk(w, c, i, 14)

    # ---- sweep grid (PRIMARY marked) ----
    print("\n===== W-X3 sweep: top-N by 30d MEDIAN notional, raw7 =====")
    books: Dict[Tuple[int, int, int], dict] = {}
    for n_top in N_SWEEP:
        elig = elig_topN(w, coins, n_top)
        for k in K_SWEEP:
            for hold in H_SWEEP:
                res = wx2.run_book(w, elig, raw7, k, hold)
                books[(n_top, k, hold)] = res
                tag = " (PRIMARY)" if (n_top, k, hold) == PRIMARY else ""
                print(wx2.fmt(wx2.summarize_book(res, k, hold,
                      f"X3 N{n_top} raw7 k{k} H{hold}{tag}")))

    # ---- declared extras: pct_k sensitivity at primary geometry + same-k control ----
    print("\n===== declared sensitivity + control =====")
    nP, kP, hP = PRIMARY
    res_pk = wx2.run_book(w, elig_topN(w, coins, nP), pk14, kP, hP)
    print(wx2.fmt(wx2.summarize_book(res_pk, kP, hP, f"X3 N{nP} pct_k14 k{kP} H{hP} (sens)")))
    ctrl = wx2.run_book(w, wx2.elig_std(w, coins, 61), raw7, kP, hP)
    print(wx2.fmt(wx2.summarize_book(ctrl, kP, hP, f"CONTROL top-50 raw7 k{kP} H{hP}")))

    # ---- live comparator + reproduction check vs W-X2-D ----
    print("\n===== live comparator =====")
    live = wx2.run_book(w, wx2.elig_std(w, coins, 61), pk14, LIVE_K, LIVE_H)
    s_live = wx2.summarize_book(live, LIVE_K, LIVE_H, "LIVE sim pct_k14 k4 H10 top-50")
    print(wx2.fmt(s_live))
    ok = abs(s_live["gross_pct"] - 3.46) <= 0.01 and abs(s_live["net25"] - 3.25) <= 0.01
    print(f"  reproduction check vs W-X2-D (gross +3.46 / net25 +3.25): "
          f"{'OK' if ok else 'MISMATCH — investigate before trusting'}")

    # ---- additivity: primary (gate) + context lines ----
    print("\n===== additivity (W-A1/W-C3 method) =====")
    for line in additivity_block(books[PRIMARY], live, f"PRIMARY N{nP} raw7 k{kP} H{hP}"):
        print(line)
    for n_top in N_SWEEP:
        if n_top == nP:
            continue
        ov = leg_overlap(books[(n_top, kP, hP)]["recs"], live["recs"])
        ya, yl = common_series(books[(n_top, kP, hP)]["recs"], live["recs"])
        o = ols_alpha(ya, yl)
        print(f"  context N{n_top} k{kP} H{hP}: overlap legs {ov['legs']:.1%} "
              f"corr {o['corr']:+.3f}")
    ov = leg_overlap(res_pk["recs"], live["recs"])
    ya, yl = common_series(res_pk["recs"], live["recs"])
    o = ols_alpha(ya, yl)
    print(f"  context pct_k14 N{nP} k{kP} H{hP} (same ranker as live): overlap legs "
          f"{ov['legs']:.1%} corr {o['corr']:+.3f}")

    # ---- diagnostics on the primary ----
    print()
    for line in diagnostics(books[PRIMARY], w, f"PRIMARY N{nP} raw7 k{kP} H{hP}"):
        print(line)
    print()
    for line in diagnostics(live, w, "LIVE sim pct_k14 k4 H10"):
        print(line)


if __name__ == "__main__":
    main()
