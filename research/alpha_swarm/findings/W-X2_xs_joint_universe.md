# W-X2 cell C — xs_joint_universe: crypto + xyz ranked together in one book

## Hypothesis
Ranking crypto top-50 and xyz equities TOGETHER on residual momentum (one market-neutral
book spanning both worlds) adds EV via cross-asset dispersion vs running the books separately.

## Exact rule (pre-registered)
- Universe: crypto top-50 (>=61 bars) + cell-A-eligible xyz equities (>=61 bars, $250k floor).
- Score: 7d residual momentum vs OWN-WORLD bench (crypto vs BTC, xyz vs xyz:XYZ100), ranked
  JOINTLY. k=8/leg, H=5, equal weight. Same costs/null/OOS engine as all W-X2 cells.
- Judged vs the separates on the same grid: crypto resid7 k8 H5, xyz resid7 k5 H5 (cell A).

## Results
| book | n | gross | net25 | OOS h1/h2 | Sharpe | null p | $/wk |
|---|---|---|---|---|---|---|---|
| JOINT crypto+xyz resid7 k8 | 66 | +0.63% | +0.46% | **+1.09 / −0.17** | +0.143 | 0.0075 | +$7.99 |
| separate crypto resid7 k8 | 66 | +0.99% | **+0.83%** | +1.14 / +0.53 | **+0.288** | 0.000 | +$14.32 |
| separate xyz resid7 k5 (cell A) | 34 | +0.81% | +0.65% | +0.18 / +1.12 | +0.217 | 0.0055 | +$6.95 |

- corr(separate crypto book, separate xyz book) on 34 common rebalances = **+0.28**.
- Joint book leg mix: xyz names take only 87/528 long slots and 103/528 short slots (~18%).

## VERDICT: **REFUTED (for the joint construction)** — run the two books SEPARATELY.
Deciding numbers: the joint book (net25 +0.46%, Sharpe +0.143, OOS **sign-flip** +1.09/−0.17)
is **worse than BOTH separates** (crypto +0.83%/Sh +0.288 both halves +; xyz +0.65%/Sh +0.217
both halves +). Cross-asset ranking mostly lets crypto's bigger 7d dispersion crowd xyz out of
the legs (~18% share), destroying xyz's clean book while diluting crypto's. Meanwhile the two
separate books correlate at only **+0.28** — the diversification is real, but you harvest it by
RUNNING BOTH AS SEPARATE BOOKS with separate risk budgets, not by joint ranking. Consistent
with the combo-never-beats-best-single pattern (A16, W-A6, D3).

Caveat: joint h2 weakness partly reflects the crypto book's h2 fade on this window; same
survivorship caveats as cells A/B/D.
