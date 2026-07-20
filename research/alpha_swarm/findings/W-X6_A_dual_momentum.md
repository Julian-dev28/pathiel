# W-X6-A — dual-momentum intersection (absolute + relative) as a leg filter

## Hypothesis
The classic dual-momentum intersection (Antonacci): a leg must pass BOTH the xs rank AND
its own-sign absolute trend — long only top-k names with trailing-14d > 0, short only
bottom-k names with trailing-14d < 0 — improves the live book by filtering "relative
winners in absolute downtrends" (and vice versa).

## Cousin prior (cited before running)
A2 tsmom: MARGINAL — absolute momentum standalone (L30/H14 keeper, down-tape loaded,
mostly "be short, smartly"). Never tested as an INTERSECTION FILTER on the xs legs; that
intersection is this cell's new content.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py docstring before first run)
- BASELINE = live recipe verbatim per 18622d3: pct_k(14) rank, k=4/leg, H=10, top-50
  minus the 20 declared SECTOR_MAP MEME names, equal weight, decide completed bar i,
  fill open[i+1], exit open[i+1+H], non-overlapping, start=66. Reproduces W-X4 PRIMARY
  to the digit (gross +3.88 / net25 +3.68 / n=33; asserted at runtime).
- LAYER: longs = baseline top-4 KEEPING only trailing-14d > 0; shorts = baseline
  bottom-4 KEEPING only trailing-14d < 0. Undersized book holds FEWER legs (empty
  slots = cash) — never reaches down-rank. Slot accounting: ev = (0.5/k)*sum(long fwd)
  − (0.5/k)*sum(short fwd); turnover = weight-increases/(2k) (reduces exactly to the
  W-X2 convention for full books; engine asserted equal to wx2.run_book).
- GATE (pre-registered, W-X4 convention): DOMINANT iff beats baseline on net25 EV AND
  net25 Sharpe in BOTH halves. Data: W-X2_cache_daily.json (401 bars to 2026-07-20).

## Results (n=33 rebalances; Sharpe = net25 mean/pstdev; $/wk at $76.8/leg)
| book | legs | gross | net25 | net50 | oos h1/h2 | Sharpe | null p | turn | $/wk |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE meme-excl pct_k14 k4 H10 | 8.00 | +3.88% | +3.68% | +3.48% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | +$15.83 |
| A dual-mom intersection | 7.27 | +3.14% | +2.96% | +2.77% | +3.72/+2.24 | +0.498 | 0.000 | 0.75 | +$12.71 |

Dominance: **0/4 — LOSES both EV and Sharpe in BOTH halves.** Paired delta net25
−0.726%/rebal (h1 −0.619 / h2 −0.827), t=−1.73. Delta split: long −0.186, short −0.554,
fee +0.014. Marginal turnover: 0.754 vs 0.811 (fee delta −0.014%/rebal, −$0.06/wk —
the filter is slightly cheaper, and still loses). $-delta at sizing: **−$3.12/wk**.

## Mechanism — the filter removes exactly the best legs
- 11 SHORT legs dropped (tr14 >= 0): their mean forward return was **−13.29%** — the
  "absolute-uptrend" bottom-rank names were the book's BEST shorts, not traps. A coin
  at the bottom of the 14d channel with a positive trailing return goes on to crash.
- 13 LONG legs dropped: mean forward +3.78% — also earning, also forfeited.
The absolute-sign condition is ANTI-signal at both extremes of the pct_k ranking in
this tape. Consistent with A2's caveat that tsmom's value was regime tilt, not
coin-level selection: as coin-level selection on top of xs rank it subtracts.

## VERDICT: **REFUTED-AS-LAYER (0/4)**
Deciding numbers: net25 +2.96% vs +3.68%, Sharpe +0.498 vs +0.636, worse in both
halves, −$3.12/wk. Dual-momentum intersection is a clean no-go on the live recipe.
No spec / no revert block: nothing to wire.

Caveats: survivor-biased top-50 cache (all numbers upper bounds); n=33; funding not
modeled; ungated sim; single tape (down-then-recover 2025-26) — but the layer loses in
BOTH halves, so no regime excuse.

## Scoreboard line
W-X6-A dual_momentum_intersection: REFUTED-AS-LAYER 0/4 — tr14 own-sign filter on the
live legs: net25 +2.96% vs +3.68%, Sh 0.498 vs 0.636, both halves lose, −$3.12/wk.
Dropped shorts had mean fwd −13.29% (best shorts), dropped longs +3.78% — the
absolute-sign condition removes exactly the extreme-continuation legs. tsmom (A2
MARGINAL) does not survive as an intersection layer.
