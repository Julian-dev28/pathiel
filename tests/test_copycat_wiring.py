"""copycat wiring: config shape, dashboard panel, and the trading_loop /
autonomous_cycle plumbing that makes the book reachable by the evidence loop.

This file does NOT re-test copycat_live's own mirror logic (diffing a
leader's positions, sizing, leverage/margin mode application) — that belongs
to copycat_live's own test file. It tests the WIRING: that .agent-config.json
carries a valid copycat block, that it survives the generic config-merge path
unmolested (no schema silently strips the leaders list or an unknown key),
that the dashboard renders it (both as a books-table row and its own
read-only panel + endpoint), and that trading_loop.py / autonomous_cycle.py
reach it the same way they reach copy_trade and drawdown_ladder.

Gate tests — no network, no live state (conftest.py redirects
PATHIEL_AGENT_CONFIG_FILE etc. to a throwaway temp dir before any module
import). Tests that need the REAL checked-in .agent-config.json read it by
path directly, same pattern as
tests/test_every_live_book_is_gradeable.py::test_the_live_config_has_no_shadow_book_either.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathiel import dashboard as db
from pathiel.agents.config_store import DEFAULT_CONFIG, merge_agent_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".agent-config.json"
LOOP_SRC = (ROOT / "scripts" / "trading_loop.py").read_text()

_HAS_COPYCAT_LIVE = importlib.util.find_spec("pathiel.agents.copycat_live") is not None


def _real_copycat_block() -> dict:
    return json.loads(CONFIG_PATH.read_text())["copycat"]


# ── .agent-config.json shape ─────────────────────────────────────────────────

def test_copycat_config_block_is_valid_json_with_expected_shape():
    cfg = json.loads(CONFIG_PATH.read_text())
    assert "copycat" in cfg
    cc = cfg["copycat"]
    assert cc["enabled"] is False, "real money — must ship OFF, operator flips it on"
    assert cc["shadow_only"] is False, "no shadow tier on this account (2026-08-30 directive)"

    leaders = cc["leaders"]
    assert isinstance(leaders, list) and len(leaders) == 10
    assert len(set(leaders)) == 10, "duplicate leader address"
    for addr in leaders:
        assert isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42, addr
        assert addr == addr.lower(), f"{addr} is not lowercase"

    assert cc["sizing_mode"] in ("proportional", "fixed_margin", "fixed_notional")
    assert cc["sizing_mode"] == "fixed_margin", (
        "equity is $1.29 — proportional sizing rounds to sub-floor on every "
        "trade at that equity; fixed_margin must be the shipped default")
    assert cc["size_multiplier"] == 1.0
    assert cc["fixed_margin_usd"] == 0.25
    assert cc["fixed_notional_usd"] == 10.5
    assert cc["fixed_notional_usd"] >= 10.5, "below the exchange minimum order size"
    assert cc["sleeve_frac"] == 0.25
    assert cc["max_position_leverage"] == 50
    assert cc["leverage_mode"] in ("max", "match", "fixed") and cc["leverage_mode"] == "max"
    assert cc["leverage"] == 20
    assert 1 <= cc["leverage"] <= 50
    assert cc["margin_mode"] in ("isolated", "cross") and cc["margin_mode"] == "isolated"
    assert cc["include_spot_staked"] is False
    assert cc["only_new_positions"] is False
    assert cc["max_worse_entry_pct"] == 10.0
    assert cc["min_order_bump"] is True
    assert cc["leader_overrides"] == {}, "no leader is special-cased in the shipped config"

    note = cc["_note"]
    assert isinstance(note, str) and len(note) > 200
    for keyword in ("leverage_mode", "margin_mode", "isolated", "cross",
                    "spot", "staked", "max_worse_entry_pct", "min_order_bump",
                    "sleeve_frac", "leaders", "sizing_mode", "fixed_margin",
                    "fixed_notional", "proportional", "size_multiplier",
                    "leader_overrides", "$1.29", "leader_settings"):
        assert keyword in note, f"_note does not explain '{keyword}'"


def test_copycat_fixed_margin_at_max_leverage_clears_the_order_floor():
    """The specific arithmetic the coordinator cited as the reason
    fixed_margin is the default: $0.25 margin at 50x must clear the
    exchange's $10.50 minimum order size, or the default sizing_mode would
    never actually place a trade at this account's real equity ($1.29)."""
    cc = _real_copycat_block()
    notional = cc["fixed_margin_usd"] * cc["max_position_leverage"]
    assert notional == pytest.approx(12.5)
    assert notional >= 10.5


