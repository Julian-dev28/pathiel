# C14 sector_rotation — REFUTED (sector structure adds nothing over all-universe momentum)

## Hypothesis
Hand-tag sectors (L1/meme/DeFi/AI/infra); trade sector momentum + intra-sector relative value.

## Exact rule
Daily, non-overlapping rebalances, lookahead-safe (score@i, enter i+1 open, exit i+1+H close).
Sectors: L1(16), MEME(7), DEFI(9), AI(4), INFRA(3). (1) SECTOR-momentum: long best / short worst
sector basket by trailing-L return. (2) INTRA-sector momentum: long top-half / short bottom-half
within each sector. (3) INTRA-sector RV: reverse. Reference: ALL-universe XS-momentum (top/bottom-8).

## Results (EV@12bps, both halves)
| L | H | SECTOR-mom | INTRA-mom | INTRA-RV | ALL-universe ref |
|--|--|--|--|--|--|
| 10 | 3 | −0.16 (flip h2−0.93) | +1.21 (+1.25/+1.16) | −1.45 | **+1.31** |
| 10 | 5 | +0.14 (flip h2−0.87) | +1.55 (+1.55/+1.56) | −1.79 | **+2.84** |
| 20 | 3 | +0.03 (flip h2−1.39) | +0.27 (flip h2−0.86) | −0.51 | **+1.30** |
| 20 | 5 | +0.51 (flip h2−0.18) | +0.46 (flip h2−0.98) | −0.70 | **+1.46** |

- **Sector-momentum rotation:** ~0 EV and sign-flips every cell (h1 positive, h2 negative).
  Rotating between sectors is not robust.
- **Intra-sector momentum:** +EV (it's momentum) but **weaker than the all-universe book in
  every single cell** — sector-neutralizing discards the cross-sector dispersion that is part
  of the momentum signal.
- **Intra-sector RV:** strongly negative (−1.45 to −1.79) — within-sector reversal loses,
  confirming momentum is the right direction.

## VERDICT
**REFUTED.** Deciding number: **intra-sector momentum EV (+1.2 to +1.6%) < all-universe
momentum (+1.3 to +2.8%) in 4/4 cells**, and sector-rotation EV ≈ 0 with OOS sign-flips. The
live all-universe XS-momentum book already dominates both sector variants — imposing sector
structure only throws away signal. No independent edge from sector tagging. (Sector sizes are
also tiny — AI=4, INFRA=3 — so intra-sector baskets are noisy by construction.)
