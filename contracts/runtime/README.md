# contracts/runtime

One place to ask "public or operator", "mainnet or testnet" and "which builder fee".
Stdlib only, no pathiel or services imports. Every function reads the
environment at call time; pass `env={...}` to test it with a plain dict.

`CONTRACT_VERSION = "1.2.0"`. Bump it if a reader would break. 1.2.0
(2026-11-13): the builder fee is waived (`BUILDER_FEE_WAIVED = True`, so
`DEFAULT_BUILDER_FEE_TENTHS_BP` is 0 and no order carries a builder code);
`LISTED_BUILDER_FEE_TENTHS_BP` keeps the rate the fee returns at (10, 0.01%).
1.1.0 (2026-11-12): a public install with no `PATHIEL_NETWORK` is on mainnet.

## Env contract

| variable | values | set by |
|---|---|---|
| `PATHIEL_INSTALL` | `public`, anything else = operator | `scripts/_plugin_env.sh` and `Dockerfile.mcp` only |
| `PATHIEL_NETWORK` | `mainnet`, `testnet` (any case); invalid = unset | the user, in `~/.pathiel/.env.local`, exported by the launcher before Python starts |
| `PATHIEL_NETWORK_SOURCE` | `environment`, `env file`, `default`, `invalid` | the launcher only; `default` and `invalid` mean nobody chose the network |
| `PATHIEL_BUILDER_ADDRESS` | `0x` + 40 hex; empty = the default; any other value = no builder | optional override of `DEFAULT_BUILDER_ADDRESS` |
| `PATHIEL_BUILDER_FEE_TENTHS_BP` | integer 0..100; anything else = 0 (no builder) | optional override of `DEFAULT_BUILDER_FEE_TENTHS_BP` |

The operator's stack (repo `.mcp.json`, `pathiel/server.py`) sets none of them, so
it resolves to operator, mainnet, and never attaches a builder.

## Resolution table

| `PATHIEL_INSTALL` | `PATHIEL_NETWORK` | `install_kind()` | `network()` | `api_url()` |
|---|---|---|---|---|
| unset | unset | operator | mainnet | `https://api.hyperliquid.xyz` |
| unset | `testnet` | operator | testnet | `https://api.hyperliquid-testnet.xyz` |
| unset | `bogus` | operator | mainnet | mainnet URL |
| `public` | unset | public | mainnet | mainnet URL |
| `public` | `testnet` | public | testnet | testnet URL |
| `public` | `mainnet` | public | mainnet | mainnet URL |
| `public` | `bogus` | public | testnet | testnet URL |

Mainnet is the default on both install kinds (decision of 2026-11-12): a
public install trades real money unless the user opts into testnet. The
public launcher (`scripts/_plugin_env.sh`) never hands Python an invalid
value: it reads the process env, then the first `PATHIEL_NETWORK` line of the
env file (an `export ` prefix and a trailing ` # comment` are allowed),
exports `mainnet` when nothing is set and `testnet` plus one warning when the
value is set but is neither network. State is split by network:
`~/.pathiel/state` for mainnet, `~/.pathiel/state-testnet` for testnet.

`network_is_explicit()` is True only when `PATHIEL_NETWORK` holds a valid value and
`PATHIEL_NETWORK_SOURCE` is neither `default` nor `invalid`. The public launcher
always exports `PATHIEL_NETWORK` and sets the source to `default` when nobody
chose a network.

## Builder fee

| function | result |
|---|---|
| `builder_address()` | env override when non-empty, else `DEFAULT_BUILDER_ADDRESS`; lowercase; `''` unless `0x` + 40 hex |
| `builder_fee_tenths_bp()` | env override when non-empty, else `10`; `0` when not a plain integer in 0..100 |
| `fee_rate_str(f)` | exact percent string: `1 -> 0.001%`, `10 -> 0.01%`, `25 -> 0.025%`, `100 -> 0.1%`, `0 -> 0%` |

`DEFAULT_BUILDER_ADDRESS` sits under the `BUILDER CONFIG POINT` banner in
`__init__.py`: `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`, Julien's funded
master account (decision of 2026-11-12). It is public: anyone can see which
address collects the fee. The wallet needs at least 100 USDC of perps account
value on Hyperliquid to collect fees. An order from that same account never
carries the builder (`self_trade`, compared lowercase), so a public install
Julien runs with his own account pays no fee to himself. Empty turns the
builder off on every install.

Default fee: 10 tenths of a basis point = 1 bp = 0.01% of notional per fill,
0.02% per round trip. The perp cap is 100 (0.1%).

Who uses it:

* `pathiel/client/hl_client.py`: `HL_API = runtime.api_url()` at import.
* `pathiel/client/builder.py`: whether and what builder to attach (cache only on the order path).
* `pathiel/client/exchange.py`: attaches it, resends once without it when the venue placed nothing (not on a known venue refusal such as an IOC that did not match).

`describe()` returns all of the above as one dict for status output.
