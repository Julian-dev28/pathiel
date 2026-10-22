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
import time
from typing import Any, Dict, List, Optional, Tuple

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

from pathiel.client.hl_client import (
    HL_API,
    _http_post,
    fetch_hl_candles,
    resolve_user_address,
)
from pathiel.client.http_session import _set_session_timeout

logger = logging.getLogger(__name__)

# ── Environment ────────────────────────────────────────────────────────────────

HL_WALLET = os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")
HL_MASTER = os.environ.get("HYPERLIQUID_MASTER_ADDRESS", "")
PRIVATE_KEY_HEX = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "")

# Unified account: MASTER holds funds, WALLET signs orders
IS_AGENT = bool(HL_MASTER and HL_WALLET and HL_MASTER.lower() != HL_WALLET.lower())
HL_ACCOUNT = HL_MASTER if IS_AGENT else HL_WALLET

HL_LEVERAGE = 5  # 5x cross margin


# ── Credential resolution (services/accounts overhaul, 2026-10-17) ─────────────
#
# This module used to read HYPERLIQUID_* once at import time and never again.
# services/accounts lets Julien connect an account from the UI instead of
# hand-editing .env.local, which means the identity that should sign orders
# can change while this process is already running. The five globals above
# stay EXACTLY as they were (every existing test monkeypatches them directly,
# e.g. tests/test_set_leverage_margin_mode.py, tests/test_mcp_overhaul.py) —
# they are now simply "the legacy env identity" rather than "the only
# identity". Resolution adds one more source ahead of them: an active,
# verified, mode=trade account in services.accounts' registry/vault.


def resolve_env_identity() -> Optional[Dict[str, Any]]:
    """The trading identity implied by THIS module's own PRIVATE_KEY_HEX /
    HL_WALLET / HL_MASTER / IS_AGENT / HL_ACCOUNT globals.

    Deliberately reads those globals, not a fresh os.environ lookup: that is
    what lets a test (or a future caller) fully control this function by
    monkeypatching them directly, and what guarantees this function can never
    disagree with what _make_exchange() has always built — they share one
    source of truth. services.accounts.credentials' own env-fallback leg
    calls this exact function for the same reason: so the registry/vault path
    and the legacy path can never silently drift apart.

    Returns None when there is no legacy key at all (PRIVATE_KEY_HEX empty).
    """
    if not PRIVATE_KEY_HEX:
        return None
    return {
        "private_key": PRIVATE_KEY_HEX,
        "main_address": HL_ACCOUNT,
        "agent_address": HL_WALLET if IS_AGENT else None,
        "is_agent": IS_AGENT,
    }


def _vault_credentials() -> Optional[Any]:
    """services.accounts' active-trade-account credentials, or None.

    Guarded: services/accounts may not be importable in every context this
    module is used from (a minimal script, an old pinned environment before
    that service existed), and this module must keep working exactly as it
    did before that service existed in that case.

    Filters out a `source == "env"` result on purpose. That leg of
    get_trading_credentials() is itself just resolve_env_identity() above —
    i.e. the SAME data this function's caller already has directly via
    PRIVATE_KEY_HEX / HL_ACCOUNT / HL_WALLET / IS_AGENT. Re-trusting it here
    would add nothing when it agrees, and would risk disagreeing with this
    module's own (possibly monkeypatched, e.g. forced empty in
    tests/test_mcp_overhaul.py's live-money-safety suite) globals if the two
    code paths were ever extended differently in the future. The registry
    path is the only thing this function exists to add.
    """
    try:
        from services.accounts.credentials import get_trading_credentials
    except ImportError:
        return None
    try:
        creds = get_trading_credentials()
    except Exception as e:
        logger.warning(f"[_vault_credentials] accounts registry lookup failed, "
                       f"falling back to the legacy env identity: {e}")
        return None
    if creds is None or creds.source == "env":
        return None
    return creds


def _has_usable_credential() -> bool:
    """Is there ANY identity that can sign an order right now? Checked ahead
    of every mutating call (set_leverage, place_*, cancel_orders) exactly
    where `if not PRIVATE_KEY_HEX:` used to be the whole check."""
    if PRIVATE_KEY_HEX:
        return True
    return _vault_credentials() is not None


def _resolve_identity() -> Tuple[str, str, Optional[str], bool]:
    """(private_key_hex, main_address, agent_address, is_agent) actually used
    to sign. The vault account takes priority over the legacy env globals —
    connecting or switching accounts in the UI must take effect immediately,
    with no restart — and falls back to resolve_env_identity()'s view of
    PRIVATE_KEY_HEX / HL_ACCOUNT / HL_WALLET / IS_AGENT when no vault account
    is active. Raises RuntimeError (never silently signs with nothing) when
    neither source has a key."""
    creds = _vault_credentials()
    if creds is not None:
        is_agent = bool(creds.agent_address) and creds.agent_address.lower() != creds.main_address.lower()
        return creds.private_key, creds.main_address, creds.agent_address, is_agent
    if not PRIVATE_KEY_HEX:
        raise RuntimeError("HYPERLIQUID_PRIVATE_KEY not set")
    return PRIVATE_KEY_HEX, HL_ACCOUNT, (HL_WALLET if IS_AGENT else None), IS_AGENT


_exchange_instance = None  # Singleton instance
_exchange_identity_key: Optional[Tuple[str, str, Optional[str]]] = None


