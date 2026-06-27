# C7 opening_range_breakout — REFUTED (no edge, fee-dominated + OOS sign-flip)

## Hypothesis
The break of the UTC opening-range (first K hours' hi/lo) runs to EOD; regime gate helps.

## Exact rule
1h candles grouped by UTC day. OR = first K hours' [hi,lo]. After hour K, the first hourly
CLOSE beyond OR fires (close>hi→long, close<lo→short), filled next-bar open, held to the
day's last bar with a protective stop. Gate: longs only BTC-up / shorts only BTC-down, vs
ungated. One trade/day/coin. Swept K{3,4,6} × gate{regime,none} × stop{5,10,20}%.

## Results (top by EV@25bps)
| K | gate | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|
| 3 | regime | .10 | 1785 | 0.005% | −0.125% | −0.375% | .475 | +0.25 | **−0.25** |
| 4 | regime | .20 | 1798 | −0.114% | −0.244% | −0.494% | .466 | +0.26 | −0.49 |
| 3 | none | .20 | 3252 | −0.207% | −0.337% | −0.587% | .444 | −0.22 | −0.19 |

- **No robust-both-halves cell.** Best (regime-gated) is EV ≈ 0 at 12bps and negative by
  25bps; OOS **sign-flips** (h1 +0.25 / h2 −0.25). Ungated is uniformly negative (the break
  is a coin-flip, win 44%).

## VERDICT
**REFUTED.** Deciding number: best EV@25bps = **−0.125%** with an OOS sign-flip, and the
ungated version is **−0.34%/trade** (win 44%). The regime gate only narrows the loss, it
doesn't create an edge. ORB is fee-dominated noise here — consistent with the project's
prior that intraday price-pattern entries have no edge. No MC null run (nothing passed the
slippage/OOS gate).
