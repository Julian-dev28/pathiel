"""Which account this install speaks for. An address only: no key is read
here (signing lives in the join unit and asks for the key itself)."""
from __future__ import annotations

import contracts.pool as pool


def configured_account() -> str:
    """The account this install trades or reads, as 0x + 40 lowercase hex:
    pathiel.client.hl_client.resolve_user_address() (the active trading
    account, else HYPERLIQUID_MASTER_ADDRESS, else HYPERLIQUID_WALLET_ADDRESS).
    '' when none is configured or the value is not an address. Never raises."""
    try:
        from pathiel.client.hl_client import resolve_user_address
        return pool.normalize_address(resolve_user_address())
    except Exception:   # noqa: BLE001 - no account is an answer, not an error
        return ""
