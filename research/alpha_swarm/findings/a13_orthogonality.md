# W-A1 a13_orthogonality — THE decider

**Hypothesis:** A13 (relative_strength_drawdown: long nearest-50d-high / short
deepest-drawdown, market-neutral) is NEW capacity, not a re-expression of the live
xs-momentum book.

**Live book replicated from source** (config_store.py `xs_momentum` + xs_momentum_live.py):
`ranking="pct_k"` (percent-location in trailing **14d** high/low channel, centered 0),
k=8/leg, hold=10, vol_gate=True (flat in high-BTC-vol). NOT z_ext, NOT raw — the live
ranker is the 14-day stochastic-%K channel. (residual flag is ignored for pct_k.)
**A13 book:** signal = close/max(50d high)−1; long top-6 / short bottom-6; hold=7; no gate.
Both: decide on bars≤i, fill open[i+1]→open[i+1+H]. Per-leg signed return =
0.5·mean(long fwd) − 0.5·mean(short fwd). Dataset = 188 common daily bars × 40 coins.

## Results (live=pct_k k8 14d gated, a13=rsdd k6 50d)
| spec | n | corr | resid α %/per | t(α) FULL/H1/H2 | comb Sh | best-single Sh | comb>best both halves? |
|---|---|---|---|---|---|---|---|
| **non-ovlp H7 gated** | 18 | +0.18 | +1.37 | +2.15 / +0.76 / +2.50 | 0.688 | 0.659 (a13) | **YES** (H1 .465>.426, H2 1.035>.995) |
| non-ovlp H10 gated | 13 | +0.15 | −0.13 | −0.08 / +0.42 / −0.17 | 0.213 | 0.358 (live) | NO (n=13, too thin) |
| ovlp daily H10 gated | 122 | +0.37 | +1.10 | +2.85 / +2.64 / +1.63 | 0.564 | 0.487 (live) | NO (H1 .455<.466 a13) |
| ovlp daily H7 gated | 125 | +0.34 | +0.64 | +1.89 / +2.04 / +0.87 | 0.516 | 0.503 (live) | NO (H2 .567<.627 live) |
| ovlp daily H7 NOgate | 125 | +0.43 | +0.62 | +2.00 / +2.04 / +0.76 | 0.443 | 0.401 (live) | NO (H2 .429<.437 live) |

(Overlap t-stats are autocorr-inflated: H-day overlapping returns → effective n ≈ n/H ≈ 12,
so treat the non-overlap row as the honest significance.)

## The three deciding numbers
1. **Return correlation = +0.35** (overlap, gated; +0.18 non-overlap). This **REFUTES the
   "+0.7 / same factor wearing a different hat" fear.** The prior +0.7 was vs a raw/z_ext
   momentum proxy and almost certainly a SIGNAL-rank correlation; the REAL live ranker is a
   **14-day** channel while A13 is a **50-day** channel, and that horizon gap genuinely
   decorrelates the portfolio RETURNS. A13 is partially orthogonal.
2. **Residual-alpha t = +2.15** (non-overlap H7 full), both-half signs positive
   (+0.73 t0.76 / +2.16 t2.50). Positive in 4 of 5 multi-period configs; only the n=13
   non-overlap H10 flips. But the H1 t-stats are weak (<1) everywhere — alpha is real-ish
   but not strongly significant on the honest (non-overlap) sample.
3. **Combined OOS Sharpe beats best single only at H7** (0.688 vs 0.659 non-overlap; both
   halves there). At the live hold H10 and the overlap specs the lift is inconsistent —
   combined dips below the best single in one half.

## VERDICT: **MARGINAL** (qualified new capacity — NOT a duplicate, NOT a co-equal book)
Deciding number: **return corr +0.35** kills the duplication thesis (it is NOT 0.7, NOT the
same factor), and residual alpha is positive both halves (t up to +2.5) — so A13 carries
**incremental** information over the live book. BUT the strict gate (combined Sharpe > best
single AND residual α > 0 BOTH halves) is met cleanly at **only one** spec (non-overlap H7);
at the live hold (H10) it fails, H1 alpha t-stats are <1, and the sample is tiny (13–18
non-overlapping periods on 188 bars). The new capacity is **small and hold-fragile**.

**Recommendation:** A13 is additive but thin → a **SMALL shadow sleeve at H7**, sized as a
satellite, NOT a co-equal second book. Do NOT double-count it as a full independent factor.
For W-A6 factor_combo_v2: include A13 only as a down-weighted (e.g. 0.25–0.5×) sleeve at H7.

**Biggest risk:** 188-bar sample / ≤18 non-overlapping rebals — the residual alpha rests on a
handful of periods and the H1 split is insignificant. A larger cache could flip it either way.
Survivorship still inflates the long-near-high leg (L/S short-deep partially offsets).
