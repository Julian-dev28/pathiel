# MCP Server Configuration

The MCP server is a Python stdio process. It imports `pathia` directly —
there is no separate HTTP server to keep running.

## Starting the MCP Server

```bash
python scripts/pathia-mcp-server.py
```

It auto-loads `.env.local` from the project root, so credentials must be set
there (see Environment Variables below).

## Pathia Agent config.yaml

```yaml
mcp_servers:
  pathia:
    command: python
    args:
      - /absolute/path/to/pathia/scripts/pathia-mcp-server.py
    cwd: /absolute/path/to/pathia   # so .env.local resolves
    timeout: 120
```

## Primary Tools

The server exposes 99 tools (52 implemented + 47 honest `not_implemented` stubs
for Hyperliquid SDK calls not yet wired). The 7 trading-core tools below are the
ones you call directly.

| Tool | Args | Returns |
|------|------|---------|
| `scan` | `minScore: number` (0-100), `maxMarkets?: number` | Triggered candidates |
| `research` | `coin: string` | AI analysis verdict from the configured brain provider |
| `submit_verdict` | verdict payload | Store an agent-authored verdict and return `analysisId` |
| `execute` | `analysisId: string` | Trade result |
| `close_position` | `coin: string` | Delegates to `executor.close_position_market()` |
| `state` | none | Full agent state |
| `config` | see SKILL.md | Current or updated config, including `ai_brain` |

## Environment Variables

Set in `.env.local` at the project root:

```bash
HYPERLIQUID_WALLET_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...
# HYPERLIQUID_MASTER_ADDRESS=0x...   # optional, for agent-wallet setups
OPENROUTER_API_KEY=sk-or-...
# AI_BRAIN_PROVIDER=openrouter   # openrouter | claude_cli | codex_cli
# AI_BRAIN_TIMEOUT_S=120
# CLAUDE_CLI_COMMAND=claude
# CODEX_CLI_COMMAND=codex
```

## Testing Tools

In Pathia Agent, after the MCP server connects:

```
mcp pathia scan { minScore: 80 }
mcp pathia research { coin: "BTC" }
mcp pathia state
```
