# W-F2 funding_delta_term_structure

**Hypothesis.** (A) The D4 extreme-positive-funding SHORT survives two fixes D4 skipped:
funding actually collected in-trade, and independent-episode dedup (a coin at z≥2 for days
is ONE episode). (B) The 24h CHANGE in funding predicts forward returns beyond the LEVEL.
(C) How fast does extreme funding mean-revert (feeds live-book grading windows)?

**Rule** (`hypotheses/W-F2.py`). Level z = trailing-24h mean funding vs own trailing-30d
daily F24 distribution (settled rows only). Events short at day t+1 open, 5d hold, stops
{8,15,20,25,40}% swept, PnL = price + cum funding collected to actual exit. Null =
`mc_null.shuffle_label_p` (3000 iters) vs the pool of ALL valid same-side short trades
WITH funding term (2200 bars). Delta = F24(t)−F24(t−1d), z vs own 30d deltas.

## A) D4 re-check: HOLDS, and strengthens per-episode after dedup

| variant | n | net25 by stop (8/15/20/25/40%) | excess vs null | p | OOS25 h1/h2 |
|---|---|---|---|---|---|
| no dedup (D4-style) | 45 | 3.3/4.5/4.8/4.5/3.8% | +4.4% | 0.0010 | **−0.05**/+8.59 |
| **dedup (episodes)** | **25** | **5.0/6.0/5.7/5.3/4.7%** | **+5.6%** | **0.0027** | **+2.44/+8.98** ✅ |
| z=1.5 dedup | 48 | 3.1/3.6/3.0/2.8/3.1% | +3.4% | 0.0087 | +0.52/+6.12 ✅ |

- Funding collected while short adds **+0.19%/event** (shorts on extreme-positive funding
  get PAID — the funding term helps, unlike the refuted neg_funding_fade long-side mirror).
- Letting the stop trigger inside the entry day (D4 excluded it) changes nothing.
- D4's n=53 did contain clusters (25 independent episodes at my z), but per-episode EV
  goes UP after dedup — the cluster days were the weaker tail, not the source of the edge.
- Still regime-tilted (h2 = down tape carries +9.0% vs h1 +2.4%), same caveat as D4.

## B) Delta / acceleration beyond level: NOTHING there

Fama-MacBeth daily XS regression, fwd price ret ~ level_z + delta_z (both XS-standardized):
| h | n_days | level coef (t) | delta coef (t) |
|---|---|---|---|
| 1d | 70 | −0.9 bps (−0.07) | +8.5 bps (+0.57) |
| 3d | 23 | +8.6 bps (+0.30) | −20.5 bps (−0.54) |

No linear effect from either (the D4 edge is an extreme-TAIL effect, echoing D6's
"magnitude not sign"). Level-neutral delta-spike shorts (dz≥2, |lz|<1, 3d hold, n=56):
net25 +0.6–1.2%, p=0.08–0.22, OOS h2 flips negative at stops 20/25% → noise.

## C) Funding mean-reversion after a z≥2 positive spike

28/29 episodes see F24 halve within 15d; **median 3 days** (IQR 1–7). Funding extremes
are fast-decaying — consistent with the 5d price hold and with re-grading live funding
books on ≤1-week windows.

## VERDICT
- **A: VALIDATED (re-confirms D4, stronger form)** — SHORT z≥2 funding spike, 5d hold,
  15% stop, net25 +6.0%/episode, p=0.0027, both OOS halves +, funding term +0.19%.
- **B: REFUTED** — funding delta/acceleration adds nothing beyond the extreme level.
- **C: descriptive** — funding spike half-life ≈ 3 days.

Survivorship: short-side results are if anything conservative (dead coins = best shorts
absent). Sample = 90d, one regime cycle; the h2 tilt says expect the +2.4% end in flat tape.
