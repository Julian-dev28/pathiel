# W-A5 rank_weighting_schemes — which within-leg weighting maximizes the live book's net Sharpe?

**Hypothesis:** rank-weighting or inverse-vol-weighting the live pct_k book beats equal-weight
net of turnover/fees.

**Rule:** same live selections (pct_k 14d, k=8, market-neutral). Vary within-leg weights only:
EQUAL (1/8), RANK (linear rank weights, strongest signal heaviest), INVVOL (∝1/realized_vol_20d).
Book per-leg return = Σw_long·fwd − Σw_short·fwd. Net Sharpe applies 12bps·turnover per rebal.

## Results (per-leg EV; net12 = after 12bps·coin-turnover)
| hold/grid | scheme | n | EV | Sh full / h1 / h2 | net12 EV | net12 Sh | turnover |
|---|---|---|---|---|---|---|---|
| H10 non-ovlp | **equal** | 15 | +1.40 | **+0.399** / +0.67 / +0.25 | +1.23 | **+0.350** | 1.43 |
| | rank | 15 | +1.56 | +0.355 / +0.91 / +0.13 | +1.38 | +0.312 | 1.56 |
| | invvol | 15 | +0.75 | +0.197 / +0.65 / −0.05 | +0.57 | +0.149 | 1.50 |
| H7 non-ovlp | **equal** | 21 | +1.74 | +0.457 | +1.59 | **+0.417** | 1.25 |
| | rank | 21 | +1.79 | +0.453 | +1.62 | +0.410 | 1.38 |
| | invvol | 21 | +1.41 | +0.431 | +1.25 | +0.382 | 1.33 |
| H10 daily-ovlp | equal | 144 | +1.46 | +0.350 | +1.38 | +0.332 | 0.62 |
| | rank | 144 | +1.68 | +0.370 | +1.58 | +0.350 | 0.77 |
| | invvol | 144 | +1.26 | +0.311 | +1.17 | +0.289 | 0.71 |
| H7 daily-ovlp | equal | 147 | +1.30 | +0.349 | +1.22 | +0.329 | 0.63 |
| | rank | 147 | +1.42 | +0.338 | +1.33 | +0.316 | 0.72 |
| | invvol | 147 | +1.12 | +0.333 | +1.04 | +0.307 | 0.72 |

## VERDICT: **REFUTED (no implementation alpha) — equal-weight is already near-optimal; keep it.**
Deciding number: **net-of-fee Sharpe — equal 0.350 / 0.417 vs rank 0.312 / 0.410 vs invvol
0.149 / 0.382 (H10 / H7 non-overlap).** Equal-weight ties or beats rank on net Sharpe in 3 of 4
specs and beats inverse-vol everywhere.
- **RANK-weight** raises GROSS EV ~+0.15%/leg (concentrating on the strongest signals) but its
  ~10–25% higher turnover eats the gain → net Sharpe is a wash with equal (sometimes worse).
- **INVERSE-VOL** is strictly worst: it down-weights the high-vol names that carry the momentum
  payoff, cutting EV ~30% and Sharpe in every spec. Do NOT inverse-vol-weight this book.
Keep the live equal-weight. Don't add weighting complexity. (Thin n=15–21 non-overlapping;
the overlap rows agree, so the ranking is stable.)
