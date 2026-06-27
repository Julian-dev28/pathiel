# W-A6 factor_combo_v2 — diversification lift of a combined Lane-A book over the best single?

**Hypothesis:** combining the Lane-A survivors (momentum + A13-if-orthogonal) into one
vol-weighted market-neutral book beats the best single on OOS Sharpe.

**Streams:** M = momentum book, tested as **pct_k** (live ranker) AND **residual-momentum**
(W-A4, the better construction). A = A13 RSDD L/S (k6, N50). Combos: 50/50 and inverse-stream-vol
(rolling 6). B13 skew-arm is a Lane-B overlay (no return series here) → excluded. Lookahead-safe.

## Results (Sharpe; lift = combo − best single; beta tilt = net long−short beta)
| mom | H | n | M Sh | A Sh | combo50 Sh | combo invvol Sh | best single | lift 50/50 | lift invvol | combo β tilt |
|---|---|---|---|---|---|---|---|---|---|---|
| pctk (live) | 10 | 13 | +0.297 | +0.398 | +0.382 | +0.388 | +0.398 (A) | **−0.015** | −0.010 | −0.17 |
| pctk (live) | 7 | 19 | +0.174 | +0.137 | +0.171 | +0.184 | +0.174 (M) | −0.003 | +0.010 | −0.19 |
| **resid** (W-A4) | 10 | 13 | **+0.633** | +0.398 | +0.549 | +0.549 | +0.633 (M) | **−0.084** | −0.084 | −0.14 |
| **resid** (W-A4) | 7 | 19 | **+0.492** | +0.137 | +0.365 | +0.355 | +0.492 (M) | **−0.126** | −0.137 | −0.12 |

## VERDICT: **REFUTED (no diversification lift) — run the best single, do NOT combine.**
Deciding number: **against the live pct_k book the lift is ≈0 (−0.015 / −0.003); against the
BEST single (residual momentum, Sharpe +0.633) adding A13 HURTS (lift −0.084 to −0.126).**
- Residual (BTC-neutral) momentum ALONE is the best Lane-A book (Sh +0.633 H10 / +0.492 H7).
  Mixing in A13 dilutes it (A13 is weaker AND correlated) → negative lift.
- Adding A13 also **imports a down-beta tilt**: A13's own tilt is −0.25 (its short-deep leg is
  down-beta, W-A3), dragging every combo to −0.12…−0.19 net-short-beta = a hidden regime risk
  the residual book (tilt +0.02) doesn't have.
- This closes the W-A1 loop: A13 is not enough new capacity to lift a combined book, and where
  the base book is already beta-clean (residual momentum) A13 is strictly subtractive.

**Action:** the one true Lane-A edge is **residual momentum, equal-weighted, alone** (W-A4 + W-A5).
Do NOT ship a momentum+A13 combo. If A13 is wired at all it must be a tiny, beta-NEUTRALIZED,
separately-risk-budgeted satellite (W-A1/W-A3), never folded into the momentum book.
(Thin n=13–19 non-overlapping; mirrors the original A16 "composite never beats best single.")
