"""Books-only mode (rebuild 2026-07-18): main-engine AI-verdict ENTRIES are
gated off via main_engine.entries_enabled=false. Forensics on 2,721 fills:
sub-2h AI-engine churn -$385.59 net vs >=2h holds +$134.96 — entries from
the thought-engine were the #1 measured loss source. Strategy books (tagged
strategy_book) pass; absent config defaults to enabled (old behavior)."""
import pytest

from pathia.agents import executor as ex


def _analysis(**kw):
    d = {"id": "t1", "coin": "BTC", "side": "long", "confidence": 0.9,
         "verdict": "LONG", "entry_px": 100.0}
    d.update(kw)
    return d


def _cfg(entries_enabled):
    return {"mode": "LIVE", "main_engine": {"entries_enabled": entries_enabled}}


def _stub_network(monkeypatch):
    """maybe_execute() reaches several real-network calls past the books-only
    gate (fetch_account_state, get_max_leverage, get_hl_price, ... all real
    HTTP POSTs to api.hyperliquid.xyz). Unpatched, any test whose analysis
    clears the gate (strategy_book set, or entries_enabled defaulting True)
    hits the real network — flaky (ConnectionError) and slow (real
    retry/backoff, seconds per call) offline / on CI. Same full stub set as
    test_claims_registry_and_sizing.py's `_execute` helper."""
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xtest")
    monkeypatch.setattr(ex, "fetch_account_state", lambda user, include_hip3=False: {
        "equity": 60.0,
        "available": 60.0,
        "asset_positions": [],
        "total_ntl": 0.0,
        "dex_equity": {"": 60.0},
        "dex_available": {"": 60.0},
    })
    monkeypatch.setattr(ex, "get_hl_price", lambda coin: 100.0)
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(ex, "set_leverage", lambda coin, lev: None)
    monkeypatch.setattr(ex, "get_hl_atr", lambda interval, period, coin: 1.0)
    monkeypatch.setattr(ex, "entry_size_for_notional",
                        lambda coin, notional, px: round(notional / px, 4))
    monkeypatch.setattr(ex, "min_entry_notional_usd", lambda coin, px: 10.0)
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **kw: {
        "status": "ok", "filled": True, "avg_px": 100.0, "order_id": "test-order",
    })


def test_main_engine_entry_blocked_when_disabled(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis())
    assert r["executed"] is False
    assert r["reason"] == "main_engine_entries_disabled"


def test_book_entry_passes_the_gate(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: _cfg(False))
    r = ex.maybe_execute(_analysis(strategy_book="extreme_fade"))
    # must NOT be blocked by the books-only gate — downstream gates may still
    # refuse (no live account in tests), but the reason must differ
    assert r.get("reason") != "main_engine_entries_disabled"


def test_absent_config_defaults_to_enabled(monkeypatch):
    _stub_network(monkeypatch)
    monkeypatch.setattr(ex, "read_agent_config", lambda: {"mode": "LIVE"})
    r = ex.maybe_execute(_analysis())
    assert r.get("reason") != "main_engine_entries_disabled"


# ── kill-switch rescale (rebuild step 4) ─────────────────────────────────────

from pathia.agents.risk_gates import effective_daily_loss_limit


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


# ── deposit-race peak guard (2026-07-18 incident) ────────────────────────────

def test_deposit_race_does_not_poison_peak_daily_pnl(monkeypatch):
    """The tick where a deposit lands can compute daily_pnl before the
    contributions fetch reflects it — 2026-07-18: +$132 deposit read as
    +$131 daily PnL for one tick, peakDailyPnl froze at 128.28 on a -$4 day
    and the give-back gate blocked ALL entries (books included) until UTC
    roll. A single-tick jump > max($10, 30% of equity) must freeze the peak
    high-water for that tick; the corrected next tick proceeds normally."""
    from pathia.agents.memory import AgentMemory
    m = AgentMemory.__new__(AgentMemory)
    m._start_of_day_equity = 18.0
    m._day_start_ts = 2**63 - 1          # force the "same day" branch
    m._daily_pnl = -0.5
    m._peak_daily_pnl = 0.0
    m._equity = 17.5
    m._last_eq_reading = 0.0   # 0 disables the fast-swing guard's compare
    m._last_eq_reading_ts = 0.0
    import datetime as _dt
    # same-day branch requires day_start >= today's midnight: pin it
    m._day_start_ts = int(_dt.datetime.now(_dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())

    # tick 1: deposit landed ($150 equity) but contributions still report 0
    m.track_daily_pnl(150.0, net_contributions=0.0)
    assert m._peak_daily_pnl == 0.0, "transfer-race tick must not move the peak"

    # tick 2: contributions caught up — honest daily pnl, peak tracks again
    m.track_daily_pnl(150.0, net_contributions=132.0)
    assert m._peak_daily_pnl == pytest.approx(0.0, abs=0.01)
    assert m._daily_pnl == pytest.approx(0.0, abs=0.01)


# ── xs basket exit ownership (2026-07-19 incident) ───────────────────────────

def test_book_owned_holds_skip_ai_close_check():
    """Book-claimed coins are exempt from the AI close-check (their books own
    exits); the loop consults the claims registry before researching a held
    coin. Text-level assertion — trading_loop must never be imported."""
    import os
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "scripts", "trading_loop.py")).read()
    i = src.index("if coin in held_coins:")
    block = src[i:i + 2000]
    assert "owner_of(coin)" in block
    assert "BOOK_OWNED_HOLD" in block
    assert block.index("owner_of(coin)") < block.index("(now_ms - last_research)")


