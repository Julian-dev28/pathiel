# Hyperliquid Order-Placement Gotchas

Hard-won operational knowledge for `hermes_agent/client/exchange.py`. Every item
below is reflected in the current code — this is reference, not a TODO list.

## szDecimals vs pxDecimals

Hyperliquid gives each asset **two separate** decimal counts: `szDecimals`
(size) and `pxDecimals` (price). They are not always equal. `get_coin_index()`
returns `(asset_index, sz_decimals, px_decimals)` — use `sz_decimals` to format
order size, `px_decimals` for price rounding. Mixing them up causes
"Order has invalid price/size" / "Price must be divisible by tick size".

## Tick-size price rounding

Order price must be a multiple of the tick size and carry ≤5 significant figures.
`_round_price_for_hl(price, sz_decimals, is_perp)` handles both constraints with
`Decimal` math. Order placement is verified working end-to-end against mainnet —
the historical "SDK float precision" blocker is **resolved**; do not reintroduce
REST-bypass workarounds.

## Order type / tif

`OrderType(limit={"tif": "Ioc"})` — `"Ioc"` is capital-I (the SDK uses a
`Literal` type; `"ioc"` fails type-checking). IOC orders use a small offset from
mid (`mid_price * (1.001 if is_buy else 0.999)`) so they cross and fill.

## $10 minimum order value

Hyperliquid rejects orders below $10 notional. `executor.py` floors the size:
`size_in_coin = max(size_in_coin, 10.0 / mid_price)`. For integer-size coins
(`sz_decimals == 0`, e.g. XRP) a floor-rounded size can still land under $10 —
round those up.

## Client singletons (WebSocket connection limit)

Hyperliquid caps a wallet at ~10 simultaneous WebSocket connections. Both HL
clients are therefore module-level singletons:

- `exchange.py:_make_exchange()` — reuses `_exchange_instance` (write side; needs
  `HYPERLIQUID_PRIVATE_KEY`). Constructing a fresh `Exchange()` per order would
  exhaust the connection limit.
- `exchange.py:_get_info()` — reuses one `Info(skip_ws=True)` (read side). The
  `skip_ws=True` matters: the read path is REST-only, so no socket is opened.

## Equity on unified accounts

On a unified account `perp_equity` (from `marginSummary.accountValue`) **already
includes** spot USDC. `fetch_account_state()` sets `equity = perp_equity` — do
not add `spot_usdc` on top, or equity double-counts.

## Sizing

`executor.py` sizes a trade at `equity * 0.01 * HL_LEVERAGE` (1% of equity at
5×). The leverage is already in that figure — `position_notional = trade_notional`,
never `trade_notional * HL_LEVERAGE` again.

## Info endpoint payloads

HL `/info` requests use a `type` field (`{"type": "clearinghouseState", ...}`).
An `action` field returns HTTP 422.
