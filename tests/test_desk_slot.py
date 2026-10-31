"""scripts/desk_slot.py: the first step of every desk tick."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("desk_slot", ROOT / "scripts" / "desk_slot.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def _raw(positions=(), entries=()):
    return {"state": {"assetPositions": [{"position": {"coin": c, "szi": sz}} for c, sz in positions]},
            "orders": [{"coin": c, "reduceOnly": False, "oid": i} for i, c in enumerate(entries)]
                      + [{"coin": "xyz:GOLD", "reduceOnly": True, "oid": 99}]}   # a stop never holds a slot


def test_other_desk_holds_the_slot():
    v = S.slot_view(["xyz:GOLD", "xyz:SILVER"], [_raw(positions=[("xyz:BRENTOIL", "1.33")])])
    assert v == {"slot": "held", "holder": "xyz:BRENTOIL", "mine": False, "kind": "position",
                 "positions": ["xyz:BRENTOIL"], "resting_entries": []}


def test_my_desk_holds_the_slot_with_a_position_or_a_resting_entry():
    assert S.slot_view(["xyz:BRENTOIL", "xyz:CL"], [_raw(positions=[("xyz:BRENTOIL", "1.33")])])["mine"] is True
    v = S.slot_view(["xyz:BRENTOIL", "xyz:CL"], [_raw(entries=["xyz:BRENTOIL"])])
    assert v["mine"] is True and v["kind"] == "resting_entry"


def test_free_slot_and_position_outranks_entry():
    assert S.slot_view(["xyz:GOLD"], [_raw()])["slot"] == "free"
    v = S.slot_view(["xyz:GOLD"], [_raw(positions=[("xyz:NVDA", "-2")], entries=["xyz:GOLD"])])
    assert v["holder"] == "xyz:NVDA" and v["mine"] is False and v["resting_entries"] == ["xyz:GOLD"]


def test_cli_resolves_the_desk_coins_and_prints_json(capsys):
    fetch = lambda user, dex: _raw(positions=[("xyz:BRENTOIL", "1.33")])
    assert S.main(["--desk", "metals"], fetch_raw=fetch, dexes=["xyz"], user="0xu") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["desk"] == "metals" and "xyz:GOLD" in out["desk_coins"]
    assert out["slot"] == "held" and out["mine"] is False and out["holder"] == "xyz:BRENTOIL"
    S.main(["--desk", "oil"], fetch_raw=fetch, dexes=["xyz"], user="0xu")
    assert json.loads(capsys.readouterr().out)["mine"] is True


# ── per-desk slots (2026-10-31: oil 95%, silver 5%) ──────────────────────────

def test_per_desk_slot_ignores_the_other_desks_position():
    v = S.slot_view(["xyz:SILVER", "xyz:GOLD"], [_raw(positions=[("xyz:BRENTOIL", "1.33")])], own_slot=True)
    assert v["slot"] == "free" and v["mode"] == "per_desk" and v["positions"] == ["xyz:BRENTOIL"]
    v = S.slot_view(["xyz:SILVER", "xyz:GOLD"],
                    [_raw(positions=[("xyz:BRENTOIL", "1.33")], entries=["xyz:SILVER"])], own_slot=True)
    assert v["slot"] == "held" and v["mine"] is True and v["holder"] == "xyz:SILVER"


def _cfg(monkeypatch, cfg):
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda: cfg)


def test_cli_with_shares_ticks_silver_while_oil_holds_brent(monkeypatch, capsys):
    _cfg(monkeypatch, {"active_desks": ["oil", "metals"], "desk_equity_share": {"oil": 0.95, "metals": 0.05}})
    fetch = lambda user, dex: _raw(positions=[("xyz:BRENTOIL", "1.33")])
    S.main(["--desk", "metals"], fetch_raw=fetch, dexes=["xyz"], user="0xu")
    out = json.loads(capsys.readouterr().out)
    assert out["slot"] == "free" and out["equity_share"] == 0.05
    S.main(["--desk", "oil"], fetch_raw=fetch, dexes=["xyz"], user="0xu")
    assert json.loads(capsys.readouterr().out)["mine"] is True


def test_cli_a_desk_without_a_share_is_inactive(monkeypatch, capsys):
    _cfg(monkeypatch, {"active_desks": ["oil"], "desk_equity_share": {"oil": 0.95}})
    assert S.main(["--desk", "metals"], fetch_raw=lambda u, d: _raw(), dexes=["xyz"], user="0xu") == 0
    assert json.loads(capsys.readouterr().out)["slot"] == "inactive"


def test_cli_bad_shares_exit_nonzero(monkeypatch, capsys):
    _cfg(monkeypatch, {"active_desks": ["oil", "metals"], "desk_equity_share": {"oil": 0.95, "metals": 0.2}})
    assert S.main(["--desk", "oil"], fetch_raw=lambda u, d: _raw(), dexes=["xyz"], user="0xu") == 1
    assert "adds up to" in json.loads(capsys.readouterr().out)["error"]
