# pathia on Kubernetes (local, $0)

Run the full system on a local kind cluster with Prometheus + Grafana
observability — **no cloud spend**. This is a skill-signal / demo layer; the
live bot still runs on Fly.io (see `../DEPLOY.md`).

## Topology

One `StatefulSet` pod, five containers, one shared `PersistentVolumeClaim` at
`/data` — the faithful translation of the Fly setup (`scripts/restart.sh`'s
five managed processes, each its own container):

```
StatefulSet pathia  (replicas: 1 — singleton by design)
├── container: web      python -m pathia.server        :8000  /api/health + /metrics
├── container: loop     scripts/trading_loop.py                      (writes /data snapshot)
├── container: sched    scripts/scheduler.py                         (poly-board/judgment, autonomous-cycle, trends-*)
├── container: rotator  scripts/log_rotate.py --daemon               (bounds logs/, no /data mount)
        └── PVC data → /data   (.dsl-state.json, .agent-memory.json, positions snapshot,
                                 .state/ — shadow ledgers, scheduler bookkeeping, per-strategy timers)
```

**Why one replica / StatefulSet, not a Deployment?** The `loop` is stateful and
must be a singleton — two loops would double-trade and corrupt the DSL ratchet.
`web` and `sched` both read or write the same shared state the loop
owns, so co-locating everything in one pod keeps the shared volume simple (no
ReadWriteMany needed) and portable to multi-node clusters. `rotator` doesn't
touch `/data` at all — it only bounds `logs/` inside the pod's own writable
layer — but it runs in the same pod because it's cheap and its whole job is
downstream of `sched`'s output.

## Prerequisites

```bash
brew install kind helm        # kubectl already present
docker info                   # Docker must be running
```

## 1. Build the image and load it into kind

```bash
# from the repo root
docker build -t pathia:local .
kind create cluster --name pathia
kind load docker-image pathia:local --name pathia
```

## 2. Create the namespace + secret

```bash
kubectl apply -f k8s/namespace.yaml

# Real keys from your gitignored .env.local (bot trades in LIVE per config),
# OR dummy values for a pure screenshot demo (bot boots in OFF mode):
kubectl create secret generic pathia-secrets -n pathia \
  --from-literal=OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-sk-or-dummy}" \
  --from-literal=HYPERLIQUID_WALLET_ADDRESS="${HYPERLIQUID_WALLET_ADDRESS:-0xdummy}" \
  --from-literal=HYPERLIQUID_PRIVATE_KEY="${HYPERLIQUID_PRIVATE_KEY:-0xdummy}" \
  --from-literal=PATHIA_OPERATOR_TOKEN="$(openssl rand -hex 16)"
```

## 3. Deploy the app

```bash
kubectl apply -k k8s/
kubectl -n pathia rollout status statefulset/pathia
kubectl -n pathia get pods         # expect pathia-0  5/5  Running
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
kubectl -n pathia port-forward svc/pathia 8000:8000
#   → http://localhost:8000   (raw metrics at http://localhost:8000/metrics)

# Grafana
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
#   → http://localhost:3000   (user: admin)
kubectl -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d ; echo
# In Grafana → Explore, query: pathia_open_positions, pathia_unrealized_pnl_usd, …

# Prometheus targets (confirm the scrape is UP)
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
#   → http://localhost:9090/targets   (look for the pathia endpoint)
```

## Exposed metrics

| Metric | Meaning |
|--------|---------|
| `pathia_equity_usd` | Last known account equity |
| `pathia_open_positions` | Open positions (from the loop snapshot) |
| `pathia_open_notional_usd` | Sum of open position notional |
| `pathia_unrealized_pnl_usd` | Sum of unrealized PnL |
| `pathia_trades_total` | Recorded trades |
| `pathia_live_mode` | 1 = LIVE, 0 = OFF |

Plus the standard `process_*` / `python_gc_*` collectors (CPU, RSS, GC) — these
populate on Linux, i.e. inside the container.

## Teardown (back to $0)

```bash
kind delete cluster --name pathia
```
