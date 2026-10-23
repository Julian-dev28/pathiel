# AI Brain Operator Wiring

This repo has two supported ways to make an agentic CLI the decision-maker.
Both keep execution on the existing gated path.

## Mode 1: CLI Brain Behind The Loop

Use this when the normal trading loop should keep orchestration authority
(which coins, scan cadence, held-coin close checks), while Codex or Claude owns
the verdict.

Set either environment or hot config:

```bash
AI_BRAIN_PROVIDER=codex_cli   # openrouter | claude_cli | codex_cli
```

or:

```json
{
  "ai_brain": {
    "provider": "codex_cli",
    "timeout_s": 120,
    "codex_cli": { "command": "codex" },
    "claude_cli": { "command": "claude", "max_turns": 1 }
  }
}
```

Flow:

```text
trading_loop.py
  -> research._call_ai()
  -> ai_brain.CodexCliBrain / ClaudeCliBrain / OpenRouterBrain
  -> parse_verdict()
  -> executor.route_verdict()
  -> maybe_execute() / close_position_market()
```

Failure contract: CLI timeout, non-zero exit, empty stdout, or output without
verdict JSON returns `""`. `parse_verdict()` turns that into `ai_down=True`
PASS, and the TA sidestep override must not upgrade it.

Before using a CLI provider live, test the exact daemon environment:

```bash
claude -p --output-format json "Return PASS as final-line JSON."
printf '%s' 'Return PASS as final-line JSON.' | codex exec --sandbox read-only --ephemeral -
```

On this local Codex environment, `codex exec` may need permissions outside the
test sandbox. The live host must be able to run it non-interactively.

## Mode 2: Agent As MCP Brain And Operator (headless oil desk, 2026-10-23)

As of 2026-10-23 pathiel is a headless oil desk: Claude (or any MCP client)
owns orchestration directly through the MCP server, the autonomous bot's own
brain path (`scan` / `research` / `submit_verdict` / `execute`) is retired,
and Claude places trades through `place_bracket` rather than routing a stored
verdict through `executor.route_verdict()`. See
`.claude/skills/oil-tick/SKILL.md` for the loop Claude actually runs.

The important MCP tools are:

| Tool | Purpose |
|------|---------|
| `desk_list`, `desk_brief`, `desk_refresh` | The desk's thesis for a coin. |
| `market_get_asset_data`, `get_candles`, `get_l2_book`, `state` | Read-only context gathering. |
| `place_bracket` | One entry with an exact stop and target, sized by the risk engine. |
| `bracket_sweep` | Cancel a stranded sibling order; confirm an open position is covered by a stop. |
| `close_position` | Direct discretionary close via `close_position_market()`. |
| `config` | Read/write hot config, including `mode` and `ai_brain`. |

Agent-owned flow:

```text
desk_brief / market read tools
  -> agent decides a setup (or holds / does nothing)
  -> place_bracket(coin, entry_px?, stop_px, tp_px)
  -> bracket_sweep(coin)        # after every close, to clear a stranded sibling
```

`place_bracket` sizes with `services.risk.size.size_trade`, then places the
entry with its stop and target attached through
`pathiel.agents.bracket.place_bracket`. It refuses under the R-multiple
minimum, refuses when the stop sits too close to liquidation for the
requested leverage, and refuses a second entry on a coin that already has a
position or a resting order — a repeated call can never stack a trade.

Example payload:

```json
{
  "coin": "xyz:BRENTOIL",
  "entry_px": 105.5,
  "stop_px": 101.35,
  "tp_px": 112.19
}
```

## One Executor Rule

Only one live writer/executor should control the account.

Recommended topologies:

- **Loop brain provider mode**: loop is `LIVE`; MCP clients observe or operate
  manually. Do not run a second autonomous MCP executor.
- **MCP operator mode**: keep the loop in `OFF` so it still runs the heartbeat,
  kill-switch checks, and DSL exit monitor, while the MCP agent owns entries and
  discretionary closes.

Set loop OFF through MCP or config:

```json
{ "mode": "OFF" }
```

`mode=OFF` skips scan/research/entry execution in the loop, but the loop still
monitors exits and can close positions from the DSL engine.

## Pathiel Agent

Register the stdio MCP server in Pathiel Agent config:

```yaml
mcp_servers:
  pathiel:
    command: python3
    args:
      - /Users/julian_dev/Documents/code/pathiel/scripts/pathiel-mcp-server.py
    cwd: /Users/julian_dev/Documents/code/pathiel
    timeout: 120
    connect_timeout: 30
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
      AI_BRAIN_PROVIDER: ${AI_BRAIN_PROVIDER}
```

