"""T2.4 — the per-trade risk budget is sized against the FUNDING account, not
aggregate equity.

Hermetic (in-memory harness, no network). Pins the upstream 729be391cac3 fix:
a trade is FUNDED by one account (main for crypto, the specific HIP-3 dex for
colon coins), so the per-trade risk budget — the primary-stop equal-risk branch
and the ATR sizer — must use THAT account's equity (`size_equity`, = the
per-`_target_dex` equity), NOT the aggregate. Sizing on aggregate over-sizes
vs the balance that actually funds the trade, so the funding account saturates
after 1-2 trades and every other mover is margin-blocked. `agg_equity` stays on
the aggregate exposure / gross caps and the daily PnL / kill threshold.
"""
import pytest

from hermes_trader.agents.sizing import atr_equal_risk_notional as _real_atr
from test_cleanup import _analysis, _exec_baseline

# Shared primary-stop geometry (mirrors the live DSL floor): risk 1%,
# dsl_exit max_loss 2% / ROE 30% at lev 10 -> stop_frac = min(2.0, 30/10)/100.
RISK_PCT = 0.01
STOP_FRAC = 0.02
MID = 100.0
DSL = {"max_loss_pct": 2.0, "max_loss_roe_pct": 30.0, "protect_pct": 0.5,
       "retrace_threshold": 0.3, "hard_timeout_minutes": 180.0}
CFG = {"leverage": 10, "max_trade_notional_usd": 100000,
       "atr_risk_sizing": {"enabled": True, "risk_per_trade_pct": RISK_PCT,
                            "sizing_basis": "primary_stop"},
       "dsl_exit": DSL}


def _setup(monkeypatch, state_overrides, cfg_overrides=None):
    """_exec_baseline + a fully-hermetic 24h-volume stub (no universe/disk read)."""
    ex, captured, _ = _exec_baseline(monkeypatch, {**CFG, **(cfg_overrides or {})},
                                     state_overrides)
    monkeypatch.setattr(ex, "_get_market_volume_24h", lambda c: 5e7)
    return ex, captured


