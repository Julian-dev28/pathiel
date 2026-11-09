---
name: desk-loops
description: Start the live desk loops in their own Claude Code window - the 15-minute oil and metals desk-tick crons plus the price trigger watcher. Run once per tick window; re-run after 7 days when the crons expire. Live money on the pathiel wallet.
---

# desk-loops

This window runs the desk ticks and nothing else, so the operator's main
chat stays clean (operator, 2026-11-02: "move the ticks to their own
window"). Start it from the repo root:

    claude --model opus --permission-mode auto -n "pathiel desk loops" "/desk-loops"

## Setup, in order

1. **Crons.** `CronList` first. Create only the ones missing (never two of
   the same desk):
   - `CronCreate` cron `3,18,33,48 * * * *`, prompt `/desk-tick oil`, recurring.
   - `CronCreate` cron `10,25,40,55 * * * *`, prompt `/desk-tick metals`, recurring.
   - `CronCreate` cron `7 0 * * *`, prompt `/review-trades`, recurring
     (the daily trade review: read only, see `.claude/skills/review-trades`).
   - `CronCreate` cron `17 */12 * * *`, prompt `/event-calendar scan`,
     recurring (the 12-hour event scan: congress.gov, the Federal Register
     and one research agent, written to the scan overlay; see the "Scan"
     section of `.claude/skills/event-calendar`).
   They are session-only and expire after 7 days; re-run `/desk-loops`
   then.
   Then run the deterministic scan once so the brief has today's events
   before the first tick: `python3 scripts/event_scan.py` (one
   `EVENT-SCAN ...` line; without `CONGRESS_API_KEY` it says so and
   still runs the Federal Register part).
2. **Price watcher.** `pgrep -f scripts/trigger_watch.py`. If one is
   already running (another window owns it), do not start a second one.
   Otherwise run in the background (Bash, `run_in_background: true`):
   `mkdir -p /tmp/trigger-watch && python3 scripts/trigger_watch.py 2>>/tmp/trigger-watch/stderr.log`
3. Say one line: which crons exist (ids) and whether this window owns the
   watcher. Then wait.

## When the watcher exits

- It printed `{"fire": "<desk>", ...}`: run `/desk-tick <desk>` (the
  desk-tick skill), then start the watcher again exactly as in step 2.
- The fire line says `"reason": "headline"` (a surprise headline, an odds
  jump or a price shock; operator, 2026-11-05: "FIX THEM"): run
  `/desk-tick` for the `"fire"` desk AND for each desk listed in `"also"`,
  one agent per desk (the overlap rule below still applies to each), then
  start the watcher again. The tick runs the rewrite check first; the
  `"headline"` field is what it will read about.
- The fire line says `"reason": "weekend_flat"` (operator, 2026-11-05:
  "weekend flat"; the oil futures weekend just opened and the oil desk
  holds a position or a resting entry): run `/desk-tick oil`; the tick's
  weekend step closes it. Then start the watcher again. It fires once per
  weekend; the 15-minute cron covers a tick that fails.
- The fire line says `"reason": "signer"` (2026-11-09: the API wallet that
  signs our orders dropped off the account and every order failed for 11
  hours): do NOT run a tick, it can only fail. Send the operator one
  PushNotification and one chat line with the `"detail"` text (it names the
  address to approve again on the Hyperliquid API page), then start the
  watcher again. It repeats at most every 6 hours while the key stays
  refused. Check by hand any time: `python3 scripts/signer_check.py`.
- It exited without a fire line (error, crash): print the last lines of
  `/tmp/trigger-watch/stderr.log` in one line, then start it again. If it
  crashes 3 times within 10 minutes, stop restarting and say so.

## After a close

When a tick's line says a position closed (`action=CLOSED`, `STOPPED` or
`TARGET`, or any stop or target fill line), run `/review-trades` once after
that tick finishes. One run per close; the daily cron covers the rest. The
review only reads: it never changes a stop, a size or the config.

## Overlap rule

If a tick for the same desk is still running (started under 20 minutes
ago), or one started in the last minute (a cron and a watcher fire landing
together), do not launch a second agent. A tick older than 20 minutes is
presumed dead (the desk-tick skill's dead-tick rule): launch a new one. Append
`DESK-TICK <desk> <utc> action=SKIP previous tick still running` to
`/tmp/oil-loop/progress.log` instead.

## Rules

- The desk-tick skill and the `desk-tick` agent carry every trading rule;
  this skill only decides WHEN a tick runs.
- Quiet chat: HOLD, SKIP and repeat-NONE ticks say nothing here either.
- Do not restart the pathiel MCP server, touch `.env.local`, or call a
  live-money tool outside a desk tick.