def _resolve_perp_dexs() -> Optional[List[str]]:
    """Discover HIP-3 perpDex names so the SDK can resolve colon-namespaced coins.

    Only invoked when HIP-3 is enabled via .agent-config.json `enable_hip3`.
    Returns None when disabled so the SDK uses its default (main perp dex only).

    CRITICAL: the SDK treats `perp_dexs` as *exclusive* — if you pass a list,
    it loads ONLY those dexes and drops the main perp universe. The empty
    string `""` is the sentinel for the main dex. So we must prepend `""` to
    keep BTC/ETH/etc. resolvable alongside the HIP-3 namespaced coins.
    """
    try:
        from pathiel.agents.config_store import read_agent_config
        if not read_agent_config().get("enable_hip3"):
            return None
        from pathiel.client.universe import list_hip3_dexes
        hip3 = list_hip3_dexes()
        return [""] + hip3 if hip3 else None
    except Exception as e:
        logger.warning(f"[_resolve_perp_dexs] HIP-3 dex discovery failed: {e}")
        return None


def _make_exchange() -> Exchange:
    """Create or reuse the Exchange client singleton (avoids WebSocket
    connection limit) for whichever identity _resolve_identity() currently
    resolves to.

    Rebuilds the singleton when that identity changes (a different key, main
    address, or agent address than what built the cached instance) — the
    accounts vault can change which account is active while this process is
    already running, and signing with a stale SDK client would silently keep
    using the PREVIOUS account. Raises exactly as before
    ("HYPERLIQUID_PRIVATE_KEY not set") when nothing is usable at all;
    tests/test_mcp_overhaul.py::TestLiveMoneyToolsNeverTouchNetwork pins this
    message's text and this function's raise-not-return behavior.
    """
    global _exchange_instance, _exchange_identity_key
    key_hex, main_address, agent_address, is_agent = _resolve_identity()

    identity_key = (key_hex, main_address, agent_address)
    if _exchange_instance is not None and _exchange_identity_key == identity_key:
        return _exchange_instance

    clean_key = key_hex[2:] if key_hex.startswith("0x") else key_hex

    # The SDK uses eth_account for signing
    acct = Account.from_key(clean_key)

    # account_address is the FUNDS account, used by a couple of the SDK's own
    # convenience reads (e.g. Exchange.market_close(), which this codebase
    # does not call — it closes via place_hl_order(reduce_only=True) instead)
    # when signing as a distinct agent wallet. It is never sent to Hyperliquid
    # on an order/cancel/leverage call (those are authorized purely by WHO
    # signed, resolved server-side from the agent approval) — grepped
    # confirmed zero non-SDK callers of market_close or .account_address in
    # this repo, so this was previously a latent, inert bug (set to the
    # agent's own address, i.e. equivalent to None) rather than one with any
    # live trading impact. See the accounts-agent final report for the full
    # trace.
    account_address = main_address if is_agent else None

    # perp_dexs= teaches the SDK the HIP-3 dex list at init so name_to_asset
    # can resolve `xyz:NVDA` etc. when enable_hip3 is on. With it None the
    # SDK behaves exactly as before.
    _exchange_instance = _set_session_timeout(Exchange(
        wallet=acct,
        base_url=HL_API,
        account_address=account_address,
        perp_dexs=_resolve_perp_dexs(),
    ))
    _exchange_identity_key = identity_key
    return _exchange_instance


_info_instance: Optional[Info] = None


def _get_info() -> Info:
    """Get (or lazily create) the shared HTTP-only Info client.

    skip_ws=True: the callers only use REST methods (meta, all_mids, l2,
    fills, ...), so no WebSocket connection is opened.
    """
    global _info_instance
    if _info_instance is None:
        _info_instance = _set_session_timeout(Info(skip_ws=True, perp_dexs=_resolve_perp_dexs()))
    return _info_instance


# Per-dex meta cache. szDecimals / pxDecimals / maxLeverage are static, but
# get_coin_index / get_max_leverage used to call info.meta(dex=...) on EVERY
# order + trigger + leverage-set. Under burst load (several executes in one
# cycle) those uncached HTTP calls 429'd, get_coin_index fell through to
# "Unknown coin: xyz:SMSN", and the HIP-3 backup stop-loss silently failed.
# Cache the meta universe per dex so a transient 429 can't break coin
# resolution and we stop hammering the API. TTL is long — meta rarely changes.
_META_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_META_TTL_S = float(os.environ.get("PATHIEL_META_TTL_S", "3600"))


