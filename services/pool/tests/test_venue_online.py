"""Live, read-only reads against the real Hyperliquid info endpoint.
Deselected by default (addopts: -m 'not online and not live and not
browser'); run with `pytest -m online`. No key is used."""
from __future__ import annotations

import pytest

import contracts.pool as pool
from contracts import runtime
from services.pool import venue

pytestmark = pytest.mark.online


def test_extra_agents_and_user_role_reply_shapes_for_the_published_builder():
    address = runtime.DEFAULT_BUILDER_ADDRESS.lower()

    agents = venue.post_info(pool.extra_agents_request(address))
    assert isinstance(agents, list)
    for entry in agents:
        assert isinstance(entry, dict)
        assert isinstance(entry.get("address"), str) and entry["address"].startswith("0x")
        assert "name" in entry and "validUntil" in entry

    role = venue.post_info(pool.user_role_request(address))
    assert isinstance(role, dict) and "role" in role
    assert role["role"] in ("missing", "user", "agent", "vault", "subAccount")
    if role["role"] == "agent":
        assert isinstance(role.get("data"), dict) and "user" in role["data"]
