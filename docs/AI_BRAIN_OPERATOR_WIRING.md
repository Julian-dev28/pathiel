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

## Mode 2: Agent As MCP Brain And Operator

Use this when Codex / Claude Code / Hermes Agent / OpenClaw owns orchestration:
which coins to inspect, when to submit a verdict, when to execute, and when to
close.

The important MCP tools are:

| Tool | Purpose |
|------|---------|
| `scan` | Surface triggered candidates. |
| `market_get_asset_data`, `get_candles`, `get_l2_book`, `state` | Read-only context gathering. |
| `submit_verdict` | Store the agent's own verdict as an analysis. |
| `execute` | Route the stored verdict through `executor.route_verdict()`. |
| `close_position` | Direct discretionary close via `close_position_market()`. |
| `config` | Read/write hot config, including `mode` and `ai_brain`. |

Agent-owned verdict flow:

```text
scan / read tools
  -> agent decides PASS/LONG/SHORT/CLOSE
  -> submit_verdict(...)
  -> execute(analysisId)
  -> route_verdict()
  -> maybe_execute() or close_position_market()
```

`submit_verdict` is the thin verdict-authority adapter. It does not place
orders. It records the submitted verdict as an analysis and returns an
`analysisId`. `execute` is the only action step and still runs the existing risk
gates for entries. `CLOSE` routes to the same close helper used by the loop.

Example payload:

```json
{
  "coin": "BTC",
  "verdict": "LONG",
  "confidence": 0.82,
  "side": "long",
  "entryPx": 100000,
  "stopPx": 98500,
  "tpPx": 103000,
  "reasoning": "4h/1d trend aligned, pullback held, volume confirms.",
  "source": "codex_mcp"
}
```

Then:

```json
{ "analysisId": "<id returned by submit_verdict>" }
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

## Hermes Agent

Register the stdio MCP server in Hermes Agent config:

```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /Users/julian_dev/Documents/code/hermes-trader
    timeout: 120
    connect_timeout: 30
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
      AI_BRAIN_PROVIDER: ${AI_BRAIN_PROVIDER}
```

Use the repo skill and instruct Hermes:

```text
Use hermes-trader as the MCP operator. Scan, inspect the best candidates with
read-only tools, submit your own verdict with submit_verdict, then execute the
returned analysisId only if the verdict is LONG/SHORT/CLOSE. Keep risk gates
non-bypassable and never place orders outside hermes-trader tools.
```

## Claude Code

Claude Code can connect to the same stdio MCP server. Use an MCP config JSON file
or the CLI's `--mcp-config` option.

Example `hermes-trader.mcp.json`:

```json
{
  "mcpServers": {
    "hermes-trader": {
      "command": "python3",
      "args": [
        "/Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py"
      ],
      "cwd": "/Users/julian_dev/Documents/code/hermes-trader",
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
claude --mcp-config /Users/julian_dev/Documents/code/hermes-trader/hermes-trader.mcp.json
```

Operator prompt:

```text
You are the hermes-trader brain. Use only hermes-trader MCP tools for market
state and actions. For entries or discretionary closes, submit your own verdict
with submit_verdict, then call execute on the returned analysisId. Never bypass
the risk gates or call exchange APIs directly.
```

## Codex CLI

Codex can be used in either mode:

- Provider mode: set `AI_BRAIN_PROVIDER=codex_cli`.
- MCP operator mode: run Codex in a workspace where the MCP client is configured
  to expose `hermes-trader`.

For provider mode smoke test:

```bash
.venv/bin/python -c 'from hermes_trader.agents.ai_brain import CodexCliBrain; print(CodexCliBrain().complete("Return final-line JSON.", "Return PASS for BTC with numeric fields."))'
```

For MCP operator mode, use the same `submit_verdict -> execute` contract as
Claude Code.

## OpenClaw / OpenClaw-Compatible MCP Clients

If the client supports stdio MCP servers, use the same server definition:

```json
{
  "name": "hermes-trader",
  "command": "python3",
  "args": [
    "/Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py"
  ],
  "cwd": "/Users/julian_dev/Documents/code/hermes-trader",
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
Use hermes-trader MCP tools only. Read market/account context with read-only
tools. When you decide, call submit_verdict. Then call execute with the returned
analysisId. Do not call raw exchange tools or create another executor path.
```

## Research routing: MCP sampling

The `research` tool runs the deep multi-timeframe analysis and returns a verdict.
By default it uses the configured `ai_brain` provider (OpenRouter unless changed).
When the connected harness advertises the MCP `sampling` capability at
`initialize`, `research` instead routes the verdict completion back through the
harness's own model via `sampling/createMessage`, so the harness driving the bot
is also the brain that researches, with no OpenRouter call. If the host cannot
sample, or sampling returns nothing, it falls back to the configured provider so
a verdict is never lost.

- No config needed: it auto-detects the client's `sampling` capability.
- Force off with `HERMES_MCP_DISABLE_SAMPLING=1` (research then uses `ai_brain`).
- `submit_verdict` is unaffected: the harness authored that verdict itself.
- Tests: `tests/test_mcp_sampling.py` (transport correlation, brain parse, capability gate, fallback).

## Verification

Safe focused tests:

```bash
.venv/bin/python -m pytest \
  tests/test_ai_brain.py \
  tests/test_cleanup.py::test_mcp_submit_verdict_records_and_executes_agent_analysis \
  tests/test_cleanup.py::test_mcp_submit_close_routes_to_executor_close \
  tests/test_cleanup.py::test_mcp_close_position_delegates_to_executor \
  tests/test_cleanup.py::test_mcp_config_allows_ai_brain_provider_update
```

MCP wiring audit:

```bash
python3 skills/hermes-trader-agent/scripts/audit_mcp_server.py
```

Codex provider smoke:

```bash
.venv/bin/python -c 'from hermes_trader.agents.ai_brain import CodexCliBrain; from hermes_trader.agents.research import parse_verdict; t=CodexCliBrain().complete("Return final-line JSON.", "Return PASS for BTC with confidence 0.0, side null, entryPx 100, stopPx 0, tpPx 0."); print(t); print(parse_verdict(t, "BTC", {"mid": 100.0}))'
```