def _cached_universe(dex: Optional[str] = None,
                     force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Return the meta `universe` for a dex (None = main), cached for _META_TTL_S.

    On a fetch failure we serve a stale cached copy if we have one, rather than
    raising — a transient API blip must not break coin resolution mid-execute.

    `force_refresh` bypasses a live cache entry. It exists for the case where the
    cached copy is present but INCOMPLETE — a partial warm after a restart, where
    an entry is missing `maxLeverage`. Serving that quietly is how a 3x book
    opened at 1x on 2026-09-08; see get_max_leverage.
    """
    import time as _time
    key = dex or ""
    hit = _META_CACHE.get(key)
    now = _time.time()
    if hit and not force_refresh and (now - hit[0]) < _META_TTL_S:
        return hit[1]
    info = _get_info()
    try:
        meta = info.meta(dex=dex) if dex else info.meta()
        universe = meta.get("universe", []) or []
        if universe:
            _META_CACHE[key] = (now, universe)
        return universe
    except Exception as e:
        if hit:  # serve stale rather than fail the lookup
            logger.warning(f"[_cached_universe] meta fetch failed for dex={dex!r}; serving stale: {e}")
            return hit[1]
        raise


def prewarm_meta_cache() -> int:
    """Populate the per-dex meta cache at startup, BEFORE the first scan/execute
    burst hammers the API. Without this the cache is cold on restart, so the
    restart-time 429 storm makes get_coin_index/get_max_leverage fall through to
    'Unknown coin' (killing the HIP-3 backup stop-loss) and candle fetches
    return empty. Best-effort: a dex that 429s now will be retried lazily later.
    Returns the number of dex universes successfully cached.
    """
    dexes: List[Optional[str]] = [None]  # main perp dex
    try:
        for d in (_resolve_perp_dexs() or []):
            if d and d not in dexes:
                dexes.append(d)
    except Exception:
        pass
    warmed = 0
    for dex in dexes:
        try:
            if _cached_universe(dex=dex):
                warmed += 1
        except Exception as e:
            logger.warning(f"[prewarm_meta_cache] dex={dex!r} failed (will retry lazily): {e}")
    logger.info(f"[prewarm_meta_cache] warmed {warmed}/{len(dexes)} dex meta universes")
    return warmed


def get_coin_index(coin: str) -> Tuple[int, int, int]:
    """Resolve a coin name to (asset_index, sz_decimals, px_decimals) via the SDK meta endpoint.

    Searches the main perp universe first, then (for HIP-3 colon-namespaced
    coins like `xyz:NVDA`) the parent dex's meta. The returned index is the
    coin's position *within its own universe*; downstream callers (the SDK
    `order`/`update_leverage` etc.) translate name → asset ID internally using
    `perp_dexs`, so this helper is only used for sz/px decimals in our own
    rounding code.
    """
    for i, u in enumerate(_cached_universe()):
        if u["name"] == coin:
            return i, u.get("szDecimals", 5), u.get("pxDecimals", 4)
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        try:
            for i, u in enumerate(_cached_universe(dex=dex)):
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
    # `maxLeverage` missing must NEVER quietly become 1. Callers do
    # `min(requested, get_max_leverage(coin))`, so a 1 here silently opens a 1x
    # position where the book asked for 3x — same risk per trade (loss is on
    # NOTIONAL, not margin) but triple the margin, so the account fits a third
    # of the intended book and the sizing model is wrong without erroring.
    #
    # 2026-09-08: exactly that happened. The watchdog re-exec'd, startup logged
    # "meta prewarm exceeded 3s — metadata will warm lazily", and the two
    # positions opened in that cold window went on at 1x while the config said
    # 3x. Every xyz market allows at least 3x, so 1 was never a real answer.
    def _lev(entry: dict) -> Optional[int]:
        try:
            v = int(entry.get("maxLeverage") or 0)
        except (TypeError, ValueError):
            return None
        return v if v >= 1 else None

    for u in _cached_universe():
        if u["name"] == coin:
            v = _lev(u)
            if v:
                return v
    if ":" in coin:
        dex = coin.split(":", 1)[0]
        for attempt, refresh in enumerate((False, True)):
            try:
                for u in _cached_universe(dex=dex, force_refresh=refresh):
                    if u["name"] == coin:
                        v = _lev(u)
                        if v:
                            return v
                        if refresh:
                            break
                        # found but unusable: the cache is warm-but-partial, so
                        # force one refresh before giving up
                        logger.warning(
                            f"[get_max_leverage] {coin} has no usable maxLeverage "
                            f"in cached meta — forcing a dex refresh")
            except Exception as e:
                logger.warning(f"[get_max_leverage] HIP-3 meta lookup failed for "
                               f"dex={dex} (refresh={refresh}): {e}")
    raise ValueError(
        f"Unknown or unusable maxLeverage for {coin} — refusing rather than "
        f"defaulting to 1x, which would silently mis-size the position")


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
    # Freshness assertion (Phase-0 hardening): an empty main all_mids() is a
    # degraded read, not an empty market — every perp has a mid. Silently
    # returning {} freezes every DSL tracker (advance() short-circuits on a
    # missing mid) with no trace. Flag it loudly.
    if not raw:
        logger.warning(
            "[get_all_hl_mids] FEED-FRESHNESS: main all_mids() returned EMPTY "
            "(degraded read) — DSL trackers will get no price this pass")
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    if include_hip3:
        try:
            from pathiel.client.universe import list_hip3_dexes
            _hip3_dropped = 0
            for dex in list_hip3_dexes():
                try:
                    dex_mids = info.all_mids(dex=dex) or {}
                except Exception as e:
                    _hip3_dropped += 1
                    logger.warning(f"[get_all_hl_mids] HIP-3 all_mids failed for dex={dex}: {e}")
                    continue
                for k, v in dex_mids.items():
                    try:
                        out[k] = float(v)
                    except (TypeError, ValueError):
                        continue
            if _hip3_dropped:
                logger.warning(
                    f"[get_all_hl_mids] FEED-FRESHNESS: {_hip3_dropped} HIP-3 dex(es) "
                    f"dropped from mids this pass — their positions' DSL trackers get "
                    f"no price (peak/floor frozen until next good read)")
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


def set_leverage(coin: str, leverage: int, is_cross: Optional[bool] = None,
                  strict: bool = False) -> Dict[str, Any]:
    """Set leverage for a coin, choosing cross vs isolated based on the market.

    `is_cross`:
      None (default) — auto: isolated on markets flagged `onlyIsolated: true`
        (most HIP-3 + ~3% of native HL, via `_is_isolated_only`), else cross.
        This is today's behavior, unchanged — without it the leverage call
        no-ops on isolated-only markets and the order that follows is
        rejected by HL with "Insufficient margin to place order" despite
        plenty of free margin.
      True on an isolated-only market — HL rejects cross there, so we fall
        back to isolated and log it. The returned `is_cross` reflects what
        was actually sent (False), not what was requested.
      False — isolated, unconditionally (regardless of market type).

    `strict`:
      False (default) — `ok` is True whenever the SDK call didn't raise.
        Matches every existing caller's expectation. NOTE: this preserves a
        known bug — `exchange.update_leverage` can return HTTP 200 with a
        body of `{"status": "err", "response": "<message>"}` (same shape
        `_parse_order_result`/`cancel_orders` check for order/cancel calls),
        and this path reports that as `ok: True` since the SDK call itself
        didn't raise.
      True — `ok` is only True when the HL response body says
        `status == "ok"`; an `err` body surfaces as `ok: False` with HL's
        error string, instead of a false success.

    No-op when no private key is set. `leverage < 1` is rejected before any
    exchange call (and before the `is_cross` meta lookup).
    """
    if not _has_usable_credential():
        return {"ok": False, "error": "no private key"}
    if leverage < 1:
        return {"ok": False, "error": f"invalid leverage {leverage} (< 1)"}

    isolated_only = _is_isolated_only(coin)
    if is_cross is None:
        use_cross = not isolated_only
    elif is_cross and isolated_only:
        logger.info(
            f"[set_leverage] {coin} is isolated-only; cross requested, "
            f"falling back to isolated")
        use_cross = False
    else:
        use_cross = bool(is_cross)

    try:
        exchange = _make_exchange()
        # SDK: update_leverage(leverage, coin, is_cross).
        result = exchange.update_leverage(leverage, coin, is_cross=use_cross)
        if strict:
            if isinstance(result, dict) and result.get("status") == "ok":
                return {"ok": True, "result": result, "is_cross": use_cross}
            err = result.get("response") if isinstance(result, dict) else result
            logger.error(
                f"Failed to set leverage for {coin} (is_cross={use_cross}): {err}")
            return {"ok": False, "error": str(err), "is_cross": use_cross}
        return {"ok": True, "result": result, "is_cross": use_cross}
    except Exception as e:
        logger.error(f"Failed to set leverage for {coin} (is_cross={use_cross}): {e}")
        return {"ok": False, "error": str(e), "is_cross": use_cross}


def _round_price_for_hl(price: float, sz_decimals: int, is_perp: bool = True,
                        is_buy: Optional[bool] = None) -> str:
    """Round a price to satisfy Hyperliquid's two constraints:

    1. Multiple of the tick size: tick = 10^(-(MAX_DECIMALS - sz_decimals))
       where MAX_DECIMALS = 6 for perps, 8 for spot.
    2. At most 5 significant figures total.

    `is_buy` controls rounding direction so an IOC limit always rounds in
    the direction of MORE aggression (preserves cross-the-book intent):
      * BUY → ROUND_CEILING (round UP; limit stays at/above ask)
      * SELL → ROUND_FLOOR (round DOWN; limit stays at/below bid)
    Without this, a SELL price like 0.00016533 (1% below bid 0.000167)
    half-up-rounds to 0.00017, which is ABOVE the bid → IOC can never
    match → "could not immediately match" rejection. Passing None falls
    back to ROUND_HALF_UP for non-order callsites.
    """
    from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR, getcontext
    getcontext().prec = 28

    if price <= 0:
        return "0"

    MAX_DECIMALS = 6 if is_perp else 8
    px_decimals_by_tick = max(0, MAX_DECIMALS - int(sz_decimals))

    import math
    int_digits = max(0, int(math.floor(math.log10(price))) + 1)
    px_decimals_by_sigfig = max(0, 5 - int_digits)
    px_decimals = min(px_decimals_by_tick, px_decimals_by_sigfig)

    if is_buy is True:
        rounding_mode = ROUND_CEILING
    elif is_buy is False:
        rounding_mode = ROUND_FLOOR
    else:
        rounding_mode = ROUND_HALF_UP

    q = Decimal(10) ** -px_decimals if px_decimals > 0 else Decimal(1)
    rounded = (Decimal(str(price)) / q).quantize(Decimal('1'), rounding=rounding_mode) * q
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


def min_entry_notional_usd(coin: str, mid_price: float) -> float:
    """Minimum entry notional after HL size precision is applied.

    This is higher than MIN_ORDER_USD on integer-size markets because the
    smallest valid size may overshoot the dollar floor. Executors should compare
    their intended notional to this before gates so the order layer never
    silently up-sizes a trade after risk checks have passed.
    """
    if mid_price <= 0:
        return 0.0
    _, sz_dec, _ = get_coin_index(coin)
    return _min_order_size(mid_price, sz_dec) * mid_price


def entry_size_for_notional(coin: str, notional_usd: float, mid_price: float) -> float:
    """Coin size the entry order will submit for an intended dollar notional.

    Mirrors place_hl_order's size precision and minimum-order logic, without
    placing anything. Callers can feed this into risk gates and SL/TP sizing so
    their bookkeeping matches the order that will actually be sent.
    """
    if notional_usd <= 0 or mid_price <= 0:
        return 0.0
    _, sz_dec, _ = get_coin_index(coin)
    size = max(notional_usd / mid_price, _min_order_size(mid_price, sz_dec))
    return float(f"{size:.{sz_dec}f}")


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
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """Place an IOC (immediate-or-cancel) limit order on Hyperliquid.

    reduce_only=True for CLOSES: the size floor below bumps small orders to HL's
    $10 minimum, so closing a sub-$10 position (e.g. an illiquid residual) would
    OVERSHOOT and flip the position to the opposite side without this flag. With
    reduce_only, HL fills only up to the live position size and rejects the rest
    → clean flatten, never a flip."""
    if not _has_usable_credential():
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if mid_price <= 0:
        return {"ok": False, "error": f"invalid price for {coin}"}

    try:
        # Always get sz_dec from get_coin_index() (px_dec is ignored — we compute it correctly)
        _, sz_dec, _ = get_coin_index(coin)

        # Price the IOC to cross the live book (best bid/ask + 1% headroom).
        price = _ioc_cross_price(coin, is_buy, mid_price)

        # Round price honoring HL's tick + 5-sigfig rules. is_buy tells the
        # rounder which direction preserves IOC-cross aggression.
        price_str = _round_price_for_hl(price, sz_dec, is_perp=True, is_buy=is_buy)

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
            reduce_only=reduce_only,
        )

        parsed = _parse_order_result(result)
        if not parsed.get("ok"):
            # Surface the HL error + the price/size we sent so future
            # "could not immediately match" failures are diagnosable.
            logger.warning(
                f"[place_hl_order] {coin} {'BUY' if is_buy else 'SELL'} "
                f"size={size_str} px={price_str} REJECTED: {parsed.get('error')}"
            )
        return parsed
    except Exception as e:
        logger.error(f"Failed to place order for {coin}: {e}")
        return {"ok": False, "error": str(e)}


# ── Entry order mode: taker vs maker-first ───────────────────────────────────
# Fee audit (2026-10, docs/overhaul/PROFIT_AUDIT.md): 88/88 fills over the
# trailing 60d were taker (crossed=True) — $0 of maker volume. Taker costs 3x
# maker on the main dex (0.045% vs 0.015%) and ~3x on HIP-3 xyz too (0.009%
# observed vs an implied ~0.003% add rate at the same discount ratio). A
# post-only entry that fills converts that 3x to 1x; one that doesn't still
# costs nothing beyond the fill it would have gotten anyway, because it falls
# back to the exact same taker order `place_hl_order` would have placed.
#
# `entry_order_mode` ("taker" | "maker_first") gates this. Default "taker" —
# every existing call site is unaffected until the operator flips it. Stops,
# emergency exits and the close path never route through here; they call
# `place_hl_order` directly, same as before this existed.
DEFAULT_MAKER_FIRST_TIMEOUT_S = 3.0
DEFAULT_MAKER_FIRST_POLL_INTERVAL_S = 0.5


def _best_touch_price(coin: str, is_buy: bool, mid_price: float) -> float:
    """Best bid (buy) / best ask (sell) from a fresh L2 snapshot — the price a
    post-only maker order joins so it never crosses. Falls back to the mid
    price if the L2 fetch fails or that side is empty (thin/new market); the
    caller's own passive rounding keeps the order non-crossing either way."""
    try:
        levels = _get_info().l2_snapshot(coin).get("levels", [])
        bids, asks = levels[0], levels[1]
        if is_buy and bids:
            return float(bids[0]["px"])
        if not is_buy and asks:
            return float(asks[0]["px"])
    except Exception as e:
        logger.warning(f"[_best_touch_price] l2_snapshot failed for {coin}: {e}")
    return mid_price


