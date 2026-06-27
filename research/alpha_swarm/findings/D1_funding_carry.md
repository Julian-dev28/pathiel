# D1 funding_carry

**Hypothesis.** A market-neutral book that SHORTS the highest-positive-funding coins
(collects carry) and LONGS the lowest-funding coins, inverse-vol weighted and rebalanced
daily, harvests the perpetual funding carry net of fees while staying delta-neutral.

**Rule.** Universe = 34 coins with both price+funding. Each UTC day i: signal =
trailing mean hourly funding through start of day i (lookahead-safe). Rank; LONG bottom-K,
SHORT top-K. Weights = inverse-20d-vol, scaled to gross 1.0, net 0.0 (neutral). Hold during
day i; realize price_ret=(C-O)/O and funding carry = −Σ wᵢ·fundᵢ (short high-funding collects).
Fill at day-i open after a close-of-day-(i−1) decision. Fees charged on turnover Σ|Δw| at each
slippage tier. Swept K∈{5,8,10}, signal horizon∈{24,72,168}h, inv-vol vs equal-weight.

**Key finding: the signal HORIZON is everything.** 24h funding is whipsaw noise (turnover
0.78-0.89, mean −12 bps/d @12bps, null p=0.64). The 7-day (168h) trailing-funding rank is
persistent (turnover 0.27-0.34) and survives fees.

## Results (inverse-vol, net of turnover fees)

| Config | carry bps/d | turn | net@0 | net@12 (Sharpe_ann) | net@25 | OOS@12 h1 / h2 |
|---|---|---|---|---|---|---|
| K=8 sig=24h | 3.65 | 0.78 | −2.7 | −12.1 (−2.3) | −22.2 | −15.0 / −9.0 ❌ |
| K=8 sig=72h | 3.66 | 0.45 | 10.4 | 4.9 (0.94) | −1.0 | −2.8 / 13.0 |
| **K=8 sig=168h** | **3.39** | **0.34** | **14.3** | **10.2 (2.24)** | **5.7** | **3.2 / 17.5** ✅ |
| K=5 sig=168h | 4.90 | 0.34 | 16.5 | 12.4 (1.66) | 8.0 | 8.1 / 16.9 ✅ |
| K=10 sig=168h | 2.85 | 0.27 | 10.3 | 7.0 (1.95) | 3.5 | 7.9 / 6.1 ✅ |

(bps/d = basis points per day on gross-1 book. net@X = mean daily return at X bps slippage on turnover.)

## Permutation null (real funding-rank vs random neutral inv-vol books, 3000 iter)
- K=8 sig=168h: real 14.3 bps vs null 0.17, z=1.73, **p=0.045**
- K=5 sig=168h: p=0.065 · K=10 sig=168h: p=0.083
- K=8 sig=24h: p=0.64 (refuted) · K=8 sig=72h: p=0.097

## VERDICT: MARGINAL (shadow-deploy candidate)
Deciding numbers: with the 7-day funding rank the book is net-positive both OOS halves at
12 AND 25 bps (K=8: +3.2/+17.5 bps/d @12bps; K=5: +8.1/+16.9), Sharpe_ann ≈ 2.0-2.2 @12bps.
That clears the carry headline gate. BUT the permutation null is only borderline (p=0.045 at the
single best config, drifting to 0.065-0.083 at neighboring K) — over 90 days the total return is
barely distinguishable from a lucky random neutral book, because the deterministic carry (~3.4
bps/d) is small relative to daily price-PnL noise. The 24h horizon is cleanly REFUTED.

**If shadowed:** K=8, 7-day trailing-funding rank, inverse-20d-vol weights, daily rebal,
gross-1 / net-0. Biggest risks: (1) borderline null on a 90-day sample — needs forward
confirmation; (2) survivorship — 34 PIT-liquid coins, so +EV is an UPPER BOUND; (3) edge halves
from 12→25 bps, so execution slippage above ~25 bps on turnover kills it.
