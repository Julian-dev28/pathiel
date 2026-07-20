# W-X6-E — two-factor (BTC+ETH) residual momentum

## Hypothesis
Residualizing returns against BTC AND ETH (2-factor) before ranking produces cleaner
idiosyncratic momentum than the single-BTC residual — or amplifies noise, given how
collinear the two factors are.

## Cousin prior (cited before running)
W-A4 idio_momentum_residual: **MARGINAL** — 1F BTC-residual at lb14 beat the RAW-RETURN
rank (Sh 0.56 vs 0.23 at H10) and de-confounded the beta tilt; the recommendation was a
shadow A/B vs pct_k, never a flip. The live ranker is pct_k14, which W-A4 never
compared against. This cell's new content = the SECOND factor (ETH); the 1F book is
re-run as the locating rung so the comparison ladder is complete on ONE baseline:
pct_k14 (live) vs r1F vs r2F, all meme-excluded k4 H10.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py before first run)
- BASELINE as in W-X6-A (reproduces W-X4 PRIMARY exactly; asserted).
- r1f = rc14 − beta*rBTC14, beta = OLS on trailing 30 daily rets (W-A4 construction,
  wx2.residual_ret verbatim).
- r2f = rc14 − b1*rBTC14 − b2*rETH14, (b1,b2) = joint 2-var OLS (with intercept) on
  trailing 30 daily rets; fallback to (1F beta, 0) ONLY on numerical singularity
  (det <= 1e-12*Sbb*See). Selftest: exact beta recovery on constructed 2-factor data,
  singularity fallback on an ETH==BTC clone.
- Full k4 books on each score; GATE: strict dominance vs baseline, judged layer = r2f.

## Results (n=33; $/wk at $76.8/leg)
| book | gross | net25 | oos h1/h2 | Sharpe | null p | turn | delta net25 (t) | $delta/wk |
|---|---|---|---|---|---|---|---|---|
| BASELINE pct_k14 | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | — | — |
| r1F BTC lb14 (rung) | +2.90% | +2.72% | +2.96/+2.50 | +0.461 | 0.000 | 0.71 | −0.96% (t=−1.15) | −$4.13 |
| r2F BTC+ETH lb14 (JUDGED) | +2.66% | +2.48% | +3.04/+1.95 | +0.408 | 0.000 | 0.73 | −1.21% (t=−1.33) | −$5.19 |

Dominance: r2F 0/4 vs baseline; r1F 0/4 vs baseline. r2F vs r1F (the marginal factor):
h1 narrowly better (+3.04/+0.439 vs +2.96/+0.415) but h2 clearly worse (+1.95/+0.384 vs
+2.50/+0.564) and worse full-sample — the ETH increment is not even an improvement on
its own rung. Marginal turnover: 0.71-0.73 vs 0.81 (fee saving +0.02%/rebal, immaterial).

## Noise diagnostics (pre-registered)
- Mean 30d BTC-ETH daily correlation across rebalances: **+0.882**.
- 2F betas: b_btc mean +0.52 sd 0.97 range [−4.4, +9.3]; b_eth mean +0.66 sd 0.68
  range [−4.0, +4.1]. vs 1F beta: mean +1.44 sd 0.63 range [−0.5, +5.5].
- 0/1303 numerical-singularity fallbacks — the OLS never degenerates outright; it just
  splits one stable loading into two wild ones (collinearity variance inflation ~4-5x
  at corr 0.88 on 30 points).

## VERDICT: **REFUTED-AS-LAYER (0/4) — noise amplification, answered directly.**
Deciding numbers: r2F net25 +2.48% < r1F +2.72% < baseline +3.68%; ETH-factor beta
range [−4.4, +9.3] on the BTC loading vs [−0.5, +5.5] for 1F. The second factor makes
the residual WORSE, and even the clean 1F residual — W-A4's marginal winner over raw
returns — loses to the live pct_k14 ranker on this baseline (0.461 vs 0.636 Sharpe).
This closes W-A4's open A/B question: KEEP-PCTK stands; do not shadow the resid ranker.
No spec / no revert: nothing to wire.

Caveats: survivor-biased cache; n=33; 30-point beta window is the live/W-A4 convention
(a longer window would shrink beta noise but is a different, unregistered cell);
funding not modeled; ungated sim.

## Scoreboard line
W-X6-E two_factor_residual: REFUTED-AS-LAYER 0/4 — 2F (BTC+ETH) residual rank net25
+2.48% < 1F +2.72% < live pct_k14 +3.68% (Sh 0.408/0.461/0.636); BTC-ETH 30d corr
+0.882 splits one stable loading into two wild ones (b_btc range −4.4..+9.3 vs 1F
−0.5..+5.5, 0 singular fallbacks) = pure noise amplification. Also CLOSES W-A4's open
A/B: even 1F residual loses to pct_k14 on the meme-excluded baseline — KEEP-PCTK
settled again. −$5.2/wk.
