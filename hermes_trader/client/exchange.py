"""Hyperliquid exchange client — order placement using official SDK.

This module uses the hyperliquid-python-sdk for all authenticated operations:
- ECDSA signing (handled internally by the SDK)
- Order placement, trigger orders, leverage updates, cancel orders
- ATR calculation on HL candles

The SDK handles:
- msgpack encoding of actions
- ECDSA secp256k1 signing with Agent typed-data domain
- Keccak256 hashing for connection IDs
- EIP-712 typed data for Exchange domain signing
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional, Tuple

# Hyperliquid rejects any order below $10 notional. Target a small buffer above
# it so the IOC price offset and mark-vs-limit rounding can't dip under.
MIN_ORDER_USD = 10.5

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import (
    OrderType,
    TriggerOrderType,
)

from hermes_trader.client.hl_client import HL_API, fetch_hl_candles

logger = logging.getLogger(__name__)

# ── Environment ────────────────────────────────────────────────────────────────

HL_WALLET = os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")
HL_MASTER = os.environ.get("HYPERLIQUID_MASTER_ADDRESS", "")
PRIVATE_KEY_HEX = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "")

# Unified account: MASTER holds funds, WALLET signs orders
IS_AGENT = bool(HL_MASTER and HL_WALLET and HL_MASTER.lower() != HL_WALLET.lower())
HL_ACCOUNT = HL_MASTER if IS_AGENT else HL_WALLET

HL_LEVERAGE = 5  # 5x cross margin


_exchange_instance = None  # Singleton instance

def _resolve_perp_dexs() -> Optional[list]:
    """Discover HIP-3 perpDex names so the SDK can resolve colon-namespaced coins.

    Only invoked when HIP-3 is enabled via .agent-config.json `enable_hip3`.
    Returns None when disabled so the SDK uses its default (main perp dex only).

    CRITICAL: the SDK treats `perp_dexs` as *exclusive* — if you pass a list,
    it loads ONLY those dexes and drops the main perp universe. The empty
    string `""` is the sentinel for the main dex. So we must prepend `""` to
    keep BTC/ETH/etc. resolvable alongside the HIP-3 namespaced coins.
    """
    try:
        from hermes_trader.agents.config_store import read_agent_config
        if not read_agent_config().get("enable_hip3"):
            return None
        from hermes_trader.client.universe import list_hip3_dexes
        hip3 = list_hip3_dexes()
        return [""] + hip3 if hip3 else None
    except Exception as e:
        logger.warning(f"[_resolve_perp_dexs] HIP-3 dex discovery failed: {e}")
        return None


def _make_exchange() -> Exchange:
    """Create or reuse Exchange client singleton (avoids WebSocket connection limit)."""
    global _exchange_instance
    if _exchange_instance is not None:
        return _exchange_instance

    if not PRIVATE_KEY_HEX:
        raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")

    key_hex = PRIVATE_KEY_HEX
    if key_hex.startswith("0x"):
        key_hex = key_hex[2:]

    # The SDK uses eth_account for signing
    acct = Account.from_key(key_hex)

    # For unified accounts with agent wallet:
    # - WALLET signs orders
    # - MASTER holds funds
    account_address = HL_WALLET if IS_AGENT else None

    # perp_dexs= teaches the SDK the HIP-3 dex list at init so name_to_asset
    # can resolve `xyz:NVDA` etc. when enable_hip3 is on. With it None the
    # SDK behaves exactly as before.
    _exchange_instance = Exchange(
        wallet=acct,
        base_url=HL_API,
        account_address=account_address,
        perp_dexs=_resolve_perp_dexs(),
    )
    return _exchange_instance


_info_instance: Optional[Info] = None


def _get_info() -> Info:
    """Get (or lazily create) the shared HTTP-only Info client.

    skip_ws=True: the callers only use REST methods (meta, all_mids, l2,
    fills, ...), so no WebSocket connection is opened.
    """
    global _info_instance
    if _info_instance is None:
        _info_instance = Info(skip_ws=True, perp_dexs=_resolve_perp_dexs())
    return _info_instance


def get_coin_index(coin: str) -> Tuple[int, int, int]:
    """Resolve a coin name to (asset_index, sz_decimals, px_decimals) via the SDK meta endpoint.

    Searches the main perp universe first, then (for HIP-3 colon-namespaced
    coins like `xyz:NVDA`) the parent dex's meta. The returned index is the
    coin's position *within its own universe*; downstream callers (the SDK
    `order`/`update_leverage` etc.) translate name → asset ID internally using
    `perp_dexs`, so this helper is only used for sz/px decimals in our own
    rounding code.
    """
    info = _get_info()
    for i, u in enumerate(info.meta().get("universe", [])):
        if u["name"] == coin:
            return i, u.get("szDecimals", 5), u.get("pxDecimals", 4)
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        try:
            for i, u in enumerate(info.meta(dex=dex).get("universe", [])):
                if u["name"] == coin:
                    return i, u.get("szDecimals", 5), u.get("pxDecimals", 4)
        except Exception as e:
            logger.warning(f"[get_coin_index] HIP-3 meta lookup failed for dex={dex}: {e}")
    raise ValueError(f"Unknown coin: {coin}")


def get_max_leverage(coin: str) -> int:
    """The coin's maximum allowed leverage, from the SDK meta endpoint.

    Hyperliquid sets this per coin (e.g. BOME 3x, ONDO 10x, BTC 40x); an order
    that tries to exceed it is rejected, so callers cap their leverage here.
    HIP-3 namespaced coins (e.g. `xyz:NVDA`) are looked up in the parent dex's
    metadata when not found in the main perp universe.
    """
    info = _get_info()
    # Main perp dex
    for u in info.meta().get("universe", []):
        if u["name"] == coin:
            return int(u.get("maxLeverage", 1))
    # HIP-3: derive dex name from the namespace prefix and consult that dex's meta
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        try:
            for u in info.meta(dex=dex).get("universe", []):
                if u["name"] == coin:
                    return int(u.get("maxLeverage", 1))
        except Exception as e:
            logger.warning(f"[get_max_leverage] HIP-3 meta lookup failed for dex={dex}: {e}")
    raise ValueError(f"Unknown coin: {coin}")


# ── Market data ────────────────────────────────────────────────────────────────

def get_hl_price(coin: str = "BTC") -> float:
    """Get the current mid price for a coin.

    HIP-3 namespaced coins (e.g. `xyz:NVDA`) live on a separate perpDex and
    aren't returned by the bare `all_mids()` call — that endpoint only
    covers the main HL perp universe. For colon-namespaced names we derive
    the dex from the prefix and call `all_mids(dex=...)`. Without this fix
    the executor was silently aborting HIP-3 trades at
    `if mid_price <= 0: return invalid_price`.
    """
    info = _get_info()
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        try:
            mids = info.all_mids(dex=dex)
            v = mids.get(coin)
            if v is not None:
                return float(v)
        except Exception as e:
            logger.warning(f"[get_hl_price] HIP-3 all_mids failed for dex={dex}: {e}")
        return 0.0
    mids = info.all_mids()
    return float(mids.get(coin, "0"))


def get_all_hl_mids(include_hip3: bool = False) -> Dict[str, float]:
    """Return {coin: mid_price} for every perp — one HTTP call for the whole universe.

    When `include_hip3=True`, also queries each registered HIP-3 perpDex
    (~8 extra POSTs, weight ~2 each) and merges colon-namespaced mids
    (`xyz:MU`, `vntl:NVDA`, ...) into the result. Without this, the DSL
    exit pass receives no mid for any HIP-3 position, every tracker's
    `advance()` short-circuits, peak/floor never update, and the
    dashboard shows "no DSL" indefinitely for those positions.
    """
    info = _get_info()
    raw = info.all_mids() or {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    if include_hip3:
        try:
            from hermes_trader.client.universe import list_hip3_dexes
            for dex in list_hip3_dexes():
                try:
                    dex_mids = info.all_mids(dex=dex) or {}
                except Exception as e:
                    logger.warning(f"[get_all_hl_mids] HIP-3 all_mids failed for dex={dex}: {e}")
                    continue
                for k, v in dex_mids.items():
                    try:
                        out[k] = float(v)
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f"[get_all_hl_mids] HIP-3 dex enumeration failed: {e}")
    return out


# ── Order placement ────────────────────────────────────────────────────────────

def _is_isolated_only(coin: str) -> bool:
    """True if the market only supports isolated margin (no cross-margin).

    Most HIP-3 markets are isolated-only (85% of xyz, 100% of vntl, 87% of km,
    etc.) and a small subset of native HL perps too. Calling
    `update_leverage(is_cross=True)` on these silently fails on the exchange,
    after which the next `order()` is rejected with "Insufficient margin to
    place order" — even when the wallet has plenty of free equity, because no
    isolated-margin position was actually opened first.

    Reads the per-market meta (main dex or the namespace-prefix HIP-3 dex)
    and returns the `onlyIsolated` flag. Defaults to False (cross) if lookup
    fails so native crypto behavior is preserved.
    """
    info = _get_info()
    try:
        for m in info.meta().get("universe", []):
            if m["name"] == coin:
                return bool(m.get("onlyIsolated", False))
        if ":" in coin:
            dex = coin.split(":", 1)[0]
            for m in info.meta(dex=dex).get("universe", []):
                if m["name"] == coin:
                    return bool(m.get("onlyIsolated", False))
    except Exception as e:
        logger.warning(f"[_is_isolated_only] meta lookup failed for {coin}: {e}")
    return False


def set_leverage(coin: str, leverage: int) -> Dict[str, Any]:
    """Set leverage for a coin, choosing cross vs isolated based on the market.

    For markets flagged `onlyIsolated: true` (most HIP-3 + ~3% of native HL),
    we send `is_cross=False`. Without this branch the leverage call no-ops
    on isolated-only markets and the order that follows is rejected by HL
    with "Insufficient margin to place order" despite plenty of free margin.

    No-op when no private key is set.
    """
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "no private key"}

    is_cross = not _is_isolated_only(coin)
    try:
        exchange = _make_exchange()
        # SDK: update_leverage(leverage, coin, is_cross). is_cross=False for
        # markets where the dex rejects cross-margin (HIP-3 majority).
        result = exchange.update_leverage(leverage, coin, is_cross=is_cross)
        return {"ok": True, "result": result, "is_cross": is_cross}
    except Exception as e:
        logger.error(f"Failed to set leverage for {coin} (is_cross={is_cross}): {e}")
        return {"ok": False, "error": str(e), "is_cross": is_cross}


def _round_price_for_hl(price: float, sz_decimals: int, is_perp: bool = True) -> str:
    """Round a price to satisfy Hyperliquid's two constraints:
    
    1. Multiple of the tick size: tick = 10^(-(MAX_DECIMALS - sz_decimals))
       where MAX_DECIMALS = 6 for perps, 8 for spot.
    2. At most 5 significant figures total.
    
    Returns a string formatted with the resolved decimal count so the SDK
    sees the exact representation HL expects.
    """
    from decimal import Decimal, ROUND_HALF_UP, getcontext
    getcontext().prec = 28

    if price <= 0:
        return "0"

    MAX_DECIMALS = 6 if is_perp else 8
    px_decimals_by_tick = max(0, MAX_DECIMALS - int(sz_decimals))

    # Sig-fig limit: 5 sig figs across the whole number.
    # int_digits = number of digits to the LEFT of the decimal point in `price`.
    import math
    int_digits = max(0, int(math.floor(math.log10(price))) + 1)
    px_decimals_by_sigfig = max(0, 5 - int_digits)

    # The binding constraint is whichever is *smaller* (fewer decimals).
    px_decimals = min(px_decimals_by_tick, px_decimals_by_sigfig)

    # Round using Decimal to avoid float drift.
    q = Decimal(10) ** -px_decimals if px_decimals > 0 else Decimal(1)
    rounded = (Decimal(str(price)) / q).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * q
    # Format with the exact decimal count HL expects (no trailing zeros stripped)
    if px_decimals > 0:
        return f"{rounded:.{px_decimals}f}"
    return f"{rounded:.0f}"


def _parse_order_result(result: Any, accept_resting: bool = False) -> Dict[str, Any]:
    """Normalize a raw SDK order response into {ok, order_id?, avg_px?, total_sz?, error?}.

    For `filled` statuses we extract avgPx and totalSz too — downstream uses
    these to compute realized PnL from the actual fill price rather than the
    pre-trade mid (the two differ by spread + slippage, which compounds at
    leverage).
    """
    if not (isinstance(result, dict) and result.get("status") == "ok"):
        return {"ok": False, "error": str(result)}
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if statuses:
        st = statuses[0]
        if accept_resting and st.get("resting"):
            return {"ok": True, "order_id": str(st["resting"]["oid"])}
        if st.get("filled"):
            f = st["filled"]
            out: Dict[str, Any] = {"ok": True, "order_id": str(f.get("oid", ""))}
            try:
                if "avgPx" in f:
                    out["avg_px"] = float(f["avgPx"])
                if "totalSz" in f:
                    out["total_sz"] = float(f["totalSz"])
            except (TypeError, ValueError):
                pass
            return out
        if st.get("error"):
            return {"ok": False, "error": st["error"]}
    return {"ok": True}


def _min_order_size(price: float, sz_decimals: int) -> float:
    """Smallest size at the coin's precision worth at least MIN_ORDER_USD.

    Rounded UP to the size tick (10^-sz_decimals): for integer-size coins
    (sz_decimals=0) a plain round-to-precision would drop a near-$10 size
    under HL's floor. e.g. MEGA at $0.084 needs ~125 coins, not 100.
    """
    tick = 10.0 ** (-sz_decimals)
    return math.ceil((MIN_ORDER_USD / price) / tick) * tick


def _ioc_cross_price(coin: str, is_buy: bool, mid_price: float) -> float:
    """Limit price for an IOC order that reliably crosses the live book.

    Anchors to the current best bid/ask from a *fresh* L2 fetch — the mid
    passed into place_hl_order is stale by the time the order is built
    (set_leverage + get_hl_atr run in between) — and steps 1% past the
    touch. An IOC fills at the resting price, so that 1% is headroom that
    absorbs price moves before the order lands, not slippage. The old
    fixed 0.1%-from-mid offset missed on moving coins ("could not
    immediately match"). Falls back to mid +/- 1% if the L2 fetch fails.
    """
    try:
        levels = _get_info().l2_snapshot(coin).get("levels", [])
        bids, asks = levels[0], levels[1]
        if is_buy and asks:
            return float(asks[0]["px"]) * 1.01
        if not is_buy and bids:
            return float(bids[0]["px"]) * 0.99
    except Exception as e:
        # Best-effort: fall back to mid ±1%. Log so a persistent l2 outage
        # is visible even though execution still succeeds.
        logger.warning(f"[aggressive_limit_px] l2_snapshot failed for {coin}: {e}")
    return mid_price * (1.01 if is_buy else 0.99)


def place_hl_order(
    is_buy: bool,
    size: float,
    mid_price: float,
    coin: str = "BTC",
) -> Dict[str, Any]:
    """Place an IOC (immediate-or-cancel) limit order on Hyperliquid."""
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if mid_price <= 0:
        return {"ok": False, "error": f"invalid price for {coin}"}

    try:
        # Always get sz_dec from get_coin_index() (px_dec is ignored — we compute it correctly)
        _, sz_dec, _ = get_coin_index(coin)

        # Price the IOC to cross the live book (best bid/ask + 1% headroom).
        price = _ioc_cross_price(coin, is_buy, mid_price)

        # Round price honoring Hyperliquid's tick + 5-sigfig rules
        price_str = _round_price_for_hl(price, sz_dec, is_perp=True)

        # Never place below HL's $10 minimum. _min_order_size rounds the floor
        # UP to the coin's size tick, so rounding to sz_dec can't drop it under.
        size = max(size, _min_order_size(mid_price, sz_dec))
        size_str = f"{size:.{sz_dec}f}"
        
        logger.info(f"[place_hl_order] price_str={price_str}, size_str={size_str}, mid={mid_price}, sz_dec={sz_dec}")
        
        exchange = _make_exchange()
        order_type = OrderType(limit={"tif": "Ioc"})
        
        logger.info(f"[place_hl_order] Calling exchange.order({coin}, {is_buy}, {float(size_str)}, {float(price_str)}, {order_type})")
        
        # SDK expects float for both size and price (signature: limit_px: float).
        # price_str was already rounded to HL's tick + sigfig rules, so float() is safe.
        result = exchange.order(
            coin,
            is_buy,
            float(size_str),
            float(price_str),
            order_type,
            reduce_only=False,
        )
        
        return _parse_order_result(result)
    except Exception as e:
        logger.error(f"Failed to place order for {coin}: {e}")
        return {"ok": False, "error": str(e)}


def place_hl_trigger_order(
    is_long_position: bool,
    size: float,
    trigger_px: float,
    kind: str,  # 'sl' or 'tp'
    coin: str = "BTC",
) -> Dict[str, Any]:
    """Place a reduce-only trigger order (stop-loss or take-profit).

    Triggers a market order in the position-closing direction once the
    trigger price is crossed.
    """
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if size <= 0 or trigger_px <= 0:
        return {"ok": False, "error": "invalid size/price"}

    try:
        _, sz_dec, _ = get_coin_index(coin)

        exchange = _make_exchange()

        # Trigger order closes the position: opposite direction, reduce-only.
        # For a long position: sell trigger (is_buy=False).
        # For a short position: buy trigger (is_buy=True).
        is_buy = not is_long_position

        trigger_str = _round_price_for_hl(trigger_px, sz_dec, is_perp=True)
        trigger_f = float(trigger_str)
        size_str = f"{size:.{sz_dec}f}"

        order_type = OrderType(
            trigger=TriggerOrderType(
                triggerPx=trigger_f,
                isMarket=True,
                tpsl="sl" if kind == "sl" else "tp",
            )
        )

        logger.info(
            f"[place_hl_trigger_order] {coin} {kind} is_buy={is_buy} "
            f"trigger={trigger_str} size={size_str}"
        )

        # isMarket=True fills at market on trigger; limit_px is a reference.
        result = exchange.order(
            coin,
            is_buy,
            float(size_str),
            trigger_f,
            order_type,
            reduce_only=True,
        )

        return _parse_order_result(result, accept_resting=True)
    except Exception as e:
        logger.error(f"Failed to place trigger order for {coin}: {e}")
        return {"ok": False, "error": str(e)}


def cancel_orders(oid: int, coin: Optional[str] = None, asset_idx: Optional[int] = None) -> Dict[str, Any]:
    """Cancel an order by order ID."""
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "PRIVATE_KEY not set"}
    
    try:
        # Need coin name for cancel - use asset_idx to look it up
        coin_name = coin
        if not coin_name and asset_idx is not None:
            info = _get_info()
            meta = info.meta()
            for u in meta.get("universe", []):
                if u.get("index") == asset_idx:
                    coin_name = u["name"]
                    break
            if not coin_name:
                return {"ok": False, "error": f"unknown asset index {asset_idx}"}
        
        if not coin_name:
            return {"ok": False, "error": "coin name required"}
        
        exchange = _make_exchange()
        result = exchange.cancel(coin_name, oid)
        
        if isinstance(result, dict) and result.get("status") == "ok":
            return {"ok": True}
        return {"ok": False, "error": str(result)}
    except Exception as e:
        logger.error(f"Failed to cancel order: {e}")
        return {"ok": False, "error": str(e)}


# ── ATR ────────────────────────────────────────────────────────────────────────

def get_hl_atr(
    interval: str = "4h",
    period: int = 14,
    coin: str = "BTC",
) -> float:
    """Compute ATR(14) on a given HL interval (defaults to 4h)."""
    candles = fetch_hl_candles(coin, interval, period + 10)
    if len(candles) < period + 1:
        return 0.0
    
    tr = []
    for i in range(1, len(candles)):
        cur, pc = candles[i], candles[i - 1]
        tr.append(max(
            cur.h - cur.l,
            abs(cur.h - pc.c),
            abs(cur.l - pc.c),
        ))
    
    if len(tr) < period:
        return 0.0
    
    atr = sum(tr[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period

    return atr
