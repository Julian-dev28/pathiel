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
def test_pass_short_records_the_inverse_side(monkeypatch):
    out = _captured(monkeypatch)
    a = {"coin": "VIRTUAL", "daily_move_pct": 16.0, "daily_volume_usd": 8e6,
         "confidence": 0.55, "last_price": 0.61}
    assert mr.record_mover_pass_short(a, {}) is True
    book, kw = out[0]
    assert book == "mover_pass_short" and kw["side"] == "short" and kw["entry_ref_px"] == 0.61
    assert kw["meta"]["move_pct"] == 16.0
    assert mr.record_mover_pass_short(a, {}) is False   # same coin same day: deduped
def test_pass_short_live_opens_bounded_short(monkeypatch):
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
    cfg = {"mover_recorders": {"pass_short_live": {"enabled": True, "shadow_only": False,
                                                   "notional_usd": 20.0, "leverage": 10,
                                                   "stop_pct": 15.0, "hold_days": 1.0}}}
    a = {"coin": "RIP", "daily_move_pct": 12.0, "daily_volume_usd": 9e6,
         "confidence": 0.5, "last_price": 3.0}
    assert mr.record_mover_pass_short(a, cfg, execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1
    o = opened[0]
    assert o["strategy_book"] == "mover_pass_short" and o["side"] == "short"
    assert o["verdict"] == "SHORT"
    assert o["strategy_book_notional"] == 20.0 and o["leverage_override"] == 10
    assert o["dsl_exit_override"]["max_loss_pct"] == 15.0
    assert out[0][1]["meta"]["shadow"] is False


def test_pass_short_xyz_equity_uses_the_250k_floor_crypto_uses_5m():
    cfg = {"min_volume_usd": 5_000_000.0}
    equity = mr._pass_short_live_analysis("xyz:IBM", 12.0, cfg)
    crypto = mr._pass_short_live_analysis("ARB", 12.0, cfg)
    assert equity["min_short_volume_usd_override"] == 250_000.0
    assert crypto["min_short_volume_usd_override"] == 5_000_000.0


def test_pass_short_live_disabled_records_only(monkeypatch):
    out = _captured(monkeypatch)
    opened = []
    a = {"coin": "RIP2", "daily_move_pct": 12.0, "daily_volume_usd": 9e6, "last_price": 3.0}
    assert mr.record_mover_pass_short(a, {}, execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == [] and out[0][1]["meta"]["shadow"] is True


# --------------------------------------------------------------------- real-registry collision
def test_mover_pass_and_mover_pass_short_cannot_both_claim_the_same_coin(tmp_path):
    """Real ClaimsRegistry (not a mock): whichever book claims the coin first
    this cycle holds it; the other must see it in claimed_by_others and back
    off. This is the exact race the two books share every PASS event."""
    from hermes_trader.agents.rebalancer_owned import ClaimsRegistry

    claims_path = str(tmp_path / ".rebalancer_claims.json")
    registry = ClaimsRegistry(claims_path).load()

    assert registry.claim("VIRTUAL", "mover_pass") is True
    assert "VIRTUAL" in registry.claimed_by_others("mover_pass_short")
    assert registry.claim("VIRTUAL", "mover_pass_short") is False
    registry.release("VIRTUAL", "mover_pass")
    assert registry.claim("VIRTUAL", "mover_pass_short") is True


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


# --------------------------------------------------------------------- young_mover_short
def test_young_mover_short_records_inverse_of_the_history_floor(monkeypatch):
    out = _captured(monkeypatch)
    ok = mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5, {})
    assert ok is True
    book, kw = out[0]
    assert book == "young_mover_short" and kw["side"] == "short"
    assert kw["entry_ref_px"] == 12.5
    assert kw["meta"]["listing_days"] == 29 and kw["meta"]["equity"] is True


def test_young_mover_short_ignores_other_preflight_reasons(monkeypatch):
    out = _captured(monkeypatch)
    for reason in ("liquidity_floor_preflight ($0.10M < $0.70M)",
                   "runner_gate_blocked (needs volume+breakout)",
                   "cooldown (12min remaining)", ""):
        assert mr.record_young_mover_short("xyz:X", reason, 10.0, {}) is False
    assert out == []


def test_young_mover_short_needs_a_real_price(monkeypatch):
    _captured(monkeypatch)
    assert mr.record_young_mover_short(
        "xyz:X", "history_floor_preflight (5d < 60d history)", 0.0, {}) is False


def test_young_mover_short_crypto_records_but_never_trades(monkeypatch):
    """Evidence boundary: the n=126 sample is xyz equities. Young CRYPTO
    listings pointed the same way on n=9, which is not evidence — CASHCAT-class
    coins record at zero capital until they earn their own forward n."""
    out = _captured(monkeypatch)

    class _Claims:
        def claimed_by_others(self, book): return set()
        def claim(self, coin, book): return True
        def release(self, coin, book): pass
        def save(self): pass

    monkeypatch.setattr(mr, "get_claims_registry", lambda: _Claims())
    opened = []
    cfg = {"mover_recorders": {"young_short_live": {
        "enabled": True, "shadow_only": False, "notional_usd": 20.0,
        "leverage": 10, "stop_pct": 6.0, "hold_days": 1.0}}}
    assert mr.record_young_mover_short(
        "CASHCAT", "history_floor_preflight (10d < 60d history)", 0.06, cfg,
        execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert out[0][0] == "young_mover_short"   # recorded
    assert opened == []                        # never traded


def test_young_mover_short_equity_opens_bounded_short(monkeypatch):
    _captured(monkeypatch)

    class _Claims:
        def claimed_by_others(self, book): return set()
        def claim(self, coin, book): return True
        def release(self, coin, book): pass
        def save(self): pass

    monkeypatch.setattr(mr, "get_claims_registry", lambda: _Claims())
    opened = []
    cfg = {"mover_recorders": {"young_short_live": {
        "enabled": True, "shadow_only": False, "notional_usd": 20.0,
        "leverage": 10, "stop_pct": 6.0, "hold_days": 1.0}}}
    assert mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5, cfg,
        execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert len(opened) == 1
    o = opened[0]
    assert o["strategy_book"] == "young_mover_short" and o["side"] == "short"
    assert o["leverage_override"] == 10
    assert o["dsl_exit_override"]["max_loss_pct"] == 6.0
    assert o["dsl_exit_override"]["hard_timeout_minutes"] == 1440.0


def test_young_mover_short_dedups_per_coin_per_day(monkeypatch):
    _captured(monkeypatch)
    r = "history_floor_preflight (29d < 60d history)"
    assert mr.record_young_mover_short("xyz:ZHIPU", r, 12.5, {}) is True
    assert mr.record_young_mover_short("xyz:ZHIPU", r, 12.5, {}) is False


# --------------------------------------------------- regime tag (forward-gradeable)
def test_young_mover_short_carries_a_macro_regime_tag(monkeypatch):
    """The 2026-07-20 finding — young-listing shorts pay +6.03%/85% when the
    equity index is UP over 7d vs +0.18%/48% when down — was a RETROSPECTIVE
    slice. Tagging every row with the macro regime makes it a forward-gradeable
    hypothesis (shadow_status --meta macro_regime=up), so the loop settles it
    on live evidence instead of a one-window backtest."""
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "_macro_regime", lambda coin: "up")
    mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5, {})
    assert out[0][1]["meta"]["macro_regime"] == "up"


