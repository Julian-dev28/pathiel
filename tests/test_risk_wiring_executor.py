"""E2E: the per-trade feasibility gate (services/risk/derive.py) replacing
the old global min_tradable_equity floor in pathiel.agents.executor.maybe_execute.

docs/overhaul/PROFIT_AUDIT.md section 1: the live account has $15.19 equity
and a min_tradable_equity_usd override of $26 that blocks EVERY trade this
week. This test feeds maybe_execute Julien's REAL .agent-config.json --
copied via a fresh read_text()+json.loads() (never the live file object,
never written to) -- at the account's own $15.19 equity, and proves the NEW
gate places a valid order where the OLD code returned
below_min_tradable_equity unconditionally. At $5 it refuses, naming the
dollar shortfall.

Per docs/overhaul/SWARM_RULES.md section 1: no order is actually placed
(place_entry_order is a local mock, never pathiel.client.exchange's real
network call), HYPERLIQUID_PRIVATE_KEY is a fake test string, and the real
config file is only ever read, never opened for writing.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_real_config_copy() -> Dict[str, Any]:
    """A read-only COPY of the real agent config: a fresh json.loads of a
    fresh read_text(), not any object another part of the system holds a
    reference to. Never written back."""
    raw = (REPO_ROOT / ".agent-config.json").read_text()
    return json.loads(raw)


def _analysis(coin: str = "BTC") -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()), "coin": coin, "verdict": "LONG", "side": "long",
        "confidence": 0.95, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": "[test]", "news_risk": "none", "ai_down": False,
        "created_at": int(time.time() * 1000), "composite_score": 0.0,
        "strategy_book": "xs_reversal",
    }


def _mock_exchange_and_memory(monkeypatch, equity_usd: float) -> Dict[str, Any]:
    """Every network/exchange call maybe_execute can reach, stubbed. Mirrors
    the proven harness in tests/test_claims_registry_and_sizing.py's
    TestStrategyBookEquityFracSizing._run_execute."""
    import pathiel.agents.executor as ex
    from pathiel.agents.memory import memory

    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xtest")
    monkeypatch.setattr(ex, "active_position_coins", lambda: {})
    monkeypatch.setattr(ex, "fetch_account_state", lambda user, include_hip3=False: {
        "equity": equity_usd, "available": equity_usd, "asset_positions": [],
        "total_ntl": 0.0, "dex_equity": {"": equity_usd}, "dex_available": {"": equity_usd},
    })
    monkeypatch.setattr(ex, "get_hl_price", lambda coin: 60_000.0)
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 50)
    monkeypatch.setattr(ex, "set_leverage", lambda coin, lev: None)
    monkeypatch.setattr(ex, "get_hl_atr", lambda interval, period, coin: 1200.0)
    monkeypatch.setattr(ex, "entry_size_for_notional", lambda coin, notional, px: round(notional / px, 6))
    monkeypatch.setattr(ex, "min_entry_notional_usd", lambda coin, px: 10.5)

    captured: Dict[str, Any] = {}

    def _place(is_buy, sz, px, coin, **kw):
        captured.update(is_buy=is_buy, size=sz, px=px)
        return {"ok": True, "order_id": "e2e-order", "avg_px": px, "total_sz": sz}

    monkeypatch.setattr(ex, "place_entry_order", _place)
    monkeypatch.setattr(ex, "place_hl_trigger_order", lambda is_buy, sz, px, kind, coin: {"ok": True})
    monkeypatch.setattr(ex, "cancel_open_orders_for_coin", lambda coin: None)

    # The entry context recorded after a fill reads the live venue: the
    # funding rate and the market regime (both best-effort). The regime also
    # feeds a risk gate, so a live read made this outcome depend on the day.
    import pathiel.client.hl_client as _hl
    from pathiel.agents import market_regime as _mr
    monkeypatch.setattr(_hl, "fetch_funding_history", lambda coin, start_ms, *a, **kw: [])
    monkeypatch.setattr(_mr, "detect_regime", lambda coin, **kw: "neutral")

    monkeypatch.setattr(memory, "get_recent_trades", lambda n: [])
    monkeypatch.setattr(memory, "track_daily_pnl", lambda eq: None)
    monkeypatch.setattr(memory, "get_daily_pnl", lambda: 0.0)
    monkeypatch.setattr(memory, "peak_daily_pnl", lambda: 0.0)
    monkeypatch.setattr(memory, "loss_cooldown_remaining_min", lambda coin: 0)
    monkeypatch.setattr(memory, "count_entries_since", lambda coin, t: 0)
    monkeypatch.setattr(memory, "record_trade", lambda t: None)
    monkeypatch.setattr(memory, "record_entry_context", lambda *a, **kw: None)
    monkeypatch.setattr(memory, "latest_trade_ts_by_coin", lambda n: {})

    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", "0xdeadbeef")
    return captured


def _real_config_for_test() -> Dict[str, Any]:
    cfg = dict(_load_real_config_copy())
    cfg["mode"] = "LIVE"
    # The live account trades HIP-3 tokenized equities only (enable_crypto is
    # False there); flipping it lets this test use a plain BTC analysis
    # instead of also mocking the HIP-3 dex-balance preflight's raw
    # hl_client._http_post call, which is unrelated to what this test is
    # proving. Every risk-relevant key (min_tradable_equity_usd,
    # equity_fraction_per_trade, leverage, max_concurrent,
    # max_daily_loss_pct, min_order_bump_max_mult, the book blocks) is
    # exactly the real file's.
    cfg["enable_crypto"] = True
    # The operator switched every book off on 2026-10-23 (trades through
    # Claude only). This test proves the SIZING path for the book it names in
    # _analysis, so re-enable that one book in the copy; its parameters stay
    # exactly the real file's.
    cfg["xs_reversal"] = {**cfg.get("xs_reversal", {}), "enabled": True}
    return cfg


def test_old_floor_would_have_blocked_this_exact_account():
    """Companion proof, not the fix itself: min_tradable_equity() (UNCHANGED,
    still exported — tests/test_funded_readiness.py and others assert its
    standalone behavior directly) still reports this account under the OLD
    floor. The fix is that maybe_execute stopped asking it the blocking
    question, not that the floor concept was deleted."""
    from pathiel.agents.executor import min_tradable_equity
    cfg = _load_real_config_copy()
    assert cfg.get("min_tradable_equity_usd") == 26.0, (
        "sanity: the real config's override moved — update this test's premise")
    assert 15.19 < min_tradable_equity(cfg)


def test_15_dollar_account_places_a_valid_order_with_the_real_legacy_config(monkeypatch):
    """THE scenario: $15.19 equity, Julien's real config, previously
    unconditionally refused with below_min_tradable_equity. Must now place
    a real (mocked) order at or above Hyperliquid's $10 minimum."""
    import pathiel.agents.executor as ex

    cfg = _real_config_for_test()
    monkeypatch.setattr(ex, "read_agent_config", lambda: cfg)
    captured = _mock_exchange_and_memory(monkeypatch, 15.19)

    result = ex.maybe_execute(_analysis())

    assert result.get("executed") is True, f"expected a placed order, got: {result}"
    assert result.get("order_id") == "e2e-order"
    assert "book_not_feasible" not in str(result.get("reason", ""))
    notional = captured["size"] * captured["px"]
    assert notional >= 10.0, f"order notional ${notional:.2f} is under Hyperliquid's $10 minimum"


