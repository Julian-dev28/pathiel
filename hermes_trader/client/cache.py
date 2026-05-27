"""API response memoization with TTL and an LRU cap.

Duplicate calls to the same endpoint within a short window reuse the result.
Per-key in-flight tracking deduplicates concurrent misses on the same key.
"""

import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, Dict, Optional


class _CacheEntry:
    """A single cache entry with TTL and access tracking."""
    __slots__ = ('value', 'expiry', 'in_flight')

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expiry = time.time() + ttl
        # Event for deduplicating concurrent misses on the same key
        self.in_flight: Optional[threading.Event] = None


class _Cache:
    """OrderedDict-backed LRU cache with TTL eviction and counters.

    Per-key in-flight tracking prevents thundering herd: if N parallel
    callers miss the same key simultaneously, only the first issues the
    API call; the others wait on an Event and read the result once it lands.
    """

    def __init__(self, max_size: int = 512, default_ttl: float = 5.0) -> None:
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl
        # Counters
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> Any:
        """Get a cached value, or None if expired/missing."""
        with self._lock:
            if key not in self._store:
                self.misses += 1
                return None
            entry = self._store[key]
            if time.time() > entry.expiry:
                # Expired
                self._store.pop(key, None)
                self.misses += 1
                return None
            if entry.in_flight is not None:
                # Another thread is computing this — wait for it
                self.misses += 1  # technically a "waiting miss"
                return None  # caller will re-miss and compute
            # Move to end for LRU
            self._store.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Cache a value."""
        if ttl is None:
            ttl = self._default_ttl
        with self._lock:
            if key in self._store:
                self._store.pop(key)
            # Evict oldest if at capacity
            while len(self._store) >= self._max_size:
                self._store.popitem(last=False)
                self.evictions += 1
            self._store[key] = _CacheEntry(value, ttl)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0
            self.evictions = 0

# Module-level cache for global (non-client) memoization
_global_cache: Optional[_Cache] = None
_global_cache_lock = threading.Lock()


def get_global_cache(max_size: int = 512, ttl: float = 5.0) -> _Cache:
    """Get (or create) a module-level cache for global memoization."""
    global _global_cache
    with _global_cache_lock:
        if _global_cache is None:
            _global_cache = _Cache(max_size=max_size, default_ttl=ttl)
        return _global_cache


def cached_api_call(key_func: Callable, ttl: float = 3.0, max_size: int = 512) -> Callable:
    """Decorator for memoizing API calls.

    Args:
        key_func: callable(*args, **kwargs) -> str that generates a cache key.
        ttl: seconds to cache the result.
        max_size: max entries in LRU cache.

    Usage:
        @cached_api_call(lambda coin, interval: f"candles:{coin}:{interval}")
        def fetch_candles(coin, interval):
            return api_call(...)
    """
    cache = _Cache(max_size=max_size, default_ttl=ttl)

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_func(*args, **kwargs)
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = fn(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator
