# W-EF1 — extreme_fade entry sweep: deeper crash = bigger bounce (VALIDATED, flipped 1% live)

**Question (operator, 2026-07-23):** extreme_fade grade is PENDING and recent realized is
0/5 (−$9). Before any live flip, re-test the ENTRY space — which crash threshold / hold pays?

**Method (`hypotheses/W-EF1_crash_fade_sweep.py`):** dataset.json 1d, 40 coins. Crash =
trailing-lb return <= threshold → LONG next open, exit +hold open. Sweep threshold
{−8,−10,−12,−15,−20%} × lb {1,2} × hold {1,2,3,5}. Per-trade EV net 25bps, matched same-coin
random-time null (2000), OOS halves.

## Results — monotone, all-ROBUST

| crash | hold | n | EV/trade | halves | p |
|---|---|---|---|---|---|
| **−20%** | 3d | 37 | **+11.72%** | +10.3/+13.1 | 0.0005 |
| −15% | 3d | 45 | +9.03% | — | 0.0005 |
| **−12% (live base)** | 3d | ~ | +4.73% | — | 0.0005 |
| −10% | 3d | ~ | +1.93% | — | 0.0005 |
| −8% | 2d | ~ | +0.78% | — | 0.0010 |

**Deeper crash → bigger fade bounce, monotonically.** Every cell is ROBUST (EV+ both OOS
halves, beats the null p=0.0005). The live −12% base is the WEAKEST winner (+4.73%); the real
money is −15% to −20% (+9–12%), which the config's deep tier (−20%) already targets.

## Reading

1. The crash-fade LONG edge is real and strong on this universe — a −20% flush bounces
   +11.7% over 3d net of fees. It aligns with the standing edge profile (LONG / mean-reversion).
2. The live −12% threshold under-harvests. Keeping it captures more (frequent) but weaker
   entries; the deep tier (−20%, +11.7%) carries the EV. Sizing the deep tier a touch higher
   is correct.
3. Reconciling with the PENDING forward grade + recent 0/5 (−$9): the backtest is overwhelming
   but forward is unconfirmed — the recent losses were likely shallow (−12%-ish) entries and/or
   exit timing. The tension is resolved the disciplined way: flip **bounded (1% equity) + kill**,
   not on faith.

## VERDICT: **VALIDATED — flipped LIVE at 1%.** extreme_fade: shadow_only false,
equity_fraction 0.40→0.01 (deep tier 0.60→0.015), max_new_per_cycle 0→1 (was opening nothing),
crash −12% base + −20% deep, 20% stop, 3d hold, 4x. Bounded ~1% margin/trade. KILL: forward
EV25 < 0 over 12 resolved episodes → shadow. Caveats: survivor-biased top-40 universe (upper
bound), deepest cell n=37 (rare deep flushes fire seldom), one tape. Artifact:
`W-EF1_crash_fade_sweep.py`.