Use the repo skill and instruct Pathiel:

```text
Use pathiel as the MCP operator. Read the desk brief and market context with
read-only tools, then place_bracket a setup with an exact stop and target if
one passes the R gate. Keep risk gates non-bypassable and never place orders
outside pathiel tools.
```

## Claude Code

Claude Code can connect to the same stdio MCP server. Use an MCP config JSON file
or the CLI's `--mcp-config` option.

Example `pathiel.mcp.json`:

```json
{
  "mcpServers": {
    "pathiel": {
      "command": "python3",
      "args": [
        "/Users/julian_dev/Documents/code/pathiel/scripts/pathiel-mcp-server.py"
      ],
      "cwd": "/Users/julian_dev/Documents/code/pathiel",
      "env": {
        "OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}",
        "AI_BRAIN_PROVIDER": "${AI_BRAIN_PROVIDER}"
      }
    }
  }
}
```

Launch:

```bash
claude --mcp-config /Users/julian_dev/Documents/code/pathiel/pathiel.mcp.json
```

Operator prompt:

```text
You are the pathiel brain. Use only pathiel MCP tools for market
state and actions. For entries, call place_bracket with an exact stop and
target. For discretionary closes, call close_position, then bracket_sweep.
Never bypass the risk gates or call exchange APIs directly.
```

## Codex CLI

Codex can be used in either mode:

- Provider mode: set `AI_BRAIN_PROVIDER=codex_cli`.
- MCP operator mode: run Codex in a workspace where the MCP client is configured
  to expose `pathiel`.

For provider mode smoke test:

```bash
.venv/bin/python -c 'from pathiel.agents.ai_brain import CodexCliBrain; print(CodexCliBrain().complete("Return final-line JSON.", "Return PASS for BTC with numeric fields."))'
```

For MCP operator mode, use the same `place_bracket` / `bracket_sweep` contract
as Claude Code.

## OpenClaw / OpenClaw-Compatible MCP Clients

If the client supports stdio MCP servers, use the same server definition:

```json
{
  "name": "pathiel",
  "command": "python3",
  "args": [
    "/Users/julian_dev/Documents/code/pathiel/scripts/pathiel-mcp-server.py"
  ],
  "cwd": "/Users/julian_dev/Documents/code/pathiel",
  "env": {
    "OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}",
    "AI_BRAIN_PROVIDER": "${AI_BRAIN_PROVIDER}"
  }
}
```

If OpenClaw expects a different outer key (`servers`, `mcpServers`,
`mcp_servers`), keep the command/args/cwd/env values identical and adapt only
the wrapper shape.

Required operator instruction:

```text
Use pathiel MCP tools only. Read market/account context with read-only
tools. When you decide, call place_bracket with an exact stop and target.
Do not call raw exchange tools or create another executor path.
```

## Research routing: MCP sampling (no current consumer)

The MCP server still negotiates the client's `sampling` capability at
`initialize` and can send a server -> client `sampling/createMessage` request
via `_server_request`. The `research` tool that originally consumed this (so
the harness driving the bot was also the brain that researched, no OpenRouter
call) was retired with the bot's MCP brain path on 2026-10-23 — Mode 2 above
is now `place_bracket`, not a stored-verdict flow. The transport itself stays
wired, for a future tool that wants the connected harness's own model instead
of a hosted API.

- `PATHIEL_MCP_DISABLE_SAMPLING=1` still exists but has nothing to disable.
- Tests: `tests/test_mcp_sampling.py` (transport correlation; the brain-parse
  and capability-gate tests were removed with `research`).

## Verification

Safe focused tests:

```bash
.venv/bin/python -m pytest \
  tests/test_ai_brain.py \
  tests/test_cleanup.py::test_mcp_close_position_delegates_to_executor \
  tests/test_cleanup.py::test_mcp_config_allows_ai_brain_provider_update
```

MCP wiring audit:

```bash
python3 skills/pathiel-agent/scripts/audit_mcp_server.py
```

Codex provider smoke:

```bash
.venv/bin/python -c 'from pathiel.agents.ai_brain import CodexCliBrain; from pathiel.agents.research import parse_verdict; t=CodexCliBrain().complete("Return final-line JSON.", "Return PASS for BTC with confidence 0.0, side null, entryPx 100, stopPx 0, tpPx 0."); print(t); print(parse_verdict(t, "BTC", {"mid": 100.0}))'
```
