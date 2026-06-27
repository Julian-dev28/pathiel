# D3 carry_plus_trend

**Hypothesis.** Combining funding-carry/fade with price-momentum (the two strongest perp
factors) into one market-neutral book beats either alone via diversification / confirmation.

**Rule.** Three inv-vol, gross-1/net-0 books, daily rebal: MOM (long high trailing-price-return /
short low — the live XS factor), FUND (long low-funding / short high-funding — D1/D2 direction),
COMBO (score = z(mom) + z(−funding)). Return = price + carry. Lmom∈{7,14,30}d, Lfund=168h,
K=8. Headline at the NON-overlapping h=1d hold (h=3d inflates via overlapping windows).

## Results — h=1d, net of 12 bps (bps/d, Sharpe_ann), OOS both halves

| Lmom | book | net12 Sharpe | net25 Sharpe | OOS h1 / h2 |
|---|---|---|---|---|
| 14d | MOM | 29.1 (4.23) | 23.2 (3.36) | 9.5 / 49.6 |
| 14d | FUND | 10.2 (2.24) | 5.7 (1.25) | 3.2 / 17.5 |
| 14d | COMBO | 25.9 (4.44) | 20.3 (3.48) | 8.0 / 44.6 |
| 30d | **MOM** | 35.1 (**4.56**) | 30.8 (4.00) | 15.2 / 55.9 |
| 30d | FUND | 10.2 (2.24) | 5.7 (1.25) | 3.2 / 17.5 |
| 30d | COMBO | 27.4 (**4.47**) | 23.1 (3.75) | 13.3 / 42.2 |

corr(MOM, FUND) daily returns = **−0.34** (real diversification potential).

## VERDICT: REFUTED (no diversification lift over the dominant factor)
Deciding number: COMBO Sharpe_ann **4.47 ≤ MOM-alone 4.56** at the cleanest config (Lmom=30d,
h=1d). The combo cleanly beats FUND-alone (2.24) but NOT momentum-alone. Despite the favorable
−0.34 correlation, funding-fade's standalone Sharpe (~2.2) is too low relative to momentum's
(~4.5), so equal-z blending DILUTES the momentum book rather than improving it. Momentum is the
dominant price factor; funding-carry is a weaker, negatively-correlated satellite that adds no
net lift on top of it.

Caveat: the −0.34 correlation means an optimally-weighted (momentum-heavy) blend could shave
variance, but the gain is marginal and not worth the extra signal plumbing. The momentum leg here
IS essentially the live xs_momentum book — so the actionable takeaway is "funding does not improve
the existing momentum book." Survivorship upper bound, 90-day window.
