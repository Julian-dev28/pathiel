# Signal vs Action Gap

Use this when scans show triggers but the bot does not open a position.
Do not assume the scanner is broken. In the live stack, most non-executions are
intentional gate decisions.

## Current diagnostic order

1. Read the activity feed:
   ```bash
   python3 skills/hermes-trader-agent/scripts/feed.py -n 80
   python3 skills/hermes-trader-agent/scripts/feed.py --filter entry_preflight,ta_skip,research,execute --since 30m
   ```
2. Bucket the latest candidates by event:
   - `entry_preflight` = deterministic live gate proved the entry cannot execute, so paid AI was skipped.
   - `ta_skip` = TA rejection, held/cooldown throttle, research throttle, or pre-research runner gate.
   - `research` with `PASS` = AI found no clean setup.
   - `execute executed=false` = executor/risk gate blocked after research.
3. Use `execute.detail` / `blocked_by` as the source of truth. Typical valid blocks:
   - `runner_gate_blocked (late trend-only chase; no fresh breakout/burst)`
   - `trend_filter (long fights the daily 200d-MA downtrend...)`
   - `override_no_volume_confirm`
   - `market 24h volume ... below floor`
   - `cooldown`, `daily_giveback`, `insufficient_free_margin`

## What not to do

- Do not lower `min_ai_confidence` just to force activity.
- Do not reintroduce removed EV-/shadow methods.
- Do not treat `ta_skip` as only TA rejection; inspect `signal` and `reason`.
- Do not write blocked attempts to trade memory; cooldown should key off real fills.

## When to change code/config

Only change behavior after a fresh backtest or live-forward audit shows a
specific block is rejecting EV+ setups. Good examples of narrow changes:

- Admit a validated late-chase pocket through `late_chase_relax`.
- Add or tighten a deterministic preflight when a gate will obviously reject.
- Fix logging when a block reason is hidden.

Bad examples:

- Broad PASS upgrades.
- Shadow-only alpha.
- Global confidence lowering.
- Disabling liquidity or trend safety gates to increase trade count.

## File pointers

- `scripts/trading_loop.py` — feed event emission and pre-research gates.
- `hermes_trader/agents/executor.py` — verdict routing, runner gate, trend filter.
- `hermes_trader/agents/risk_gates.py` — risk-gate block reasons.
- `skills/hermes-trader-agent/scripts/feed.py` — human-readable feed rendering.
