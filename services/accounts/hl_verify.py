"""Read-only Hyperliquid `/info` verification. Never signs, never calls
`/exchange` — the one thing this module must never become is a second way to
place a real action.

Response shapes below were confirmed against the live public endpoint
(read-only, allowed by SWARM_RULES docs/overhaul/SWARM_RULES.md section 1)
during development, and against the installed SDK's own
`hyperliquid.info.Info.extra_agents` docstring:

    extraAgents -> [{"name": str, "address": str, "validUntil": int}, ...]
    userRole    -> {"role": "missing" | "user" | "vault" | "agent" | ...}
    clearinghouseState -> {..., "withdrawable": "<usd string>", ...}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from pathiel.client.hl_client import HLRequestError, _http_post_strict  # noqa: F401 (HLRequestError re-exported for callers)

logger = logging.getLogger(__name__)


def extra_agents(main_address: str) -> List[Dict[str, Any]]:
    """Every agent Hyperliquid currently has approved for `main_address`.
    Raises HLRequestError if the network call itself fails — a verification
    step must not silently treat "couldn't reach Hyperliquid" the same as
    "not approved"."""
    result = _http_post_strict("/info", {"type": "extraAgents", "user": main_address})
    return result if isinstance(result, list) else []


def is_agent_approved(main_address: str, agent_address: str) -> bool:
    """True if `agent_address` appears in `main_address`'s live
    extraAgents list right now. The only way this becomes True is because
    Hyperliquid itself says so — there is no local shortcut."""
    agent_lc = (agent_address or "").strip().lower()
    if not agent_lc:
        return False
    for entry in extra_agents(main_address):
        if isinstance(entry, dict) and (entry.get("address") or "").strip().lower() == agent_lc:
            return True
    return False


def user_role(address: str) -> str:
    """Hyperliquid's own classification of `address` ("missing", "user",
    "vault", "agent", ...). Used as a secondary signal / diagnostic; the
    primary approval check is `is_agent_approved`, which names WHICH agent,
    not just that some relationship exists."""
    result = _http_post_strict("/info", {"type": "userRole", "user": address})
    if isinstance(result, dict):
        return str(result.get("role", "unknown"))
    return "unknown"
