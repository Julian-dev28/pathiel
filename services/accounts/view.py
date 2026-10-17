"""Converts services.accounts.registry's internal StoredAccount rows into the
contract's `Account` model, and synthesizes the read-only "env" account from
`.env.local` (contracts/accounts/models.py item: "env — legacy .env.local
keys, shown read-only so old setups keep working").

The env account is never persisted — it is recomputed on every call from
`pathiel.client.exchange.resolve_env_identity()`, the same single source of
truth services.accounts.credentials' fallback leg uses, so what the UI shows
as the "env" account and what actually trades when nothing else is active can
never drift apart.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from contracts.accounts.models import Account as ContractAccount
from services.accounts.registry import StoredAccount

ENV_ACCOUNT_ID = "env"


def to_contract_account(stored: StoredAccount, active_id: Optional[str]) -> ContractAccount:
    return ContractAccount(
        id=stored.id,
        label=stored.label,
        main_address=stored.main_address,
        agent_address=stored.agent_address,
        mode=stored.mode,
        source=stored.source,
        is_active=(stored.id == active_id),
        created_at=stored.created_at,
        verified_at=stored.verified_at,
        agent_name=stored.agent_name,
    )


def synthesize_env_account(active_id: Optional[str]) -> Optional[ContractAccount]:
    """A read-only `source="env"` Account mirroring whatever
    `.env.local` currently resolves to, or None if there is nothing there.

    `active_id` here is the REGISTRY's own active_id (possibly None). The env
    account is marked active only when the registry has no account of its own
    marked active — i.e. it is the fallback identity, exactly as
    services.accounts.credentials treats it.
    """
    try:
        from pathiel.client.exchange import resolve_env_identity
    except ImportError:
        return None
    identity = resolve_env_identity()
    if identity is None:
        return None
    now = datetime.now(timezone.utc)
    return ContractAccount(
        id=ENV_ACCOUNT_ID,
        label="From .env.local",
        main_address=identity["main_address"],
        agent_address=identity["agent_address"],
        mode="trade",
        source="env",
        is_active=(active_id is None),
        created_at=now,
        verified_at=now,
        agent_name=None,
    )


def list_all_accounts() -> List[ContractAccount]:
    """Every account the UI should show: stored registry accounts (in
    creation order), plus the synthetic env account last, when one exists."""
    from services.accounts import registry

    active_id = registry.get_active_id()
    out = [to_contract_account(a, active_id) for a in registry.list_accounts()]
    env_account = synthesize_env_account(active_id)
    if env_account is not None:
        out.append(env_account)
    return out


def get_account_or_env(account_id: str) -> Optional[ContractAccount]:
    """A single account by id, including the synthetic "env" id."""
    from services.accounts import registry

    if account_id == ENV_ACCOUNT_ID:
        return synthesize_env_account(registry.get_active_id())
    stored = registry.get(account_id)
    if stored is None:
        return None
    return to_contract_account(stored, registry.get_active_id())


def get_effective_active_account() -> Optional[ContractAccount]:
    """Whichever account the dashboard should treat as "the active one" —
    a real registry account if one is marked active, else the synthetic env
    account if one exists, else None (needs_onboarding)."""
    from services.accounts import registry

    active_id = registry.get_active_id()
    if active_id is not None:
        stored = registry.get(active_id)
        if stored is not None:
            return to_contract_account(stored, active_id)
    return synthesize_env_account(active_id)
