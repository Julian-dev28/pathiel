# C4 wick_rejection — REFUTED (no edge on 1h or 4h)

## Hypothesis
A large lower-wick (rejection of lows) → long; large upper-wick → short, on 1h/4h.

## Exact rule
1h candles + aggregated 4h. wick: lower=min(o,c)−l, upper=h−max(o,c), body=|c−o|.
lower: lower>R·body AND lower>upper → LONG; upper symmetric → SHORT. Decide i close,
fill i+1 open. Swept R∈{1.5,2,3} × horizon × stop{8..40}%. `alpha_lib.summarize` (OOS +
slippage), mc_null on any robust cell.

## Results
**1h — top by EV@25bps:**
| mode | side | R | hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|--|
| lower | long | 3 | 24 | .15 | 2412 | 0.024% | **−0.106%** | −0.356% | 0.476 | +0.378 | **−0.333** |

**4h — top by EV@25bps:**
| mode | side | R | hz | stop | n | EV@12 | EV@25 | EV@50 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|--|
| lower | long | 2 | 6 | .40 | 1649 | 0.154% | 0.024% | −0.226% | 0.492 | +0.379 | **−0.077** |

- **No robust-both-halves cell on either timeframe.** Win rate <50% everywhere.
- Every cell: EV ≈ 0 at 12bps, negative by 25bps, ~−0.23% at 50bps. Classic fee-dominated.
- OOS sign-flips hard (h1 positive, h2 negative) — the apparent edge is first-half-only noise.
- upper/short mode never reached the top, so the short variant is worse.

## VERDICT
**REFUTED.** Deciding number: best EV@25bps ≤ **0.024%** (indistinguishable from zero),
turns **−0.23% at 50bps**, with OOS sign-flip (h1 +0.38 / h2 −0.33) and sub-50% win rate.
No robust cell across 90 combos per timeframe; n is large (1600–2700) so it's not thin —
wick rejection carries no tradeable edge here. Did not run the MC null (nothing survived the
slippage/OOS gate to test).
