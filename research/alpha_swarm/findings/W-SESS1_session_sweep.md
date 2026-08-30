# W-SESS1 — Asia/London session-sweep reversal: REFUTED

**Date:** 2026-08-30
**Claim tested:** mark the Asia (00:00-08:00 UTC) and London (08:00-16:00 UTC)
high/low; when one is swept, enter a reversal toward the other side at 1-2 R.
Popularised as an ICT-style "liquidity sweep" setup.

## Method

BTC 15m, 5001 bars, 52 days. One signal per session per day: the first sweep
after the session window closes. Stop set as a fraction of the session range,
target at R multiples of the stop. A bar spanning both stop and target is scored
as a STOP (conservative).

Scored against a **matched random-time null** — same entry geometry, same
holding rule, same bar distribution, random entry times — because the question
is never "does this make money" (a drifting asset makes money) but "does it beat
entering at random".

Grid: 2 sessions x RR {1, 2} x stop {0.25, 0.5} of session range = 8 cells.

## Result

| session | RR | stop | n | win | mean R | null | excess | p |
|---|---|---|---|---|---|---|---|---|
| asia | 1.0 | 0.25 | 52 | 0.44 | -0.115 | -0.007 | -0.108 | 0.788 |
| asia | 1.0 | 0.50 | 52 | 0.44 | -0.077 | +0.002 | -0.079 | 0.729 |
| asia | 2.0 | 0.25 | 52 | 0.31 | -0.152 | +0.001 | -0.154 | 0.785 |
| asia | 2.0 | 0.50 | 52 | 0.38 | +0.005 | +0.034 | -0.029 | 0.568 |
| london | 1.0 | 0.25 | 38 | 0.63 | +0.282 | -0.006 | **+0.288** | **0.032** |
| london | 1.0 | 0.50 | 38 | 0.55 | +0.047 | +0.003 | +0.044 | 0.380 |
| london | 2.0 | 0.25 | 38 | 0.50 | +0.193 | +0.003 | +0.190 | 0.199 |
| london | 2.0 | 0.50 | 38 | 0.53 | +0.012 | +0.032 | -0.020 | 0.538 |

Seven of eight cells are indistinguishable from random. Every Asia cell is
NEGATIVE against its null.

## Why the one surviving cell is not an edge

Re-run at 10,000 null draws: p = 0.0360.

- **Fails Bonferroni.** Eight cells were tested, so the threshold is
  0.05/8 = 0.00625. p=0.036 is five times too large. This is the same
  best-of-N trap that produced the refuted `day_root_odd` result.
- **Decays across halves.** First half +0.504 R, second half +0.060 R on n=19
  each. Both are positive, so it clears that part of the bar, but an 8x decay
  across a 52-day sample is the signature of a fit rather than an edge.
- **Fees are 16.7% of 1R.** The typical stop is 0.366% of price against a
  measured 6.1bps round trip. Net of fees the mean is +0.115 R, and one
  additional bp of slippage removes a further 3% of it.
- **n=38, one asset, 52 days, one regime.** Below this repo's n>=8 floor only
  in the sense that it clears it; nowhere near enough to fund.

## Verdict

REFUTED. Do not build it. The specific failure is not "the pattern is fake" —
sweeps of session extremes obviously happen — it is that entering on them does
not beat entering at a random time with the same stop and target.

Consistent with the neighbouring results already in this repo:
`KillaXBT` (mechanical range/deviation) failed out of sample, `Williams
patterns` graded -EV, and `price entries no edge` covers the family. Session
levels are another coordinate on a chart, and chart coordinates are the
saturated part of this space.

Reproduce: `/tmp/hermes-sweep/session_sweep.py` (method), `verify_london.py`
(the correction and fee arithmetic).
