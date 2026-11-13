# contracts/wallet_connect

The boundary for "connect your Hyperliquid wallet" on a plugin install. Stdlib
only (plus `contracts.runtime`), so the MCP server imports it at start for free.

`CONTRACT_VERSION = "1.0.0"`. Bump it if a reader would break.

## Who uses it

| side | what it takes from here |
|---|---|
| `scripts/pathiel-mcp-server.py` | `MCP_TOOL`, `MCP_TOOL_CLASS` (`account_setup`: destructive, never live money), `mcp_handler()` |
| `scripts/connect_wallet.py` | `operator_refusal()` before anything else |
| `services/accounts/connect_wallet.py` | `ENV_KEYS`, `AGENT_NAME`, `PAGE_TIMEOUT_S`, `STATES`, `ERROR_CODES`, `ConnectResult`, `RECONNECT_NOTE` |

## The flow

```
Claude ─ wallet_connect ─▶ preflight (public only, not a checkout .env.local,
                           same network as the file, no key unless replace)
                         ─▶ lock <env>.connect-lock, make agent key here
                         ─▶ page on http://127.0.0.1:<random>/<secret>/  (10 min, one use)
browser wallet ◀─ page: EIP-6963 / window.ethereum, eth_requestAccounts,
                  eth_chainId, eth_signTypedData_v4 (ApproveAgent)
page ─ POST {address, signature, chainId} ─▶ rebuild typed data, recover signer == address
                         ─▶ POST /exchange approveAgent once
                         ─▶ /info extraAgents or userRole lists the agent
                         ─▶ write HYPERLIQUID_PRIVATE_KEY / _WALLET_ADDRESS / _MASTER_ADDRESS
                            (other lines kept, mode 600, atomic)
Claude ─ wallet_connect {status: true} ─▶ done: "reconnect pathiel in /mcp"
```

## Rules

- The operator's checkout is refused first (`operator_install`), before any
  file is read or written. `mcp_handler()` refuses before importing the
  service; the service refuses again on its own.
- The agent private key never appears in a result, a log line, an HTTP
  response or the page. `result_problems()` checks every result for a
  64-hex value.
- Results follow `ConnectResult`: `ok`, `state` (one of `STATES`), and on a
  failure `code` (one of `ERROR_CODES`) with a plain `error` and `next`.

Tests: `contracts/wallet_connect/tests/`, `services/accounts/tests/test_connect_wallet.py`,
`tests/test_wallet_connect_mcp.py`, eval `tests/test_eval_wallet_connect.py`.
