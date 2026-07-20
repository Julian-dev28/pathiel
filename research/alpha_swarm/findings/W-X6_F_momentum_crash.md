# W-X6-F — momentum-crash exposure (Daniel-Moskowitz) — diagnostic first

## Hypothesis
Daniel-Moskowitz momentum crashes: after a market drawdown, when the market rebounds,
the short leg (past losers, high down-beta) rallies violently and crushes the momentum
book. If our sample contains that signature, an asymmetric layer (halve/zero the SHORT
leg in post-drawdown-rebound regimes) should dominate.

## Cousin prior (cited before running)
W-A3 short_leg_is_beta: the short-side xs PnL in this tape is heavily down-beta (deep
basket beta +1.24 vs universe +1.15) — exactly the exposure DM crashes punish. No cell
has tested the crash REGIME on the live book before.

## Exact rule (pre-registered in hypotheses/W-X6_momentum_theory.py before first run)
- BASELINE as in W-X6-A (reproduces W-X4 PRIMARY exactly; asserted).
- Qualifying rebalance (ex-ante, decision bar i): BTC >= 15% below its trailing 90d
  close-high AND trailing-14d BTC return > 0 (crypto-scaled DM state; thresholds
  declared before first run: dd90 <= −0.15, r14 > 0).
- DIAGNOSTIC FIRST: report qualifying count, distinct episodes (maximal consecutive
  runs), and the BASELINE short-leg contribution inside qualifying windows vs outside.
- Pre-registered gate on scoring: if n_qualifying >= 5 rebalances, score SHORT-HALF
  (short slots at weight 0.5 in-regime) and SHORT-ZERO (short slots to cash in-regime)
  vs baseline under the strict-dominance gate; else BLOCKED-DATA. n_q = 7 → scored.

## Diagnostic (n=33 rebalances, 401 bars to 2026-07-20)
Qualifying: **7/33 rebalances, 5 distinct episodes** (2025-12-08; 2026-01-07/17;
2026-02-26; 2026-04-07/17; 2026-07-06; dd90 −17.0% to −30.4%, r14 +0.1% to +15.1%).

**The DM signature is INVERTED in this sample.** Baseline SHORT-leg contribution:
- inside qualifying windows: **+3.477%/rebal** (n=7)
- elsewhere: **+1.385%/rebal** (n=26)

Post-drawdown rebounds were the shorts' BEST regime, not their crash: 6 of 7 windows
had positive short contribution (+12.87% on 2025-12-08, +6.64% on 2026-04-17); the one
loss (2026-04-07, −10.17%) is a single junk-rally window. Crypto losers kept losing
through BTC rebounds — the equity-style loser-beta squeeze did not materialize (the
bounce concentrated in leaders while laggards like XPL/JTO/WLD/ACE kept bleeding).

## Layer scoring (forced by the diagnostic gate, n_q >= 5)
| book | gross | net25 | oos h1/h2 | Sharpe | turn | delta net25 (t) | $delta/wk |
|---|---|---|---|---|---|---|---|
| BASELINE | +3.88% | +3.68% | +4.34/+3.06 | +0.636 | 0.81 | — | — |
| SHORT-HALF in regime | +3.52% | +3.32% | +3.68/+2.98 | +0.613 | 0.78 | −0.36% (t=−1.25) | −$1.55 |
| SHORT-ZERO in regime | +3.15% | +2.96% | +3.03/+2.90 | +0.535 | 0.74 | −0.72% (t=−1.25) | −$3.09 |

Dominance: both 1/4 (a lone h2 Sharpe win from variance reduction; every EV check
loses). Delta split: long 0 by construction, short −0.369/−0.738, fee +0.009/+0.018.
Marginal turnover: LOWER (0.74-0.78 vs 0.81; regime resizing costs less than the
avoided short churn) — the fee saving is 40-80x smaller than the forfeited short EV.

## VERDICT: **REFUTED-AS-LAYER (crash-protection is anti-EV here); diagnostic answer: NO
## DM crash signature in this sample — the exposure is currently a tailwind.**
Deciding numbers: qualifying-window short contribution +3.48%/rebal vs +1.39% outside
(inverted sign vs the DM prediction); SHORT-HALF −0.36%/rebal, SHORT-ZERO −0.72%/rebal
vs baseline. Standing warning stays: W-A3 showed the short leg IS down-beta, so a
GENUINE loser-squeeze regime (alt-led V-recovery, not yet observed in these 401 bars)
would still hit the book — this cell shows 2025-26 rebounds were not that regime, not
that the exposure is gone. Forward condition to revisit: a rebound episode where the
BOTTOM-rank basket outperforms BTC over the window (loser-led bounce). No spec / no
revert: nothing to wire.

Caveats: survivor-biased cache; only 7 qualifying windows (scored because the
pre-registered n>=5 gate said so, but thin — the paired t is only −1.25); thresholds
crypto-scaled by declaration, not swept (by design); funding not modeled; ungated sim.

## Scoreboard line
W-X6-F momentum_crash_asymmetry: REFUTED-AS-LAYER + diagnostic INVERTED — 7 qualifying
post-drawdown-rebound rebalances (5 episodes): baseline short leg earned +3.48%/rebal
IN-regime vs +1.39% outside (6/7 windows positive) — crypto losers kept losing through
BTC rebounds; no DM crash signature in 401 bars. SHORT-HALF/-ZERO layers cost
−0.36/−0.72%/rebal (1/4 gate, only variance-reduction h2 Sharpe wins). W-A3's down-beta
warning stands for a genuinely loser-led bounce (not yet observed). −$1.6 to −$3.1/wk.
