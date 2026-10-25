# Deploy pathiel (Docker and Kubernetes)

pathiel is a headless live desk on Hyperliquid. Claude is the trader, through
the MCP server (`scripts/pathiel-mcp-server.py`) on the operator's own
machine; the web server (`python -m pathiel.server`) is the Desk and Account
pages, the JSON API, `/metrics` and the desk-grading loop. Only the web half
is containerised. There is no trading loop, no scheduler and no AI brain
process to deploy (the autonomous engine was retired 2026-10-23, the brain
module 2026-10-25), and Fly.io was dropped as a target on 2026-10-25: the one
container target is Kubernetes (`k8s/`), with plain `docker run` for a local
smoke test.

| Where | What runs | How it starts |
|---|---|---|
| operator's machine | the server, the log rotator, the MCP server, Claude's desk ticks | `scripts/restart.sh`; Claude Code with `.mcp.json`; `/loop 15m /desk-tick oil` |
| container (`web`) | `python3 -m pathiel.server` on `:8000` | k8s StatefulSet, or `docker run` |
| container (`rotator`) | `python3 scripts/log_rotate.py --daemon` | second container in the same pod |

The container can show the Desk pages, serve the API and grade expired
theses. It cannot write a new thesis: `desk_refresh` goes through
`services/llm`, which shells out to a locally signed-in Claude Code CLI, and
no such session exists in a pod. In a container the thesis writer degrades to
the previous thesis or a flat placeholder; the operator refreshes theses from
their own machine.

## Prerequisites

```bash
docker info                     # Docker must be running
brew install kind helm          # only for the Kubernetes path (kubectl already present)
```

A funded Hyperliquid account with an agent wallet (the key that signs orders,
never the master key). No LLM account: nothing in the image calls a hosted
model.

## Secrets

**Never** put these in `k8s/configmap.yaml`, the `Dockerfile`, or any tracked
file. Create the k8s Secret imperatively (see `k8s/secret.example.yaml`) or
pass `-e` flags to `docker run` from your shell environment.

| Secret | Required? | Unlocks |
|---|---|---|
| `HYPERLIQUID_WALLET_ADDRESS` | **Required** | The agent wallet that signs orders |
| `HYPERLIQUID_PRIVATE_KEY` | **Required** | That wallet's key. The money key |
| `HYPERLIQUID_MASTER_ADDRESS` | **Required** | The account the agent wallet acts for |
| `EIA_API_KEY`, `FRED_API_KEY`, `CONGRESS_API_KEY` | Optional | `services/intel` data sources. A missing key skips that source and the desk brief's `stale` list says so |
| `HYPERLIQUID_MASTER_PRIVATE_KEY` | **Never in a container** | `scripts/treasury.py` only, on the operator's machine. `scripts/preflight_secrets.py --deploy` fails if it is set |

Run the preflight before any deploy. It prints names, never values, and exits
non-zero when secrets handling is wrong:

```bash
.venv/bin/python scripts/preflight_secrets.py --deploy
```

`docs/SECRETS.md` is the full inventory and rotation runbook.

## Runtime state

Everything the server persists lives under one volume mounted at `/data`:

| Path | What |
|---|---|
| `/data/.state/` | `PATHIEL_STATE_DIR`: desk journals and theses (`strategies/<desk>/`), the intel cache, candle cache, the accounts registry and vault, backup receipts |
| `/data/.agent-config.json` | `PATHIEL_AGENT_CONFIG_FILE`: mode, HIP-3 and coin lists, risk profile, active desks |
| `/data/session-log.jsonl` | `SESSION_LOG_PATH`: the action log the Account page tails |

`logs/` is deliberately **not** on the volume: log history needs no
backup or rollback semantics, and keeping it off the volume keeps the one
singleton-sensitive claim small. `rotator` bounds it inside the container's
own writable layer.

The `web` container runs with `PATHIEL_STATE_READONLY=1` and a throttled
Hyperliquid token bucket (`PATHIEL_HL_RATE_REFILL_PER_SEC=2`,
`PATHIEL_HL_RATE_CAPACITY=60`), the same env `scripts/restart.sh` gives the
server on the operator's machine, so the dashboard's polling yields venue
budget to the trading calls.

