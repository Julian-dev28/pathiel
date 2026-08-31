# Deploy hermes-trader to Fly.io

A single image, five Fly processes, one persistent volume. This is
`scripts/restart.sh`'s process list translated to Fly process groups — the
container never runs `restart.sh` itself; Fly launches each process directly
and supervises it:

| Process   | Command                                                | What it is |
|-----------|---------------------------------------------------------|------------|
| `web`     | `python3 -m hermes_trader.server`                        | Dashboard + JSON API + SSE feed + `/metrics`, public |
| `loop`    | `python3 scripts/trading_loop.py`                        | Autonomous scan → research → execute → DSL monitor, private |
| `sched`   | `python3 scripts/scheduler.py`                           | Cron replacement — fires `capital-flows` (6h), `autonomous-cycle` (daily), `trends-price` (30min), `trends-recorders` (6h) |
| `rotator` | `python3 scripts/log_rotate.py --daemon`                 | Bounds `logs/` growth — `sched`'s fired jobs write real log files regardless of how `sched` itself was launched |

All four run from one image. `web`, `loop`, and `sched` share the
`/data` volume; `rotator` doesn't touch `/data` (see "Runtime state" below).

Cost: roughly **$5–8/month** for five `shared-cpu-1x` VMs (three at 512MB, two
at 256MB) + a 1GB volume.

## Prerequisites

```bash
brew install flyctl
flyctl auth signup   # or `flyctl auth login` if you already have an account
docker info           # Docker must be running — flyctl builds the image locally by default
```

