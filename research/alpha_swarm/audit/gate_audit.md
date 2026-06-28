# Gate audit — were our pre-research / entry skips RIGHT, or did they kill alpha?

Read-only forensic. For every LONG the bot SKIPPED (gated out), measure the coin's forward return
*after* the skip ts. WRONG skip = coin then mooned (up-move we missed). RIGHT skip = flat/down.

- Source: `~/.hermes-trader-session-log.jsonl`, last **14 days** by ts (log ends 2026-06-28 09:15 UTC).
- Skip sources: `entry_preflight` events (reason field) + `execute` events with `executed=false`
  (the `runner_gate_blocked` / `trend_filter` / etc. live in `execute.detail`, NOT a standalone event).
- Long-only (the bot was considering a long). Deduped coin+reason+hour.
- Forward prices: HL 1h candles, entry ref = close of first candle at/after skip; max-up / last-return
  over next 24h and 72h. 158 unique coins fetched.
- Script: `scratchpad/gate_audit.py`; raw: `scratchpad/gate_audit_result.json`.

## Caveats (read before trusting a row)
1. **Survivorship.** Skipped coins that later delisted are absent → a skip that dodged a death looks
   neutral/missing. Real "RIGHT" rate is higher than shown.
2. **Paper mid-return, no slippage/fill.** Forward return is on the mid. For thin coins the realized
   long would eat slippage — matters a lot for `liquidity_floor` (sub-$0.7M vol).
3. **`entry_preflight` only spans the last ~2 days** of the log (liquidity_floor / daily_giveback /
   loss_cooldown-preflight), so their 24h-forward n is small and 72h is mostly n/a. `runner_gate` /
   `trend_filter` (from `execute`) span the full 11-14d → those verdicts are the solid ones.
4. **`maxUp24` is a high-water mark, not a fill you'd keep.** A high maxUp with a NEGATIVE median
   return = blowoff-top you'd have bought and given back (see sidestep_extension).

## Coin-level table (each coin counts ONCE per gate — neutralizes one-mooner concentration)

| gate (reason)               | coins | mooned >8%/24h | % moon | median 24h ret | verdict |
|-----------------------------|------:|---------------:|-------:|---------------:|---------|
| **runner_gate_blocked**     |    86 |             33 | **38%**|        **+0.7%** | **OVER-BLOCKING — killing alpha** |
| trend_filter                |    58 |             10 |    17% |          -2.2% | RIGHT (median down; best filter) |
| insufficient_free_margin    |    73 |             23 |    32% |          +1.0% | NOT a signal gate — CAPITAL leak |
| override_no_volume_confirm  |    57 |             13 |    23% |          +0.4% | marginal / mild over-block |
| liquidity_floor_preflight   |    17 |              7 |    41% |          +1.9% | paper-alpha but UNTRADEABLE (slippage) |
| loss_cooldown               |    23 |              9 |    39% |          +1.8% | leans RIGHT (anti-churn) — watch |
| signal_veto                 |    16 |              0 |  **0%**|          -0.1% | **RIGHT — perfect filter** |
| sidestep_extension_blocked  |     5 |              4 |    80% |        **-9.9%** | **RIGHT (buys blowoff top → dumps)** |
| daily_giveback_gate         |    13 |              5 |    38% |          +0.3% | risk gate (capital protection cost) |
| account_state_unavailable   |     6 |              3 |    50% |          -1.7% | infra/degraded-read, not a strategy gate |
| reentry_cap                 |     2 |              0 |     0% |          +0.0% | n too small |

(Event-level table — un-deduped, in `gate_audit_result.json` → `report`. runner_gate event-level:
612 events, 25% moon>8%/24h, 18% moon>15%/72h, median 24h -1.2%, median 72h -1.6%, median maxUp +4.3%.)

## Headline

**`runner_gate_blocked` is the gate costing us alpha.** 86 distinct coins blocked in 14d; **38% of
them popped 8%+ within 24h**, broad not concentrated (xyz:BIRD +34.6%, MET +27.6%, EIGEN +25.1%,
AAVE +21.9%, POPCAT +27.1%, TNSR +20.7%), median blocked-coin return slightly POSITIVE (+0.7%). It
does catch duds (event-level median -1.2%, so most *attempts* are flat), but the tail it throws away
is fat and real. This is the documented "AI vetoes TA-confirmed breakouts / entry-latency" leak,
re-confirmed forward: the runner-gate's "late trend-only chase, no fresh breakout/burst" rejection
fires on coins that then break out anyway.

**The gates that are RIGHT:**
- `signal_veto` — 0/16 mooned, median -0.1%. Flawless; vetoed signals went nowhere.
- `sidestep_extension_blocked` — high maxUp (4/5 spiked) but median **-9.9%**: those coins blow off
  then dump. Blocking the extended chase is correct (matches the TNSR/extension validation).
- `trend_filter` — 17% moon, median -2.2% (24h) / -8.8% (72h): counter-trend longs it blocked mostly
  fell. Cleanest signal filter in the set.

**Not a gate problem — a capital problem:** `insufficient_free_margin` blocked 73 coins, 32% then
mooned (UNI +25.3%, xyz:QNT +24.6%, PURR +19.9%). This isn't a mis-calibrated filter; the book/main
account was simply full or stranded (matches the "missed moves = capital saturation" and "stranded
xyz capital" findings). Fixing it is upstream capital allocation, not the gate.

**Looks alpha-costly but is a trap:** `liquidity_floor_preflight` shows 41% moon / +1.9% median, but
that's a mid-return on sub-$0.7M-vol coins. Prior validation says lowering this floor is -EV once
slippage is in; the paper number here does not contradict that — it's exactly the un-tradeable
mirage the floor exists to avoid. Keep the floor.

## runner_gate sub-reason split (coin-level, 24h, 14d)

| sub-reason                       | events | coins | % moon>8% | median 24h ret |
|----------------------------------|-------:|------:|----------:|---------------:|
| late_chase_no_breakout           |    170 |    38 |       37% |          -2.0% |
| needs_vol+breakout+structure     |    147 |    43 |       37% |          -0.8% |
| confidence<floor (AI conf<0.70)  |    120 |    49 |       31% |          **+1.0%** |
| other                            |    175 |    20 |       25% |          +2.3% |

No single sub-reason is innocent — all three big ones throw away ~31-37% of coins that then pop 8%+.
But the texture differs: `late_chase_no_breakout` and `needs_vol+breakout+structure` have NEGATIVE
median return (they do catch the median dud; the 37% moon is the fat tail), whereas
**`confidence<floor` has a POSITIVE median (+1.0%) AND 49 distinct coins blocked** — that's the
purest "AI confidence floor vetoed coins that then ran" signal, the exact entry-latency leak in the
memory notes. The breakout-freshness checks are defensible (median down); the **0.70 confidence floor
is the lever most worth relaxing/backtesting.**

## Recommended next probe (not done here — read-only audit)
Backtest a relaxed `runner_gate` confidence floor (e.g. 0.70 → 0.60-0.65) and/or a softer
breakout-freshness check, cost-swept, both exit modes, before any live change. The breakout/structure
sub-reasons have negative median return so leave them; target the confidence floor specifically.
Separately, the `insufficient_free_margin` misses (32% of 73 coins) are the capital-allocation leak,
not a gate — route capital to main / unstrand xyz USDC (operator action, per memory).