def _fills_for_order(oid: Any, coin: str, since_ms: int) -> Tuple[float, float]:
    """(total size filled, size-weighted avg price) for one order id, read from
    the account's own fill history. (0.0, 0.0) when nothing has matched yet.

    `since_ms` is the local time the order was placed; the 5s lookback margin
    absorbs clock skew between this process and the exchange's fill timestamps
    without widening the window enough to risk picking up a same-coin fill from
    an unrelated, immediately-prior order."""
    user = resolve_user_address()
    if not user:
        return 0.0, 0.0
    try:
        fills = _http_post("/info", {
            "type": "userFillsByTime", "user": user,
            "startTime": max(0, since_ms - 5_000),
        }) or []
    except Exception as e:
        logger.warning(f"[_fills_for_order] fill lookup failed for {coin} oid={oid}: {e}")
        return 0.0, 0.0
    matched = [f for f in fills if f.get("coin") == coin and str(f.get("oid")) == str(oid)]
    total_sz = sum(abs(float(f.get("sz") or 0)) for f in matched)
    if total_sz <= 0:
        return 0.0, 0.0
    notional = sum(abs(float(f.get("sz") or 0)) * float(f.get("px") or 0) for f in matched)
    return total_sz, (notional / total_sz if total_sz > 0 else 0.0)