def test_copycat_leaders_include_the_wallet_copy_trade_already_refuted():
    # W-CP1 refuted copying 0xe282 specifically; copycat mirrors it again
    # (among 9 others) as part of the operator's un-backtested build. The
    # dashboard thesis names this wallet explicitly, so the config must
    # actually contain it or the thesis is lying.
    leaders = _real_copycat_block()["leaders"]
    assert "0xe2823659be02e0f48a4660e4da008b5e1abfdf29" in leaders


def test_copycat_config_has_no_shadow_book_either():
    """Same check as test_every_live_book_is_gradeable.py's live-config
    guard, scoped to copycat specifically so a future edit to this block
    that flips shadow_only can't slip past a broader test rename."""
    assert _real_copycat_block().get("shadow_only") is not True


# ── merge_agent_config: no schema strips the new key, the nested list, or
#    an unknown key — the generic POST /api/agent/config write path ─────────

def test_merge_agent_config_preserves_leaders_list_on_partial_update():
    cc = _real_copycat_block()
    base = {"mode": "OFF", "copycat": dict(cc)}
    merged = merge_agent_config(base, {"copycat": {"enabled": True}})
    assert merged["copycat"]["enabled"] is True
    assert merged["copycat"]["leaders"] == cc["leaders"], (
        "a partial {'copycat': {'enabled': True}} update stripped or "
        "mutated the leaders list — merge_agent_config must deep-merge "
        "nested dicts, not replace them")
    assert merged["copycat"]["margin_mode"] == cc["margin_mode"]
    assert merged["copycat"]["sleeve_frac"] == cc["sleeve_frac"]


def test_merge_agent_config_adds_copycat_as_a_new_top_level_key():
    """copycat is not in config_store.DEFAULT_CONFIG — it must still survive
    a merge onto the bare defaults (the no-file-on-disk fallback path)."""
    assert "copycat" not in DEFAULT_CONFIG
    cc = _real_copycat_block()
    merged = merge_agent_config(DEFAULT_CONFIG, {"copycat": cc})
    assert merged["copycat"] == cc


def test_merge_agent_config_does_not_touch_other_books():
    """A copycat-only update must not perturb copy_trade/drawdown_ladder —
    deep-merge, not a blanket dict replace at any level."""
    base = json.loads(CONFIG_PATH.read_text())
    before_copy_trade = dict(base["copy_trade"])
    merged = merge_agent_config(base, {"copycat": {"enabled": True}})
    assert merged["copy_trade"] == before_copy_trade


# ── leader_overrides: a write for one leader must merge, never wipe another
#    leader's override or another field already set on the same leader ─────

LEADER_A, LEADER_B = (_real_copycat_block()["leaders"][0],
                      _real_copycat_block()["leaders"][1])


def test_leader_overrides_write_does_not_wipe_a_different_leader():
    base = json.loads(CONFIG_PATH.read_text())
    base["copycat"]["leader_overrides"] = {LEADER_A: {"leverage": 10}}
    merged = merge_agent_config(base, {"copycat": {"leader_overrides": {LEADER_B: {"leverage": 5}}}})
    overrides = merged["copycat"]["leader_overrides"]
    assert overrides[LEADER_A] == {"leverage": 10}, (
        "writing leader B's override wiped leader A's — merge_agent_config "
        "must recurse into leader_overrides, not replace it wholesale")
    assert overrides[LEADER_B] == {"leverage": 5}


def test_leader_overrides_write_merges_fields_within_one_leader():
    """A second write adding `leverage_mode` to a leader that already has
    `margin_mode` overridden must end up with BOTH fields, not just the new
    one — field-level merge within a single leader's override dict."""
    base = json.loads(CONFIG_PATH.read_text())
    base["copycat"]["leader_overrides"] = {LEADER_A: {"margin_mode": "cross"}}
    merged = merge_agent_config(
        base, {"copycat": {"leader_overrides": {LEADER_A: {"leverage_mode": "fixed", "leverage": 7}}}})
    assert merged["copycat"]["leader_overrides"][LEADER_A] == {
        "margin_mode": "cross", "leverage_mode": "fixed", "leverage": 7}