You'll also need, before the first deploy:
- An [OpenRouter](https://openrouter.ai/keys) account (the default AI brain provider)
- A funded Hyperliquid wallet + its private key
- (Optional) [Brave Search](https://api.search.brave.com/) for news in research

## Secrets

**Never** put these in `fly.toml`, `k8s/configmap.yaml`, or the image — set
them with `flyctl secrets set` / a `kubectl create secret` (see
`k8s/secret.example.yaml`). This lists names and purpose only, never values.

| Secret | Required? | Unlocks |
|--------|-----------|---------|
| `OPENROUTER_API_KEY` | **Required** | The default AI brain (`AI_BRAIN_PROVIDER=openrouter`, baked into the image) — research and the /trends narrative pass |
| `HYPERLIQUID_WALLET_ADDRESS` | **Required** | The trading account |
| `HYPERLIQUID_PRIVATE_KEY` | **Required** | Signs orders — the money key |
| `HERMES_OPERATOR_TOKEN` | **Required** | Gates every mutating dashboard endpoint (`?token=` / `X-Operator-Token`). Missing = the operator console 503s closed, which is safe but means you can't start/stop/configure the bot from the dashboard |
| `BRAVE_API_KEY` | Optional | News search inside `hermes_trader/agents/research.py`. Unset = that source is skipped, not an error |
| `UW_API_KEY` | Optional | Unusual Whales options-flow client (`hermes_trader/client/uw_client.py`). RESEARCH ONLY since the book cull — `uw_flow_xs` no longer exists; the client is used by `research/alpha_swarm/hypotheses/W-UW2_signal_battery.py` and `W-UW3_gex.py` to re-run those verdicts. Unset = those scripts report NO UW_API_KEY and exit. Nothing live reads it |
| `HYDROMANCER_API_KEY` | Optional | Hydromancer market-data provider (`hermes_trader/data_providers/hydromancer.py`). Unset = provider raises `HydromancerError`, caught by its caller |
| `HYPERLIQUID_MASTER_ADDRESS` | Optional | Agent-wallet setup — the funding account behind the trading wallet |
| `HYPERLIQUID_MASTER_PRIVATE_KEY` | Optional | Only used by `scripts/treasury.py` (manual transfers between master/agent wallets) — never read by any of the five deployed processes. Set it only if you plan to `flyctl ssh console` and run treasury commands by hand |

```bash
flyctl secrets set \
  OPENROUTER_API_KEY="sk-or-..." \
  HYPERLIQUID_WALLET_ADDRESS="0x..." \
  HYPERLIQUID_PRIVATE_KEY="0x..." \
  HERMES_OPERATOR_TOKEN="$(openssl rand -hex 16)"

# Optional
flyctl secrets set BRAVE_API_KEY="BSA..."
flyctl secrets set UW_API_KEY="..."
flyctl secrets set HYDROMANCER_API_KEY="..."
flyctl secrets set HYPERLIQUID_MASTER_ADDRESS="0x..." HYPERLIQUID_MASTER_PRIVATE_KEY="0x..."
```

```bash
flyctl secrets list                                # names only, never values
flyctl secrets set HERMES_OPERATOR_TOKEN="$(openssl rand -hex 16)"   # rotate
flyctl secrets unset BRAVE_API_KEY                 # remove
```

Setting or unsetting a secret triggers a rolling redeploy of every process.

## AI provider — read this before you deploy

The image bakes `AI_BRAIN_PROVIDER=openrouter` (see `Dockerfile`). That's a
plain HTTPS call authenticated by `OPENROUTER_API_KEY` and works out of the
box in any container.

The alternative provider, `claude_cli` (what CLAUDE.md's "route LLM calls
through local Claude Code" rule refers to, and what is configured on the
operator's Mac today per `.env.local`), shells out to a **locally
authenticated Claude Code CLI session** — an interactive desktop
subscription, not a credential you can bake into an image or hand to
`flyctl secrets set`. It does not run in a stateless cloud container.

Two things now make that impossible to get wrong silently:

- `scripts/preflight_secrets.py --deploy` **fails** on a CLI provider. Run it
  before every deploy; it refuses rather than shipping an image whose brain
  cannot start.
- `/api/health/system` reports `ai_brain` as failing when the selected brain
  cannot run, so a misconfigured deploy shows up as a 503 instead of as a
  system that quietly never produces a verdict.

Fixed 2026-08-29: `services/trend_engine/ai.py` used to shell out to the
`claude` binary **unconditionally**, ignoring `AI_BRAIN_PROVIDER`, so the
/trends narrative pass failed every call in a container and returned an empty
string — indistinguishable from a model with nothing to say. It now routes
through the shared brain like everything else, so the image default
(`openrouter`) works.

## Runtime state

Everything the app persists lives on one Fly volume mounted at `/data`,
shared by `web`, `loop`, and `sched`:

| Path | What |
|------|------|
| `/data/.dsl-state.json` | Position exit-tracker ratchets (`HERMES_DSL_STATE_FILE`) |
| `/data/.agent-config.json` | Live mode / config (`HERMES_AGENT_CONFIG_FILE`) — first boot writes `mode: "OFF"` |
| `/data/.agent-memory.json` | Perception/analysis memory (`HERMES_AGENT_MEMORY_FILE`) |
| `/data/session-log.jsonl` | Trade/action session log (`SESSION_LOG_PATH`) |
| `/data/.positions-snapshot.json` | Cross-process position snapshot the loop writes and the dashboard reads (`HERMES_POSITIONS_SNAPSHOT_FILE`) — also what `loop`'s k8s `startupProbe` checks |
| `/data/.state/` | Everything routed through `HERMES_STATE_DIR`: shadow ledgers (`.state/shadow_ledger/<book>.jsonl`), the cross-book claims registry, the capital-flow record the drawdown is computed from (`.state/capital_flows.jsonl`), per-strategy throttle timers, `services/trend_engine`'s cached lanes, and `scripts/scheduler.py`'s own job-run bookkeeping (via a symlink — see the Dockerfile) |

`logs/` is deliberately **not** on the volume — see the Dockerfile and
`k8s/statefulset.yaml`'s `rotator` container comment for why.

## One-time setup

```bash
# From the repo root, launch the app (skips automatic deploy so we can wire
# secrets first)
flyctl launch --no-deploy --copy-config

# When prompted:
#   - App name: pick something unique (e.g. hermes-trader-julian)
#   - Region: pick one near you (iad / ord / fra / nrt …)
#   - Postgres / Redis: NO
#   - Deploy now: NO

# Create the persistent volume (one per region; size = 1GB is plenty)
flyctl volumes create hermes_data --size 1 --region iad

# Wire secrets — see "Secrets" above
flyctl secrets set OPENROUTER_API_KEY="..." HYPERLIQUID_WALLET_ADDRESS="..." \
  HYPERLIQUID_PRIVATE_KEY="..." HERMES_OPERATOR_TOKEN="$(openssl rand -hex 16)"

# First deploy
flyctl deploy
```

After a successful deploy you'll get a URL like `https://hermes-trader-julian.fly.dev`.
The dashboard is at `/`, the operator console is at `/operator?token=<HERMES_OPERATOR_TOKEN>`.

## Verifying the image before you ship it

The image was built and exercised on 2026-08-29 — these are the exact commands,
not an aspiration:

```bash
docker build -t hermes-test .

# Every module a running process imports must be IN the image. This is the
# check that would have caught services/ being absent from a 3-month-stale
# Dockerfile.
docker run --rm hermes-test python3 -c "
import hermes_trader.server, hermes_trader.dashboard, hermes_trader.metrics
from hermes_trader.agents import executor, perception, risk_gates, ta_filter
import services.trend_engine.run"

# The server boots and every page renders inside the container.
docker run --rm -e HERMES_OPERATOR_TOKEN=t hermes-test python3 -c "
from fastapi.testclient import TestClient
from hermes_trader.server import app
c = TestClient(app)
print({p: c.get(p).status_code for p in ('/','/activity','/news','/analytics','/trends')})"
```

Expect all five pages to return 200, and `/api/health/system` to return **503**
with `['ai_brain', 'loop']` failing — a container with no credentials and no
running loop SHOULD report unhealthy. That 503 is the deep healthcheck working,
not a build problem.

## Verifying the deploy is healthy

```bash
flyctl status                          # all 4 process groups should show "started"
flyctl checks list                     # both http_service checks should be "passing"

# Shallow: is the web process serving?
curl -s https://<your-app>.fly.dev/api/health

# Deep: is the SYSTEM working? 503 lists exactly what is broken. This is the
# one to alert on — /api/health answers 200 even with a dead trading loop.
curl -s https://<your-app>.fly.dev/api/health/system | jq

# Confirm every process actually came up (not just the machine)
flyctl logs -i web     | tail -20      # "Starting Hermes server on port 8000"
flyctl logs -i loop    | tail -20      # first scan cycle log lines
flyctl logs -i sched   | tail -20      # "[scheduler] up — N jobs, 60s tick"
flyctl logs -i rotator | tail -20      # first disk-guard pass

# Confirm the loop is actually writing state to the volume (not just running)
flyctl ssh console -s -C "cat /data/.positions-snapshot.json"
```

If `web` is up but every other process group shows 0 machines, you likely
deployed before `flyctl secrets set` or before `flyctl volumes create` —
check `flyctl logs -i loop` for the crash reason (missing `HYPERLIQUID_PRIVATE_KEY`
fails loudly at import time).

## Tailing logs

```bash
flyctl logs                          # combined, all five processes
flyctl logs -i web
flyctl logs -i loop
flyctl logs -i sched
flyctl logs -i rotator
```

## Pausing the bot without redeploying

```bash
flyctl machines stop --process-group loop                # halt trading loop
flyctl machines start --process-group loop                # resume
```

`web` and `sched` keep running either way — the dashboard stays
readable, `/predictions` and `/trends` keep refreshing, without the loop
placing trades. You can also flip mode to `OFF` in the operator console — the
loop process stays alive but stops opening new positions; existing positions
still get DSL-managed.

## Deploying new code (upgrade)

```bash
git push          # main branch; doesn't trigger deploy
flyctl deploy     # builds image + rolls all five processes
```

Fly does a rolling restart for `web` for free (zero-downtime). `loop`,
`sched` and `rotator` briefly drop and restart; the loop's next
scan tick (~60s later) resumes, and `sched`'s catch-up logic
(`scripts/scheduler.py:is_due`) means a job due during the restart window
fires on the next tick instead of being silently skipped.

## Rollback

```bash
flyctl releases list                              # find the last-known-good version
flyctl deploy --image <previous-image-ref>         # from a specific past release
# or, simplest:
flyctl releases rollback <version>
```

Rollback does not touch `/data` — state (positions, DSL ratchets, shadow
ledgers) is untouched by a code rollback, since it's all on the volume, not
in the image.

## SSH into the running machine

```bash
flyctl ssh console -s
ls /data                              # see the persisted state files
tail -f /data/session-log.jsonl       # live activity from inside the box
```

## Backing up volume state

```bash
flyctl ssh sftp shell
get /data/.dsl-state.json
get /data/.agent-memory.json
get /data/session-log.jsonl
```

Or set up a periodic snapshot:

```bash
flyctl volumes snapshots create hermes_data
flyctl volumes snapshots list hermes_data
```

## Switching to a custom domain

```bash
flyctl certs add hermes.yourdomain.com
flyctl certs show hermes.yourdomain.com    # follow the DNS-CNAME instructions
```

## Common gotchas

- **Four of the five processes need the volume mount.** `web`, `loop`,
  and `sched` all read or write `/data` (directly, or through
  `HERMES_STATE_DIR`); `rotator` deliberately does not. `[[mounts]] processes`
  in `fly.toml` reflects this exactly — don't add `rotator` to it and don't
  remove any of the other four.
- **`HERMES_BIND` is baked to `0.0.0.0` in the image.** The code default is
  `127.0.0.1` (a security fix for running bare on a Mac). If you ever run
  this image with a custom entrypoint that unsets image `ENV`, the health
  check will time out with no obvious error — the process is up, just
  unreachable. See the Dockerfile's "Networking" comment.
- **Mode defaults to OFF.** First boot writes `/data/.agent-config.json` with
  `mode: "OFF"`. Open the operator console and flip to `LIVE` once you've
  verified the dashboard.
- **Time on Fly is UTC.** The dashboard converts to your browser's local zone;
  the session log timestamps are epoch ms (timezone-agnostic).
- **The `~/.hermes-trader.pid` file** that the FastAPI start/stop endpoints
  reference is meaningless in a container — Fly handles process supervision.
  Those endpoints will report stopped state inside Fly; ignore them and use
  `flyctl machines stop --process-group loop` instead.
- **`AI_BRAIN_PROVIDER=claude_cli` will not work as deployed.** See "AI
  provider" above before changing it.

## Local build + smoke test (before you deploy)

```bash
docker build -t hermes-trader:local .

# Prove the image actually contains every package it imports:
docker run --rm hermes-trader:local python3 -c \
  "import hermes_trader.server, hermes_trader.dashboard, scripts.trading_loop, \
   services.trend_engine.run; print('ok')"

# Prove the server actually binds reachably and serves /api/health:
docker run --rm -d --name hermes-smoke -p 8000:8000 \
  -e HYPERLIQUID_WALLET_ADDRESS=0xtest -e HYPERLIQUID_PRIVATE_KEY=0x$(printf '1%.0s' {1..64}) \
  -e OPENROUTER_API_KEY=test -e HERMES_OPERATOR_TOKEN=test \
  hermes-trader:local
sleep 3 && curl -sf http://localhost:8000/api/health && echo OK
docker rm -f hermes-smoke
```

## Full env var reference

Most of these are optional tuning knobs with safe defaults and don't need to
be set for a working deploy — listed here so every env var the code reads is
documented somewhere, per this repo's deploy-config gate test
(`tests/test_deploy_config.py`).

### Secrets — see "Secrets" above

`OPENROUTER_API_KEY`, `HYPERLIQUID_WALLET_ADDRESS`, `HYPERLIQUID_PRIVATE_KEY`,
`HERMES_OPERATOR_TOKEN`, `BRAVE_API_KEY`, `UW_API_KEY`, `HYDROMANCER_API_KEY`,
`HYPERLIQUID_MASTER_ADDRESS`, `HYPERLIQUID_MASTER_PRIVATE_KEY`

### Baked into the image — see the Dockerfile, don't override casually

`HERMES_BIND` (`0.0.0.0`), `AI_BRAIN_PROVIDER` (`openrouter`),
`HERMES_STATE_DIR`, `HERMES_POSITIONS_SNAPSHOT_FILE`, `SESSION_LOG_PATH`,
`HERMES_DSL_STATE_FILE`, `HERMES_AGENT_CONFIG_FILE`, `HERMES_AGENT_MEMORY_FILE`

### Set in fly.toml `[env]` / k8s ConfigMap

`HERMES_PORT` (`8000`), `HERMES_SCAN_INTERVAL` (`60`)

### Set only on the `web` process (statefulset.yaml `web` container `env:` /
### fly.toml's `env` command prefix on the `web` process)

`HERMES_STATE_READONLY` — never let the dashboard process flush
`.agent-memory.json` over the loop's live data (see `hermes_trader/agents/memory.py`).
`HERMES_HL_RATE_REFILL_PER_SEC` / `HERMES_HL_RATE_CAPACITY` — throttle the
dashboard's HL polling to ~1/4 budget so it yields to the loop's fetches
(defaults 15/300 otherwise; `loop` keeps the full budget, unset).

### Tuning knobs — safe code defaults, override only if you know why

| Var | Default | Purpose |
|-----|---------|---------|
| `HERMES_MAX_MARKETS` | 45 | Total candle-fetch budget per scan |
| `HERMES_MAX_MARKETS_HIP3` | 18 | Of that budget, slots reserved for HIP-3 |
| `HERMES_MAX_MARKETS_MOVERS` | 10 | Slots reserved for movers |
| `HERMES_BATCH_SIZE` | 20 | Markets per parallel batch |
| `HERMES_BATCH_SLEEP` | 0.3 | Seconds between batches |
| `HERMES_SCAN_WORKERS` | (auto) | Parallel scan threads |
| `HERMES_UNIVERSE_SWEEP` | 0 (disabled) | Full-universe sweep toggle |
| `HERMES_UNIVERSE_REFRESH_S` | 1800 | Universe refresh interval |
| `HERMES_HIP3_MOVERS_FLOOR_USD` | 50000 | HIP-3 movers volume floor |
| `HERMES_MOVERS_VOL_FLOOR_USD` | 300000 | Movers volume floor |
| `HERMES_CANDLE_CACHE_TTL_S` | 90 | Candle cache freshness window |
| `HERMES_CANDLE_RETRIES` | 6 | Candle fetch retry count |
| `HERMES_CANDLE_BACKOFF_CAP_S` | 8 | Candle fetch backoff cap |
| `HERMES_META_TTL_S` | 3600 | Exchange metadata cache TTL |
| `HERMES_HL_RATE_CAPACITY` | 300 | HL rate-limit token bucket capacity |
| `HERMES_HL_RATE_REFILL_PER_SEC` | 15 | HL rate-limit refill rate |
| `HERMES_HL_RATE_MAX_WAIT_S` | 30 | Max wait for a rate-limit token |
| `HERMES_STARTUP_GRACE_S` | 12 | Loop startup grace period before first scan |
| `HERMES_META_PREWARM_TIMEOUT_S` | 3 | Meta cache prewarm timeout |
| `HERMES_WATCHDOG_TIMEOUT_S` | 600 | Loop self-heal re-exec timeout on a stuck cycle |
| `HERMES_CYCLE_DEADLINE_S` | 1500 | `autonomous_cycle.py` per-run deadline |
| `HERMES_DASHBOARD_READONLY` | unset | Hard-refuse every mutating dashboard POST regardless of token |
| `HERMES_LOG_DIR` | `<repo>/logs` | Override where `hermes_trader/log_setup.py` writes/rotates logs |
| `HERMES_LOG_MAX_BYTES`, `HERMES_LOG_BACKUP_COUNT`, `HERMES_LOG_DIR_MAX_BYTES`, `HERMES_LOG_ROTATE_INTERVAL_SEC` | see `hermes_trader/log_setup.py` | Log rotation policy |
| `HERMES_DISK_FREE_WARN_MB` / `HERMES_DISK_FREE_CRITICAL_MB` | 2048 / 500 | Disk-guard thresholds `scripts/log_rotate.py --guard` enforces before `restart.sh` starts a process |
| `AI_BRAIN_TIMEOUT_S` | 120 | AI brain call timeout |
| `OPENROUTER_MODEL` | `x-ai/grok-4.3` | Model for the openrouter provider |
| `OPENROUTER_MAX_TOKENS` | 2048 | Response token cap |
| `OPENROUTER_WEB_ENGINE`, `OPENROUTER_WEB_MAX_RESULTS`, `OPENROUTER_WEB_MAX_TOTAL_RESULTS` | see `ai_brain.py` | OpenRouter web-search tuning |
| `CLAUDE_CLI_COMMAND`, `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_MAX_TURNS`, `CLAUDE_CLI_WEB_MAX_TURNS` | see `ai_brain.py` | Only relevant if `AI_BRAIN_PROVIDER=claude_cli` — not viable in this deploy, see "AI provider" |
| `CODEX_CLI_COMMAND` | `codex` | Only relevant for the unused `codex_cli` provider |
| `POLY_SCOUT_MODEL` | `claude-opus-4-8` | Forecaster model (via the AI brain, so effectively an openrouter model in this deploy) |
| `TREND_AI_MODEL`, `TREND_AI_TIMEOUT_S` | `claude-opus-4-8` / 180 | Only relevant to the `/trends` narrative pass, which is not reachable in this deploy — see "AI provider" |
| `HYDROMANCER_TESTNET`, `HYDROMANCER_TIMEOUT_S` | unset / 10 | Hydromancer provider tuning |
| `UW_CACHE_DIR` | unset (disabled) | On-disk cache for Unusual Whales responses — opt-in, off by default |
| `NO_SSL_FIX` | unset | Skip the macOS `certifi` cert-bundle workaround (`hermes_trader/__init__.py`) — irrelevant on Linux, harmless either way |
| `HERMES_MCP_DISABLE_SAMPLING`, `HERMES_MCP_SAMPLING_MAX_TOKENS` | unset / 2048 | `scripts/hermes-mcp-server.py` only — see "Not containerized" below |
| `HERMES_SMOKE_BASE` | `http://127.0.0.1:8000` | `scripts/smoke_trends.py` test target, dev-only |
| `HERMES_V2_LIVE` | unset | `scripts/v2_shadow_loop.py` only — not one of the five managed processes, not run in this deploy |

## Not containerized

- **`scripts/hermes-mcp-server.py`** — a transient stdio process Hermes
  Agent (the IDE tool) respawns on each tool call, not a long-lived service.
  `scripts/restart.sh` deliberately doesn't manage it either. There's nothing
  to deploy: it only makes sense next to an interactive Claude Code session,
  the same one the `claude_cli` AI provider depends on.
- **The `claude_cli` AI provider** — see "AI provider" above. The `claude`
  binary itself could be installed into the image, but the credential it
  needs is an interactively-authenticated desktop session, not a bakeable
  secret, so installing the binary alone would not make the feature work.
  Left out rather than shipped half-working.
- **`scripts/v2_shadow_loop.py`** and other one-off scripts under `scripts/`
  not named in `scripts/restart.sh` — these are run by hand (`flyctl ssh
  console`) when needed, not continuously supervised processes.
