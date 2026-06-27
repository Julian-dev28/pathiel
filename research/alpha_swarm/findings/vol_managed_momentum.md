# A6 vol_managed_momentum (Barroso)

**Hypothesis:** Scaling the XS-momentum book by inverse realized strategy vol
(Barroso momentum-crash protection) lifts Sharpe over the un-scaled book.

**Exact rule:** XS-momentum portfolio (rank trailing-k, long top-6/short bottom-6,
equal-weight, fill open[i+1], hold 7, non-overlapping) -> raw return series. Vol-manage:
w_t = target_vol / realized_vol(last Lv portfolio returns, known before t), cap 3x.
Metric = Sharpe(w*raw) - Sharpe(raw). Both OOS halves. k{14,30}, Lv{4,8}.

## Results (portfolio per-rebalance, Sharpe-like = mean/std)
| k | book | n | mean% | Sharpe | OOS sh h1/h2 | **LIFT** |
|---|---|---|---|---|---|---|
| 14 | RAW | 40 | +1.87 | +0.454 | +0.67/+0.27 | — |
| 14 | VM Lv4 | 36 | +1.71 | +0.227 | +0.14/+0.43 | **-0.227** |
| 14 | VM Lv8 | 32 | +1.23 | +0.249 | +0.17/+0.35 | -0.205 |
| 30 | RAW | 38 | +1.76 | +0.348 | +0.19/+0.57 | — |
| 30 | VM Lv4 | 34 | +2.12 | +0.319 | +0.23/+0.44 | -0.029 |
| 30 | VM Lv8 | 30 | +1.75 | +0.326 | +0.14/+0.52 | -0.023 |

## Verdict: **REFUTED** (no Sharpe lift)
Deciding number: LIFT is **negative in all 4 configs** (-0.23 to -0.02). Vol-managing the
momentum book does not help on this tape — the strategy's high-vol windows weren't where
its losses concentrated, so cutting exposure there just clipped good returns. Side note: the
RAW XS-momentum book itself prints a healthy Sharpe (+0.35 to +0.45, both halves positive),
re-confirming the live momentum factor — but the Barroso overlay adds nothing. Sample is
thin (30-40 non-overlapping rebalances) so this is "no evidence of lift," not a hard kill of
the academic effect; on this dataset, do not add the overlay.
