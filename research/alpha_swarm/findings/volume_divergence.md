# C3 volume_divergence — REFUTED (raw EV is the short-beta tape, not the signal)

## Hypothesis
Price up + volume fading = unsupported → fade short; price down + volume fading =
exhaustion → reversal long. Standalone entry (and a candidate dud-filter).

## Exact rule
Daily. Decide on bar i close, fill i+1 open. vol_trend = recent-half/prior-half avg vol −1
over window L. bear: pret_L > pthr AND vol_trend<0 → SHORT. bull: pret_L < −pthr AND
vol_trend<0 → LONG. Swept mode×L{10,20}×pthr{.05,.10,.15}×horizon{3,5,10}×stop{8..40}%.
Scored via `alpha_lib.summarize`, then the best robust cell vs a matched random-SHORT
baseline (`mc_null.shuffle_label_p`) to strip the −44%-tape short tailwind.

## Results
| mode | side | L | pthr | hz | stop | n | EV@12 | EV@25 | win | h1 | h2 |
|--|--|--|--|--|--|--|--|--|--|--|--|
| bear | short | 10 | .15 | 10 | .40 | 151 | 3.51% | 3.38% | .68 | **+6.61** | **+0.21** |
| bear | short | 10 | .10 | 10 | .20 | 254 | 3.17% | 3.04% | .62 | +6.52 | **−0.69** |
| bear | short | 10 | .15 | **3** | .08 | 222 | 1.81% | 1.68% | — | +2.03 | +1.58 |

- The high-EV horizon=10 cells **sign-flip** (h1 ≈ +6.6%, h2 ≈ 0 or negative) = noise.
- Best both-halves-positive cell (bear/short, hz3, stop8%): EV +1.81%@12bps, h1 +2.03 /
  h2 +1.58 — looks ok until you net the tape.
- **MC null (matched random short, same hz/stop):** obs_mean +1.93%/trade vs random-short
  null_mean **+1.27%**, **excess only +0.66%, z=1.16, p_one_sided = 0.12.**
- bull/long mode produced no robust cell (never reached top-8).

## VERDICT
**REFUTED.** Deciding number: after removing the short-side beta tailwind (a random short
earns +1.27%/3d in this −44% tape), the divergence signal adds **+0.66% excess, p=0.12** —
not significant. The headline "+1.8% EV" is ~70% tape, ~30% signal-shaped noise. No
independent edge as a standalone. As a dud-filter it has no validated standalone signal to
lean on, so I would not bolt it onto the live momentum/fade entries. Volume-trend
divergence is not a tradeable edge here.
