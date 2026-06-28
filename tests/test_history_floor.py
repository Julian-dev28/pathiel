"""Tests for the history-age preflight gate (risk_gates.history_floor_reason)."""
from hermes_trader.agents.risk_gates import history_floor_reason


def _bars(n):
    return [{"t": i, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1} for i in range(n)]


def test_disabled_when_zero():
    assert history_floor_reason("ALT", 0, lambda c, n: _bars(3)) == ""
    assert history_floor_reason("ALT", None, lambda c, n: _bars(3)) == ""


def test_blocks_young_coin():
    r = history_floor_reason("NEWCOIN", 60, lambda c, n: _bars(6))
    assert r.startswith("history_floor_preflight")
    assert "6d < 60d" in r


def test_passes_coin_with_enough_history():
    assert history_floor_reason("BTC", 60, lambda c, n: _bars(90)) == ""
    assert history_floor_reason("BTC", 60, lambda c, n: _bars(60)) == ""  # exactly at floor


def test_fail_open_on_empty_read():
    assert history_floor_reason("ALT", 60, lambda c, n: []) == ""
    assert history_floor_reason("ALT", 60, lambda c, n: None) == ""


def test_fail_open_on_fetch_exception():
    def boom(c, n):
        raise RuntimeError("429")
    assert history_floor_reason("ALT", 60, boom) == ""


def test_requests_enough_bars():
    seen = {}
    def fetch(c, n):
        seen["n"] = n
        return _bars(n)
    history_floor_reason("ALT", 60, fetch)
    assert seen["n"] >= 60
