---
name: desk-tick
description: One tick of a desk's trading loop on pathiel, for the desk id given as the argument (oil, metals, ai, gas). Slot check here, then the desk-tick agent (Opus 5.5, medium effort) runs sweep, levels, brief, one decision, one log line. Run on a schedule with /loop 15m /pathiel:desk-tick oil. Live money on the connected Hyperliquid account.
---

<!-- GENERATED from .claude/skills/desk-tick/SKILL.md by scripts/build_plugin_skill.py. Edit the source, then rerun it. -->
# desk-tick $ARGUMENTS

One tick for desk `$ARGUMENTS` (default `oil` if empty). Two parts: the slot
check runs here because it is one script call, and the tick itself runs in
the `desk-tick` agent so the session's own model is not spent on a
15-minute loop (operator, 2026-10-25: "fable is valuable and the tick is
eating my usage").

1. **Slot first.** `"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/desk_slot.py --desk $ARGUMENTS`.
   If `slot` is `held` and `mine` is false, another desk's coin holds the
   account's one slot: append
   `DESK-TICK $ARGUMENTS <utc> action=SKIP slot held by <holder>` to
   `/tmp/oil-loop/progress.log`, print it, and STOP. No agent, no MCP calls.
   The desk that holds the slot is the only desk that ticks (operator rule,
   2026-10-23).
2. **Otherwise launch the agent.** Agent tool, `subagent_type` `pathiel:desk-tick`,
   prompt `DESK: $ARGUMENTS`. Do not run the tick steps yourself and do not
   launch more than one agent per tick. The agent file
   (the plugin's `agents/desk-tick.md`) carries the whole procedure and its hard
   rules.
3. **Relay.** Print the agent's log line. Report to the operator only when
   something changed (PLACED, CANCELLED, CLOSED, NONE, an uncovered
   position, a tool error): one sentence with the numbers. A HOLD tick is
   the log line only.

If the agent fails to launch or returns no log line, append
`DESK-TICK $ARGUMENTS <utc> action=NONE agent did not run` to the log and
say so.
