# Cron Jobs

How pathia is wired into Pathia Agent's cron scheduler
(`~/.pathia/cron/jobs.json`, managed by `pathia cron`).

## Hourly status report

A `no_agent` cron job that runs the `pathia-status.sh` wrapper and
delivers its stdout verbatim — zero LLM cost, read-only (no orders, no writes).
The wrapper does a read-only Hyperliquid query for live equity, using the
public wallet address from `.env.local` — no private key involved.

- **Job id:** `8a82eaa567fe` — "Pathia Trader Hourly Report"
- **Schedule:** every 60m
- **Script:** `~/.pathia/scripts/pathia-status.sh` — a wrapper that runs
  `status.py` (cached + live snapshot) followed by `feed.py --since 60m` (the
  last hour's activity). Cron `script` paths resolve under `~/.pathia/scripts/`,
  so the wrapper must live there. It calls the skill's scripts by absolute path:

  ```bash
  #!/usr/bin/env bash
  set -uo pipefail
  REPO=/Users/julian_dev/Documents/code/pathia
  python3 "$REPO/skills/pathia-agent/scripts/status.py"
  echo; echo "--- activity (last 60m) ---"
  python3 "$REPO/skills/pathia-agent/scripts/feed.py" --since 60m
  ```

It ships **paused** (`enabled: false`). Enable it when ready:

```bash
pathia cron list --all          # confirm the job
pathia cron resume 8a82eaa567fe # start the hourly report
pathia cron pause  8a82eaa567fe # stop it again
```

### Recreating it from scratch

If the job is lost, recreate the wrapper (above) then:

```bash
pathia cron create "every 60m" "Hourly pathia status snapshot" \
  --name "Pathia Trader Hourly Report" --deliver local
# then set it to a no_agent script job:
pathia cron edit <new-id> --script pathia-status.sh --no-agent
```

## Removed: "pathia hourly scan" (job `afe033fc6731`)

Deleted. It invoked the long-removed TypeScript codebase (`npx next dev`,
`node scripts/trade-engine.mjs`) and overlapped `trading_loop.py`, which already
scans continuously every `PATHIA_SCAN_INTERVAL` seconds (default 60s). A separate
hourly cron scan is redundant — the continuous loop is the scan path.

If a *scheduled* (rather than continuous) trade cycle is ever wanted, the loop
would need a one-shot mode first; do not resurrect the old job.