def test_macro_regime_never_raises_and_degrades_to_none(monkeypatch):
    """Metadata must never break a recorder. A regime-detect failure yields
    None, not an exception that would drop the ledger row."""
    def boom(*a, **k):
        raise RuntimeError("regime backend down")
    monkeypatch.setattr(
        "hermes_trader.agents.market_regime.detect_regime", boom, raising=False)
    assert mr._macro_regime("xyz:ZHIPU") is None
    assert mr._macro_regime("BTC") is None


def test_mover_pass_short_carries_regime_tag(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "_macro_regime", lambda coin: "down")
    a = {"coin": "VIRTUAL", "daily_move_pct": 16.0, "daily_volume_usd": 8e6,
         "confidence": 0.55, "last_price": 0.61}
    mr.record_mover_pass_short(a, {})
    assert out[0][1]["meta"]["macro_regime"] == "down"


# --------------------------------------------------- news_ta_aligned (LIVE aligned quadrant)
class _OKClaims:
    def claimed_by_others(self, book): return set()
    def claim(self, coin, book): return True
    def release(self, coin, book): pass
    def save(self): pass


def _aligned_cfg(**over):
    c = {"enabled": True, "shadow_only": False, "notional_usd": 20.0,
         "leverage": 3, "stop_pct": 15.0, "hold_days": 1.0, "min_confidence": 0.0}
    c.update(over)
    return {"news_ta_aligned": c}


def test_news_ta_aligned_trades_positive_news_long(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "AL1", "verdict": "LONG", "news_risk": "positive",
         "news_context": "record partnership launch", "confidence": 0.62,
         "last_price": 4.0, "web_search_used": True}
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(), execute_fn=lambda x: opened.append(x) or {"executed": True})
    # recorded to its OWN ledger, aligned, real confidence preserved
    book, kw = out[0]
    assert book == "news_ta_aligned" and kw["side"] == "long"
    assert kw["meta"]["quadrant"] == "aligned" and kw["meta"]["confidence"] == 0.62
    assert kw["meta"]["shadow"] is False and kw["stop_pct"] == 15.0
    # opened with the right geometry, confidence hardcoded 0.99 to clear exec gates
    assert len(opened) == 1
    o = opened[0]
    assert o["strategy_book"] == "news_ta_aligned" and o["verdict"] == "LONG"
    assert o["confidence"] == 0.99 and o["leverage_override"] == 3
    assert o["strategy_book_notional"] == 20.0
    assert o["dsl_exit_override"]["max_loss_pct"] == 15.0
    assert o["dsl_exit_override"]["max_loss_roe_pct"] == 45.0   # 15% * 3x, fires before liq
    assert o["dsl_exit_override"]["hard_timeout_minutes"] == 1440.0


def test_news_ta_aligned_trades_negative_news_short(monkeypatch):
    _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "AL2", "verdict": "SHORT", "news_risk": "negative",
         "news_context": "exchange hacked, token plunges", "confidence": 0.7,
         "last_price": 2.0}
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(), execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1 and opened[0]["side"] == "short" and opened[0]["verdict"] == "SHORT"


