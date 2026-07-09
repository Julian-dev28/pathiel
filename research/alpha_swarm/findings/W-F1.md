# W-F1 xs_funding_carry_momentum (Lane F recheck of D1/D2)

**Hypothesis.** Cross-sectional funding fade — LONG bottom-K trailing-funding coins /
SHORT top-K, market-neutral inverse-vol weights — is +EV on TOTAL return (price move +
funding actually received/paid), with the two D1/D2 weaknesses fixed: non-overlapping
1-5d holds (D2's multi-day Sharpe was overlap-inflated) and funding legs inside the
same book as price (the trade you'd actually put on).

**Rule** (`hypotheses/W-F1.py`). Signal at rebal date t = mean hourly funding over
(t−L, t], settled rows only (the row stamped t+33ms is excluded → lookahead-safe).
K=8 (5/10 swept), inv-20d-vol weights, gross 1 / net 0, fill at day-t open, hold to
day-(t+h) open, PnL = Σ w·(price_ret − cum_funding), fees = bps × turnover Σ|Δw|.
Null = 2000 random-rank neutral books, same dates/weights/fee model. 34 coins, 68 days.

## Results (K=8, bps/day)

| L | h | n | net@0 | net@12 (Sharpe) | net@25 | carry part | OOS12 h1/h2 | null p |
|---|---|---|---|---|---|---|---|---|
| 24h | 1d | 67 | −1.4 | −10.6 (−1.8) | −20.5 | 3.4 | −15.8/−5.5 | 0.23 ❌ |
| 72h | 1d | 67 | 11.7 | 6.5 (1.1) | 0.8 | 3.3 | **−14.7**/27.1 | 0.006 |
| 168h | 1d | 67 | 15.1 | 11.0 (2.2) | 6.5 | 3.0 | **−5.3**/26.7 | 0.003 |
| 168h | 2d | 33 | 11.5 | 8.4 (1.7) | 5.0 | 3.0 | −0.3/16.5 | 0.048 |
| **168h** | **3d** | 22 | 20.2 | **17.6 (4.0)** | 14.8 | 2.9 | **+6.4/+28.9** ✅ | **0.017** |
| 168h | 5d | 13 | 6.1 | 4.1 (0.9) | 2.0 | 2.8 | −8.4/14.8 | 0.27 |

24h signal cleanly refuted (matches D1). Carry contributes only ~3 bps/d — the effect
is mostly the contrarian PRICE prediction (matches D2's decomposition).

## The honesty check that demotes it: h=3 phase-offset sweep (hidden dof)

Non-overlapping 3d periods have 3 possible grid phases. L=168 h=3, net@12 bps/day:

| phase | K=5 | K=8 | K=10 |
|---|---|---|---|
| off=0 | 17.9 (p=.054) | 17.6 (p=.018) | 9.0 (p=.069) |
| off=1 | 15.1 (p=.075) | **2.9 (p=.227)** | 1.4 (p=.229) |
| off=2 | 20.6 (p=.020) | 10.9 (p=.057) | 5.7 (p=.100) |

All 9 cells positive, but the headline p=0.017 exists at exactly one phase; off=1 K=8
collapses to +2.9 bps/d. h1 is negative in 3/9 phase cells. The second OOS half (the
down tape) carries most of the return everywhere.

## VERDICT: MARGINAL (independent confirm of D1/D2; do NOT wire live)

Deciding numbers: direction is robustly positive (every L≥72h cell across h/K/phase has
net@12 > 0; the opposite momentum sign was symmetric-negative in D2), but with
non-overlapping holds the significance D2 reported at h=3d (p=0.055) does not survive the
phase sweep (p 0.017→0.23), and 1d-hold first-half EV is negative (−5.3 bps/d). The edge
is real-looking, small, regime-tilted to the down tape, and 90d/34-survivor-coin bounded
(upper bound). Same fade direction as D1/D2 — this is ONE factor, counted once.
No shadow-wire beyond the existing D1 shadow spec (K=8, 7d rank, daily rebal); a live
flip would need forward confirmation in a non-crash regime first.
