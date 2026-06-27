# W-C3 engulf_orthogonality — ORTHOGONAL to the live book (not a momentum restatement)

## Hypothesis
C9 is just a fast 1-day restatement of the live XS-momentum (pct_k) book.

## Rule
Aligned DAILY always-on, market-neutral, 1-day-forward PnL series for the live pct_k k=8
book vs the engulf book (symmetric, and short-only per W-C2), flat-day=0. Return
correlation + OLS residual alpha (engulf = a + b·live), t-stat, FULL + both halves.
Additive only if low corr AND residual alpha>0 both halves. (Series truncated to the
common length N=188 bars so day indices align → ~166 usable days; underpowered.)

## Results
| book | corr(FULL) | beta(FULL) | alpha%/d FULL (t) | H1 alpha (t) | H2 alpha (t) |
|--|--|--|--|--|--|
| ENGULF symmetric | **−0.054** | −0.22 | **+0.585 (t=1.30)** | +0.392 (0.60) | +0.744 (1.18) |
| ENGULF short-only | +0.066 | +0.18 | −0.034 (−0.11) | +0.121 (0.26) | **−0.234 (−0.61)** |

## Read
- **Orthogonality is clean.** Correlation to the live book is ~0 (|corr|<0.07 full) and
  the regression beta is ~0 (−0.22 / +0.18) for both engulf forms. C9 is NOT a fast
  momentum restatement — it is a genuinely different factor. That is the W-C3 question and
  it PASSES.
- **Additive-alpha magnitude is shaky on this window.** The symmetric book shows positive
  residual alpha in BOTH halves (+0.39 / +0.74) but underpowered (t≈1.2–1.3, n=166). The
  short-only daily series is near-zero full and H2 NEGATIVE (−0.23) — a tension with
  W-C2's strong per-trade short (+1.47%/trade).
- Likely cause of the tension: the daily-always-on aligned window truncates to N=188 bars
  (recent ~6 mo) and weights every day equally with flat-day zeros, whereas W-C2's
  +1.47%/trade runs on each coin's full ~300-bar series over 808 sparse trade events. The
  strong short-leg edge appears concentrated outside the short aligned window.

## VERDICT
**ORTHOGONAL (additive, not momentum).** Deciding number: correlation **−0.05** and
regression **beta ≈ 0** to the live pct_k book → C9 is new capacity, not a re-expression
of XS-momentum. Caveat (yellow flag): the additive-alpha t-stat is underpowered (≈1.3) and
the short-only daily series goes negative in H2 on the truncated window, so the *size* of
the diversification benefit is unconfirmed even though the *independence* is clean. Forward
shadow needed to confirm magnitude.
