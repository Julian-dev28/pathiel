# B4 vol_targeting_overlay

## Hypothesis
Forecasting next-day book vol (EWMA/RiskMetrics) and scaling exposure to constant target risk
improves Sharpe and cuts drawdown vs flat sizing.

## Exact rule
- Book: daily market-neutral XS momentum (L=14, top/bottom-8), close-to-close. 285 days.
- Forecast: EWMA var, lambda=0.94, updated AFTER use (no lookahead). mult = min(target/forecast_vol, 3x),
  target = full-sample realized daily vol (apples-to-apples gross). 20-day warmup at mult=1.
- Compare flat vs vol-targeted: annualized Sharpe, maxDD, realized vol, both-halves Sharpe.

## Results
| variant | annSharpe | maxDD | dailyVol% | meanRet% | h1Sh | h2Sh |
|--|--|--|--|--|--|--|
| flat | 2.791 | -24.0% | 2.74 | 0.401 | 4.43 | 1.05 |
| voltgt | 2.703 | -22.3% | 2.86 | 0.404 | 4.26 | 1.27 |

Sharpe lift **-0.087**, maxDD change **+1.7pp** (improvement).

## VERDICT: MARGINAL
Deciding number: Sharpe lift **-0.087** (slightly worse) for a **+1.7pp** drawdown reduction. On an
already market-neutral, risk-balanced book the conditional vol is too stable for vol-targeting to add
edge: it improves the weaker second half (h2 Sharpe 1.05->1.27) but pays for it in the first half.
Net inert as a Sharpe lever; only mild drawdown-smoothing value. Survivor-biased / cost-free upper
bound. Not worth the turnover it adds. (Vol-targeting would matter more on a directional book than a
neutral one.)
