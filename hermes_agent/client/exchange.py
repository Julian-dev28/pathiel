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
from typing import Any, Dict, List, Optional, Tuple

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import (
    OrderRequest,
    CancelRequest,
    OrderType,
    TriggerOrderType,
    PriorityGrouping,
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


def _make_exchange() -> Exchange:
    """Create an Exchange client instance.
    
    Uses the SDK's built-in signing which handles:
    - ECDSA secp256k1 key from private key hex
    - Agent typed data domain signing
    - Connection ID hashing
    """
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
    
    return Exchange(
        wallet=acct,
        base_url=HL_API,
        account_address=account_address,
    )


def _get_info() -> Info:
    """Get or create an Info client."""
    return Info()


def get_coin_index(coin: str) -> Tuple[int, int]:
    """Resolve a coin name to (asset_index, sz_decimals).
    
    Uses the SDK's meta endpoint.
    Returns (asset_idx, sz_decimals).
    """
    info = _get_info()
    meta = info.meta()
    for i, u in enumerate(meta.get("universe", [])):
        if u["name"] == coin:
            return i, u.get("szDecimals", 5)
    raise ValueError(f"Unknown coin: {coin}")


# ── Market data ────────────────────────────────────────────────────────────────

def get_hl_price(coin: str = "BTC") -> float:
    """Get the current mid price for a coin."""
    info = _get_info()
    mids = info.all_mids()
    return float(mids.get(coin, "0"))


def get_all_positions(raw_perp: Dict[str, Any], all_mids: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """Extract non-zero positions from clearinghouse state.
    
    Translation of getAllPositions() from lib/hyperliquid.ts.
    """
    positions = []
    for p in (raw_perp.get("assetPositions") or []):
        pos = p.get("position", {})
        szi = float(pos.get("szi", "0"))
        if szi == 0:
            continue
        entry_px = float(pos.get("entryPx", "0"))
        notional = abs(szi) * entry_px
        positions.append({
            "coin": pos.get("coin", ""),
            "side": "long" if szi > 0 else "short",
            "szi": abs(szi),
            "entryPx": entry_px,
            "unrealizedPnl": float(pos.get("unrealizedPnl", "0")),
            "leverage": float(pos.get("leverage", {}).get("value", "5") if isinstance(pos.get("leverage"), dict) else "5"),
            "notional": notional,
        })
    return positions


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


def place_hl_order(
    is_buy: bool,
    size: float,
    mid_price: float,
    coin: str = "BTC",
    asset_idx: Optional[int] = None,
) -> Dict[str, Any]:
    """Place a limit order on Hyperliquid.
    
    Translation of placeHLOrder() from lib/hyperliquid.ts.
    Uses IOC (Immediate-or-Cancel) with 5% offset from mid price.
    """
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    
    try:
        if asset_idx is None:
            asset_idx, sz_dec = get_coin_index(coin)
        else:
            # Need to get sz_decimals
            info = _get_info()
            meta = info.meta()
            for u in meta.get("universe", []):
                if u.get("index") == asset_idx:
                    sz_dec = u.get("szDecimals", 5)
                    break
            else:
                sz_dec = 5
        
        # 5% offset from mid for market-like execution
        price = mid_price * 1.05 if is_buy else mid_price * 0.95
        price_str = f"{float(f'{price:.6f}')}"
        size_str = f"{size:.{sz_dec}f}"
        
        exchange = _make_exchange()
        order_type = OrderType(limit={"tif": "Ioc"})
        
        # SDK signature: order(name, is_buy, sz, limit_px, order_type, ...)
        result = exchange.order(
            coin,
            is_buy,
            float(size_str),
            float(price_str),
            order_type,
            reduce_only=False,
        )
        
        # Parse result
        if isinstance(result, dict) and result.get("status") == "ok":
            resp_data = result.get("response", {})
            statuses = resp_data.get("data", {}).get("statuses", [])
            if statuses:
                st = statuses[0]
                if st.get("filled"):
                    return {"ok": True, "order_id": str(st["filled"]["oid"])}
                if st.get("error"):
                    return {"ok": False, "error": st["error"]}
            return {"ok": True}
        
        return {"ok": False, "error": str(result)}
    except Exception as e:
        logger.error(f"Failed to place order for {coin}: {e}")
        return {"ok": False, "error": str(e)}


def place_hl_trigger_order(
    is_long_position: bool,
    size: float,
    trigger_px: float,
    kind: str,  # 'sl' or 'tp'
    asset_idx: Optional[int] = None,
    coin: str = "BTC",
) -> Dict[str, Any]:
    """Place a reduce-only trigger order (stop-loss or take-profit).
    
    Translation of placeHLTriggerOrder() from lib/hyperliquid.ts.
    Uses bulk_orders with PriorityGrouping for grouped SL/TP.
    """
    if not PRIVATE_KEY_HEX:
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if size <= 0 or trigger_px <= 0:
        return {"ok": False, "error": "invalid size/price"}
    
    try:
        if asset_idx is None:
            asset_idx, _ = get_coin_index(coin)
        
        exchange = _make_exchange()
        
        # Trigger order: market close in opposite direction
        # For long position: sell trigger (is_buy=False)
        # For short position: buy trigger (is_buy=True)
        is_buy = not is_long_position
        
        # Use bulk_orders with PriorityGrouping for grouped SL/TP
        # is_market=True means trigger triggers a market order; limit_px is just a fallback
        result = exchange.bulk_orders(
            order_requests=[
                OrderRequest(
                    coin=coin,
                    is_buy=is_buy,
                    sz=size,
                    limit_px=trigger_px,  # trigger price used as reference; market order fills at mid
                    order_type=order_type,
                    reduce_only=True,
                ),
            ],
            grouping=PriorityGrouping.NORMAL_TPSL,
        )
        
        if isinstance(result, dict) and result.get("status") == "ok":
            resp_data = result.get("response", {})
            statuses = resp_data.get("data", {}).get("statuses", [])
            if statuses:
                st = statuses[0]
                if st.get("resting"):
                    return {"ok": True, "order_id": str(st["resting"]["oid"])}
                if st.get("error"):
                    return {"ok": False, "error": st["error"]}
            return {"ok": True}
        
        return {"ok": False, "error": str(result)}
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
    """Compute ATR(14) on a given HL interval.
    
    Translation of getHLATR() from lib/hyperliquid.ts.
    Defaults to 4h, the timeframe used for backtested entries.
    """
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


def transfer_spot_to_perp(amount: float) -> Dict[str, Any]:
    """Transfer USDC from spot to perp margin.
    
    Unified account: no transfer needed — spot and perp share margin pool.
    """
    # Unified account: no transfer needed
    return {"ok": True, "message": "unified account — no transfer needed"}
