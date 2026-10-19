"""services.accounts.summary — GET /api/accounts/active/summary's
business logic, independent of the HTTP layer."""
from __future__ import annotations

from services.accounts import registry, summary


def _clear_env_identity(monkeypatch):
    monkeypatch.setattr("pathiel.client.exchange.PRIVATE_KEY_HEX", "")


def test_no_account_connected(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    result = summary.get_active_summary()
    assert result.account is None
    assert result.can_trade is False
    assert "connect" in result.blockers[0].lower()


def test_watch_only_account_is_blocked_from_trading(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    registry.add(label="Whale", main_address="0x" + "c3" * 20, agent_address=None,
                mode="watch", source="watch")

    import pathiel.client.hl_client as hl_client

    def fake_fetch(user, include_hip3=False, **kw):
        return {"equity": 48210.5, "withdrawable_aggregated": 30000.0,
               "asset_positions": [1, 2, 3]}
    monkeypatch.setattr(hl_client, "fetch_account_state", fake_fetch)

    result = summary.get_active_summary()
    assert result.account is not None
    assert result.account.mode == "watch"
    assert result.can_trade is False
    assert any("watch-only" in b.lower() for b in result.blockers)
    assert result.equity_usd == 48210.5
    assert result.withdrawable_usd == 30000.0
    assert result.open_positions == 3


def test_verified_trade_account_can_trade_with_no_blockers(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    registry.add(label="Main", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)

    import pathiel.client.hl_client as hl_client

    def fake_fetch(user, include_hip3=False, **kw):
        return {"equity": 15.21, "withdrawable_aggregated": 15.21, "asset_positions": []}
    monkeypatch.setattr(hl_client, "fetch_account_state", fake_fetch)

    result = summary.get_active_summary()
    assert result.can_trade is True
    assert result.blockers == []
    assert result.equity_usd == 15.21
    assert result.withdrawable_usd == 15.21
    assert result.open_positions == 0


def test_live_state_fetch_failure_blocks_trading_but_keeps_the_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    registry.add(label="Main", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                mode="trade", source="wallet_sign", verified=True)

    import pathiel.client.hl_client as hl_client

    def boom(user, include_hip3=False, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(hl_client, "fetch_account_state", boom)

    result = summary.get_active_summary()
    assert result.account is not None
    assert result.can_trade is False
    assert any("could not read" in b.lower() for b in result.blockers)
    assert result.equity_usd is None


def test_snapshot_returns_the_raw_state_from_the_same_single_read(monkeypatch, tmp_path):
    """account_overview (MCP) needs per-dex detail; it must come from the
    same read as the summary, not a second Hyperliquid round trip."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    registry.add(label="Main", main_address="0x" + "a1" * 20, agent_address="0x" + "b2" * 20,
                mode="trade", source="wallet_sign", agent_name="pathiel", verified=True)

    import pathiel.client.hl_client as hl_client
    calls = []

    def fake_fetch(user, include_hip3=False, **kw):
        calls.append(user)
        return {"equity": 15.21, "withdrawable_aggregated": 15.0, "asset_positions": [],
                "dex_equity": {"": 0.01, "xyz": 15.18}}
    monkeypatch.setattr(hl_client, "fetch_account_state", fake_fetch)

    result, state = summary.get_active_snapshot()
    assert len(calls) == 1
    assert result.equity_usd == 15.21
    assert state["dex_equity"]["xyz"] == 15.18


def test_snapshot_state_is_none_without_an_account(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    _clear_env_identity(monkeypatch)
    result, state = summary.get_active_snapshot()
    assert result.account is None and state is None
