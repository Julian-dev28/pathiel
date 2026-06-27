# W-C1 engulf_spec — ROBUST, spec pinned (hz=1 is the stable hold)

## Hypothesis
There is a single best engulf definition + hold + (optional) volume filter that pins C9
into a forward shadow-wire spec.

## Rule searched
Daily cross-sectional signed engulf (bull→long / bear→short), fill i+1 open, per-coin
signed ret. Grid: body_ratio {1.0,1.25,1.5} × full-engulf{T,F} × gap{F,T} ×
vol-confirm{none, gt_prev, gt_ma5} × hold{1,2} × stop{0.08,0.40}. Robust = both OOS
halves >0 AND EV@25bps>0. MC null = strict BIGBAR (same body-ratio range-expansion bar,
direction only, WITHOUT the engulf-of-opposite-body condition).

## Key results
| spec | n | ev12 | ev25 | ev50 | win | h1 | h2 | MC excess (p) |
|--|--|--|--|--|--|--|--|--|
| **C9 baseline** hz1 br1.0 full gapF volNone stop.40 | 1476 | 0.68 | 0.55 | 0.30 | .52 | +0.57 | +0.78 | +0.72% (p=0.0009) |
| EV-max hz2 br1.0 full gapF **gt_prev** stop.40 | 786 | 1.08 | 0.95 | 0.70 | .51 | +1.07 | +1.10 | +1.18% (p=0.0009) |
| hz2 br1.0 full gapF **volNone** stop.40 | 2404 | 0.40 | 0.27 | 0.02 | .51 | +1.05 | **−0.25** | (sign-flip) |

- **hz=1 is robust across nearly every definition** (almost all hz=1 cells are both-halves+).
- **hz=2 sign-flips (h2 negative) WITHOUT a volume filter** → the high-EV "hz2+gt_prev"
  cell is hz=2 rescued by a free parameter. Chasing it = overfitting. Do NOT wire hz=2.
- Volume-confirm `gt_prev` (today's volume > prior bar) is a genuine but optional sharpener
  at hz=1; the base needs no filter to be robust.
- Stop width is insensitive (0.08 ↔ 0.40 both robust), consistent with C9.
- Full-engulf vs loose (close-beyond-prior-open) barely matters; body_ratio≥1.0 is enough.

## VERDICT
**ROBUST +EV — spec pinned.** Deciding number: the simplest spec (hz=1, classic full-body
engulf, body_ratio≥1.0, no gap, no vol filter, wide stop) clears the strict BIGBAR null by
**excess +0.72%/trade, z=3.42, p=0.00087**, both OOS halves + (+0.57/+0.78).

**Shadow-wire spec:** daily; bullish full-body engulf → long, bearish → short; fill next
open; **hold 1 day**; equal-weight cross-sectional (long bull set / short bear set);
stop wide (8–40%, insensitive). Optional `gt_prev` volume-confirm for a modest EV lift but
it is NOT required and hold=2 is REFUTED (sign-flips without it).