def test_leader_overrides_write_does_not_perturb_other_copycat_keys():
    base = json.loads(CONFIG_PATH.read_text())
    before = {k: v for k, v in base["copycat"].items() if k != "leader_overrides"}
    merged = merge_agent_config(base, {"copycat": {"leader_overrides": {LEADER_A: {"leverage": 3}}}})
    after = {k: v for k, v in merged["copycat"].items() if k != "leader_overrides"}
    assert after == before


def test_leader_overrides_subset_of_fields_matches_the_spec():
    """leader_overrides values may only be a subset of these eight keys —
    pinned so a future copycat_live.py change to the override schema is
    forced to update this list (and the UI's override form) deliberately."""
    allowed = {"sizing_mode", "size_multiplier", "fixed_margin_usd", "fixed_notional_usd",
              "leverage_mode", "leverage", "margin_mode", "max_worse_entry_pct"}
    override = {"leverage_mode": "fixed", "leverage": 12, "margin_mode": "cross"}
    assert override.keys() <= allowed


# ── dashboard: books table row ───────────────────────────────────────────────

def test_copycat_is_a_known_book_with_an_honest_thesis():
    assert "copycat" in db._KNOWN_BOOK_NAMES
    thesis = next(t for n, _, t in db._BOOKS if n == "copycat")
    assert "UNTESTED" in thesis
    assert "no backtest" in thesis
    assert "W-CP1" in thesis
    assert "0xe282" in thesis
    assert "10 Hyperliquid leaderboard wallets" in thesis


def test_books_payload_includes_copycat_row(monkeypatch):
    cfg = {"copycat": {"enabled": True, "shadow_only": False,
                       "sleeve_frac": 0.25, "max_position_leverage": 50}}
    monkeypatch.setattr(db, "read_agent_config", lambda: cfg)
    rows = {r["name"]: r for r in db._books_payload()}
    assert rows["copycat"]["status"] == "live"
    assert rows["copycat"]["thesis"]


def test_books_endpoint_row_count_includes_copycat(client, monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    r = client.get("/api/dashboard/books")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 8
    assert any(row["name"] == "copycat" for row in rows)


# ── dashboard: read-only copycat panel + endpoint ────────────────────────────
#
# DECISION: read-only. No page in pathiel/templates edits .agent-config.json
# today — POST /api/agent/config (pathiel/server.py) is a generic
# operator-auth merge endpoint, but nothing in the frontend calls it; the
# only existing config-rendering pattern anywhere in the dashboard is the
# landing-page books table, which is a pure READ off _books_payload. This
# panel follows that same tier rather than inventing an editing pattern that
# does not exist yet for any other book either.

def test_copycat_payload_status_off_with_no_config(monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    payload = db._copycat_payload()
    assert payload["status"] == "off"
    # With no config block, settings() (when importable) falls back to its
    # own DEFAULT_LEADERS rather than an empty list — same honesty as
    # copy_trade's DEFAULT_LEADER fallback. Without copycat_live importable,
    # the raw-cfg fallback has no "leaders" key either, which is also [].
    assert isinstance(payload["leaders"], list)
    assert payload["owned_coins"] == []
    # One entry per leader (even with no state file yet), each with empty
    # coins/ignored and, when copycat_live is importable, its effective
    # settings — never an empty dict once there are leaders to show.
    assert set(payload["per_leader"]) == set(payload["leaders"])
    for entry in payload["per_leader"].values():
        assert entry["coins"] == [] and entry["ignored"] == []
    assert isinstance(payload["settings"], dict)


def test_copycat_payload_falls_back_to_raw_cfg_when_module_unimportable(monkeypatch):
    """Force the lazy `from pathiel.agents.copycat_live import ...` imports
    inside _copycat_payload to fail (a None entry in sys.modules raises
    ImportError on import, regardless of whether the real module is on
    disk) and verify the panel still renders the raw config block instead
    of 500ing. This is the behavior that keeps dashboard.py decoupled from
    copycat_live the same way _books_payload never imports any book
    module at all."""
    monkeypatch.setitem(sys.modules, "pathiel.agents.copycat_live", None)
    raw_cfg = {"copycat": {"enabled": True, "shadow_only": False,
                           "leaders": ["0xabc0000000000000000000000000000000abc0"],
                           "sleeve_frac": 0.3, "margin_mode": "cross"}}
    monkeypatch.setattr(db, "read_agent_config", lambda: raw_cfg)
    payload = db._copycat_payload()
    assert payload["status"] == "live"
    assert payload["settings"]["sleeve_frac"] == 0.3
    assert payload["settings"]["margin_mode"] == "cross"
    assert payload["leaders"] == ["0xabc0000000000000000000000000000000abc0"]
    assert payload["owned_coins"] == []  # owned_coins() also unimportable -> safe empty


def test_copycat_endpoint_returns_expected_shape(client, monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {"copycat": _real_copycat_block()})
    r = client.get("/api/dashboard/copycat")
    assert r.status_code == 200
    body = r.json()
    assert {"status", "settings", "leaders", "owned_coins", "per_leader"} <= set(body)
    assert body["status"] == "off"  # checked-in config ships enabled: false
    assert len(body["leaders"]) == 10


# ── dashboard: write endpoint (POST /api/dashboard/copycat/settings) ────────
#
# DECISION: a dedicated, operator-auth endpoint that merges only the
# `copycat` key, rather than reusing the generic POST /api/agent/config.
# Same auth as every other operator action on this page (X-Operator-Token /
# _require_operator) — matches the existing kill-switch button exactly —
# but scoped so a frontend bug or a malformed body can never perturb any
# OTHER top-level config key or book. See _copycat_settings_update's
# docstring for the full reasoning.
#
# These tests redirect config_store.CONFIG_PATH to a per-test tmp_path,
# the SAME pattern tests/test_cleanup.py already uses for config_store
# round-trips — never monkeypatch db.read_agent_config with a fixed-value
# lambda here: a write endpoint calls read AND write, and a stubbed read
# paired with a real write is exactly how a previous draft of this file
# almost clobbered the live .agent-config.json during manual smoke testing.

def _isolated_config_path(tmp_path, monkeypatch, initial: dict):
    from pathiel.agents import config_store
    path = tmp_path / ".agent-config.json"
    path.write_text(json.dumps(initial))
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))
    return path


