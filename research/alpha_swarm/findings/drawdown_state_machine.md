# B11 drawdown_state_machine

## Hypothesis
BTC drawdown states (peak / correction / bear / recovery) gate which live edge pays, enabling a
state-conditional router/size-multiplier.

## Exact rule
- State from BTC equity curve at decision t: dd = price/running-peak - 1. peak: dd>-5%; bear: dd<-20%
  & not rising; correction: -5..-20% & not rising; recovery: in-DD & up>3% over 5d. (causal, trailing.)
- Edges measured by state: xs_book (market-neutral momentum), long_all (equal-weight long = beta),
  mom7 (TS 7d directional). Mean daily ret + both-halves within each state.

## Results (mean daily ret %, first/second half)
| edge | peak(22) | correction(34) | bear(181) | recovery(48) |
|--|--|--|--|--|
| xs_book | 1.21 (h1 -0.03/h2 2.69) | 0.75 (0.13/1.44) | **0.32 (0.32/0.32)** | 0.09 (0.23/-0.07) |
| long_all | -1.34 (-1.10/-1.63) | 0.17 (0.81/-0.55) | -0.02 (-0.16/0.13) | -0.59 (-1.33/0.22) |
| mom7 | 0.51 (1.02/-0.09) | -0.19 (-0.55/0.22) | 0.15 (0.29/0.02) | 0.00 |
state freq: bear 181, recovery 48, correction 34, peak 22.

## VERDICT: INCONCLUSIVE (router), with one clean sub-finding
Deciding observation: the **XS momentum book is +EV in every state** and is most reliably stable in
the dominant BEAR state (h1 0.318 / h2 0.323 — near-identical both halves, n=181). Its higher EV in
peak/correction (1.21% / 0.75%) is tempting for an up-size multiplier, but those states are rare
(22/34 days) and OOS-fragile (peak h1 -0.03, recovery h2 -0.07). So no robust state-conditional
router multiplier survives. long_all (beta) and mom7 are state-dependent and weak. Net: the actionable
result is that the XS book needs NO drawdown-state router — it pays across states; the state machine
adds no reliable sizing lift. Survivor-biased upper bound.
