"""Agent approval and the pull-request-time statement checks, on top of the
venue reads `contracts.pool` defines the shapes for. The pure decision lives
in the contract (`pool.agent_approved`, `pool.leave_signer_allowed`); this
module makes the two reads and hands them to it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import contracts.pool as pool
from services.pool import venue

Post = Callable[[Dict[str, Any]], Any]


def _names_signer(extra_agents_reply: Any, signer: str) -> bool:
    """True when `extra_agents_reply` is a list that NAMES `signer` (listed,
    whether or not its validUntil has passed). check_agent reads userRole only
    when the list does not name the signer at all."""
    if not isinstance(extra_agents_reply, list):
        return False
    low = signer.lower()
    return any(isinstance(e, dict) and str(e.get("address") or "").lower() == low
              for e in extra_agents_reply)


def check_agent(address: str, signer: str, *, now_ms: int, post: Optional[Post] = None) -> Tuple[bool, str]:
    """Is `signer` an approved API wallet of `address`, asking the venue?

    One read (extraAgents for `address`) when the reply names the signer;
    a second (userRole for `signer`) only when it does not. `post` defaults
    to services.pool.venue.post_info, resolved at call time so a test that
    monkeypatches venue.post_info is honored without passing `post`.
    venue.VenueError propagates: a failed read must never be read as
    "not approved"."""
    post_fn = post if post is not None else venue.post_info
    reply1 = post_fn(pool.extra_agents_request(address))
    if _names_signer(reply1, signer):
        return pool.agent_approved(address, signer, reply1, None, now_ms)
    reply2 = post_fn(pool.user_role_request(signer))
    return pool.agent_approved(address, signer, reply1, reply2, now_ms)


def check_statement(v: pool.Verified, *, pool_root: str, now_ms: int,
                    post: Optional[Post] = None) -> List[Tuple[str, str]]:
    """(code, message) problems with one verified statement at pull-request
    time. Offline rules first, then the one venue read a join or a leave
    needs (none at all for a leave signed by the master or by the join's own
    signer: see `pool.leave_signer_allowed`'s documented gap)."""
    problems: List[Tuple[str, str]] = []
    stale = pool.fresh_problem(v["at_ms"], now_ms)
    if stale is not None:
        problems.append(("stale", stale))

    address, signer = v["address"], v["signer"]
    if v["kind"] == "join":
        if v["signer_role"] == "agent":
            approved, reason = check_agent(address, signer, now_ms=now_ms, post=post)
            if not approved:
                problems.append(("agent_not_approved",
                                 f"{pool.short_address(address)} has not approved API wallet "
                                 f"{pool.short_address(signer)} to join for it ({reason})"))
        return problems

    # leave
    try:
        join_in_force: Optional[pool.Verified] = pool.load_statement(
            pool.member_path(pool_root, address), "join")
    except pool.PoolError:
        join_in_force = None
    if v["signer_role"] == "master" or (join_in_force is not None and join_in_force["signer"] == signer):
        return problems    # no venue read: the account itself, or the key that signed the join
    agent_ok, _reason = check_agent(address, signer, now_ms=now_ms, post=post)
    if not pool.leave_signer_allowed(v, join_in_force, agent_ok):
        problems.append(("leave_signer",
                         f"{pool.short_address(address)}'s leave is signed by "
                         f"{pool.short_address(signer)}, which is neither the account, the key that "
                         "signed the join in force, nor an API wallet the account approves now"))
    return problems
