# MCP Server Configuration

The MCP server is a Python stdio process. It imports `pathiel` directly —
there is no separate HTTP server to keep running.

## It does not go through the dashboard's auth

Worth stating plainly, because the answer is not obvious and the security
consequence is real: **the MCP server calls `pathiel` in-process**. It does not
make HTTP requests to `pathiel.server`, so none of the 2026-09-04 web auth
applies to it — no wallet sign-in, no session cookie, no operator role, no
CSP.

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
scripts/pathiel-mcp
```

That is the public launcher: it reads keys from `~/.pathiel/.env.local`, uses
the public 3x risk profile and mainnet unless `PATHIEL_NETWORK=testnet` is
set. Do not point another client at `scripts/pathiel-mcp-server.py` directly:
that is the maintainer path (mainnet, the tracked 20x config).

## Pathiel Agent config.yaml

```yaml
mcp_servers:
  pathiel:
    command: /absolute/path/to/pathiel/scripts/pathiel-mcp
    args: []
    timeout: 120
```

## Primary Tools

The server lists 30 tools on a plugin install, 26 on the operator's with `PATHIEL_TOOLSET=v2`, or the legacy 60 tools without it (`contracts/mcp_public`).
Every older name still answers when called. The trading-core tools below are the ones you call directly;
the table keeps the operator's legacy names, and the public names are in `docs/overhaul/MCP.md`.

Six stubs were promoted on 2026-09-06 after an audit found the capability
already sat in `pathiel.client`: `get_coin_price`, `get_leverage`,
`get_funding_history`, `get_asset_context`, `get_open_interest` and
`get_predicted_funding`. A stub is worse than a missing tool — an agent reads
"not implemented" as "this data does not exist here" and goes without it.
`get_predicted_funding` returns rates across venues, so it is a basis read as
well as a funding one. (`get_funding_history` was later cut outright on
2026-10-23 when pathiel became a headless oil desk — `get_funding_rate` and
`get_predicted_funding` cover the funding picture a trader needs.)

| Tool | Args | Returns |
|------|------|---------|
| `place_bracket` | `coin, entry_px?, stop_px, tp_px, leverage?, min_r?` | One entry with an exact stop and target, sized by the risk engine |
| `bracket_sweep` | `coin: string` | Cancels a stranded sibling order, reports whether the position is covered by a stop |
| `close_position` | `coin: string` | Delegates to `pathiel.agents.bracket.close_position()` |
| `set_leverage` | `coin: string, leverage: number` | Sets leverage; with an open position it keeps that position's margin type (isolated on HIP-3), flat it uses the market default; a venue refusal is an error |
| `cancel_order` | `asset: number, order_id: number` | Cancels one resting order |
| `state` | none | Full agent state |
| `config` | see SKILL.md | Current or updated config (mode, HIP-3 and coin lists, risk_profile, active_desks) |

## Environment Variables

Set in `.env.local` at the project root:

```bash
HYPERLIQUID_WALLET_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...
# HYPERLIQUID_MASTER_ADDRESS=0x...   # optional, for agent-wallet setups
# PATHIEL_LLM_MODEL=...            # optional: pin the local Claude Code model services/llm uses
```

The web auth vars (`PATHIEL_AUTH_DOMAIN`, `PATHIEL_PUBLIC_DASHBOARD`)
are **not** read by this process. They belong to
`pathiel.server`; see `services/auth/README.md`.

## Testing Tools

In Pathiel Agent, after the MCP server connects:

```
mcp pathiel get_desk_brief { desk: "oil" }
mcp pathiel sweep_brackets { coin: "xyz:BRENTOIL" }
mcp pathiel get_status
```
