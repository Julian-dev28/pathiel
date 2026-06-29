# Config Audit 4 — runner_entry_gate + atr_risk_sizing

READ-ONLY audit. No config or code was changed. Live config = `.agent-config.json` (hot-read).
Gate logic = `hermes_trader/agents/executor.py:_runner_entry_block_reason` (lines 1332-1454),
plus the sidestep pre-checks at executor.py:109-157 / 322-396 and the loop pre-block at
`scripts/trading_loop.py:263-282`.

## Live values audited

```
runner_entry_gate:
  enabled:                 true
  sidestep_exempt_conf:    true
  allow_shorts:            false
  require_daily_mover_longs: false
  shock_day_fresh_impulse: true
  min_confidence:          0.65
  sidestep_require_bullish: true     # added 2026-06-29
  sidestep_bearish_move_pct: -3.0    # added 2026-06-29
  min_composite:           30.0
  min_crypto_composite:    20.0
  min_hip3_composite:      32.0
  min_short_confidence:    0.72
  min_short_composite:     25.0
  mover_min_confidence:    0.65
  mover_min_composite:     20.0
atr_risk_sizing:
  enabled:                 true
  risk_per_trade_pct:      0.2
  sizing_basis:            primary_stop
```

## Per-key wiring table

| Key | Read at (file:line) | What it gates | Live? |
|-----|---------------------|---------------|-------|
| enabled | executor.py:1341; trading_loop.py:264 | master on/off for the whole gate | LIVE (true) |
| sidestep_exempt_conf | executor.py:1380 | exempt PASS->LONG sidestep from the conf floor (structure/composite still apply) | LIVE (true) |
| allow_shorts | executor.py:1385; trading_loop.py:266 | shorts pass at all; loop pre-blocks short candidates when false | LIVE (false -> short branch DEAD) |
| require_daily_mover_longs | executor.py:1404 | force every long to be a daily-mover + uptrend slow-burn pocket | wired, INERT (false) |
| shock_day_fresh_impulse | executor.py:1369 | lets a shock-day bar count as fresh impulse / exempt from composite floor | LIVE (true) |
| min_confidence | executor.py:1355,1381 | conf floor for all non-sidestep entries (first gate) | LIVE binds |
| sidestep_require_bullish | executor.py:124 (via :357) | block a PASS->LONG sidestep that is a bearish impulse | LIVE (true), WIRED |
| sidestep_bearish_move_pct | executor.py:132 (via :357) | the 24h move (<= -3.0%) that defines "bearish selloff" for the sidestep block | LIVE, WIRED |
| min_composite | executor.py:1356 (+326 sidestep_bar, +1232) | structure-substitute score in fresh_impulse/structured_runner; sidestep qualify bar | LIVE binds |
| min_crypto_composite | executor.py:1357,1424 | hard composite floor for NON-hip3 fresh non-burst longs | LIVE binds (narrow) |
| min_hip3_composite | executor.py:1358,1441 | composite floor for hip3 longs (unless structured daily-mover) | LIVE binds |
| min_short_confidence | executor.py:1388 | short conf floor | wired, DEAD (allow_shorts=false) |
| min_short_composite | executor.py:1387 | short composite floor | wired, DEAD (allow_shorts=false) |
| mover_min_confidence | executor.py:1411 | conf floor inside structured_daily_mover | wired, NON-BINDING (see below) |
| mover_min_composite | executor.py:1412 | composite floor inside structured_daily_mover | LIVE binds |
| atr_risk_sizing.enabled | executor.py:683,688 | turn on equal-risk sizing | LIVE (true) |
| atr_risk_sizing.risk_per_trade_pct | executor.py:708,721 | fraction of equity risked at stop | LIVE but INERT (see below) |
| atr_risk_sizing.sizing_basis | executor.py:709,710 | primary_stop vs backup_stop sizing path | LIVE (primary_stop) |

