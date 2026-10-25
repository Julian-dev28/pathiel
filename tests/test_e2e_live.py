"""Real-money end-to-end tests against Hyperliquid mainnet.

DOUBLE-GATED — these are excluded from the default run AND skip themselves
unless ``PATHIEL_E2E=1`` is set, because they spend real funds:

  - test_live_order_roundtrip: sets leverage, opens a tiny (~$14) position and
    closes it — costs trading fees + slippage (~1-2 cents);
  - test_live_research_loop: runs the full research pipeline including the
    billable OpenRouter LLM call.

Run with:  PATHIEL_E2E=1 pytest -m live
"""
import os
import time

import pytest

pytestmark = pytest.mark.live

_E2E = os.environ.get("PATHIEL_E2E") == "1"
_needs_e2e = pytest.mark.skipif(
    not _E2E, reason="set PATHIEL_E2E=1 to run real-money e2e tests")


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
    from pathiel.client.exchange import HL_LEVERAGE, get_hl_price, place_hl_order, set_leverage
    from pathiel.client.hl_client import _http_post, fetch_account_state, resolve_user_address

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
