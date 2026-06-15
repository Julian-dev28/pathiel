"""Tests for the free GEX / max-pain engine (pure functions; no network)."""

from hermes_trader.agents import options_gex
from hermes_trader.agents.options_gex import (
    OptRow, GexReport, parse_occ, underlying_for, compute_gex, compute_max_pain,
    gex_override_caution,
)


def test_parse_occ():
    assert parse_occ("NVDA260624C00240000") == ("NVDA", "260624", True, 240.0)
    assert parse_occ("_SPX260618P06000000") == ("_SPX", "260618", False, 6000.0)
    assert parse_occ("garbage") is None


def test_underlying_mapping():
    assert underlying_for("xyz:NVDA") == "NVDA"
    assert underlying_for("xyz:SP500") == "_SPX"
    assert underlying_for("xyz:GOLD") == "GLD"
    assert underlying_for("HOOD") == "HOOD"


def _chain():
    # spot ~100. Heavy call gamma at 110 (resistance), heavy put gamma at 90 (support).
    return [
        OptRow(110, True, oi=5000, gamma=0.02, delta=0.3, expiry="260101"),
        OptRow(120, True, oi=1000, gamma=0.01, delta=0.1, expiry="260101"),
        OptRow(90, False, oi=5000, gamma=0.02, delta=-0.3, expiry="260101"),
        OptRow(80, False, oi=1000, gamma=0.01, delta=-0.1, expiry="260101"),
    ]


def test_gex_walls_and_regime():
    g = compute_gex(_chain(), spot=100.0)
    assert g["call_wall"] == 110          # biggest call gamma above spot
    assert g["put_wall"] == 90            # biggest put gamma below spot
    assert g["regime"] in ("pin_long_gamma", "trend_short_gamma")
    # symmetric call/put gamma here -> total near zero
    assert abs(g["total"]) < 1.0


def test_gex_positive_when_calls_dominate():
    rows = [OptRow(110, True, oi=10000, gamma=0.03, delta=0.3, expiry="260101"),
            OptRow(90, False, oi=100, gamma=0.01, delta=-0.1, expiry="260101")]
    g = compute_gex(rows, spot=100.0)
    assert g["total"] > 0 and g["regime"] == "pin_long_gamma"


def test_gex_negative_when_puts_dominate():
    rows = [OptRow(110, True, oi=100, gamma=0.01, delta=0.1, expiry="260101"),
            OptRow(90, False, oi=10000, gamma=0.03, delta=-0.3, expiry="260101")]
    g = compute_gex(rows, spot=100.0)
    assert g["total"] < 0 and g["regime"] == "trend_short_gamma"


def test_max_pain_between_clusters():
    # equal call OI at 110 and put OI at 90 -> max pain pulls toward the middle
    mp = compute_max_pain(_chain())
    assert 80 <= mp <= 120


def test_empty_safe():
    g = compute_gex([], spot=0)
    assert g["total"] == 0.0 and g["call_wall"] is None
    assert compute_max_pain([]) is None


# ── override-caution wiring (no network: monkeypatch the cached signal) ───────

def _rep(**kw):
    base = dict(ticker="HOOD", spot=100.0, total_gex=50.0, regime="pin_long_gamma",
                gamma_flip=90.0, call_wall=100.5, put_wall=95.0, max_pain=98.0,
                n_contracts=10, note="")
    base.update(kw)
    return GexReport(**base)


def test_caution_safe_for_crypto_and_shorts(monkeypatch):
    monkeypatch.setattr(options_gex, "gex_signal_cached", lambda *a, **k: _rep())
    # crypto (no colon) → never cautioned
    assert gex_override_caution("BTC", "long") == (False, "")
    # short side → never cautioned
    assert gex_override_caution("xyz:HOOD", "short") == (False, "")


def test_caution_fires_pinned_at_wall(monkeypatch):
    # long-gamma pin, spot above flip, jammed 0.5% under the call wall → suppress
    monkeypatch.setattr(options_gex, "gex_signal_cached",
                        lambda *a, **k: _rep(spot=100.0, call_wall=100.5, gamma_flip=90.0))
    suppress, why = gex_override_caution("xyz:HOOD", "long", near_wall_pct=1.0)
    assert suppress and "pin-trap" in why


def test_caution_allows_below_flip(monkeypatch):
    # spot BELOW gamma flip = trend/squeeze-prone → let it run even at a wall
    monkeypatch.setattr(options_gex, "gex_signal_cached",
                        lambda *a, **k: _rep(spot=100.0, call_wall=100.5, gamma_flip=110.0))
    assert gex_override_caution("xyz:HOOD", "long") == (False, "")


def test_caution_allows_room_to_wall(monkeypatch):
    # wall 5% overhead = room to run → no suppression
    monkeypatch.setattr(options_gex, "gex_signal_cached",
                        lambda *a, **k: _rep(spot=100.0, call_wall=105.0, gamma_flip=90.0))
    assert gex_override_caution("xyz:HOOD", "long", near_wall_pct=1.0) == (False, "")


def test_caution_allows_negative_gamma(monkeypatch):
    # trend_short_gamma regime is the ripper-friendly tape → never suppress
    monkeypatch.setattr(options_gex, "gex_signal_cached",
                        lambda *a, **k: _rep(regime="trend_short_gamma"))
    assert gex_override_caution("xyz:HOOD", "long") == (False, "")


def test_caution_safe_on_no_data(monkeypatch):
    monkeypatch.setattr(options_gex, "gex_signal_cached", lambda *a, **k: None)
    assert gex_override_caution("xyz:HOOD", "long") == (False, "")