## Build and verify the image

```bash
docker build -t pathiel:local .

# Every package a running process imports must be IN the image. This is the
# check that would have caught services/ being absent from a stale Dockerfile
# (tests/test_deploy_config.py runs the same check offline on every commit).
docker run --rm pathiel:local python3 -c "
import pathiel.server, pathiel.dashboard, pathiel.metrics
from pathiel.agents import bracket, hyperfeed, config_store
import services.strategies.api, services.intel.api, services.accounts.api, services.risk.api
print('ok')"

# The server boots and both pages render inside the container.
docker run --rm pathiel:local python3 -c "
from fastapi.testclient import TestClient
from pathiel.server import app
c = TestClient(app)
print({p: c.get(p).status_code for p in ('/', '/desk/oil', '/account')})"
```

Expect every page to return 200.

## Run it with Docker

```bash
docker volume create pathiel_data
docker run -d --name pathiel -p 8000:8000 \
  -v pathiel_data:/data \
  -e HYPERLIQUID_WALLET_ADDRESS -e HYPERLIQUID_PRIVATE_KEY -e HYPERLIQUID_MASTER_ADDRESS \
  -e PATHIEL_AUTH_DOMAIN=localhost:8000 \
  -e PATHIEL_STATE_READONLY=1 -e PATHIEL_HL_RATE_REFILL_PER_SEC=2 -e PATHIEL_HL_RATE_CAPACITY=60 \
  pathiel:local
curl -sf http://localhost:8000/api/health && echo OK
curl -s  http://localhost:8000/api/health/system | python3 -m json.tool
```

The bare `-e NAME` form copies the variable from your shell without putting
the value on the command line. `PATHIEL_AUTH_DOMAIN` must match the host the
browser is on or every wallet sign-in is rejected for a domain mismatch.

## Run it on Kubernetes

`k8s/README.md` is the step-by-step for a local kind cluster with Prometheus
and Grafana. The short form:

```bash
kind create cluster --name pathiel
kind load docker-image pathiel:local --name pathiel
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic pathiel-secrets -n pathiel \
  --from-literal=HYPERLIQUID_WALLET_ADDRESS="$HYPERLIQUID_WALLET_ADDRESS" \
  --from-literal=HYPERLIQUID_PRIVATE_KEY="$HYPERLIQUID_PRIVATE_KEY" \
  --from-literal=HYPERLIQUID_MASTER_ADDRESS="$HYPERLIQUID_MASTER_ADDRESS" \
kubectl apply -k k8s/                      # namespace, configmap, service, statefulset
kubectl -n pathiel rollout status statefulset/pathiel
kubectl -n pathiel port-forward svc/pathiel 8000:8000
```

What the manifests encode, and what `tests/test_deploy_config.py` and
`tests/test_health_endpoints.py` pin:

- one `StatefulSet` replica (the server is a singleton over its state files),
  two containers, `web` and `rotator`, one per process in `scripts/restart.sh`
- only `web` mounts the `/data` claim
- liveness probes `/api/health` (shallow, always 200 while the process can
  serve); readiness probes `/api/health/system` (deep, 503 when the disk is
  under the critical floor or the state directory is not writable)
- `k8s/monitoring/` adds the ServiceMonitor and the PrometheusRule after
  kube-prometheus-stack is installed: `kubectl apply -k k8s/monitoring/`
- the configmap sets `PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR=1`, so the first
  wallet to sign in does NOT become the operator. Grant the role yourself,
  once, from inside the pod:

  ```bash
  kubectl -n pathiel exec pathiel-0 -c web -- python3 scripts/grant_operator.py 0xYourWallet
  ```

  Off the operator's own machine every write route is closed to anyone
  but a signed-in operator; there is no shared token (removed 2026-10-25).

## Health

```bash
curl -s http://localhost:8000/api/health            # liveness: the process serves
curl -s http://localhost:8000/api/health/system      # readiness: disk + state dir, plus hl_budget
curl -s http://localhost:8000/metrics | grep pathiel_
```

`/api/health/system` lists `failing` by name and carries `hl_budget`, the
server's Hyperliquid weight granted and refused per request type: the trace
for "the site is slow". The `loop`, `feed` and `ai_brain` checks it used to
carry are gone (2026-10-25); each watched a process that no longer exists
and had turned into a permanent false 503.

