"""signer_status: is the API wallet that signs our orders still approved?

Regression for 2026-11-09: the signing wallet 0xD46C...eb5b dropped off the
account, every place_bracket failed for 11 hours, and the overview still said
can_trade=true."""
from __future__ import annotations

import json
import os
import sys

import pytest

from services.accounts import signer_status as S

MAIN = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"
SIGNER = "0xD46C20732c480f41f4603aEdBB0F73F6AF78eb5b"
NOW = 1_794_000_000_000
# the account's approved list on 2026-11-09 (public /info extraAgents)
AGENTS_1109 = [{"name": "copycat", "address": "0xa018000000000000000000000000000000555d", "validUntil": 1798944201165},
               {"name": "vps", "address": "0x3c50000000000000000000000000000000bff8", "validUntil": 1794985141622}]


@pytest.fixture(autouse=True)
def _fresh_cache():
    S.clear_cache()
    yield
    S.clear_cache()


def test_the_2026_11_09_outage_reads_refused():
    v = S.assess(AGENTS_1109, MAIN, SIGNER, NOW)
    assert v["ok"] is False
    assert SIGNER in v["reason"]                      # the full address to approve again
    assert "copycat" in v["reason"] and "vps" in v["reason"]


def test_listed_signer_is_ok_case_insensitive():
    agents = AGENTS_1109 + [{"name": "pathiel", "address": SIGNER.lower(), "validUntil": NOW + 30 * S.DAY_MS}]
    v = S.assess(agents, MAIN, SIGNER, NOW)
    assert v["ok"] is True and v["name"] == "pathiel" and v["warning"] is None
    assert v["days_left"] == 30.0


def test_ending_soon_warns_but_trades():
    agents = [{"name": "pathiel", "address": SIGNER, "validUntil": NOW + 2 * S.DAY_MS}]
    v = S.assess(agents, MAIN, SIGNER, NOW)
    assert v["ok"] is True and "2.0 days" in v["warning"]


def test_past_valid_until_is_refused():
    agents = [{"name": "pathiel", "address": SIGNER, "validUntil": NOW - 1}]
    assert S.assess(agents, MAIN, SIGNER, NOW)["ok"] is False


def test_no_expiry_field_is_ok():
    v = S.assess([{"name": "", "address": SIGNER}], MAIN, SIGNER, NOW)
    assert v["ok"] is True and v["days_left"] is None


def test_master_key_needs_no_approval():
    v = S.assess([], MAIN, MAIN.lower(), NOW)
    assert v["ok"] is True and v["kind"] == "master"


def test_live_failed_read_is_unknown_not_refused(monkeypatch):
    monkeypatch.setattr(S, "current_identity", lambda: (MAIN, SIGNER))
    import services.accounts.hl_verify as hv

    def boom(_m):
        raise RuntimeError("http 503")
    monkeypatch.setattr(hv, "extra_agents", boom)
    v = S.live(max_age_s=0)
    assert v["ok"] is None and "503" in v["reason"]
    assert S._cache["value"] is None                  # a failed read is not cached


def test_live_caches_a_good_read(monkeypatch):
    calls = []
    monkeypatch.setattr(S, "current_identity", lambda: (MAIN, SIGNER))
    import services.accounts.hl_verify as hv
    monkeypatch.setattr(hv, "extra_agents", lambda m: calls.append(m) or AGENTS_1109)
    t = [1000.0]
    a = S.live(max_age_s=300, clock=lambda: t[0])
    t[0] += 100
    b = S.live(max_age_s=300, clock=lambda: t[0])
    t[0] += 300
    S.live(max_age_s=300, clock=lambda: t[0])
    assert a is b and a["ok"] is False and len(calls) == 2


def test_current_identity_returns_address_never_the_key(monkeypatch):
    from eth_account import Account
    acct = Account.create()
    key = acct.key.hex()
    import pathiel.client.exchange as ex
    monkeypatch.setattr(ex, "_resolve_identity", lambda: (key, MAIN, None, True))
    main, signer = S.current_identity()
    assert main == MAIN and signer == acct.address
    import services.accounts.hl_verify as hv
    monkeypatch.setattr(hv, "extra_agents", lambda m: [])
    out = json.dumps(S.live(max_age_s=0))
    assert key.removeprefix("0x") not in out and acct.address in out


def test_summary_blocks_trading_when_signer_refused(monkeypatch):
    from services.accounts import summary
    monkeypatch.setattr(S, "live", lambda *a, **k: S.assess(AGENTS_1109, MAIN, SIGNER, NOW))

    class Acct:
        mode, source, verified_at, main_address = "trade", "env", "now", MAIN
    blockers = summary._blockers_for(Acct(), state_ok=True)
    assert len(blockers) == 1 and SIGNER in blockers[0]


def test_summary_unknown_or_ok_adds_no_blocker(monkeypatch):
    from services.accounts import summary

    class Acct:
        mode, source, verified_at, main_address = "trade", "env", "now", MAIN
    monkeypatch.setattr(S, "live", lambda *a, **k: {"ok": None, "reason": "503"})
    assert summary._blockers_for(Acct(), state_ok=True) == []
    monkeypatch.setattr(S, "live", lambda *a, **k: {"ok": True, "reason": "fine"})
    assert summary._blockers_for(Acct(), state_ok=True) == []


def _trigger_watch():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.path.insert(0, os.path.join(root, "scripts"))
    import trigger_watch
    return trigger_watch


def test_watcher_fires_once_per_six_hours_on_refused_signer():
    tw = _trigger_watch()
    refused = S.assess(AGENTS_1109, MAIN, SIGNER, NOW)
    fired = {}
    line = tw.signer_fire(refused, fired, 10_000.0, "oil")
    assert line["reason"] == "signer" and line["fire"] == "oil" and line["signer"] == SIGNER
    assert tw.signer_fire(refused, fired, 10_000.0 + 3600, "oil") is None
    assert tw.signer_fire(refused, fired, 10_000.0 + tw.SIGNER_REPEAT_S, "oil") is not None


def test_watcher_ignores_ok_and_unknown():
    tw = _trigger_watch()
    assert tw.signer_fire({"ok": True}, {}, 1.0, "oil") is None
    assert tw.signer_fire({"ok": None, "reason": "503"}, {}, 1.0, "oil") is None
