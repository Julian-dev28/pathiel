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


# ── kill-switch rescale (rebuild step 4) ─────────────────────────────────────

from hermes_trader.agents.risk_gates import effective_daily_loss_limit


def test_pct_limit_scales_with_sod_equity():
    # $18.06 equity, -$0.5 on the day -> SOD 18.56 -> floor -15% = -$2.78
    lim = effective_daily_loss_limit({"max_daily_loss_pct": 0.15,
                                      "max_daily_loss_usd": -100}, 18.06, -0.5)
    assert lim == pytest.approx(-2.784, abs=0.01)


def test_pct_zero_falls_back_to_usd():
    assert effective_daily_loss_limit({"max_daily_loss_usd": -12}, 18.0, 0.0) == -12


def test_degraded_zero_equity_never_yields_zero_floor():
    # equity read 0 (degraded tick): pct path disabled, usd fallback holds
    lim = effective_daily_loss_limit({"max_daily_loss_pct": 0.15,
                                      "max_daily_loss_usd": -100}, 0.0, -5.0)
    assert lim == -100


def test_garbage_config_defaults():
    assert effective_daily_loss_limit({"max_daily_loss_pct": "x",
                                       "max_daily_loss_usd": None}, 20.0, 0.0) == -100