Alerts (`k8s/prometheusrule.yaml`): LIVE mode with an account under the
structural minimum, disk under 2GB, `logs/` over 800MB, no verified state
backup in 36h, max drawdown past 25%.

## Upgrade and rollback

```bash
docker build -t pathiel:local .
kind load docker-image pathiel:local --name pathiel
kubectl -n pathiel rollout restart statefulset/pathiel
kubectl -n pathiel rollout status statefulset/pathiel
kubectl -n pathiel rollout undo statefulset/pathiel     # back to the previous image
```

Tag images with the commit (`pathiel:<sha>`) for anything beyond a local
cluster so `rollout undo` has a real target. A rollback never touches
`/data`: theses, journals and the account registry live on the volume, not in
the image.

## Backing up state

```bash
# on the operator's machine (the live desk)
.venv/bin/python scripts/backup_state.py
# from a pod
kubectl -n pathiel cp pathiel-0:/data/.state ./pathiel-state-backup -c web
```

`scripts/backup_state.py` writes a verified archive and a receipt;
`pathiel_backup_age_seconds` on `/metrics` reads the receipt and the
`PathielBackupStale` alert fires past 36h without one.

## Common gotchas

- **`PATHIEL_BIND` is baked to `0.0.0.0` in the image.** The code default is
  `127.0.0.1` (a security fix for running bare on a Mac). A custom entrypoint
  that drops the image `ENV` makes the process unreachable from outside the
  container with no error in its log.
- **Mode defaults to OFF.** A first boot with no `/data/.agent-config.json`
  writes `mode: "OFF"`. Flip it with the MCP `config` tool or a hand edit.
- **No theses get written in a pod.** See the top of this file. A pod shows
  the last thesis the operator wrote; `desk_refresh` from the operator's
  machine is what moves it.
- **Wallet sign-in fails silently on a domain mismatch.** Set
  `PATHIEL_AUTH_DOMAIN` (configmap, or `-e`) to the exact host in the browser.
- **The `~/.pathiel.pid` start/stop routes are meaningless in a container.**
  k8s owns process supervision; use `kubectl rollout`.

## Env var reference

Set by the image (do not override casually): `PATHIEL_BIND` (`0.0.0.0`),
`PATHIEL_STATE_DIR` (`/data/.state`), `PATHIEL_AGENT_CONFIG_FILE`,
`SESSION_LOG_PATH`.

Set per deploy (configmap, or `-e`): `PATHIEL_PORT` (`8000`),
`PATHIEL_AUTH_DOMAIN`.

Set on the `web` container only: `PATHIEL_STATE_READONLY`,
`PATHIEL_HL_RATE_REFILL_PER_SEC`, `PATHIEL_HL_RATE_CAPACITY`.

Tuning knobs with code defaults, by module. Override only with a reason:

