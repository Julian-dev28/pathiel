# MCP Server Configuration

The MCP server is a Python stdio process. It imports `hermes_trader` directly —
there is no separate HTTP server to keep running.

## Starting the MCP Server

```bash
python scripts/hermes-mcp-server.py
```

It auto-loads `.env.local` from the project root, so credentials must be set
there (see Environment Variables below).

## Hermes Agent config.yaml

```yaml
mcp_servers:
  hermes-trader:
    command: python
    args:
      - /absolute/path/to/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /absolute/path/to/hermes-trader   # so .env.local resolves
    timeout: 120
```

## Primary Tools

The server exposes 100 tools (52 implemented + 48 honest `not_implemented` stubs
for Hyperliquid SDK calls not yet wired). The 5 trading-core tools below are the
ones you call directly.

| Tool | Args | Returns |
|------|------|---------|
| `scan` | `minScore: number` (0-100), `maxMarkets?: number` | Triggered candidates |
| `research` | `coin: string` | AI analysis verdict |
| `execute` | `analysisId: string` | Trade result |
| `state` | none | Full agent state |
| `config` | see SKILL.md | Current or updated config |

## Environment Variables

Set in `.env.local` at the project root:

```bash
HYPERLIQUID_WALLET_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...
# HYPERLIQUID_MASTER_ADDRESS=0x...   # optional, for agent-wallet setups
OPENROUTER_API_KEY=sk-or-...
```

## Testing Tools

In Hermes Agent, after the MCP server connects:

```
mcp hermes-trader scan { minScore: 80 }
mcp hermes-trader research { coin: "BTC" }
mcp hermes-trader state
```
