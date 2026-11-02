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
   They are session-only and expire after 7 days; re-run `/desk-loops`
   then.
2. **Price watcher.** `pgrep -f scripts/trigger_watch.py`. If one is
   already running (another window owns it), do not start a second one.
   Otherwise run in the background (Bash, `run_in_background: true`):
   `mkdir -p /tmp/trigger-watch && python3 scripts/trigger_watch.py 2>>/tmp/trigger-watch/stderr.log`
3. Say one line: which crons exist (ids) and whether this window owns the
   watcher. Then wait.

## When the watcher exits

- It printed `{"fire": "<desk>", ...}`: run `/desk-tick <desk>` (the
  desk-tick skill), then start the watcher again exactly as in step 2.
- It exited without a fire line (error, crash): print the last lines of
  `/tmp/trigger-watch/stderr.log` in one line, then start it again. If it
  crashes 3 times within 10 minutes, stop restarting and say so.

## Overlap rule

If a tick for the same desk is still running, or one started in the last
minute (a cron and a watcher fire landing together), do not launch a
second agent. Append
`DESK-TICK <desk> <utc> action=SKIP previous tick still running` to
`/tmp/oil-loop/progress.log` instead.

## Rules

- The desk-tick skill and the `desk-tick` agent carry every trading rule;
  this skill only decides WHEN a tick runs.
- Quiet chat: HOLD, SKIP and repeat-NONE ticks say nothing here either.
- Do not restart the pathiel MCP server, touch `.env.local`, or call a
  live-money tool outside a desk tick.
