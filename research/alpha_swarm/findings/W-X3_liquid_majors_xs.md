# W-X3 — liquid_majors_xs: the W-X2-B side-finding as its own pre-registered cell

## Hypothesis
W-X2 cell B's within-L1 book (+1.50% net25, p=0.000) was read as "momentum among liquid
majors is cleaner than among the thin tail". Test that read on its own terms: xs momentum
restricted to a MECHANICALLY declared liquid-majors universe, and answer the key question —
is such a book ADDITIVE to the live top-50 book, or the same trades?

## Exact rule (pre-registered in hypotheses/W-X3_liquid_majors_xs.py docstring before first run)
- Data: `W-X2_cache_daily.json` (2026-07-20, 401 daily bars, top-50 crypto by dayNtlVlm).
  Same shared engine as W-X2 (imported, not re-implemented): decide bar i, fill open[i+1],
  exit open[i+1+H], non-overlapping, equal weight, >=61-bar eligibility, start=66.
- UNIVERSE RULE (declared first, no hand-picking): at each rebalance, top-N of the 50-coin
  set by trailing **30d MEDIAN daily notional** (close x vol; >=15 obs; median so one wash
  day can't buy entry), N in {10,15,20}. Point-in-time, recomputed every rebalance.
- Score: raw 7d trailing return (the exact cell-B signal). Sweep k {2,3} x H {5,10}.
  **PRIMARY: N=15, k=3, H=10** (H10 = the live book's new hold); all else SENSITIVITY.
  Declared extras: pct_k(14) at primary geometry; CONTROL = top-50 raw7 k3 H10 (same-k
  concentration control).
- Costs 0/12/25/50 bps RT turnover-scaled; null = 2000 matched random books (gross), OOS
  halves; verdict gates identical to W-X2.
- ADDITIVITY (W-A1/W-C3, gate pre-registered): live comparator = pct_k(14) k4 H10 top-50
  (the recipe as configured 2026-07-20; residual flag ignored under pct_k; ungated = the
  stricter same-trades test). Reproduced W-X2-D to the digit (gross +3.46/net25 +3.25, n=33).
  ADDITIVE iff corr(gross EV) < 0.5 AND OLS residual alpha > 0 in BOTH halves.
- Selftests: engine exact-EV/turnover/null; median-vs-wash-spike; PIT universe regime change;
  deterministic tie-break; liquidity-restricted selection; OLS/overlap exact. All green.

## Results (per-rebalance per-leg signed EV; $/wk at $76.8/leg = 0.10-frac x 12x)
| cell | n | gross | net25 | net50 | OOS h1/h2 | Sharpe | null p | $/wk |
|---|---|---|---|---|---|---|---|---|
| **PRIMARY N15 raw7 k3 H10** | 33 | +0.21% | **+0.01%** | −0.20% | **−0.18 / +0.18** | +0.001 | **0.39** | +$0.02 |
| N10 raw7 k2 H5 | 66 | +1.25% | +1.09% | +0.92% | +2.01 / +0.17 | +0.218 | 0.008 | +$4.68 |
| N10 raw7 k2 H10 | 33 | +0.88% | +0.68% | +0.48% | +1.13 / +0.26 | +0.092 | 0.20 | +$1.46 |
| N10 raw7 k3 H5 | 66 | +0.86% | +0.72% | +0.57% | +1.83 / −0.40 | +0.172 | 0.023 | +$4.62 |
| N10 raw7 k3 H10 | 33 | +0.29% | +0.10% | −0.08% | +0.25 / −0.04 | +0.018 | 0.36 | +$0.33 |
| N15 raw7 k2 H5 | 66 | +1.04% | +0.86% | +0.69% | +1.60 / +0.13 | +0.174 | 0.0125 | +$3.72 |
| N15 raw7 k2 H10 | 33 | +1.53% | +1.32% | +1.11% | +1.58 / +1.07 | +0.206 | 0.0575 | +$2.84 |
| N15 raw7 k3 H5 | 66 | +0.81% | +0.65% | +0.48% | +1.18 / +0.12 | +0.168 | 0.022 | +$4.17 |
| N20 raw7 k2 H5 | 66 | +0.79% | +0.60% | +0.42% | +1.55 / −0.34 | +0.116 | 0.0545 | +$2.60 |
| N20 raw7 k2 H10 | 33 | +1.81% | +1.60% | +1.39% | +2.06 / +1.17 | +0.261 | 0.032 | +$3.44 |
| N20 raw7 k3 H5 | 66 | +0.81% | +0.63% | +0.46% | +1.08 / +0.19 | +0.177 | 0.026 | +$4.09 |
| N20 raw7 k3 H10 | 33 | +0.51% | +0.30% | +0.09% | +1.10 / −0.45 | +0.064 | 0.26 | +$0.97 |
| sens: N15 pct_k14 k3 H10 | 33 | +2.05% | +1.86% | +1.68% | +2.03 / +1.70 | +0.360 | 0.0065 | +$6.00 |
| CONTROL top-50 raw7 k3 H10 | 33 | +1.44% | +1.22% | +1.00% | +1.63 / +0.83 | +0.185 | 0.0555 | +$3.93 |
| LIVE sim pct_k14 k4 H10 top-50 | 33 | +3.46% | +3.25% | +3.05% | +3.64 / +2.89 | +0.589 | 0.000 | +$14.00 |

## Additivity vs the live book (overlap = share of X3 (coin,side) legs also in the live book)
| cell | overlap legs (L/S) | corr | resid alpha h1 | h2 | additive gate |
|---|---|---|---|---|---|
| **PRIMARY N15 raw7 k3 H10** | **33.8%** (34/33) | **+0.525** | −1.64% (t −1.01) | −1.67% (t −1.26) | **FAIL** |
| N10 raw7 k3 H10 | 25.3% | +0.465 | — | — | (primary-geometry context) |
| N20 raw7 k3 H10 | 39.9% | +0.541 | — | — | corr fails |
| N15 raw7 k2 H10 | 43.2% | +0.511 | +0.47% | −1.41% | FAIL (both legs) |
| N20 raw7 k2 H10 (best raw7 sens) | 50.8% | +0.465 | +0.45% (t 0.26) | −0.35% (t −0.21) | FAIL (h2<0) |
| N10 raw7 k2 H5 vs live-H5 twin | 30.7% | +0.334 | +1.94% (t 2.21) | −0.29% (t −0.35) | FAIL (h2<0, decays) |
| N15 raw7 k2 H5 vs live-H5 twin | 40.9% | +0.262 | +1.50% (t 1.67) | −0.11% | FAIL (h2<0) |
| sens: N15 pct_k14 k3 H10 | 49.0% (54/44) | +0.379 | +1.02% (t 0.62) | +0.63% (t 0.47) | passes numerically — see below |

Combined 50/50 (primary+live) gross Sharpe +0.377 vs live alone +0.627 — mixing in the
primary DAMAGES the live book (W-A1 diagnostic).

## Where the W-X2-B +1.50% went (the mechanism, traced)
- within-L1 raw7 k2 H5 replicates exactly: +1.50% net25, OOS +2.51/+0.49 (engine consistent).
- Composition bridge — mechanical top-18 by liquidity, same k2 H5: **+0.49%, OOS +1.56/−0.58.**
- The difference is WHO is in the book. Mid-sample, mechanical-top18 minus L1-sector =
  {FARTCOIN, PUMP, DOGE, ENA, PAXG, AAVE, LINK, UNI}; L1-sector minus mechanical =
  {ADA, ARB, DOT, LTC, NEAR, OP, XLM, XMR}. "Liquid majors" mechanically = memes + hot DeFi;
  the L1 filter was silently a MEME-EXCLUSION filter. That, not liquidity, carried the +1.50%.
- Failure mode visible in the primary book: long leg **−1.00%**/rebal, short leg +1.42%;
  FARTCOIN longed 9x AND shorted 11x, ZEC 11L/9S, PUMP 6L/9S — raw7 at 15-name depth churns
  the same high-vol names in both directions (chop-following). The live pct_k14 book has no
  such churn (long +3.85/short +3.08, PAXG(13)/ZEC(11) longs vs VVV/JTO/TRUMP shorts).

## VERDICT: **REFUTED — and NOT ADDITIVE.** The "liquid-majors cleanliness" read of W-X2-B is wrong.
Deciding numbers: PRIMARY net25 **+0.01%/rebal** (net50 −0.20%), OOS **−0.18/+0.18 sign-flip**
with **p=0.39** (the pre-registered REFUTED clause: sign-flip AND p>=0.15; the letter of the
MARGINAL clause also matches at +0.01>0 but the refutation disjunct governs). Additive gate:
corr **+0.525** (>=0.5) and residual alpha **negative in both halves** (−1.64/−1.67, full
t=−1.62) — after hedging out live-book exposure the majors raw7 book LOSES money. Every raw7
sensitivity cell also fails the gate (h2 residual alpha <= 0 across the board; the k2 H5
cells' h1 alpha decays to ~0 in h2). Scattered k2 positives (best: N20 k2 H10 +1.60%,
p=0.032) are 1-of-12 sweep survivors, all dominated by the live book (+3.25%, Sh +0.589)
and all gate-failers — nothing here to wire.

The one numeric gate-passer, pct_k14 N15 k3 H10 (+1.86%, p=0.0065, corr 0.379, resid alpha
+1.02/+0.63), is a SENSITIVITY cell, not the primary: it is the live ranker on a subset of
the live universe — a diluted live book (Sharpe +0.360 vs +0.589) that duplicates **49% of
its legs** with the live book under a binding notional cap (capital saturation is THE live
constraint), with residual-alpha t of only 0.6/0.5. Per A16/W-A6 (combo never beats best
single) and pre-registration discipline: NOT actionable. If capital ever stops being binding,
it would need its own primary cell first.

**Standing conclusion for the queue: do NOT rebuild "liquid-majors xs" from liquidity ranks.
The live top-50 pct_k14 book already owns these trades; the only real lead left from W-X2-B
is a MEME-EXCLUSION overlay on the live universe — a different hypothesis, its own cell.**

Caveats: survivor-biased top-50 cache (upper bounds); 13 months, n=33 at H10; funding not
modeled; live comparator ungated (declared: stricter for the same-trades question).

## Scoreboard line
W-X3 liquid_majors_xs: REFUTED + NOT ADDITIVE — primary (top-15 by 30d median notional,
raw7 k3 H10) net25 +0.01%, OOS −0.18/+0.18, p=0.39; vs live book corr +0.525, resid alpha
NEGATIVE both halves, 50/50 mix drops Sharpe 0.627→0.377. W-X2-B's +1.50% was MEME-EXCLUSION
(sector filter), not liquidity: mechanical top-18 swaps ADA/LTC/DOT-class for
FARTCOIN/PUMP/DOGE and collapses to +0.49% w/ OOS flip; primary long leg −1.00%/rebal,
FARTCOIN longed 9x + shorted 11x. NO-GO (+$0.02/wk at 0.10-frac). Lead worth its own cell:
meme-exclusion overlay on the live top-50 book.
