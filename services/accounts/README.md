# services/accounts — connect a Hyperliquid account instead of hand-editing keys

Hyperdash-style account management. Connecting a Hyperliquid account IS the
identity — there is no separate "sign in" step. Three ways in: sign a wallet
approval (`wallet_sign`), paste an API wallet already created on
app.hyperliquid.xyz/API (`api_key`), or just watch an address read-only
(`watch`). `.env.local`'s legacy keys still work with zero action, shown as a
read-only `source="env"` account.

The target model: the main (funds) key never touches this repo. An approved
agent key places trades and can never withdraw.

## File map

| File | Owns |
|---|---|
| `env.py` | `$PATHIEL_STATE_DIR` resolution — self-contained, mirrors `services/intel/env.py` |
| `registry.py` | `accounts.json`: labels, addresses, mode, source, which one is active |
| `vault.py` | Agent private keys, encrypted at rest (`eth_account`'s keystore format), one file per account id |
| `agent_approval.py` | The `approveAgent` EIP-712 flow: typed data, signature recovery, the (real, never-called-by-tests) `/exchange` submission |
| `hl_verify.py` | Read-only Hyperliquid `/info` checks (`extraAgents`, `userRole`) |
| `pending.py` | In-memory `prepare` → `confirm` handshake, single-use |
| `credentials.py` | `get_trading_credentials()` — what `pathiel/client/exchange.py` actually signs orders with |
| `view.py` | Contract-shape conversions + the synthetic `env` pseudo-account |
| `summary.py` | `ActiveSummary` — live equity/positions plus plain-English blockers |
| `api.py` | The `APIRouter` — every route in `contracts/accounts/models.py` |

## Security model

- The main (funds) key is never generated, stored, or read by this service.
  Only an agent key — created server-side in `prepare`, or supplied by the
  user for `import` — is ever stored, and only encrypted
  (`Account.encrypt`/`Account.decrypt`, password from
  `PATHIEL_VAULT_PASSPHRASE` or a machine-local secret created once with
  chmod 600).
- No route returns key material, ever — `services/accounts/tests/test_security.py`
  scans every response body and every log line for it across every flow.
- `agent/confirm` burns its `request_id` (an atomic, single-use pop) BEFORE
  the signature is even inspected, so a replayed confirm call — the same
  request_id and signature, resent by whoever observed the first one — can
  never call `services.auth.integration.issue_session_for_address` a second
  time. See `test_wallet_sign_confirm_cannot_be_replayed`.
- Every mutating route requires `services.auth.integration.require_session_or_local`
  (local mode, or an existing session established via SIWE); routes acting on
  a specific account additionally require
  `integration.session_may_control_account` against that account's own
  `main_address`. `agent/prepare`, `agent/confirm`, and `import` are
  rate-limited the same way `/auth/verify` is.
- A second connected account never steals activation from an existing one —
  only "nothing is active yet" auto-activates a new account — matching
  wallet-ui's own client-side rule so the two can never disagree about which
  account is actually trading.
- `scripts/preflight_secrets.py` recognizes a connected vault account as
  satisfying the legacy `HYPERLIQUID_*` env-var requirement, and warns
  (naming the exact risk and mitigation) whenever `HYPERLIQUID_MASTER_PRIVATE_KEY`
  is present.

## Not mounted here

`pathiel/server.py` is integrator-only this wave. Mount with:

```python
from services.accounts.api import router as _accounts_router
app.include_router(_accounts_router)
```

## Tests

```
PATHIEL_STATE_DIR="$(mktemp -d)" python -m pytest services/accounts/tests/ -q
```

~115 gate tests, no network (every Hyperliquid call — `/info` and the real,
implemented-but-never-exercised `/exchange` submission — is mocked), under
3s. Covers: vault round trips and file permissions; typed data proven
hash-identical to the installed hyperliquid SDK's own `sign_agent` in both
signing directions; signature recovery (correct signer, wrong signer,
expired/unknown/replayed requests); import verification against a mocked
`/info`; activate/rename/delete including the active account; the env
fallback; credentials cache invalidation (same-process and
cross-process, via the registry file's own mtime); and full end-to-end
flows through `TestClient` for `wallet_sign`, `api_key`, and `watch`,
including every auth-gating and rate-limit case.

No eval suite: this service makes zero LLM calls.
