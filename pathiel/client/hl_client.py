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
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

import requests

from pathiel.client.http_session import SDK_TIMEOUT_S, _set_session_timeout
from pathiel.client.rate_limit import (HL_LIMITER as _HL_LIMITER, carry_priority as _carry_priority,
                                       endpoint_weight as _endpoint_weight, in_background as _in_background)
from pathiel.models.types import Candle

if TYPE_CHECKING:
    from hyperliquid.info import Info
    pass

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


def _fetch_meta_sync() -> Tuple[Dict[str, Any], Dict[str, Any]]:
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
            _info_instance = Info(skip_ws=True, meta=perp_meta, spot_meta=spot_meta, timeout=SDK_TIMEOUT_S)
            _set_session_timeout(_info_instance)
            logger.info("[hl] Info client initialized (HTTP-only, 10s timeout)")
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


class HLRequestError(RuntimeError):
    """An /info or /exchange request that did not produce a response body."""


def _caller() -> str:
    """`module.function` of the first frame outside pathiel.client: who is
    spending the venue budget. Recorded in the limiter's per-label counters
    so GET /api/health/system answers "who burns it" with numbers, not a
    guess (2026-10-24: 92 allMids reads in two minutes starved every
    open-orders read and blanked the Desk page's positions; nothing in the
    log said which code path made them)."""
    f = sys._getframe(2)
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if not mod.startswith("pathiel.client"):
            short = ".".join(mod.split(".")[-2:])          # services.positions, pathiel.server
            return f"{short}.{f.f_code.co_name}"
        f = f.f_back
    return "?"


