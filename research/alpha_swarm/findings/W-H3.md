# W-H3 — 1h cascade aftershocks: idio vs systemic — pre-registered fade REFUTED
# (and INVERTED: idio flushes CONTINUE — hourly confirmation of crash_continue)

## Hypothesis (pre-registered)
After a >= 6-8% 1h flush on a liquid coin, fading LONG works ONLY when the
cascade was IDIOSYNCRATIC (BTC did not flush simultaneously); systemic
cascades (BTC 2-sigma down in the same hour) were the control cell.
Extends C2 `liquidation_cascade_fade.md` (refuted at 5m on fees) with the one
conditional split C2 never tested. Spec in `hypotheses/W-H3.py` docstring.

## Rule
Event: coin 1h ret <= -THR, THR {6%, 8% primary}, dayNtlVlm >= $10M (15 coins),
per-coin 24h dedup, ~208d extended data (W-H0). IDIO = BTC same-bar ret >
-2 sigma_168 (strictly past). Rule: LONG open[i+1], 12h horizon, stop sweep
{8,15,20,25,40}%, costs 12/25bps + funding on the 12h hold where covered.
MC null = 6000 random-bar 12h longs, same universe.

## Events
THR 8%: 10 events total (8 idio / 2 systemic) — **NOT-RIPE even on 208d**.
THR 6%: 53 events (27 idio / 26 systemic).

## Aftershock map (THR 6%, cumulative from open[i+1])
| cell | n | +1h | +3h | +6h | +12h | +24h | retest-of-low 24h | post/prior vol |
|--|--|--|--|--|--|--|--|--|
| IDIO | 27 | -0.22% | -0.84% | -1.94% | **-2.35%** | -1.79% | **96%** | 1.47x |
| SYSTEMIC | 26 | +0.30% | +0.38% | +0.63% | +1.64% | +2.47% | 73% | 1.68x |

## Pre-registered rule (IDIO long, 12h)
| cell | n | gross(net-fund) | net12 | OOS12 h1/h2 | mc_p | stop sweep net12 (8/15/20/25/40%) |
|--|--|--|--|--|--|--|
| IDIO | 27 | -2.346% | **-2.466%** | -2.387/-2.550 | 0.999 | -1.72/-2.71/-2.86/-2.37/-2.93 |
| IDIO deduped | 23 | -1.377% | -1.497% | -2.354/-0.562 | 0.966 | -1.30/-2.33/-2.28/-1.50/-1.50 |
| SYSTEMIC (control) | 26 | +1.638% | +1.518% | +0.317/+2.919 | 0.018 | -0.49/+1.52/+1.52/+1.52/+1.52 |
| SYSTEMIC deduped | 12 | — | — | — | — | **NOT-RIPE** |

Integrity check (the btc_leadlag "7 macro candles" trap, W-H3.py:139): systemic
flushes cluster in the same BTC hours — deduping to the deepest flush per 12h
window collapses the systemic cell from n=26 to **n=12 independent episodes**.

## POST-HOC inverse (labeled, not pre-registered): SHORT the idio flusher
n=23 deduped episodes, 12h, funding credited to the short:
gross +1.377%, **net12 +1.257%, net25 +1.127%**, OOS12 **+2.114/+0.322** (both
halves +), mc_p **0.048** vs random same-universe shorts, stop sweep stable
{8:+1.59, 15/20/25/40: +1.26}% net12 (continuation shape — no squeeze to bank).

## VERDICT
- Pre-registered idio-fade-long: **REFUTED, sign-inverted.** Deciding number:
  IDIO long 12h = **-2.47% net12, mc_p 0.999, 96% retest of the flush low**, both
  OOS halves negative at every stop width. An idiosyncratic 1h cascade is the
  START of the move, not the overshoot.
- Systemic-bounce (the fade that "worked" in the control): **INCONCLUSIVE** —
  n=12 independent episodes after clustering dedup, and it was the control cell,
  not the registered rule. Do not trust the +1.52% net12 headline. (It rhymes
  with the known down-tape extreme-fade-long keeper; nothing new to wire.)
- The inversion **independently confirms the premise of the LIVE shadow book
  `crash_continue_div_short` at hourly frequency**: coin-specific weakness while
  BTC holds up CONTINUES (here -1.4 to -2.3% over 12h net). Post-hoc, thin
  (n=23), mc_p marginal (0.048) — shadow-grade material only; see the lane
  report for the single wire spec.
- Survivorship: 15-coin TODAY-liquid universe; all positives are upper bounds.
