# B2 correlation_regime_gate

## Hypothesis
Cross-sectional dispersion (XS-momentum) dies when everything moves together, so sizing the
live XS book by inverse rolling avg-pairwise-correlation lifts Sharpe / cuts drawdown.

## Exact rule
- Book: daily market-neutral XS momentum, rank by trailing L=14d return, long top-8 / short
  bottom-8 equal-weight, close-to-close (slow daily signal, documented approximation, no turnover cost).
- Regime: avg pairwise correlation of 38 aligned coins over trailing CORRW=20d (known at decision).
- Overlays tested: inv_corr (mult ∝ 1/corr, mean-1), gate_off_q75 (flat on top-quartile-corr days),
  half_above_med. 280 book-days. Metric = annualized Sharpe + maxDD vs un-gated.

## Results
| variant | annSharpe | maxDD | meanRet% | Sharpe-lift | h1Sh | h2Sh |
|--|--|--|--|--|--|--|
| ungated | 2.884 | -24.0% | 0.416 | +0.000 | 4.47 | 1.16 |
| inv_corr | 2.894 | -26.2% | 0.421 | +0.010 | 4.45 | 1.28 |
| **gate_off_q75** | **3.044** | -24.0% | 0.379 | **+0.160** | 3.78 | 2.26 |
| half_above_med | 2.882 | -24.0% | 0.338 | -0.002 | 4.09 | 1.69 |

## VERDICT: MARGINAL
Deciding number: best overlay (sit flat on top-quartile-correlation days) = **+0.16 annualized
Sharpe**, and it helps the weaker second half (h2 1.16 -> 2.26, both halves stay +). Inverse-corr
*sizing* is inert (+0.01). The gate cuts mean return (sits out ~25% of days) and does NOT reduce
drawdown (maxDD flat at -24%). Note the base book Sharpe 2.88 is an upper bound: survivor-biased
38-coin universe + zero turnover cost. Net: a mild, OOS-consistent risk-off gate, not a profit edge.
Shadow-worthy as a de-risk switch; not a standalone alpha.
