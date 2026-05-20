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
   RSI, ATR, ADX, volume) at zero AI cost. `trading_loop.py` runs it as a gate:
   only CONFIRMED perceptions (score ≥ 45) reach AI research; WEAK / REJECTED are
   dropped. A perception whose `momentumBurst` trigger fired bypasses the gate.
3. **AI Research** — deep AI analysis via OpenRouter on triggered candidates.
4. **Execution** — equity-sized orders (1% × leverage), SDK order signing, an
   ATR-based backup stop-loss, and DSL dynamic exits.

## Running

The trading loop is a standalone process (no Hermes command wraps it yet):

```bash
python scripts/trading_loop.py        # continuous scan -> research -> execute
# background: nohup python scripts/trading_loop.py > logs/trading_loop.log 2>&1 &
```

Recommended production start (background daemon):
```bash
python3 scripts/trading_loop.py --env prod --daemon
```

Cadence is `HERMES_SCAN_INTERVAL` (default 60s). Or drive the steps individually
through the MCP `scan` / `research` / `execute` tools.

### Restarting the Trading Loop + MCP Server

To stop and restart cleanly (especially after config or MCP changes):

1. Stop the trading loop first:
   ```bash
   pkill -f trading_loop.py || true
   ```

2. Explicitly stop the MCP server (separate stdio process):
   ```bash
   pkill -f hermes-mcp-server.py || true
   sleep 2
   ```

3. Verify both are gone:
   ```bash
   ps aux | grep -E "(trading_loop|hermes-mcp-server)" | grep -v grep || echo "All cleared"
   ```

4. Restart the trading loop (background daemon):
   ```bash
   python3 scripts/trading_loop.py --env prod --daemon
   ```

The MCP server is intentionally transient. It respawns automatically on the next Hermes tool call because it is registered in `~/.hermes/config.yaml`. No persistent MCP daemon is required.

This two-kill + verify sequence prevents stale MCP state from interfering with the fresh trading loop.

## MCP Integration

The server (`scripts/hermes-mcp-server.py`, stdio, 100 tools) is registered in

Trading Mode rules
See `references/trading-mode.md` for the explicit "execute first, report results only" contract when the user is actively monitoring/trading. This file also contains the exact command sequence the team has standardized.
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
equityRiskCap, newsBlackout. Gate config keys are read tolerantly —
`snake_case` or `camelCase` both resolve.

## Trade Sizing

Per-trade size = `equity_fraction_per_trade × perp_equity × leverage`, keyed off
**total perp equity** (not free margin). Each trade commits a fixed fraction, so
N trades scales the account fully in — `0.10` means ~10 trades = fully deployed.
Both keys live in `.agent-config.json`; defaults if absent: `0.01` and `5`.
Bounded by `maxConcurrent` (simultaneous positions), `max_total_notional_pct`
(combined-notional ceiling), and `maxTradeNotionalUsd` (per-trade ceiling) —
keep those above the intended deployment or trades get gate-blocked.

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

## Market Coverage & Scan Scope

The default `scan` / `scan_once` only evaluates the **top-N markets by 24h volume** (default `maxMarkets=50`).  
Low-volume or newly listed names (e.g. DYM) are therefore frequently missed even though they exist in the full perp universe (confirmed via `get_perp_markets` or `market_list_instruments`).

To force coverage of a specific coin:
- Increase `maxMarkets` (expensive) or
- Call `research` / analysis directly on the symbol after confirming it appears in `get_perp_markets`.

Pitfall: assuming “we scanned everything” when the log simply says “50 markets”. Always check via the MCP market-list tools when the user mentions an asset that was not reported.

## Common Pitfalls

| Issue | Fix |
|-------|-----|
| Equity reads far too low | Unified accounts: `perp_equity` already includes spot USDC — do not add `spot_usdc` again. |
| `@` coins as noise in scan results | Spot pairs are filtered in `perception.py`; if they appear, the filter regressed. |
| "perception not found" on research | Send the full perception object inline, not just a `perceptionId`. |
| Order rejected on price/size | Hyperliquid `szDecimals` ≠ `pxDecimals` — see `references/hyperliquid-gotchas.md`. |
| MCP tool runs stale code after a fix | The server is a separate process — `pkill -f hermes-mcp-server.py` to respawn. |
| Scan returns 0 triggers | Often correct (quiet market). Lower minScore only to widen deliberately. |
| status.py / .agent-memory.json shows equity: 0 while account is funded | The loop heartbeat is not running. trading_loop.py must call _sync_account_state() every cycle. |
| Scanner fires triggers but zero executes (Signal vs Action Gap) | See "Signal vs Action Gap" subsection and references/signal-vs-action-gap.md. Triggers appear (volume/range/trend), research returns PASS + low confidence, no execute. Caused by minAiConfidence 0.5 or strict TA gate (CONFIRMED >= 45). Scanner itself is healthy; second-stage filter is the knob. |

