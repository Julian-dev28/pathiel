"""Tests for the shadow-signal advisor (routing + summary; no network)."""

import hermes_trader.agents.options_gex as gex_mod
import hermes_trader.agents.short_volume as sv_mod
import hermes_trader.agents.crypto_whale as cw_mod
import hermes_trader.agents.news_catalyst as news_mod
from hermes_trader.agents.shadow_signals import (
    gather_shadow_signals, shadow_summary, _base_symbol,
)
from hermes_trader.agents.options_gex import GexReport
from hermes_trader.agents.short_volume import ShortVolReport
from hermes_trader.agents.crypto_whale import WhaleReport


def test_base_symbol():
    assert _base_symbol("xyz:WDC") == "WDC"
    assert _base_symbol("kPEPE") == "PEPE"
    assert _base_symbol("BTC") == "BTC"


def test_gather_xyz_uses_gex_and_shortvol(monkeypatch):
    monkeypatch.setattr(gex_mod, "gex_signal_cached", lambda *a, **k: GexReport(
        ticker="WDC", spot=50.0, total_gex=10.0, regime="pin_long_gamma",
        gamma_flip=49.0, call_wall=51.0, put_wall=48.0, max_pain=50.0, n_contracts=5))
    monkeypatch.setattr(sv_mod, "short_volume_signal", lambda *a, **k: ShortVolReport(
        symbol="WDC", date="20260615", ratio=0.62, regime="crowded_short_squeeze_fuel",
        trend="rising", series=[0.5, 0.62]))
    # crypto + news off to isolate
    sig = gather_shadow_signals("xyz:WDC", "long",
                                {"news": False, "crypto_whale": False})
    assert sig["gex"]["regime"] == "pin_long_gamma"
    assert sig["short_vol"]["regime"] == "crowded_short_squeeze_fuel"
    assert "whale" not in sig
    s = shadow_summary(sig)
    assert "gex=" in s and "shortvol=62%" in s


def test_gather_crypto_uses_whale(monkeypatch):
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: WhaleReport(
        symbol="ETHUSDT", window_n=4000, whale_n=3, buy_usd=500000, sell_usd=100000,
        net_usd=400000, bias="whale_buying", min_usd=100000, window_minutes=15))
    sig = gather_shadow_signals("ETH", "long", {"news": False, "gex": False})
    assert sig["whale"]["bias"] == "whale_buying"
    assert "gex" not in sig and "short_vol" not in sig
    assert "whale=whale_buying" in shadow_summary(sig)


def test_news_only_surfaces_when_elevated(monkeypatch):
    from hermes_trader.agents.news_catalyst import CatalystReport, Article
    # not breaking, low surge -> omitted
    monkeypatch.setattr(news_mod, "catalyst_scan", lambda *a, **k: CatalystReport(
        query="ETH", n_recent=2, breaking=False, surge_x=1.0, headlines=[]))
    sig = gather_shadow_signals("ETH", "long", {"crypto_whale": False, "gex": False})
    assert "news" not in sig
    # breaking -> surfaced
    art = Article(title="ETH ETF approved", url="u", domain="reuters.com", seen=None)
    monkeypatch.setattr(news_mod, "catalyst_scan", lambda *a, **k: CatalystReport(
        query="ETH", n_recent=9, breaking=True, surge_x=4.0, headlines=[art]))
    sig = gather_shadow_signals("ETH", "long", {"crypto_whale": False, "gex": False})
    assert sig["news"]["breaking"] and "BREAKING" in shadow_summary(sig)


def test_summary_empty():
    assert shadow_summary({}) == "(no signal data)"


# ── enforcement (Veto + Boost) ───────────────────────────────────────────────

from hermes_trader.agents.shadow_signals import enforce_signals, Enforcement

_EN = {"signal_enforcement": {"enabled": True, "veto": True, "boost": True,
                              "gex_veto": True, "whale_veto_min_usd": 250000,
                              "whale_boost_min_usd": 250000}}


def test_enforce_disabled_when_flag_off():
    assert enforce_signals("xyz:WDC", "long", {"signal_enforcement": {"enabled": False}}) == Enforcement()


def test_enforce_short_side_noop():
    assert enforce_signals("xyz:WDC", "short", _EN) == Enforcement()


def test_enforce_gex_veto_xyz(monkeypatch):
    monkeypatch.setattr(gex_mod, "gex_override_caution",
                        lambda *a, **k: (True, "pin-trap: jammed under call wall"))
    # news/shortvol off so only veto path matters
    e = enforce_signals("xyz:WDC", "long", _EN)
    assert e.veto and "pin-trap" in e.veto_reason and not e.boost


def test_enforce_whale_veto_crypto(monkeypatch):
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: WhaleReport(
        symbol="SOLUSDT", window_n=5000, whale_n=4, buy_usd=0, sell_usd=600000,
        net_usd=-600000, bias="whale_selling", min_usd=100000, window_minutes=15))
    e = enforce_signals("SOL", "long", _EN)
    assert e.veto and "dumping" in e.veto_reason


def test_enforce_whale_veto_below_threshold_no_veto(monkeypatch):
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: WhaleReport(
        symbol="SOLUSDT", window_n=5000, whale_n=1, buy_usd=0, sell_usd=100000,
        net_usd=-100000, bias="whale_selling", min_usd=100000, window_minutes=15))
    e = enforce_signals("SOL", "long", _EN)        # -100k < 250k threshold
    assert not e.veto


def test_enforce_boost_breaking_news(monkeypatch):
    from hermes_trader.agents.news_catalyst import CatalystReport
    monkeypatch.setattr(news_mod, "catalyst_scan", lambda *a, **k: CatalystReport(
        query="SOL", n_recent=20, breaking=True, surge_x=5.0, headlines=[]))
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: None)
    e = enforce_signals("SOL", "long", _EN)
    assert e.boost and "breaking news" in e.boost_reason and not e.veto


def test_enforce_boost_whale_buying(monkeypatch):
    monkeypatch.setattr(news_mod, "catalyst_scan", lambda *a, **k: None)
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: WhaleReport(
        symbol="ETHUSDT", window_n=6000, whale_n=3, buy_usd=900000, sell_usd=0,
        net_usd=900000, bias="whale_buying", min_usd=100000, window_minutes=15))
    e = enforce_signals("ETH", "long", _EN)
    assert e.boost and "buying" in e.boost_reason


def test_entry_context_roundtrip():
    from hermes_trader.agents.memory import AgentMemory
    m = AgentMemory()
    m._initialized = True   # allow flush() (writes to the conftest temp file)
    m.record_entry_context("ETH", "long",
                           {"entry_time": 123, "signals": {"whale": {"bias": "whale_buying"}}})
    ctx = m.pop_entry_context("ETH", "long")
    assert ctx["entry_time"] == 123
    assert ctx["signals"]["whale"]["bias"] == "whale_buying"
    assert m.pop_entry_context("ETH", "long") == {}   # cleared after pop


def test_enforce_veto_takes_precedence_over_boost(monkeypatch):
    # whale selling (veto) should short-circuit before any boost is considered
    monkeypatch.setattr(cw_mod, "crypto_whale_signal", lambda *a, **k: WhaleReport(
        symbol="SOLUSDT", window_n=5000, whale_n=4, buy_usd=0, sell_usd=600000,
        net_usd=-600000, bias="whale_selling", min_usd=100000, window_minutes=15))
    e = enforce_signals("SOL", "long", _EN)
    assert e.veto and not e.boost