def _http_post_strict(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
    """`_http_post` that raises HLRequestError instead of returning None.

    For callers that must tell "the venue answered null/[]" apart from "the
    request never landed" (rate budget, 429, timeout). A legitimate JSON null
    (e.g. vaultDetails for an unknown vault) is still returned as None.
    """
    weight = _endpoint_weight(payload.get("type"))
    max_wait = float(os.environ.get("PATHIEL_HL_RATE_MAX_WAIT_S", "30"))
    label = f"{payload.get('type') or 'unknown'}<{_caller()}"
    if not _HL_LIMITER.acquire(weight, max_wait=max_wait, background=_in_background(),
                               label=label):
        raise HLRequestError(
            f"rate budget exhausted for {payload.get('type') or 'unknown'} "
            f"(weight={weight}, waited {max_wait:g}s)"
        )
    try:
        resp = _get_session().post(f"{HL_API}{path}", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise HLRequestError(f"HTTP POST {path} {payload.get('type') or ''} failed: {e}") from e


def _http_post(path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
    """Direct HTTP POST over the shared keep-alive connection pool.

    Acquires a rate-limit token first (HL: ~1200 weight/min). On budget
    exhaustion, returns None so the caller's existing retry/backoff handles it
    rather than firing into a 429.
    """
    try:
        return _http_post_strict(path, payload, timeout=timeout)
    except HLRequestError as e:
        if "rate budget exhausted" in str(e):
            logger.warning(f"[hl] {e} — skipping request this retry")
        else:
            logger.error(f"[hl] {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────

def get_info() -> "Info | None":
    """Get the shared Info client instance."""
    global _info_instance
    if _info_instance is None:
        init_info()
    return _info_instance


def resolve_user_address() -> str:
    """The address whose state (fills, positions, open orders) this process
    reads, and that actually trades right now.

    Active trade account in the services.accounts registry first, else the
    legacy fallback (master address if set, else wallet address, else empty
    string) exactly as before.

    Deliberately NOT "whatever account is active for dashboard viewing" — a
    `watch`-mode active account is skipped here on purpose. This function
    backs real trading code (stranded-order cancellation, fill lookups,
    order-status polling), and pointing it at a different
    account than the one actually placing orders would silently break fill
    tracking for whoever IS trading. services/accounts/summary.py is the
    purpose-built way to look up an arbitrary (including watch-mode)
    account's state for display.
    """
    try:
        from services.accounts.credentials import get_trading_credentials
        creds = get_trading_credentials()
        if creds is not None and creds.source != "env":
            return creds.main_address
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[resolve_user_address] accounts registry lookup failed, "
                       f"falling back to legacy env: {e}")
    return os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")


# Short-TTL candle cache. ta_filter and research() each fetched the SAME
# coin's 1h/4h/1d candles back-to-back per scan (6 network calls where 3 suffice),
# doubling the API pressure behind the recurring 429 storms that kill scans. A
# small per-(coin,interval) TTL collapses those duplicates within a cycle. TTL is
# well under a candle period so freshness is unaffected; env-tunable / 0 disables.
_CANDLE_CACHE: Dict[str, Tuple[float, List[Candle]]] = {}
_CANDLE_CACHE_TTL_S = float(os.environ.get("PATHIEL_CANDLE_CACHE_TTL_S", "90"))


def fetch_hl_candles(
    coin: str,
    interval: str = "5m",
    count: int = 100,
) -> List[Candle]:
    """Fetch candles via HTTP (short-TTL cached per coin+interval+count)."""
    cache_key = f"{coin}|{interval}|{count}"
    if _CANDLE_CACHE_TTL_S > 0:
        hit = _CANDLE_CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _CANDLE_CACHE_TTL_S:
            return hit[1]

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
    # A non-list result means a transient failure (429 / timeout), NOT "no
    # candles" — HL returns an empty LIST for a coin with no history. Those are
    # indistinguishable downstream: research treated a 429-blanked fetch as
    # "insufficient history" and emitted stop/tp = 0.0 (which the whale override
    # could then execute without a valid stop). So retry a transient failure a few
    # times with backoff before giving up; a genuine empty list returns at once.
    # INTEGRITY OVER PERFORMANCE (owner directive): a 429-blanked candle fetch makes a
    # coin silently "read as no signal" — a missed setup, and historically the path that
    # let a stop=0 trade slip through. So retry HARDER with exponential backoff before
    # giving up. Bounded (cap per-attempt + total) so a persistent outage can't hang the
    # scan loop and starve monitor_exits. Env-tunable: PATHIEL_CANDLE_RETRIES / _BACKOFF_CAP_S.
    _max_retries = int(os.environ.get("PATHIEL_CANDLE_RETRIES", "6"))
    _backoff_cap = float(os.environ.get("PATHIEL_CANDLE_BACKOFF_CAP_S", "8"))
    raw = _http_post("/info", payload)
    attempts = 0
    while not isinstance(raw, list) and attempts < _max_retries:
        attempts += 1
        time.sleep(min(0.5 * (2 ** (attempts - 1)), _backoff_cap))   # .5,1,2,4,8,8s
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
    if _CANDLE_CACHE_TTL_S > 0 and candles:
        _CANDLE_CACHE[cache_key] = (time.time(), candles)
    return candles


def fetch_account_state(user: str, include_hip3: bool = False, *,
                        hip3_dexes: Optional[Iterable[str]] = None,
                        strict: bool = False) -> Dict[str, Any]:
    """Fetch perp + spot account state, optionally aggregating HIP-3 dexes.

    When `include_hip3=True`, queries each HIP-3 perpDex's clearinghouse
    (one POST per dex), sums equity + total_ntl, concatenates asset_positions
    (HIP-3 coins normalized to `<dex>:<coin>`), and returns `dex_equity` +
    `queried_dexes` (the dexes that actually responded — used by the DSL
    rehydrator to avoid dropping trackers when a dex query times out).

    `available` stays main-dex only because HIP-3 free margin only backs
    trades on its own dex; the executor sizes against this for main trades.

    `hip3_dexes` (with include_hip3): ask exactly these HIP-3 dexes instead of
    every listed one; pathiel.client.account_snapshot re-reads only the dexes
    that held money at its last full sweep. `strict`: a main or spot read
    that never landed (rate budget, 429, timeout) raises HLRequestError
    instead of reading as an empty $0 account. Both default to the old
    behavior, which the trading loop and the executor rely on.
    """
    post = _http_post_strict if strict else _http_post
    perp = post("/info", {"type": "clearinghouseState", "user": user})
    spot = post("/info", {"type": "spotClearinghouseState", "user": user})

    if not perp:
        perp = {}
    if not spot:
        spot = {}

    margin_summary = perp.get("marginSummary", {})
    perp_equity = float(margin_summary.get("accountValue", "0"))
    total_ntl = float(margin_summary.get("totalNtlPos", "0"))
    total_margin_used = float(margin_summary.get("totalMarginUsed", "0"))
    # HL's own "withdrawable" (confirmed live, 2026-10-17: a top-level field on
    # clearinghouseState, distinct from `available` below) — the
    # spot-bridgeable amount, tighter than free trading margin. services.accounts
    # surfaces this as ActiveSummary.withdrawable_usd; nothing else reads it,
    # so a missing/odd value here degrades to 0.0 rather than breaking the
    # existing `available` (margin-based) callers.
    withdrawable = float(perp.get("withdrawable", "0") or 0)

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
    queried_dexes: Set[str] = {""}
    available_aggregated = available  # starts as main; HIP-3 adds in
    withdrawable_aggregated = withdrawable  # starts as main; HIP-3 adds in

    if include_hip3:
        from pathiel.client.universe import list_hip3_dexes
        from concurrent.futures import ThreadPoolExecutor
        if hip3_dexes is not None:
            dexes = [d for d in hip3_dexes if d]
        else:
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
                for dex, dex_state in pool.map(_carry_priority(_fetch_dex), dexes):
                    if dex_state is None:
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
                    withdrawable_aggregated += float(dex_state.get("withdrawable", 0) or 0)
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
        "available": available,                       # main-only — for executor sizing
        "available_aggregated": available_aggregated, # total across all dexes — for display
        "withdrawable": withdrawable,                             # main-only, HL's own tighter number
        "withdrawable_aggregated": withdrawable_aggregated,       # total across all dexes — for display
        "spot_usdc": spot_usdc,
        "total_usdc": equity + spot_usdc,
        "total_ntl": total_ntl,
        "spot_balances": spot_balances,
        "asset_positions": asset_positions,
        "dex_equity": dex_equity,
        "dex_available": dex_available,
        "queried_dexes": queried_dexes,
    }


def missing_material_dexes(last_dex_equity: Optional[Dict[str, float]],
                           queried_dexes: Set[str],
                           floor_usd: float = 0.50) -> Set[str]:
    """Dexes that held material equity on the last good read but are absent
    from this read's `queried_dexes`.

    The held-dex guard in the trading loop only protects dexes backing an
    open position. A dex holding idle USDC but NO position (xyz on
    2026-07-17: $8.47 flat) can drop out of a read silently — the aggregate
    then under-reports equity, poisons daily-PnL memory, and drags the
    daily-loss kill switch toward a false trip. Callers keep a last-known
    dex_equity dict and treat any non-empty return as a partial read."""
    return {d for d, v in (last_dex_equity or {}).items()
            if float(v or 0) >= floor_usd and d not in queried_dexes}


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
        from pathiel.client.universe import list_hip3_dexes
        in_pool.update(list_hip3_dexes())
    except Exception as exc:
        # On failure, a HIP-3-dex-internal transfer gets miscounted as
        # external capital flow, which distorts daily_pnl (feeds the
        # kill-switch) for any account holding HIP-3 positions.
        logger.warning(f"[hl] HIP-3 dex enumeration failed, contribution "
                       f"accounting may miscount HIP-3 transfers: {exc}")

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





def fetch_all_mids(include_hip3: bool = False) -> Dict[str, str]:
    """Get all mid prices.

    HTTP POST only (the never-invoked WebSocket path was deleted 2026-07-10).

    Args:
        include_hip3: when True, also fetches `allMids` for each HIP-3 perpDex
            (one HTTP POST per dex, sequential) and merges results in. Adds
            ~8 small POSTs per call — only enable from contexts that need
            tokenized-equity / commodity prices.
    """
    # Native perp + spot mids (one HTTP POST).
    raw = _http_post("/info", {"type": "allMids"})
    out: Dict[str, str] = {}
    if raw and isinstance(raw, dict):
        out = {k: str(v) for k, v in raw.items()}

    if include_hip3:
        # HIP-3 mids live behind a `dex` parameter. Walk the registered dex list
        # and merge — one POST per dex (~8 total, weight ~2 each).
        from pathiel.client.universe import list_hip3_dexes
        for dex in list_hip3_dexes():
            r = _http_post("/info", {"type": "allMids", "dex": dex})
            if r and isinstance(r, dict):
                for k, v in r.items():
                    out[k] = str(v)
    return out




def fetch_funding_history(
    coin: str, start_time: int, end_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch funding rate history."""
    if end_time is None:
        end_time = int(time.time() * 1000)
    payload = {"type": "fundingHistory", "coin": coin, "startTime": start_time, "endTime": end_time}
    raw = _http_post("/info", payload)
    return raw if isinstance(raw, list) else []


def fetch_user_fills_by_time(
    user: str, start_time_ms: int, end_time_ms: Optional[int] = None, *, max_pages: int = 50,
) -> List[Dict[str, Any]]:
    """Every fill `user` made in [start_time_ms, end_time_ms] (end defaults to
    now), oldest first. userFillsByTime caps at 2000 rows per call; this
    pages forward (same technique as scripts/pnl_by_book.fetch_all_fills)
    using each batch's own latest `time` as the next `startTime`, de-duping
    by `tid` since a page boundary can repeat the last row. [] on no fills,
    a rate-limited request, or no `user` -- never raises (services/strategies/
    trades.py grades what it gets; a 429 mid-sweep should not crash the
    grading job, just undercount until the next sweep)."""
    if not user:
        return []
    if end_time_ms is None:
        end_time_ms = int(time.time() * 1000)
    out: List[Dict[str, Any]] = []
    seen: Set[int] = set()
    cur = start_time_ms
    for _ in range(max_pages):
        payload: Dict[str, Any] = {"type": "userFillsByTime", "user": user, "startTime": cur,
                                   "endTime": end_time_ms}
        batch = _http_post("/info", payload)
        if not isinstance(batch, list) or not batch:
            break
        fresh = [f for f in batch if f.get("tid") not in seen]
        for f in fresh:
            seen.add(f.get("tid"))
        out.extend(fresh)
        if len(batch) < 2000:
            break
        nxt = max((int(f.get("time") or 0) for f in batch), default=cur)
        if nxt <= cur:
            break
        cur = nxt
    out.sort(key=lambda f: f.get("time") or 0)
    return out
