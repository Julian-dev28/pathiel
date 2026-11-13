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
2b. **Check the call grades are current** (read only, no network):

       python3 -m services.strategies.evals.grading_lag_check

   It prints one `GRADING OK|FAIL ...` line. On FAIL, print it and the
   rows under it: a call past its horizon with no grade means the
   dashboard server's hourly sweep (pathiel/server.py `_grade_loop`) is
   not running or is on old code; say so and name the restart. Never
   grade from here.
3. **Something new?** If `new_leaks` names a slice or `new_proposals` is
   above 0: say so in one short message (the slice, its label, n, and the
   lever the report proposes, from the report's "What to try next"), and
   append the summary line to `/tmp/oil-loop/progress.log` as
   `REVIEW <utc> <rest of the line>`.
4. **A new LEAK goes to the operator as a yes/no, the same day**
   (operator, 2026-11-04: "aren't we grading and growing... too many tests
   not enough results"). When `new_leaks` names a slice, send ONE
   `PushNotification` and print the same question:

       LEAK <slice>: <n> trades, <wins> win(s), <net $>. Turn on <lever>? yes/no

   `<lever>` is that slice's line under "What to try next" (its `Try` and
   what it does in plain words). Ask once per new leak. A yes is the go:
   make the change in the desk rules with a gate test and a real-fills
   eval (`services/strategies/evals/no_break_real_fills.py` is the pattern),
   commit, and say what to restart. A no: note it in the next report run and
   do not ask again for that slice. The review itself still changes nothing.
5. **If it fails** (network, a parse error): print the last line of the
   error once and stop. Do not retry in a loop; the next scheduled run will.

## Rules

- LEANING proposals are backtested on the operator's OK, with the command
  the report gives, and adopted only on the operator's go. A LEAK (10+
  trades, labeled LEAK) skips the backtest: it goes straight to the
  operator as the yes/no in step 4.
- Never edit live config, `.claude/agents/desk-tick.md`, the desk-tick
  skill, or a live constant from this skill. Never call a pathiel MCP tool
  here. The review only reads.
- Never feed the report into a thesis prompt (SCORECARD_TEST_2026-10-31.md:
  the desk's own record in its prompt made money worse).
- n is small: an EDGE or LEAK needs at least 10 trades in one slice. Say
  LEANING, FLUKE or NOISE as the report does; never round them up.
