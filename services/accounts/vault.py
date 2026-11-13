"""Encrypted-at-rest storage for agent private keys.

Keys are stored as eth_account keystore JSON (`Account.encrypt` /
`Account.decrypt` — the same format geth/parity use), one file per account id,
under `services.accounts.env.vault_dir()`. The password is
`PATHIEL_VAULT_PASSPHRASE` if set, else a machine-local random secret
generated once at `env.vault_secret_path()`.

Hard rules (SWARM_RULES docs/overhaul/SWARM_RULES.md section 10 + the task
brief): a key is never logged, never returned by any route, and this module
is the ONLY place a raw private key is ever written to or read from disk.
Every write is chmod 600 and atomic (write to a temp file, then `os.replace`,
so a crash mid-write can never leave a half-written or world-readable key
file behind).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from eth_account import Account

from services.accounts import env as accounts_env

logger = logging.getLogger(__name__)

_PASSPHRASE_ENV = "PATHIEL_VAULT_PASSPHRASE"

# Tests only. eth_account's own default KDF (scrypt, N=262144) is the right
# choice for a real secret — it costs ~1.2s per encrypt, which is the point.
# services/accounts/tests exercises encrypt/decrypt dozens of times across the
# suite and must stay inside the repo's gate-test time budget, so its conftest
# sets this to swap in a cheap pbkdf2/1000 — still a real password-based KDF
# round trip through Account.encrypt/decrypt, just not one tuned to resist
# brute force. Nothing in production code ever sets this var.
_FAST_KDF_ENV = "PATHIEL_VAULT_FAST_KDF"

_FILE_MODE = 0o600


def _kdf_kwargs() -> dict:
    if os.environ.get(_FAST_KDF_ENV):
        return {"kdf": "pbkdf2", "iterations": 1000}
    return {}


def _chmod_600(path: str) -> None:
    os.chmod(path, _FILE_MODE)


def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    _chmod_600(tmp_path)
    os.replace(tmp_path, path)  # atomic on the same filesystem


def _machine_secret() -> str:
    """A random passphrase generated once per state dir and reused after
    that, so a key encrypted today can still be decrypted after a restart
    with no operator action — while never being committed, logged, or
    derived from anything guessable."""
    path = accounts_env.vault_secret_path()
    if os.path.exists(path):
        return Path(path).read_text().strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    secret = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    except FileExistsError:
        # Lost a create race to another process — read what it wrote.
        return Path(path).read_text().strip()
    with os.fdopen(fd, "w") as fh:
        fh.write(secret)
    _chmod_600(path)
    return secret


def _passphrase() -> str:
    explicit = os.environ.get(_PASSPHRASE_ENV)
    if explicit:
        return explicit
    return _machine_secret()


def key_file_path(account_id: str) -> str:
    return os.path.join(accounts_env.vault_dir(), f"{account_id}.json")


def store_key(account_id: str, private_key_hex: str) -> None:
    """Encrypt `private_key_hex` and write it to this account's key file.
    Overwrites any existing key file for this id (used when re-approving an
    agent for an account that already exists)."""
    keystore = Account.encrypt(private_key_hex, _passphrase(), **_kdf_kwargs())
    _atomic_write(key_file_path(account_id), json.dumps(keystore))


def load_key(account_id: str) -> Optional[str]:
    """Decrypt and return the raw `0x`-prefixed private key hex for
    `account_id`, or None if no key file exists for it. Raises whatever
    eth_account raises (e.g. a wrong passphrase) rather than swallowing it
    into "no key" — a decrypt failure is a real problem, not an absence."""
    path = key_file_path(account_id)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        keystore = json.load(fh)
    raw = Account.decrypt(keystore, _passphrase())
    return "0x" + raw.hex()


def delete_key(account_id: str) -> None:
    """Best-effort remove. Never raises if the key was already gone."""
    try:
        os.remove(key_file_path(account_id))
    except FileNotFoundError:
        pass


def key_file_mode(account_id: str) -> Optional[int]:
    """The key file's permission bits (for tests asserting chmod 600), or
    None if it does not exist."""
    path = key_file_path(account_id)
    if not os.path.exists(path):
        return None
    return stat.S_IMODE(os.stat(path).st_mode)
