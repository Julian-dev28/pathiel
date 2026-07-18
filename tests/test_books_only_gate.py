"""Books-only mode (rebuild 2026-07-18): main-engine AI-verdict ENTRIES are
gated off via main_engine.entries_enabled=false. Forensics on 2,721 fills:
sub-2h AI-engine churn -$385.59 net vs >=2h holds +$134.96 — entries from
the thought-engine were the #1 measured loss source. Strategy books (tagged
strategy_book) pass; absent config defaults to enabled (old behavior)."""
import pytest

from hermes_trader.agents import executor as ex


def _analysis(**kw):
    d = {"id": "t1", "coin": "BTC", "side": "long", "confidence": 0.9,
         "verdict": "LONG", "entry_px": 100.0}
    d.update(kw)
    return d


def _cfg(entries_enabled):
    return {"mode": "LIVE", "main_engine": {"entries_enabled": entries_enabled}}


def test_main_engine_entry_blocked_when_disabled(monkeypatch):
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False
    assert r["reason"] == "main_engine_entries_disabled"


def test_book_entry_passes_the_gate(monkeypatch):
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis(strategy_book="extreme_fade"))
    # must NOT be blocked by the books-only gate — downstream gates may still
    # refuse (no live account in tests), but the reason must differ
    assert r.get("reason") != "main_engine_entries_disabled"


def test_absent_config_defaults_to_enabled(monkeypatch):
    monkeypatch.setattr(ex, "read_agent_config", lambda: {"mode": "LIVE"})
    r = ex.maybe_execute(_analysis())
    assert r.get("reason") != "main_engine_entries_disabled"
