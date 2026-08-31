# Secrets

Local dev survives a plaintext `.env.local` on a laptop nobody else touches
and no key-management layer. A deployed service does not: the process
environment, the image, and (if anyone ever runs `git add -A` in this tree)
the repo history all become plausible leak paths for a real-money Hyperliquid
key. This doc is the inventory + the containment story + the rotation
runbook. `scripts/preflight_secrets.py` is the machine-checked version of the
same rules — run it before every deploy and every restart of a deployed
service:

```bash
.venv/bin/python scripts/preflight_secrets.py            # local check
.venv/bin/python scripts/preflight_secrets.py --deploy    # simulate deploy-mode checks locally
```

It prints findings by NAME, never by VALUE, and exits non-zero if anything is
wrong. Wire it into the deploy pipeline as a hard gate (see
[Wire it into CI/CD](#wire-it-into-cicd) below) — this doc explains *why*
each check exists; the script is what actually enforces it.

## Secret inventory

| Var | Purpose | Reads it | Required | Missing behavior |
|---|---|---|---|---|
| `HYPERLIQUID_WALLET_ADDRESS` | Agent/API wallet address — the identity that signs orders | `pathia/client/exchange.py`, `pathia/client/hl_client.py` | **Always** | Silent — defaults to `""`; downstream calls that need a real address fail deep in the SDK, not at startup |
| `HYPERLIQUID_PRIVATE_KEY` | Agent/API wallet private key — signs every order | `pathia/client/exchange.py`, `pathia/agents/executor.py`, `scripts/trading_loop.py` | **Always** | Loud where it matters: `executor.py` returns `{"executed": False, "reason": "private_key_missing"}` per attempt and `trading_loop.py` blocks LIVE mode outright. `exchange.py` itself just holds `""` — an actual sign attempt would blow up in the SDK, not here |
| `HYPERLIQUID_MASTER_ADDRESS` | Master account public address — where funds actually live | `pathia/client/exchange.py`, `pathia/client/hl_client.py`, `scripts/treasury.py` | **Always** (for the agent-vs-master safety check — see below) | Silent — `exchange.py`'s `IS_AGENT` flag quietly becomes `False` and the trading identity falls back to the wallet address alone. The app tolerates this; the preflight check does not |
| `HYPERLIQUID_MASTER_PRIVATE_KEY` | Master account private key — signs treasury transfers/swaps ONLY | `scripts/treasury.py` (nowhere else) | **Local-only** — must never exist in a deployed environment | Loud: `treasury.py` prints an error and `sys.exit(2)` |
| `PATHIA_OPERATOR_TOKEN` | Bearer token gating every write endpoint on the dashboard (`/operator`) | `pathia/dashboard.py`, `scripts/smoke_trends.py` | **Always** | Loud and fail-*closed*: `_require_operator()` 503s "operator surface disabled" rather than opening the surface with no auth |
| `OPENROUTER_API_KEY` | OpenRouter API key — the default AI research brain | `pathia/agents/ai_brain.py` | **Conditional** — required iff `AI_BRAIN_PROVIDER` resolves to `openrouter` (the default when unset) | Logs a warning and returns `""` from the completion call. `research.py` tags the resulting analysis `ai_down: True` so the executor's structural override cannot upgrade a failure-PASS into a blind LONG (fixed 2026-06-11 after exactly that happened during an OpenRouter 402 window) — but every research call still fails silently at the log level, not at startup |
| `BRAVE_API_KEY` | Brave Search — news context for research | `pathia/agents/research.py` | Optional | Silent by design — returns `"no news"` and continues |
| `UW_API_KEY` | Unusual Whales — options-flow alt-data (xyz equities) | `pathia/client/uw_client.py` | Optional | Silent by design — best-effort client, returns `None`/`[]`, never raises |
| `HYDROMANCER_API_KEY` | Hydromancer data-plane client (research/backfill, not live execution) | `pathia/data_providers/hydromancer.py` | Optional | Silent — empty key string; the first real call fails with an auth error, not checked upfront |

Non-secret vars read alongside these (model names, timeouts, CLI binary
paths, scan-tuning knobs, state-file path overrides) are listed — grouped and
commented — in `.env.local.example`. They don't belong in this table because
leaking one costs nothing; the table above is specifically the set that
`scripts/preflight_secrets.py` treats as sensitive (required-presence check,
git-index scan, redaction).

**One dead var found during this inventory:** `PATHIA_CHAT_MODEL` is set in
the live `.env.local` but is not read by any module in `pathia/`,
`services/`, or `scripts/` — grep across all three came up empty. Likely a
leftover from an earlier iteration. Safe to drop; harmless to leave (it is
not a secret, just an unused knob).


## Provisioning for a deploy

Never put a real value in `fly.toml`'s `[env]` block, a `Dockerfile` `ENV`
line, a k8s `ConfigMap`, or any tracked file — only in the platform's secret
store, injected as process environment variables at runtime.

### Fly.io (see `DEPLOY.md` for the full one-time setup)

```bash
flyctl secrets set \
  HYPERLIQUID_WALLET_ADDRESS="0x..." \
  HYPERLIQUID_PRIVATE_KEY="0x..." \
  HYPERLIQUID_MASTER_ADDRESS="0x..." \
  OPENROUTER_API_KEY="sk-or-..." \
  PATHIA_OPERATOR_TOKEN="$(openssl rand -hex 16)"

# optional
flyctl secrets set BRAVE_API_KEY="BSA..."
flyctl secrets set UW_API_KEY="..."

# NEVER: flyctl secrets set HYPERLIQUID_MASTER_PRIVATE_KEY=...
# Treasury transfers are a manual, local-only operation — see below.
```

`flyctl secrets list` prints names only, never values. Setting or unsetting
a secret triggers a rolling redeploy — that's expected, not a bug.

### Kubernetes (see `k8s/README.md`, `k8s/secret.example.yaml`)

Create the Secret straight from the operator's gitignored `.env.local` so
values never touch a tracked file:

```bash
kubectl create secret generic pathia-secrets -n pathia \
  --from-literal=HYPERLIQUID_WALLET_ADDRESS="$HYPERLIQUID_WALLET_ADDRESS" \
  --from-literal=HYPERLIQUID_PRIVATE_KEY="$HYPERLIQUID_PRIVATE_KEY" \
  --from-literal=HYPERLIQUID_MASTER_ADDRESS="$HYPERLIQUID_MASTER_ADDRESS" \
  --from-literal=OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  --from-literal=PATHIA_OPERATOR_TOKEN="$(openssl rand -hex 16)"
```

`kubectl get secret pathia-secrets -o yaml` shows base64, not plaintext, but
base64 is encoding, not encryption — treat `kubectl get -o yaml` access on
that namespace as equivalent to reading the values directly when reasoning
about who can see secrets.

### Plain env (systemd unit, bare VM, etc.)

Set via the unit's `Environment=`/`EnvironmentFile=` pointed at a file with
`0600` permissions and *not* under the app's working directory (so a
misconfigured static file server, backup job, or `tar czf .` can't sweep it
in). `scripts/preflight_secrets.py`'s permission and gitignore checks apply
the same way to this file as to `.env.local` — point `--env-file` at it and
run the same command.

## Agent-wallet setup — the containment boundary

This is the single highest-value control in this document, and the one
`scripts/preflight_secrets.py` treats as a hard gate
(`check_master_not_agent`).

Hyperliquid supports two distinct keys per account:

- **Master key** — full control: trade, transfer, withdraw. This is the key
  behind your actual wallet/private key if you never set anything else up.
- **Agent (API) wallet key** — a separate keypair, approved on-chain by the
  master account (`approveAgent`), that can place and cancel orders **but
  cannot withdraw or transfer funds**. Hyperliquid's UI calls this "API
  wallet" under *More → API*.

The bot should trade with the agent key, full stop. Set:

```bash
HYPERLIQUID_WALLET_ADDRESS=<agent wallet address>
HYPERLIQUID_PRIVATE_KEY=<agent wallet private key>
HYPERLIQUID_MASTER_ADDRESS=<master account address>   # public, not a secret
```

Why this is the boundary that matters: every other secret in this file, if
leaked, costs *research quality* (an API key someone else can spend against
your quota) or *trading privacy* (someone can see what the bot would have
done). A leaked agent private key costs *the positions currently open* —
real, but bounded, and rate-limited by however fast a thief can flatten and
re-open in the wrong direction before the bot or the operator notices. A
leaked **master** key costs *everything the account holds, gone in one
transaction, with no bound*. That asymmetry is why
`check_master_not_agent()` fails the preflight outright — not warns — when
`HYPERLIQUID_WALLET_ADDRESS` equals `HYPERLIQUID_MASTER_ADDRESS`, and why it
also fails when `HYPERLIQUID_MASTER_ADDRESS` is simply unset: an unset master
address means there's no way to *prove* the trading key isn't the master, and
"unprovable" gets treated as "unsafe" for a deploy gate, even though the app
itself tolerates it at runtime.

`HYPERLIQUID_MASTER_PRIVATE_KEY` is the one secret in this repo that can
actually withdraw. It exists purely for `scripts/treasury.py` — moving USDC
between spot/perp/HIP-3 dexes, a manual operation the operator runs by hand.
It has no business in any deployed process's environment: the trading loop
and the dashboard never read it. `scripts/preflight_secrets.py` warns if it's
present locally and **fails** if it detects a deploy context (`FLY_APP_NAME`
or `KUBERNETES_SERVICE_HOST` set) and finds it anyway.

## Rotation procedure

General shape for every credential below: generate/rotate at the provider,
update the deploy secret store, redeploy (or restart, if the process re-reads
env on the fly — none of these currently do; a restart is required), then
revoke the old value at the provider once the new one is confirmed live.

| Secret | Rotate at | Redeploy needed | If it leaked |
|---|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | Hyperliquid UI → *More → API* → revoke the old agent wallet, create a new one, `approveAgent` it | Yes | **Revoke the agent wallet in the Hyperliquid UI immediately** (this alone stops it — an agent key cannot withdraw, so the blast radius is bounded to open positions). Flatten any open positions if you don't trust what a thief might have done with the window. Generate + wire a new agent wallet. The master funds were never exposed. |
| `HYPERLIQUID_WALLET_ADDRESS` | Follows the private key above (they're a pair) | Yes | Not a secret by itself (a public address) — rotate alongside the private key |
| `HYPERLIQUID_MASTER_ADDRESS` | This is your actual account; you don't "rotate" it. If it needs to change, you're moving to a different Hyperliquid account entirely — treat as a full re-provision | Yes | Not a secret (public address) — no action needed on its own, but if leaked *alongside* the master private key, see below |
| `HYPERLIQUID_MASTER_PRIVATE_KEY` | Hyperliquid does not support rotating a master wallet's key — it's an on-chain wallet, so "rotation" means moving funds to a new wallet | N/A (local-only, never deployed) | **Treat as a full account compromise.** Move all funds to a brand-new wallet immediately via the Hyperliquid UI from a separate trusted device if possible, then stop using the old wallet entirely. This is the one leak this doc cannot make routine — it is the master key, it can empty the account, and speed is the only mitigation. |
| `PATHIA_OPERATOR_TOKEN` | Anywhere: `openssl rand -hex 16`, then `flyctl secrets set PATHIA_OPERATOR_TOKEN=...` (or the k8s/systemd equivalent) | Yes | Rotate immediately (see above). Worst case with the old token still valid: someone can flip config knobs and trigger manual closes through `/operator` — no fund transfer is reachable through that surface, but it's still real control of the bot. |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys — revoke old, create new | Yes | Revoke at OpenRouter immediately. Worst case: someone burns your quota / sees prompts you sent (market context, not credentials). |
| `BRAVE_API_KEY` | https://api.search.brave.com/ dashboard | Yes | Revoke at Brave. Low severity — a news-search key. |
| `UW_API_KEY` | Unusual Whales account dashboard | Yes | Revoke at Unusual Whales. Low-medium severity — paid data quota, not funds. |
| `HYDROMANCER_API_KEY` | Hydromancer account dashboard | Yes | Revoke at Hydromancer. Low severity — read-only data plane. |

After rotating anything that touched a tracked file (it shouldn't have, but
if `scripts/preflight_secrets.py`'s git-index scan ever fires): rotating the
credential is necessary but not sufficient — the old value is still in git
history. `git filter-repo` or BFG Repo-Cleaner to purge it, then force-push
with the team's explicit sign-off (never unilaterally force-push shared
history).

## Wire it into CI/CD

Run the preflight as the last step before a deploy actually starts serving
traffic — it's deterministic and offline, so it costs nothing to run on
every deploy:

```bash
.venv/bin/python scripts/preflight_secrets.py --deploy || exit 1
```

`--deploy` forces the deploy-mode checks (currently just
`HYPERLIQUID_MASTER_PRIVATE_KEY` absence) even when run from a CI runner that
doesn't carry Fly/k8s platform env vars itself. On the actual Fly machine or
k8s pod, `FLY_APP_NAME`/`KUBERNETES_SERVICE_HOST` are set automatically and
`--deploy` isn't needed — running the bare command is enough.