## Bundled Scripts

Runnable tooling shipped with this skill (`scripts/`) — all read-only, no orders
or writes. `audit_mcp_server.py` and `feed.py` are stdlib-only; `status.py` also
does a read-only Hyperliquid query for live equity:

- `scripts/audit_mcp_server.py` — validates MCP tool wiring (`TOOLS` /
  `tool_handlers` / `_STUB_RESPONSES` consistency). Run before and after editing
  the server; parses via `ast` (no import, no execution) and exits non-zero on
  drift.
- `scripts/status.py` — plain-text snapshot showing BOTH the cached state from
  `.agent-memory.json` and LIVE state pulled directly from Hyperliquid. The
  live read uses the repo's `hl_client` and `.env.local` for credentials, so
  drift between the two surfaces a broken loop heartbeat immediately. Falls
  back to cache-only when no wallet env var is set.
- `scripts/feed.py` — human-readable activity feed from the session log. The
  trading loop appends every scan / heartbeat / TA filter / research /
  execute / error event; `feed.py` renders them with timestamps and symbols.
  Examples:
  ```bash
  python3 scripts/feed.py                     # last 20 events
  python3 scripts/feed.py -n 50               # last 50
  python3 scripts/feed.py --since 30m         # last 30 minutes
  python3 scripts/feed.py --follow            # tail -f forever
  python3 scripts/feed.py --filter execute,error  # only those types
  ```
  Event types emitted by the loop: `loop_start`, `loop_stop`, `loop_heartbeat`
  (per-cycle equity/positions sync), `scan` (trigger count + coins), `ta_skip`
  (TA filter dropped a perception), `research` (verdict + confidence),
  `execute` (order outcome), `error`. `status.py` also prints TA verdict counts
  (CONFIRMED / WEAK / REJECTED + avg composite score over the last 30) so you
  immediately see if the statistical gate is over-filtering early signals.

## Visibility / "What is the bot doing right now?"

MCP tools are request/response — there is no streaming progress mid-call.
The right way to watch the trading system is:

1. **Tail the feed:** `python3 scripts/feed.py --follow` in a terminal.
2. **One-off snapshot:** `python3 scripts/status.py` for cached + live state.
3. **Hourly auto-report:** Hermes cron job `8a82eaa567fe` (`hermes-trader-status.sh`)
   runs `status.py` + `feed.py --since 60m` every hour and delivers the
   combined report to the originating chat (no LLM cost — `no_agent=true`).
   - Pause:  `hermes cron pause 8a82eaa567fe`
   - Resume: `hermes cron resume 8a82eaa567fe`
   - Run now: `hermes cron run 8a82eaa567fe`

## Loop Heartbeat (live equity sync)

`trading_loop.py` calls `_sync_account_state()` at the top of every cycle:

1. Resolves the user address via `resolve_user_address()` (master else wallet).
2. Calls `fetch_account_state(user)` — the same path the executor uses.
3. Calls `memory.track_daily_pnl(equity)` + `memory.update_open_positions(...)`.
4. Calls `memory.flush()` so `.agent-memory.json` always reflects current equity.
5. Appends a `loop_heartbeat` event with equity/available/daily_pnl/positions.

This is why `status.py` shows fresh equity even when no trade has executed —
before this fix, equity stayed at 0 between trades because nothing in the loop
ever refreshed it. If `status.py` shows `cached equity: $0` while LIVE is
non-zero, the heartbeat is broken or the loop hasn't completed one cycle yet.

## Scheduled Operation

An hourly Hermes cron job (`no_agent`, zero LLM cost) runs `status.py` and
delivers the snapshot. It ships paused — `hermes cron resume 8a82eaa567fe` to
start it. See `references/cron-jobs.md`.

## References

- `references/mcp-config.md` — MCP server config and tool list.
- `references/mcp-server.md` — server structure, adding tools, the audit invariant.
- `references/hyperliquid-gotchas.md` — order-placement gotchas (decimals, tick size, $10 min, singletons).
- `references/cron-jobs.md` — Hermes cron wiring for the hourly status report.
- `references/signal-vs-action-gap.md` — diagnosis of the recurring \"scanner fires, trader stays silent\" pattern + all live tuning performed 2026-05-19.
- `references/restart-sequence.md` — canonical short `pkill + restart` command the user repeatedly uses (maintains this exact two-line ritual).
- `references/trading-mode.md` — explicit execute-first reporting contract when the user is in active trading mode.