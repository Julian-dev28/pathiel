"""Is the key that signs our orders still approved on the account, right now?

2026-11-09: every place_bracket from 07:38Z on failed with "API wallet
0xd46c...eb5b does not exist" for 11 hours. The signing API wallet had
dropped off the account's approved list (expired or removed), but the
overview kept saying `agent_approved: true` / `can_trade: true` because the
env account is stamped verified once, at load (services/accounts/view.py),
and nothing re-asked Hyperliquid. This module re-asks.

Read-only: one public `/info extraAgents` call. It derives the signer's
ADDRESS from the key in memory and never returns, logs or prints the key.

    assess(agents, main, signer, now_ms)   pure verdict
    live(max_age_s=300)                    current identity, cached
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

WARN_DAYS = 3.0          # say so this many days before the approval runs out
DAY_MS = 86_400_000


def short(addr: Optional[str]) -> str:
    a = addr or ""
    return f"{a[:6]}...{a[-4:]}" if len(a) > 12 else a


def assess(agents: List[Dict[str, Any]], main: str, signer: Optional[str], now_ms: int,
           warn_days: float = WARN_DAYS) -> Dict[str, Any]:
    """Verdict for `signer` signing for `main`, given Hyperliquid's extraAgents
    list for `main`. ok=True: orders will be accepted (maybe with a warning
    that the approval ends soon). ok=False: every order will be refused. Pure."""
    out: Dict[str, Any] = {"main": main, "signer": signer, "name": None,
                           "valid_until_ms": None, "days_left": None, "warning": None}
    if not signer or signer.lower() == (main or "").lower():
        return {**out, "ok": True, "kind": "master",
                "reason": "orders are signed by the account's own key"}
    match = next((a for a in agents if isinstance(a, dict)
                  and (a.get("address") or "").lower() == signer.lower()), None)
    approved = ", ".join(f"{a.get('name') or '(unnamed)'} {short(a.get('address'))}"
                         for a in agents if isinstance(a, dict)) or "none"
    if match is None:
        return {**out, "ok": False, "kind": "agent",
                "reason": (f"API wallet {short(signer)} is not approved on {short(main)} "
                           f"(expired or removed); every order will be refused. "
                           f"Approved now: {approved}. Fix: approve {signer} again on the "
                           f"Hyperliquid API page, or put an approved API wallet's key in .env.local")}
    until = match.get("validUntil")
    out.update(name=match.get("name") or None, valid_until_ms=until)
    if until:
        days = (int(until) - now_ms) / DAY_MS
        out["days_left"] = round(days, 2)
        if days <= 0:
            return {**out, "ok": False, "kind": "agent",
                    "reason": f"API wallet {short(signer)} approval ended; every order will be refused"}
        if days < warn_days:
            out["warning"] = (f"API wallet {short(signer)} approval ends in {days:.1f} days: "
                              f"approve it again on the Hyperliquid API page")
    return {**out, "ok": True, "kind": "agent", "reason": f"API wallet {short(signer)} is approved"}


def current_identity() -> Tuple[str, Optional[str]]:
    """(main address, signer address) for whatever identity would sign the
    next order. The signer is derived from the key, which is what Hyperliquid
    checks, not from the configured agent address (they could disagree)."""
    from eth_account import Account
    from pathiel.client.exchange import _resolve_identity
    key, main, _agent, _is_agent = _resolve_identity()
    signer = Account.from_key(key).address
    del key
    return main, signer


_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def live(max_age_s: float = 300.0, *, clock=time.time) -> Dict[str, Any]:
    """assess() for the current identity against Hyperliquid now. A failed
    read returns ok=None (unknown), never ok=False: a network blip must not
    look like a revoked key. Cached for `max_age_s` (dashboard polls)."""
    with _lock:
        now = clock()
        if _cache["value"] is not None and now - _cache["at"] < max_age_s:
            return _cache["value"]
        try:
            main, signer = current_identity()
        except Exception as e:  # noqa: BLE001 - no key at all is its own blocker elsewhere
            return {"ok": None, "reason": f"no signing identity: {str(e)[:120]}"}
        try:
            from services.accounts.hl_verify import extra_agents
            agents = extra_agents(main)
        except Exception as e:  # noqa: BLE001
            return {"ok": None, "main": main, "signer": signer,
                    "reason": f"could not read approvals from Hyperliquid: {str(e)[:120]}"}
        value = assess(agents, main, signer, int(now * 1000))
        _cache.update(at=now, value=value)
        return value


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, value=None)
