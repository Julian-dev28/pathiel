"""Real-money end-to-end tests against Hyperliquid mainnet.

DOUBLE-GATED — these are excluded from the default run AND skip themselves
unless ``HERMES_E2E=1`` is set, because they spend real funds:

  - test_live_order_roundtrip: sets leverage, opens a tiny (~$14) position and
    closes it — costs trading fees + slippage (~1-2 cents);
  - test_live_research_loop: runs the full research pipeline including the
    billable OpenRouter LLM call.

Run with:  HERMES_E2E=1 pytest -m live
"""
import os
import time

import pytest

pytestmark = pytest.mark.live

_E2E = os.environ.get("HERMES_E2E") == "1"
_needs_e2e = pytest.mark.skipif(
    not _E2E, reason="set HERMES_E2E=1 to run real-money e2e tests")


def _positions(state):
    return {p["position"]["coin"]: float(p["position"]["szi"])
            for p in state.get("asset_positions", [])}


@_needs_e2e
@pytest.mark.skipif(
    not (os.environ.get("HYPERLIQUID_PRIVATE_KEY") and os.environ.get("HYPERLIQUID_WALLET_ADDRESS")),
    reason="Hyperliquid credentials not configured",
)
def test_live_order_roundtrip():
    """Set leverage, open a tiny leveraged position, close it, verify it is flat."""
    from hermes_trader.client.exchange import HL_LEVERAGE, get_hl_price, place_hl_order, set_leverage
    from hermes_trader.client.hl_client import _http_post, fetch_account_state, resolve_user_address

    user = resolve_user_address()
    state0 = fetch_account_state(user)
    assert state0["equity"] >= 5, "account equity too low for the e2e order test"

    held = _positions(state0)
    coin = next((c for c in ("BTC", "ETH", "SOL") if c not in held), None)
    assert coin is not None, "BTC/ETH/SOL all have open positions — cannot isolate the test"

    mid = get_hl_price(coin)
    assert mid > 0
    size = 14.0 / mid

    leverage = set_leverage(coin, HL_LEVERAGE)
    assert leverage.get("ok") is True, leverage

    opened = place_hl_order(True, size, mid, coin)
    assert opened.get("ok") is True, opened
    assert opened.get("order_id"), "open fill returned no order_id"

    time.sleep(2)
    assert _positions(fetch_account_state(user)).get(coin, 0.0) > 0, "position did not open"

    flat = False
    for _ in range(3):
        szi = _positions(fetch_account_state(user)).get(coin, 0.0)
        if abs(szi) < 1e-9:
            flat = True
            break
        res = place_hl_order(szi < 0, abs(szi), get_hl_price(coin), coin)
        assert res.get("ok") is True, res
        time.sleep(2)
    assert flat, f"{coin} position not flat after close retries"

    # the resulting fills must carry no empty fields
    fills = _http_post("/info", {"type": "userFills", "user": user}) or []
    recent = [f for f in fills if f.get("coin") == coin][:2]
    assert len(recent) == 2, "expected an open and a close fill"
    for fill in recent:
        for field in ("px", "sz", "fee", "oid", "dir"):
            assert fill.get(field) not in (None, ""), f"empty fill field: {field}"
        assert float(fill["px"]) > 0 and float(fill["sz"]) > 0


@_needs_e2e
@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"),
                    reason="OPENROUTER_API_KEY not configured")
def test_live_research_loop():
    """Run the full research pipeline (incl. the real LLM) and validate the output."""
    from hermes_trader.agents.research import research
    from hermes_trader.client.hl_client import fetch_all_mids

    mid = float(fetch_all_mids().get("BTC", 0))
    assert mid > 0
    perception = {
        "id": f"e2e-{int(time.time() * 1000)}",
        "coin": "BTC", "type": "perp", "mid": mid, "composite_score": 55,
        "triggers": [{"name": "pctMoveSpike", "score": 6.0, "reason": "e2e", "fired": True}],
    }
    analysis = research("BTC", perception)

    expected = {"id", "perception_id", "coin", "verdict", "confidence", "side",
                "entry_px", "stop_px", "tp_px", "reasoning", "news_context", "created_at"}
    assert expected <= set(analysis), f"missing analysis keys: {expected - set(analysis)}"

    assert analysis["coin"] == "BTC"
    assert isinstance(analysis["id"], str) and analysis["id"], "empty analysis id"
    assert analysis["perception_id"] == perception["id"]
    assert analysis["verdict"] in ("PASS", "LONG", "SHORT", "CLOSE")
    assert isinstance(analysis["confidence"], (int, float)), \
        f"confidence not numeric: {analysis['confidence']!r}"
    assert 0 <= analysis["confidence"] <= 1
    assert analysis["side"] in (None, "long", "short")
    for field in ("entry_px", "stop_px", "tp_px"):
        assert isinstance(analysis[field], (int, float)), \
            f"{field} not numeric: {analysis[field]!r}"
    assert isinstance(analysis["created_at"], int) and analysis["created_at"] > 0
    assert isinstance(analysis["reasoning"], str) and analysis["reasoning"].strip(), \
        "research produced empty reasoning"
    assert analysis["news_context"], "empty news_context"

    # an actionable verdict must carry a complete, usable trade plan
    if analysis["verdict"] in ("LONG", "SHORT"):
        assert analysis["side"] in ("long", "short"), "actionable verdict without a side"
        assert analysis["entry_px"] > 0, "actionable verdict without an entry price"
        assert analysis["stop_px"] > 0, "actionable verdict without a stop price"