Nothing in the block is fully DEAD-unread. Both 2026-06-29 sidestep keys are confirmed wired:
`_sidestep_bearish_block_reason` (executor.py:109) reads `sidestep_require_bullish` (:124) and
`sidestep_bearish_move_pct` (:132), and it is called on the sidestep path at executor.py:356-364,
upstream of the runner gate. Commit `51bc23b` ("TA-sidestep was buying selloffs ... xyz:SMSN
falling-knife") is the origin.

## Which floor BINDS for each entry type

The gate runs in this order: (1) conf floor, (2) short branch, (3) long branch with composite
checks. For longs, structure gates (`fresh_impulse`, `structured_runner`, `structured_daily_mover`)
interleave with the composite floors. Effective binding floor per path:

| Entry type | Conf floor that binds | Composite floor that binds | Notes |
|------------|----------------------|----------------------------|-------|
| Crypto long, fresh impulse, no slow-burn | min_confidence 0.65 | min_composite 30 (via structured_runner `score>=min_score`) | needs score>=30 OR slow_burn OR shock to clear structure |
| Crypto long, fresh impulse + slow-burn | min_confidence 0.65 | min_crypto_composite 20 (executor.py:1424) | the 20 floor only bites in the 20-29 window once slow-burn supplies structure |
| HIP-3 long | min_confidence 0.65 | min_hip3_composite 32 (dominant) | `not is_hip3` guard means min_crypto_composite never applies here; min_composite 30 also passes via structured_runner, but 32 is the higher/effective bar |
| Daily-mover long | min_confidence 0.65 (mover_min_confidence 0.65 is subsumed) | mover_min_composite 20 | structured_daily_mover also BYPASSES the hip3-32 floor for a hip3 mover (executor.py:1441 `and not structured_daily_mover`) |
| Short (any) | n/a | n/a | entire branch DEAD: allow_shorts=false returns "shorts disabled" at executor.py:1386; loop also pre-blocks |
| Sidestep PASS->LONG | conf EXEMPT (sidestep_exempt_conf) | min_composite 30 to qualify (executor.py:326) + min_crypto/min_hip3 floor still applies in-gate; sidestep_bearish_move_pct -3.0 + direction pre-check | the only path that skips the conf floor |

Net: the conf floor that actually binds for every live long is `min_confidence=0.65`. The composite
floor depends on venue/structure — `min_composite=30` is the workhorse (structure substitute +
sidestep qualify), `min_hip3_composite=32` governs hip3, and `min_crypto_composite=20` /
`mover_min_composite=20` only bite in narrow already-structured windows.

## Redundancy / consolidation findings

1. **mover_min_confidence (0.65) is non-binding.** It equals min_confidence (0.65), and the
   min_confidence gate (executor.py:1381) runs FIRST for every non-sidestep entry, so any mover
   that fails 0.65 is already blocked. On the sidestep path the conf is forced to
   `max(0.70, conf)` (executor.py:392, min_ai_confidence floor 0.70), so it is always >= 0.70 >
   0.65 there too. `mover_min_confidence` cannot independently reject anything at the current value.
   CONSOLIDATE: drop it, or only keep it meaningful by setting it ABOVE min_confidence (its code
   default is 0.80, which would bind). Right now it is decorative.

2. **mover_min_composite (20) == min_crypto_composite (20).** Same numeric floor, two different
   paths (mover vs structured non-mover crypto). Not strictly mergeable because the mover path also
   covers hip3 movers and BYPASSES the hip3-32 floor, but the duplicate "20" is a latent foot-gun:
   a hip3 daily-mover scoring 20-31 is admitted under the 20 floor while a non-mover hip3 at the
   same score is rejected by the 32 floor. Flag for a deliberate decision, not auto-merge.

3. **min_composite vs min_crypto_composite overlap is confusing but not redundant.** `min_composite`
   (30) is a structure-substitute and the sidestep qualify bar; `min_crypto_composite` (20) is a
   narrow hard floor that only fires when slow-burn already supplied structure. They are layered
   (30 >= 20 by design). Keep both, but the naming implies they are alternative floors for the same
   thing — they are not. Document, do not merge.

4. **Short floors are DEAD while allow_shorts=false.** `min_short_confidence` (0.72) and
   `min_short_composite` (25) are unreachable. Cheap to keep as dormant config (needed the instant
   shorts are re-enabled, and they were swept by strategy_grid_search.py:457-459). KEEP as dormant,
   but they should not be counted as live tuning levers.

## atr_risk_sizing — RECONSIDER (the sizing is inert)

With live values (leverage 12, dsl max_loss_pct 2.5, max_loss_roe_pct 25, risk_per_trade_pct 0.2,
max_trade_notional_usd 200), the primary_stop path (executor.py:710-734) computes:

```
stop_frac      = min(2.5, 25/12) / 100 = 0.0208
trade_notional = risk_per_trade_pct * equity / stop_frac = 0.2 * equity / 0.0208 = 9.6 * equity
lev cap        = 12 * equity   (9.6 < 12 -> NOT binding)
notional cap   = $200          (binds whenever 9.6 * equity > 200, i.e. equity > $20.8)
```

For any account above ~$21 (it has run $35-$148), `trade_notional` is ALWAYS clamped to the $200
notional cap. The "equal-risk" sizing therefore never governs the bet size — it degrades to a flat
$200 notional every trade. `risk_per_trade_pct=0.2` (20% of equity at stop) is 10x the
config_store default (0.02) and 8x the historical 0.025 (commit 944afc8). At 0.2 the risk solve is
so large it is dominated by the cap, so the lever does nothing except guarantee the cap binds.

Two coherent fixes (operator call):
- Lower `risk_per_trade_pct` to ~0.02-0.025 so equal-risk actually normalizes size below the $200
  cap (then sizing becomes risk-driven, cap is a backstop), OR
- Accept that sizing is really "flat $200 notional" and say so — but then ATR equal-risk sizing is
  theater and could be simplified to a fixed-notional path.

This is the single highest-impact finding in the block.

## Verdicts

| Key | Verdict | Why / impact |
|-----|---------|--------------|
| enabled | KEEP | master switch, LIVE |
| sidestep_exempt_conf | KEEP | validated P3 entry-latency fix (commit b7e2c30, +0.43%/tr net main) |
| allow_shorts | KEEP | intentional off; gates the whole short branch + loop pre-block |
| require_daily_mover_longs | KEEP (dormant) | inert at false but a real lever; cheap to keep |
| shock_day_fresh_impulse | KEEP | LIVE, flag-gated shock path |
| min_confidence | KEEP | the binding conf floor for every live long |
| sidestep_require_bullish | KEEP | LIVE, wired, fixes the xyz:SMSN falling-knife buy |
| sidestep_bearish_move_pct | KEEP | LIVE, wired, the -3% bearish definition |
| min_composite | KEEP | workhorse: structure substitute + sidestep qualify bar |
| min_crypto_composite | KEEP | narrow hard floor for slow-burn crypto longs; not redundant |
| min_hip3_composite | KEEP | dominant hip3-long floor |
| min_short_confidence | KEEP (dormant) | DEAD until allow_shorts flips; needed then |
| min_short_composite | KEEP (dormant) | DEAD until allow_shorts flips; needed then |
| mover_min_confidence | CONSOLIDATE | non-binding (== min_confidence, subsumed by first gate); raise above min_confidence to make it matter, or drop it |
| mover_min_composite | KEEP (watch) | binds for movers; same value as min_crypto_composite and BYPASSES the hip3-32 floor for hip3 movers — confirm that is intended |
| atr_risk_sizing.enabled | KEEP | LIVE |
| atr_risk_sizing.risk_per_trade_pct | RECONSIDER | 0.2 makes equal-risk sizing inert (always clamps to $200 cap); lower to ~0.02-0.025 or admit sizing is flat-notional |
| atr_risk_sizing.sizing_basis | KEEP | primary_stop path wired correctly |

## Removable / consolidate list

- CONSOLIDATE: `mover_min_confidence` (non-binding duplicate of min_confidence at 0.65).
- WATCH/DECIDE: `mover_min_composite` == `min_crypto_composite` (both 20) and the mover path
  bypasses the hip3-32 floor — confirm the hip3-mover admission at score 20-31 is intended.
- DORMANT (not live levers, keep): `min_short_confidence`, `min_short_composite`,
  `require_daily_mover_longs` (all gated off by allow_shorts / require flag = false).
- RECONSIDER (highest impact): `atr_risk_sizing.risk_per_trade_pct=0.2` defeats the equal-risk
  sizing; the $200 notional cap binds on every trade above ~$21 equity.

Nothing is dead-unread. No key recommended for hard REMOVE — the unreachable ones are config-gated
dormant levers, not dead code.