def test_5_dollar_account_refuses_naming_the_shortfall_in_dollars(monkeypatch):
    """At $5, even the account's own aggressive migrated risk tolerance
    (docs/overhaul/PROFIT_AUDIT.md: 0.35 equity-fraction x 3x leverage
    implies a high risk_per_trade_pct) cannot clear the real $10.50 minimum
    on a book with no tight stop override. The refusal must name the exact
    dollar shortfall, not just say no."""
    import pathiel.agents.executor as ex

    cfg = _real_config_for_test()
    monkeypatch.setattr(ex, "read_agent_config", lambda: cfg)
    _mock_exchange_and_memory(monkeypatch, 5.0)

    result = ex.maybe_execute(_analysis())

    assert result.get("executed") is False
    assert "book_not_feasible" in result["reason"]
    match = re.search(r"Add \$([\d.]+)", result["reason"])
    assert match, f"expected a dollar shortfall in the refusal, got: {result['reason']!r}"
    assert float(match.group(1)) > 0
    assert result["derived_limits"]["min_equity_to_trade_usd"] > 5.0


@pytest.mark.parametrize("equity", [15.19, 20.0, 50.0, 500.0])
def test_accounts_the_old_config_already_served_are_unaffected(monkeypatch, equity):
    """Regression guard: ANY equity that satisfies the real file's OLD $26
    override must ALSO still be able to trade under the new gate (equity
    below the old floor trading now is the intended fix; equity ABOVE the
    old floor must never start failing). At $50/$500 the old gate would
    already have passed (>= $26); confirm the new one agrees."""
    import pathiel.agents.executor as ex

    cfg = _real_config_for_test()
    monkeypatch.setattr(ex, "read_agent_config", lambda: cfg)
    _mock_exchange_and_memory(monkeypatch, equity)

    result = ex.maybe_execute(_analysis())
    assert result.get("executed") is True, f"equity=${equity}: expected a placed order, got: {result}"