def _place_maker_first(
    is_buy: bool,
    size: float,
    mid_price: float,
    coin: str,
    config: Dict[str, Any],
    reduce_only: bool,
) -> Dict[str, Any]:
    """The maker_first state machine. Raises on any setup failure (coin
    resolution, exchange client) — `place_entry_order` catches that and falls
    back to taker, so every failure mode here degrades to "exactly what taker
    mode would have done," never to a worse or stuck state.

    1. Rest a post-only (ALO) limit AT the current touch (join best bid/ask —
       by construction this cannot cross, so HL either rests it or rejects it
       outright; it can never pay the taker fee).
    2. Poll briefly (`maker_first_poll_interval_s`, up to
       `maker_first_timeout_s`) for it to fill.
    3. Cancel whatever is left resting (a no-op, ignored, if it already filled
       or was rejected) and read the account's OWN fill record for this oid —
       this is the only way to learn the fill price/size for an order that
       filled asynchronously after our last poll.
    4. Whatever didn't fill goes out taker (`place_hl_order`) for the exact
       remainder, at a fresh price — so a book that ran away during the wait
       is handled the same way plain taker mode always handled it.

    Returns the same {ok, order_id?, avg_px?, total_sz?, error?} shape as
    `place_hl_order`, blending the two legs' avg price when both filled.
    """
    _, sz_dec, _ = get_coin_index(coin)
    touch_px = _best_touch_price(coin, is_buy, mid_price)
    # Passive rounding — same convention as place_hl_limit_order: a buy rounds
    # DOWN and a sell rounds UP, so the order never rests at a price more
    # aggressive than the touch it is joining (which would make it crossing).
    maker_price_str = _round_price_for_hl(touch_px, sz_dec, is_perp=True, is_buy=not is_buy)
    order_size = max(size, _min_order_size(mid_price, sz_dec))
    size_str = f"{order_size:.{sz_dec}f}"

    placed_at_ms = int(time.time() * 1000)
    exchange = _make_exchange()
    result = exchange.order(
        coin, is_buy, float(size_str), float(maker_price_str),
        OrderType(limit={"tif": "Alo"}), reduce_only=reduce_only,
    )
    placed = _parse_order_result(result, accept_resting=True)

    if not placed.get("ok"):
        # Post-only rejected outright (would have crossed the book) — no
        # resting order exists to poll or cancel; go straight to taker.
        logger.info(f"[place_entry_order] {coin} ALO rejected "
                    f"({placed.get('error')}) — taker for the full size")
        return place_hl_order(is_buy, size, mid_price, coin, reduce_only=reduce_only)

    if "avg_px" in placed:
        # Not expected from a true post-only order (a fill there would have
        # been a taker cross), but if the venue ever reports one this way the
        # position is already fully open — nothing left to chase.
        return placed

    oid = placed.get("order_id")
    timeout_s = float(config.get("maker_first_timeout_s", DEFAULT_MAKER_FIRST_TIMEOUT_S)
                       or DEFAULT_MAKER_FIRST_TIMEOUT_S)
    poll_s = float(config.get("maker_first_poll_interval_s", DEFAULT_MAKER_FIRST_POLL_INTERVAL_S)
                    or DEFAULT_MAKER_FIRST_POLL_INTERVAL_S)
    max_polls = max(1, int(timeout_s / poll_s)) if poll_s > 0 else 1

    status = "open"
    for i in range(max_polls):
        try:
            status = fetch_order_status(oid)
        except Exception as e:
            logger.warning(f"[place_entry_order] status poll failed for {coin} oid={oid}: {e}")
            status = "error"
        if status != "open":
            break
        if i < max_polls - 1:
            time.sleep(poll_s)

    try:
        cancel_orders(oid, coin)  # harmless no-op if already filled/gone
    except Exception as e:
        logger.warning(f"[place_entry_order] cancel failed for {coin} oid={oid}: {e}")

    filled_sz, filled_avg_px = _fills_for_order(oid, coin, placed_at_ms)
    remainder = max(0.0, order_size - filled_sz)

    if remainder <= 0:
        return {"ok": True, "order_id": str(oid), "avg_px": filled_avg_px, "total_sz": filled_sz}

    logger.info(f"[place_entry_order] {coin} maker leg filled {filled_sz:g}/{order_size:g} "
                f"(status={status}) — taking the remainder {remainder:g} at market")
    taker_res = place_hl_order(is_buy, remainder, mid_price, coin, reduce_only=reduce_only)

    if filled_sz <= 0:
        # Nothing rested; the taker leg alone IS the result.
        return taker_res
    if not taker_res.get("ok"):
        # Partial maker fill + a failed taker remainder: report the position
        # the account actually holds (the maker fill is real) rather than a
        # clean failure that would hide it from the caller.
        return {
            "ok": True, "order_id": str(oid), "avg_px": filled_avg_px, "total_sz": filled_sz,
            "error": f"maker leg filled {filled_sz:g}; taker remainder failed: {taker_res.get('error')}",
        }

    taker_sz = float(taker_res.get("total_sz") or remainder)
    taker_px = float(taker_res.get("avg_px") or mid_price)
    total_sz = filled_sz + taker_sz
    blended_px = ((filled_sz * filled_avg_px) + (taker_sz * taker_px)) / total_sz if total_sz > 0 else 0.0
    return {"ok": True, "order_id": str(oid), "avg_px": blended_px, "total_sz": total_sz}


