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

from hermes_trader.models.types import Candle

if TYPE_CHECKING:
    from hyperliquid.info import Info
    from hermes_trader.client.ws_client import HyperliquidWebSocket

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


def fetch_account_state(user: str, include_hip3: bool = False) -> Dict[str, Any]:
    """Fetch perp + spot account state, optionally aggregating HIP-3 dexes.

    When `include_hip3=True`, queries each HIP-3 perpDex's clearinghouse
    (one POST per dex), sums equity + total_ntl, concatenates asset_positions
    (HIP-3 coins normalized to `<dex>:<coin>`), and returns `dex_equity` +
    `queried_dexes` (the dexes that actually responded — used by the DSL
    rehydrator to avoid dropping trackers when a dex query times out).

    `available` stays main-dex only because HIP-3 free margin only backs
    trades on its own dex; the executor sizes against this for main trades.
    """
    perp = _http_post("/info", {"type": "clearinghouseState", "user": user})
    spot = _http_post("/info", {"type": "spotClearinghouseState", "user": user})

    if not perp:
        perp = {}
    if not spot:
        spot = {}

    margin_summary = perp.get("marginSummary", {})
    perp_equity = float(margin_summary.get("accountValue", "0"))
    total_ntl = float(margin_summary.get("totalNtlPos", "0"))
    total_margin_used = float(margin_summary.get("totalMarginUsed", "0"))

    raw_balances = spot.get("balances", []) or []
    spot_balances = [b for b in raw_balances if b.get("coin", "") in ("USDC", "USDT", "USD")]
    spot_usdc = sum(float(b.get("total", "0") or 0) for b in spot_balances)

    raw_positions = perp.get("assetPositions", []) or []
    asset_positions = [
        p for p in raw_positions
        if float(p.get("position", {}).get("szi", "0")) != 0
    ]

    equity = perp_equity
    # Free initial margin = what HL's UI shows as "Available to Trade" and
    # what HL checks before accepting new orders. `withdrawable` is a
    # different (much tighter) number — the spot-bridgeable amount — and
    # using it gated the executor at ~5% of equity even when ~50% was
    # actually free for new positions.
    available = max(0.0, equity - total_margin_used)

    dex_equity: Dict[str, float] = {"": perp_equity}
    dex_available: Dict[str, float] = {"": available}
    queried_dexes: set = {""}
    available_aggregated = available  # starts as main; HIP-3 adds in

    if include_hip3:
        from hermes_trader.client.universe import list_hip3_dexes
        try:
            dexes = list_hip3_dexes()
        except Exception as e:
            logger.warning(f"[hl] list_hip3_dexes failed during account aggregation: {e}")
            dexes = []
        for dex in dexes:
            try:
                dex_state = _http_post("/info", {
                    "type": "clearinghouseState", "user": user, "dex": dex,
                }) or {}
            except Exception as e:
                logger.warning(f"[hl] HIP-3 clearinghouseState failed for {dex}: {e}")
                continue
            queried_dexes.add(dex)
            dex_ms = dex_state.get("marginSummary", {}) or {}
            dex_value = float(dex_ms.get("accountValue", 0) or 0)
            dex_ntl = float(dex_ms.get("totalNtlPos", 0) or 0)
            dex_margin_used = float(dex_ms.get("totalMarginUsed", 0) or 0)
            dex_free = max(0.0, dex_value - dex_margin_used)
            dex_equity[dex] = dex_value
            dex_available[dex] = dex_free
            equity += dex_value
            total_ntl += dex_ntl
            available_aggregated += dex_free
            for p in (dex_state.get("assetPositions") or []):
                pos = p.get("position", {}) or {}
                if float(pos.get("szi", "0") or 0) == 0:
                    continue
                coin = pos.get("coin", "") or ""
                # HL's HIP-3 endpoints sometimes return bare ("MU"), sometimes
                # namespaced ("xyz:MU"). Normalize so DSL tracker keys match.
                if coin and ":" not in coin:
                    pos["coin"] = f"{dex}:{coin}"
                asset_positions.append(p)

    return {
        "equity": equity,
        "available": available,                       # main-only — for executor sizing
        "available_aggregated": available_aggregated, # total across all dexes — for display
        "spot_usdc": spot_usdc,
        "total_usdc": equity + spot_usdc,
        "total_ntl": total_ntl,
        "spot_balances": spot_balances,
        "asset_positions": asset_positions,
        "dex_equity": dex_equity,
        "dex_available": dex_available,
        "queried_dexes": queried_dexes,
    }


