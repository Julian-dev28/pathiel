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

1. **Scan** — Fetch all mids, evaluate 5 triggers per market (pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength). Spot pairs (@ prefix) excluded to avoid noise spikes.
2. **TA Filter** — Multi-TF technical analysis (1h/4h/1d EMA, RSI, ATR, ADX, volume) — zero AI cost. Only CONFIRMED signals (score >= 45) proceed. WEAK (30-44) and REJECTED (< 30) dropped.
3. **AI Research** — Deep AI analysis on CONFIRMED candidates. Max 3 per cycle. News fetch DISABLED.
4. **Execution** — Kelly-sized orders, EIP-712 signing, auto SL/TP brackets

## Cron Job Management

The agent runs via cron jobs (managed by the `cronjob` tool), not a standalone daemon.

Two cron jobs:
- **Hourly scan**: runs scan -> TA filter -> research -> risk gates -> execute
- **Hourly report**: summarizes state, positions, PnL

Both can be paused/resumed via `cronjob` tool.

## MCP Integration

Provides tools to Hermes Agent:

```yaml
# In your Hermes Agent config.yaml
mcp_servers:
  hermes-trader:
    command: python
    args: [/path/to/hermes-trader/scripts/hermes-mcp-server.py]
    cwd: /path/to/hermes-trader  # ← important: sets working directory
    timeout: 120
```

Tools: `scan`, `research`, `execute`, `state`, `config`

## Persistent Memory

Files (all gitignored):
- `.agent-config.json` — mode (OFF/LIVE), risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `.trader-session-log.jsonl` — append-only cycle summaries

## Unified Accounts Support

On HL unified accounts, the agent wallet signs orders (API key) but the master account holds funds. **Equity = perp + spot combined.** The `fetch_account_state` resolves the user via `HYPERLIQUID_MASTER_ADDRESS` (preferred) or falls back to `HYPERLIQUID_WALLET_ADDRESS`.

**Info endpoints use `type` not `action`.** The old `action` field returns 422.

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
- **TA filter** — cheap statistical pass before AI, cuts token cost 80%
- **FULL AUTONOMY** — never ask permission for trade decisions
- **Real money only** — no simulation or dry-run
- **Token cost aware** — flat market with 0 CONFIRMED = $0 spent = correct

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| MCP hangs when dev server is down | Check `curl localhost:3000/api/agent/config` first. Start server if down. |
| Dev server startup takes 20s+ | Don't assume ready after `npx next dev &`. Check with curl. |
| Equity shows $4 instead of ~$75 | Must sum perp + spot, not use perp alone |
| @ coin noise in scan results | Spot pairs (@ prefix) filtered in perception.ts |
| Research fails with "perception not found" | Must send full perception object inline, not just perceptionId |
| `.next` cache causes phantom TS errors | `rm -rf .next` after code changes |
| Research returns zero equity verdicts | Verify equity via `/api/hl/portfolio` separately |
| Scan returns 0 triggers | Markets may be quiet. Lower `minScore` to 20 for broader scan. |
| Session stuck on old cwd | Set workdir to project root |

## Config Tuning

**Micro-account (< $10)**: conf >= 0.50, $2-3/trade, 1 concurrent, 30min cooldown, 15% notional
**Conservative (< $100)**: conf >= 0.85, $20/trade, 2 concurrent, 60min cooldown, 8% notional
**Aggressive (default)**: conf >= 0.75, $25/trade, 5 concurrent, 30min cooldown, 15% notional

**All-PASS diagnostic**: If 3+ consecutive scans produce only PASS with low confidence, lower threshold or review system prompt for over-cautiousness.

## Files

```
hermes-trader/
├── lib/agent/
│   ├── ta-filter.ts          ← Pre-AI statistical filter
│   ├── perception.ts         ← Scan triggers (filters @ spot pairs)
│   ├── research.ts           ← AI analysis pipeline (equity = perp + spot)
│   ├── risk-gates.ts         ← 10 compliance gates
│   ├── executor.ts           ← Order placement
│   ├── memory.ts             ← Persistent state
│   ├── config-store.ts       ← Config management
│   └── system-prompt.ts      ← Agent system prompt
├── lib/
│   ├── hl-client.ts          ← Shared HL API helpers
│   ├── hl-universe.ts        ← Market universe (perps only)
│   └── types.ts              ← Shared types
├── scripts/
│   ├── hermes-mcp-server.mjs ← MCP server (stdio)
│   ├── backtest.mjs          ← Backtesting
│   └── analyze-journal.mjs   ← Trade journal analysis
├── app/
│   ├── page.tsx              ← Dashboard
│   └── api/
│       ├── agent/
│       │   ├── scan/route.ts      ← Scan + TA filter
│       │   ├── research/[coin]/route.ts
│       │   ├── execute/route.ts
│       │   ├── state/route.ts
│       │   └── config/route.ts
│       └── hl/                  ← Hyperliquid API proxies
│           ├── account, candles, close-position, place-order, ...
└── skills/hermes-trader-agent/ ← This skill
```
