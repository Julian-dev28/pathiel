# A13 relative_strength_drawdown

**Hypothesis:** Long survivors trading near their N-day high (high relative strength),
cross-sectional, captures a drawdown/proximity-to-high momentum effect (George-Hwang 52wk-high).

**Exact rule:** day i, dd_c = close[i]/max(high[i-N+1..i]) - 1 (proximity to N-day high, <=0).
NEAR = smallest |dd| (near high), DEEP = largest |dd| (most beaten). Fill open[i+1], hold H,
daily overlapping rebal, m=6. Tested long-only (BTC-up gated) AND market-neutral L/S
(long NEAR / short DEEP). vs matched random baselines. N{20,50} x H{5,7}.

## Results (per-leg signed gross %, 12bps)
### Long-only NEAR, BTC-up gated (baseline = up-regime random-long)
| N | H | EV0 | EV12 | EV25 | OOS h1 / h2 | excess vs base |
|---|---|---|---|---|---|---|
| 50 | 7 | +1.55 | +1.43 | +1.30 | +1.11 / +1.76 | **+3.30** |
| 50 | 5 | +1.41 | +1.29 | +1.16 | +0.78 / +1.82 | +2.71 |
| 20 | 5 | +0.89 | +0.77 | +0.64 | +0.64 / +0.91 | +2.20 |

DEEP (most-beaten) long-only is strongly negative (-1.9 to -3.7) — clean monotone signal.
Long-only NO-GATE collapses (h1 -1.2 / h2 +1.5 sign-flip) → the long-only book is directional.

### Market-neutral L/S (long NEAR / short DEEP) — the clean version
| gate | N | H | EV0 | EV12 | EV25 | EV50 | OOS h1 / h2 | n |
|---|---|---|---|---|---|---|---|---|
| **all** | 50 | 7 | +1.20 | **+1.08** | +0.95 | +0.73 | **+1.09 / +1.07** | 2904 |
| all | 50 | 5 | +0.83 | +0.71 | +0.58 | +0.36 | +0.73 / +0.69 | 2928 |
| up | 50 | 7 | +2.16 | +2.04 | +1.91 | +1.71 | +2.66 / +1.40 | 1104 |
| down | 50 | 7 | +0.61 | +0.49 | +0.36 | +0.16 | +0.18 / +0.81 | 1800 |

## Verdict: **ROBUST +EV** (market-neutral L/S, N=50)
Deciding number: market-neutral, **no regime gate**, N=50 H=7: EV12 **+1.08%/leg** with OOS
halves **+1.09 / +1.07** (symmetric, zero decay), monotone NEAR>>DEEP, survives to **50 bps**
(+0.73), n=2904. The down-regime leg is also positive (+0.49) so it isn't a pure bull bet.
Precise params: signal = close/max(50d high)-1; long top-6 nearest-high / short bottom-6
deepest-drawdown; fill open[i+1]; hold 5-7d; daily rebal; L/S equal-weight; no gate.

**Biggest risk #1 (must check before sizing):** overlap with the live XS-momentum book —
proximity-to-50d-high is highly correlated with 50d-return rank, so this may be a
re-expression of the existing momentum factor, not additive alpha. Run an orthogonality /
double-sort vs the live momentum signal before treating it as new capacity.
**Risk #2 (survivorship):** the long-near-high leg is the worst case for survivor inflation
(near-high survivors are selected); the L/S short-deep leg partially offsets it (real dead
coins would have paid the short *more*), so the L/S is the survivorship-safer construction and
is the one to trust — the long-only NEAR numbers are an upper bound.
