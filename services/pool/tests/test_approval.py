"""services/pool/approval.py: agent approval and the pull-request-time
statement checks, against a fake venue that records every payload it is
given. Offline; `post` never reaches the network."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

import contracts.pool as pool
from services.pool import approval, venue

ADDR = "0x" + "ab" * 20
SIGNER = "0x" + "cd" * 20
NOW = pool.ms_from_iso("2026-11-12T15:00:00Z")


def _fake_post(replies: List[Any]):
    calls: List[Dict[str, Any]] = []
    queue = list(replies)

    def post(payload: Dict[str, Any]) -> Any:
        calls.append(payload)
        return queue.pop(0)
    return post, calls


# ── check_agent ──────────────────────────────────────────────────────────────

def test_check_agent_listed_and_valid_is_one_read():
    reply = [{"name": "pathiel", "address": SIGNER, "validUntil": NOW + 1}]
    post, calls = _fake_post([reply])
    assert approval.check_agent(ADDR, SIGNER, now_ms=NOW, post=post) == (True, "extraAgents")
    assert calls == [pool.extra_agents_request(ADDR)]


def test_check_agent_listed_and_expired_is_one_read():
    reply = [{"name": "pathiel", "address": SIGNER, "validUntil": NOW - 1}]
    post, calls = _fake_post([reply])
    assert approval.check_agent(ADDR, SIGNER, now_ms=NOW, post=post) == (False, "expired")
    assert calls == [pool.extra_agents_request(ADDR)]


def test_check_agent_not_listed_then_user_role_agent_of_this_account():
    reply1 = [{"name": "other", "address": "0x" + "ee" * 20, "validUntil": NOW + 1}]
    reply2 = {"role": "agent", "data": {"user": ADDR}}
    post, calls = _fake_post([reply1, reply2])
    assert approval.check_agent(ADDR, SIGNER, now_ms=NOW, post=post) == (True, "userRole")
    assert calls == [pool.extra_agents_request(ADDR), pool.user_role_request(SIGNER)]


def test_check_agent_agent_of_another_account():
    reply1: List[Any] = []
    reply2 = {"role": "agent", "data": {"user": "0x" + "ee" * 20}}
    post, calls = _fake_post([reply1, reply2])
    assert approval.check_agent(ADDR, SIGNER, now_ms=NOW, post=post) == (False, "not_approved")
    assert calls == [pool.extra_agents_request(ADDR), pool.user_role_request(SIGNER)]


def test_check_agent_lets_venue_error_propagate():
    def boom(payload):
        raise venue.VenueError("down")
    with pytest.raises(venue.VenueError):
        approval.check_agent(ADDR, SIGNER, now_ms=NOW, post=boom)


def test_check_agent_defaults_post_to_venue_post_info(monkeypatch):
    seen = []

    def fake_post_info(payload, **kwargs):
        seen.append(payload)
        return [{"name": "pathiel", "address": SIGNER, "validUntil": NOW + 1}]
    monkeypatch.setattr(venue, "post_info", fake_post_info)
    assert approval.check_agent(ADDR, SIGNER, now_ms=NOW) == (True, "extraAgents")
    assert seen == [pool.extra_agents_request(ADDR)]


# ── check_statement ──────────────────────────────────────────────────────────

def _verified(kind: str, role: str, at_ms: int = NOW - 1000) -> pool.Verified:
    signer = ADDR if role == "master" else SIGNER
    return {"kind": kind, "address": ADDR, "signer": signer, "signer_role": role,
            "at": pool.iso_from_ms(at_ms), "at_ms": at_ms}


def test_check_statement_stale(tmp_path):
    old_ms = NOW - pool.FRESH_MAX_AGE_S * 1000 - 1000
    v = _verified("join", "master", old_ms)
    problems = approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW)
    assert [c for c, _ in problems] == ["stale"]


def test_check_statement_future(tmp_path):
    future_ms = NOW + pool.FRESH_FUTURE_SKEW_S * 1000 + 1000
    v = _verified("join", "master", future_ms)
    problems = approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW)
    assert [c for c, _ in problems] == ["stale"]


def test_check_statement_master_join_needs_no_read(tmp_path):
    v = _verified("join", "master")

    def boom(payload):
        raise AssertionError("master join must never read the venue")
    assert approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=boom) == []


def test_check_statement_agent_join_not_approved():
    v = _verified("join", "agent")
    post, _ = _fake_post([[], {"role": "user"}])
    problems = approval.check_statement(v, pool_root="/nope", now_ms=NOW, post=post)
    assert [c for c, _ in problems] == ["agent_not_approved"]
    assert "not_approved" in problems[0][1]


def test_check_statement_agent_join_approved():
    v = _verified("join", "agent")
    post, _ = _fake_post([[{"name": "p", "address": SIGNER, "validUntil": NOW + 1}]])
    assert approval.check_statement(v, pool_root="/nope", now_ms=NOW, post=post) == []


def test_check_statement_agent_join_expired():
    v = _verified("join", "agent")
    post, _ = _fake_post([[{"name": "p", "address": SIGNER, "validUntil": NOW - 1}]])
    problems = approval.check_statement(v, pool_root="/nope", now_ms=NOW, post=post)
    assert [c for c, _ in problems] == ["agent_not_approved"]
    assert "expired" in problems[0][1]


def test_check_statement_leave_by_the_joins_own_signer_needs_no_read(tmp_path, new_account, signed, put):
    """The documented gap, pinned: the key that signed the join may leave
    with NO venue read, even when a fake venue would say that signer is
    revoked now."""
    acct, agent = new_account(), new_account()
    put(signed("join", acct, agent), root=str(tmp_path))
    leave = signed("leave", acct, agent)
    v = pool.verify_statement(leave)

    def says_revoked(payload):
        return [] if payload["type"] == "extraAgents" else {"role": "user"}
    assert approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=says_revoked) == []


def test_check_statement_leave_by_a_stranger_refused(tmp_path, new_account, signed, put):
    acct, agent, stranger = new_account(), new_account(), new_account()
    put(signed("join", acct, agent), root=str(tmp_path))
    leave = signed("leave", acct, stranger)
    v = pool.verify_statement(leave)
    post, _ = _fake_post([[], {"role": "user"}])
    problems = approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=post)
    assert [c for c, _ in problems] == ["leave_signer"]


def test_check_statement_leave_by_an_approved_other_agent_allowed(tmp_path, new_account, signed, put):
    acct, agent, other_agent = new_account(), new_account(), new_account()
    put(signed("join", acct, agent), root=str(tmp_path))
    leave = signed("leave", acct, other_agent)
    v = pool.verify_statement(leave)
    post, _ = _fake_post([[{"name": "p", "address": other_agent.address.lower(), "validUntil": NOW + 1}]])
    assert approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=post) == []


def test_check_statement_leave_by_master_needs_no_read(tmp_path):
    v = _verified("leave", "master")

    def boom(payload):
        raise AssertionError("a master leave must never read the venue")
    assert approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=boom) == []


def test_check_statement_leave_with_no_join_on_file_falls_through_to_the_venue(tmp_path, new_account, signed):
    """No member file at all (e.g. already removed): join_in_force is None,
    so the leave_signer_allowed table's second rule cannot match and the
    agent check decides."""
    acct, agent = new_account(), new_account()
    leave = signed("leave", acct, agent)
    v = pool.verify_statement(leave)
    post, calls = _fake_post([[{"name": "p", "address": agent.address.lower(), "validUntil": NOW + 1}]])
    assert approval.check_statement(v, pool_root=str(tmp_path), now_ms=NOW, post=post) == []
    assert calls == [pool.extra_agents_request(acct.address.lower())]
