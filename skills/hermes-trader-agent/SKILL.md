---
name: hermes-trader-agent
category: autonomous-trading-agent
description: Multi-market autonomous trading agent with pre-AI TA filter, risk gates, and MCP integration. Scans 230+ HL markets (crypto, equity, commodity perps) and only calls AI on CONFIRMED signals.
tags: [hermes-agent, trading, hyperliquid, mcp, openrouter, autonomous]
homepage: https://github.com/Julian-dev28/hermes-trader
---

# Hermes-Trader Agent

Autonomous multi-market trading agent built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).

## Architecture

Four-layer pipeline designed to minimize AI token costs:

1. **Scan** — Fetch all mids, evaluate 5 triggers per market (pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength)
2. **TA Filter** — Multi-TF technical analysis (1h/4h/1d EMA, RSI, ATR, ADX, volume) — zero AI cost
3. **AI Research** — Only on CONFIRMED signals (score ≥65), fetches deep candle data + news
4. **Execution** — Kelly-sized orders, EIP-712 signing, auto SL/TP brackets

## Heartbeat Daemon

Runs as standalone `node scripts/agent-heartbeat.mjs` (NOT inside Next.js — serverless-unsafe):

```bash
# Every 3 minutes:
node scripts/agent-heartbeat.mjs
```

Uses drift-corrected `setTimeout` for precise timing. Configurable via env vars:
- `AGENT_HEARTBEAT_INTERVAL_MS` (default 180000)
- `AGENT_MIN_SCORE` (default 80)
- `AGENT_MAX_AI_PER_CYCLE` (default 2)

## MCP Integration

Provides tools to Hermes Agent:

```yaml
# In Hermes config.yaml
mcp_servers:
  hermes-trader:
    command: node
    args: [/path/to/hermes-trader/scripts/hermes-mcp-server.mjs]
    timeout: 60
```

Tools: `scan`, `research`, `execute`, `state`, `config`

## Persistent Memory

Two files (gitignored):
- `.agent-config.json` — mode (OFF/LIVE), risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `.trader-session-log.jsonl` — append-only cycle summaries

## Risk Gates (10 independent, no short-circuiting)

All gates evaluated independently, results collected even if one blocks:
1. confidence — min AI confidence threshold
2. maxConcurrent — max open positions
3. perTradeNotionalCap — notional per trade
4. dailyLossKillSwitch — max daily loss
5. marketLiquidityFloor — min 24h volume
6. coinAllowlist/Blocklist
7. cooldown — time between same-market trades
8. oppositeDirectionGuard — no counter-trend entries
9. correlationCap — exposure correlation
10. equityRiskCap — max total exposure %

## User Rules

- **NO simulated trading** — real orders only (OFF or LIVE)
- **Cmd+K** — emergency kill switch: mode OFF + cancel orders
- **TA filter** — cheap statistical pass before AI, cuts token cost 80%

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| `@/` imports fail in standalone scripts | Use relative paths from project root |
| `setInterval` in Next.js | Use standalone daemon with `setTimeout` |
| Cross-route imports in App Router | Extract to `lib/` modules |
| TA filter blocks all signals | Lower TA threshold or check candle count |
| Heartbeat rate limited on scan | `/api/agent/scan` has 30s debounce |
| Session stuck on old cwd | Terminal cwd was old path — set workdir |

## Cost Optimization

| Setting | Default | Impact |
|---------|---------|--------|
| Scan interval | 180s | Fewer cycles = fewer AI calls |
| Min score | 80 | Filters false triggers |
| Max AI/cycle | 2 | Caps worst-case |
| TA threshold | 65 | Statistical gate before AI |
| **Result** | | **~80% token savings** |

## Files

```
hermes-trader/
├── lib/agent/
│   ├── ta-filter.ts          ← Pre-AI statistical filter
│   ├── perception.ts         ← Scan triggers
│   ├── research.ts           ← AI analysis pipeline
│   ├── risk-gates.ts         ← 10 compliance gates
│   ├── executor.ts           ← Order placement
│   ├── memory.ts             ← Persistent state
│   ├── config-store.ts       ← Config management
│   └── system-prompt.ts      ← Agent system prompt
├── scripts/
│   ├── agent-heartbeat.mjs   ← Autonomous loop
│   └── hermes-mcp-server.mjs ← MCP server
├── app/
│   ├── page.tsx              ← Trading desk
│   └── agent/desk/page.tsx   ← Full desk view
└── .agent-config.json        ← Runtime config
```
