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


def test_classify_news_polarity_is_deterministic():
    # AI's polar read wins over keywords
    assert mr.classify_news_polarity("positive", "token crashes, hack") == ("positive", "news_risk")
    assert mr.classify_news_polarity("negative", "record rally") == ("negative", "news_risk")
    # keyword fallback — the SKHX-class headline reads negative
    pol, src = mr.classify_news_polarity("none",
        "SK Hynix Falls In Seoul After Strong Nasdaq Debut: US Memory Stocks "
        "Decline Overnight Amid Fresh US-Iran Tensions")
    assert (pol, src) == ("negative", "keywords")
    # the LIT burn/rally headline reads positive
    pol, src = mr.classify_news_polarity(None,
        "Lighter Prepares to Burn 15.5 Million LIT in First Revenue-Funded "
        "Supply Reduction, Will LIT Rally?")
    assert (pol, src) == ("positive", "keywords")
    # balanced / contentless -> neutral
    assert mr.classify_news_polarity("none", "SOL price on Jul 11 - Robinhood")[0] == "neutral"


def test_news_ta_quadrant_tags_all_three_quadrants(monkeypatch):
    out = _captured(monkeypatch)
    base = {"verdict": "LONG", "confidence": 0.73, "last_price": 2.0,
            "web_search_used": True}
    # positive news + LONG -> aligned
    assert mr.record_news_ta_quadrant(
        {**base, "coin": "A1", "news_risk": "positive",
         "news_context": "integration news"}, {}) is True
    # positive news + SHORT -> conflict (the SKHX question)
    assert mr.record_news_ta_quadrant(
        {**base, "coin": "A2", "verdict": "SHORT", "news_risk": "positive",
         "news_context": "record listing"}, {}) is True
    # no polar news_risk, negative keywords + LONG -> conflict via keywords
    assert mr.record_news_ta_quadrant(
        {**base, "coin": "A3", "news_risk": "none",
         "news_context": "Exchange hacked, token plunges"}, {}) is True
    # real but contentless news -> neutral
    assert mr.record_news_ta_quadrant(
        {**base, "coin": "A4", "news_risk": "none",
         "news_context": "Coin price on Jul 13 - Robinhood"}, {}) is True
    rows = {kw["coin"]: (book, kw) for book, kw in out}
    assert all(book == "news_ta_quadrant" for book, _ in rows.values())
    assert rows["A1"][1]["meta"]["quadrant"] == "aligned"
    assert rows["A1"][1]["side"] == "long"
    assert rows["A2"][1]["meta"]["quadrant"] == "conflict"
    assert rows["A2"][1]["side"] == "short"
    assert rows["A3"][1]["meta"]["quadrant"] == "conflict"
    assert rows["A3"][1]["meta"]["polarity_source"] == "keywords"
    assert rows["A4"][1]["meta"]["quadrant"] == "neutral"
    for _, kw in rows.values():
        assert kw["horizon_days"] == 1.0 and kw["stop_pct"] == 15.0
        assert kw["meta"]["web_search_used"] is True
        assert kw["entry_ref_px"] == 2.0


def test_news_ta_quadrant_skips_and_dedups(monkeypatch):
    out = _captured(monkeypatch)
    good = {"coin": "Q", "verdict": "SHORT", "news_risk": "positive",
            "news_context": "big listing", "confidence": 0.7, "last_price": 5.0}
    # 'no news' (the SKHX blind spot): nothing to tag
    assert mr.record_news_ta_quadrant(
        {**good, "news_context": "no news"}, {}) is False
    # PASS verdict: no direction, no row
    assert mr.record_news_ta_quadrant(
        {**good, "verdict": "PASS"}, {}) is False
    # missing price reference
    assert mr.record_news_ta_quadrant(
        {**good, "last_price": 0}, {}) is False
    # hot-kill
    assert mr.record_news_ta_quadrant(
        good, {"mover_recorders": {"enabled": False}}) is False
    assert out == []
    # records once, dedups same coin same UTC day
    assert mr.record_news_ta_quadrant(good, {}) is True
    assert mr.record_news_ta_quadrant(good, {}) is False
    assert len(out) == 1


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
