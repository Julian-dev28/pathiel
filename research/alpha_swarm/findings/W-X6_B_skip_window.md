# W-X6-B — skip-window echo momentum (Novy-Marx, crypto-scaled)

## Hypothesis
Ranking on the INTERMEDIATE window (skipping the most recent days) beats ranking on the
full recent window — the equity-literature "echo": {lookback 28 skip 7, lookback 14
skip 3} vs the live 14-skip-0.

## Cousin prior (cited before running)
B8 momentum_12_1_reversal: **REFUTED standalone** — raw-return skip ranks at L30/60/90 x
skip 0/7/14, k8 daily rebal, full universe, no meme exclusion. Mechanism finding: "the
recent 7d window CARRIES the signal, so removing it doesn't help." This cell differs:
crypto-scaled shorter windows (28/7, 14/3), BOTH the live pct_k ranker family and the
Novy-Marx return form, on the settled recipe (k4, H10, non-overlapping, meme-excluded).
Registered expectation from the cousin: refutation; run to close the cell ON the live
recipe rather than leave the near-replication implicit.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py before first run)
- BASELINE as in W-X6-A (reproduces W-X4 PRIMARY exactly; asserted).
- Four full-k4 layer books, same engine/grid, ranked on:
  - pks_28_7: pct_k(28) evaluated at bar i−7 (channel form, skip 7)
  - pks_14_3: pct_k(14) evaluated at bar i−3 (channel form, skip 3)
  - ri_28_7 : close[i−7]/close[i−28] − 1 (Novy-Marx return form, 21d window ending 7d ago)
  - ri_14_3 : close[i−3]/close[i−14] − 1
- GATE: strict dominance vs baseline (net25 EV AND Sharpe, both halves).

## Results (n=33; $/wk at $76.8/leg)
| book | gross | net25 | oos h1/h2 | Sharpe | null p | turn | delta net25 (t) | $delta/wk |
|---|---|---|---|---|---|---|---|---|
| BASELINE pct_k14 skip-0 | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | — | — |
| pct_k28 @ −7d | +1.91% | +1.75% | +1.51/+1.97 | +0.326 | 0.0085 | 0.66 | −1.94% (t=−1.49) | −$8.33 |
| pct_k14 @ −3d | +1.62% | +1.42% | +1.89/+0.98 | +0.236 | 0.022 | 0.81 | **−2.26% (t=−2.83)** | −$9.73 |
| ret[28→7] | +2.33% | +2.19% | +1.03/+3.27 | +0.385 | 0.001 | 0.59 | −1.50% (t=−1.09) | −$6.43 |
| ret[14→3] | +2.26% | +2.07% | +2.51/+1.65 | +0.298 | 0.002 | 0.79 | −1.62% (t=−1.55) | −$6.95 |

Dominance: pct_k28@−7d 0/4, pct_k14@−3d 0/4, ret[14→3] 0/4. ret[28→7] 2/4 (wins h2
narrowly, +3.27 vs +3.06) but h1 collapses (−3.31%/rebal delta) — fails the gate.
Marginal turnover: every skip book turns over the SAME or LESS than baseline (0.59-0.81
vs 0.81); the fee saving (up to +0.056%/rebal) never comes close to the gross give-up.

## Mechanism
The sharpest refutation is the minimal perturbation: lagging the live ranker by just
3 days (pct_k14@−3d) destroys −2.26%/rebal at t=−2.83 — the strongest-t result in the
whole W-X6 suite. The last few days of channel position ARE the signal, exactly B8's
mechanism, now confirmed at H10/k4 on the meme-excluded live recipe. There is no
crypto "echo": all four skip books remain +EV standalone (p <= 0.022) because momentum
is pervasive here, but every one is strictly dominated by not skipping.

## VERDICT: **REFUTED-AS-LAYER (all four variants)**
Deciding number: the best variant loses −1.50%/rebal net25 vs baseline; the closest
h2-winner (ret[28→7]) gives up −3.31%/rebal in h1. Confirms and extends B8 to the live
recipe. No spec / no revert: nothing to wire.

Caveats: survivor-biased cache (upper bounds); n=33; funding not modeled; ungated sim;
skip windows were pre-declared (28/7, 14/3) — no sweep beyond them, by design.

## Scoreboard line
W-X6-B skip_window_echo: REFUTED-AS-LAYER — all 4 pre-declared skip ranks (pct_k and
return form, 28/7 + 14/3) strictly dominated by live pct_k14 skip-0: best net25 +2.19%
vs +3.68%; lagging the live ranker 3 days alone costs −2.26%/rebal (t=−2.83, strongest
t in W-X6). Confirms B8's "recent window carries the signal" ON the live recipe; no
crypto echo. −$6.4 to −$9.7/wk.