def place_entry_order(
    is_buy: bool,
    size: float,
    mid_price: float,
    coin: str = "BTC",
    config: Optional[Dict[str, Any]] = None,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """Entry order, routed by `config["entry_order_mode"]` ("taker" | "maker_first").

    This is the ONLY function that should gain a maker-first mode — stops,
    emergency exits and position closes call `place_hl_order` directly and
    must keep doing so (a stop that waits to save a fee is not a stop).

    "taker" (default, or any value other than "maker_first") calls
    `place_hl_order` with the exact same arguments a direct call would have
    used — byte-identical to every call site before this function existed.

    "maker_first" tries a post-only entry first (see `_place_maker_first`) and
    is built so that NO failure mode inside it can end up worse than plain
    taker: any exception anywhere in the maker path is caught here and the
    full order falls back to `place_hl_order`, same as "taker" mode.
    """
    cfg = config or {}
    mode = str(cfg.get("entry_order_mode", "taker") or "taker").lower()
    if mode != "maker_first":
        return place_hl_order(is_buy, size, mid_price, coin, reduce_only=reduce_only)
    if mid_price <= 0:
        return {"ok": False, "error": f"invalid price for {coin}"}
    if size <= 0:
        return {"ok": False, "error": f"invalid size for {coin}"}
    try:
        return _place_maker_first(is_buy, size, mid_price, coin, cfg, reduce_only)
    except Exception as e:
        logger.warning(f"[place_entry_order] maker_first path raised for {coin}, "
                       f"falling back to taker: {e}")
        return place_hl_order(is_buy, size, mid_price, coin, reduce_only=reduce_only)


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
    if not _has_usable_credential():
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


def place_hl_limit_order(
    coin: str,
    is_buy: bool,
    size: float,
    limit_px: float,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """Place a resting GTC limit order: a ladder rung or a take-profit.

    Rounds the price in the PASSIVE direction, the opposite of an IOC: a buy
    rounds down and a sell rounds up, so the order never rests at a worse price
    than the one asked for. If the book has already moved through the price, HL
    fills it on arrival, which is the same fill a resting order would have had.

    Returns {ok, order_id} when it rests, {ok, order_id, avg_px, total_sz} when
    it fills on arrival, {ok: False, error} otherwise.
    """
    if not _has_usable_credential():
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if size <= 0 or limit_px <= 0:
        return {"ok": False, "error": f"invalid size/price for {coin}"}
    try:
        _, sz_dec, _ = get_coin_index(coin)
        price_str = _round_price_for_hl(limit_px, sz_dec, is_perp=True, is_buy=not is_buy)
        size_str = f"{size:.{sz_dec}f}"
        result = _make_exchange().order(
            coin, is_buy, float(size_str), float(price_str),
            OrderType(limit={"tif": "Gtc"}), reduce_only=reduce_only)
        parsed = _parse_order_result(result, accept_resting=True)
        if not parsed.get("ok"):
            logger.warning(f"[place_hl_limit_order] {coin} {'BUY' if is_buy else 'SELL'} "
                           f"size={size_str} px={price_str} REJECTED: {parsed.get('error')}")
        return parsed
    except Exception as e:
        logger.error(f"Failed to place limit order for {coin}: {e}")
        return {"ok": False, "error": str(e)}


def place_hl_bracket_order(coin: str, is_buy: bool, size: float, entry_px: float,
                           stop_px: float, tp_px: float) -> Dict[str, Any]:
    """Rest a GTC limit entry with its stop and take-profit attached, in ONE
    signed action (Hyperliquid grouping "normalTpsl"). The two reduce-only
    children arm only as the entry fills, sized to what filled, so a resting
    entry can never sit naked after a fill, and nothing has to watch it.

    Returns {ok, entry: {...}, sl: {...}, tp: {...}} with each leg parsed by
    _parse_order_result; ok is False if the action or ANY leg was rejected.
    """
    if not _has_usable_credential():
        return {"ok": False, "error": "HYPERLIQUID_PRIVATE_KEY not set"}
    if size <= 0 or min(entry_px, stop_px, tp_px) <= 0:
        return {"ok": False, "error": f"invalid size/price for {coin}"}
    try:
        _, sz_dec, _ = get_coin_index(coin)
        size_f = float(f"{size:.{sz_dec}f}")
        # Entry rounds passively (never rests at a worse price than asked).
        entry_f = float(_round_price_for_hl(entry_px, sz_dec, is_perp=True, is_buy=not is_buy))
        stop_f = float(_round_price_for_hl(stop_px, sz_dec, is_perp=True))
        tp_f = float(_round_price_for_hl(tp_px, sz_dec, is_perp=True))
        close_is_buy = not is_buy

        def _trigger(px: float, kind: str) -> Dict[str, Any]:
            return {"coin": coin, "is_buy": close_is_buy, "sz": size_f, "limit_px": px,
                    "order_type": OrderType(trigger=TriggerOrderType(
                        triggerPx=px, isMarket=True, tpsl=kind)),
                    "reduce_only": True}

        reqs = [
            {"coin": coin, "is_buy": is_buy, "sz": size_f, "limit_px": entry_f,
             "order_type": OrderType(limit={"tif": "Gtc"}), "reduce_only": False},
            _trigger(stop_f, "sl"),
            _trigger(tp_f, "tp"),
        ]
        logger.info(f"[place_hl_bracket_order] {coin} {'BUY' if is_buy else 'SELL'} {size_f} "
                    f"@ {entry_f} sl={stop_f} tp={tp_f}")
        result = _make_exchange().bulk_orders(reqs, grouping="normalTpsl")
        if not (isinstance(result, dict) and result.get("status") == "ok"):
            return {"ok": False, "error": str(result)}
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        legs = []
        for st in statuses[:3]:
            one = {"status": "ok", "response": {"data": {"statuses": [st]}}}
            legs.append(_parse_order_result(one, accept_resting=True)
                        if isinstance(st, dict) and ("resting" in st or "filled" in st or "error" in st)
                        else {"ok": True, "status": st})   # e.g. "waitingForFill"
        while len(legs) < 3:
            legs.append({"ok": False, "error": "no status returned"})
        out = {"ok": all(leg.get("ok") for leg in legs), "entry": legs[0], "sl": legs[1],
               "tp": legs[2], "px": {"entry": entry_f, "stop": stop_f, "tp": tp_f}, "size": size_f}
        if not out["ok"]:
            out["error"] = "; ".join(str(leg.get("error")) for leg in legs if not leg.get("ok"))
        return out
    except Exception as e:
        logger.error(f"Failed to place bracket for {coin}: {e}")
        return {"ok": False, "error": str(e)}


def fetch_order_status(oid: int) -> str:
    """Hyperliquid's status for one order: open, filled, canceled, rejected,
    marginCanceled, ... Returns "unknown" when HL does not know the oid and
    "error" when the read itself failed, which callers must not mistake for a
    cancel."""
    user = resolve_user_address()
    if not user:
        return "error"
    raw = _http_post("/info", {"type": "orderStatus", "user": user, "oid": int(oid)})
    if not isinstance(raw, dict):
        return "error"
    if raw.get("status") == "unknownOid":
        return "unknown"
    try:
        return str(raw["order"]["status"])
    except (KeyError, TypeError):
        return "error"


def _dex_of(coin: str) -> str:
    """The perp dex `coin` trades on: "xyz" for "xyz:CL", "" (main) for "BTC"."""
    return coin.split(":", 1)[0] if ":" in coin else ""


def _names_on_dex(coin: str) -> Tuple[str, ...]:
    """Names `coin` can carry in a read scoped to its own dex. Hyperliquid
    prefixes HIP-3 names there ("xyz:CL"); the bare symbol is accepted too,
    which is unambiguous inside one dex's list."""
    return (coin, coin.split(":", 1)[1]) if ":" in coin else (coin,)


def _resting_orders_for_coin(user: str, coin: str) -> List[Dict[str, Any]]:
    """Every resting order on `coin`, read from the coin's OWN perp dex.

    Hyperliquid answers an open-orders read for one dex at a time, the main
    dex unless `dex` is given, so a bare read never lists an xyz:CL order.
    That is how stranded HIP-3 brackets survived every cleanup until
    2026-10-19. frontendOpenOrders rather than openOrders: same weight, and
    each row says whether it is reduce-only. A failed read gives []."""
    dex = _dex_of(coin)
    payload: Dict[str, Any] = {"type": "frontendOpenOrders", "user": user}
    if dex:
        payload["dex"] = dex
    rows = _http_post("/info", payload)
    names = _names_on_dex(coin)
    return [o for o in (rows if isinstance(rows, list) else [])
            if isinstance(o, dict) and o.get("coin") in names and o.get("oid") is not None]


def _position_size_on_coin(user: str, coin: str) -> Optional[float]:
    """Signed size of the account's position on `coin`, read fresh from the
    coin's own dex: 0.0 when flat, None when the read failed (callers must
    treat None as "unknown", never as flat)."""
    dex = _dex_of(coin)
    payload: Dict[str, Any] = {"type": "clearinghouseState", "user": user}
    if dex:
        payload["dex"] = dex
    state = _http_post("/info", payload)
    if not isinstance(state, dict) or not isinstance(state.get("assetPositions"), list):
        return None
    names = _names_on_dex(coin)
    for row in state["assetPositions"]:
        pos = (row or {}).get("position") or {}
        if pos.get("coin") in names:
            try:
                return float(pos.get("szi") or 0)
            except (TypeError, ValueError):
                return None
    return 0.0


def cancel_open_orders_for_coin(coin: str) -> int:
    """Best-effort: cancel all resting orders for `coin` — e.g. the reduce-only
    SL/TP trigger bracket left stranded after a market close. Without this,
    stale triggers accumulate and a later reduce-only order on the same coin is
    rejected ('reduce only order would increase position'). Reads the coin's
    own dex: xyz:CL orders live on xyz and a main-dex read never sees them.
    Returns the count cancelled. Never raises."""
    try:
        user = resolve_user_address()
        if not user:
            return 0
        n = 0
        for o in _resting_orders_for_coin(user, coin):
            if cancel_orders(int(o["oid"]), coin).get("ok"):
                n += 1
        if n:
            logger.info(f"[cancel_open_orders_for_coin] cancelled {n} stranded order(s) for {coin}")
        return n
    except Exception as e:
        logger.warning(f"[cancel_open_orders_for_coin] {coin} failed: {e}")
        return 0


def cancel_stranded_reduce_only_orders(coin: str) -> int:
    """Cancel the reduce-only orders left on `coin` after its position closed
    outside our own close path: an exchange-side stop or take-profit filled
    (its sibling keeps resting), a manual close, a liquidation.

    A reduce-only order on a flat coin is not harmless clutter. The moment a
    new position opens on that coin in the same direction as the old one,
    the old trigger is live again at a stale price and can close it.

    Only reduce-only orders are touched (a manual entry or a ladder rung is
    not), and only after a fresh read of the coin's own dex says it is flat,
    so one bad position read can never strip a live position's stops.
    Returns the count cancelled. Never raises."""
    try:
        user = resolve_user_address()
        if not user:
            return 0
        size = _position_size_on_coin(user, coin)
        if size is None:
            logger.warning(f"[cancel_stranded_reduce_only_orders] {coin}: position read failed; "
                           f"leaving its orders alone")
            return 0
        if size != 0:
            return 0   # still open: its reduce-only orders are its live stops
        n = 0
        for o in _resting_orders_for_coin(user, coin):
            if o.get("reduceOnly") and cancel_orders(int(o["oid"]), coin).get("ok"):
                n += 1
        if n:
            logger.info(f"[cancel_stranded_reduce_only_orders] cancelled {n} stranded reduce-only "
                        f"order(s) for flat {coin}")
        return n
    except Exception as e:
        logger.warning(f"[cancel_stranded_reduce_only_orders] {coin} failed: {e}")
        return 0


def cancel_orders(oid: int, coin: Optional[str] = None, asset_idx: Optional[int] = None) -> Dict[str, Any]:
    """Cancel an order by order ID."""
    if not _has_usable_credential():
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
