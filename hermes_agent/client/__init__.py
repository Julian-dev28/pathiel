"""hermes-trader client utilities."""

from hermes_agent.client.hl_client import (
    HL_API,
    _MS_PER_CANDLE,
    fetch_account_state,
    fetch_all_mids,
    fetch_hl_candles,
    get_info,
    start_ws_mids,
    stop_ws_mids,
)
from hermes_agent.client.universe import get_market_by_coin, get_universe

from hermes_agent.client.cache import _Cache, _CacheEntry, cached_api_call, get_global_cache
from hermes_agent.client.lock import scanner_lock, check_lock_status
from hermes_agent.client.parallel import parallel
from hermes_agent.client.daemon import producer_daemon, check_daemon_state

__all__ = [
    # HL API
    "HL_API",
    "_MS_PER_CANDLE",
    "fetch_account_state",
    "fetch_all_mids",
    "fetch_hl_candles",
    "get_info",
    "start_ws_mids",
    "stop_ws_mids",
    # Universe
    "get_universe",
    "get_market_by_coin",
    # Utilities
    "_Cache",
    "_CacheEntry",
    "cached_api_call",
    "get_global_cache",
    "scanner_lock",
    "check_lock_status",
    "parallel",
    "producer_daemon",
    "check_daemon_state",
]