def test_news_ta_aligned_conflict_never_trades(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    ex = lambda x: opened.append(x) or {"executed": True}
    # positive news + SHORT verdict = conflict (the AI fights the news)
    assert mr.maybe_trade_news_ta_aligned(
        {"coin": "C1", "verdict": "SHORT", "news_risk": "positive",
         "news_context": "record listing", "confidence": 0.8, "last_price": 3.0},
        _aligned_cfg(), execute_fn=ex) is False
    # negative news + LONG verdict = conflict
    assert mr.maybe_trade_news_ta_aligned(
        {"coin": "C2", "verdict": "LONG", "news_risk": "negative",
         "news_context": "lawsuit, delisting", "confidence": 0.8, "last_price": 3.0},
        _aligned_cfg(), execute_fn=ex) is False
    # neutral news = not aligned
    assert mr.maybe_trade_news_ta_aligned(
        {"coin": "C3", "verdict": "LONG", "news_risk": "none",
         "news_context": "Coin price on Jul 13 - Robinhood", "confidence": 0.8,
         "last_price": 3.0}, _aligned_cfg(), execute_fn=ex) is False
    assert opened == [] and out == []       # conflict/neutral: no trade AND no ledger row


def test_news_ta_aligned_skips_non_directional_and_newsless(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    good = {"coin": "S", "verdict": "LONG", "news_risk": "positive",
            "news_context": "big launch", "confidence": 0.7, "last_price": 5.0}
    assert mr.maybe_trade_news_ta_aligned({**good, "verdict": "PASS"}, _aligned_cfg()) is False
    assert mr.maybe_trade_news_ta_aligned({**good, "news_context": "no news"}, _aligned_cfg()) is False
    assert mr.maybe_trade_news_ta_aligned({**good, "last_price": 0}, _aligned_cfg()) is False
    assert mr.maybe_trade_news_ta_aligned(good, {"news_ta_aligned": {"enabled": False}}) is False
    assert out == []


def test_news_ta_aligned_min_confidence_gate(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "LOWCONF", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.30, "last_price": 5.0}
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(min_confidence=0.55),
        execute_fn=lambda x: opened.append(x) or {"executed": True}) is False
    assert opened == [] and out == []


def test_news_ta_aligned_shadow_only_records_but_never_trades(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "SH", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.7, "last_price": 5.0}
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(shadow_only=True),
        execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == []                                   # no live entry
    assert out[0][0] == "news_ta_aligned" and out[0][1]["meta"]["shadow"] is True


def test_news_ta_aligned_dedups_per_coin_per_day(monkeypatch):
    _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "DD", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.7, "last_price": 5.0}
    ex = lambda x: opened.append(x) or {"executed": True}
    assert mr.maybe_trade_news_ta_aligned(a, _aligned_cfg(), execute_fn=ex) is True
    assert mr.maybe_trade_news_ta_aligned(a, _aligned_cfg(), execute_fn=ex) is False
    assert len(opened) == 1


def test_news_ta_aligned_dedup_key_is_independent_of_the_recorder(monkeypatch):
    """The recorder (ntq) and the trader (ntq_trade) must use SEPARATE dedup keys
    so tagging the research quadrant never suppresses the live trade on the same
    coin+day, and vice versa."""
    _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    opened = []
    a = {"coin": "IND", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.7, "last_price": 5.0}
    # recorder fires first (consumes the "ntq" key)
    assert mr.record_news_ta_quadrant(a, {}) is True
    # trader still fires (its own "ntq_trade" key is untouched)
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(), execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1


def test_news_ta_aligned_backs_off_when_coin_claimed_by_another_book(monkeypatch):
    """A coin already owned by another live book must NOT be double-booked. The
    aligned trade records (evidence) but never opens."""
    out = _captured(monkeypatch)

    class _Taken:
        def claimed_by_others(self, book): return {"TAKEN"}
        def claim(self, coin, book): return False
        def release(self, coin, book): pass
        def save(self): pass

    monkeypatch.setattr(mr, "get_claims_registry", lambda: _Taken())
    opened = []
    a = {"coin": "TAKEN", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.7, "last_price": 5.0}
    assert mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(), execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == []                                  # not double-booked
    assert out[0][0] == "news_ta_aligned"                # still recorded as evidence


def test_news_ta_aligned_carries_macro_regime_tag(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_macro_regime", lambda coin: "up")
    a = {"coin": "RG", "verdict": "LONG", "news_risk": "positive",
         "news_context": "launch", "confidence": 0.7, "last_price": 5.0}
    mr.maybe_trade_news_ta_aligned(
        a, _aligned_cfg(), execute_fn=lambda x: {"executed": True})
    assert out[0][1]["meta"]["macro_regime"] == "up"


def test_news_ta_aligned_leverage_stop_fires_before_liquidation():
    """15% stop needs 1/lev - maint > 0.15. At the default 3x the stop (max_loss_pct)
    is well inside the ~33% liquidation band; a 6x would liquidate at ~14.7% BEFORE
    the 15% stop, so the default must stay <= 4x."""
    o = mr._ntq_aligned_analysis("ETH", "long", {"leverage": 3, "stop_pct": 15.0})
    lev, stop = o["leverage_override"], o["dsl_exit_override"]["max_loss_pct"]
    maint = 0.02
    assert (1.0 / lev - maint) > (stop / 100.0)      # stop fires before liquidation
    assert lev <= 4
