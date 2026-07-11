# W-M4 — our-interaction audit: what the bot did with every scanned mover, vs what the mover did next

**Method (PIT, `hypotheses/W-M4_gate_audit.py`):** every coin in a `[scan] crypto-movers` / `HIP-3-movers` log line, 2026-06-27 .. 2026-07-11; episodes = appearances separated by >=24h absence (165 episodes, 109 coins). For each episode, the bot's actions in the next 24h (gates / research / verdict / execution) from the same log, and the coin's forward 24h/72h return from fetched 1h closes (one-time cached fetch, W-H0/W-Y0 pacing). fwd returns unavailable for episodes newer than 24/72h or failed fetches.

## Gate-vs-winners table

| outcome (dominant) | n | n24 | mean fwd24 | med fwd24 | %pos24 | n72 | mean fwd72 |
|---|---|---|---|---|---|---|---|
| never_touched | 60 | 52 | -0.29% | -0.26% | 42% | 38 | +2.85% |
| gate:liquidity_floor_preflight | 19 | 17 | +0.94% | +0.68% | 53% | 15 | +0.75% |
| gate:runner_gate_blocked | 17 | 15 | +0.27% | +1.18% | 53% | 14 | -0.99% |
| gate:daily_giveback_gate | 16 | 16 | +1.76% | -0.06% | 50% | 0 | - |
| **ai_pass** | 15 | 14 | **+4.48%** | **+2.23%** | **79%** | 11 | +3.41% |
| gate:notional_room_full | 10 | 10 | -1.40% | +0.75% | 60% | 1 | +13.8% |
| gate:daily_loss_gate | 9 | 5 | **+5.68%** | +6.06% | 80% | 5 | +0.81% |
| gate:history_floor_preflight | 6 | 6 | +4.15% | +1.87% | 67% | 5 | -0.80% |
| EXECUTED | 5 | 5 | +1.50% | +1.52% | 60% | 4 | **-12.71%** |
| gate:insufficient_free_margin_preflight | 4 | 4 | +4.07% | +2.63% | 100% | 4 | +7.40% |
| verdict_LONG_blocked | 3 | 2 | -2.17% | -2.17% | 50% | 2 | -5.85% |
| verdict_SHORT_blocked | 1 | 1 | -9.53% | -9.53% | 0% | 1 | -2.10% |
| ALL | 165 | 147 | +0.99% | +0.45% | 54% | 100 | +1.22% |

## Reading (with the honest caveats: n is small, no matched null, 14 days, one tape)

1. **The scanner is not the leak.** The 60 movers the loop never even gate-checked went -0.29% mean fwd24. The pipeline's attention allocation is roughly sane.
2. **The AI PASS veto is the standout.** 15 episodes were researched and PASSed; they went +4.48% mean / +2.23% median fwd24, 79% positive — the best-performing bucket we did not trade. Even excluding the S outlier (+29.5% fwd24, PASSed 28 times in a row) the bucket is ~+2.6% mean. This is fresh, independent confirmation of the known runner-gate/AI-PASS leak (activity audit 2026-06-28; entry-latency memory) on new data.
3. **Loss/giveback lockouts blanket-block winner days.** daily_loss_gate episodes went +5.68% mean fwd24 (n24=5, 80% pos); the 07-09 daily_giveback lockout alone swallowed 12 episodes including xyz:ZHIPU (seen +25%, fwd24 +20.6%). 07-11's daily_loss_gate lockout (manual-loss triggered) blocked 1 episode so far, fwd pending. Risk gates cost EV by construction — but note the gates fire on ACCOUNT state, uncorrelated with the coin's prospects, so this is unpriced opportunity cost, not protection against these specific trades.
4. **Execution is fine at 24h, ugly at 72h.** The 5 executed movers: +1.50% fwd24 but -12.71% mean fwd72 — the coins we did catch rolled over hard after. Consistent with the exit-config-is-the-lever finding; not an entry problem.
5. **liquidity_floor / history_floor blocked a few real runners** (xyz:RKLB +15.7%, xyz:BOT +12.2% fwd24, both history_floor; MEGA +9.6% liquidity_floor) but their buckets average only +0.9..+4.2% on tiny n — matches the standing verdict that floors mostly save money (W-Y1 covered the young-listing side and refuted chasing it).

**Bottom line:** on these 14 days the misses were NOT a missing entry trigger (W-M1/2/3 refute the trigger space); they were (a) AI PASS on already-researched movers and (b) account-state lockouts. Both are known, already-instrumented levers — this audit adds 14 days of PIT evidence, it does not by itself justify loosening either (small n, no null, and the giveback/loss gates exist for solvency reasons).

Artifacts: `scratchpad/W-M4_results.json` (all 165 episodes with actions + forward returns), candle cache `scratchpad/W-M4_movers_1h.json`.
