# MCP Server Configuration

The MCP server is a Python stdio process. It imports `pathia` directly —
there is no separate HTTP server to keep running.

## It does not go through the dashboard's auth

Worth stating plainly, because the answer is not obvious and the security
consequence is real: **the MCP server calls `pathia` in-process**. It does not
make HTTP requests to `pathia.server`, so none of the 2026-09-04 web auth
applies to it — no wallet sign-in, no session cookie, no operator role, no
`PATHIA_OPERATOR_TOKEN`, no CSP.

What gates it instead is the filesystem. It reads `.env.local` for the trading
key and `.agent-config.json` for the risk caps, so **anyone who can run this
process can already sign orders with your wallet**. Treat the ability to launch
it as equivalent to holding the key, because it is.

That is the correct design for a local operator tool and would be the wrong one
for anything reachable over a network. If the MCP server is ever exposed
remotely, it needs its own authentication — reusing the dashboard's session gate
would not help, because this process never touches those routes.

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

The server exposes 86 tools, every one of them implemented. The 7 trading-core tools below
are the ones you call directly.

Six of those stubs were promoted on 2026-09-06 after an audit found the
capability already sat in `pathia.client`: `get_coin_price`, `get_leverage`,
`get_funding_history`, `get_asset_context`, `get_open_interest` and
`get_predicted_funding`. A stub is worse than a missing tool — an agent reads
"not implemented" as "this data does not exist here" and goes without it.
`get_predicted_funding` returns rates across venues, so it is a basis read as
well as a funding one.

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

The web auth vars (`PATHIA_AUTH_DOMAIN`, `PATHIA_PUBLIC_DASHBOARD`,
`PATHIA_OPERATOR_TOKEN`) are **not** read by this process. They belong to
`pathia.server`; see `services/auth/README.md`.

## Testing Tools

In Pathia Agent, after the MCP server connects:

```
mcp pathia scan { minScore: 80 }
mcp pathia research { coin: "BTC" }
mcp pathia state
```
