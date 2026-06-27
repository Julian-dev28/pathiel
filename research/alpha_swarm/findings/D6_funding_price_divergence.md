# D6 funding_price_divergence

**Hypothesis.** When price-trend and funding DISAGREE, the funding (positioning) side wins:
funding>0 + price falling = trapped longs → SHORT; funding<0 + price rising = squeezed shorts →
LONG. Does the divergence predict continuation in the funding-implied direction?

**Rule.** 2×2 event study (funding sign over trailing 72h × price sign over trailing 3-5d). Four
cells, each with an implied trade, next-day-open entry, 5-day hold, 20% stop. Each cell's trade
scored as EXCESS over a matched SAME-SIDE random-entry null. Divergence cells = PN (trapped-longs
short) and NP (squeezed-shorts long); aligned cells = PP, NN as controls.

## Results (Lfund=72h, Lprice=3d, h=5d, stop=20%, net of 25 bps)
| cell | trade | n | net25 | win | null excess | null p | OOS25 h1 / h2 |
|---|---|---|---|---|---|---|---|
| PN trapped-longs | short | 995 | **−0.37%** | .53 | +0.007 | 0.022 | −2.67 / +2.03 ❌ flip |
| NP squeezed-shorts | long | 318 | +1.21% | .56 | +0.006 | **0.20** | +0.54 / +1.90 |
| PP aligned-bull | short | 1070 | −0.96% | .49 | +0.001 | 0.37 | −3.70 / +1.78 |
| NN aligned-bear | long | 376 | +1.78% | .57 | +0.012 | 0.040 | +2.30 / +1.25 |

(Lprice=5d gives the same picture: PN net −0.61% p=0.092 OOS-flip; NP net +1.56% but p=0.107.)

## VERDICT: REFUTED — divergence is not tradeable alpha
Deciding numbers: the two DIVERGENCE cells both fail. PN (trapped-longs short) is net-NEGATIVE
(−0.4 to −0.6% @25bps) with an OOS SIGN-FLIP (h1 −2.7 / h2 +2.0) — it only "beats" its null
because the matched random-short pool is even more negative in the down tape, i.e. it's
less-bad-beta, not alpha. NP (squeezed-shorts long) is net-positive (+1.2-1.6%) but does NOT beat
a matched random-LONG pool (p=0.10-0.20) — that's just long-beta in up windows, not a funding edge.

The price-vs-funding SIGN disagreement adds nothing. Because most coins carry positive funding,
the sign-based cells are huge (n~1000) and the edge washes out — confirming that what carries the
real short alpha is the funding/premium EXTREME MAGNITUDE (D4/D5 z-spike), not the directional
divergence. Conditioning the funding-fade on price-divergence does not sharpen it.
