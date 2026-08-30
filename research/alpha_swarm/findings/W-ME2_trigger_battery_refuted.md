# W-ME2 — the main-engine trigger stack has no edge. Properly measured this time.

**Date:** 2026-08-30
**Supersedes:** W-ME1's evidence (not its conclusion — the conclusion holds)

## Why this re-test was owed

W-ME1 refuted main_engine on ~17 days of 5m bars, n=90, ONE composite
threshold, long only. That is thin enough to have missed a real edge, and it
tested the composite as a lump rather than asking which component carries
signal. Being right for a weak reason is still a weak reason.

## Pre-registered grid

Declared in the script header before running; every cell reported.

- 1h bars, **208 days** (12x W-ME1's window), BTC/ETH/SOL/BNB/XRP
- each of the 6 non-zero-weighted triggers, fired individually
- long and short = **12 cells**, Bonferroni threshold 0.05/12 = **0.00417**
- 24-bar hold, 6% stop, 25bps round trip
- matched random-time null on the same coins, same holding rule

## Result — zero survivors

| trigger | side | n | win | mean % | null % | excess | h1 | h2 | p |
|---|---|---|---|---|---|---|---|---|---|
| trendStrength | long | 1140 | 0.47 | -0.116 | -0.206 | +0.090 | -0.10 | -0.13 | 0.149 |
| trendStrength | short | 1140 | 0.44 | -0.411 | -0.319 | -0.092 | -0.40 | -0.43 | 0.892 |
| pctMoveSpike | long | 745 | 0.45 | -0.038 | -0.202 | +0.164 | -0.00 | -0.07 | 0.059 |
| pctMoveSpike | short | 745 | 0.46 | -0.484 | -0.318 | -0.166 | -0.50 | -0.47 | 0.956 |
| breakout | long | 621 | 0.45 | -0.043 | -0.207 | +0.164 | -0.12 | +0.03 | 0.075 |
| breakout | short | 621 | 0.46 | -0.440 | -0.316 | -0.124 | -0.35 | -0.53 | 0.881 |
| volumeSpike | long | 940 | 0.47 | -0.018 | -0.204 | +0.186 | +0.07 | -0.10 | 0.028 |
| volumeSpike | short | 940 | 0.44 | -0.471 | -0.317 | -0.154 | -0.53 | -0.41 | 0.968 |
| momentumBurst | long | 52 | 0.44 | +0.531 | -0.205 | +0.736 | +0.86 | +0.20 | 0.031 |
| momentumBurst | short | 52 | 0.50 | -0.823 | -0.312 | -0.510 | -1.01 | -0.64 | 0.924 |
| volumeBuildup1h | long | 519 | 0.45 | +0.060 | -0.211 | +0.271 | -0.03 | +0.15 | 0.015 |
| volumeBuildup1h | short | 519 | 0.42 | -0.505 | -0.319 | -0.186 | -0.38 | -0.63 | 0.950 |

**Nothing clears 0.00417.** The closest is volumeBuildup1h long at p=0.015 —
3.6x too large — on a mean of +0.060% before you notice its first half is
negative.

## Three things worth reading in that table

**Every short cell is strongly negative, p > 0.88.** These triggers are
long-structured, as the config itself says. Shorting them is not a missing
feature; it is a reliable way to lose.

**The longs lose money in absolute terms while beating the null.** Excess is
positive across the board (+0.09 to +0.27) because the null was also negative
(-0.20%): random entry into this 208-day window lost too. So the triggers
surface coins that fall slightly LESS than random. That is a weak filter, not
an edge, and it is not tradeable through a 25bps round trip.

**trendStrength does not survive contact with out-of-sample data.** The config
carries `"trendStrength": 0.55, # lift +2.08% (was 0.10) — strongest edge`,
measured on n=497 trades in June. On 1140 signals across 208 days it returns
-0.116% long at p=0.149. The weight that dominates the composite was fit, not
found. Those weights still drive which coins the scan surfaces.

## Verdict

main_engine cannot be made profitable from its own trigger stack. This is now
measured at n up to 1140 per cell over 208 days rather than n=90 over 17, on
every component individually rather than the composite as a lump, on both sides.

The remaining possibility is that the trigger is only a coarse filter and the
edge lived entirely in the AI verdict. That cannot be backtested — replaying an
LLM over historical bars is neither cheap nor deterministic — and under
`research/EVIDENCE_DOCTRINE.md` a signal that cannot be validated must not be
built. Its live record was -$172.33 over 157 trades.

Stays deleted.

Reproduce: `research/alpha_swarm/hypotheses/W-ME2_trigger_battery.py`.
