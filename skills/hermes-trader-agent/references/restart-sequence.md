# Restart Sequence

Use `scripts/restart.sh` — it handles stop (SIGTERM → SIGKILL fallback),
verify, background start with logs, and a status readout. It manages the
trading loop AND the FastAPI server (which serves the dashboard at
`http://localhost:8000`). The MCP server is intentionally NOT managed —
it's a transient stdio process respawned by Hermes Agent on each tool
call.

```bash
cd /Users/julian_dev/Documents/code/hermes-trader
scripts/restart.sh              # restart loop + server
scripts/restart.sh loop         # loop only
scripts/restart.sh server       # server only
scripts/restart.sh stop         # stop both
scripts/restart.sh status       # show PIDs
```

Logs: `logs/trading_loop.log`, `logs/server.log`.

## When to restart

- After code changes to `trading_loop.py`, anything under `hermes_trader/`,
  or `.env.local`. Most config changes (`.agent-config.json`) are
  hot-reloaded per-trade and don't need a restart, but the asset-class
  flags (`enable_hip3`, `enable_crypto`) need a restart because the
  universe is fetched once at startup.
- After an HL API timeout cluster — usually self-heals via
  `queried_dexes` preservation, but if DSL trackers are stuck in a weird
  state a restart re-reads `.dsl-state.json` clean.

## Resetting today's daily PnL baseline

If a deposit / transfer / cross-day boundary leaves the on-disk baseline
out of sync, the contribution-aware tracker normally self-corrects within
one heartbeat. To force a reset:

```bash
scripts/restart.sh stop
python3 -c "
import json
m = json.load(open('.agent-memory.json'))
m['startOfDayEquity'] = m.get('equity', 0)
m['dailyPnl'] = 0
json.dump(m, open('.agent-memory.json','w'), indent=2)
"
scripts/restart.sh restart
```

To set the baseline to a specific point (e.g. last UTC midnight from
HL's portfolio history rather than current equity), see the snippet
that called HL's `/info portfolio` endpoint in the session log.

## Stale MCP server

If an MCP tool runs old code after a fix:

```bash
pkill -f hermes-mcp-server.py
```

The next Hermes tool call respawns it fresh from `~/.hermes/config.yaml`.

## Verifying clean state

```bash
scripts/restart.sh status
ps ax | rg "(scripts/trading_loop.py|hermes-mcp-server.py|hermes_trader.server)"
```

`status` may show the process group that owns the loop (`screen`, shell,
`python`, and `caffeinate`). The important invariant is exactly one
`python ... scripts/trading_loop.py` process. If the log shows overlapping scan
cadences, an older orphan loop is probably still alive; stop the older process
before trusting fills, cooldowns, or PnL attribution. `scripts/restart.sh` has a
`ps` fallback for environments where `pgrep -f` is unreliable.
