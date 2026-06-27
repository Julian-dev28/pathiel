"""PTJ 200-day-MA trend-regime filter — gate-logic unit tests (no network)."""

import hermes_trader.agents.executor as ex


def _patch_direction(monkeypatch, direction):
    monkeypatch.setattr(ex, "_daily_ma_direction", lambda coin, period: direction)


def test_disabled_never_blocks(monkeypatch):
    _patch_direction(monkeypatch, -1)  # downtrend
    cfg = {"trend_filter_200ma": {"enabled": False}}
    assert ex._trend_filter_block_reason({"coin": "BTC", "side": "long"}, cfg) == ""


def test_long_in_uptrend_passes(monkeypatch):
    _patch_direction(monkeypatch, 1)
    cfg = {"trend_filter_200ma": {"enabled": True}}
    assert ex._trend_filter_block_reason({"coin": "BTC", "side": "long"}, cfg) == ""


def test_long_in_downtrend_blocked(monkeypatch):
    _patch_direction(monkeypatch, -1)
    cfg = {"trend_filter_200ma": {"enabled": True}}
    reason = ex._trend_filter_block_reason({"coin": "BTC", "side": "long"}, cfg)
    assert "trend_filter" in reason and "counter-trend" in reason


def test_short_in_downtrend_passes(monkeypatch):
    _patch_direction(monkeypatch, -1)
    cfg = {"trend_filter_200ma": {"enabled": True}}
    assert ex._trend_filter_block_reason({"coin": "BTC", "side": "short"}, cfg) == ""


def test_short_in_uptrend_blocked(monkeypatch):
    _patch_direction(monkeypatch, 1)
    cfg = {"trend_filter_200ma": {"enabled": True}}
    assert "trend_filter" in ex._trend_filter_block_reason({"coin": "BTC", "side": "short"}, cfg)


def test_unknown_history_lenient_by_default(monkeypatch):
    _patch_direction(monkeypatch, 0)  # insufficient daily history
    cfg = {"trend_filter_200ma": {"enabled": True}}
    assert ex._trend_filter_block_reason({"coin": "xyz:NEW", "side": "long"}, cfg) == ""


def test_unknown_history_blocked_when_strict(monkeypatch):
    _patch_direction(monkeypatch, 0)
    cfg = {"trend_filter_200ma": {"enabled": True, "block_unknown": True}}
    assert "insufficient daily history" in ex._trend_filter_block_reason({"coin": "xyz:NEW", "side": "long"}, cfg)


def test_strategy_book_exempt(monkeypatch):
    _patch_direction(monkeypatch, -1)  # would block a normal long
    cfg = {"trend_filter_200ma": {"enabled": True}}
    a = {"coin": "BTC", "side": "long", "strategy_book": "xs_momentum"}
    assert ex._trend_filter_block_reason(a, cfg) == ""


def test_daily_mover_long_bypass_exempts_downtrend(monkeypatch):
    _patch_direction(monkeypatch, -1)  # would block a normal long
    cfg = {
        "trend_filter_200ma": {
            "enabled": True,
            "allow_daily_mover_long_bypass": True,
            "daily_mover_min_ext_pct": 10.0,
            "daily_mover_max_ext_pct": 30.0,
        }
    }
    a = {
        "coin": "IP",
        "side": "long",
        "daily_mover_fired": True,
        "uptrend_momentum_fired": True,
        "slow_burn_count": 1,
        "daily_move_pct": 24.0,
    }

    assert ex._trend_filter_block_reason(a, cfg) == ""


def test_daily_mover_long_bypass_requires_full_pocket(monkeypatch):
    _patch_direction(monkeypatch, -1)
    cfg = {
        "trend_filter_200ma": {
            "enabled": True,
            "allow_daily_mover_long_bypass": True,
            "daily_mover_min_ext_pct": 10.0,
            "daily_mover_max_ext_pct": 30.0,
        }
    }
    a = {
        "coin": "IP",
        "side": "long",
        "daily_mover_fired": True,
        "uptrend_momentum_fired": False,
        "slow_burn_count": 1,
        "daily_move_pct": 24.0,
    }

    assert "trend_filter" in ex._trend_filter_block_reason(a, cfg)
