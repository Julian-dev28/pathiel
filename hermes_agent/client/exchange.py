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
import os
from typing import Any, Dict, Optional, Tuple

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import (
    OrderType,
    TriggerOrderType,
)

from hermes_agent.client.hl_client import HL_API, fetch_hl_candles

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

    _exchange_instance = Exchange(
        wallet=acct,
        base_url=HL_API,
        account_address=account_address,
    )
    return _exchange_instance


def _get_info() -> Info:
    """Get or create an Info client."""
    return Info()


def get_coin_index(coin: str) -> Tuple[int, int, int]:
    """Resolve a coin name to (asset_index, sz_decimals, px_decimals) via the SDK meta endpoint."""
    info = _get_info()
    meta = info.meta()
    for i, u in enumerate(meta.get("universe", [])):
        if u["name"] == coin:
            return i, u.get("szDecimals", 5), u.get("pxDecimals", 4)
    raise ValueError(f"Unknown coin: {coin}")


# ── Market data ────────────────────────────────────────────────────────────────

def get_hl_price(coin: str = "BTC") -> float:
    """Get the current mid price for a coin."""
    info = _get_info()
    mids = info.all_mids()
    return float(mids.get(coin, "0"))


# ── Order placement ────────────────────────────────────────────────────────────

def set_leverage(coin: str, leverage: int) -> Dict[str, Any]:
    """Set leverage for a coin. No-op if no private key is set."""
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "no private key"}
    
    try:
        exchange = _make_exchange()
        # SDK signature: update_leverage(leverage, coin, is_cross=True)
        result = exchange.update_leverage(leverage, coin, is_cross=True)
        return {"ok": True, "result": result}
    except Exception as e:
        logger.error(f"Failed to set leverage for {coin}: {e}")
        return {"ok": False, "error": str(e)}


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
    """Normalize a raw SDK order response into {ok, order_id?, error?}."""
    if not (isinstance(result, dict) and result.get("status") == "ok"):
        return {"ok": False, "error": str(result)}
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if statuses:
        st = statuses[0]
        if accept_resting and st.get("resting"):
            return {"ok": True, "order_id": str(st["resting"]["oid"])}
        if st.get("filled"):
            return {"ok": True, "order_id": str(st["filled"]["oid"])}
        if st.get("error"):
            return {"ok": False, "error": st["error"]}
    return {"ok": True}


def place_hl_order(
    is_buy: bool,
    size: float,
    mid_price: float,
    coin: str = "BTC",
) -> Dict[str, Any]:
    """Place an IOC (immediate-or-cancel) limit order on Hyperliquid."""
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    
    try:
        # Always get sz_dec from get_coin_index() (px_dec is ignored — we compute it correctly)
        _, sz_dec, _ = get_coin_index(coin)
        
        # Use 0.1% offset from mid for market-like execution (small enough to pass 95% validation)
        price = mid_price * (1.001 if is_buy else 0.999)
        
        # Round price honoring Hyperliquid's tick + 5-sigfig rules
        price_str = _round_price_for_hl(price, sz_dec, is_perp=True)
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