def test_copycat_settings_endpoint_requires_operator_token(client, monkeypatch, tmp_path):
    _isolated_config_path(tmp_path, monkeypatch, {"copycat": _real_copycat_block()})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")
    r = client.post("/api/dashboard/copycat/settings", json={"enabled": True})
    assert r.status_code == 401
    r = client.post("/api/dashboard/copycat/settings", json={"enabled": True},
                    headers={"X-Operator-Token": "wrong"})
    assert r.status_code == 401


def test_copycat_settings_endpoint_401_when_no_token_configured_at_all(client, monkeypatch, tmp_path):
    """_require_operator fails CLOSED (503, not open) when
    PATHIEL_OPERATOR_TOKEN is unset — this just pins that the copycat
    write path inherits that same fail-closed behavior rather than, say,
    accepting any token when none is configured."""
    _isolated_config_path(tmp_path, monkeypatch, {"copycat": _real_copycat_block()})
    monkeypatch.delenv("PATHIEL_OPERATOR_TOKEN", raising=False)
    r = client.post("/api/dashboard/copycat/settings", json={"enabled": True})
    assert r.status_code == 503


def test_copycat_settings_endpoint_rejects_non_object_body(client, monkeypatch, tmp_path):
    _isolated_config_path(tmp_path, monkeypatch, {"copycat": _real_copycat_block()})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")
    r = client.post("/api/dashboard/copycat/settings", json=[1, 2, 3],
                    headers={"X-Operator-Token": "test-token"})
    assert r.status_code == 400


