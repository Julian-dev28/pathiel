"""Zero-capital mover recorders (W-M4 PASS-veto + W-M1 b15 near-miss)."""
import pytest

from hermes_trader.agents import mover_recorders as mr


@pytest.fixture(autouse=True)
def _clean_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "_SEEN_FILE", str(tmp_path / "seen.json"))


def _captured(monkeypatch):
    out = []
    monkeypatch.setattr(mr.shadow_ledger, "record",
                        lambda book, **kw: out.append((book, kw)) or {})
    return out


def test_pass_veto_records_movers_only(monkeypatch):
    out = _captured(monkeypatch)
    a = {"coin": "VIRTUAL", "daily_move_pct": 16.0, "daily_volume_usd": 8e6,
         "confidence": 0.55, "last_price": 0.61}
    assert mr.record_mover_pass(a, {}) is True
    book, kw = out[0]
    assert book == "mover_pass" and kw["side"] == "long" and kw["entry_ref_px"] == 0.61
    assert kw["meta"]["move_pct"] == 16.0
    # non-mover PASS: no record
    assert mr.record_mover_pass({"coin": "BTC", "daily_move_pct": 1.2,
                                 "daily_volume_usd": 1e9, "last_price": 60000}, {}) is False
    # same coin same day: deduped
    assert mr.record_mover_pass(a, {}) is False
    assert len(out) == 1


def test_pass_veto_respects_volume_floor_and_disable(monkeypatch):
    out = _captured(monkeypatch)
    thin = {"coin": "THIN", "daily_move_pct": 20.0, "daily_volume_usd": 1e6,
            "last_price": 1.0}
    assert mr.record_mover_pass(thin, {}) is False
    assert mr.record_mover_pass(
        {"coin": "X", "daily_move_pct": 20.0, "daily_volume_usd": 9e6, "last_price": 1.0},
        {"mover_recorders": {"enabled": False}}) is False
    assert out == []


def test_b15_records_crossings_in_up_regime_only(monkeypatch):
    out = []
    monkeypatch.setattr(mr.shadow_ledger, "record",
                        lambda book, **kw: out.append((book, kw)) or {})
    uni = [{"coin": "RUN", "prevDayPx": 100.0, "midPx": 117.0, "dayNtlVlm": 9e6},
           {"coin": "SLOW", "prevDayPx": 100.0, "midPx": 108.0, "dayNtlVlm": 9e6},
           {"coin": "THIN", "prevDayPx": 100.0, "midPx": 130.0, "dayNtlVlm": 1e6}]
    assert mr.record_b15_crossings(uni, btc_up=False, config={}) == 0
    assert mr.record_b15_crossings(uni, btc_up=None, config={}) == 0
    n = mr.record_b15_crossings(uni, btc_up=True, config={})
    assert n == 1 and out[0][0] == "mover_b15_up" and out[0][1]["coin"] == "RUN"
    # second scan same day: deduped
    assert mr.record_b15_crossings(uni, btc_up=True, config={}) == 0


def test_pass_live_opens_bounded_long(monkeypatch):
    out = _captured(monkeypatch)

    class _Claims:
        def claimed_by_others(self, book):
            return set()

        def claim(self, coin, book):
            return True

        def release(self, coin, book):
            pass

        def save(self):
            pass

    monkeypatch.setattr(mr, "get_claims_registry", lambda: _Claims())
    opened = []
    cfg = {"mover_recorders": {"pass_live": {"enabled": True, "shadow_only": False,
                                             "notional_usd": 20.0, "leverage": 1,
                                             "stop_pct": 15.0, "hold_days": 1.0}}}
    a = {"coin": "RIP", "daily_move_pct": 12.0, "daily_volume_usd": 9e6,
         "confidence": 0.5, "last_price": 3.0}
    assert mr.record_mover_pass(a, cfg, execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1
    o = opened[0]
    assert o["strategy_book"] == "mover_pass" and o["side"] == "long"
    assert o["strategy_book_notional"] == 20.0
    assert o["dsl_exit_override"]["max_loss_pct"] == 15.0
    # ledger row marked live
    assert out[0][1]["meta"]["shadow"] is False


def test_pass_live_disabled_records_only(monkeypatch):
    out = _captured(monkeypatch)
    opened = []
    a = {"coin": "RIP2", "daily_move_pct": 12.0, "daily_volume_usd": 9e6, "last_price": 3.0}
    assert mr.record_mover_pass(a, {}, execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == [] and out[0][1]["meta"]["shadow"] is True


def test_trend_block_news_long_records_only_exact_pocket(monkeypatch):
    out = _captured(monkeypatch)
    blocked = {"executed": False,
               "blocked_by": ["trend_filter (long fights the daily 200d-MA downtrend — counter-trend entries bleed)"]}
    base = {"coin": "ARB", "verdict": "LONG", "news_risk": "positive",
            "confidence": 0.75, "last_price": 0.10113, "web_search_used": True}
    assert mr.record_trend_block_news_long(base, blocked, {}) is True
    book, kw = out[0]
    assert book == "trend_block_news_long" and kw["side"] == "long"
    assert kw["entry_ref_px"] == 0.10113 and kw["meta"]["web_search_used"] is True
    # dedup same coin same day
    assert mr.record_trend_block_news_long(base, blocked, {}) is False
    # wrong gate: not recorded
    other = {"executed": False, "blocked_by": ["short_liquidity floor"]}
    assert mr.record_trend_block_news_long({**base, "coin": "X"}, other, {}) is False
    # neutral news: not recorded
    assert mr.record_trend_block_news_long(
        {**base, "coin": "Y", "news_risk": "none"}, blocked, {}) is False
    # executed trades: not recorded
    assert mr.record_trend_block_news_long(
        {**base, "coin": "Z"}, {"executed": True}, {}) is False
    assert len(out) == 1
