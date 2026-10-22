---
name: oil-tick
description: One tick of the Claude-run oil loop on pathiel. Sweep, read levels, read the desk brief, decide, place or hold, log one line. Run on a schedule with /loop 15m /oil-tick. Live money on the pathiel wallet.
---

# oil-tick

One tick of the oil loop Julien approved on 2026-10-23. Oil only
(`xyz:BRENTOIL`; `xyz:CL` only when the thesis names it). One position at
a time. Max 10x. Never chase. Every number comes from a tool or a script,
never from mental arithmetic.

## Steps, in order

1. **Housekeeping.** `bracket_sweep {coin: "xyz:BRENTOIL"}`. It cancels the
   sibling order left after a stop or target fired and tells you whether an
   open position is covered by a stop. `ok: false` with "NOT FULLY COVERED"
   means a 10x position is sitting naked: `close_position` it in the same
   tick, run `bracket_sweep` again, and report to Julien at once. An
   unprotected position is the failure that liquidated this account before;
   it is never left for the next tick.
2. **Levels.** `python3 scripts/oil_levels.py` (add `--coin xyz:CL` for WTI).
   Gives mid, last closed 1h candle, 8h/24h/48h range, ATR14, 15m micro
   range. Candles beyond that: `get_candles` with interval 15m or 1h.
3. **Thesis.** `desk_brief {desk: "oil"}` (quick). Call `desk_refresh` only
   when the brief says the thesis is stale or expired, or a headline has
   moved the market more than 1.5%; the tool skips itself otherwise, and it
   costs about $0.30 per rewrite. At most once an hour.
4. **Decide.** Exactly one of:
   - **In a position:** HOLD. Close with `close_position` only if the thesis
     invalidation triggered on a CLOSED hourly candle, or a Hormuz reopen /
     ceasefire headline landed (the desk brief's `what_changes_my_mind`).
     After any close, run `bracket_sweep` again.
   - **Flat with a resting entry:** keep it if the entry is still within one
     ATR14 of the mid and `scripts/oil_levels.py --check ENTRY STOP TP`
     still passes. Otherwise `cancel_order` it and fall through to the next
     case.
   - **Flat, nothing resting:** look for one setup in the thesis direction,
     anchored on structure from step 2: a pullback to the 8h or 24h range
     edge, or a breakout retest. Stop beyond the structure, target at the
     next structure. Run `--check`; it must say `ok: true` (1.5R or more).
     Then `place_bracket {coin, entry_px, stop_px, tp_px}` with `entry_px`
     set (a resting limit). Let the tool size it. No setup that passes:
     no trade, say why in one clause.
5. **Log.** Append one line to `/tmp/oil-loop/progress.log` (create the
   directory if needed) and print the same line:
   `OIL-TICK <utc> mid=<mid> pos=<size@entry or flat> action=<HOLD|PLACED|CANCELLED|CLOSED|NONE> <reason, under 15 words>`

## Hard rules

- A market entry only when `--check` with the live mid as ENTRY passes.
- Never widen a stop. Never add to a position. Never trade both Brent and
  WTI at once.
- If `place_bracket` or `bracket_sweep` is not in the tool list, do nothing
  and log `action=NONE reconnect pathiel MCP (/mcp)`.
- Any tool error on a live-money call: stop the tick, report it verbatim.
- Report to Julien only when something changed (placed, filled, closed,
  cancelled, uncovered position, tool error). A HOLD tick is the log line
  only.
