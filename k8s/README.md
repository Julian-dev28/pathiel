# pathiel on Kubernetes (local, $0)

Run the full system on a local kind cluster with Prometheus + Grafana
observability — **no cloud spend**. This is a skill-signal / demo layer; the
live bot still runs on Fly.io (see `../DEPLOY.md`).

## Topology

One `StatefulSet` pod, two containers, one shared `PersistentVolumeClaim` at
`/data` — the faithful translation of the Fly setup (`scripts/restart.sh`'s
managed processes, each its own container).

2026-10-23 (wave 2): the `loop` (scripts/trading_loop.py) and `sched`
(scripts/scheduler.py) containers this topology used to run are retired
with the rest of the autonomous scan/research/execute engine. Claude is the
only trader now, through the MCP server (scripts/pathiel-mcp-server.py) — a
transient stdio process Claude Code respawns per tool call, not a
standing container here.

```
StatefulSet pathiel  (replicas: 1 — singleton by design)
├── container: web      python -m pathiel.server        :8000  /api/health + /metrics
├── container: rotator  scripts/log_rotate.py --daemon               (bounds logs/, no /data mount)
        └── PVC data → /data   (.agent-config.json, capital_flows.jsonl, desk journals)
```

**Why one replica / StatefulSet, not a Deployment?** `web` is stateful and
must be a singleton — two copies would corrupt the shared state files. One
pod keeps the shared volume simple (no ReadWriteMany needed) and portable to
multi-node clusters. `rotator` doesn't touch `/data` at all — it only bounds
`logs/` inside the pod's own writable layer — but it runs in the same pod
because it's cheap and its whole job is downstream of `web`'s own log output.

## Prerequisites

```bash
brew install kind helm        # kubectl already present
docker info                   # Docker must be running
```

## 1. Build the image and load it into kind

```bash
# from the repo root
docker build -t pathiel:local .
kind create cluster --name pathiel
kind load docker-image pathiel:local --name pathiel
```

## 2. Create the namespace + secret

```bash
kubectl apply -f k8s/namespace.yaml

# Real keys from your gitignored .env.local (bot trades in LIVE per config),
# OR dummy values for a pure screenshot demo (bot boots in OFF mode):
kubectl create secret generic pathiel-secrets -n pathiel \
  --from-literal=OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-sk-or-dummy}" \
  --from-literal=HYPERLIQUID_WALLET_ADDRESS="${HYPERLIQUID_WALLET_ADDRESS:-0xdummy}" \
  --from-literal=HYPERLIQUID_PRIVATE_KEY="${HYPERLIQUID_PRIVATE_KEY:-0xdummy}" \
  --from-literal=PATHIEL_OPERATOR_TOKEN="$(openssl rand -hex 16)"
```

## 3. Deploy the app

```bash
kubectl apply -k k8s/
kubectl -n pathiel rollout status statefulset/pathiel
kubectl -n pathiel get pods         # expect pathiel-0  5/5  Running
```

## 4. Install Prometheus + Grafana (free, in-cluster)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace

# Wire Prometheus to scrape /metrics (needs the operator CRDs from the step above)
kubectl apply -f k8s/servicemonitor.yaml
```

> The ServiceMonitor's `release: kube-prometheus-stack` label must match the
> Helm release name above. If you name the release differently, edit the label.

## 5. Open the UIs (port-forward)

```bash
# Trading dashboard
kubectl -n pathiel port-forward svc/pathiel 8000:8000
#   → http://localhost:8000   (raw metrics at http://localhost:8000/metrics)

# Grafana
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
#   → http://localhost:3000   (user: admin)
kubectl -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d ; echo
# In Grafana → Explore, query: pathiel_can_trade, pathiel_max_drawdown_pct, …

# Prometheus targets (confirm the scrape is UP)
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
#   → http://localhost:9090/targets   (look for the pathiel endpoint)
```

## Exposed metrics

2026-10-23 (wave 2): `pathiel_equity_usd`/`pathiel_open_positions`/
`pathiel_open_notional_usd`/`pathiel_unrealized_pnl_usd`/
`pathiel_trades_total` are retired along with them — all five read from
either the autonomous engine's own memory ledger or a position snapshot
only the now-deleted trading loop ever wrote. See `pathiel/metrics.py`'s
module docstring for the full account.

| Metric | Meaning |
|--------|---------|
| `pathiel_live_mode` | 1 = LIVE, 0 = OFF |
| `pathiel_can_trade` | 1 when equity clears the structural minimum |
| `pathiel_ai_brain_ready` | 1 when the selected AI brain can actually run |
| `pathiel_disk_free_bytes` | Free bytes on the state filesystem |
| `pathiel_log_dir_bytes` | Total bytes under logs/ |
| `pathiel_drawdown_pct` / `pathiel_max_drawdown_pct` | Current / worst drawdown from peak |
| `pathiel_backup_age_seconds` | Seconds since the last verified state backup |

Plus the standard `process_*` / `python_gc_*` collectors (CPU, RSS, GC) — these
populate on Linux, i.e. inside the container.

## Teardown (back to $0)

```bash
kind delete cluster --name pathiel
```
