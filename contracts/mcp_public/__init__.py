"""The public MCP toolset: what a plugin / container install (PATHIEL_INSTALL=
public) lists in tools/list, and how each name reaches the handlers.

Why (2026-11-13): directory graders (Glama) scored the old public surface,
65 tools that were the operator's 60 plus five, low on disambiguation,
naming and scope: four price tools, three candle tools, six equity views,
"also answers X" aliases. This module is the scoped toolbelt: 30 tools,
every name verb_noun, one tool per job, each description saying when to use
it and which sibling covers the neighbouring job.

Nothing is removed. Every old name stays callable by name (the server keeps
its handler), it is only no longer listed. The operator's own install
(.mcp.json, live desks) lists TOOLS unchanged; this module never touches it.

Three ways a public name reaches a handler:

  RENAMES     new name -> one old tool, same arguments. The server treats
              the new name as that old one everywhere (_canonical), so the
              public safety gate, fee notes and output decoration are the
              old tool's, byte for byte.
  COMPOSITES  new name -> run(name, args, call): picks one or more old
              tools from the arguments and calls each through `call`,
              which is the server's own _call_tool (gate and decoration per
              inner call). Reads only, plus get_desk_record(grade=true),
              which writes the local desk journal like desk_grade.
  set_stop    a new live-money tool with its own handler (bracket.set_stop).

Pure: no server import. tests/test_mcp_public_toolset.py drives run() with a
fake `call`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# kind -> MCP annotations (2025-06-18), only the hints that differ from the
# spec defaults. Same buckets as the server's _annotate_tools.
KINDS = ("market_read", "account_read", "local_read", "live_money", "live_config",
         "local_write", "account_setup")
READ_KINDS = frozenset({"market_read", "account_read", "local_read"})


def annotations(kind: str) -> Dict[str, Any]:
    if kind == "live_money":
        return {"destructiveHint": True}
    if kind == "live_config":
        return {"destructiveHint": True, "idempotentHint": True}
    if kind == "account_setup":
        return {"destructiveHint": True}
    if kind == "local_write":
        return {"destructiveHint": False}
    return {"readOnlyHint": True}


NAME_RE = re.compile(r"^(get|list|size|place|set|cancel|close|sweep|refresh|submit|update|connect|join|leave)_[a-z]+(_[a-z]+)*$")

# new public name -> old tool name (same arguments, same handler)
RENAMES: Dict[str, str] = {
    "get_funding_regime": "market_get_funding_regime",
    "get_risk_limits": "risk_limits",
    "place_bracket": "place_bracket",
    "set_target": "set_target",
    "cancel_order": "cancel_order",
    "close_position": "close_position",
    "set_leverage": "set_leverage",
    "sweep_brackets": "bracket_sweep",
    "list_desks": "desk_list",
    "get_desk_brief": "desk_brief",
    "refresh_desk": "desk_refresh",
    "submit_desk_call": "desk_submit_call",
    "update_config": "config",
    "connect_wallet": "wallet_connect",
    "join_pool": "pool_join",
    "leave_pool": "pool_leave",
}

# Internal names for an old handler a public composite of the SAME name
# replaces on a public install (get_candles): the composite calls the old one
# through this name, never through its own (that would recurse). Callable,
# never listed; _canonical maps each to its old tool for gating.
INTERNAL_ALIASES: Dict[str, str] = {"get_candles_by_count": "get_candles"}

# composite name -> every old tool run() may call for it
COMPOSITE_INNER: Dict[str, Tuple[str, ...]] = {
    "get_market": ("get_price", "get_asset_context", "get_predicted_funding", "get_max_leverage"),
    "list_markets": ("market_list_instruments", "get_all_mids", "get_funding_rate",
                     "get_open_interest", "get_assets"),
    "get_candles": ("get_candles_by_count", "get_price_history"),
    "get_order_book": ("get_l2_book", "get_price_impact"),
    "get_account": ("account_overview", "wallet_account", "get_risk_metrics",
                    "get_portfolio_status", "get_user_fees", "get_api_rate_limits"),
    "get_positions": ("get_asset_positions", "get_liquidation_price"),
    "get_orders": ("get_open_orders", "get_historical_orders", "get_order_by_oid",
                   "query_order_by_cloid"),
    "get_history": ("get_trade_history", "get_user_fills_by_time", "get_historical_funding",
                    "get_deposits", "get_withdrawals", "get_withdrawal_status", "get_transfers"),
    "size_trade": ("risk_size_trade", "get_max_trade_size"),
    "get_status": ("state", "accounts"),
    "get_config": ("config",),
    "get_desk_record": ("desk_track_record", "desk_grade"),
    "get_pool_status": ("pool_status", "pool_report"),
}

# Tools with their own handler on every install (listed on public only).
OWN_HANDLER = frozenset({"set_stop"})

_COIN = {"type": "string", "description": "Coin: a main-dex perp by name (BTC, ETH) or a HIP-3 perp as dex:SYMBOL (xyz:GOLD, xyz:BRENTOIL)."}
_ADDRESS = {"type": "string", "description": "Any 0x address to read instead of the configured account (read-only; needs no key)."}
_LIMIT = {"type": "integer", "minimum": 1, "description": "Most rows to return."}
_MS = "Unix time in milliseconds."


def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        s["required"] = required
    return s


# name -> (kind, description, inputSchema or None for "the renamed tool's own")
# Order is the listing order: market, account, risk, trading, desks, setup, pool.
TOOLS: List[Tuple[str, str, str, Optional[Dict[str, Any]]]] = [
    # ── market data: no key, no account ────────────────────────────────────
    ("get_market", "market_read",
     "Get one coin's market snapshot in a single call: mid, mark and oracle price, current funding "
     "and the predicted next funding per venue, open interest, 24h volume, previous-day price and "
     "max leverage. Use it for any question about one coin right now. Not for many coins at once "
     "(list_markets), price over time (get_candles) or depth and slippage (get_order_book). "
     "Read-only; needs no key or account. A HIP-3 coin (xyz:GOLD) is read from its own dex. "
     "Returns {coin, mid: {price}, context: {funding, openInterest, markPx, oraclePx, midPx, "
     "dayNtlVlm, prevDayPx}, funding_next: {venues[]}, max_leverage}; a part the venue cannot "
     "answer carries its own error while the rest still returns, and an unknown coin returns "
     "{error} with isError true.",
     _schema({"coin": _COIN}, ["coin"])),
    ("list_markets", "market_read",
     "List many coins at once, one board per call, to scan or rank the market. view=instruments "
     "(default): tradable perps and spot, highest 24h volume first, each with max leverage, funding, "
     "open interest and mid; query, type and limit narrow this view only. view=mids: every mid "
     "price (coins narrows it). view=funding: every coin by hourly funding. view=open_interest: the "
     "top 100 by open interest. view=assets: size decimals and max leverage per main-dex perp, for "
     "order sizing. limit defaults to 50 (max 1000). For one coin's full snapshot use get_market "
     "instead; for price history use get_candles. Read-only; needs no key. Returns "
     "{instruments[], counts, matched} for instruments, a coin-to-price map for mids, and ranked "
     "rows for the other views; a venue failure returns {error} with isError true.",
     _schema({
         "view": {"type": "string", "enum": ["instruments", "mids", "funding", "open_interest", "assets"],
                  "default": "instruments", "description": "Which board to return."},
         "query": {"type": "string", "description": "view=instruments: keep names containing this text."},
         "type": {"type": "string", "enum": ["perp", "spot"], "description": "view=instruments: perps or spot only."},
         "coins": {"type": "array", "items": {"type": "string"}, "description": "view=mids: only these coins."},
         "limit": {"type": "integer", "minimum": 1, "description": "view=instruments: most rows to return."},
     })),
    ("get_candles", "market_read",
     "Get OHLCV candles for one coin as [{t, o, h, l, c, v}], oldest first, t in unix ms. Two "
     "modes: the last `count` candles at `interval` (default 100 x 1h), or a fixed window when "
     "start_time is given (end_time defaults to now; count is then ignored). interval is one of "
     "Hyperliquid's: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1w, 1M. Use for trend, "
     "levels, volatility and backtests. For the price right now use get_market; for depth use "
     "get_order_book. Read-only; needs no key. Returns the candle list.",
     _schema({
         "coin": _COIN,
         "interval": {"type": "string", "description": "Candle size: 1m, 5m, 15m, 1h, 4h, 1d, ... (default 1h)."},
         "count": {"type": "integer", "minimum": 1, "description": "How many recent candles (default 100; ignored with start_time)."},
         "start_time": {"type": "number", "description": f"Window start; switches to window mode. {_MS}"},
         "end_time": {"type": "number", "description": f"Window end (default now). {_MS}"},
     }, ["coin"])),
    ("get_order_book", "market_read",
     "Get the live L2 order book for one coin: levels [bids, asks], up to 20 each, every level "
     "{px, sz, n orders}. Give size (in coins) or notional_usd, not both, to also walk the book for a "
     "market order of that size: "
     "average fill, worst price, slippage % and any unfilled amount; is_buy picks the side "
     "(default buy). Use before a large entry or exit to judge slippage. For the mid price alone "
     "use get_market. Read-only; needs no key. Returns {coin, time, levels}, or {book, impact: "
     "{avg_fill_px, worst_px, slippage_pct, filled_usd, unfilled_usd, book_exhausted}} when a "
     "size is given; an unknown coin returns {error}.",
     _schema({
         "coin": _COIN,
         "size": {"type": "number", "description": "Order size in coins to simulate (or use notional_usd)."},
         "notional_usd": {"type": "number", "description": "Order size in USD to simulate (or use size)."},
         "is_buy": {"type": "boolean", "description": "Side of the simulated order (default true: buy)."},
     }, ["coin"])),
    ("get_funding_regime", "market_read",
     "Get the market-wide funding regime: whether longs or shorts are paying to hold (crowded) "
     "overall and per asset class (crypto, equity, commodity), with the coins whose funding is most "
     "extreme (funding as the hourly rate). asset_class narrows the coin list to one class; limit "
     "caps it (default 25, max 1000), most extreme first. Use to judge crowding "
     "before taking a side. For one coin's funding use get_market; for every coin ranked by funding "
     "use list_markets view=funding. Read-only; needs no key. Returns {regime, regimes_by_class, "
     "assets[]: {asset, asset_class, funding_rate, regime, oi, volume_24h}, assets_total}.",
     None),
    # ── the account: reads ─────────────────────────────────────────────────
    ("get_account", "account_read",
     "Get the account's money in one call: total equity across the main perp dex, every HIP-3 dex "
     "and spot USDC, withdrawable, whether it can trade (and what blocks it), open positions in "
     "brief, plus risk: account leverage, cross margin ratio (1.0 = liquidation) and distance to "
     "liquidation. detail=full adds PnL per window (day, week, month, all time), the fee tier and "
     "API rate-limit use, for the configured account only. address reads any wallet instead. Use "
     "for balance and liquidation-risk questions. For each position in detail use get_positions; "
     "for what the risk profile allows use get_risk_limits. Needs a configured account or an "
     "address, never a key; with neither it returns {error, no_account: true}. Returns {account, "
     "risk}, plus {pnl, fees, rate_limits} with detail=full (ignored with address).",
     _schema({
         "address": _ADDRESS,
         "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary",
                    "description": "summary: equity, margin, risk. full: also PnL history, fees, rate limits (configured account only)."},
     })),
    ("get_positions", "account_read",
     "Get open perp positions on every dex (main and HIP-3): coin, signed size (negative = short), "
     "entry, position value, unrealized PnL, margin, leverage and liquidation price. coin narrows "
     "to one position and, for the configured account, adds the venue's liquidation price for it. "
     "address reads any wallet. Use to see what is open before placing, moving or closing anything. "
     "For balances use get_account; for the resting stop and target use get_orders. Needs a "
     "configured account or an address; with neither it returns {error, no_account: true}. "
     "Returns {count, positions[]} (count 0 when flat), or {positions, liquidation: {source, "
     "liquidation_price}} with coin.",
     _schema({"coin": _COIN, "address": _ADDRESS})),
    ("get_orders", "account_read",
     "Get orders, one query per call. status=open (default): resting orders on every dex with "
     "order type (Stop Market, Take Profit Limit, Limit), trigger price, size and reduce-only flag; "
     "this is how to see a position's stop and target. status=history: filled and cancelled "
     "orders, newest first. oid or cloid looks up one order and overrides status. coin and limit "
     "narrow the list (limit default 100 open, 50 history, max 2000); address reads any wallet. Use before cancel_order (it needs the oid) and to "
     "check a position is protected. For executed trades use get_history. Needs a configured "
     "account or an address; with neither it returns {error, no_account: true}. Returns {count, "
     "orders[]: {coin, oid, side, sz, limitPx, triggerPx, orderType, reduceOnly}} or one order's "
     "status.",
     _schema({
         "status": {"type": "string", "enum": ["open", "history"], "default": "open",
                    "description": "open: resting now. history: filled and cancelled."},
         "coin": _COIN,
         "oid": {"type": "integer", "description": "One order by order id (overrides status)."},
         "cloid": {"type": "string", "description": "One order by client order id, 0x + 32 hex (overrides status)."},
         "address": _ADDRESS,
         "limit": _LIMIT,
     })),
    ("get_history", "account_read",
     "Get the account's ledger, one kind per call. kind=fills (default): executed trades with "
     "price, size, side, fee and closed PnL; give start_time (and end_time) for a time "
     "window on the configured account, otherwise the latest fills, narrowed by coin and limit. "
     "kind=funding: funding paid or received per coin between start_time and end_time. "
     "kind=deposits, withdrawals or transfers: money in, out and between perp, spot, sub-accounts "
     "and vaults since start_time; withdrawal_id checks one withdrawal's settlement. pathiel cannot "
     "move funds (its API wallet can trade, never withdraw), so these are records only. address "
     "reads any wallet. Rows come oldest first; limit (default 100, max 2000) keeps the newest. Use "
     "for PnL review and audits; "
     "for resting or cancelled orders use get_orders. Needs a configured account or an address; "
     "with neither it returns {error, no_account: true}. Returns {count, fills[]} for fills and "
     "{count, rows[]} for the other kinds.",
     _schema({
         "kind": {"type": "string", "enum": ["fills", "funding", "deposits", "withdrawals", "transfers"],
                  "default": "fills", "description": "Which ledger."},
         "coin": {**_COIN, "description": "kind=fills or funding: only this coin."},
         "start_time": {"type": "number", "description": f"From this time. {_MS}"},
         "end_time": {"type": "number", "description": f"Until this time (fills and funding). {_MS}"},
         "limit": _LIMIT,
         "address": _ADDRESS,
         "withdrawal_id": {"type": "string", "description": "kind=withdrawals: the tx hash of one withdrawal to check."},
     })),
    # ── risk and sizing ────────────────────────────────────────────────────
    ("get_risk_limits", "account_read",
     "Get what this account may trade under its saved risk profile: positions at once, dollars at "
     "risk per trade at the stop, max leverage, the minimum equity to trade and whether each book "
     "can trade now, with a one-line headline. equity_usd and preset answer what-if questions (a "
     "different account size or preset) and change nothing. Use to explain the limits, or before "
     "changing the profile with update_config. To size one specific trade use size_trade; for the "
     "raw config use get_config. Needs a configured account unless equity_usd is given (else "
     "{error, no_account: true}). A book is one strategy slot the profile "
     "allows (desk or manual). Returns {headline, can_trade, "
     "max_open_positions, risk_per_trade_usd, max_leverage, books[], notes[]}.",
     None),
    ("size_trade", "account_read",
     "Size one trade before placing it, with the rules place_bracket uses: size, notional, "
     "leverage, margin, dollars at risk at the stop, payoff at each target and the liquidation "
     "price, plus the most this account's margin allows on the coin. The side follows the levels: "
     "stop below entry is a long, above is a short. targets adds a payoff per target price; "
     "leverage overrides the default (capped by the profile); equity_usd sizes a what-if account "
     "and skips the margin check. Use before every place_bracket; never size a trade by hand. "
     "Places nothing. Needs a configured account unless equity_usd is given. Returns {sizing: "
     "{side, size, notional_usd, leverage, margin_usd, risk_usd, risk_pct, targets[], liq_px, reason, warnings[]}, "
     "max_by_margin}, or the sizing alone for a what-if. A trade that cannot be sized (stop at the "
     "entry, equity too small) comes back can_trade false with size null and the reason.",
     _schema({
         "coin": _COIN,
         "entry_px": {"type": "number", "description": "Planned entry price."},
         "stop_px": {"type": "number", "description": "Planned stop: below entry for a long, above for a short."},
         "leverage": {"type": "number", "description": "Leverage to size at (capped by the profile)."},
         "targets": {"type": "array", "items": {"type": "number"}, "description": "Target prices to report the payoff for."},
         "equity_usd": {"type": "number", "description": "What-if equity in USD instead of the account's own."},
     }, ["coin", "entry_px", "stop_px"])),
    # ── trading: live money ────────────────────────────────────────────────
    ("place_bracket", "live_money",
     "Open one trade as a bracket: an entry plus an exact stop-loss and take-profit, sized by the "
     "risk profile exactly as size_trade reports. The side follows the levels (stop below the "
     "entry or mark is a long). entry_px rests a limit order and the stop and target attach when "
     "it fills; omit entry_px to enter at market now. The take-profit is placed 0.5% inside tp_px "
     "so it fills before the level (the result shows both). leverage defaults to the profile's "
     "max and is set on the coin first. Refused, with nothing sent: reward to risk under "
     "min_r (never below 2R), a stop outside half the distance to liquidation, a coin not listed on "
     "this network, or a second position or resting entry on the coin, unless add=true, which "
     "tops an open same-side position up to full size with a stop no wider than the current one. "
     "dry_run=true runs every check and returns the sizing without placing. Call only when the "
     "user asked for this trade; size it first with size_trade, adjust it later with set_stop or "
     "set_target, exit with close_position. Needs a trading key. Returns {ok, placed, mode, "
     "entry_px, stop_px, tp_px, size, leverage, result, network, builder_fee}; a refusal is "
     "ok=false, placed=false with the reason. If a market entry fills but the venue refuses the "
     "stop, the result is ok=false, placed=true, reason \"FILLED WITHOUT A STOP\": place one at "
     "once with set_stop.",
     None),
    ("set_target", "live_money",
     "Move an open position's take-profit to tp_px. One limit take-profit for the whole position "
     "goes on first, then every older target on the coin is cancelled, so the position always has "
     "a target and a refused new order leaves the old one in place. Refused: no open position, or "
     "a target on the losing side of the mark (it would fill at once). Never changes the stop or "
     "the size. Use to raise or lower a target. To move the stop use set_stop; to exit now use "
     "close_position. Needs a trading key. Returns {ok, tp_px, size, replaced: old target prices}; "
     "a refusal is {ok: false, reason} with isError true and nothing sent.",
     None),
    ("set_stop", "live_money",
     "Move an open position's stop-loss tighter, toward the mark, to lock in gains or cut risk; "
     "never wider. One stop for the whole position goes on first, then every older stop on the coin "
     "is cancelled, so the position is never unprotected and a refused new order leaves the old one "
     "in place. Refused: no open position, a stop past the mark (it would fire at once), or a stop "
     "wider than or equal to the current one (a long's new stop must be above it, a short's below). "
     "With no stop at all, any stop on the right side of the mark is placed. Never changes the "
     "target or the size. To widen a stop, close and re-enter with place_bracket; to move the "
     "target use set_target. Needs a trading key. Returns {ok, stop_px, size, mark, replaced: old "
     "stop prices}; a refusal is {ok: false, reason, mark, current_stop} with isError true and "
     "nothing sent.",
     _schema({
         "coin": _COIN,
         "stop_px": {"type": "number", "description": "New stop: above the current stop for a long, below it for a short, and on the safe side of the mark."},
     }, ["coin", "stop_px"])),
    ("cancel_order", "live_money",
     "Cancel one resting order by its order id. order_id comes from get_orders; pass coin with it "
     "(asset, the numeric index, only when coin is unknown). Cancelling a position's only stop "
     "leaves it unprotected: to move a stop use set_stop, and to clear leftover orders on a flat "
     "coin use sweep_brackets. Use for a stale entry or a mistaken order. Needs a trading key. Returns "
     "{ok}; an order already filled or cancelled, or a missing coin and asset, returns the "
     "venue's {error} with isError true.",
     None),
    ("close_position", "live_money",
     "Close a coin's entire open position now with one market order for the full size; partial "
     "closes are not supported, and like any market order it pays the spread and slippage of the "
     "book. A flat coin returns ok=true with noop already_flat and sends nothing. Closes are "
     "never refused for a delisted coin. Afterwards run "
     "sweep_brackets on the coin to cancel the stop and target that no longer protect anything. "
     "Use when the user asks to exit now; to exit at a price instead, move the level with set_stop "
     "or set_target. Needs a trading key. Returns {ok, result: {total_sz, avg_px}} or {error} "
     "with isError true.",
     None),
    ("set_leverage", "live_money",
     "Set a coin's leverage, a whole number from 1 up to the coin's max (default 5 when omitted). "
     "It is capped by the risk profile's max_leverage and refused "
     "above it (raise the cap with update_config first). An open position keeps its own margin "
     "type (cross or isolated); a venue refusal, such as leverage too low for the margin in use, "
     "comes back as an error. place_bracket already sets leverage when given one, so call this "
     "alone only to change an open position or prepare a coin. Confirm with get_positions. Needs a "
     "trading key. Returns {ok, coin, leverage} or the error.",
     None),
    ("sweep_brackets", "live_money",
     "Tidy one coin's protective orders (one coin per call). On a flat coin it cancels stops and "
     "targets left behind "
     "after a stop or target fired (a resting entry's own attached stop and target are kept) and "
     "lists resting entries. With an open position it checks the stop covers the full size and "
     "folds several stops or targets at one price (one per fill) into a single pair, placing the "
     "new order before cancelling the old ones. Never opens, closes or moves a level. Run it after "
     "every close or fill; to move a level use set_stop or set_target. Needs a trading key. "
     "Returns {ok, position, stop_px[], tp_px[], stop_covers} with an open position, or {ok, "
     "position: 0, cancelled_stranded, resting_entries[]: {oid, px, sz}} when flat; ok=false with "
     "reason \"POSITION NOT FULLY COVERED BY A STOP\" means place one now with set_stop.",
     None),
    # ── desks: the written calls Claude trades from ────────────────────────
    ("list_desks", "local_read",
     "List every trading desk (crude oil, metals, ...) as a one-line card: id, primary coin, mid, "
     "1-day return, the latest call's side and one-liner, how old the thesis is, the next catalyst "
     "and the track record. Use first to pick a desk, then get_desk_brief for its full call; for "
     "raw market listings use list_markets instead. Reads the local desk journal and live mids; "
     "needs no key. Returns {count, desks[]}.",
     _schema({})),
    ("get_desk_brief", "local_read",
     "Get one desk's current trade call to trade from: side, entry, stop and targets, the "
     "reasoning, and your_size, the size this account would take at those levels under its risk "
     "profile and desk share. mode=quick (default) is short markdown with the call first; "
     "mode=full adds the evidence (news, levels, scenarios). stale lists what is out of date; when "
     "the thesis is stale or its target was hit, call refresh_desk. Use before placing a desk "
     "trade with place_bracket. Not for the desk's past accuracy (get_desk_record) or the list of "
     "desks (list_desks). Read-only; needs no key. An unknown desk id returns {error: no such "
     "desk}. Returns {desk, mode, markdown, your_size: {side, size, notional_usd, leverage, "
     "margin_usd}, stale[]}.",
     None),
    ("refresh_desk", "local_write",
     "Write a NEW thesis for one desk: pulls fresh market data, news, policy and odds, then makes "
     "one local Claude Code call (about a minute, billed to the local Claude Code plan; needs the "
     "claude CLI on PATH, else it fails fast). A refresh already running for the desk is joined, "
     "not repeated; force=true refreshes even when the desk's data is still fresh. Writes only the local desk journal; "
     "places no order. Use when get_desk_brief says the thesis is stale or its target was hit; "
     "otherwise read the current call with get_desk_brief. Returns the new call or the error.",
     None),
    ("submit_desk_call", "local_write",
     "Record the user's own trade call on a desk in the local journal (author=user), graded later "
     "beside the machine's theses in get_desk_record. call needs side (long, short, flat or pair), "
     "instrument, a one-liner of at most 160 characters, confidence from 0 to 1 and the "
     "invalidation; entry_px (or an entry_zone [lo, hi]), stop_px, targets and leverage are "
     "optional. scenarios, when given, are {name, probability, move_lo_pct, move_hi_pct (percent "
     "moves), description, triggers[]} whose probabilities sum to 1; a call that breaks a rule "
     "returns {error} naming it. Places no order and changes no config. Use when the user "
     "states a view they want tracked; to trade it use place_bracket. Returns the journaled call.",
     None),
    ("get_desk_record", "local_write",
     "Get a desk's graded track record: calls graded, direction hit rate, mean Brier score, mean "
     "return, results by outcome, machine calls vs the user's own, and trades taken against each "
     "call. grade=true first grades expired calls and closed trades (writes the local journal, "
     "places no order); use it when calls have expired since the last grading, else leave it off "
     "and read. Use to judge whether a desk's calls deserve trust; for the current call use "
     "get_desk_brief. A desk with nothing graded yet returns n_graded 0. Returns {desk, track_record}, or {graded, "
     "record} with grade=true.",
     _schema({
         "desk": {"type": "string", "description": "Desk id from list_desks (oil, metals, ...)."},
         "grade": {"type": "boolean", "default": False, "description": "Grade expired calls and trades before reading."},
     }, ["desk"])),
    # ── setup ──────────────────────────────────────────────────────────────
    ("get_status", "local_read",
     "Start here. Get the install's status: network (mainnet or testnet), whether trading is "
     "enabled (an API wallet key is configured) or read-only, the builder fee, the trading mode, "
     "equity with open positions in brief, and the registered accounts with the one that trades. "
     "Use first in a session and whenever an order is refused as read-only. For full balances and "
     "liquidation risk use get_account; for settings use get_config. Read-only. With no wallet "
     "connected it still answers, showing trading as read-only; a venue that cannot be reached "
     "shows in the equity fields. Returns {status: {mode, equity, equity_by_dex, positions, "
     "install: {network, trading, builder_fee}}, accounts: {count, accounts[], bot_trades_with}}.",
     _schema({})),
    ("get_config", "local_read",
     "Get the trading config from ~/.pathiel/.agent-config.json: mode (LIVE or OFF), HIP-3 on or "
     "off, coin and HIP-3 dex allow and block lists, the risk profile when set, and every other "
     "top-level key. Changes nothing. Use before update_config to see current values and key "
     "names; for what the risk profile allows in dollars use get_risk_limits, and for whether "
     "trading is enabled use get_status. A missing file reads as the defaults. Returns the config "
     "object.",
     _schema({})),
    ("update_config", "live_config",
     "Change the trading config on this machine; it applies at once to every later order. Pass "
     "only the keys to change: mode (LIVE or OFF), enable_hip3, coin_allowlist, coin_blocklist, "
     "hip3_dex_allowlist, hip3_dex_blocklist, or any other top-level key get_config shows, such as "
     "risk_profile; nested objects merge into the current ones and a list replaces the old list. "
     "mode is a label only: OFF does "
     "not stop the live-money tools. A call with nothing to change is refused. Read current values with get_config first, and call only when the user asked for "
     "this exact change. Not for one coin's leverage (set_leverage) or for sizing questions "
     "(get_risk_limits). Returns the updated config.",
     None),
    ("connect_wallet", "account_setup",
     "Connect the user's Hyperliquid wallet so pathiel can trade: makes a new API wallet key on "
     "this machine, opens a local page where the user's browser wallet approves it with one "
     "signature, and saves the key to ~/.pathiel/.env.local. The API wallet can trade and never "
     "withdraw. status=true reports a connect in progress (waiting, done or failed) and starts "
     "nothing; cancel=true stops a waiting one; replace=true swaps a key already set, only when the "
     "user said so; open_browser=false prints the page link instead of opening it. Call only when "
     "the user asked; get_status shows whether trading is enabled. It returns at once and does not "
     "wait for the signature: poll with status=true. With a key already set it keeps that key "
     "unless replace=true. A failure returns {ok: false, state: failed, code, error, next} saying "
     "what to do. Returns the connect state (waiting, done or failed) and the "
     "page link.",
     None),
    # ── research pool (opt-in) ─────────────────────────────────────────────
    ("get_pool_status", "account_read",
     "Get this account's research pool status (the pool is mainnet only): joined or not (and when), "
     "what is published and "
     "what stays private, the pool's latest counts, the scoreboard URL and how to join or leave. "
     "report=true adds your own graded report from your public fills since joining: win rate, "
     "mean R with ranges, labelled fluke or edge (members only). Use before join_pool or leave_pool and to see your grade; not for market data or "
     "balances (get_account) or your fills (get_history). Reads local files (the report also reads your public fills); signs and "
     "publishes nothing. Returns the status object, or "
     "{status, report}.",
     _schema({"report": {"type": "boolean", "default": False, "description": "Add your own graded trade report (members only)."}})),
    ("join_pool", "local_write",
     "Join the research pool, which grades your public fills on a public scoreboard. Two steps: "
     "without confirm it shows the exact statement and signs nothing; with confirm=true and the "
     "joined_at that first call returned, it SIGNS that statement with your key and writes the "
     "member file for a pull request. No order is placed and no money moves. Call only when the "
     "user asked to join, and confirm only after they read the statement. Check get_pool_status "
     "first. Needs a trading key and mainnet: on testnet it returns {error, code: testnet} and "
     "signs nothing. The user opens the pull request; leave_pool undoes it. Returns "
     "the statement, then the signed member file path.",
     None),
    ("leave_pool", "local_write",
     "Leave the research pool. Two steps: without confirm it shows the exact statement and signs "
     "nothing; with confirm=true and the left_at that first call returned, it SIGNS that statement "
     "with your key and writes the leave file for a pull request. No order is placed and no money "
     "moves. Call only when the user asked to leave, and confirm only after they read the "
     "statement. Needs a trading key and mainnet: on testnet it returns {error, code: testnet} and "
     "signs nothing. Check get_pool_status first. Returns the statement, then the signed leave file path.",
     None),
]

# Descriptions for a renamed tool's parameters that its old schema left bare
# (graders read them; so does the model). Applied only where none exists.
PARAM_DOCS: Dict[str, str] = {
    "coin": _COIN["description"],
    "entry_px": "Limit entry price: the order rests there and the stop and target attach on fill. Omit to enter at market.",
    "stop_px": "Stop-loss price: below the entry for a long, above it for a short.",
    "tp_px": "Take-profit price: above the entry for a long, below it for a short.",
    "leverage": "Leverage for the coin, capped by the risk profile's max leverage.",
    "min_r": "Least reward-to-risk to accept; never below the 2R floor.",
    "dry_run": "true: size and check the order, place nothing.",
    "order_id": "The order id (oid) from get_orders.",
    "equity_usd": "What-if equity in USD instead of the account's own.",
    "preset": "What-if risk preset instead of the saved one.",
    "notes": "Free-text notes kept with the call.",
    "enable_hip3": "Allow HIP-3 markets (xyz:, vntl:, ...).",
    "hip3_dex_allowlist": "Only these HIP-3 dexes (empty: all).",
    "hip3_dex_blocklist": "Never these HIP-3 dexes.",
    "coin_allowlist": "Only these coins (empty: all).",
    "coin_blocklist": "Never these coins.",
}

# Same parameter name, different meaning per tool: these win over PARAM_DOCS
# and replace a terse old description.
PARAM_DOCS_BY_TOOL: Dict[Tuple[str, str], str] = {
    ("get_desk_brief", "mode"): "quick (default): short markdown, the call first. full: adds the evidence.",
    ("get_desk_brief", "desk"): "Desk id from list_desks (oil, metals, ...).",
    ("refresh_desk", "desk"): "Desk id from list_desks (oil, metals, ...).",
    ("refresh_desk", "force"): "true: refresh even when a recent refresh exists.",
    ("submit_desk_call", "desk"): "Desk id from list_desks (oil, metals, ...).",
    ("update_config", "mode"): "Agent mode label, LIVE or OFF, read by the preflight check and metrics. The live-money tools here do not refuse on OFF.",
    ("place_bracket", "coin"): _COIN["description"],
    ("cancel_order", "coin"): "The order's coin, as in get_orders (BTC or xyz:GOLD).",
    ("set_leverage", "leverage"): "Whole number from 1 to the coin's max, at most the profile cap (default 5 when omitted).",
    ("set_leverage", "coin"): _COIN["description"],
    ("close_position", "coin"): _COIN["description"],
    ("set_target", "coin"): _COIN["description"],
    ("set_target", "tp_px"): "New target: above the mark for a long, below it for a short.",
    ("sweep_brackets", "coin"): _COIN["description"] + " One coin per call.",
    ("cancel_order", "asset"): "Numeric asset index, only when coin is unknown.",
    ("place_bracket", "add"): "Top up an open same-side position to full size; the new stop may not be wider than the current one.",
}

NAMES: List[str] = [t[0] for t in TOOLS]
KIND: Dict[str, str] = {t[0]: t[1] for t in TOOLS}


def build_tools(old_schema: Callable[[str], Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The tools/list entries. old_schema(old_name) gives a renamed tool's own
    inputSchema, so a rename takes exactly the arguments it always did."""
    out = []
    for name, kind, desc, schema in TOOLS:
        if schema is None:
            schema = old_schema(RENAMES[name])
            for key, prop in (schema.get("properties") or {}).items():
                if not isinstance(prop, dict):
                    continue
                if (name, key) in PARAM_DOCS_BY_TOOL:
                    prop["description"] = PARAM_DOCS_BY_TOOL[(name, key)]
                elif not prop.get("description") and key in PARAM_DOCS:
                    prop["description"] = PARAM_DOCS[key]
        if kind == "live_money" and not desc.startswith("LIVE MONEY"):
            desc = "LIVE MONEY: acts on the real account. " + desc
        out.append({"name": name, "description": desc, "inputSchema": schema,
                    "annotations": annotations(kind)})
    return out


