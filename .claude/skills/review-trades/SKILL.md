---
name: review-trades
description: Review the desks' closed trades - rebuild the trade ledger from public fills, test every slice for fluke or edge, list backtest proposals. Read only; never edits live config or places an order. Runs daily from /desk-loops and once after any close.
---

# review-trades

The learning loop (operator, 2026-11-02: "continuously evolve, learn from
what we did right, fix what we did wrong, see if mistakes and successes are
just flukes or a predictions"). One script call; no agent, no LLM, no MCP.

1. **Run it** from the repo root:

       python3 -m services.review run

   It fetches the wallet's fills (public Hyperliquid reads), updates
   `$PATHIEL_STATE_DIR/review/ledger.jsonl`, writes
   `docs/strategy/learning/REVIEW_<UTC date>.md` and prints one line that
   starts `REVIEW <utc> trades=...`.
2. **Print that line.** Nothing else when it says `new_leaks=none` and
   `new_proposals=0`.
3. **Something new?** If `new_leaks` names a slice or `new_proposals` is
   above 0: say so in one short message (the slice, its label, n, and the
   lever the report proposes, from the report's "What to try next"), and
   append the summary line to `/tmp/oil-loop/progress.log` as
   `REVIEW <utc> <rest of the line>`.
4. **If it fails** (network, a parse error): print the last line of the
   error once and stop. Do not retry in a loop; the next scheduled run will.

## Rules

- Proposals are backtested on the operator's OK, with the command the
  report gives. Adopt only what beats live on most datasets as a plateau,
  and then only on the operator's go.
- Never edit live config, `.claude/agents/desk-tick.md`, the desk-tick
  skill, or a live constant from this skill. Never call a pathiel MCP tool
  here. The review only reads.
- Never feed the report into a thesis prompt (SCORECARD_TEST_2026-10-31.md:
  the desk's own record in its prompt made money worse).
- n is small: an EDGE or LEAK needs at least 10 trades in one slice. Say
  LEANING, FLUKE or NOISE as the report does; never round them up.
