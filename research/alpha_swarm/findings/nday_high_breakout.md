# C5 nday_high_breakout — MARGINAL (+EV, shadow candidate; survivorship is the risk)

## Hypothesis
A slow positional long on a fresh N-day-high breakout, with a WIDE stop and a BTC-up gate,
captures trend continuation (distinct from the refuted intraday breakout).

## Exact rule
Daily. Entry: close_i > max(close[i−N..i−1]) (fresh N-day-high close) AND BTC up-regime
(BTC close > 20d SMA at bar i). Fill i+1 open. Horizon {5,10,20}d, stop {8,15,20,25,40}%.
De-clustered to ≥horizon spacing. Scored via `alpha_lib.summarize` then EXCESS over a
matched random-LONG baseline drawn ONLY from BTC-up-regime bars (so the tape is in both),
via `mc_null` shuffle-label AND block-bootstrap.

## Results
| N | hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|
| **50** | **20** | **0.25** | 68 | **+1.98%** | **+1.85%** | **+1.60%** | — | **+2.31** | **+1.54** |
| 50 | 5 | 0.15 | 106 | +2.12% | +1.99% | +1.74% | 0.46 | +0.43 | +3.87 |
| 100 | 5 | 0.08 | 35 | +2.22% | +2.09% | +1.84% | 0.43 | +1.28 | +3.21 |
| 50 | 20 | 0.20 | 68 | +1.68% | +1.55% | +1.30% | 0.37 | +2.20 | +0.97 |

- **Best robust cell (N=50, hz=20d, 25% stop):** both OOS halves positive (+2.31 / +1.54),
  survives all the way to **+1.60% at 50bps** (slow positional → fees negligible).
- **MC null vs matched random-long-in-up-regime** (which LOSES −3.74%/20d in this tape):
  excess **+5.84%**, shuffle-label **p=0.020**, block-bootstrap **p=0.025** — agree.
- **Parameter robustness:** ALL 15 cells of the N=50 family are EV@25bps>0. Not one lucky
  cell — every horizon×stop combo for 50-day-high breakouts in up-regime is +EV after costs.
- Signal concentrates in LONG lookbacks (N=50,100); N=20 never reached the top (too noisy),
  consistent with a genuine positional-trend read, not a fast flip.

## VERDICT
**MARGINAL (+EV shadow candidate).** Deciding number: **excess +5.84% over a matched
random-long-in-up-regime baseline, p≈0.02–0.025 on two independent nulls**, both OOS halves
positive, slippage-stable to 50bps, and 15/15 family cells positive. This is the cleanest
Lane-C survivor and matches the project's "edge is long/trend-aligned" profile.

**Why not ROBUST:** (1) **Survivorship is acute** — in a universe of today's liquid
survivors, "made a new 50-day high and kept running" is exactly the selection bias; the
+5.84% is an UPPER BOUND and the true number is lower. (2) n=68 on the best cell is moderate;
(3) 45 cells were swept, so single-cell p must be read with the family-consistency, not alone
(the 15/15 consistency is what rescues it from multiple-comparison concern).

**Shadow proposal:** log a 50-day-high-breakout long, BTC-up gate, 25% stop, ~20d hold,
small satellite size; forward-validate the bounce rate against the survivorship caveat before
any live flip. Params: N=50, BTC>20d-SMA, stop 25%, horizon ~20d.
