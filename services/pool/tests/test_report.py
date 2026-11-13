"""services/pool/report.py, report_cli.py and report_tool.py: the member's
own local, undelayed, unpublished report. Offline."""
from __future__ import annotations

import json

import pytest

import contracts.pool as pool
from services.pool import account as ACC
from services.pool import paths
from services.pool import report as RPT
from services.pool import report_cli as RC
from services.pool import report_tool as RT
from services.pool import venue
from services.pool.tests import pool_fakes as PF

AS_OF_ISO = "2026-11-14T12:00:00Z"
AS_OF_MS = pool.ms_from_iso(AS_OF_ISO)
COIN = "xyz:BRENTOIL"


def _empty_bundle(address, as_of_ms=AS_OF_MS):
    return {"as_of_ms": as_of_ms, "inputs": {address: {"fills": [], "orders": []}}, "bars5": {}, "bars1h": {}}


def test_not_a_member_raises_not_member(pool_env, new_account):
    acct = new_account()
    with pytest.raises(pool.PoolError) as exc:
        RPT.personal(acct.address.lower(), bundle=_empty_bundle(acct.address.lower()))
    assert exc.value.code == "not_member"


def test_signed_not_merged_gets_a_report_from_its_joined_at(pool_env, new_account, signed, put):
    acct = new_account()
    joined_at = "2026-11-10T00:00:00Z"
    put(signed("join", acct, at=joined_at), root=paths.outbox_root())
    result = RPT.personal(acct.address.lower(), bundle=_empty_bundle(acct.address.lower()))
    assert result["ok"] is True
    assert result["state"] == "signed_not_merged"
    assert result["since"] == joined_at


def test_a_leave_in_the_outbox_stops_the_report(pool_env, new_account, signed, put):
    acct = new_account()
    put(signed("join", acct, at="2026-11-10T00:00:00Z"), root=paths.outbox_root())
    put(signed("leave", acct, at="2026-11-11T00:00:00Z"), root=paths.outbox_root())
    with pytest.raises(pool.PoolError) as exc:
        RPT.personal(acct.address.lower(), bundle=_empty_bundle(acct.address.lower()))
    assert exc.value.code == "not_member"


def test_no_delay_a_trade_closed_one_minute_before_as_of_is_provisional(pool_env, new_account, signed, put):
    acct = new_account()
    addr = acct.address.lower()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.outbox_root())
    entry = AS_OF_MS - 61_000
    fills, orders = PF.bracket_trade(COIN, "long", 70.0, 69.0, 5.0, entry, target_px=72.0, hold_ms=60_000)
    bundle = {"as_of_ms": AS_OF_MS, "inputs": {addr: {"fills": fills, "orders": orders}}, "bars5": {}, "bars1h": {}}
    result = RPT.personal(addr, bundle=bundle)
    assert result["closed_trades"] == 1 and result["graded_trades"] == 1 and result["provisional"] == 1
    assert "Nothing in this report is published by this call" in result["markdown"]
    assert "scoreboard" in result["markdown"].lower()


def test_the_result_shape_is_exactly_the_contract_and_carries_no_key(pool_env, new_account, signed, put):
    acct = new_account()
    addr = acct.address.lower()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.outbox_root())
    result = RPT.personal(addr, bundle=_empty_bundle(addr))
    assert set(result) == {"ok", "account", "state", "since", "as_of", "closed_trades", "graded_trades",
                           "provisional", "row", "slices", "trades_needed", "markdown"}
    dumped = json.dumps(result)
    assert "PRIVATE_KEY" not in dumped and "private_key" not in dumped
    # a 64-hex-char private key never appears anywhere in the result
    import re
    assert not re.search(r"\b[0-9a-fA-F]{64}\b", dumped)


def test_personal_is_testnet_refused_off_mainnet(pool_env, new_account, signed, put, monkeypatch):
    acct = new_account()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.outbox_root())
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    with pytest.raises(pool.PoolError) as exc:
        RPT.personal(acct.address.lower())   # no bundle: the live path, refused before any venue read
    assert exc.value.code == "testnet"


# ── report_cli ───────────────────────────────────────────────────────────

def test_report_cli_help_exits_0():
    with pytest.raises(SystemExit) as exc:
        RC.main(["report", "--help"])
    assert exc.value.code == 0


def test_report_cli_no_account_exits_2(pool_env, capsys, monkeypatch):
    monkeypatch.setattr(ACC, "configured_account", lambda: "")
    rc = RC.main(["report"])
    out, err = capsys.readouterr()
    assert rc == 2 and "no account" in err


def test_report_cli_not_member_exits_2(pool_env, new_account, capsys, tmp_path):
    acct = new_account()
    addr = acct.address.lower()
    d = str(tmp_path / "bundle")
    from services.pool import fetch as F
    F.save_bundle(d, _empty_bundle(addr))
    rc = RC.main(["report", "--address", addr, "--offline", d])
    out, err = capsys.readouterr()
    assert rc == 2 and "not_member" in err


def test_report_cli_prints_json_with_the_flag(pool_env, new_account, signed, put, capsys, tmp_path):
    acct = new_account()
    addr = acct.address.lower()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.outbox_root())
    d = str(tmp_path / "bundle")
    from services.pool import fetch as F
    F.save_bundle(d, _empty_bundle(addr))
    rc = RC.main(["report", "--address", addr, "--offline", d, "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    doc = json.loads(out)
    assert doc["ok"] is True and doc["account"] == pool.short_address(addr)


def test_report_cli_venue_error_exits_3(pool_env, new_account, signed, put, monkeypatch, capsys):
    acct = new_account()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.outbox_root())

    def boom(payload):
        raise venue.VenueError("down")

    monkeypatch.setattr(venue, "post_info", boom)
    rc = RC.main(["report", "--address", acct.address.lower()])
    out, err = capsys.readouterr()
    assert rc == 3


# ── report_tool (MCP handler) ───────────────────────────────────────────

def test_handler_never_raises_and_returns_json_for_every_failure(pool_env, monkeypatch):
    monkeypatch.setattr(ACC, "configured_account", lambda: "")
    out = json.loads(RT.handle_pool_report({}))
    assert out == {"ok": False, "code": "no_account", "error": "no account is configured on this install"}

    monkeypatch.setattr(ACC, "configured_account", lambda: "0x" + "77" * 20)
    out = json.loads(RT.handle_pool_report({}))
    assert out["ok"] is False and out["code"] == "not_member"

    monkeypatch.setattr(ACC, "configured_account", lambda: "not-an-address")
    out = json.loads(RT.handle_pool_report({}))
    assert out["ok"] is False and "error" in out


def test_handler_returns_json_on_venue_error(pool_env, new_account, signed, put, monkeypatch):
    acct = new_account()
    put(signed("join", acct, at="2026-11-01T00:00:00Z"), root=paths.repo_pool_root())
    monkeypatch.setattr(ACC, "configured_account", lambda: acct.address.lower())

    def boom(payload):
        raise venue.VenueError("down")

    monkeypatch.setattr(venue, "post_info", boom)
    out = json.loads(RT.handle_pool_report({}))
    assert out == {"ok": False, "code": "venue", "error": "down"}
