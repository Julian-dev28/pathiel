# HIP-3 — current production state

HIP-3 (Hyperliquid's separately-deployed perp dexes for tokenized stocks,
commodities, indices, FX) is fully wired through the trading pipeline.
Dexes seen in production: `xyz`, `km`, `vntl`, `flx`, `hyna`, `abcd`,
`cash`, `para`. Markets are namespaced `<dex>:<symbol>` — e.g.
`xyz:NVDA`, `vntl:NVDA`, `xyz:GOLD`, `xyz:SP500`, `km:USOIL`,
`cash:TSLA`.

## Enable

Set `"enable_hip3": true` in `.agent-config.json`, then restart the loop
(`scripts/restart.sh loop`). The universe is fetched once at startup, so
flipping the flag without restart has no effect on the scanner.

## Threading

The `include_hip3=True` parameter is honored at five entry points:

| Function | Effect |
|---|---|
| `get_universe(include_hip3=True)` | Auto-discovers HIP-3 dexes via `/info perpDexs` and merges each dex's markets. Each market dict gets `"dex": "<name>"`. |
| `fetch_all_mids(include_hip3=True)` | One extra POST per HIP-3 dex; merges `<dex>:<symbol>` mids. |
| `get_all_hl_mids(include_hip3=True)` | Same for the DSL exit pass. Without this, HIP-3 trackers receive no mid and peak/floor never advance. |
| `fetch_account_state(user, include_hip3=True)` | Aggregates equity + `total_ntl` across main + all HIP-3 clearinghouses; concatenates `asset_positions` (bare coins normalized to `<dex>:`); returns `dex_equity` per-dex breakdown and `queried_dexes` set. |
| `Info(perp_dexs=[""] + dex_names)` / `Exchange(perp_dexs=...)` | Teaches the HL SDK to resolve colon names at order-placement time. **CRITICAL: prepend `""`** — the SDK treats the list as exclusive; without main, BTC/ETH/etc. start raising `KeyError`. |

## Per-class config

Two flags govern what gets scanned/traded:
- `enable_crypto` (default `true`) — native HL perps
- `enable_hip3` (default `true`) — HIP-3 dexes

The executor enforces these at execute-time too, so stale perceptions in
memory can't sneak through after a flag flip.

## Per-dex equity vs cross-dex sizing

HIP-3 dexes are SEPARATE clearinghouses. Agent wallets sign orders but
cannot transfer USDC between dexes; the master wallet must do that via
the HL frontend. Sizing semantics:

- **`equity` field (aggregated)**: returned when `include_hip3=True`,
  used by dashboard / heartbeat / portfolio / CLI. Reflects total
  tradeable USDC.
- **`available` field (main-only)**: free initial margin on main HL
  (`accountValue − totalMarginUsed`); the executor sizes against this
  for native crypto trades. Matches what HL UI calls "Available to Trade".
- **Per-trade HIP-3 preflight**: before placing an HIP-3 order, the
  executor queries that specific dex's clearinghouse and refuses with
  `hip3_dex_underfunded` if the dex has < $1.

## DSL safety: `queried_dexes`

`fetch_account_state(include_hip3=True)` returns a set of dexes whose
clearinghouse responded successfully this cycle. `rehydrate_from_exchange`
takes this as a hint and only drops trackers whose dex was actually
queried. If the `xyz` dex query times out, all xyz trackers are
preserved (peak/floor intact) until the next successful poll, instead
of getting nuked and re-synthesized fresh.

## Liquidity floor split

- `min_market_volume_usd` (default $5M) — applies to native crypto
- `min_hip3_volume_usd` (default $5M) — applies to colon-namespaced
  markets (most `xyz:*` markets sit in $1M–$50M vs $1B+ for BTC)

## Asset-class routing

`agents/market_regime.classify_asset()` strips the dex prefix before
lookup so `xyz:NVDA` correctly maps to `equity` (not crypto). The
regime gate also skips the binary-news check for tokenized equities
because their headlines always include earnings/Fed/SEC by definition.

## Off-hours

HIP-3 equity markets only trade during US equity hours. Outside those
hours volume drops to ~zero and the scanner skips them via
`min_hip3_volume_usd`. No explicit hours-gate.

## Original session learnings (May 2026, kept for posterity)

- Universe fetched once at startup, so changing `enable_hip3` mid-run
  required a restart (still true).
- HIP-3 trades initially died at `if mid_price <= 0` because
  `info.all_mids()` defaults to native dex; fix was to detect the colon
  prefix and call `all_mids(dex=...)`.
- HIP-3 dex clearinghouse balance check exists because agent wallets
  cannot move funds between dexes; HL's "Insufficient margin" rejection
  doesn't say which dex.
