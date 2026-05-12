# MCP Server Configuration

## Starting the MCP Server

```bash
node scripts/hermes-mcp-server.mjs
```

## Hermes Agent config.yaml

```yaml
mcp_servers:
  hermes-trader:
    command: node
    args:
      - /absolute/path/to/hermes-trader/scripts/hermes-mcp-server.mjs
    timeout: 60
```

## Available Tools

| Tool | Args | Returns |
|------|------|---------|
| `scan` | `minScore: number` (0-100) | Perceptions with TA signals |
| `research` | `coin: string, perceptionId?: string` | AI analysis verdict |
| `execute` | `analysisId: string` | Trade result |
| `state` | none | Full agent state |
| `config` | see SKILL.md | Current or updated config |

## Environment Variables

```bash
SCANNER_API_URL=http://localhost:3000  # Next.js server
```

## Testing Tools

In Hermes Agent, after MCP connects:

```
mcp hermes-trader scan { minScore: 80 }
mcp hermes-trader research { coin: "BTC" }
mcp hermes-trader state
```