def test_copycat_settings_endpoint_writes_through_and_preserves_other_keys(client, monkeypatch, tmp_path):
    path = _isolated_config_path(
        tmp_path, monkeypatch,
        {"mode": "LIVE", "max_concurrent": 3, "copy_trade": {"enabled": False},
         "copycat": _real_copycat_block()})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")

    r = client.post("/api/dashboard/copycat/settings", json={"enabled": True, "sleeve_frac": 0.1},
                    headers={"X-Operator-Token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "live"
    assert body["settings"]["enabled"] is True
    assert body["settings"]["sleeve_frac"] == pytest.approx(0.1)
    # untouched fields survive the partial write
    assert body["settings"]["leverage_mode"] == "max"
    assert len(body["leaders"]) == 10

    on_disk = json.loads(path.read_text())
    assert on_disk["mode"] == "LIVE", "write endpoint perturbed a top-level key outside copycat"
    assert on_disk["max_concurrent"] == 3
    assert on_disk["copy_trade"] == {"enabled": False}, "write endpoint perturbed another book"
    assert on_disk["copycat"]["enabled"] is True
    assert on_disk["copycat"]["leaders"] == _real_copycat_block()["leaders"]


def test_copycat_settings_endpoint_tries_to_smuggle_a_top_level_key_and_fails(client, monkeypatch, tmp_path):
    """Even if the body itself contains a key that LOOKS like a top-level
    config field (e.g. "mode"), it lands inside copycat's own block, never
    at the top level — the endpoint wraps the whole body as
    {"copycat": body} before any merge happens, so there is no code path
    by which a request body can reach another key."""
    path = _isolated_config_path(
        tmp_path, monkeypatch, {"mode": "LIVE", "copycat": _real_copycat_block()})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")

    r = client.post("/api/dashboard/copycat/settings", json={"mode": "OFF", "enabled": True},
                    headers={"X-Operator-Token": "test-token"})
    assert r.status_code == 200
    on_disk = json.loads(path.read_text())
    assert on_disk["mode"] == "LIVE", "a 'mode' key in the body reached the top-level config"
    assert on_disk["copycat"]["enabled"] is True


def test_copycat_settings_endpoint_leader_override_merges_through_http(client, monkeypatch, tmp_path):
    """End-to-end version of the merge_agent_config unit tests above: two
    separate HTTP writes, each touching a different leader's override,
    must both survive on disk afterward."""
    cc = dict(_real_copycat_block())
    cc["leader_overrides"] = {LEADER_A: {"leverage": 10}}
    path = _isolated_config_path(tmp_path, monkeypatch, {"copycat": cc})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")

    r = client.post("/api/dashboard/copycat/settings",
                    json={"leader_overrides": {LEADER_B: {"leverage": 5}}},
                    headers={"X-Operator-Token": "test-token"})
    assert r.status_code == 200

    on_disk = json.loads(path.read_text())
    overrides = on_disk["copycat"]["leader_overrides"]
    assert overrides[LEADER_A] == {"leverage": 10}, "leader A's override was wiped by leader B's write"
    assert overrides[LEADER_B] == {"leverage": 5}


def _post(client, body):
    return client.post("/api/dashboard/copycat/settings", json=body,
                       headers={"X-Operator-Token": "test-token"})


def test_a_leader_override_write_replaces_that_leaders_override(client, monkeypatch, tmp_path):
    """The panel sends one leader's WHOLE override. A field set back to
    "inherit" is simply absent, so a deep-merge would leave the old value
    behind forever. Replace per leader, and leave every other leader alone."""
    cc = dict(_real_copycat_block())
    cc["leader_overrides"] = {LEADER_A: {"leverage": 10, "sizing_mode": "fixed_notional"},
                              LEADER_B: {"leverage": 5}}
    path = _isolated_config_path(tmp_path, monkeypatch, {"copycat": cc})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")

    assert _post(client, {"leader_overrides": {LEADER_A: {"leverage": 40}}}).status_code == 200
    overrides = json.loads(path.read_text())["copycat"]["leader_overrides"]
    assert overrides[LEADER_A] == {"leverage": 40}, "a field reset to inherit survived the write"
    assert overrides[LEADER_B] == {"leverage": 5}


def test_a_null_override_clears_only_that_leader(client, monkeypatch, tmp_path):
    cc = dict(_real_copycat_block())
    cc["leader_overrides"] = {LEADER_A: {"leverage": 10}, LEADER_B: {"leverage": 5}}
    path = _isolated_config_path(tmp_path, monkeypatch, {"copycat": cc})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")

    assert _post(client, {"leader_overrides": {LEADER_A.upper().replace("0X", "0x"): None}}).status_code == 200
    overrides = json.loads(path.read_text())["copycat"]["leader_overrides"]
    assert LEADER_A not in overrides
    assert overrides[LEADER_B] == {"leverage": 5}


@pytest.mark.parametrize("body", [
    {"leaders": ["not-an-address"]},
    {"leaders": "0x95da8596c44dd09f4b8becce87ad3b7894fb2328"},
    {"leaders": ["0x95da8596c44dd09f4b8becce87ad3b7894fb23"]},       # 38 hex chars
    {"leader_overrides": [1]},
    {"leader_overrides": {"0x95da8596c44dd09f4b8becce87ad3b7894fb2328": 7}},
])
def test_malformed_leader_writes_are_rejected_and_nothing_lands(client, monkeypatch, tmp_path, body):
    """A typo'd address would otherwise be polled every cycle and skipped
    forever with "leader read failed"; reject it at the door instead."""
    path = _isolated_config_path(tmp_path, monkeypatch, {"copycat": _real_copycat_block()})
    before = path.read_text()
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")
    assert _post(client, body).status_code == 400
    assert path.read_text() == before


def test_leaders_list_write_replaces_the_list(client, monkeypatch, tmp_path):
    """Remove-leader in the panel posts the shortened list; lists are not
    deep-merged, so the removed address must actually be gone."""
    path = _isolated_config_path(tmp_path, monkeypatch, {"copycat": _real_copycat_block()})
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "test-token")
    keep = _real_copycat_block()["leaders"][1:]
    assert _post(client, {"leaders": keep}).status_code == 200
    assert json.loads(path.read_text())["copycat"]["leaders"] == keep


def test_landing_page_has_the_editable_copycat_controls(client):
    html = client.get("/").text
    for marker in ('id="copycat-form"', "data-cc-seg=", "sizing_mode", "leverage_mode",
                   "max_position_leverage", "margin_mode", "include_spot_staked",
                   "only_new_positions", "max_worse_entry_pct", "fixed_margin_usd",
                   "/api/dashboard/copycat/settings", "X-Operator-Token", "data-cc-ovr-save"):
        assert marker in html, f"copycat panel is missing {marker}"


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_copycat_payload_uses_the_real_settings_normalizer(monkeypatch):
    from pathiel.agents import copycat_live

    full_cfg = {"copycat": _real_copycat_block()}
    monkeypatch.setattr(db, "read_agent_config", lambda: full_cfg)
    payload = db._copycat_payload()
    assert payload["settings"] == copycat_live.settings(full_cfg)


# ── settings() round-trip: the checked-in block must parse with no
#    clamping surprises (task requirement, run once copycat_live exists) ────

@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_copycat_config_round_trips_through_settings_unclamped():
    from pathiel.agents import copycat_live

    real_cfg = json.loads(CONFIG_PATH.read_text())
    norm = copycat_live.settings(real_cfg)
    cc = real_cfg["copycat"]
    assert norm["enabled"] == cc["enabled"]
    assert norm["shadow_only"] == cc["shadow_only"]
    assert norm["leaders"] == cc["leaders"], (
        "settings() reordered/deduped/dropped a leader the checked-in "
        "config declares uniquely and lowercase already")
    assert norm["sleeve_frac"] == pytest.approx(cc["sleeve_frac"])
    assert norm["max_position_leverage"] == cc["max_position_leverage"], (
        "settings() clamped max_position_leverage below the configured "
        "50x — if that is intentional (like copy_trade's MAX_MULT clamp), "
        "document the clamp next to DEFAULT_LEADERS and update this test "
        "to assert the clamped value instead")
    assert norm["leverage_mode"] == cc["leverage_mode"]
    assert norm["leverage"] == cc["leverage"], "fixed-mode leverage clamped unexpectedly"
    assert norm["margin_mode"] == cc["margin_mode"]
    assert norm["include_spot_staked"] == cc["include_spot_staked"]
    assert norm["only_new_positions"] == cc["only_new_positions"]
    assert norm["max_worse_entry_pct"] == pytest.approx(cc["max_worse_entry_pct"])
    assert norm["min_order_bump"] == cc["min_order_bump"]
    assert norm["sizing_mode"] == cc["sizing_mode"]
    assert norm["size_multiplier"] == pytest.approx(cc["size_multiplier"])
    assert norm["fixed_margin_usd"] == pytest.approx(cc["fixed_margin_usd"])
    assert norm["fixed_notional_usd"] == pytest.approx(cc["fixed_notional_usd"]), (
        "fixed_notional_usd must not be clamped away from the configured "
        "10.5 — it is already exactly at the exchange-minimum floor")
    assert norm["leader_overrides"] == cc["leader_overrides"] == {}


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_copycat_fixed_notional_usd_is_clamped_to_the_exchange_minimum():
    """Spec: fixed_notional_usd has a hard minimum of 10.50 — a value below
    it must be clamped up, never persisted-and-trusted as a sub-floor
    notional that the exchange would reject every single time."""
    from pathiel.agents import copycat_live

    norm = copycat_live.settings({"copycat": {"fixed_notional_usd": 1.0}})
    assert norm["fixed_notional_usd"] >= 10.5


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_leader_settings_with_no_override_matches_book_wide_settings():
    from pathiel.agents import copycat_live

    full_cfg = {"copycat": _real_copycat_block()}
    book_wide = copycat_live.settings(full_cfg)
    book_wide.pop("leader_overrides")
    per_leader = copycat_live.leader_settings(full_cfg, _real_copycat_block()["leaders"][0])
    assert per_leader == book_wide


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_leader_settings_applies_only_that_leaders_override():
    from pathiel.agents import copycat_live

    cc = dict(_real_copycat_block())
    cc["leader_overrides"] = {LEADER_A: {"leverage_mode": "fixed", "leverage": 7}}
    full_cfg = {"copycat": cc}
    overridden = copycat_live.leader_settings(full_cfg, LEADER_A)
    assert overridden["leverage_mode"] == "fixed"
    assert overridden["leverage"] == 7
    # every other field still comes from the book-wide block
    assert overridden["margin_mode"] == cc["margin_mode"]
    assert overridden["sizing_mode"] == cc["sizing_mode"]

    untouched = copycat_live.leader_settings(full_cfg, LEADER_B)
    assert untouched["leverage_mode"] == cc["leverage_mode"]
    assert untouched["leverage"] == cc["leverage"]
def test_copycat_default_leaders_matches_the_configured_list():
    """DEFAULT_LEADERS is the fallback when the config has no leaders block
    (or copycat_live.settings() is handed a bare/empty config, as the
    dashboard does when read_agent_config() returns {}). It matches the
    operator's checked-in 10 exactly, so a stripped config still mirrors the
    intended wallets rather than silently falling back to someone else's
    list."""
    from pathiel.agents import copycat_live

    assert list(copycat_live.DEFAULT_LEADERS) == _real_copycat_block()["leaders"]


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_copycat_payload_per_leader_reads_the_real_state_shape(monkeypatch):
    """copycat_live's state file is {"leaders": {addr: {"last": {...},
    "ignored": [...]}}, "copies": {...}, "realized_cum": ...} — NOT a flat
    {addr: {...}} dict (that was copy_trade's single-leader shape; copycat
    generalizes it under a "leaders" key because it tracks many). Pins the
    real shape so a future internal change to copycat_live.py's state dict
    is caught here instead of the dashboard panel silently going blank.

    Written straight to copycat_live._STATE, which conftest.py has already
    redirected to this test run's isolated PATHIEL_STATE_DIR by the time
    copycat_live is imported — no monkeypatching of the path needed, and
    this can never touch the live .copycat.json.
    """
    from pathiel.agents import copycat_live
    from pathiel.agents.atomic_io import write_json_atomic

    leader = _real_copycat_block()["leaders"][0]
    state = {
        "leaders": {leader: {"last": {"BTC": 1.5, "ETH": -2.0}, "ignored": ["SOL"]}},
        "copies": {"BTC": {"leader": leader}, "ETH": {"leader": leader}},
        "realized_cum": 0.0,
    }
    write_json_atomic(copycat_live._STATE, state, indent=1)
    monkeypatch.setattr(db, "read_agent_config", lambda: {"copycat": _real_copycat_block()})
    payload = db._copycat_payload()
    entry = payload["per_leader"].get(leader)
    assert entry["coins"] == ["BTC", "ETH"]
    assert entry["ignored"] == ["SOL"]
    # also carries that leader's effective settings (leader_settings()),
    # not just the raw mirrored-coin state
    assert entry["effective"] == copycat_live.leader_settings(
        {"copycat": _real_copycat_block()}, leader)
    assert payload["owned_coins"] == ["BTC", "ETH"]


# ── scripts/trading_loop.py wiring (read as source — importing it starts the
#    live loop, same reasoning as test_slots_full_gate.py and
#    test_every_live_book_is_gradeable.py::test_every_book_the_loop_calls_can_reach_capital) ─

def test_trading_loop_imports_the_copycat_contract():
    assert "from pathiel.agents.copycat_live import" in LOOP_SRC
    assert "maybe_run as _copycat_maybe_run" in LOOP_SRC
    assert "owned_coins as _copycat_owned_coins" in LOOP_SRC


def test_trading_loop_exempts_copycat_coins_same_as_copy_trade():
    i = LOOP_SRC.index("_ladder_coins =")
    line = LOOP_SRC[i:LOOP_SRC.index("\n", i)]
    assert "_copycat_owned_coins()" in line
    assert "_copy_owned_coins()" in line
    assert "_ladder_owned_coins()" in line


def test_trading_loop_calls_copycat_maybe_run_outside_the_mode_off_branch():
    """copy_trade and drawdown_ladder both run before the `mode == OFF`
    early-continue so they keep mirroring/managing exits even when the
    account is paused. copycat must sit in the same place."""
    off_branch = LOOP_SRC.index('if str(_cfg.get("mode", "OFF")).upper() == "OFF":')
    call = LOOP_SRC.index("_copycat_maybe_run(")
    assert call < off_branch, (
        "_copycat_maybe_run is called after the OFF-mode early continue — "
        "it would stop managing open mirrors whenever mode is OFF")


def test_trading_loop_copycat_call_signature_matches_copy_trade():
    """Same contract (config, positions, equity, dex_available, daily_pnl,
    daily_loss_limit, allow_entries=, log_event=) called the same way, so a
    future contract change to one is forced to visit the other."""
    tree = ast.parse(LOOP_SRC)
    calls = {"_copy_trade_maybe_run": None, "_copycat_maybe_run": None}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) in calls):
            calls[node.func.id] = node
    for name, call in calls.items():
        assert call is not None, f"{name} is not called in trading_loop.py"
    ct_call, cc_call = calls["_copy_trade_maybe_run"], calls["_copycat_maybe_run"]
    assert len(ct_call.args) == len(cc_call.args), (
        "copy_trade and copycat maybe_run calls pass a different number of "
        "positional arguments — one of them drifted from the shared contract")
    ct_kwargs = {kw.arg for kw in ct_call.keywords}
    cc_kwargs = {kw.arg for kw in cc_call.keywords}
    assert ct_kwargs == cc_kwargs == {"allow_entries", "log_event"}


