# contracts/runtime

One place to ask "public or operator", "mainnet or testnet" and "which builder fee".
Stdlib only, no pathiel or services imports. Every function reads the
environment at call time; pass `env={...}` to test it with a plain dict.

`CONTRACT_VERSION = "1.0.0"`. Bump it if a reader would break.

## Env contract

| variable | values | set by |
|---|---|---|
| `PATHIEL_INSTALL` | `public`, anything else = operator | `scripts/_plugin_env.sh` and `Dockerfile.mcp` only |
| `PATHIEL_NETWORK` | `mainnet`, `testnet` (any case); invalid = unset | the user, in `~/.pathiel/.env.local`, exported by the launcher before Python starts |
| `PATHIEL_BUILDER_ADDRESS` | `0x` + 40 hex; invalid = no builder | optional override of `DEFAULT_BUILDER_ADDRESS` |
| `PATHIEL_BUILDER_FEE_TENTHS_BP` | integer 0..100; anything else = 0 (no builder) | optional override of `DEFAULT_BUILDER_FEE_TENTHS_BP` |

The operator's stack (repo `.mcp.json`, `pathiel/server.py`) sets none of them, so
it resolves to operator, mainnet, and never attaches a builder.

## Resolution table

| `PATHIEL_INSTALL` | `PATHIEL_NETWORK` | `install_kind()` | `network()` | `api_url()` |
|---|---|---|---|---|
| unset | unset | operator | mainnet | `https://api.hyperliquid.xyz` |
| unset | `testnet` | operator | testnet | `https://api.hyperliquid-testnet.xyz` |
| unset | `bogus` | operator | mainnet | mainnet URL |
| `public` | unset | public | testnet | testnet URL |
| `public` | `mainnet` | public | mainnet | mainnet URL |
| `public` | `bogus` | public | testnet | testnet URL |

`network_is_explicit()` is True only when `PATHIEL_NETWORK` holds a valid value.

## Builder fee

| function | result |
|---|---|
| `builder_address()` | env override when non-empty, else `DEFAULT_BUILDER_ADDRESS`; lowercase; `''` unless `0x` + 40 hex |
| `builder_fee_tenths_bp()` | env override when non-empty, else `10`; `0` when not a plain integer in 0..100 |
| `fee_rate_str(f)` | exact percent string: `1 -> 0.001%`, `10 -> 0.01%`, `25 -> 0.025%`, `100 -> 0.1%`, `0 -> 0%` |

`DEFAULT_BUILDER_ADDRESS` sits under the `BUILDER CONFIG POINT` banner in
`__init__.py`. It is empty: no builder is attached anywhere until Julien fills it
in. Filling it links that wallet to the project in public. The wallet needs at
least 100 USDC of perps account value on Hyperliquid to collect fees.

Default fee: 10 tenths of a basis point = 1 bp = 0.01% of notional per fill,
0.02% per round trip. The perp cap is 100 (0.1%).

Who uses it:

* `pathiel/client/hl_client.py`: `HL_API = runtime.api_url()` at import.
* `pathiel/client/builder.py`: whether and what builder to attach (cache only on the order path).
* `pathiel/client/exchange.py`: attaches it, resends once without it when the venue placed nothing.

`describe()` returns all of the above as one dict for status output.