| Area | Vars | Read by |
|---|---|---|
| Hyperliquid budget | `PATHIEL_HL_RATE_CAPACITY`, `PATHIEL_HL_RATE_REFILL_PER_SEC`, `PATHIEL_HL_RATE_MAX_WAIT_S`, `PATHIEL_HL_BACKGROUND_RESERVE` | `pathiel/client/rate_limit.py` |
| Account reads | `PATHIEL_ACCOUNT_FRESH_S`, `PATHIEL_ACCOUNT_MAX_STALE_S`, `PATHIEL_ACCOUNT_SWEEP_S`, `PATHIEL_ACCOUNT_WAIT_S`, `PATHIEL_RISK_EQUITY_TTL_S`, `PATHIEL_DASHBOARD_LIVE_EQUITY` | `pathiel/client/account_snapshot.py`, `services/risk` |
| Candles and meta | `PATHIEL_CANDLE_CACHE_TTL_S`, `PATHIEL_CANDLE_RETRIES`, `PATHIEL_CANDLE_BACKOFF_CAP_S`, `PATHIEL_META_TTL_S`, `PATHIEL_META_PREWARM`, `PATHIEL_MAX_MARKETS` | `pathiel/client/hl_client.py` |
| Desks | `PATHIEL_DESK_CARD_MAX_AGE_S`, `PATHIEL_DESK_BRIEF_MAX_AGE_S`, `PATHIEL_DESK_CANDLE_MAX_AGE_S`, `PATHIEL_DESK_INTEL_MAX_AGE_S`, `PATHIEL_DESK_ACCOUNT_TTL_S`, `PATHIEL_DESK_BACKGROUND_REFRESH`, `PATHIEL_DESK_WARMUP` | `services/strategies/facade.py` |
| Grading loop | `PATHIEL_GRADE_LOOP`, `PATHIEL_GRADE_INTERVAL_MIN`, `PATHIEL_GRADE_INITIAL_DELAY_S` | `pathiel/server.py` |
| LLM (operator machine only) | `PATHIEL_LLM_MODEL`, `PATHIEL_LLM_CONCURRENCY`, `PATHIEL_THESIS_EFFORT` | `services/llm`, `services/thesis` |
| Auth | `PATHIEL_AUTH_DOMAIN`, `PATHIEL_AUTH_URI`, `PATHIEL_AUTH_CHAIN_ID`, `PATHIEL_AUTH_DB`, `PATHIEL_AUTH_NONCE_SECRET`, `PATHIEL_AUTH_STATELESS_NONCE`, `PATHIEL_AUTH_STATELESS_SESSION`, `PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR`, `PATHIEL_PUBLIC_DASHBOARD`, `PATHIEL_DISABLE_LOCAL_MODE`, `PATHIEL_INSECURE_COOKIES`, `PATHIEL_DATABASE_URL` | `services/auth` |
| Vault | `PATHIEL_VAULT_PASSPHRASE`, `PATHIEL_VAULT_FAST_KDF` | `services/accounts` |
| HTTP rate limit | `PATHIEL_RATE_LIMIT_PER_MIN`, `PATHIEL_RATE_LIMIT_BURST`, `PATHIEL_DASHBOARD_READONLY` | `pathiel/server.py` |
| HTTP hardening (2026-10-25 review) | `PATHIEL_CORS_ORIGINS` (exact origins, default none), `PATHIEL_MAX_BODY_BYTES` (default 1 MiB), `PATHIEL_API_DOCS` (`1` to serve /docs on a deployment), `PATHIEL_METRICS_TOKEN` (bearer for /metrics, a secret), `PATHIEL_LOCAL_HOSTNAMES`, `PATHIEL_TRUST_FORWARDED_FOR` | `pathiel/server.py`, `services/auth/integration.py` |
| Logs and disk | `PATHIEL_LOG_DIR`, `PATHIEL_LOG_MAX_BYTES`, `PATHIEL_LOG_BACKUP_COUNT`, `PATHIEL_LOG_DIR_MAX_BYTES`, `PATHIEL_LOG_ROTATE_INTERVAL_SEC`, `PATHIEL_LOG_JSON`, `PATHIEL_DISK_FREE_WARN_MB`, `PATHIEL_DISK_FREE_CRITICAL_MB` | `pathiel/log_setup.py`, `scripts/log_rotate.py` |
| Backups | `PATHIEL_BACKUP_DIR`, `PATHIEL_BACKUP_KEEP` | `scripts/backup_state.py` |
| MCP server (operator machine only) | `PATHIEL_MCP_MAX_CONCURRENT`, `PATHIEL_MCP_TOOL_TIMEOUT_S`, `PATHIEL_MCP_MAX_OUTPUT_CHARS` | `scripts/pathiel-mcp-server.py` |
| Misc | `NO_SSL_FIX` (skip the macOS certifi workaround), `PATHIEL_ENV` | `pathiel/__init__.py` |

## Not containerised

- **`scripts/pathiel-mcp-server.py`**: a transient stdio process Claude Code
  respawns per tool call next to the operator's interactive session. Nothing
  to deploy.
- **The desk thesis writer**: needs that same signed-in Claude Code CLI.
- **One-off scripts under `scripts/`** not named in `scripts/restart.sh`:
  `backup_state.py`, `treasury.py`, `preflight_live.py`, `desk_slot.py`,
  `oil_levels.py`. Run by hand on the operator's machine.
