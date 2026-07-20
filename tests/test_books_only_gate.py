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


# ── deposit-race peak guard (2026-07-18 incident) ────────────────────────────

def test_deposit_race_does_not_poison_peak_daily_pnl(monkeypatch):
    """The tick where a deposit lands can compute daily_pnl before the
    contributions fetch reflects it — 2026-07-18: +$132 deposit read as
    +$131 daily PnL for one tick, peakDailyPnl froze at 128.28 on a -$4 day
    and the give-back gate blocked ALL entries (books included) until UTC
    roll. A single-tick jump > max($10, 30% of equity) must freeze the peak
    high-water for that tick; the corrected next tick proceeds normally."""
    from hermes_trader.agents.memory import AgentMemory
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
    import hermes_trader.agents.memory as mem_mod
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

def test_xs_analysis_carries_book_exit_policy():
    """Caught live 2026-07-19, 1.7h after the first full basket deploy: xs
    legs registered under the MAIN-ENGINE DSL policy (30h timeout, 8h
    stale-flat, 2.5% stop) which would shred the validated 5-day
    rebalance-owned hold. The analysis must carry the wide book override."""
    from hermes_trader.agents.xs_momentum_live import _analysis
    a = _analysis("BTC", "long", 0.12, hold_days=5.0)
    dsl = a["dsl_exit_override"]
    assert dsl["hard_timeout_minutes"] == 5.0 * 1440.0     # the full hold
    assert dsl["stale_flat_timeout_minutes"] == 0.0        # no flat-cutter
    assert dsl["protect_pct"] == 1000.0                    # phase-2 never arms
    assert dsl["max_loss_pct"] == 20.0                     # disaster stop only
    assert a["backup_sl_pct_override"] == 20.0
    # shorts carry it too
    s = _analysis("XPL", "short", -0.2, hold_days=5.0)
    assert s["dsl_exit_override"]["hard_timeout_minutes"] == 7200.0


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


def test_xs_exclude_coins_filters_before_ranking():
    """W-X4 (b02276b): declared meme names drop from the eligible set BEFORE
    volume ranking; absent config key changes nothing."""
    from hermes_trader.agents.xs_momentum_live import _eligible
    uni = [{"coin": c, "type": "perp", "dayNtlVlm": 1e9} for c in
           ("BTC", "ETH", "FARTCOIN", "kBONK", "SOL")]
    cfg = {"xs_momentum": {"min_volume_usd": 0, "universe_top_n": 50,
                           "exclude_coins": ["FARTCOIN", "kBONK"]}}
    out = _eligible(uni, cfg)
    assert "FARTCOIN" not in out and "kBONK" not in out
    assert {"BTC", "ETH", "SOL"} <= set(out)
    cfg2 = {"xs_momentum": {"min_volume_usd": 0, "universe_top_n": 50}}
    assert "FARTCOIN" in _eligible(uni, cfg2)


def test_xs_book_integrity_alert_fires_below_40(monkeypatch, caplog):
    """W-X5: below ~$40 equity the 3x-cap legs fall under HL's min order and
    vanish silently — the rebalance must WARN loudly (never block)."""
    import logging
    import hermes_trader.agents.xs_momentum_live as xsl
    from hermes_trader.agents import memory as mem_mod
    monkeypatch.setattr(mem_mod.memory, "_equity", 35.0, raising=False)
    monkeypatch.setattr(xsl, "_last_ts", lambda: 2**62, raising=False)  # not rebalance time
    with caplog.at_level(logging.WARNING):
        xsl.maybe_rebalance({"xs_momentum": {"enabled": True}}, [], [],
                            lambda *a: [], lambda a: None, lambda *a: None)
    assert any("BOOK-INTEGRITY" in r.message for r in caplog.records)
