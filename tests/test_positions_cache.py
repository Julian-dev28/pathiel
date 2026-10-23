"""services/accounts/positions.py and services/strategies/levels.py cache their
venue reads. 2026-10-23: the Desk page's polling alone exhausted the
1,200/min Hyperliquid budget (frontendOpenOrders is weight 20) and every
Wallets request timed out."""
from __future__ import annotations

from services.accounts import positions as P
from services.strategies import levels as L


def test_positions_snapshot_is_cached_per_account_and_expires(monkeypatch):
    P.clear_cache()
    clock = [1000.0]
    monkeypatch.setattr(P, "_now", lambda: clock[0])
    calls = []

    def fake_fetch(user, dex):
        calls.append((user, dex))
        return {"state": {"marginSummary": {}, "assetPositions": []}, "orders": [], "mids": {}}
    monkeypatch.setattr(P, "_fetch_raw", fake_fetch)
    a = P.fetch_positions_for_account("0xA", dexes=["xyz"])
    b = P.fetch_positions_for_account("0xA", dexes=["xyz"])
    assert a == b and len(calls) == 1                     # second call within TTL: no venue read
    P.fetch_positions_for_account("0xB", dexes=["xyz"])
    assert len(calls) == 2                                # another account is its own entry
    clock[0] += P.POSITIONS_TTL_S + 1
    P.fetch_positions_for_account("0xA", dexes=["xyz"])
    assert len(calls) == 3                                # expired: read again


def test_dex_discovery_is_cached_longer(monkeypatch):
    P.clear_cache()
    clock = [0.0]
    monkeypatch.setattr(P, "_now", lambda: clock[0])
    n = []
    monkeypatch.setattr(P, "_dexes_with_margin_uncached", lambda user: n.append(1) or ["", "xyz"])
    assert P._dexes_with_margin("0xA") == ["", "xyz"]
    clock[0] += P.POSITIONS_TTL_S + 1                    # positions would have expired, dexes have not
    assert P._dexes_with_margin("0xA") == ["", "xyz"] and len(n) == 1
    clock[0] += P.DEXES_TTL_S
    P._dexes_with_margin("0xA")
    assert len(n) == 2


def test_levels_are_cached_per_coin_unless_a_time_is_pinned(monkeypatch):
    L._LEVELS_CACHE.clear()
    clock = [0.0]
    monkeypatch.setattr(L, "_mono", lambda: clock[0])
    reads = []
    monkeypatch.setattr(L, "fetch_mid", lambda coin: reads.append("mid") or 100.0)
    monkeypatch.setattr(L, "fetch_candles", lambda coin, interval, hours, now_ms: reads.append(interval) or [])
    L.summarize_coin("xyz:GOLD"); L.summarize_coin("xyz:GOLD")
    assert reads.count("mid") == 1                        # one live read served two calls
    L.summarize_coin("xyz:GOLD", now_ms=123)              # a pinned time (tests, replays) bypasses the cache
    assert reads.count("mid") == 2
    clock[0] += L.LEVELS_TTL_S + 1
    L.summarize_coin("xyz:GOLD")
    assert reads.count("mid") == 3
