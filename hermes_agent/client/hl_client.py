"""Hyperliquid API client — HTTP + persistent Info() for REST.

Architecture:
1. Single Info() instance for meta/spot_meta (REST, not WS)
2. All candle/mids/account calls via direct HTTP POST
3. Optional websocket for future realtime features
4. Volume-based pre-filtering to stay under rate limits

Rate limit management:
- metaAndAssetCtxs (weight 20) returns ALL 230 perps with dayNtlVlm
- spotMetaAndAssetCtxs (weight 20) returns ALL 297 spot assets
- candleSnapshot per-coin costs weight 20 each
- Total capacity: 1,200 weight/minute
- Strategy: volume-filter to top N markets, then fetch candles only for those
- Candle cache with 15min TTL so repeated scans reuse data
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import requests

from hermes_agent.models.types import Candle

if TYPE_CHECKING:
    from hyperliquid.info import Info
    from hermes_agent.client.ws_client import HyperliquidWebSocket

logger = logging.getLogger(__name__)

HL_API = "https://api.hyperliquid.xyz"
_MS_PER_CANDLE: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

# ── Singleton Info client ─────────────────────────────────────────────
# We create Info() ONCE with pre-fetched meta (HTTP, fast, no WS blocking).

_info_instance: "Info | None" = None
_info_lock = threading.Lock()


def _fetch_meta_sync() -> tuple:
    """Fetch meta and spot_meta via HTTP (fast, no WS needed)."""
    try:
        perp = requests.post(f"{HL_API}/info", json={"type": "meta"}, timeout=10)
        perp.raise_for_status()
        perp_meta = perp.json()

        spot = requests.post(f"{HL_API}/info", json={"type": "spotMeta"}, timeout=10)
        spot.raise_for_status()
        spot_meta = spot.json()
        return perp_meta, spot_meta
    except Exception as e:
        logger.error(f"[hl] Meta fetch failed: {e}")
        return {}, {}


def init_info() -> None:
    """Initialize the shared Info client.

    Fast path: fetches meta via HTTP then creates Info with skip_ws=True.
    """
    global _info_instance

    with _info_lock:
        if _info_instance is not None:
            return

        logger.info("[hl] Initializing Info client...")
        perp_meta, spot_meta = _fetch_meta_sync()

        try:
            from hyperliquid.info import Info
            # skip_ws=True prevents blocking WS connect + meta fetch
            # We already have meta from HTTP above
            _info_instance = Info(skip_ws=True, meta=perp_meta, spot_meta=spot_meta)
            logger.info("[hl] Info client initialized (HTTP-only)")
        except Exception as e:
            logger.warning(f"[hl] Failed to create Info: {e}")
            _info_instance = None


# ── HTTP helpers ──────────────────────────────────────────────────────

def _http_post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
    """Direct HTTP POST."""
    try:
        resp = requests.post(f"{HL_API}{path}", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"[hl] HTTP POST {path} failed: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────

def get_info() -> "Info | None":
    """Get the shared Info client instance."""
    global _info_instance
    if _info_instance is None:
        init_info()
    return _info_instance


def resolve_user_address() -> str:
    """Master address if set, else wallet address, else empty string."""
    return os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")


def fetch_hl_candles(
    coin: str,
    interval: str = "5m",
    count: int = 100,
) -> List[Candle]:
    """Fetch candles via HTTP."""
    ms = _MS_PER_CANDLE.get(interval, 300_000)
    end_time = int(time.time() * 1000)
    start_time = end_time - ms * count

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_time,
            "endTime": end_time,
        }
    }
    raw = _http_post("/info", payload)
    if not isinstance(raw, list):
        return []

    return [
        Candle(
            t=c["t"], o=float(c["o"]), h=float(c["h"]),
            l=float(c["l"]), c=float(c["c"]), v=float(c.get("v", "0")),
        )
        for c in raw
    ]


def fetch_account_state(user: str) -> Dict[str, Any]:
    """Fetch perp + spot account state."""
    perp = _http_post("/info", {"type": "clearinghouseState", "user": user})
    spot = _http_post("/info", {"type": "spotClearinghouseState", "user": user})

    if not perp:
        perp = {}
    if not spot:
        spot = {}

    margin_summary = perp.get("marginSummary", {})
    perp_equity = float(margin_summary.get("accountValue", "0"))
    total_ntl = float(margin_summary.get("totalNtlPos", "0"))

    raw_balances = spot.get("balances", []) or []
    spot_balances = [b for b in raw_balances if b.get("coin", "") in ("USDC", "USDT", "USD")]

    raw_positions = perp.get("assetPositions", []) or []
    asset_positions = [
        p for p in raw_positions
        if float(p.get("position", {}).get("szi", "0")) != 0
    ]

    # On unified accounts perp_equity already includes spot USDC, so it is
    # the total equity directly — spot balances are not added on top.
    equity = perp_equity

    return {
        "equity": equity,
        "total_ntl": total_ntl,
        "spot_balances": spot_balances,
        "asset_positions": asset_positions,
    }


# ── WebSocket mids (real-time, low latency) ───────────────────────────
# The persistent WebSocket connection gives sub-second latency for all 500+
# market prices. It's used by the autonomous scanning loop for real-time data.

_ws_mids_instance: "HyperliquidWebSocket | None" = None
_ws_mids_lock = threading.Lock()


def _get_ws_mids_instance() -> "HyperliquidWebSocket | None":
    """Return the active WebSocket mids instance, or None if not started."""
    with _ws_mids_lock:
        return _ws_mids_instance


def _try_ws_mids() -> Dict[str, str] | None:
    """Try to get mids from the persistent WebSocket (non-blocking).

    Returns None immediately if WS isn't running. Caller decides whether
    to fall back to HTTP.
    """
    ws = _get_ws_mids_instance()
    if ws is None:
        return None
    mids = ws.get_all_mids()
    if mids:
        return {k: str(v) for k, v in mids.items()}
    return None


def fetch_all_mids() -> Dict[str, str]:
    """Get all mid prices.

    For one-shot commands: uses HTTP POST (reliable, fast).
    For the autonomous loop: use start_ws_mids() to keep a persistent
    WebSocket running, then call ws.get_all_mids() for sub-second data.
    """
    ws_result = _try_ws_mids()
    if ws_result:
        return ws_result

    # Fallback: HTTP POST (_http_post handles its own network errors).
    raw = _http_post("/info", {"type": "allMids"})
    if raw and isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items()}
    return {}


def start_ws_mids() -> "HyperliquidWebSocket | None":
    """Start the persistent WebSocket for real-time mids (call once at startup)."""
    global _ws_mids_instance
    with _ws_mids_lock:
        if _ws_mids_instance is None:
            try:
                from hermes_agent.client.ws_client import HyperliquidWebSocket
                ws = HyperliquidWebSocket()
                ws.start()
                _ws_mids_instance = ws
                logger.info("[hl] WebSocket started for real-time mids")
            except Exception as e:
                logger.warning(f"[hl] WebSocket init failed: {e}")
                return None
        return _ws_mids_instance


def stop_ws_mids() -> None:
    """Stop the persistent WebSocket. Call when exiting the scanning loop."""
    global _ws_mids_instance
    with _ws_mids_lock:
        if _ws_mids_instance is not None:
            _ws_mids_instance.stop(timeout=2.0)
            _ws_mids_instance = None
            logger.info("[hl] WebSocket stopped")


def fetch_funding_history(
    coin: str, start_time: int, end_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch funding rate history."""
    if end_time is None:
        end_time = int(time.time() * 1000)
    payload = {"type": "fundingHistory", "coin": coin, "startTime": start_time, "endTime": end_time}
    raw = _http_post("/info", payload)
    return raw if isinstance(raw, list) else []
