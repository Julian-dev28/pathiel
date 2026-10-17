# MCP tool table

`scripts/pathiel-mcp-server.py` -- 74 tools over stdio (down from 88; 19 old names collapsed into hidden aliases, 5 desk_* tools added). Generated from TOOLS/ALIASES on 2026-10-17; regenerate the same way if the surface changes again (see the script this table was built with: scripts/mcp_token_report.py measures the result, this table documents it).

LIVE-MONEY tools (never call outside a mocked test): **execute**, **close_position**, **set_leverage**, **cancel_order**.

## Agent loop (live-money or state)

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `config` |  |  | Get or set agent configuration. Call with no params to read the full config. Keys are snake_case and match .agent-config.json exactly. Covers mode, sizing, risk caps, regime gates, exits, GEX, and the live strategy blocks. |
| `execute` | YES |  | Execute a trade based on a prior analysis. Passes through risk gates and DSL exit registration. |
| `research` |  |  | Deep AI analysis on a specific coin. Requires a triggered signal from scan first. |
| `scan` |  |  | Scan Hyperliquid markets for trading signals. Returns triggered candidates above a score threshold. |
| `state` |  |  | Get full agent state: mode, config, positions, recent trades, and account equity. |
| `submit_verdict` |  |  | Submit an agent-authored verdict as a stored analysis. Use this when Claude/Codex/OpenClaw is the brain. The returned analysisId can be passed to execute, which still routes through risk gates / close helper. |

## Wallet / dashboard (read-only)

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `connected_wallets` |  |  | Wallets that have signed in to the dashboard with Sign-In With Ethereum, newest first. READ-ONLY and informational: this server signs with the deployment's own key, so it can never place, close or size an order on any wallet listed here. Use it to answer 'whose account am I looking at' and to feed an address to wallet_account below. |
| `wallet_account` |  |  | Read-only Hyperliquid state for any address: equity, withdrawable, and open positions. Needs no key, because /info clearinghouseState takes a plain address. Omit `address` to read the wallet most recently signed in to the dashboard; pass one to read a specific account. An unfunded address is not an error — it returns funded=false. |

## Market data

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `get_all_mids` |  | `market_get_mids` | All current mid prices, including HIP-3 (xyz:, vntl:, km:, ...). Also answers market_get_mids. |
| `get_asset_context` |  | `get_market_stats` | Funding, open interest, mark and day volume for one coin (HIP-3-aware: xyz:CL, vntl:NVDA, ... route to their own dex), or every main-dex coin when omitted. Also answers get_market_stats. |
| `get_asset_positions` |  | `get_portfolio` | Open positions across main dex and HIP-3; pass coin to filter. Also answers get_portfolio. |
| `get_assets` |  | `get_asset_contexts`, `get_markets_info`, `get_perp_markets` | Every tradeable perp (main dex) with size decimals and max leverage. Also answers get_markets_info, get_perp_markets, get_asset_contexts. |
| `get_candles` |  | `get_candles_aggregated` | Candles for a coin and interval, lookback by count. Also answers get_candles_aggregated. For an explicit time window see get_price_history. |
| `get_coin_info` |  |  | Get detailed info for a specific coin. |
| `get_funding_history` |  |  | Venue funding rate history for one coin over a window (default last 7 days). |
| `get_funding_rate` |  |  | Current hourly funding for one coin, or the whole board sorted by it when omitted. |
| `get_historical_funding` |  | `get_user_funding_history` | Funding this ACCOUNT paid or received (not the venue rate; see get_funding_history). Also answers get_user_funding_history. |
| `get_l2_book` |  | `get_market_depth` | L2 order book for a coin. Also answers get_market_depth. |
| `get_liquidation_price` |  |  | Venue liquidation price for an open position (pass coin only), or HL's isolated-margin estimate for a hypothetical one (pass leverage + is_long). |
| `get_max_leverage` |  | `get_leverage` | Maximum allowed leverage for a coin (HIP-3-aware: xyz:CL, vntl:NVDA, ... route to their own dex). Also answers get_leverage. |
| `get_open_interest` |  |  | Open interest for a coin (HIP-3-aware), or the top 100 by OI when omitted. |
| `get_predicted_funding` |  |  | Predicted next funding per venue for one coin, or every main-dex coin when omitted. HIP-3 coins (xyz:, vntl:, km:, ...) have no cross-venue prediction on HL; this falls back to their current hourly rate, clearly labeled as such. |
| `get_price` |  | `get_coin_price` | Get current mid price for a coin. Also answers get_coin_price. |
| `get_price_history` |  |  | Candles for a coin over a time window (default last 100 hours). |
| `get_price_impact` |  | `get_slippage_estimate` | Walk the live L2 book for a trade and report avg fill, worst price, slippage and unfilled size. Also answers get_slippage_estimate. |
| `get_spot_markets` |  |  | Get all spot markets. |
| `get_user_defined_types` |  |  | Get user-defined perpetual types. |
| `leaderboard_get_markets` |  |  | Get Hyperliquid SM leaderboard market rankings by volume. |
| `market_get_asset_data` |  |  | Get comprehensive asset data: candles + funding + OI. |
| `market_get_funding_regime` |  |  | Get market-wide funding regime analysis (crowded trades). |
| `market_list_instruments` |  |  | List all tradable instruments (perps + spot). |
| `set_leverage` | YES |  | Set leverage for a coin (cross margin). |

