"""Signing and identity for the research pool: produce an EIP-191 signature
with the configured key or a key typed at a hidden prompt, and say which
account and signer a key speaks for.

The private key is read inside one function, used for exactly one signature,
and dropped (`del` / reassigned to None) before the function returns. No
function here returns, logs, prints or writes the key. The hidden prompt
copies the pattern of scripts/approve_builder_fee.py (not an import: that
script is the builder-fee flow, this is the pool's own).
"""
from __future__ import annotations

import sys
from typing import Callable, Optional, Tuple

import contracts.pool as pool

HIDDEN_PROMPT = "Hyperliquid master private key (hidden, not stored): "


class NoKey(RuntimeError):
    """No usable signing key is configured, or none was typed at the prompt."""


def sign_message(message: str, private_key: str) -> str:
    """EIP-191 personal-message signature over `message`: '0x' + 130 lowercase
    hex (r, s, v). Must round-trip through contracts.pool.recover_signer."""
    from eth_account import Account
    from eth_account.messages import encode_defunct
    raw = Account.sign_message(encode_defunct(text=message), private_key).signature.hex()
    return "0x" + raw.removeprefix("0x").lower()


def _signer_of(private_key: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key).address.lower()


def identity() -> Tuple[str, str]:
    """(account, signer) for the configured key, lowercase.

    Lazy import of pathiel.client.exchange._resolve_identity(), which returns
    (private_key_hex, main_address, agent_address, is_agent): the identity
    that actually signs the next order, exactly as services/accounts/
    signer_status.py reads it. signer = Account.from_key(key).address.lower().
    account = main_address.lower() or signer (an operator install with no
    separate main address signs for itself).

    Any RuntimeError from _resolve_identity (no key configured) or an empty
    key raises NoKey. The key is read, used once to derive the signer
    address, and dropped; it is never returned."""
    from pathiel.client.exchange import _resolve_identity
    try:
        key, main_address, _agent_address, _is_agent = _resolve_identity()
    except RuntimeError as e:
        raise NoKey(str(e)) from e
    if not key:
        raise NoKey("no signing key is configured")
    try:
        signer = _signer_of(key)
    finally:
        key = None
    account = str(main_address or "").strip().lower() or signer
    return account, signer


def sign_configured(message: str) -> Tuple[str, str]:
    """(signer, signature): sign `message` with the key services.pool reads
    for this install (the same key `identity()` derives from). The key is
    read inside this one function, used for one signature, and dropped.
    NoKey exactly as `identity()` raises it."""
    from pathiel.client.exchange import _resolve_identity
    try:
        key, _main_address, _agent_address, _is_agent = _resolve_identity()
    except RuntimeError as e:
        raise NoKey(str(e)) from e
    if not key:
        raise NoKey("no signing key is configured")
    try:
        signer = _signer_of(key)
        signature = sign_message(message, key)
    finally:
        key = None
    return signer, signature


def _read_hidden_key(getpass_fn: Callable[[str], str]) -> str:
    try:
        return (getpass_fn(HIDDEN_PROMPT) or "").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def sign_prompted(message: str, account: str, *, getpass_fn: Optional[Callable[[str], str]] = None,
                  isatty: Optional[Callable[[], bool]] = None) -> Tuple[str, str]:
    """(signer, signature): prompt for the account's MASTER key at a hidden
    terminal prompt and sign `message` with it. `sign_prompted` is for the
    master role only (an agent key is never worth typing at a prompt, since
    it is the account's own configured key already): the recovered signer
    must equal `account`, lowercase.

    PoolError('no_tty') when stdin is not a terminal, or nothing was typed,
    or the typed text is not a private key. PoolError('wrong_key') when the
    typed key's address is not `account`. Never reads
    HYPERLIQUID_MASTER_PRIVATE_KEY or any other environment variable, and
    never echoes the input (getpass). Never calls print."""
    isatty_fn = isatty or (lambda: sys.stdin.isatty())
    if not isatty_fn():
        raise pool.PoolError("no_tty", "no terminal to read a key from: run the master-prompt "
                             "command in your own terminal, not through a pipe or a script")
    if getpass_fn is None:
        import getpass as _getpass_module
        getpass_fn = _getpass_module.getpass
    key = _read_hidden_key(getpass_fn)
    if not key:
        raise pool.PoolError("no_tty", "nothing was typed at the key prompt")
    try:
        try:
            signer = _signer_of(key)
        except Exception as e:   # noqa: BLE001 - any failure to parse is "that was not a key"
            raise pool.PoolError("wrong_key", "that is not a valid private key") from e
        signature = sign_message(message, key)
    finally:
        key = None
    wanted = pool.normalize_address(account)
    if signer != wanted:
        raise pool.PoolError("wrong_key", f"that key signs for {pool.short_address(signer)}, not "
                             f"{pool.short_address(wanted)}: use the account's own master key, not "
                             "an API wallet key")
    return signer, signature
