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
- Rolling-window candle cache per (coin, interval): longest series stored,
  shorter counts served by slicing; per-interval TTLs (see _WINDOW_TTL_S)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import requests

from hermes_trader.client.rate_limit import HL_LIMITER as _HL_LIMITER, endpoint_weight as _endpoint_weight
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

# Shared connection pool. Every _http_post reuses keep-alive connections
# instead of opening a fresh TCP+TLS handshake per call (~50-200ms each).
# At 60 markets × 2 candle fetches + 8 dex queries per scan, that handshake
# tax dominated. requests.Session is thread-safe for concurrent .post()
# when the adapter pool is sized for our fan-out (8 dex queries + headroom).
_session: "requests.Session | None" = None
_session_lock = threading.Lock()


def _get_session() -> "requests.Session":
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=16,
                    pool_maxsize=32,
                    max_retries=requests.adapters.Retry(
                        total=2, backoff_factor=0.3,
                        status_forcelist=[502, 503, 504],
                        allowed_methods=["POST"],
                    ),
                )
                s.mount("https://", adapter)
                _session = s
    return _session


def _http_post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
    """Direct HTTP POST over the shared keep-alive connection pool.

    Acquires a rate-limit token first (HL: ~1200 weight/min). On budget
    exhaustion, returns None so the caller's existing retry/backoff handles it
    rather than firing into a 429.
    """
    weight = _endpoint_weight(payload.get("type"))
    max_wait = float(os.environ.get("HERMES_HL_RATE_MAX_WAIT_S", "30"))
    if not _HL_LIMITER.acquire(weight, max_wait=max_wait):
        logger.warning(
            f"[hl] rate budget exhausted for {payload.get('type') or 'unknown'} "
            f"(weight={weight}, waited {max_wait:g}s) — skipping request this retry"
        )
        return None
    try:
        resp = _get_session().post(f"{HL_API}{path}", json=payload, timeout=timeout)
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


# Rolling-window candle cache. The old exact-count TTL cache (key
# coin|interval|count) collapsed same-call duplicates but NOT the cross-consumer
# fan-out: because candleSnapshot costs a FLAT weight 20 regardless of bar count
# (rate_limit.py), one candidate coin paid ~4 separate weight-20 fetches for the
# SAME 5m series (×50 chronos/tirex, ×60 rvol, ×100 scan/timesfm, ×290 coin-tape)
# plus 2× for 4h and 2× for 1d — ~9-10 fetches for ~4 distinct series, and that
# duplication is what saturates the 1200 weight/min bucket mid-scan.
#
# The window store keys on (coin, interval) ONLY: it keeps the LONGEST series
# fetched for that pair and serves any count <= stored length by slicing the
# tail (a slice read never extends the TTL — freshness is always measured from
# the original fetch). A refetch on expiry re-fetches max(requested, stored)
# bars at the SAME weight-20 cost, keeping the long window warm for the tape/
# model consumers. Write-back only UPGRADES: a fresh short fetch (fast-exit's
# 1m×3) never shrinks a still-fresh longer window.
#
# Per-interval freshness budgets (the old uniform 90s both wasted freshness on
# 5m tip-readers and starved the slow intervals): a 5m window is refetchable
# after 30s — fresher than the scan already tolerated under the old 90s — while
# 1h/4h/1d bars change once per period, so they cache for minutes. HERMES_CANDLE_
# CACHE_TTL_S, when set, forces a UNIFORM TTL across all intervals (0 disables
# caching entirely) — same kill-switch semantics as the old env var.
_CANDLE_WINDOWS: Dict[str, tuple] = {}
_CANDLE_WINDOWS_LOCK = threading.Lock()
_WINDOW_TTL_S: Dict[str, float] = {
    "1m": 15.0,
    "5m": 30.0,
    "15m": 300.0,
    "1h": 600.0,
    "4h": 1800.0,
    "1d": 1800.0,
}
_WINDOW_TTL_DEFAULT_S = 90.0
_MAX_STORED_BARS = 500  # cap the upgrade ratchet so one odd request can't bloat the window
_CANDLE_TTL_OVERRIDE_S: Optional[float] = (
    float(os.environ["HERMES_CANDLE_CACHE_TTL_S"])
    if os.environ.get("HERMES_CANDLE_CACHE_TTL_S") is not None else None
)


def _window_ttl(interval: str) -> float:
    if _CANDLE_TTL_OVERRIDE_S is not None:
        return _CANDLE_TTL_OVERRIDE_S
    return _WINDOW_TTL_S.get(interval, _WINDOW_TTL_DEFAULT_S)


