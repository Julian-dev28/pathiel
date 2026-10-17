"""The account registry: labels, addresses, mode, source, and which one is
active. Lives at `$PATHIEL_STATE_DIR/accounts/accounts.json`
(`services.accounts.env.registry_path()`).

Never a private key — those live one-per-file, encrypted, in
`services.accounts.vault`. This file is plain JSON on purpose: addresses are
public (SWARM_RULES docs/overhaul/SWARM_RULES.md section 10), so a tool like
`scripts/preflight_secrets.py` is meant to be able to read it directly for the
addresses and source alone, without importing this module or touching the
vault.

Every write is atomic (temp file + `os.replace`) and chmod 600. A
`threading.Lock` serializes mutations within this process; cross-process
safety comes from the same atomic-replace property plus
`services.accounts.credentials`'s fingerprint-based cache, which keys off the
registry file's own mtime rather than trusting any one process's view of how
many times it has mutated the file.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.accounts import env as accounts_env
from services.accounts import vault

logger = logging.getLogger(__name__)

REGISTRY_SCHEMA_VERSION = 1

_LOCK = threading.Lock()

# In-process mutation counter. Belt-and-suspenders alongside the registry
# file's own mtime (see credentials.py): covers the vanishingly rare case of
# two writes landing within the same filesystem mtime tick, at zero cost.
_GENERATION = 0


def generation() -> int:
    return _GENERATION


def _bump_generation() -> None:
    global _GENERATION
    _GENERATION += 1


@dataclass
class StoredAccount:
    id: str
    label: str
    main_address: str
    agent_address: Optional[str]
    mode: str              # "trade" | "watch"
    source: str            # "wallet_sign" | "api_key" | "watch" (never "env" — synthesized, not stored)
    created_at: str        # ISO 8601, UTC
    verified_at: Optional[str]
    agent_name: Optional[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_registry() -> Dict[str, Any]:
    return {"version": REGISTRY_SCHEMA_VERSION, "rev": 0, "active_id": None, "accounts": []}


def _read_raw() -> Dict[str, Any]:
    path = accounts_env.registry_path()
    if not os.path.exists(path):
        return _empty_registry()
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        logger.warning(f"[accounts.registry] unreadable registry at {path}, treating as empty: {e}")
        return _empty_registry()
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        logger.warning(f"[accounts.registry] malformed registry at {path}, treating as empty")
        return _empty_registry()
    data.setdefault("rev", 0)
    data.setdefault("active_id", None)
    return data


def _write_raw(data: Dict[str, Any]) -> None:
    data["version"] = REGISTRY_SCHEMA_VERSION
    data["rev"] = int(data.get("rev", 0)) + 1
    directory = accounts_env.accounts_dir()
    os.makedirs(directory, exist_ok=True)
    path = accounts_env.registry_path()
    tmp_path = f"{path}.tmp-{os.getpid()}"
    with open(tmp_path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def rev() -> int:
    """The registry file's own monotonic write counter (0 if it does not
    exist yet). credentials.py mixes this with the file's mtime for its
    cache fingerprint."""
    return int(_read_raw().get("rev", 0))


def list_accounts() -> List[StoredAccount]:
    data = _read_raw()
    out = []
    for a in data.get("accounts", []):
        try:
            out.append(StoredAccount(**a))
        except TypeError as e:
            logger.warning(f"[accounts.registry] dropping unreadable account entry: {e}")
    return out


def get_active_id() -> Optional[str]:
    return _read_raw().get("active_id")


def get(account_id: str) -> Optional[StoredAccount]:
    for a in list_accounts():
        if a.id == account_id:
            return a
    return None


def get_active() -> Optional[StoredAccount]:
    active_id = get_active_id()
    if not active_id:
        return None
    return get(active_id)


def _next_id() -> str:
    return "acc_" + uuid.uuid4().hex[:12]


def add(*, label: str, main_address: str, agent_address: Optional[str], mode: str,
        source: str, agent_name: Optional[str] = None, verified: bool = False,
        activate: bool = False) -> StoredAccount:
    """Append a new account and persist it.

    Becomes the active account when there is no active one already —
    covers both the very first account ever connected and the state after
    every account has been removed — but NEVER steals activation from an
    existing active account just because a new one was added. Matches
    wallet-ui's own client-side rule (it auto-activates a freshly connected
    account only when it is the very first one) so the two can never
    disagree about which account is "the" active one: connecting a second
    wallet to watch, or as a backup, must not silently start trading through
    it. `activate=True` is the explicit override for a caller that really
    does want to force-switch (equivalent to calling set_active right after)."""
    with _LOCK:
        data = _read_raw()
        account_id = _next_id()
        now = _now_iso()
        stored = StoredAccount(
            id=account_id, label=label[:48], main_address=main_address.lower(),
            agent_address=agent_address.lower() if agent_address else None,
            mode=mode, source=source, created_at=now,
            verified_at=now if verified else None, agent_name=agent_name,
        )
        data.setdefault("accounts", []).append(asdict(stored))
        if activate or not data.get("active_id"):
            data["active_id"] = account_id
        _write_raw(data)
        _bump_generation()
        return stored


def set_active(account_id: str) -> StoredAccount:
    with _LOCK:
        data = _read_raw()
        match = next((a for a in data.get("accounts", []) if a["id"] == account_id), None)
        if match is None:
            raise KeyError(account_id)
        data["active_id"] = account_id
        _write_raw(data)
        _bump_generation()
        return StoredAccount(**match)


def clear_active() -> None:
    """Deactivate every stored account — used when the caller "activates"
    the synthetic env pseudo-account (view.ENV_ACCOUNT_ID), which has no row
    here to point `active_id` at. `view.get_effective_active_account()`
    treats `active_id is None` as "fall back to the env identity",
    consistently with `get_trading_credentials()`'s own priority."""
    with _LOCK:
        data = _read_raw()
        data["active_id"] = None
        _write_raw(data)
        _bump_generation()


def rename(account_id: str, label: str) -> StoredAccount:
    with _LOCK:
        data = _read_raw()
        match = next((a for a in data.get("accounts", []) if a["id"] == account_id), None)
        if match is None:
            raise KeyError(account_id)
        match["label"] = label[:48]
        _write_raw(data)
        _bump_generation()
        return StoredAccount(**match)


def delete(account_id: str) -> None:
    """Remove the account and its vault key (if any). If the deleted account
    was active, the next remaining account (in list order) becomes active, or
    None if that was the last one."""
    with _LOCK:
        data = _read_raw()
        accounts = data.get("accounts", [])
        remaining = [a for a in accounts if a["id"] != account_id]
        if len(remaining) == len(accounts):
            raise KeyError(account_id)
        data["accounts"] = remaining
        if data.get("active_id") == account_id:
            data["active_id"] = remaining[0]["id"] if remaining else None
        _write_raw(data)
        _bump_generation()
    vault.delete_key(account_id)
