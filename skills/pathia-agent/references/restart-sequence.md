# Restart Sequence

Use `scripts/restart.sh` — it handles stop (SIGTERM → SIGKILL fallback),
verify, background start with logs, and a status readout. It manages the
trading loop AND the FastAPI server (which serves the dashboard at
`http://localhost:8000`). The MCP server is intentionally NOT managed —
it's a transient stdio process respawned by Pathia Agent on each tool
call.

```bash
cd <repo root>
scripts/restart.sh              # restart loop + server
scripts/restart.sh loop         # loop only
scripts/restart.sh server       # server only
scripts/restart.sh sched        # scheduler only
scripts/restart.sh stoploop     # KILL SWITCH — stop the loop, stay stopped
scripts/restart.sh stop         # stop ALL four: loop, server, scheduler, rotator
scripts/restart.sh status       # show PIDs and what is halted
```

Logs: `logs/trading_loop.log`, `logs/server.log`.

## The halt marker — read before wondering why a restart did nothing

`stoploop` and `stop` are kill switches, so they must stay stopped. Each writes
its component into `.state/supervisor_halt.json`, and `supervise_processes.py`
refuses to restart anything listed there. Every explicit start clears its own
marker.

So a loop that will not come up is usually not broken:

```bash
cat "${PATHIA_STATE_DIR:-.}/supervisor_halt.json"   # {"halted": ["loop"]} means deliberate
```

A supervisor that restarted the loop two minutes after the operator stopped it
would have silently deleted the kill switch, which is why this exists.

## After a long stop

`data_logger` writes the funding/OI panel from inside the loop, so that panel
stops growing while the loop is down. `xs_reversal` needs ~7 days of trailing
funding history to judge a coin's market awake and will decline to rank until it
refills — taking no trade rather than trading on partial data. Expect it to sit
out for a while after a pause longer than a week; that is correct, not a fault.

## Restarting after a code change

The server does **not** hot-reload. On 2026-09-04 a three-day-old server process
kept serving pre-auth routes: the sign-in button called `/auth/nonce`, got a
404, and did nothing, while the dashboard still served the house balance
ungated. It looked exactly like broken auth. `restart.sh loop` restarts only the
loop — if you changed anything under `pathia/`, restart the server too.

**Loop vs server restart:** `restart.sh server` restarts ONLY the dashboard — it
does not touch open positions or DSL trackers, so it's safe any time. `restart.sh
loop` restarts the trading loop; it rehydrates DSL trackers from `.dsl-state.json`
(positions survive), but prefer doing it when the book is flat. `start_server` now
launches the dashboard with a hard-throttled HL rate bucket
(`PATHIA_HL_RATE_REFILL_PER_SEC`/`_CAPACITY`, ~¼ budget) so a server restart can't
burst the shared per-IP rate budget the loop relies on.

## When to restart

- After code changes to `trading_loop.py`, anything under `pathia/`,
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
m['peakDailyPnl'] = 0
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
pkill -f pathia-mcp-server.py
```

The next Pathia tool call respawns it fresh from `~/.pathia/config.yaml`.

## Verifying clean state

```bash
scripts/restart.sh status
ps ax | rg "(scripts/trading_loop.py|pathia-mcp-server.py|pathia.server)"
```

`status` may show the process group that owns the loop (`screen`, shell,
`python`, and `caffeinate`). The important invariant is exactly one
`python ... scripts/trading_loop.py` process. If the log shows overlapping scan
cadences, an older orphan loop is probably still alive; stop the older process
before trusting fills, cooldowns, or PnL attribution. `scripts/restart.sh` has a
`ps` fallback for environments where `pgrep -f` is unreliable.

If Codex launches `restart.sh` and the execution wrapper reaps detached
background children, use persistent `screen` sessions:

```bash
screen -dmS pathia-server /bin/zsh -lc 'cd /Users/julian_dev/Documents/code/pathia && PATHIA_HL_RATE_REFILL_PER_SEC=5 PATHIA_HL_RATE_CAPACITY=200 .venv/bin/python -m pathia.server >> logs/server.log 2>&1'
screen -dmS pathia-loop /bin/zsh -lc 'cd /Users/julian_dev/Documents/code/pathia && PATHIA_STARTUP_GRACE_S=0 PATHIA_META_PREWARM_TIMEOUT_S=3 .venv/bin/python scripts/trading_loop.py >> logs/trading_loop.log 2>&1'
screen -ls
curl -s -o /tmp/pathia-dashboard.html -w "%{http_code}\n" http://localhost:8000/
```
