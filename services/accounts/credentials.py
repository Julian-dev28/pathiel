"""The one function the trading client actually calls:
`get_trading_credentials()`. Returns the agent private key, the main (funds)
address, and the agent (signing) address for whichever identity should place
orders right now — the active trade account in the registry, else the
`.env.local` fallback — or None if there is nothing usable at all.

Priority, exactly as the task brief states it: active trade account, else env
fallback. A `watch`-mode active account is deliberately never returned here —
this function backs real order placement, and a watch account exists
precisely so it can never do that.

Caching: resolving a vault account decrypts a keystore file (a real,
deliberately-slow KDF — see vault.py), so this is cached and only
recomputed when the registry's on-disk fingerprint (path + mtime + this
process's own mutation counter) changes. That fingerprint is correct across
processes too: the dashboard/API server and a separate trading-loop process
both read the same file, and an mtime change from EITHER process invalidates
the cache in the other.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from services.accounts import registry, vault
from services.accounts import env as accounts_env

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradingCredentials:
    # 0x-prefixed hex, the key that SIGNS orders. repr=False: a dataclass repr
    # lands in assertion output, logs and tracebacks, and once printed a real key.
    private_key: str = field(repr=False)
    main_address: str         # the funds-holding account HL executes orders against
    agent_address: Optional[str]  # the signing address, when distinct from main_address
    source: str                # "wallet_sign" | "api_key" | "env"


def _env_fallback() -> Optional[TradingCredentials]:
    """The legacy `.env.local` identity, read through
    `pathiel.client.exchange.resolve_env_identity()` — not a second,
    independent `os.environ` lookup. That function reads THAT module's own
    PRIVATE_KEY_HEX/HL_WALLET/HL_MASTER/IS_AGENT globals, which is what makes
    this fallback agree with exchange.py's own singleton by construction (see
    that function's docstring) and what keeps a test that monkeypatches those
    globals directly in full control of this path too, regardless of what the
    real process environment happens to hold.
    """
    try:
        from pathiel.client.exchange import resolve_env_identity
    except ImportError:
        return None
    identity = resolve_env_identity()
    if identity is None:
        return None
    return TradingCredentials(
        private_key=identity["private_key"],
        main_address=identity["main_address"],
        agent_address=identity["agent_address"],
        source="env",
    )


def _resolve() -> Optional[TradingCredentials]:
    active = registry.get_active()
    if active is not None and active.mode == "trade":
        try:
            key = vault.load_key(active.id)
        except Exception as e:
            logger.error(f"[accounts.credentials] vault decrypt failed for active "
                         f"account {active.id}: {e}")
            key = None
        if key is not None:
            return TradingCredentials(
                private_key=key, main_address=active.main_address,
                agent_address=active.agent_address, source=active.source,
            )
        logger.warning(f"[accounts.credentials] active trade account {active.id} "
                       f"has no usable vault key — falling back to env")
    return _env_fallback()


_cache_lock = threading.Lock()
_cache_key: Optional[tuple] = None
_cache_value: Optional[TradingCredentials] = None


def _fingerprint() -> tuple:
    path = accounts_env.registry_path()
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        mtime_ns = 0
    return (path, mtime_ns, registry.generation())


def get_trading_credentials() -> Optional[TradingCredentials]:
    global _cache_key, _cache_value
    key = _fingerprint()
    with _cache_lock:
        if _cache_key == key:
            return _cache_value
    result = _resolve()
    with _cache_lock:
        _cache_key = key
        _cache_value = result
    return result


def invalidate_cache() -> None:
    """Force the next `get_trading_credentials()` call to recompute,
    regardless of the registry fingerprint. Mutating the registry through
    registry.py already does this on its own (the write changes the file's
    mtime); this exists for tests that change what the env fallback resolves
    to without touching the registry file at all, and as an explicit escape
    hatch if a caller ever needs one."""
    global _cache_key
    with _cache_lock:
        _cache_key = None
