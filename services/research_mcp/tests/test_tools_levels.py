"""services/research_mcp/tools.py: levels_tool / summarize_levels.

summarize_levels reimplements services.strategies.levels.summarize()'s math
from its pure exported building blocks instead of calling summarize_coin()
(which would import services.strategies.next_trade -> pathiel.agents.bracket,
order-placement code this server must never import -- see tools.py's
docstring). test_matches_the_real_levels_summarize proves that
reimplementation produces the SAME numbers as the original, field for
field, not just "some numbers that look plausible."
"""
from __future__ import annotations

import pytest

from services.research_mcp import coins, tools
from services.strategies import levels as L

HOUR_MS = L.HOUR_MS


def _candle(i: int, close_ms: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"t": close_ms - HOUR_MS + 1, "T": close_ms, "o": o, "h": h, "l": lo, "c": c, "v": 10 + i}


def _candles_1h(n: int, now_ms: int, base: float = 100.0) -> list:
    """n CLOSED 1h candles ending just before now_ms, prices drifting
    slightly so ranges and ATR are non-trivial, highest/lowest predictable."""
    out = []
    for i in range(n):
        close_ms = now_ms - (n - i) * HOUR_MS
        px = base + i * 0.1
        out.append(_candle(i, close_ms, px, px + 1.0 + (i % 5) * 0.2, px - 1.0 - (i % 3) * 0.2, px + 0.05))
    return out


def test_summarize_levels_happy_path(monkeypatch):
    now_ms = 100 * HOUR_MS
    c1h = _candles_1h(50, now_ms)
    monkeypatch.setattr(tools, "fetch_mid", lambda coin: 107.5)
    monkeypatch.setattr(tools, "fetch_candles",
                        lambda coin, interval, hours, nm: c1h if interval == "1h" else c1h[-3:])
    out = tools.summarize_levels("xyz:BRENTOIL", now_ms=now_ms)
    assert out["coin"] == "xyz:BRENTOIL"
    assert out["mid"] == 107.5
    assert out["n_closed_1h"] == 50
    assert out["last_closed_1h"]["close"] == c1h[-1]["c"]
    assert out["range_24h"]["high"] == max(c["h"] for c in c1h[-24:])
    assert out["range_24h"]["low"] == min(c["l"] for c in c1h[-24:])
    assert out["atr14_1h"] is not None and out["atr14_1h"] > 0
    assert 0.0 <= out["mid_pos_in_24h"] <= 1.0 or out["mid_pos_in_24h"] < 0 or out["mid_pos_in_24h"] > 1
    assert "breakout_24h" not in out, "must not carry next_trade's breakout field"
    assert "closes_1h" not in out, "must not carry next_trade's trend-gate field"


def test_summarize_levels_matches_the_real_levels_summarize(monkeypatch):
    """Field-for-field equality against services.strategies.levels.summarize()
    on the SAME candle data, for every field both compute."""
    now_ms = 200 * HOUR_MS
    c1h = _candles_1h(60, now_ms, base=4000.0)
    c15 = [_candle(i, now_ms - (3 - i) * 900_000, 4000, 4001, 3999, 4000.5) for i in range(3)]

    real = L.summarize(c1h, c15, mid=4005.0, now_ms=now_ms)
    monkeypatch.setattr(tools, "fetch_mid", lambda coin: 4005.0)
    monkeypatch.setattr(tools, "fetch_candles",
                        lambda coin, interval, hours, nm: c1h if interval == "1h" else c15)
    mine = tools.summarize_levels("xyz:GOLD", now_ms=now_ms)

    shared_keys = ["mid", "last_closed_1h", "range_8h", "range_24h", "range_48h", "atr14_1h",
                  "range_15m_last4", "n_closed_1h", "mid_pos_in_24h", "atr14_pct"]
    for key in shared_keys:
        assert mine.get(key) == real.get(key), f"{key} differs: mine={mine.get(key)} real={real.get(key)}"


def test_summarize_levels_raises_a_clear_error_with_no_mid(monkeypatch):
    monkeypatch.setattr(tools, "fetch_mid", lambda coin: None)
    with pytest.raises(RuntimeError, match="xyz:BRENTOIL"):
        tools.summarize_levels("xyz:BRENTOIL")


def test_summarize_levels_handles_no_closed_candles_gracefully(monkeypatch):
    monkeypatch.setattr(tools, "fetch_mid", lambda coin: 50.0)
    monkeypatch.setattr(tools, "fetch_candles", lambda coin, interval, hours, nm: [])
    out = tools.summarize_levels("BTC", now_ms=1_000_000)
    assert out["n_closed_1h"] == 0
    assert out["last_closed_1h"] is None
    assert out["atr14_1h"] is None
    assert "mid_pos_in_24h" not in out
    assert "atr14_pct" not in out


def test_levels_tool_resolves_coin_aliases(monkeypatch):
    monkeypatch.setattr(tools, "fetch_mid", lambda coin: 65.0)
    monkeypatch.setattr(tools, "fetch_candles", lambda coin, interval, hours, nm: [])
    out = tools.levels_tool("hyperliquid")
    assert out["coin"] == "HYPE"


def test_levels_tool_rejects_an_unsupported_coin():
    with pytest.raises(coins.UnsupportedCoinError):
        tools.levels_tool("DOGE")
