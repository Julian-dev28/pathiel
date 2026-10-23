"""services/strategies/levels.py: the pure candle math is covered end to end
against the real shapes in tests/test_oil_levels.py (which imports this
module directly). This file covers the parts specific to this module: the
live-fetch leg (fetch_candles/fetch_mid/summarize_coin) and its failure
modes, which scripts/oil_levels.py's CLI does not exercise (it has its own
curl-based fetch)."""
from __future__ import annotations

import pytest

from services.strategies import levels as L


def test_fetch_candles_returns_rows_with_the_close_timestamp(monkeypatch):
    import pathiel.client.hl_client as hlc

    def fake_post(path, body):
        assert body["type"] == "candleSnapshot"
        assert body["req"]["coin"] == "xyz:BRENTOIL"
        return [{"t": 0, "T": L.HOUR_MS - 1, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1}]

    monkeypatch.setattr(hlc, "_http_post", fake_post)
    rows = L.fetch_candles("xyz:BRENTOIL", "1h", 50, now_ms=L.HOUR_MS)
    assert rows[0]["T"] == L.HOUR_MS - 1


def test_fetch_candles_degrades_to_empty_on_a_bad_response(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "_http_post", lambda path, body: None)
    assert L.fetch_candles("xyz:BRENTOIL", "1h", 50, now_ms=0) == []


def test_fetch_mid_reads_the_hip3_coin(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "fetch_all_mids", lambda include_hip3=False: {"xyz:BRENTOIL": "107.5"})
    assert L.fetch_mid("xyz:BRENTOIL") == pytest.approx(107.5)


def test_fetch_mid_is_none_when_the_coin_is_missing(monkeypatch):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "fetch_all_mids", lambda include_hip3=False: {})
    assert L.fetch_mid("xyz:BRENTOIL") is None


def test_summarize_coin_raises_a_clear_error_with_no_mid(monkeypatch):
    monkeypatch.setattr(L, "fetch_mid", lambda coin: None)
    with pytest.raises(RuntimeError, match="xyz:BRENTOIL"):
        L.summarize_coin("xyz:BRENTOIL")


def test_summarize_coin_stamps_the_coin_on_the_result(monkeypatch):
    monkeypatch.setattr(L, "fetch_mid", lambda coin: 107.5)
    monkeypatch.setattr(L, "fetch_candles", lambda coin, interval, hours, now_ms=None: [])
    out = L.summarize_coin("xyz:BRENTOIL", now_ms=1_000_000)
    assert out["coin"] == "xyz:BRENTOIL" and out["mid"] == 107.5