## Account / orders / history (live-money execution excluded)

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `cancel_order` | YES |  | Cancel an open order by asset index and order ID. |
| `close_position` | YES |  | Close a position for a specific coin. |
| `get_24h_stats` |  |  | Get 24-hour statistics for a coin. |
| `get_account_summary` |  |  | Equity (main dex + HIP-3), available margin, withdrawable, notional, position count. |
| `get_api_rate_limits` |  |  | Get API rate limit status. |
| `get_deposits` |  |  | This account's deposits, from the non-funding ledger. |
| `get_historical_orders` |  |  | Get historical (filled + cancelled) orders for the configured user. |
| `get_max_trade_size` |  |  | Max notional this account can open on a coin, bounded by margin and checked against the book. |
| `get_network_stats` |  |  | Get network statistics. |
| `get_open_orders` |  | `get_frontend_open_orders`, `get_user_orders` | Open orders (frontend shape: trigger type, tp/sl), optionally filtered by coin. Also answers get_frontend_open_orders, get_user_orders. |
| `get_order_by_oid` |  | `get_order_status` | Order status by order ID. Also answers get_order_status. |
| `get_portfolio_pnl` |  |  | Unrealized pnl per open position plus HL's day/week/month/all-time pnl. |
| `get_portfolio_status` |  |  | Get portfolio status summary. |
| `get_referral` |  |  | Get referral code and statistics. |
| `get_rewards` |  |  | Get user rewards/earnings. |
| `get_risk_metrics` |  |  | Account leverage, cross margin ratio (1.0 = liquidation) and liquidation distance per main-dex position. |
| `get_server_time` |  |  | Get Hyperliquid server time. |
| `get_spot_balances` |  |  | Get spot token balances for the account. |
| `get_staking_info` |  |  | Get staking information. |
| `get_trade_history` |  | `get_recent_trades`, `get_user_fills` | This account's fills, newest last (HL has no public trade tape). Also answers get_user_fills, get_recent_trades. |
| `get_trading_permissions` |  |  | Get trading permissions for the account. |
| `get_transfers` |  | `get_transfer_history` | This account's internal, spot, sub-account and vault transfers. Also answers get_transfer_history. |
| `get_user_fees` |  | `get_user_fees_detailed` | Get user fee tiers and rates. Also answers get_user_fees_detailed. |
| `get_user_fills_by_time` |  |  | Get user fills within a time window (unix ms). |
| `get_user_orders_history` |  |  | Get user's order history with filtering. |
| `get_user_roles` |  |  | Get user roles and permissions. |
| `get_user_state` |  |  | Full raw frontend clearinghouse state (main dex only). For a normalized cross-dex view use get_asset_positions / get_account_summary. |
| `get_user_twist` |  |  | Get user staking (twist) information. |
| `get_validator_info` |  |  | Get validator information. |
| `get_vault_details` |  |  | Details for one vault by address. |
| `get_withdrawal_status` |  |  | Whether a withdrawal settled (by tx hash), or the 20 most recent withdrawals. |
| `get_withdrawals` |  |  | This account's withdrawals, from the non-funding ledger. |
| `query_order_by_cloid` |  |  | Order status by client order ID (0x-prefixed 16-byte hex). |

## Leaderboard / discovery

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `discovery_get_top_traders` |  |  | Top traders with pnl/roi for every window from Hyperliquid's public leaderboard. ROI ranking defaults to a $10k account floor. |
| `discovery_get_trader_state` |  |  | Get comprehensive state for multiple traders. |
| `leaderboard_get_top_traders` |  |  | Top traders from Hyperliquid's public leaderboard, ranked over a window, with their live positions. |
| `leaderboard_get_trader_positions` |  |  | Get open positions for a specific trader address. |

## Desk tools (services.strategies facade)

| tool | live-money | also answers (hidden aliases) | description |
|---|---|---|---|
| `desk_brief` |  |  | Full brief for one desk: spec, analytics, intel and latest thesis. mode=quick renders a short markdown summary; mode=full returns the raw structured payload. |
| `desk_list` |  |  | Every desk's one-line card: tagline, primary coin, mid, 1d return, latest call and track record. Start here before desk_brief. |
| `desk_refresh` |  |  | Kick off (or reuse) a desk's data+thesis refresh. Returns a job you can poll; the pipeline skips the LLM step unless the market moved, a high-relevance headline arrived, the thesis expired, or force=true. |
| `desk_submit_call` |  |  | Record your own trade call on a desk (author=user), journaled alongside the machine's theses for track-record comparison. |
| `desk_track_record` |  |  | Graded-thesis track record for one desk: hit rate, mean Brier, mean return, split machine vs user calls. |

