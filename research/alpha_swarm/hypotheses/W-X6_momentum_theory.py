#!/usr/bin/env python3
"""W-X6 — momentum theory alignment. PRE-REGISTERED before first run (swarm rules).

MISSION: test the canonical momentum-literature refinements as LEG-SELECTION LAYERS on
the settled live recipe. NOT sizing overlays (settled dead: A6 vol-managed REFUTED,
W-A5 weighting REFUTED). NO live wiring.

BASELINE (the live book verbatim, per 18622d3 W-X4 wiring): pct_k(14) rank, k=4/leg,
H=10, top-50 crypto universe MINUS the 20 declared SECTOR_MAP MEME names (both legs),
equal weight, >=61-bar eligibility, decide completed bar i, fill open[i+1], exit
open[i+1+H], non-overlapping (start=66). Must reproduce W-X4 PRIMARY to the digit:
gross +3.88 / net25 +3.68 / Sh 0.636 / n=33 (asserted at runtime).

DATA: W-X2_cache_daily.json (2026-07-20, 401 daily bars, top-50 by dayNtlVlm + BTC/ETH).
Survivor-biased: today's listings; every positive number is an upper bound. seed=42,
no network. Shared W-X2 engine imported; my generic runner is asserted EQUAL to
wx2.run_book on the baseline (ev + turnover series, 1e-12) before any layer runs.

COUSIN PRIORS (checked in findings/ BEFORE registering — layers must be genuinely new):
- B8 momentum_12_1_reversal: REFUTED standalone (raw-ret skip ranks, L30/60/90 x
  skip0/7/14, k8 daily-rebal, no meme exclusion). Mechanism: "the recent 7d window
  CARRIES the signal". Cell B differs: crypto-scaled shorter windows (28/7, 14/3), the
  live pct_k ranker family, H10 non-overlapping k4 meme-excluded. Expectation from the
  cousin is refutation; run to close the cell ON the live recipe, honestly framed.
- A2 tsmom: MARGINAL (absolute momentum standalone, L30/H14 keeper, down-tape loaded).
  Cell A differs: absolute trend used as an INTERSECTION FILTER on the xs legs (dual
  momentum, Antonacci), never tested here as a layer.
- A7 momentum_of_momentum: MARGINAL (acceleration decays to ~0 in h2 dense test).
  Nearest neighbor to cell C but acceleration != smoothness; FIP is new.
- W-A4 idio_momentum_residual: MARGINAL (1F BTC-residual lb14 beats RAW-RET rank,
  Sh 0.56 vs 0.23 at H10; recommendation was shadow A/B vs pct_k, never a flip).
  Cell E's new content = the SECOND factor (ETH); 1F is re-run as the mid rung so the
  2F increment is located exactly: baseline pct_k14 vs r1F vs r2F.
- W-X3 liquid_majors_xs: REFUTED + NOT ADDITIVE (top-15 by median notional; its only
  gate-passer was pct_k14-on-majors = diluted live book, 49% duplicate legs, Sh 0.360
  vs 0.589). Cell D is the direct heir: KEEPS meme exclusion + live ranker, restricts
  eligibility to the top 30d-notional quantile (Lee-Swaminathan attention proxy; no
  mcap on perps so notional rank is declared as the proxy, conflating size — stated).
  Expectation from the cousin is dilution/refutation; run to close it as a LAYER.
- W-A3 short_leg_is_beta: short-side xs PnL in this tape is heavily down-beta. This is
  exactly the Daniel-Moskowitz crash exposure cell F hunts (short leg crushed when the
  market rebounds after a drawdown).

SHARED RECORD PASS (one deterministic sweep, every cell selects from the same records)
  Per rebalance i (baseline grid): pool = eligible coins with pct_k14 non-None AND
  bars at i+1 / i+1+H (b1 open > 0) — the baseline scored set. For each pool coin:
  fwd = open[i+1+H]/open[i+1] - 1 and aux scores (None allowed, coverage reported):
    tr14      trailing 14d return (close[i]/close[i-14]-1, coin's own traded days)
    id14      information discreteness = max|daily ret| / sum|daily ret| over the
              trailing 14 completed bars (>=10 rets required; LOW = smooth accruer)
    ntl30     30d mean daily notional (close*volume)
    r1f       W-A4 residual: rc14 - beta*rBTC14, beta = OLS on trailing 30 daily rets
    r2f       2-factor residual: rc14 - b1*rBTC14 - b2*rETH14, (b1,b2) = joint OLS
              (with intercept) on trailing 30 daily rets; fallback to (beta_1F, 0)
              ONLY on numerical singularity det <= 1e-12*Sbb*See. BTC/ETH daily corr
              and cross-sectional beta dispersion reported as noise diagnostics.
    pks_28_7  pct_k(28) evaluated at bar i-7   (skip ranker, channel form)
    pks_14_3  pct_k(14) evaluated at bar i-3
    ri_28_7   intermediate return close[i-7]/close[i-28]-1 (Novy-Marx form)
    ri_14_3   intermediate return close[i-3]/close[i-14]-1
  BTC state at i: dd90 = close/max(trailing 90 closes) - 1; r14 = BTC trailing 14d ret.

CELLS (selection layers; k=4 slots/leg everywhere)
  A dual_momentum   longs = baseline top-4 pct_k14 KEEPING only tr14 > 0; shorts =
                    baseline bottom-4 KEEPING only tr14 < 0 (tr14 None fails the
                    filter). Undersized book holds FEWER legs (empty slots = cash),
                    never reaches down-rank.
  B skip_window     full k4 books ranked on {pks_28_7, pks_14_3, ri_28_7, ri_14_3}.
  C frog_in_pan     PRIMARY: longs = the 4 LOWEST id14 among the top-8 by pct_k14;
                    shorts = the 4 lowest id14 among the bottom-8. SENSITIVITY:
                    pool widened to 12. id14 None sorts last (prefer measurable
                    smoothness); deterministic tiebreak = pct_k rank position.
  D turnover_cond   PRIMARY: eligible pool cut to the top TERCILE by ntl30 (ceil(n/3))
                    BEFORE the pct_k14 ranking; SENSITIVITY: top HALF. Leg overlap vs
                    baseline reported (W-X3 dilution check).
  E two_factor_resid full k4 books ranked on r1f and on r2f. The judged layer is r2f;
                    r1f is the locating rung (W-A4 cousin re-run on THIS baseline).
  F momentum_crash  DIAGNOSTIC FIRST (Daniel-Moskowitz). Qualifying rebalance (ex-ante,
                    decision bar i): dd90 <= -0.15 AND r14 > 0 (post-drawdown rebound).
                    Report: qualifying rebalance count n_q, distinct episodes (maximal
                    consecutive runs), and the BASELINE short-leg contribution
                    (-(0.5/k)*sum short fwd) inside qualifying windows ONLY vs outside.
                    IF n_q >= 5: score TWO asymmetric layers — SHORT-HALF (short slots
                    at weight 0.5 during qualifying) and SHORT-ZERO (short slots to
                    cash during qualifying). ELSE: verdict BLOCKED-DATA with the
                    forward condition stated; do not force it.

ACCOUNTING (slot convention — reduces EXACTLY to the W-X2 convention for full books)
  Book = 2k slots. ev_t = (0.5/k)*sum(long fwd) - (0.5/k)*sum(w_s * short fwd); empty
  slot = cash = 0. Turnover (round-trip cost charged at open, wx2 convention):
  turn_t = sum(weight-increases of positions vs previous book) / (2k); first rebalance
  charges the full opening book. net_t = ev_t - tier*turn_t, tiers 0/12/25/50 bps RT.
  Sharpe-like = mean/pstdev of net25 series. OOS = first/second half of rebalances.
  $-line: $76.8/leg live sizing (0.10 equity-frac x 12x), book gross = 8*$76.8 =
  $614.4, $/wk = net25 * 614.4 * 7/10. NULL (diagnostic only): 2000 seeded matched
  draws — same dates, same pool, same n_long/n_short/short-weight, random names;
  p = frac(null mean gross >= observed mean gross).

DECISION GATE (pre-registered; identical to W-X4 — these are MODIFICATIONS of the
live book, so the bar is STRICT DOMINANCE, not significance):
  DOMINANT (propose wiring + spec + pre-committed revert) iff the layer beats BASELINE
  on net25 EV AND net25 Sharpe-like in BOTH halves (4/4).
  MARGINAL iff full-sample net25 EV AND Sharpe both beat baseline but any half fails.
  REFUTED-AS-LAYER otherwise (the standalone book may still be +EV; reported).
  Significance (paired t on per-rebalance net25 delta; matched-null p) reported
  SEPARATELY and never substitutes for dominance. Every cell reports the layer's
  MARGINAL TURNOVER COST: mean turnover vs baseline and the 25bps fee delta
  (%/rebal and $/wk). Delta identity net = long + short + fee asserted per rebalance.

Read-only research. No live wiring. One script, six findings files. seed=42.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import os
import random
import statistics
import sys
from typing import Callable, Dict, List, Optional, Tuple

REPO = "/Users/julian_dev/Documents/code/hermes-trader"
HYP = os.path.join(REPO, "research", "alpha_swarm", "hypotheses")

_spec = importlib.util.spec_from_file_location("wx2", os.path.join(HYP, "W-X2_xs_widening.py"))
wx2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wx2)

T, O, H_, L_, C, V = wx2.T, wx2.O, wx2.H_, wx2.L_, wx2.C, wx2.V
K, HOLD, START = 4, 10, 66
SEED, N_NULL = 42, 2000
TIERS = wx2.SLIP_TIERS
MEMES = {c for c, s in wx2.SECTOR_MAP.items() if s == "MEME"}
LEG_USD = 76.8
GROSS_USD = 2 * K * LEG_USD

# Cell F pre-registered regime thresholds (crypto-scaled Daniel-Moskowitz, declared
# BEFORE first run): BTC >=15% below its trailing 90d close-high AND trailing 14d
# BTC return > 0 at the decision bar.
F_DD, F_REB = -0.15, 0.0


# ---------------------------------------------------------------------------- aux scores
def id14(w: "wx2.World", coin: str, i: int) -> Optional[float]:
    """Information discreteness over trailing 14 completed bars: max|r|/sum|r|."""
    cl = w.closes_upto(coin, i, 15)
    rets = wx2.daily_rets(cl)
    if len(rets) < 10:
        return None
    tot = sum(abs(r) for r in rets)
    if tot <= 0:
        return None
    return max(abs(r) for r in rets) / tot


def beta2_ols(rc: List[float], rb: List[float], re_: List[float]
              ) -> Tuple[float, float, bool]:
    """Joint OLS (with intercept) of coin daily rets on BTC and ETH daily rets.
    Returns (b_btc, b_eth, fell_back). Fallback to (1F beta, 0) only on singularity."""
    n = min(len(rc), len(rb), len(re_))
    if n < 8:
        return 1.0, 0.0, True
    rc, rb, re_ = rc[-n:], rb[-n:], re_[-n:]
    mc, mb, me = (sum(x) / n for x in (rc, rb, re_))
    b = [x - mb for x in rb]
    e = [x - me for x in re_]
    c_ = [x - mc for x in rc]
    Sbb = sum(x * x for x in b)
    See = sum(x * x for x in e)
    Sbe = sum(x * y for x, y in zip(b, e))
    Sbc = sum(x * y for x, y in zip(b, c_))
    Sec = sum(x * y for x, y in zip(e, c_))
    det = Sbb * See - Sbe * Sbe
    if det <= 1e-12 * max(Sbb * See, 1e-30):
        return wx2.beta_ols(rc, rb), 0.0, True
    b1 = (See * Sbc - Sbe * Sec) / det
    b2 = (Sbb * Sec - Sbe * Sbc) / det
    return b1, b2, False


def r2f_score(w: "wx2.World", coin: str, i: int, lb: int = 14, bw: int = 30
              ) -> Optional[Tuple[float, float, float, bool]]:
    rc = wx2.trailing_ret(w, coin, i, lb)
    rb = wx2.trailing_ret(w, "BTC", i, lb)
    re_ = wx2.trailing_ret(w, "ETH", i, lb)
    if rc is None or rb is None or re_ is None:
        return None
    b1, b2, fb = beta2_ols(wx2.daily_rets(w.closes_upto(coin, i, bw + 1)),
                           wx2.daily_rets(w.closes_upto("BTC", i, bw + 1)),
                           wx2.daily_rets(w.closes_upto("ETH", i, bw + 1)))
    return rc - b1 * rb - b2 * re_, b1, b2, fb


def ret_intermediate(w: "wx2.World", coin: str, i: int, lb: int, skip: int
                     ) -> Optional[float]:
    """Return over [i-lb, i-skip] = trailing (lb-skip)-day return evaluated at i-skip."""
    if i - skip < 0:
        return None
    return wx2.trailing_ret(w, coin, i - skip, lb - skip)


# ---------------------------------------------------------------------------- records
def build_records(w: "wx2.World", coins: List[str]) -> List[dict]:
    elig = wx2.elig_std(w, coins, 61)
    recs = []
    i = START
    while i + 1 + HOLD < len(w.days):
        pool: List[str] = []
        pk: Dict[str, float] = {}
        fwd: Dict[str, float] = {}
        aux: Dict[str, Dict[str, Optional[float]]] = {
            k_: {} for k_ in ("tr14", "id14", "ntl30", "r1f", "r2f",
                              "pks_28_7", "pks_14_3", "ri_28_7", "ri_14_3")}
        b2meta: Dict[str, Tuple[float, float, bool]] = {}
        for c in elig(i):
            b1, b2 = w.bar(c, i + 1), w.bar(c, i + 1 + HOLD)
            if b1 is None or b2 is None or b1[O] <= 0:
                continue
            s = wx2.pctk(w, c, i, 14)
            if s is None:
                continue
            pool.append(c)
            pk[c] = s
            fwd[c] = b2[O] / b1[O] - 1.0
            aux["tr14"][c] = wx2.trailing_ret(w, c, i, 14)
            aux["id14"][c] = id14(w, c, i)
            aux["ntl30"][c] = w.mean_notional_30d(c, i)
            aux["r1f"][c] = wx2.residual_ret(w, c, i, 14, "BTC")
            z = r2f_score(w, c, i)
            if z is None:
                aux["r2f"][c] = None
            else:
                aux["r2f"][c] = z[0]
                b2meta[c] = (z[1], z[2], z[3])
            aux["pks_28_7"][c] = wx2.pctk(w, c, i - 7, 28) if i - 7 >= 0 else None
            aux["pks_14_3"][c] = wx2.pctk(w, c, i - 3, 14) if i - 3 >= 0 else None
            aux["ri_28_7"][c] = ret_intermediate(w, c, i, 28, 7)
            aux["ri_14_3"][c] = ret_intermediate(w, c, i, 14, 3)
        if len(pool) < 2 * K:
            i += HOLD
            continue
        btc_cl = w.closes_upto("BTC", i, 90)
        dd90 = btc_cl[-1] / max(btc_cl) - 1.0 if btc_cl else 0.0
        r14 = wx2.trailing_ret(w, "BTC", i, 14) or 0.0
        recs.append({"i": i, "t": w.days[i], "pool": pool, "pk": pk, "fwd": fwd,
                     "aux": aux, "b2meta": b2meta, "dd90": dd90, "btc_r14": r14,
                     "crash": dd90 <= F_DD and r14 > F_REB})
        i += HOLD
    return recs


# ---------------------------------------------------------------------------- engine
Sel = Callable[[dict], Tuple[List[str], List[str], float]]


def baseline_sel(rec: dict) -> Tuple[List[str], List[str], float]:
    ranked = sorted(rec["pool"], key=lambda c: -rec["pk"][c])
    return ranked[:K], ranked[-K:], 1.0


def run_layer(recs: List[dict], sel: Sel) -> List[dict]:
    out = []
    prev: Dict[Tuple[str, str], float] = {}
    for rec in recs:
        longs, shorts, ws = sel(rec)
        assert not set(longs) & set(shorts), (longs, shorts)
        assert len(longs) <= K and len(shorts) <= K
        fwd = rec["fwd"]
        ev = (0.5 / K) * sum(fwd[c] for c in longs) \
            - (0.5 / K) * ws * sum(fwd[c] for c in shorts)
        book = {(c, "L"): 1.0 for c in longs}
        book.update({(c, "S"): ws for c in shorts})
        turn = sum(max(0.0, wgt - prev.get(pos, 0.0)) for pos, wgt in book.items()) \
            / (2 * K)
        prev = book
        out.append({"t": rec["t"], "ev": ev, "turnover": turn, "longs": longs,
                    "shorts": shorts, "ws": ws, "rec": rec})
    return out


def net25(recs: List[dict]) -> List[float]:
    return [r["ev"] - 0.0025 * r["turnover"] for r in recs]


def ev_sh(xs: List[float]) -> Tuple[float, float]:
    return statistics.mean(xs), statistics.mean(xs) / (statistics.pstdev(xs) + 1e-12)


def summarize(recs: List[dict], label: str, with_null: bool = True) -> dict:
    evs = [r["ev"] for r in recs]
    gross = statistics.mean(evs)
    out = {"label": label, "n": len(recs), "gross_pct": round(100 * gross, 3),
           "mean_turnover": round(statistics.mean(r["turnover"] for r in recs), 3),
           "mean_legs": round(statistics.mean(len(r["longs"]) + len(r["shorts"])
                                              for r in recs), 2)}
    for tier in TIERS:
        nets = [r["ev"] - tier * r["turnover"] for r in recs]
        out[f"net{int(tier*10000)}"] = round(100 * statistics.mean(nets), 3)
    s = net25(recs)
    out["sharpe"] = round(statistics.mean(s) / (statistics.pstdev(s) + 1e-12), 3)
    h = len(s) // 2
    out["oos"] = [round(100 * statistics.mean(s[:h]), 3),
                  round(100 * statistics.mean(s[h:]), 3)]
    out["ann_pct"] = round(100 * statistics.mean(s) * 365 / HOLD, 1)
    out["usd_week"] = round(statistics.mean(s) * GROSS_USD * 7 / HOLD, 2)
    if with_null:
        out["null_p"] = round(null_p_var(recs, gross), 4)
    return out


def null_p_var(recs: List[dict], obs_gross: float, n_null: int = N_NULL,
               seed: int = SEED) -> float:
    """Matched null: same dates/pools/leg-counts/short-weight, random names."""
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_null):
        tot = 0.0
        for r in recs:
            pool, fwd = r["rec"]["pool"], r["rec"]["fwd"]
            nl, ns = len(r["longs"]), len(r["shorts"])
            pick = rng.sample(pool, nl + ns)
            lo, sh = pick[:nl], pick[nl:]
            tot += (0.5 / K) * sum(fwd[c] for c in lo) \
                - (0.5 / K) * r["ws"] * sum(fwd[c] for c in sh)
        if tot / len(recs) >= obs_gross:
            ge += 1
    return ge / n_null


def fmt(row: dict) -> str:
    return (f"{row['label']:<40} n={row['n']:<3} legs {row['mean_legs']:>4}"
            f" gross {row['gross_pct']:+.2f}% net25 {row['net25']:+.2f}%"
            f" net50 {row['net50']:+.2f}% ann {row['ann_pct']:+.1f}%"
            f" oos {row['oos'][0]:+.2f}/{row['oos'][1]:+.2f}"
            f" Sh {row['sharpe']:+.3f}"
            + (f" p={row['null_p']}" if "null_p" in row else "")
            + f" turn {row['mean_turnover']:.2f} ${row['usd_week']:+.2f}/wk")


# ---------------------------------------------------------------------------- gate
def dominance_row(recs: List[dict]) -> dict:
    s = net25(recs)
    h = len(s) // 2
    return {"full": ev_sh(s), "h1": ev_sh(s[:h]), "h2": ev_sh(s[h:]), "n": len(s)}


def gate(var: List[dict], base: List[dict], name: str) -> Tuple[str, List[str]]:
    """Pre-registered verdict vs baseline + paired stats + marginal turnover cost."""
    rv, rb = dominance_row(var), dominance_row(base)
    lines = []
    wins = 0
    for half in ("h1", "h2"):
        for j, m in ((0, "EV"), (1, "Sh")):
            wk = rv[half][j] > rb[half][j]
            wins += wk
            lines.append(f"  {half} {m}: {'WIN ' if wk else 'LOSE'} "
                         f"({rv[half][j]:+.4f} vs {rb[half][j]:+.4f})")
    full_ok = rv["full"][0] > rb["full"][0] and rv["full"][1] > rb["full"][1]
    if wins == 4:
        verdict = "DOMINANT"
    elif full_ok:
        verdict = "MARGINAL"
    else:
        verdict = "REFUTED-AS-LAYER"
    # paired per-rebalance delta with exact long/short/fee split
    mb = {r["t"]: r for r in base}
    d_net, d_long, d_short, d_fee = [], [], [], []
    for r in var:
        b = mb.get(r["t"])
        if b is None:
            continue
        fwd = r["rec"]["fwd"]
        dl = (0.5 / K) * (sum(fwd[c] for c in r["longs"])
                          - sum(fwd[c] for c in b["longs"]))
        ds = -(0.5 / K) * (r["ws"] * sum(fwd[c] for c in r["shorts"])
                           - b["ws"] * sum(fwd[c] for c in b["shorts"]))
        df = -0.0025 * (r["turnover"] - b["turnover"])
        dn = (r["ev"] - 0.0025 * r["turnover"]) - (b["ev"] - 0.0025 * b["turnover"])
        assert abs(dn - (dl + ds + df)) < 1e-12
        d_net.append(dn); d_long.append(dl); d_short.append(ds); d_fee.append(df)
    n = len(d_net)
    mu = statistics.mean(d_net)
    sd = statistics.stdev(d_net) if n > 1 else 0.0
    t = mu / (sd / math.sqrt(n)) if sd > 0 else 0.0
    h = n // 2
    tv = statistics.mean(r["turnover"] for r in var)
    tb = statistics.mean(r["turnover"] for r in base)
    lines.append(f"  paired delta net25 {100*mu:+.3f}%/rebal "
                 f"(h1 {100*statistics.mean(d_net[:h]):+.3f} / "
                 f"h2 {100*statistics.mean(d_net[h:]):+.3f}), t={t:+.2f}, n={n}")
    lines.append(f"  delta split: long {100*statistics.mean(d_long):+.3f} "
                 f"short {100*statistics.mean(d_short):+.3f} "
                 f"fee {100*statistics.mean(d_fee):+.3f}")
    lines.append(f"  marginal turnover: {tv:.3f} vs {tb:.3f} "
                 f"(fee delta {100*0.0025*(tv-tb):+.4f}%/rebal, "
                 f"${0.0025*(tv-tb)*GROSS_USD*7/HOLD:+.3f}/wk)")
    lines.append(f"  $-delta at sizing: {(mu)*GROSS_USD*7/HOLD:+.2f}/wk")
    return f"{name}: {verdict} ({wins}/4 dominance checks)", lines


# ---------------------------------------------------------------------------- cells
def sel_A(rec: dict) -> Tuple[List[str], List[str], float]:
    lo, sh, _ = baseline_sel(rec)
    tr = rec["aux"]["tr14"]
    longs = [c for c in lo if tr.get(c) is not None and tr[c] > 0]
    shorts = [c for c in sh if tr.get(c) is not None and tr[c] < 0]
    return longs, shorts, 1.0


def make_sel_rank(key: str) -> Sel:
    def f(rec: dict) -> Tuple[List[str], List[str], float]:
        sc = rec["aux"][key]
        pool = [c for c in rec["pool"] if sc.get(c) is not None]
        ranked = sorted(pool, key=lambda c: -sc[c])
        return ranked[:K], ranked[-K:], 1.0
    return f


def make_sel_fip(width: int) -> Sel:
    def f(rec: dict) -> Tuple[List[str], List[str], float]:
        idm = rec["aux"]["id14"]
        ranked = sorted(rec["pool"], key=lambda c: -rec["pk"][c])
        top, bot = ranked[:width], ranked[-width:]
        smooth = lambda seq: sorted(
            seq, key=lambda c: (idm.get(c) if idm.get(c) is not None else float("inf"),
                                seq.index(c)))[:K]
        longs = smooth(top)
        shorts = smooth(bot[::-1])   # bottom-ranked first as tiebreak anchor
        return longs, shorts, 1.0
    return f


def make_sel_ntl(frac: float) -> Sel:
    def f(rec: dict) -> Tuple[List[str], List[str], float]:
        ntl = rec["aux"]["ntl30"]
        n_keep = math.ceil(len(rec["pool"]) * frac)
        kept = sorted(rec["pool"], key=lambda c: -(ntl.get(c) or 0.0))[:n_keep]
        ranked = sorted(kept, key=lambda c: -rec["pk"][c])
        if len(ranked) < 2 * K:
            return [], [], 1.0
        return ranked[:K], ranked[-K:], 1.0
    return f


def make_sel_crash(ws_crash: float) -> Sel:
    def f(rec: dict) -> Tuple[List[str], List[str], float]:
        lo, sh, _ = baseline_sel(rec)
        if rec["crash"]:
            return (lo, sh, ws_crash) if ws_crash > 0 else (lo, [], 1.0)
        return lo, sh, 1.0
    return f


# ---------------------------------------------------------------------------- selftest
def selftest() -> None:
    day0 = 1_700_000_000_000

    def mk(rets, vol=1_000_000.0, px0=100.0):
        rows, px = [], px0
        for t, r in enumerate(rets):
            o = px
            px = px * (1 + r)
            rows.append([day0 + t * 86_400_000, o, max(o, px), min(o, px), px, vol])
        return rows

    n_days = 200
    # --- aux score exactness on constructed data ---------------------------------
    btc = [0.01 if t % 2 == 0 else -0.008 for t in range(n_days)]
    eth = [0.012 if t % 3 == 0 else -0.006 for t in range(n_days)]
    mix = [0.5 * b + 0.3 * e + 0.001 for b, e in zip(btc, eth)]
    smooth = [0.01] * n_days
    jumpy = [0.0] * n_days
    jumpy[150] = 0.14   # one discrete day inside the trailing window at i=155
    candles = {"BTC": mk(btc), "ETH": mk(eth), "MIX": mk(mix),
               "SMOOTH": mk(smooth), "JUMPY": mk(jumpy)}
    w = wx2.World(candles, sorted(candles))
    b1, b2, fb = beta2_ols(wx2.daily_rets(w.closes_upto("MIX", 199, 31)),
                           wx2.daily_rets(w.closes_upto("BTC", 199, 31)),
                           wx2.daily_rets(w.closes_upto("ETH", 199, 31)))
    assert not fb and abs(b1 - 0.5) < 1e-9 and abs(b2 - 0.3) < 1e-9, (b1, b2, fb)
    z = r2f_score(w, "MIX", 199)
    assert z is not None and z[0] > 0, z          # +0.1%/d alpha -> positive residual
    # singular fallback: ETH == BTC clone
    cl = {"BTC": mk(btc), "ETH": mk(btc), "MIX": mk(mix)}
    w2 = wx2.World(cl, sorted(cl))
    _, b2e, fb2 = beta2_ols(wx2.daily_rets(w2.closes_upto("MIX", 199, 31)),
                            wx2.daily_rets(w2.closes_upto("BTC", 199, 31)),
                            wx2.daily_rets(w2.closes_upto("ETH", 199, 31)))
    assert fb2 and b2e == 0.0
    ids, idj = id14(w, "SMOOTH", 199), id14(w, "JUMPY", 155)
    assert ids is not None and abs(ids - 1 / 14) < 1e-9, ids
    assert idj is not None and idj == 1.0, idj
    # intermediate return: [i-14, i-3] on a +1%/d coin = 1.01^11 - 1
    ri = ret_intermediate(w, "SMOOTH", 199, 14, 3)
    assert abs(ri - (1.01 ** 11 - 1)) < 1e-12, ri

    # --- engine == wx2.run_book on a baseline-shaped world -----------------------
    rng = random.Random(7)
    candles3 = {"BTC": mk(btc), "ETH": mk(eth)}
    for j in range(12):
        candles3[f"N{j}"] = mk([rng.uniform(-0.03, 0.032) for _ in range(n_days)])
    w3 = wx2.World(candles3, sorted(candles3))
    coins3 = [f"N{j}" for j in range(12)] + ["BTC", "ETH"]
    global HOLD, START
    recs = build_records(w3, coins3)
    assert recs, "no records"
    mine = run_layer(recs, baseline_sel)
    ref = wx2.run_book(w3, wx2.elig_std(w3, coins3, 61),
                       lambda c, i: wx2.pctk(w3, c, i, 14), K, HOLD, start=START)
    assert len(mine) == len(ref["recs"])
    for a, b in zip(mine, ref["recs"]):
        assert a["t"] == b["t"] and abs(a["ev"] - b["ev"]) < 1e-12
        assert abs(a["turnover"] - b["turnover"]) < 1e-12
        assert a["longs"] == b["longs"] and a["shorts"] == b["shorts"]

    # --- selection layers on synthetic records ------------------------------------
    rec = {"t": 0, "pool": [f"C{j}" for j in range(10)],
           "pk": {f"C{j}": 10 - j for j in range(10)},
           "fwd": {f"C{j}": 0.01 * (5 - j) for j in range(10)},
           "aux": {"tr14": {f"C{j}": (0.1 if j in (0, 2) else -0.1) for j in range(10)},
                   "id14": {f"C{j}": (0.9 if j in (0, 7) else 0.1) for j in range(10)},
                   "ntl30": {f"C{j}": 100.0 - 10 * j for j in range(10)},
                   "r1f": {}, "r2f": {}, "pks_28_7": {}, "pks_14_3": {},
                   "ri_28_7": {}, "ri_14_3": {}},
           "b2meta": {}, "dd90": -0.2, "btc_r14": 0.05, "crash": True}
    lo, sh, _ = sel_A(rec)
    assert lo == ["C0", "C2"] and sh == ["C6", "C7", "C8", "C9"], (lo, sh)
    lo, sh, _ = make_sel_fip(8)(rec)   # C0 (id .9) pushed out of top-4, C7 out of bottom
    assert lo == ["C1", "C2", "C3", "C4"] and set(sh) == {"C9", "C8", "C6", "C5"}, (lo, sh)
    lo, sh, _ = make_sel_ntl(0.5)(rec)  # top-5 by notional = C0..C4 < 2k -> empty book
    assert lo == [] and sh == []
    lo, sh, ws = make_sel_crash(0.5)(rec)
    assert ws == 0.5 and len(sh) == K
    lo, sh, ws = make_sel_crash(0.0)(rec)
    assert sh == [] and lo == ["C0", "C1", "C2", "C3"]

    # --- slot EV + weighted turnover ---------------------------------------------
    r0 = dict(rec, fwd={c: 0.02 for c in rec["pool"]})
    seq = [dict(r0, t=0, crash=False), dict(r0, t=1, crash=True),
           dict(r0, t=2, crash=False)]
    out = run_layer([{"i": 0, "t": r["t"], "pool": r["pool"], "pk": r["pk"],
                      "fwd": r["fwd"], "aux": r["aux"], "b2meta": {},
                      "dd90": 0, "btc_r14": 0, "crash": r["crash"]} for r in seq],
                    make_sel_crash(0.5))
    # t=0 full book: ev = (.5/4)*4*.02 - (.5/4)*4*.02 = 0; turnover = 8/8
    assert abs(out[0]["ev"]) < 1e-15 and out[0]["turnover"] == 1.0
    # t=1 shorts halved: ev = .01 - .005 = +.005; turnover 0 (no increases)
    assert abs(out[1]["ev"] - 0.005) < 1e-15 and out[1]["turnover"] == 0.0
    # t=2 shorts restored: turnover = 4*0.5/8 = 0.25
    assert abs(out[2]["ev"]) < 1e-15 and abs(out[2]["turnover"] - 0.25) < 1e-15
    # undersized book EV: 2 longs only
    out2 = run_layer([{"i": 0, "t": 0, "pool": rec["pool"], "pk": rec["pk"],
                       "fwd": {c: 0.02 for c in rec["pool"]}, "aux": rec["aux"],
                       "b2meta": {}, "dd90": 0, "btc_r14": 0, "crash": False}],
                     lambda r: (["C0", "C1"], [], 1.0))
    assert abs(out2[0]["ev"] - (0.5 / 4) * 2 * 0.02) < 1e-15
    assert out2[0]["turnover"] == 2 / 8

    # --- gate: strictly shifted book must be DOMINANT; self-vs-self must not ------
    # base and variant share the SAME record/fwd map (as real books do); the variant
    # longs "Z" whose fwd is exactly +0.01 book-EV better than "A".
    base, var = [], []
    for t in range(20):
        fa = (0.001 * ((-1) ** t) + 0.002) * 2 * K
        shared = {"pool": ["A", "B", "Z"], "fwd": {"A": fa, "B": 0.0, "Z": fa + 0.01 * 2 * K}}
        base.append({"t": t, "ev": (0.5 / K) * fa, "turnover": 0.5, "longs": ["A"],
                     "shorts": ["B"], "ws": 1.0, "rec": shared})
        var.append({"t": t, "ev": (0.5 / K) * (fa + 0.01 * 2 * K), "turnover": 0.5,
                    "longs": ["Z"], "shorts": ["B"], "ws": 1.0, "rec": shared})
    v, _ = gate(var, base, "shift")
    assert "DOMINANT (4/4" in v, v
    v, _ = gate(base, base, "self")
    assert "REFUTED-AS-LAYER" in v, v
    # episode counting
    flags = [False, True, True, False, True, False]
    eps = count_episodes(flags)
    assert eps == 2, eps
    print("W-X6 selftest OK: beta2 exact + singular fallback, id14 exact, intermediate "
          "ret exact, engine == wx2.run_book (ev/turnover/legs), all six selectors, "
          "slot EV + weighted turnover, dominance gate, episode counting")


def count_episodes(flags: List[bool]) -> int:
    eps, prev = 0, False
    for f_ in flags:
        if f_ and not prev:
            eps += 1
        prev = f_
    return eps


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
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
    w, coins = wx2.make_crypto_world(d)
    no_meme = [c for c in coins if c not in MEMES]
    print(f"universe: {len(coins)} top-50, {len(no_meme)} after meme exclusion")

    recs = build_records(w, no_meme)
    base = run_layer(recs, baseline_sel)
    s_base = summarize(base, "BASELINE meme-excl pct_k14 k4 H10")
    print("\n===== baseline =====")
    print(fmt(s_base))
    ok = (abs(s_base["gross_pct"] - 3.88) <= 0.01 and abs(s_base["net25"] - 3.68) <= 0.01
          and s_base["n"] == 33)
    print(f"  reproduction check vs W-X4 PRIMARY (gross +3.88 / net25 +3.68 / n=33): "
          f"{'OK' if ok else 'MISMATCH — investigate before trusting anything below'}")

    # coverage diagnostics
    for key in ("tr14", "id14", "r1f", "r2f", "pks_28_7", "pks_14_3", "ri_28_7", "ri_14_3"):
        tot = sum(len(r["pool"]) for r in recs)
        okn = sum(1 for r in recs for c in r["pool"] if r["aux"][key].get(c) is not None)
        if okn < tot:
            print(f"  coverage {key}: {okn}/{tot}")
    fbn = sum(1 for r in recs for c in r["b2meta"] if r["b2meta"][c][2])
    tot2 = sum(len(r["b2meta"]) for r in recs)
    corr_be = btc_eth_corr(w, recs)
    print(f"  r2f: {fbn}/{tot2} singular fallbacks; mean 30d BTC-ETH daily corr "
          f"{corr_be:+.3f}")

    cells: List[Tuple[str, str, Sel]] = [
        ("A", "A dual-mom intersection (tr14 sign)", sel_A),
        ("B", "B skip pct_k28@-7d", make_sel_rank("pks_28_7")),
        ("B", "B skip pct_k14@-3d", make_sel_rank("pks_14_3")),
        ("B", "B skip ret[28->7]", make_sel_rank("ri_28_7")),
        ("B", "B skip ret[14->3]", make_sel_rank("ri_14_3")),
        ("C", "C FIP smooth-4-of-8 (PRIMARY)", make_sel_fip(8)),
        ("C", "C FIP smooth-4-of-12 (SENS)", make_sel_fip(12)),
        ("D", "D top-tercile ntl30 (PRIMARY)", make_sel_ntl(1 / 3)),
        ("D", "D top-half ntl30 (SENS)", make_sel_ntl(1 / 2)),
        ("E", "E resid 1F BTC lb14 (rung)", make_sel_rank("r1f")),
        ("E", "E resid 2F BTC+ETH lb14 (JUDGED)", make_sel_rank("r2f")),
    ]
    print("\n===== layers =====")
    results = {}
    for cell, label, sel in cells:
        book = run_layer(recs, sel)
        row = summarize(book, label)
        print(fmt(row))
        verdict, lines = gate(book, base, label)
        print(f"  -> {verdict}")
        for ln in lines:
            print(f"    {ln}")
        results[label] = (book, row, verdict)
        if cell == "D":
            ov = statistics.mean(
                len(set(b["longs"] + b["shorts"]) & set(v["longs"] + v["shorts"])) / 8
                for b, v in zip(base, book) if v["longs"])
            print(f"    leg overlap with baseline: {100*ov:.0f}%")

    # ----- cell F: diagnostic first ------------------------------------------------
    print("\n===== cell F: momentum-crash diagnostic =====")
    flags = [r["crash"] for r in recs]
    n_q = sum(flags)
    eps = count_episodes(flags)
    print(f"qualifying rebalances (dd90<={F_DD}, r14>{F_REB}): {n_q}/{len(recs)}, "
          f"distinct episodes {eps}")
    for r in recs:
        if r["crash"]:
            import datetime as dt2
            print(f"  {dt2.datetime.fromtimestamp(r['t']/1000, dt2.timezone.utc):%Y-%m-%d}"
                  f"  dd90 {100*r['dd90']:+.1f}%  btc14 {100*r['btc_r14']:+.1f}%")
    sc_q = [-(0.5 / K) * sum(b["rec"]["fwd"][c] for c in b["shorts"])
            for b in base if b["rec"]["crash"]]
    sc_o = [-(0.5 / K) * sum(b["rec"]["fwd"][c] for c in b["shorts"])
            for b in base if not b["rec"]["crash"]]
    if sc_q:
        print(f"baseline SHORT-leg contribution: qualifying {100*statistics.mean(sc_q):+.3f}%/rebal"
              f" (n={len(sc_q)}) vs elsewhere {100*statistics.mean(sc_o):+.3f}% (n={len(sc_o)})")
    if n_q >= 5:
        for label, ws in (("F SHORT-HALF in crash regime", 0.5),
                          ("F SHORT-ZERO in crash regime", 0.0)):
            book = run_layer(recs, make_sel_crash(ws))
            row = summarize(book, label)
            print(fmt(row))
            verdict, lines = gate(book, base, label)
            print(f"  -> {verdict}")
            for ln in lines:
                print(f"    {ln}")
    else:
        print(f"n_q={n_q} < 5 -> BLOCKED-DATA per pre-registration. Forward condition: "
              f"re-run when the forward sample holds >=5 rebalances with BTC >=15% "
              f"below its 90d close-high AND trailing-14d BTC return > 0 at decision.")


def btc_eth_corr(w: "wx2.World", recs: List[dict]) -> float:
    vals = []
    for r in recs:
        rb = wx2.daily_rets(w.closes_upto("BTC", r["i"], 31))
        re_ = wx2.daily_rets(w.closes_upto("ETH", r["i"], 31))
        n = min(len(rb), len(re_))
        if n < 8:
            continue
        rb, re_ = rb[-n:], re_[-n:]
        mb, me = statistics.mean(rb), statistics.mean(re_)
        cov = sum((a - mb) * (b - me) for a, b in zip(rb, re_))
        den = (sum((a - mb) ** 2 for a in rb) * sum((b - me) ** 2 for b in re_)) ** 0.5
        if den > 0:
            vals.append(cov / den)
    return statistics.mean(vals) if vals else float("nan")


if __name__ == "__main__":
    main()
