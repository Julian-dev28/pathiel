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
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: 0.02)   # W-Y4 gate PASS (eq7>0)
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
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: 0.02)   # W-Y4 gate PASS (eq7>0)
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


# --------------------------------------------------- W-Y4 regime gate (young_mover_short)
class _Bar:
    def __init__(self, c): self.c = c


def _young_gate_cfg(**over):
    lc = {"enabled": True, "shadow_only": False, "regime_gate_eq7": True,
          "notional_usd": 20.0, "leverage": 10, "stop_pct": 6.0, "hold_days": 1.0}
    lc.update(over)
    return {"mover_recorders": {"young_short_live": lc}}


def test_equity_index_7d_drops_forming_bar_and_computes_7d_return(monkeypatch):
    import hermes_trader.client.hl_client as hl
    mr._EQ7_CACHE.clear()
    # 12 bars; index 11 (999) is the FORMING bar and must be dropped.
    # completed closes = 11 values; [-1]=110, [-8]=100 -> 110/100 - 1 = 0.10
    bars = [_Bar(x) for x in (100, 100, 100, 100, 101, 102, 103, 104, 105, 106, 110, 999)]
    monkeypatch.setattr(hl, "fetch_hl_candles", lambda coin, interval, n: bars)
    assert abs(mr._equity_index_7d() - 0.10) < 1e-9


def test_equity_index_7d_fail_closed(monkeypatch):
    import hermes_trader.client.hl_client as hl
    mr._EQ7_CACHE.clear()
    def boom(*a, **k): raise RuntimeError("api down")
    monkeypatch.setattr(hl, "fetch_hl_candles", boom)
    assert mr._equity_index_7d() is None                 # exception -> None (fail-closed)
    mr._EQ7_CACHE.clear()
    monkeypatch.setattr(hl, "fetch_hl_candles", lambda *a, **k: [_Bar(100)] * 5)
    assert mr._equity_index_7d() is None                 # <8 completed closes -> None


def test_young_mover_short_regime_gate_pass_trades(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: 0.02)     # eq7 UP
    opened = []
    assert mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5,
        _young_gate_cfg(), execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert len(opened) == 1                              # gate pass -> live entry
    assert out[0][1]["meta"]["regime_gate"] == "pass"
    assert out[0][1]["meta"]["eq_idx_7d"] == 0.02
    assert out[0][1]["meta"]["shadow"] is False


def test_young_mover_short_regime_gate_fail_records_but_no_trade(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: -0.01)    # eq7 DOWN
    opened = []
    assert mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5,
        _young_gate_cfg(), execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert opened == []                                  # gate fail -> no live entry
    assert out[0][1]["meta"]["regime_gate"] == "fail"    # counterfactual still recorded
    assert out[0][1]["meta"]["shadow"] is True


def test_young_mover_short_regime_gate_fail_closed_on_fetch_failure(monkeypatch):
    _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: None)     # fetch failed
    opened = []
    mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5,
        _young_gate_cfg(), execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert opened == []                                  # unknown regime -> fail-closed


def test_young_mover_short_gate_disabled_trades_and_still_tags_regime(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: -0.05)    # DOWN, but gate OFF
    opened = []
    mr.record_young_mover_short(
        "xyz:ZHIPU", "history_floor_preflight (29d < 60d history)", 12.5,
        _young_gate_cfg(regime_gate_eq7=False),
        execute_fn=lambda a: opened.append(a) or {"executed": True})
    assert len(opened) == 1                              # gate off -> trades despite down
    assert out[0][1]["meta"]["regime_gate"] == "fail"    # meta still records what the gate saw


# --------------------------------------------------- W-Y4 regime gate on mover_pass_short (option C)
def _pass_gate_cfg(**over):
    lc = {"enabled": True, "shadow_only": False, "regime_gate_eq7": True,
          "notional_usd": 20.0, "leverage": 10, "stop_pct": 6.0, "hold_days": 1.0}
    lc.update(over)
    return {"mover_recorders": {"pass_short_live": lc}}


def _pass_a(coin="AIRALLY"):
    return {"coin": coin, "daily_move_pct": 12.0, "daily_volume_usd": 9e6,
            "confidence": 0.5, "last_price": 3.0}


def test_mover_pass_short_regime_gate_pass_trades(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: 0.02)      # eq7 UP
    opened = []
    assert mr.record_mover_pass_short(_pass_a(), _pass_gate_cfg(),
        execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1                                        # gate pass -> live
    assert out[0][1]["meta"]["regime_gate"] == "pass"
    assert out[0][1]["meta"]["eq_idx_7d"] == 0.02
    assert out[0][1]["meta"]["shadow"] is False


def test_mover_pass_short_regime_gate_fail_records_no_trade(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: -0.007)    # eq7 DOWN (today's real regime)
    opened = []
    assert mr.record_mover_pass_short(_pass_a(), _pass_gate_cfg(),
        execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == []                                           # the overnight bleed the gate now blocks
    assert out[0][1]["meta"]["regime_gate"] == "fail"
    assert out[0][1]["meta"]["shadow"] is True


def test_mover_pass_short_regime_gate_fail_closed_on_none(monkeypatch):
    _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: None)      # fetch failed
    opened = []
    mr.record_mover_pass_short(_pass_a(), _pass_gate_cfg(),
        execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert opened == []                                           # unknown regime -> fail-closed


def test_mover_pass_short_gate_disabled_trades_and_tags(monkeypatch):
    out = _captured(monkeypatch)
    monkeypatch.setattr(mr, "get_claims_registry", lambda: _OKClaims())
    monkeypatch.setattr(mr, "_equity_index_7d", lambda: -0.05)     # DOWN, but gate OFF
    opened = []
    mr.record_mover_pass_short(_pass_a(), _pass_gate_cfg(regime_gate_eq7=False),
        execute_fn=lambda x: opened.append(x) or {"executed": True})
    assert len(opened) == 1                                        # gate off -> trades
    assert out[0][1]["meta"]["regime_gate"] == "fail"             # meta still records what the gate saw
