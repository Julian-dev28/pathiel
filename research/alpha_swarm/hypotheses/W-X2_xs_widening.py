#!/usr/bin/env python3
"""W-X2 — xs_momentum widening cells. PRE-REGISTERED before first run (swarm rules).

DATA: W-X2_cache_daily.json (see W-X2_fetch_cache.py) — HL daily candleSnapshot,
crypto top-50 by dayNtlVlm (>=$5M, the live universe rule) + ALL xyz-dex markets,
400d requested (xyz max ~208d). Survivor-biased: today's listings. Positive = upper bound.

SHARED ENGINE (all cells)
  - Decide at completed daily bar i (bars <= i only), fill open[i+1], exit open[i+1+H].
  - Non-overlapping rebalances every H bars. A coin is eligible at i iff it has >=61
    prior bars (young-listing guard, min 60d history) AND bars at i, i+1, i+1+H.
  - Book: long top-k / short bottom-k by score, equal weight (W-A5: equal-weight settled).
  - Per-rebalance per-leg signed EV = 0.5*mean(long fwd ret) - 0.5*mean(short fwd ret).
  - Costs: tier bps = ROUND-TRIP cost per replaced position (alpha_lib convention),
    turnover-scaled: net_t = ev_t - tier*turnover_t, turnover_t = fraction of (coin,side)
    book replaced since previous rebalance (first rebalance = 1.0). Tiers 0/12/25/50.
  - OOS: first/second half of rebalance dates. Sharpe-like = mean/std of net25 series.
  - NULL: 2000 seeded draws, matched random book (same rebalance dates, same eligible set,
    same k+k, same fills). p = frac(null mean GROSS EV >= observed mean GROSS EV) —
    gross, because selection skill is what the null tests; costs favor momentum's lower
    turnover and are reported separately.
  - Annualized = mean net25 EV * 365/H. $-line: live sizing = 0.10 equity-frac x 12x on
    main-dex ~$64 => ~$76.8/leg notional (observed legs were $50 at lower equity);
    book gross = 2k*$76.8; $/week = net25 EV * gross * 7/H.

VERDICT GATES (pre-registered)
  ROBUST   : net25 > 0 AND both OOS halves > 0 AND null p < 0.05 AND n >= 15 rebalances.
  MARGINAL : net25 > 0 AND (one OOS half <= 0 OR 0.05 <= p < 0.15 OR n < 15).
  REFUTED  : net25 <= 0 OR (OOS sign-flip AND p >= 0.15).
  BLOCKED-DATA when the inputs cannot support the cell.

CELL A — xs_xyz_equities
  Universe: xyz EQUITY perps only (exclude declared non-equity underlyings NON_EQUITY_XYZ),
  >=61d history, 30d mean daily notional >= $250k (thin-book floor; xyz books are thin —
  $-capacity caveat applies regardless). PRIMARY: score = 7d RESIDUAL momentum vs
  xyz:XYZ100 (beta over 30 daily rets, beta=1 fallback <8 pts), k=5/leg, H=5.
  Robustness (declared, not cherry-picked): raw 7d; pct_k(14); k=8.

CELL B — xs_sector_buckets (prior: C14 sector_rotation REFUTED on 39-coin universe —
  this cell is a targeted extension on top-50 with meme/AI breakouts, judged vs that prior)
  Sector map SECTOR_MAP declared below BEFORE running. (i) WITHIN-BUCKET xs for buckets
  with >=6 members (k=2/leg, 7d raw momentum, H=5) — esp. MEME-only, AI-only.
  (ii) BUCKET ROTATION: long all coins of the strongest bucket / short all of the weakest
  by trailing 7d equal-weight bucket return, H=5. Baseline: flat all-universe xs
  (7d raw, k=8, H=5) on the same top-50.

CELL C — xs_joint_universe
  Universe: crypto top-50 + CELL-A-eligible xyz equities. Score = 7d residual momentum
  vs OWN-WORLD bench (crypto vs BTC, xyz vs xyz:XYZ100), ranked JOINTLY. k=8/leg, H=5.
  Judged vs the separate books (crypto-only residual k=8 and cell-A xyz book): does the
  joint book beat max(separate Sharpe), and what is the corr of the separate series?

CELL D — xs_hold_sweep (live recipe, hold was inherited, never swept)
  Universe: crypto top-50. Ranking: pct_k(14) (ranker_ab: KEEP-PCTK settled).
  k=8 (design) and k=4 (live, capital-capped). H in {3,5,10,20}, net turnover-scaled fees.

CELL E — stack settled winners onto the best new cell
  GATED ON PRIORS: A6 vol_managed REFUTED (negative Sharpe lift all configs), W-A5
  rank/inv-vol weighting REFUTED (equal-weight settled). No settled winner exists to
  stack => cell E is vacuous by pre-registration; documented in the findings.

Read-only research. No live wiring. seed=42.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO = str(Path(__file__).resolve().parents[3])
CACHE = os.path.join(REPO, "research", "alpha_swarm", "hypotheses", "W-X2_cache_daily.json")

T, O, H_, L_, C, V = 0, 1, 2, 3, 4, 5
SLIP_TIERS = [0.0, 0.0012, 0.0025, 0.0050]   # round-trip fractions: 0/12/25/50 bps
SEED = 42
N_NULL = 2000

# ---- Cell A: declared non-equity xyz underlyings (indices/commodities/fx/crypto-wrap) --
NON_EQUITY_XYZ = {
    "xyz:XYZ100", "xyz:SP500", "xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL", "xyz:CL",
    "xyz:NATGAS", "xyz:COPPER", "xyz:PLAT", "xyz:URAN", "xyz:EURUSD", "xyz:GBPUSD",
    "xyz:USDJPY", "xyz:USDCHF", "xyz:AUDUSD", "xyz:USDCAD", "xyz:NIKKEI", "xyz:DAX",
    "xyz:FTSE100", "xyz:HSI", "xyz:XAU", "xyz:XAG", "xyz:WTI", "xyz:BTCEQ",
    "xyz:PURRDAT", "xyz:DRAM",   # PURRDAT = HL-data basket, DRAM = memory-chip basket (not single stocks; kept OUT of the equity cell)
}

# ---- Cell B: sector map over the top-50 crypto universe (declared BEFORE running B) ----
SECTOR_MAP = {
    # L1 / L2 platform
    "BTC": "L1", "ETH": "L1", "SOL": "L1", "AVAX": "L1", "ADA": "L1", "SUI": "L1",
    "APT": "L1", "NEAR": "L1", "TON": "L1", "DOT": "L1", "SEI": "L1", "TIA": "L1",
    "ARB": "L1", "OP": "L1", "LTC": "L1", "BCH": "L1", "XRP": "L1", "TRX": "L1",
    "HYPE": "L1", "BERA": "L1", "XLM": "L1", "HBAR": "L1", "ATOM": "L1", "MNT": "L1",
    "ZEC": "L1", "XMR": "L1", "DASH": "L1", "KAS": "L1", "ETC": "L1", "IP": "L1",
    "MEGA": "L1", "XPL": "L1", "MON": "L1",
    # DeFi
    "AAVE": "DEFI", "UNI": "DEFI", "CRV": "DEFI", "LDO": "DEFI", "PENDLE": "DEFI",
    "ENA": "DEFI", "JUP": "DEFI", "AERO": "DEFI", "MORPHO": "DEFI", "LINK": "DEFI",
    "ONDO": "DEFI", "SKY": "DEFI", "EIGEN": "DEFI", "ETHFI": "DEFI", "JTO": "DEFI",
    "RAY": "DEFI", "LIT": "DEFI", "ASTER": "DEFI", "LINEA": "DEFI", "PYTH": "DEFI",
    "STRK": "DEFI", "DYDX": "DEFI",
    # Meme
    "DOGE": "MEME", "kPEPE": "MEME", "kBONK": "MEME", "WIF": "MEME", "FARTCOIN": "MEME",
    "TRUMP": "MEME", "SPX": "MEME", "PENGU": "MEME", "POPCAT": "MEME", "MEW": "MEME",
    "MOODENG": "MEME", "PUMP": "MEME", "CASHCAT": "MEME", "PURR": "MEME", "VINE": "MEME",
    "TURBO": "MEME", "HMSTR": "MEME", "BABY": "MEME", "kSHIB": "MEME", "kFLOKI": "MEME",
    # AI / DePIN-compute
    "TAO": "AI", "FET": "AI", "RENDER": "AI", "WLD": "AI", "VIRTUAL": "AI", "AI16Z": "AI",
    "KAITO": "AI", "GRASS": "AI", "IO": "AI", "AIXBT": "AI", "VVV": "AI", "NIL": "AI",
    "GOAT": "AI", "PROMPT": "AI",
    # Infra / data / storage / oracle-adjacent
    "FIL": "INFRA", "AR": "INFRA", "GRT": "INFRA", "STX": "INFRA", "CELO": "INFRA",
    "INJ": "INFRA", "LAYER": "INFRA", "W": "INFRA", "ZRO": "INFRA", "MANTA": "INFRA",
    "ICP": "INFRA", "PAXG": "INFRA", "ORDI": "INFRA",
    # Exchange / CEX tokens
    "BNB": "EXCH", "OKB": "EXCH", "BGB": "EXCH", "CRO": "EXCH", "GT": "EXCH",
    "KCS": "EXCH", "MEXC": "EXCH", "HT": "EXCH",
}

# ---------------------------------------------------------------------------- data layer
def load_cache(path: str = CACHE) -> dict:
    d = json.load(open(path))
    return d


class World:
    """Timestamp-aligned daily bars for a set of coins."""

    def __init__(self, candles: Dict[str, List[List[float]]], coins: List[str]):
        self.bars: Dict[str, Dict[int, List[float]]] = {}
        days = set()
        for c in coins:
            rows = candles.get(c) or []
            m = {int(r[T]): r for r in rows}
            if m:
                self.bars[c] = m
                days |= set(m)
        self.days = sorted(days)
        self.day_ix = {d: i for i, d in enumerate(self.days)}

    def closes_upto(self, coin: str, i: int, n: int) -> List[float]:
        """Last n closes for coin among grid days <= i (only days the coin traded)."""
        out = []
        for j in range(i, -1, -1):
            b = self.bars.get(coin, {}).get(self.days[j])
            if b is not None:
                out.append(b[C])
                if len(out) == n:
                    break
        return out[::-1]

    def bar(self, coin: str, i: int) -> Optional[List[float]]:
        if i < 0 or i >= len(self.days):
            return None
        return self.bars.get(coin, {}).get(self.days[i])

    def history_len(self, coin: str, i: int) -> int:
        m = self.bars.get(coin, {})
        d = self.days[i]
        return sum(1 for t in m if t <= d)

    def mean_notional_30d(self, coin: str, i: int) -> float:
        tot, n = 0.0, 0
        for j in range(max(0, i - 29), i + 1):
            b = self.bars.get(coin, {}).get(self.days[j])
            if b is not None:
                tot += b[C] * b[V]
                n += 1
        return tot / n if n else 0.0


# ---------------------------------------------------------------------------- scores
def trailing_ret(w: World, coin: str, i: int, lb: int) -> Optional[float]:
    cl = w.closes_upto(coin, i, lb + 1)
    if len(cl) < lb + 1 or cl[0] <= 0:
        return None
    return cl[-1] / cl[0] - 1.0


def beta_ols(cr: List[float], br: List[float]) -> float:
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = sum(br) / n
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = sum(cr) / n
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def daily_rets(cl: List[float]) -> List[float]:
    return [cl[j] / cl[j - 1] - 1.0 for j in range(1, len(cl)) if cl[j - 1] > 0]


def residual_ret(w: World, coin: str, i: int, lb: int, bench: str, beta_w: int = 30) -> Optional[float]:
    rc = trailing_ret(w, coin, i, lb)
    rb = trailing_ret(w, bench, i, lb)
    if rc is None or rb is None:
        return None
    b = beta_ols(daily_rets(w.closes_upto(coin, i, beta_w + 1)),
                 daily_rets(w.closes_upto(bench, i, beta_w + 1)))
    return rc - b * rb


def pctk(w: World, coin: str, i: int, n: int = 14) -> Optional[float]:
    hi, lo, cur = None, None, None
    cnt = 0
    for j in range(i, -1, -1):
        b = w.bars.get(coin, {}).get(w.days[j])
        if b is None:
            continue
        if cur is None:
            cur = b[C]
        hi = b[H_] if hi is None else max(hi, b[H_])
        lo = b[L_] if lo is None else min(lo, b[L_])
        cnt += 1
        if cnt == n:
            break
    if cnt < max(4, n // 2) or hi is None or hi <= lo or cur is None:
        return None
    return (cur - lo) / (hi - lo) - 0.5


# ---------------------------------------------------------------------------- backtest
def run_book(w: World, eligible_fn: Callable[[int], List[str]],
             score_fn: Callable[[str, int], Optional[float]],
             k: int, hold: int, start: int = 66) -> dict:
    """Non-overlapping xs book. Returns per-rebalance records incl. per-coin fwd rets."""
    recs = []
    prev_book: set = set()
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
        longs = [c for c, _ in scored[:k]]
        shorts = [c for c, _ in scored[-k:]]
        ev = 0.5 * statistics.mean([fwd_all[c] for c in longs]) \
            - 0.5 * statistics.mean([fwd_all[c] for c in shorts])
        book = {(c, "L") for c in longs} | {(c, "S") for c in shorts}
        turn = 1.0 if not prev_book else len(book - prev_book) / len(book)
        prev_book = book
        recs.append({"i": i, "t": w.days[i], "ev": ev, "turnover": turn,
                     "longs": longs, "shorts": shorts, "elig": list(fwd_all), "fwd": fwd_all})
        i += hold
    return {"recs": recs}


def null_p(recs: List[dict], k: int, obs_gross: float, n_null: int = N_NULL, seed: int = SEED) -> float:
    """Matched random-book null on the SAME rebalance dates/eligible sets/fills."""
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_null):
        tot = 0.0
        for r in recs:
            coins = r["elig"]
            pick = rng.sample(coins, 2 * k)
            lo, sh = pick[:k], pick[k:]
            tot += 0.5 * statistics.mean([r["fwd"][c] for c in lo]) \
                - 0.5 * statistics.mean([r["fwd"][c] for c in sh])
        if tot / len(recs) >= obs_gross:
            ge += 1
    return ge / n_null


def summarize_book(res: dict, k: int, hold: int, label: str, with_null: bool = True) -> dict:
    recs = res["recs"]
    if len(recs) < 4:
        return {"label": label, "n": len(recs), "verdict": "BLOCKED-DATA (too few rebalances)"}
    evs = [r["ev"] for r in recs]
    gross = statistics.mean(evs)
    out = {"label": label, "n": len(recs), "k": k, "hold": hold,
           "gross_pct": round(100 * gross, 3),
           "mean_turnover": round(statistics.mean(r["turnover"] for r in recs), 3)}
    for tier in SLIP_TIERS:
        nets = [r["ev"] - tier * r["turnover"] for r in recs]
        out[f"net{int(tier*10000)}"] = round(100 * statistics.mean(nets), 3)
    net25 = [r["ev"] - 0.0025 * r["turnover"] for r in recs]
    out["sharpe_like_net25"] = round(statistics.mean(net25) / (statistics.pstdev(net25) + 1e-12), 3)
    h = len(recs) // 2
    h1 = statistics.mean(net25[:h])
    h2 = statistics.mean(net25[h:])
    out["oos_net25"] = [round(100 * h1, 3), round(100 * h2, 3)]
    out["ann_net25_pct"] = round(100 * statistics.mean(net25) * 365 / hold, 1)
    if with_null:
        out["null_p_gross"] = round(null_p(recs, k, gross), 4)
    # $-line at live sizing: $76.8/leg, 2k legs
    gross_ntl = 2 * k * 76.8
    out["usd_week"] = round(statistics.mean(net25) * gross_ntl * 7 / hold, 2)
    return out


def fmt(row: dict) -> str:
    if "verdict" in row and row.get("verdict", "").startswith("BLOCKED"):
        return f"{row['label']:<42} n={row['n']:<3} {row['verdict']}"
    return (f"{row['label']:<42} n={row['n']:<3} gross {row['gross_pct']:+.2f}%"
            f" net25 {row['net25']:+.2f}% ann {row['ann_net25_pct']:+.1f}%"
            f" oos {row['oos_net25'][0]:+.2f}/{row['oos_net25'][1]:+.2f}"
            f" Sh {row['sharpe_like_net25']:+.3f}"
            + (f" p={row['null_p_gross']}" if "null_p_gross" in row else "")
            + f" turn {row['mean_turnover']:.2f} ${row['usd_week']:+.2f}/wk")


# ---------------------------------------------------------------------------- universes
def make_crypto_world(d: dict) -> Tuple[World, List[str]]:
    coins = [x["coin"] for x in d["meta"]["crypto_universe"]]
    w = World(d["candles"], coins + ["BTC"])
    return w, coins


def make_xyz_world(d: dict) -> Tuple[World, List[str]]:
    coins = [x["coin"] for x in d["meta"]["xyz_universe"]]
    w = World(d["candles"], coins + ["xyz:XYZ100", "xyz:SP500"])
    eq = [c for c in coins if c not in NON_EQUITY_XYZ]
    return w, eq


def elig_std(w: World, coins: List[str], min_hist: int = 61,
             min_ntl: float = 0.0) -> Callable[[int], List[str]]:
    def f(i: int) -> List[str]:
        out = []
        for c in coins:
            if w.history_len(c, i) < min_hist:
                continue
            if min_ntl > 0 and w.mean_notional_30d(c, i) < min_ntl:
                continue
            out.append(c)
        return out
    return f


# ---------------------------------------------------------------------------- cells
def cell_A(d: dict) -> List[dict]:
    w, eq = make_xyz_world(d)
    elig = elig_std(w, eq, 61, 250_000.0)
    rows = []
    prim = run_book(w, elig, lambda c, i: residual_ret(w, c, i, 7, "xyz:XYZ100"), 5, 5)
    rows.append(summarize_book(prim, 5, 5, "A xyz resid7 vs XYZ100 k5 H5 (PRIMARY)"))
    rows.append(summarize_book(run_book(w, elig, lambda c, i: trailing_ret(w, c, i, 7), 5, 5),
                               5, 5, "A xyz raw7 k5 H5"))
    rows.append(summarize_book(run_book(w, elig, lambda c, i: pctk(w, c, i, 14), 5, 5),
                               5, 5, "A xyz pct_k14 k5 H5"))
    rows.append(summarize_book(run_book(w, elig, lambda c, i: residual_ret(w, c, i, 7, "xyz:XYZ100"), 8, 5),
                               8, 5, "A xyz resid7 k8 H5"))
    return rows


def cell_B(d: dict) -> List[dict]:
    w, coins = make_crypto_world(d)
    elig = elig_std(w, coins, 61)
    rows = []
    # baseline: flat all-universe
    rows.append(summarize_book(run_book(w, elig, lambda c, i: trailing_ret(w, c, i, 7), 8, 5),
                               8, 5, "B baseline all-universe raw7 k8 H5"))
    # within-bucket
    buckets: Dict[str, List[str]] = {}
    unmapped = []
    for c in coins:
        s = SECTOR_MAP.get(c)
        if s is None:
            unmapped.append(c)
        else:
            buckets.setdefault(s, []).append(c)
    if unmapped:
        print(f"  [cell B] UNMAPPED top-50 coins (excluded, declare+extend map): {unmapped}")
    for sec, members in sorted(buckets.items()):
        if len(members) < 6:
            rows.append({"label": f"B within-{sec} (n={len(members)})", "n": 0,
                         "verdict": f"BLOCKED-DATA (<6 members: {members})"})
            continue
        e = elig_std(w, members, 61)
        rows.append(summarize_book(run_book(w, e, lambda c, i: trailing_ret(w, c, i, 7), 2, 5),
                                   2, 5, f"B within-{sec} raw7 k2 H5 ({len(members)} coins)"))
    # bucket rotation: long strongest bucket basket, short weakest (>=4 members to form basket)
    rot = run_bucket_rotation(w, buckets, hold=5, lb=7)
    rows.append(summarize_book(rot, max(1, rot.get("k_eff", 4)), 5,
                               "B bucket-rotation 7d H5", with_null=False))
    return rows


def run_bucket_rotation(w: World, buckets: Dict[str, List[str]], hold: int, lb: int) -> dict:
    recs = []
    prev_book: set = set()
    i = 66
    names = sorted(b for b in buckets if len(buckets[b]) >= 4)
    while i + 1 + hold < len(w.days):
        scores = {}
        fwd_all = {}
        members_ok: Dict[str, List[str]] = {}
        for bname in names:
            rets, ok = [], []
            for c in buckets[bname]:
                if w.history_len(c, i) < 61:
                    continue
                r = trailing_ret(w, c, i, lb)
                b1, b2 = w.bar(c, i + 1), w.bar(c, i + 1 + hold)
                if r is None or b1 is None or b2 is None or b1[O] <= 0:
                    continue
                rets.append(r)
                ok.append(c)
                fwd_all[c] = b2[O] / b1[O] - 1.0
            if len(ok) >= 4:
                scores[bname] = statistics.mean(rets)
                members_ok[bname] = ok
        if len(scores) < 3:
            i += hold
            continue
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        longs, shorts = members_ok[best], members_ok[worst]
        ev = 0.5 * statistics.mean([fwd_all[c] for c in longs]) \
            - 0.5 * statistics.mean([fwd_all[c] for c in shorts])
        book = {(c, "L") for c in longs} | {(c, "S") for c in shorts}
        turn = 1.0 if not prev_book else len(book - prev_book) / max(1, len(book))
        prev_book = book
        recs.append({"i": i, "t": w.days[i], "ev": ev, "turnover": turn,
                     "longs": longs, "shorts": shorts,
                     "elig": list(fwd_all), "fwd": fwd_all})
        i += hold
    return {"recs": recs, "k_eff": 4}


def cell_C(d: dict) -> List[dict]:
    cw, ccoins = make_crypto_world(d)
    all_coins = ([x["coin"] for x in d["meta"]["crypto_universe"]]
                 + [x["coin"] for x in d["meta"]["xyz_universe"]]
                 + ["BTC", "xyz:XYZ100", "xyz:SP500"])
    w = World(d["candles"], sorted(set(all_coins)))
    _, eq = make_xyz_world(d)

    def score(c: str, i: int) -> Optional[float]:
        bench = "xyz:XYZ100" if c.startswith("xyz:") else "BTC"
        return residual_ret(w, c, i, 7, bench)

    def elig(i: int) -> List[str]:
        out = []
        for c in ccoins:
            if w.history_len(c, i) >= 61:
                out.append(c)
        for c in eq:
            if w.history_len(c, i) >= 61 and w.mean_notional_30d(c, i) >= 250_000.0:
                out.append(c)
        return out

    rows = []
    joint = run_book(w, elig, score, 8, 5)
    rows.append(summarize_book(joint, 8, 5, "C JOINT crypto+xyz resid7 k8 H5"))
    # separates on the SAME date grid for fair comparison
    sep_c = run_book(w, elig_std(w, ccoins, 61), lambda c, i: residual_ret(w, c, i, 7, "BTC"), 8, 5)
    rows.append(summarize_book(sep_c, 8, 5, "C separate crypto resid7 k8 H5"))
    sep_x = run_book(w, elig_std(w, eq, 61, 250_000.0),
                     lambda c, i: residual_ret(w, c, i, 7, "xyz:XYZ100"), 5, 5)
    rows.append(summarize_book(sep_x, 5, 5, "C separate xyz resid7 k5 H5"))
    # corr of separate series on common dates + joint leg mix
    m1 = {r["t"]: r["ev"] for r in sep_c["recs"]}
    m2 = {r["t"]: r["ev"] for r in sep_x["recs"]}
    common = sorted(set(m1) & set(m2))
    if len(common) >= 6:
        a = [m1[t] for t in common]
        b = [m2[t] for t in common]
        ma, mb = statistics.mean(a), statistics.mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        corr = cov / ((sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5 + 1e-12)
        print(f"  [cell C] corr(separate crypto, separate xyz) on {len(common)} common rebals = {corr:+.3f}")
    mix_l = mix_s = tot = 0
    for r in joint["recs"]:
        mix_l += sum(1 for c in r["longs"] if c.startswith("xyz:"))
        mix_s += sum(1 for c in r["shorts"] if c.startswith("xyz:"))
        tot += len(r["longs"])
    if tot:
        print(f"  [cell C] joint book xyz share: longs {mix_l}/{tot} shorts {mix_s}/{tot}")
    return rows


def cell_D(d: dict) -> List[dict]:
    w, coins = make_crypto_world(d)
    elig = elig_std(w, coins, 61)
    rows = []
    for k in (8, 4):
        for hold in (3, 5, 10, 20):
            r = run_book(w, elig, lambda c, i: pctk(w, c, i, 14), k, hold)
            rows.append(summarize_book(r, k, hold, f"D pct_k14 k{k} H{hold}"))
    return rows


# ---------------------------------------------------------------------------- selftest
def selftest() -> None:
    """Synthetic: 12 coins, 200 days. Coins 0-3 trend +1%/d, 4-7 flat noise-free carriers
    of 0%/d, 8-11 trend -1%/d. Momentum book MUST long the up-trenders, short the
    down-trenders, and earn ~ (1.01^H-1)/2 + (1-0.99^H)/2 per rebalance gross."""
    day0 = 1_700_000_000_000
    candles = {}
    for j in range(12):
        drift = 0.01 if j < 4 else (0.0 if j < 8 else -0.01)
        px = 100.0
        rows = []
        for t in range(200):
            o = px
            px = px * (1 + drift)
            rows.append([day0 + t * 86_400_000, o, max(o, px), min(o, px), px, 1_000_000.0])
        candles[f"C{j}"] = rows
    w = World(candles, [f"C{j}" for j in range(12)])
    res = run_book(w, lambda i: [f"C{j}" for j in range(12)],
                   lambda c, i: trailing_ret(w, c, i, 7), 2, 5, start=66)
    recs = res["recs"]
    assert recs, "no rebalances"
    for r in recs:
        assert set(r["longs"]) <= {"C0", "C1", "C2", "C3"}, r["longs"]
        assert set(r["shorts"]) <= {"C8", "C9", "C10", "C11"}, r["shorts"]
        exp = 0.5 * (1.01 ** 5 - 1) + 0.5 * (1 - 0.99 ** 5)
        assert abs(r["ev"] - exp) < 1e-9, (r["ev"], exp)
        assert r["turnover"] in (0.0, 1.0)
    # after first rebalance the book is stable -> turnover 0
    assert all(r["turnover"] == 0.0 for r in recs[1:])
    s = summarize_book(res, 2, 5, "selftest", with_null=True)
    assert s["null_p_gross"] < 0.01, s   # random books can't beat perfect selection
    assert s["net25"] < s["gross_pct"] + 1e-9
    # pctk sanity: top-of-channel vs bottom
    assert pctk(w, "C0", 100, 14) > 0.45 and pctk(w, "C8", 100, 14) < -0.45
    # residual sanity: benchmark itself scores ~0
    assert abs(residual_ret(w, "C4", 100, 7, "C4")) < 1e-12
    print("selftest OK: selection exact, EV exact, turnover exact, null p<0.01, "
          "pctk/residual sane")


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default="all", choices=["A", "B", "C", "D", "all"])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    d = load_cache()
    import datetime as dt
    last = max(rows[-1][T] for rows in d["candles"].values() if rows)
    print(f"cache: {len(d['candles'])} coins, last bar "
          f"{dt.datetime.fromtimestamp(last/1000, dt.timezone.utc):%Y-%m-%d}")
    cells = {"A": cell_A, "B": cell_B, "C": cell_C, "D": cell_D}
    todo = ["A", "B", "C", "D"] if args.cell == "all" else [args.cell]
    for name in todo:
        print(f"\n===== CELL {name} =====")
        for row in cells[name](d):
            print(fmt(row))


if __name__ == "__main__":
    main()