# ── THE FIX: primary-stop equal-risk sizes vs the FUNDING account ──────────
def test_primary_stop_sizes_vs_funding_not_aggregate(monkeypatch):
    # crypto (main-dex) coin: main equity ~$60, aggregate ~$170. The risk
    # budget must use the $60 that actually funds the trade, not the $170.
    ex, captured = _setup(monkeypatch,
        {"equity": 170.0, "available": 200.0, "total_ntl": 0.0,
         "dex_equity": {"": 60.0}})
    r = ex.maybe_execute(_analysis(coin="BTC", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    funding_based = RISK_PCT * 60.0 / STOP_FRAC   # $30  <- funding account
    agg_based = RISK_PCT * 170.0 / STOP_FRAC      # $85  <- (wrong) aggregate
    assert captured["size"] == pytest.approx(funding_based / MID)
    # The aggregate-based size is ~2.83x (≈3x) bigger — we must NOT use it.
    assert agg_based / funding_based == pytest.approx(170.0 / 60.0)
    assert captured["size"] < agg_based / MID


# ── THE ATR sizer also sizes vs the FUNDING account ────────────────────────
def test_atr_sizer_uses_funding_equity(monkeypatch):
    ex, captured = _setup(
        monkeypatch,
        {"equity": 170.0, "available": 200.0, "total_ntl": 0.0,
         "dex_equity": {"": 60.0}},
        cfg_overrides={"atr_risk_sizing": {"enabled": True,
                                            "risk_per_trade_pct": RISK_PCT,
                                            "sizing_basis": "atr_stop"}},
    )
    # capture the equity kwarg handed to the ATR sizer (imported locally inside
    # maybe_execute, so patch the source module, not the executor attribute).
    seen = {}

    def _spy(**kw):
        seen["equity"] = kw["equity"]
        return _real_atr(**kw)

    monkeypatch.setattr("hermes_trader.agents.sizing.atr_equal_risk_notional", _spy)
    r = ex.maybe_execute(_analysis(coin="BTC", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    # The sizer is handed the FUNDING account's equity (main dex = $60), not $170.
    assert seen["equity"] == pytest.approx(60.0)
    assert seen["equity"] != 170.0
    # ...and the sized notional reflects it (ATR stop = 1.5 x 2.0 / 100 = 3%):
    assert captured["size"] == pytest.approx((RISK_PCT * 60.0 / 0.03) / MID)


# ── DEGRADED READ: no per-dex breakdown -> sizing falls back to aggregate ──
def test_degraded_read_falls_back_to_aggregate(monkeypatch):
    # `dex_equity` breakdown absent entirely: _read_state resolves the per-dex
    # equity to the aggregate, so size_equity (and the sizing) use the aggregate.
    # No crash, and the sizing is the aggregate-based number.
    ex, captured = _setup(monkeypatch,
        {"equity": 170.0, "available": 200.0, "total_ntl": 0.0})  # no dex_equity
    r = ex.maybe_execute(_analysis(coin="BTC", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    # No per-dex breakdown -> size_equity falls back to the aggregate ($170),
    # so the primary-stop notional is the aggregate-based $85 (not a smaller
    # per-dex figure) — and the trade still sizes + executes (no crash).
    assert captured["size"] == pytest.approx(RISK_PCT * 170.0 / STOP_FRAC / MID)


# ── HIP-3 (colon) coin: size vs ITS OWN dex, not main, not aggregate ───────
def test_hip3_uses_its_own_dex_equity(monkeypatch):
    # xyz:MU is funded by the xyz dex ($100), not main ($900) nor the aggregate
    # ($1000). The risk budget must use the $100 that funds the trade.
    ex, captured = _setup(monkeypatch,
        {"equity": 1000.0, "available": 600.0, "total_ntl": 0.0,
         "dex_equity": {"": 900.0, "xyz": 100.0},
         "dex_available": {"": 600.0, "xyz": 80.0}})
    r = ex.maybe_execute(_analysis(coin="xyz:MU", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    funding_based = RISK_PCT * 100.0 / STOP_FRAC   # $50  <- xyz dex
    main_based = RISK_PCT * 900.0 / STOP_FRAC      # $450 <- (wrong) main
    agg_based = RISK_PCT * 1000.0 / STOP_FRAC      # $500 <- (wrong) aggregate
    assert captured["size"] == pytest.approx(funding_based / MID)
    assert captured["size"] != pytest.approx(main_based / MID)
    assert captured["size"] != pytest.approx(agg_based / MID)


# ── AGGREGATE CAPS UNCHANGED: the exposure gate still sees the aggregate ───
def test_aggregate_caps_still_use_aggregate(monkeypatch):
    # Funding ($60) < aggregate ($170). Sizing uses the funding account, but the
    # aggregate exposure gate (GateContext.equity -> equity_risk_cap / _room)
    # must STILL be the $170 aggregate — the fix only re-bases the per-trade
    # risk budget, not the gross/exposure caps.
    ex, captured = _setup(monkeypatch,
        {"equity": 170.0, "available": 200.0, "total_ntl": 0.0,
         "dex_equity": {"": 60.0}})
    seen = {}

    def _fake_gates(ctx, config, last_trade_time=None):
        seen["equity"] = ctx.equity
        seen["notional"] = ctx.trade_notional_usd
        return {"results": {}, "blocked": False, "block_reasons": []}

    monkeypatch.setattr(ex, "eval_all_gates", _fake_gates)
    r = ex.maybe_execute(_analysis(coin="BTC", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    # The gate context's equity (the basis for equity_risk_cap / _room) is the
    # AGGREGATE ($170), even though sizing used the $60 funding account.
    assert seen["equity"] == pytest.approx(170.0)
    # ...and the sized notional is the FUNDING-based $30 (not the $85 aggregate).
    assert seen["notional"] == pytest.approx(RISK_PCT * 60.0 / STOP_FRAC)


# ── UNIFIED ACCOUNT: an open position must NOT collapse the funding equity ─
def test_unified_open_position_sizes_vs_funding_not_committed_margin(monkeypatch):
    # The live 2026-10-15 DOGE case: a $950 unified account holding a $33/5x
    # ONDO short. The main clearinghouse accountValue (dex_equity[""]) then
    # reads $6.68 — ONLY the committed margin, not the shared spot pool that
    # funds new entries. Sizing on it produced $0.29 notional, below the HL
    # $10.56 floor, and every entry while the position was open was dropped
    # as below_min_order_notional. With main_equity present the sizer must
    # use the $950 funding equity.
    ex, captured = _setup(monkeypatch,
        {"equity": 950.0, "available": 943.4, "total_ntl": 33.2,
         "dex_equity": {"": 6.68}, "main_equity": 950.0})
    r = ex.maybe_execute(_analysis(coin="DOGE", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    assert captured["size"] == pytest.approx(RISK_PCT * 950.0 / STOP_FRAC / MID)
    # The committed-margin figure must NOT be the sizing basis:
    assert captured["size"] != pytest.approx(RISK_PCT * 6.68 / STOP_FRAC / MID)


def test_main_equity_absent_falls_back_to_dex_breakdown(monkeypatch):
    # States without the new field (older stubs, degraded reads) keep the
    # pre-fix resolution: dex_equity[""] -> aggregate. Pins the fallback
    # branch of the main-dex read.
    ex, captured = _setup(monkeypatch,
        {"equity": 170.0, "available": 200.0, "total_ntl": 0.0,
         "dex_equity": {"": 60.0}})  # no main_equity
    r = ex.maybe_execute(_analysis(coin="BTC", confidence=0.75, composite_score=60))
    assert r["executed"] is True, r
    assert captured["size"] == pytest.approx(RISK_PCT * 60.0 / STOP_FRAC / MID)
