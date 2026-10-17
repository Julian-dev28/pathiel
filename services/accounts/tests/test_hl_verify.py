"""services.accounts.hl_verify — read-only Hyperliquid /info checks.
Every network call is mocked; this module must never place a real request.
"""
from __future__ import annotations

import pytest

from services.accounts import hl_verify
from pathiel.client.hl_client import HLRequestError

MAIN = "0x" + "a1" * 20
AGENT = "0x" + "b2" * 20


def _mock_post(monkeypatch, result):
    calls = []

    def fake(path, payload, timeout=5):
        calls.append((path, payload))
        return result

    monkeypatch.setattr(hl_verify, "_http_post_strict", fake)
    return calls


def test_is_agent_approved_true_when_address_present(monkeypatch):
    calls = _mock_post(monkeypatch, [{"name": "pathiel", "address": AGENT, "validUntil": 123}])
    assert hl_verify.is_agent_approved(MAIN, AGENT) is True
    assert calls == [("/info", {"type": "extraAgents", "user": MAIN})]


def test_is_agent_approved_true_case_insensitive(monkeypatch):
    _mock_post(monkeypatch, [{"name": "pathiel", "address": AGENT.upper(), "validUntil": 1}])
    assert hl_verify.is_agent_approved(MAIN, AGENT.lower()) is True


def test_is_agent_approved_false_when_absent(monkeypatch):
    _mock_post(monkeypatch, [])
    assert hl_verify.is_agent_approved(MAIN, AGENT) is False


def test_is_agent_approved_false_when_list_has_other_agents(monkeypatch):
    other = "0x" + "c3" * 20
    _mock_post(monkeypatch, [{"name": "someone-else", "address": other, "validUntil": 1}])
    assert hl_verify.is_agent_approved(MAIN, AGENT) is False


def test_is_agent_approved_false_for_empty_agent_address(monkeypatch):
    _mock_post(monkeypatch, [{"name": "x", "address": AGENT, "validUntil": 1}])
    assert hl_verify.is_agent_approved(MAIN, "") is False


def test_extra_agents_tolerates_a_non_list_response(monkeypatch):
    _mock_post(monkeypatch, {"unexpected": "shape"})
    assert hl_verify.extra_agents(MAIN) == []


def test_is_agent_approved_propagates_network_failure(monkeypatch):
    def boom(path, payload, timeout=5):
        raise HLRequestError("simulated")
    monkeypatch.setattr(hl_verify, "_http_post_strict", boom)
    with pytest.raises(HLRequestError):
        hl_verify.is_agent_approved(MAIN, AGENT)


def test_user_role_returns_the_role_string(monkeypatch):
    _mock_post(monkeypatch, {"role": "user"})
    assert hl_verify.user_role(MAIN) == "user"


def test_user_role_unknown_on_bad_shape(monkeypatch):
    _mock_post(monkeypatch, None)
    assert hl_verify.user_role(MAIN) == "unknown"
