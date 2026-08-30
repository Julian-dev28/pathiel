# W-ME1 — main_engine: REFUTED, and structurally inert on the majors universe

**Date:** 2026-08-30
**Decision:** delete. Not "leave off pending evidence" — deleted.

## Why the existing verdict could not be trusted in either direction

The forward ledger reads n=47, EV +1.71% @6bps, both OOS halves positive,
survives 25bps, mc_p=0.066 — MARGINAL, failing only the significance bar.

That reads like "nearly validated". It is not. **The 47 signals span 7.5 days.**
The grader's "both OOS halves positive" is therefore splitting a single market
week in half, which is not out-of-sample in any useful sense. On that base the
book can neither validate nor refute, and the 0.066-vs-0.05 distinction is
noise about noise.

## What was actually tested

main_engine is: scan -> composite TA trigger -> AI verdict -> risk gates ->
execute. The trigger half is pure and replayable; the AI half is not. So the
answerable question: **does the trigger alone beat entering at random?**

BTC/ETH/SOL/BNB/XRP, 5m bars, ~17 days each, one signal per coin per 6h, graded
forward 1 day with a stop, against a matched random-time null on the same coins
with the same holding rule.

| min score | stop | n | win | mean % | null % | excess | p | h1 | h2 |
|---|---|---|---|---|---|---|---|---|---|
| 54 (live) | 6% | — | — | — | — | — | — | — | — |
| 54 (live) | 15% | — | — | — | — | — | — | — | — |
| 40 | 6% | 90 | 0.71 | +2.154 | +1.614 | +0.540 | 0.117 | +2.40 | +1.90 |
| 40 | 15% | 90 | 0.71 | +2.078 | +1.663 | +0.415 | 0.173 | +2.40 | +1.75 |

Bonferroni threshold for 4 cells: 0.0125. **Nothing survives.**

The +2.15% looks like a win until the null is read: a random entry on the same
coins over the same window returned +1.61%. The trigger captured beta, not
signal. Excess +0.54% at p=0.117 is not an edge.

## The finding that settles it

At the LIVE gate the trigger produced **zero signals across all five majors in
17 days**:

| coin | >=54 (live gate) | >=40 | >=30 | >=20 | peak score |
|---|---|---|---|---|---|
| BTC | 0 | 3 | 10 | 157 | 42.9 |
| ETH | 0 | 2 | 10 | 156 | 45.9 |
| SOL | 0 | 3 | 9 | 153 | 43.1 |
| BNB | 0 | 3 | 12 | 132 | 43.2 |
| XRP | 0 | 1 | 10 | 143 | 40.1 |

The composite never once reached 54; the highest score any major printed was
45.9. The weighted triggers (pct_move_spike, volume_spike, momentum_burst) are
volatility-driven, and majors do not spike the way the microcaps its 47 ledger
signals came from do.

So on the current universe main_engine is **structurally incapable of firing**,
independent of whether it has an edge. Its historical record was earned on a
universe that no longer exists.

## Verdict

Delete. Five independent reasons, any one sufficient:

1. Zero signals in 17 days on the live universe at the live gate.
2. No excess over a matched null even at a loosened gate (p=0.117).
3. Its forward record is 7.5 days — too thin to validate anything.
4. Live P&L on record: -$172.33 over 157 trades, every slice negative
   (demoted 2026-07-20).
5. It was neither live nor validated, which under the standing rule
   ("if it's shadow, nuke it") is not a state a book may occupy.

The AI research path is deleted from the trading loop with it — the four live
books use the SCAN but none of them consume an AI verdict, so nothing else in
the per-cycle path needed it. `research.py` and `ai_brain.py` survive as
operator tooling (`/api/agent/research/{coin}`, the CLI, the MCP server).

Reproduce: `research/alpha_swarm/hypotheses/W-ME1_main_engine_trigger.py`.