def fetch_aggregate_contributions_since(user: str, start_ms: int) -> float:
    """Net USDC flowing INTO main + HIP-3 dex clearinghouses since `start_ms`.

    Used by daily-PnL tracking so transfers don't masquerade as trading
    gains: `daily_pnl = equity_now - equity_sod - contributions`. Counts
    deposits/withdrawals + transfers crossing the pool boundary (spot↔perp,
    spot↔HIP-3); treats intra-pool transfers (main↔xyz, xyz↔vntl) as neutral.

    Returns 0.0 on lookup failure to avoid distorting PnL on transient outages.
    """
    if not user or start_ms <= 0:
        return 0.0
    try:
        events = _http_post("/info", {
            "type": "userNonFundingLedgerUpdates",
            "user": user,
            "startTime": int(start_ms),
        }) or []
    except Exception as e:
        logger.warning(f"[hl] ledger fetch failed for contributions: {e}")
        return 0.0
    if not isinstance(events, list):
        return 0.0

    # Pool members: main HL is keyed as "" in HL's `send` schema; HIP-3
    # dexes are keyed by name.
    in_pool = {""}
    try:
        from hermes_trader.client.universe import list_hip3_dexes
        in_pool.update(list_hip3_dexes())
    except Exception:
        pass

    user_lc = (user or "").lower()
    net = 0.0
    for e in events:
        d = e.get("delta") or {}
        t = d.get("type")
        amt = float(d.get("usdcValue", d.get("amount", 0)) or 0)
        if amt == 0:
            continue
        if t == "send":
            src = d.get("sourceDex", "") or ""
            dst = d.get("destinationDex", "") or ""
            sender = (d.get("user") or "").lower()
            receiver = (d.get("destination") or "").lower()
            src_in, dst_in = src in in_pool, dst in in_pool
            if sender == receiver == user_lc:
                if dst_in and not src_in:
                    net += amt
                elif src_in and not dst_in:
                    net -= amt
            elif sender == user_lc and src_in:
                net -= amt
            elif receiver == user_lc and dst_in:
                net += amt
        elif t in ("deposit", "vaultWithdraw"):
            net += amt
        elif t in ("withdraw", "vaultDeposit"):
            net -= amt
        elif t in ("internalTransfer", "subAccountTransfer"):
            sender = (d.get("user") or "").lower()
            net += -amt if sender == user_lc else amt
    return net


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


def fetch_all_mids(include_hip3: bool = False) -> Dict[str, str]:
    """Get all mid prices.

    For one-shot commands: uses HTTP POST (reliable, fast).
    For the autonomous loop: use start_ws_mids() to keep a persistent
    WebSocket running, then call ws.get_all_mids() for sub-second data.

    Args:
        include_hip3: when True, also fetches `allMids` for each HIP-3 perpDex
            (one HTTP POST per dex, sequential) and merges results in. Adds
            ~8 small POSTs per call — only enable from contexts that need
            tokenized-equity / commodity prices.
    """
    ws_result = _try_ws_mids()
    if ws_result and not include_hip3:
        # WebSocket only carries the native HL perp mids; if HIP-3 is needed
        # we fall through to per-dex HTTP fetches below.
        return ws_result

    # Native perp + spot mids (one HTTP POST).
    raw = _http_post("/info", {"type": "allMids"})
    out: Dict[str, str] = {}
    if raw and isinstance(raw, dict):
        out = {k: str(v) for k, v in raw.items()}
    elif ws_result:
        out = dict(ws_result)

    if include_hip3:
        # HIP-3 mids live behind a `dex` parameter. Walk the registered dex list
        # and merge — one POST per dex (~8 total, weight ~2 each).
        from hermes_trader.client.universe import list_hip3_dexes
        for dex in list_hip3_dexes():
            r = _http_post("/info", {"type": "allMids", "dex": dex})
            if r and isinstance(r, dict):
                for k, v in r.items():
                    out[k] = str(v)
    return out


def start_ws_mids() -> "HyperliquidWebSocket | None":
    """Start the persistent WebSocket for real-time mids (call once at startup)."""
    global _ws_mids_instance
    with _ws_mids_lock:
        if _ws_mids_instance is None:
            try:
                from hermes_trader.client.ws_client import HyperliquidWebSocket
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