def fetch_hl_candles(
    coin: str,
    interval: str = "5m",
    count: int = 100,
    fresh: bool = False,
) -> List[Candle]:
    """Fetch candles via HTTP (rolling-window cached per coin+interval).

    Serves `count` bars from the stored window when it is fresh and long
    enough; otherwise fetches max(count, stored length) bars — flat weight 20
    either way — and stores the longer series.

    `fresh=True` bypasses the READ-side window (still writes back, upgrade-only),
    so the caller gets the current candle snapshot rather than a sub-TTL cached
    one. The fast-exit pass uses this so a spike in the still-forming last
    candle is seen on the next tick instead of waiting out the window TTL.
    """
    window_key = f"{coin}|{interval}"
    ttl = _window_ttl(interval)
    fetch_count = count

    if ttl > 0:
        with _CANDLE_WINDOWS_LOCK:
            hit = _CANDLE_WINDOWS.get(window_key)
        if hit:
            fetched_at, bars = hit
            if not fresh and (time.time() - fetched_at) < ttl and len(bars) >= count:
                return bars[-count:]
            # Stale (or too short): the refetch is the same weight-20 price, so
            # keep the stored length warm instead of downgrading it. (The
            # caller's own count is always honored — the cap guards what we
            # STORE, never what a caller asked for.)
            fetch_count = max(count, len(bars))

    ms = _MS_PER_CANDLE.get(interval, 300_000)
    end_time = int(time.time() * 1000)
    start_time = end_time - ms * fetch_count

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_time,
            "endTime": end_time,
        }
    }
    # A non-list result means a transient failure (429 / timeout), NOT "no
    # candles" — HL returns an empty LIST for a coin with no history. Those are
    # indistinguishable downstream: research treated a 429-blanked fetch as
    # "insufficient history" and emitted stop/tp = 0.0 (which the whale override
    # could then force-execute stopless). So retry a transient failure a few
    # times with backoff before giving up; a genuine empty list returns at once.
    raw = _http_post("/info", payload)
    attempts = 0
    while not isinstance(raw, list) and attempts < 3:
        attempts += 1
        time.sleep(0.3 * attempts)
        raw = _http_post("/info", payload)
    if not isinstance(raw, list):
        # Persisted across retries. Do NOT cache failures/empties — caching a bad
        # read would blank the coin for the whole TTL. Let the next call retry.
        # A non-list AFTER we already retried means a real transient-failure data
        # gap (429/timeout), NOT "no history" — warn so it isn't silently read as
        # "no signal" downstream. (attempts==0 path can't reach here: the first
        # raw was already non-list, so attempts is always >=1 when we land here.)
        logger.warning(
            f"[candles] {coin} {interval}: fetch failed across {attempts} retries "
            f"(transient 429/timeout) — returning EMPTY; this coin reads as "
            f"'no signal' this scan (silent data gap)")
        return []

    candles = [
        Candle(
            t=c["t"], o=float(c["o"]), h=float(c["h"]),
            l=float(c["l"]), c=float(c["c"]), v=float(c.get("v", "0")),
        )
        for c in raw
    ]
    if ttl > 0 and candles:
        # Write-back UPGRADES the window only: replace when there is no entry,
        # the entry is stale, or the new series is at least as long. A fresh
        # short fetch (fast-exit's 1m×3) must never shrink a still-fresh longer
        # window that other consumers slice from. The stored series is capped
        # at _MAX_STORED_BARS (tail kept) so one pathological request can't
        # bloat the window; a caller asking for more still GETS more, it just
        # isn't cached long.
        now = time.time()
        stored = candles[-_MAX_STORED_BARS:]
        with _CANDLE_WINDOWS_LOCK:
            prev = _CANDLE_WINDOWS.get(window_key)
            if (prev is None
                    or (now - prev[0]) >= ttl
                    or len(stored) >= len(prev[1])):
                _CANDLE_WINDOWS[window_key] = (now, stored)
    # The fetch may have been longer than requested (window kept warm) — hand
    # the caller exactly the last `count` bars it asked for.
    return candles[-count:] if len(candles) > count else candles


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
    spot_balances = [b for b in raw_balances if b.get("coin", "") in ("USDC", "USDT", "USDT0", "USDE", "USDH", "USD")]
    spot_usdc = sum(float(b.get("total", "0") or 0) for b in spot_balances)

    raw_positions = perp.get("assetPositions", []) or []
    asset_positions = [
        p for p in raw_positions
        if float(p.get("position", {}).get("szi", "0")) != 0
    ]
    # In a unified account the perp margin is USDC reserved ("hold") out of the
    # spot balance — the same dollars, not extra. We need both the reserved
    # amount and the perp unrealized PnL to value the portfolio without
    # double-counting the margin that's already sitting in spot_usdc.
    spot_held = sum(float(b.get("hold", "0") or 0) for b in spot_balances)
    perp_uPnL = sum(
        float(p.get("position", {}).get("unrealizedPnl", "0") or 0)
        for p in raw_positions
    )

    # Portfolio value = spot cash + perp unrealized PnL + (any perp margin from
    # a SEPARATE deposit, i.e. non-unified accounts). Adding perp_equity
    # (accountValue = margin + uPnL) on top of spot_usdc would count the held
    # margin twice in unified mode, inflating equity by the open-book margin
    # size. `perp_equity - perp_uPnL` is the margin backing; subtract the
    # spot-reserved portion to keep only separately-deposited margin.
    perp_deposited_cash = max(0.0, perp_equity - perp_uPnL - spot_held)
    equity = spot_usdc + perp_uPnL + perp_deposited_cash
    # MAIN-DEX FUNDING EQUITY (unified-margin fix, 2026-10-15): in a unified
    # account the main clearinghouse's accountValue counts ONLY the margin
    # committed to open positions (~$6.68 for a $33/5x short), not the shared
    # spot pool that actually funds new entries. Sizing against that raw value
    # collapsed every new entry to sub-floor notional ($0.29 vs a $10.56 HL
    # minimum) while ANY position was open — the bot could hold one main-dex
    # position and nothing else. The true main-dex funding equity is the
    # aggregate BEFORE HIP-3 dex values are added (those are separate
    # clearinghouses): spot cash + perp uPnL + separately-deposited perp margin.
    main_equity = spot_usdc + perp_uPnL + perp_deposited_cash
    # Free initial margin = what HL's UI shows as "Available to Trade" and
    # what HL checks before accepting new orders. `withdrawable` is a
    # different (much tighter) number — the spot-bridgeable amount — and
    # using it gated the executor at ~5% of equity even when ~50% was
    # actually free for new positions.
    available = max(0.0, equity - total_margin_used)

    dex_equity: Dict[str, float] = {"": perp_equity}
    dex_available: Dict[str, float] = {"": available}
    queried_dexes: set = {""}
    failed_dexes: set = set()  # HIP-3 dexes whose query failed (degraded aggregate)
    available_aggregated = available  # starts as main; HIP-3 adds in

    if include_hip3:
        from hermes_trader.client.universe import list_hip3_dexes
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            dexes = list_hip3_dexes()
        except Exception as e:
            logger.warning(f"[hl] list_hip3_dexes failed during account aggregation: {e}")
            dexes = []

        # Fan out the per-dex clearinghouse queries in parallel — serial loop
        # was 8 sequential POSTs × ~150ms each = 1.2s. Parallel finishes in
        # the slowest single call (~200ms), 4-6× speedup on the dashboard.
        def _fetch_dex(dex_name: str) -> tuple[str, Optional[Dict[str, Any]]]:
            try:
                return (dex_name, _http_post("/info", {
                    "type": "clearinghouseState", "user": user, "dex": dex_name,
                }))
            except Exception as e:
                logger.warning(f"[hl] HIP-3 clearinghouseState failed for {dex_name}: {e}")
                return (dex_name, None)

        if dexes:
            with ThreadPoolExecutor(max_workers=min(8, len(dexes)),
                                    thread_name_prefix="hl-dex") as pool:
                for dex, dex_state in pool.map(_fetch_dex, dexes):
                    if dex_state is None:
                        # Query FAILED — its equity is missing from the aggregate.
                        # Report it: the heartbeat partial-dex guard needs this to
                        # catch degraded reads on dexes holding only IDLE funds
                        # (no open position => invisible to the held-dex check).
                        # 2026-10-03: a 429'd dex query dropped ~$948 from the
                        # aggregate; the phantom -$948 daily PnL fired the hard
                        # killswitch and flattened the book on fiction.
                        failed_dexes.add(dex)
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
                        # HL HIP-3 endpoints return bare or namespaced — normalize.
                        if coin and ":" not in coin:
                            pos["coin"] = f"{dex}:{coin}"
                        asset_positions.append(p)

    return {
        "equity": equity,
        "main_equity": main_equity,                   # main-dex funding equity (unified-safe)
        "available": available,                       # main-only — for executor sizing
        "available_aggregated": available_aggregated, # total across all dexes — for display
        "spot_usdc": spot_usdc,
        "total_usdc": equity,
        "total_ntl": total_ntl,
        "spot_balances": spot_balances,
        "asset_positions": asset_positions,
        "dex_equity": dex_equity,
        "dex_available": dex_available,
        "queried_dexes": queried_dexes,
        "failed_dexes": failed_dexes,
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
