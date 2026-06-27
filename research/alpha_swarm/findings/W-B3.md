# W-B3 semivol_risk_targeting

## Hypothesis
Risk-targeting the XS-momentum book on DOWNSIDE semideviation (penalize only downside vol) beats
symmetric total-vol scaling on Sharpe/drawdown.

## Rule
XS book k=14,H=7,m=6 (n=40 rebals). Scale each rebal w_t = min(target/risk_{t-1}, 3x), target =
full-sample book vol (leverage-neutral). risk = trailing-Lv total stdev (symmetric) vs downside
semideviation sqrt(mean(min(0,r)^2)). Lv{4,6,8}. Metric: annSharpe + maxDD vs raw (un-targeted) book,
OOS both halves.

## Results
| variant | annSh | maxDD | OOS h1/h2 sh | lift vs raw |
|--|--|--|--|--|
| raw (no targeting) | **+3.279** | **-9.9%** | 0.67/0.27 | — |
| total Lv=4 | +1.642 | -24.3% | 0.14/0.43 | -1.637 |
| semivol Lv=4 | +2.289 | -29.6% | 0.25/0.42 | -0.990 |
| total Lv=6 | +1.534 | -21.3% | 0.13/0.37 | -1.745 |
| semivol Lv=6 | +2.095 | -21.3% | 0.26/0.35 | -1.184 |
| total Lv=8 | +1.802 | -12.2% | 0.17/0.35 | -1.477 |
| semivol Lv=8 | +1.696 | -21.3% | 0.17/0.32 | -1.583 |

## VERDICT: REFUTED
Deciding number: best semivol variant annSharpe **+2.289 vs raw +3.279 (lift -0.990)** and its maxDD is
**-29.6% vs -9.9% (3x worse)**. The narrow sub-claim is TRUE — semivol beats symmetric total-vol
targeting at Lv=4/6 (+0.65 / +0.56 annSh) — but it is moot: BOTH targeting modes are strictly worse than
doing nothing. Targeting a market-neutral book whose vol is already near-stationary just times leverage
wrong (loads up into the high-realized-mean / high-future-vol window, hence the bigger return AND much
bigger drawdown). Confirms B4 (symmetric EWMA targeting inert/harmful) and extends it: semivol is the
better of two losing overlays. Do not risk-target this book. RISK: n=40 thin; a longer/EWMA half-life
might be gentler, but the direction (targeting hurts a neutral book) is consistent with B4.
