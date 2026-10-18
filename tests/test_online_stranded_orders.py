"""Online: the stranded-order cleanup's two reads against the live venue.

Read-only (/info only, never /exchange). Deselected by default; run with:

    .venv/bin/python -m pytest -q -m online tests/test_online_stranded_orders.py

Proves the live response shapes match what cancel_open_orders_for_coin and
cancel_stranded_reduce_only_orders parse, for a HIP-3 coin (xyz:CL) and a
main-dex coin (BTC). Whenever the account holds orders or positions on xyz,
it also proves Hyperliquid names them with the dex prefix, which is what the
cleanup matches on.
"""
from __future__ import annotations

import pytest

import pathiel.client.exchange as xc
from pathiel.client.hl_client import _http_post

pytestmark = pytest.mark.online


@pytest.fixture(scope="module")
def user() -> str:
    addr = xc.resolve_user_address()
    if not addr:
        pytest.skip("no trading account configured")
    return addr


@pytest.mark.parametrize("coin", ["xyz:CL", "BTC"])
def test_position_read_parses_live(user, coin):
    size = xc._position_size_on_coin(user, coin)
    assert size is not None, f"clearinghouseState for {coin}'s dex did not parse"
    assert isinstance(size, float)


@pytest.mark.parametrize("coin", ["xyz:CL", "BTC"])
def test_order_read_parses_live(user, coin):
    rows = xc._resting_orders_for_coin(user, coin)
    assert isinstance(rows, list)
    for o in rows:
        assert o["coin"] in xc._names_on_dex(coin) and "reduceOnly" in o


def test_xyz_rows_carry_the_dex_prefix_live(user):
    orders = _http_post("/info", {"type": "frontendOpenOrders", "user": user, "dex": "xyz"})
    state = _http_post("/info", {"type": "clearinghouseState", "user": user, "dex": "xyz"})
    assert isinstance(orders, list) and isinstance(state, dict)
    names = [o["coin"] for o in orders] + [p["position"]["coin"] for p in state["assetPositions"]]
    assert all(n.startswith("xyz:") for n in names), names


def test_a_bare_order_read_is_main_dex_only_live(user):
    """Why the dex param is needed: a read without it never lists xyz rows."""
    orders = _http_post("/info", {"type": "frontendOpenOrders", "user": user})
    assert isinstance(orders, list)
    assert not [o for o in orders if str(o.get("coin", "")).startswith("xyz:")]
