"""Shared by every services/pool test. Owned by the architect's commit:
units add fixtures in their own test files, never here.

Every test gets a throwaway state dir and a throwaway repo root, and the
wallet key variables pinned empty, so no test can read the live state, write
the tracked pool/ or docs/scoreboard/, or sign with a real key. The network
is already refused by the root conftest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

import pytest

import contracts.pool as pool


@pytest.fixture(autouse=True)
def pool_env(tmp_path, monkeypatch) -> Dict[str, Path]:
    """PATHIEL_STATE_DIR and PATHIEL_POOL_REPO_ROOT under tmp_path, with the
    repo-side folders created. Returns {'state': ..., 'repo': ...}."""
    state, repo = tmp_path / "state", tmp_path / "repo"
    state.mkdir()
    for sub in (f"{pool.POOL_DIR}/{pool.MEMBERS_SUBDIR}", f"{pool.POOL_DIR}/{pool.LEAVES_SUBDIR}",
                pool.SCOREBOARD_DIR):
        (repo / sub).mkdir(parents=True)
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(state))
    monkeypatch.setenv("PATHIEL_POOL_REPO_ROOT", str(repo))
    for key in ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY",
                "HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_MASTER_ADDRESS"):
        monkeypatch.setenv(key, "")
    return {"state": state, "repo": repo}


@pytest.fixture
def new_account() -> Callable[[], Any]:
    """A fresh throwaway eth_account account per call. Its key exists only
    in the test process."""
    from eth_account import Account
    return Account.create


@pytest.fixture
def signed() -> Callable[..., Dict[str, Any]]:
    """signed(kind, account, key_owner=None, at='2026-11-12T12:00:00Z') -> a
    statement file dict for `account`'s address, signed by `key_owner`'s key
    (the account's own when omitted)."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    def make(kind: str, account: Any, key_owner: Any = None, at: str = "2026-11-12T12:00:00Z") -> Dict[str, Any]:
        owner = key_owner or account
        address, signer = account.address.lower(), owner.address.lower()
        message = pool.statement_text(kind, address, signer, at)
        sig = Account.sign_message(encode_defunct(text=message), owner.key).signature.hex()
        return pool.build_statement_file(kind, address, signer, at, "0x" + sig.removeprefix("0x"))
    return make


@pytest.fixture
def put(pool_env) -> Callable[..., Path]:
    """put(doc, root=None) -> writes a statement file where its kind belongs
    (members/ or leaves/) under `root` (default: the test repo's pool/)."""
    def write(doc: Dict[str, Any], root: Any = None) -> Path:
        base = Path(root) if root else pool_env["repo"] / pool.POOL_DIR
        sub = pool.MEMBERS_SUBDIR if doc["kind"] == "join" else pool.LEAVES_SUBDIR
        (base / sub).mkdir(parents=True, exist_ok=True)
        path = base / sub / pool.statement_filename(doc["address"])
        path.write_text(pool.dump_statement_file(doc), encoding="ascii")
        return path
    return write
