# B12 half_life_OU_sizing

## Hypothesis
Fit OU (AR1) to each coin's market-stripped cumulative residual, trade the reversion (long neg-z /
short pos-z), and size by 1/half-life (faster reversion = bigger) to beat equal sizing.

## Exact rule
- residual return = coin daily ret - equal-weight market mean (cross-section demeaned).
- Cumulative residual spread S. At decision t: AR1 fit on trailing W=30, z-score + half-life
  = -ln2/ln|b|. Enter when |z|>1, signal = -sign(z) (revert), realize next-day residual.
- Book = weighted avg of signal·residual; weighting equal vs 1/half-life. Daily 1d hold. annالسharpe + halves.

## Results
| weighting | n | annSharpe | meanRet% | h1Sh | h2Sh |
|--|--|--|--|--|--|
| equal | 269 | **-3.590** | -0.216 | -4.30 | -2.88 |
| halflife | 269 | -2.238 | -0.175 | -3.65 | -1.16 |

(a) base residual-reversion Sharpe **-3.59**, both halves negative. (b) half-life sizing lift +1.35 Sharpe.

## VERDICT: REFUTED
Deciding number: the base residual-reversion strategy is **-3.59 Sharpe, negative in BOTH halves**.
The market-demeaned cross-section CONTINUES (momentum), it does not revert at the daily horizon —
consistent with this project's XS-momentum edge and the earlier xs_reversal refutation. OU half-life
sizing has nothing valid to bolt onto: its "+1.35 lift" merely shrinks exposure to a losing strategy
(smaller loss is not edge). No tradeable OU reversion here. (A1 pca_residual_reversion using true PCs
may differ, but simple market-residual reversion is dead.) Survivorship can't rescue a -EV both-halves result.