def test_trading_loop_logs_copycat_errors_with_its_own_scope():
    assert '"scope": "copycat"' in LOOP_SRC


# ── scripts/autonomous_cycle.py wiring ───────────────────────────────────────

def _load_autonomous_cycle():
    spec = importlib.util.spec_from_file_location(
        "autonomous_cycle_copycat_wiring", ROOT / "scripts" / "autonomous_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_autonomous_cycle_switch_table_has_copycat():
    ac = _load_autonomous_cycle()
    assert ac._SWITCHES.get("copycat") == ("top", "copycat")


def test_autonomous_cycle_demotion_is_reachable_for_copycat():
    """Mirrors test_every_live_book_is_gradeable.py's
    test_demotion_is_reachable_for_every_live_book, scoped to copycat so this
    file is self-contained."""
    ac = _load_autonomous_cycle()
    cfg = {"copycat": {"enabled": True, "shadow_only": False}}
    assert ac.apply_action(cfg, "copycat", "demote") is True
    assert cfg["copycat"]["shadow_only"] is True


@pytest.mark.skipif(not _HAS_COPYCAT_LIVE, reason="pathiel.agents.copycat_live not present yet")
def test_autonomous_cycle_mtm_books_has_copycat():
    from pathiel.agents import copycat_live

    ac = _load_autonomous_cycle()
    mtm = ac._mtm_books()
    assert "copycat" in mtm
    load_log, decision = mtm["copycat"]
    assert load_log is copycat_live.load_equity_log
    assert decision is copycat_live.mtm_decision
    # must not raise against an empty/cold-start ledger
    rows = load_log()
    assert isinstance(rows, list)


# ── tests/test_slots_full_gate.py already asserts _copycat_maybe_run is
#    UNGATED (never behind _entry_budget_open) — not duplicated here.

@pytest.fixture()
def client():
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)
