# C2 liquidation_cascade_fade — REFUTED (fee-dominated 5m mirage)

## Hypothesis
A violent 5m bar (range > N×ATR20% AND volume > M×avg_vol20) is a forced-liquidation
signature; fade the overshoot (red cascade → long, green → short).

## Exact rule
- 5m candles. Signal at bar i (lookahead-safe, decided on i close). Side = against the bar.
- Entry: open of bar i+1+delay (delay ∈ {0,1,2}). Exit: stop-width sweep {8,15,20,25,40}%
  + horizon ∈ {12,24,48} 5m bars (1h/2h/4h). De-clustered to ≥horizon spacing.
- Swept N∈{2.5,3.5}, M∈{3,5} → 180 cells. Scored via `alpha_lib.summarize` (OOS halves +
  slippage 0–50bps). Best cell carried to mc_null side-matched random-entry baseline.

## Results (top cells by EV@25bps; all 180 cells)
| N | M | delay | hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|--|
| 2.5 | 5 | 2 | 48 | 0.15 | 1450 | 0.135% | **0.005%** | −0.245% | 0.538 | −0.063 | +0.334 |
| 2.5 | 5 | 2 | 48 | 0.20 | 1450 | 0.135% | 0.005% | −0.245% | 0.538 | −0.069 | +0.340 |
| 2.5 | 3 | 2 | 48 | 0.20 | 1769 | 0.132% | 0.002% | −0.249% | 0.533 | −0.063 | +0.330 |

- **No robust-both-halves cell found** across all 180 combos.
- Stop width is nearly irrelevant (0.08 vs 0.40 move EV by <0.01%) → exits are
  horizon-driven, the cascade rarely retraces 8–40% in 4h. It's not a squeeze that a
  tight stop banks; it's drift.

## VERDICT
**REFUTED.** Deciding numbers: best EV is **~0.13%/trade at 0–12bps but collapses to
~0.005% by 25bps and goes negative (−0.25%) at 50bps** — a textbook fee-dominated 5m
signal that only "works" at zero cost. And OOS **sign-flips** (first half −0.06%, second
half +0.34%), so even the gross edge is unstable. n is huge (1450+) so it's not thin — it's
just not there after costs. Did not bother with the MC null; nothing survives the slippage
gate to test. The intrabar cousin of `extreme_fade` does NOT survive at 5m frequency;
the daily `extreme_fade` (−12% → long) remains the real version of this idea.
