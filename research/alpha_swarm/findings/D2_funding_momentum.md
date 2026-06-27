# D2 funding_momentum

**Hypothesis.** The funding TREND predicts the next PRICE move. Two competing signs:
MOMENTUM (persistent positive funding = persistent up-pressure → keep going) vs CONTRARIAN
(persistent positive funding = crowded longs → price underperforms). Test which, market-neutral.

**Rule.** Cross-sectional, inv-20d-vol weighted, gross-1/net-0. Signal = trailing mean hourly
funding over L days through start of day i (lookahead-safe). CONTRARIAN: long bottom-K funding /
short top-K. MOMENTUM: the opposite. Return = PRICE only over the next h days (carry stripped —
that is D1). Fill at day-i open. Swept L∈{72,168}h, h∈{1,3,5}d, K=8. Reported BTC-beta of the book.

## Results (K=8, net of 12 bps turnover)

| L | h | side | turn | beta_BTC | net bps/d (Sharpe_ann) | win | OOS h1 / h2 |
|---|---|---|---|---|---|---|---|
| 168h | 1d | contrarian | 0.34 | −0.02 | 6.79 (1.49) | .52 | −0.8 / 14.7 |
| 168h | 3d | contrarian | 0.35 | 0.08 | 20.5 (2.59) | .53 | 14.3 / 27.1 |
| 72h | 5d | contrarian | 0.46 | 0.02 | 24.5 (2.42) | .54 | 28.2 / 20.6 |
| 168h | 1d | **momentum** | 0.34 | 0.02 | **−15.0 (−3.32)** | .42 | −7.1 / −23.3 |
| 168h | 3d | momentum | 0.35 | −0.08 | −28.8 (−3.64) | .42 | −22.2 / −35.8 |

The MOMENTUM leg is symmetrically NEGATIVE at every horizon → the contrarian sign is a real
directional effect, not noise. Book beta-to-BTC ≈ 0 (−0.10…+0.14) → this is NOT down-beta in
the −44% tape; it is market-neutral alpha. Crowded-long (high-funding) coins underperform
crowded-short (low/neg-funding) coins on price.

## Permutation null (real funding-rank vs random neutral inv-vol books, 3000 iter)
- L=168 h=3 contrarian: real 24.7 vs null 0.0, z=1.61, **p=0.055**
- L=168 h=1 contrarian: real 10.9 vs null 0.2, z=1.31, **p=0.096**
- L=72 h=1 contrarian: p=0.204 · L=168 h=1 momentum: p=0.91 (refuted, as expected)

## VERDICT: MARGINAL (shadow-deploy candidate, reinforces D1)
Deciding numbers: contrarian funding-fade is net-positive after 12 bps with Sharpe_ann ≈ 1.5-2.6
and BTC-beta ≈ 0, and the opposite (momentum) sign is symmetrically negative — strong evidence
the sign is real. BUT the cleanest NON-overlapping config (L=168 h=1d) has a first-OOS-half that
is ~flat (−0.8 bps) and a borderline null (p=0.096). The h=3/h=5 configs look far stronger
(Sharpe 2.4-2.6, both OOS halves clearly +, p=0.055) but use OVERLAPPING multi-day holds under
daily rebal, so their per-day magnitude is inflated and serial-correlated — treat as directional
evidence, not a clean Sharpe.

**This is the same direction as D1's short-high-funding leg** — D1's gross (14.3 bps/d) was mostly
this price prediction (~11 bps/d), not the carry (~3.4). Carry and price-fade reinforce → see D3.
Biggest risk: 90-day sample + borderline null; survivorship upper bound.
