"""desk_brief's live-rule line sizes at the install's own leverage cap.

Regression for the public-install review finding: _live_rule read the raw
risk_profile.max_leverage with a 20x fallback, so a public profile without a
saved max_leverage showed a 20x live rule. The maintainer path keeps the raw
read and its 20x fallback byte for byte.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "scripts" / "pathiel-mcp-server.py"


def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_live_rule", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


class _Stop(Exception):
    pass


def _leverage_used(monkeypatch, *, public: bool, config: dict, cap=None, cap_raises=False):
    seen = {}

    def fake_bounded(fn, desk_id, lev, **kw):
        seen["lev"] = lev
        raise _Stop()

    monkeypatch.setattr(M, "_bounded", fake_bounded)
    monkeypatch.setattr(M.runtime, "is_public", lambda *a, **k: public)

    def fake_cap():
        if cap_raises:
            raise OSError("profile unreadable")
        return cap

    monkeypatch.setattr(M, "_profile_leverage_cap", fake_cap)
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda *a, **k: config)
    out = M._live_rule("oil", brief=None)
    assert out["next"] is None and "unavailable" in out["line"]
    return seen["lev"]


def test_operator_without_saved_leverage_keeps_the_20x_fallback(monkeypatch):
    assert _leverage_used(monkeypatch, public=False, config={"risk_profile": {}}) == 20.0


def test_operator_saved_leverage_is_used_as_is(monkeypatch):
    cfg = {"risk_profile": {"max_leverage": 20}}
    assert _leverage_used(monkeypatch, public=False, config=cfg, cap=3.0) == 20.0


def test_public_uses_the_profile_cap_not_20x(monkeypatch):
    assert _leverage_used(monkeypatch, public=True, config={"risk_profile": {}}, cap=3.0) == 3.0


@pytest.mark.parametrize("cap,raises", [(None, False), (0, False), (None, True)])
def test_public_unreadable_or_empty_cap_falls_back_to_the_public_default(monkeypatch, cap, raises):
    from services.risk.presets import PUBLIC_DEFAULT_PROFILE
    lev = _leverage_used(monkeypatch, public=True, config={}, cap=cap, cap_raises=raises)
    assert lev == PUBLIC_DEFAULT_PROFILE.max_leverage == 3.0
