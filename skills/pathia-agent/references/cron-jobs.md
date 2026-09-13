# Cron Jobs

How pathiel is wired into Pathia Agent's cron scheduler
(`~/.pathiel/cron/jobs.json`, managed by `pathiel cron`).

## Hourly status report

A `no_agent` cron job that runs the `pathiel-status.sh` wrapper and
delivers its stdout verbatim — zero LLM cost, read-only (no orders, no writes).
The wrapper does a read-only Hyperliquid query for live equity, using the
public wallet address from `.env.local` — no private key involved.

- **Job id:** `8a82eaa567fe` — "Pathiel Trader Hourly Report"
- **Schedule:** every 60m
- **Script:** `~/.pathiel/scripts/pathiel-status.sh` — a wrapper that runs
  `status.py` (cached + live snapshot) followed by `feed.py --since 60m` (the
  last hour's activity). Cron `script` paths resolve under `~/.pathiel/scripts/`,
  so the wrapper must live there. It calls the skill's scripts by absolute path:

  ```bash
  #!/usr/bin/env bash
  set -uo pipefail
  REPO=/Users/julian_dev/Documents/code/pathiel
  python3 "$REPO/skills/pathia-agent/scripts/status.py"
  echo; echo "--- activity (last 60m) ---"
  python3 "$REPO/skills/pathia-agent/scripts/feed.py" --since 60m
  ```

It ships **paused** (`enabled: false`). Enable it when ready:

```bash
pathiel cron list --all          # confirm the job
pathiel cron resume 8a82eaa567fe # start the hourly report
pathiel cron pause  8a82eaa567fe # stop it again
```

### Recreating it from scratch

If the job is lost, recreate the wrapper (above) then:

```bash
pathiel cron create "every 60m" "Hourly pathiel status snapshot" \
  --name "Pathiel Trader Hourly Report" --deliver local
# then set it to a no_agent script job:
pathiel cron edit <new-id> --script pathiel-status.sh --no-agent
```

## Removed: "pathiel hourly scan" (job `afe033fc6731`)

Deleted. It invoked the long-removed TypeScript codebase (`npx next dev`,
`node scripts/trade-engine.mjs`) and overlapped `trading_loop.py`, which already
scans continuously every `PATHIEL_SCAN_INTERVAL` seconds (default 60s). A separate
hourly cron scan is redundant — the continuous loop is the scan path.

If a *scheduled* (rather than continuous) trade cycle is ever wanted, the loop
would need a one-shot mode first; do not resurrect the old job.
