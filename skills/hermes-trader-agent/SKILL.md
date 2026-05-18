---
name: hermes-trader-agent
description: Use when operating, maintaining, or debugging hermes-trader — the standalone autonomous Hyperliquid trading system that Hermes Agent drives through its MCP server. Covers the scan/research/execute pipeline, the 11 risk gates, MCP tool wiring, and Hyperliquid order-placement gotchas.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [trading, hyperliquid, mcp, autonomous, quant]
    related_skills: [hyperliquid-agent-wallets]
    homepage: https://github.com/Julian-dev28/hermes-trader
---

# Hermes-Trader Agent

`hermes-trader` is a **standalone Python trading system** for Hyperliquid perpetual
markets. Hermes Agent operates it through the **MCP server** registered in
`~/.hermes/config.yaml` (`mcp_servers.hermes-trader`) — that MCP boundary is the
integration. The trading engine itself has no Hermes-framework dependency; it is
Hermes-*operated*, not Hermes-*built*.

Repo: `/Users/julian_dev/Documents/code/hermes-trader` — branch `python`, **never merge to `main`**.

## Architecture

A pipeline designed to keep AI token cost proportional to real opportunity:

1. **Scan** — fetch all mids, evaluate 6 triggers per market (pctMoveSpike,
   volumeSpike, breakout, rangeCompression, trendStrength, momentumBurst). Spot
   pairs (`@` prefix) are excluded. A fired `momentumBurst` (large fast move)
   bypasses the composite-score gate so explosive moves are never filtered out.
2. **TA Filter** — `ta_filter.py` does multi-timeframe validation (1h/4h/1d EMA,
   RSI, ATR, ADX, volume) at zero AI cost. **Note:** `analyze_perception()` exists
   but `scripts/trading_loop.py` does not currently call it — the loop researches
   every triggered perception. Wiring the filter in as a gate is a known cost
   improvement, not yet done.
3. **AI Research** — deep AI analysis via OpenRouter on triggered candidates.
4. **Execution** — equity-sized orders (1% × leverage), SDK order signing, an
   ATR-based backup stop-loss, and DSL dynamic exits.

## Running

The trading loop is a standalone process (no Hermes command wraps it yet):

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute
# background: nohup python scripts/trading_loop.py > logs/trading_loop.log 2>&1 &
```

Cadence is `HERMES_SCAN_INTERVAL` (default 60s). Or drive the steps individually
through the MCP `scan` / `research` / `execute` tools.

## MCP Integration

The server (`scripts/hermes-mcp-server.py`, stdio, 100 tools) is registered in
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  hermes-trader:
    command: python3
    args:
      - /Users/julian_dev/Documents/code/hermes-trader/scripts/hermes-mcp-server.py
    cwd: /Users/julian_dev/Documents/code/hermes-trader   # recommended
    timeout: 60
    connect_timeout: 30
    env:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
```

Primary tools: `scan`, `research`, `execute`, `state`, `config`. Adding tools and
the audit invariant: see `references/mcp-server.md`. After editing the server,
restart it: `pkill -f hermes-mcp-server.py` (the next call respawns it fresh).

## State Files

Project state — not Hermes memory (all gitignored):
- `.agent-config.json` — mode (OFF/LIVE), risk caps, thresholds
- `.agent-memory.json` — perceptions, analyses, trades, cooldowns
- `~/.hermes-trader-session-log.jsonl` — append-only cycle summaries

## Risk Gates (11 independent, no short-circuiting)

Every gate is evaluated; results are collected even when one blocks: confidence,
maxConcurrent, perTradeNotionalCap, dailyLossKillSwitch, marketLiquidityFloor,
coinAllowlist/Blocklist, cooldown, oppositeDirectionGuard, correlationCap,
equityRiskCap, newsBlackout.

## Unified Accounts

On a Hyperliquid unified account the agent wallet signs orders while the master
account holds funds; `resolve_user_address()` picks `HYPERLIQUID_MASTER_ADDRESS`
first, else `HYPERLIQUID_WALLET_ADDRESS`. Equity reads come from the master.
For agent-wallet setup and the `approveAgent` flow, see the
`hyperliquid-agent-wallets` skill.

## User Rules

- Real orders only — no simulation or dry-run; mode is `OFF` or `LIVE`.
- Full autonomy — do not ask permission for individual trade decisions.
- Token-cost aware — a flat market with 0 triggers = $0 spent = correct behavior.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Equity reads far too low | Unified accounts: `perp_equity` already includes spot USDC — do not add `spot_usdc` again. |
| `@` coins as noise in scan results | Spot pairs are filtered in `perception.py`; if they appear, the filter regressed. |
| "perception not found" on research | Send the full perception object inline, not just a `perceptionId`. |
| Order rejected on price/size | Hyperliquid `szDecimals` ≠ `pxDecimals` — see `references/hyperliquid-gotchas.md`. |
| MCP tool runs stale code after a fix | The server is a separate process — `pkill -f hermes-mcp-server.py` to respawn. |
| Scan returns 0 triggers | Often correct (quiet market). Lower `minScore` only to widen deliberately. |

## Bundled Scripts

Runnable tooling shipped with this skill (`scripts/`, stdlib-only, no side effects):

- `scripts/audit_mcp_server.py` — validates MCP tool wiring (`TOOLS` /
  `tool_handlers` / `_STUB_RESPONSES` consistency). Run before and after editing
  the server; parses via `ast` (no import, no execution) and exits non-zero on
  drift.
- `scripts/status.py` — plain-text snapshot: mode, equity, trades, open
  positions, and whether the loop / MCP server are running. Local-only, safe to
  run any time — suitable for a cron status report.

## Scheduled Operation

An hourly Hermes cron job (`no_agent`, zero LLM cost) runs `status.py` and
delivers the snapshot. It ships paused — `hermes cron resume 8a82eaa567fe` to
start it. See `references/cron-jobs.md`.

## References

- `references/mcp-config.md` — MCP server config and tool list.
- `references/mcp-server.md` — server structure, adding tools, the audit invariant.
- `references/hyperliquid-gotchas.md` — order-placement gotchas (decimals, tick size, $10 min, singletons).
- `references/cron-jobs.md` — Hermes cron wiring for the hourly status report.