# ── composites ──────────────────────────────────────────────────────────────

Call = Callable[[str, Dict[str, Any]], Tuple[str, bool]]


def _clean(args: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None and v != ""}


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _error_of(obj: Any) -> Optional[str]:
    if isinstance(obj, dict) and obj.get("error") not in (None, "", False):
        return str(obj["error"])
    return None


def _merge(parts: Dict[str, Tuple[str, bool]], head: Dict[str, Any]) -> str:
    """Several inner results under their keys. A no-account answer from any
    part, or an error from every part, becomes the whole answer's error so
    the client sees isError."""
    out = dict(head)
    errors = []
    for key, (text, is_error) in parts.items():
        obj = _parse(text)
        out[key] = obj
        if isinstance(obj, dict) and obj.get("no_account"):
            return json.dumps(obj)
        if is_error:
            errors.append(_error_of(obj) or f"{key} failed")
    if errors and len(errors) == len(parts):
        out["error"] = errors[0]
    return json.dumps(out, default=str)


def _pick(args: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return _clean({k: args.get(k) for k in keys})


def plan(name: str, args: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """[(key, old tool, its arguments)] run() will call for name/args. One
    entry with key '' means "return that tool's answer unchanged"."""
    a = args or {}
    coin = a.get("coin")
    addr = a.get("address")
    if name == "get_market":
        c = {"coin": coin}
        return [("mid", "get_price", c), ("context", "get_asset_context", c),
                ("funding_next", "get_predicted_funding", c), ("max_leverage", "get_max_leverage", c)]
    if name == "list_markets":
        view = a.get("view") or "instruments"
        if view == "mids":
            return [("", "get_all_mids", _pick(a, "coins"))]
        if view == "funding":
            return [("", "get_funding_rate", {})]
        if view == "open_interest":
            return [("", "get_open_interest", {})]
        if view == "assets":
            return [("", "get_assets", {})]
        return [("", "market_list_instruments", _pick(a, "query", "type", "limit"))]
    if name == "get_candles":
        if a.get("start_time") is not None:
            return [("", "get_price_history", _pick(a, "coin", "interval", "start_time", "end_time"))]
        return [("", "get_candles_by_count", _pick(a, "coin", "interval", "count"))]
    if name == "get_order_book":
        book = ("book", "get_l2_book", {"coin": coin})
        if a.get("size") is None and a.get("notional_usd") is None:
            return [("", "get_l2_book", {"coin": coin})]
        impact = _clean({"coin": coin, "size": a.get("size"), "notionalUsd": a.get("notional_usd"),
                         "is_buy": a.get("is_buy")})
        return [book, ("impact", "get_price_impact", impact)]
    if name == "get_account":
        if addr:
            return [("account", "wallet_account", {"address": addr}),
                    ("risk", "get_risk_metrics", {"user": addr})]
        parts = [("account", "account_overview", {}), ("risk", "get_risk_metrics", {})]
        if a.get("detail") == "full":
            parts += [("pnl", "get_portfolio_status", {}), ("fees", "get_user_fees", {}),
                      ("rate_limits", "get_api_rate_limits", {})]
        return parts
    if name == "get_positions":
        if coin and not addr:
            return [("positions", "get_asset_positions", {"coin": coin}),
                    ("liquidation", "get_liquidation_price", {"coin": coin})]
        return [("", "get_asset_positions", _clean({"coin": coin, "user": addr}))]
    if name == "get_orders":
        if a.get("oid") is not None:
            return [("", "get_order_by_oid", _clean({"oid": a.get("oid"), "user": addr}))]
        if a.get("cloid"):
            return [("", "query_order_by_cloid", _clean({"cloid": a.get("cloid"), "user": addr}))]
        if a.get("status") == "history":
            return [("", "get_historical_orders", _clean({"coin": coin, "limit": a.get("limit"), "user": addr}))]
        return [("", "get_open_orders", _clean({"coin": coin, "limit": a.get("limit"), "user": addr}))]
    if name == "get_history":
        kind = a.get("kind") or "fills"
        start, end, limit = a.get("start_time"), a.get("end_time"), a.get("limit")
        if kind == "fills":
            if start is not None and not addr:
                return [("", "get_user_fills_by_time", _clean({"start_time": start, "end_time": end}))]
            return [("", "get_trade_history", _clean({"coin": coin, "limit": limit, "user": addr}))]
        if kind == "funding":
            return [("", "get_historical_funding", _clean({"coin": coin, "startTime": start, "endTime": end,
                                                           "limit": limit, "user": addr}))]
        if kind == "withdrawals" and a.get("withdrawal_id"):
            return [("", "get_withdrawal_status", _clean({"withdrawal_id": a.get("withdrawal_id"), "user": addr}))]
        old = {"deposits": "get_deposits", "withdrawals": "get_withdrawals", "transfers": "get_transfers"}.get(kind)
        if old is None:
            return []
        return [("", old, _clean({"startTime": start, "limit": limit, "user": addr}))]
    if name == "size_trade":
        sizing = ("sizing", "risk_size_trade",
                  _pick(a, "coin", "entry_px", "stop_px", "leverage", "targets", "equity_usd"))
        if a.get("equity_usd") is not None:
            return [("", "risk_size_trade", sizing[2])]
        try:
            is_buy = float(a.get("entry_px")) > float(a.get("stop_px"))
        except (TypeError, ValueError):
            return [("", "risk_size_trade", sizing[2])]
        return [sizing, ("max_by_margin", "get_max_trade_size", {"coin": coin, "is_buy": is_buy})]
    if name == "get_status":
        return [("status", "state", {}), ("accounts", "accounts", {})]
    if name == "get_config":
        return [("", "config", {})]
    if name == "get_desk_record":
        desk = {"desk": a.get("desk")}
        if a.get("grade"):
            return [("graded", "desk_grade", desk), ("record", "desk_track_record", desk)]
        return [("", "desk_track_record", desk)]
    if name == "get_pool_status":
        if a.get("report"):
            return [("status", "pool_status", {}), ("report", "pool_report", {})]
        return [("", "pool_status", {})]
    raise KeyError(name)


def run(name: str, args: Dict[str, Any], call: Call) -> str:
    """Answer a composite tool by calling the old tools plan() names."""
    steps = plan(name, args or {})
    if not steps:
        return json.dumps({"error": f"{name}: nothing matches those arguments"})
    if len(steps) == 1 and steps[0][0] == "":
        text, _ = call(steps[0][1], steps[0][2])
        return text
    head: Dict[str, Any] = {}
    if name == "get_market":
        head["coin"] = (args or {}).get("coin")
    parts = {key: call(old, sub) for key, old, sub in steps}
    return _merge(parts, head)


def validate_update_config(args: Dict[str, Any]) -> Optional[str]:
    """update_config with nothing to change would be a read through a write
    tool: refuse and point at get_config."""
    if not _clean(args or {}):
        return json.dumps({"error": "update_config needs at least one field to change; "
                                    "to read the config use get_config"})
    return None
