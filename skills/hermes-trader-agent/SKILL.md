---
name: hermes-trader-agent
category: autonomous-trading-agent
description: Multi-market autonomous trading agent with pre-AI TA filter, risk gates, and MCP integration. Scans 230+ HL markets (crypto, equity, commodity perps) and only calls AI on CONFIRMED signals.
tags: [hermes-agent, trading, hyperliquid, mcp, openrouter, autonomous]
homepage: https://github.com/Julian-dev28/hermes-trader
---

# Hermes-Trader Agent

Autonomous multi-market trading agent built on [Hermes Agent](https://github.com/NousResearch/hermes-agent). Pure Python.

## Architecture

Four-layer pipeline designed to minimize AI token costs:

1. **Scan** — Fetch all mids, evaluate 6 triggers per market (pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength, momentumBurst). Spot pairs (@ prefix) excluded to avoid noise spikes. A fired momentumBurst (large fast move) bypasses the composite-score gate.
2. **TA Filter** — Multi-TF technical analysis (1h/4h/1d EMA, RSI, ATR, ADX, volume) — zero AI cost. Only CONFIRMED signals (score >= 45) proceed. WEAK (30-44) and REJECTED (< 30) dropped.
3. **AI Research** — Deep AI analysis on CONFIRMED candidates. Max 3 per cycle. News fetch DISABLED.
4. **Execution** — Kelly-sized orders, EIP-712 signing, auto SL/TP brackets + DSL dynamic exits.

## Running

The trading loop is a standalone Python process:

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute loop
```

Or drive the steps individually through the MCP server (see below).

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

The MCP server exposes 100 tools. Primary trading tools: `scan`, `research`,
`execute`, `state`, `config`. See `references/mcp-config.md`.

## Persistent Memory

Files (all gitignored):
- `.agent-config.json` — mode (OFF/LIVE), risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `~/.hermes-trader-session-log.jsonl` — append-only cycle summaries

## Unified Accounts Support

On HL unified accounts, the agent wallet signs orders (API key) but the master account holds funds. **Equity = perp + spot combined.** `fetch_account_state` resolves the user via `HYPERLIQUID_MASTER_ADDRESS` (preferred) or falls back to `HYPERLIQUID_WALLET_ADDRESS` — the shared `resolve_user_address()` helper.

**Info endpoints use `type` not `action`.** The old `action` field returns 422.

## Risk Gates (11 independent, no short-circuiting)

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
11. newsBlackout — stand down on binary news risk

## User Rules

- **NO simulated trading** — real orders only (OFF or LIVE)
- **TA filter** — cheap statistical pass before AI, cuts token cost 80%
- **FULL AUTONOMY** — never ask permission for trade decisions
- **Real money only** — no simulation or dry-run
- **Token cost aware** — flat market with 0 CONFIRMED = $0 spent = correct

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Equity shows $4 instead of ~$75 | Must sum perp + spot, not use perp alone |
| @ coin noise in scan results | Spot pairs (@ prefix) filtered in `perception.py` |
| Research fails with "perception not found" | Must send full perception object inline, not just perceptionId |
| Research returns zero-equity verdicts | Verify equity via the `/api/hl/portfolio` route or the `state` MCP tool |
| Scan returns 0 triggers | Markets may be quiet. Lower `minScore` to 20 for a broader scan. |
| Session stuck on old cwd | Set the MCP `cwd` to the project root |

## Config Tuning

**Micro-account (< $10)**: conf >= 0.50, $2-3/trade, 1 concurrent, 30min cooldown, 15% notional
**Conservative (< $100)**: conf >= 0.85, $20/trade, 2 concurrent, 60min cooldown, 8% notional
**Aggressive (default)**: conf >= 0.75, $25/trade, 5 concurrent, 30min cooldown, 15% notional

**All-PASS diagnostic**: If 3+ consecutive scans produce only PASS with low confidence, lower the threshold or review the system prompt for over-cautiousness.

## Files

```
hermes-trader/
├── hermes_agent/
│   ├── agents/
│   │   ├── ta_filter.py        ← Pre-AI statistical filter
│   │   ├── perception.py       ← Scan triggers (filters @ spot pairs)
│   │   ├── research.py         ← AI analysis pipeline
│   │   ├── risk_gates.py       ← 11 compliance gates
│   │   ├── executor.py         ← Kelly sizing + order execution
│   │   ├── dsl_exit.py         ← Two-phase trailing-stop engine
│   │   ├── memory.py           ← Persistent state
│   │   ├── config_store.py     ← Config management
│   │   ├── system_prompt.py    ← Agent system prompt
│   │   ├── hyperfeed.py        ← Discovery API (leaderboard, market data)
│   │   └── whale_index.py      ← Smart-money / OI-anomaly signals
│   ├── client/
│   │   ├── hl_client.py        ← HL REST API client
│   │   ├── exchange.py         ← Order placement / leverage
│   │   ├── ws_client.py        ← WebSocket mids
│   │   ├── universe.py         ← Market universe loader
│   │   └── cache.py · lock.py · parallel.py · daemon.py
│   ├── indicators/
│   │   ├── math.py             ← EMA, SMA, ATR, RSI, ADX
│   │   └── triggers.py         ← Scan triggers + composite scoring
│   ├── models/types.py         ← Candle (shared OHLCV type)
│   └── server.py               ← FastAPI server (22 routes)
├── scripts/
│   ├── hermes-mcp-server.py    ← MCP server (stdio, 100 tools)
│   └── trading_loop.py         ← Continuous trading loop
├── tests/                      ← pytest suite (offline / online / live e2e)
└── skills/hermes-trader-agent/ ← This skill
```
