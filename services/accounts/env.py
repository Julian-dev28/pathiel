"""Self-contained state-dir + .env.local resolution for services/accounts.

Deliberately a leaf module with zero imports from other `services/*` or
`pathiel/*` packages (see docs/overhaul/SWARM_RULES.md section 6 ownership —
this service owns `services/accounts/**` only and must not reach into another
service's internals). `services/intel/env.py` solves the identical problem
the identical way; the duplication is intentional, not an oversight, so each
service can be edited by a different agent in parallel without a shared-file
collision.

Call `state_dir()` to get the directory this service writes under.
`PATHIEL_STATE_DIR` always wins when set (every worktree in the swarm exports a
throwaway one per SWARM_RULES section 1); otherwise we fall back to
`.env.local` at the repo root, and finally to the repo root itself so an
ad-hoc `python -c` still works.

Everything here is a pure path computation re-read on every call (never cached
in a module-level constant) — that is what makes `PATHIEL_STATE_DIR` and
`PATHIEL_VAULT_PASSPHRASE` behave correctly under `monkeypatch.setenv` in
tests, and what lets a long-running process pick up a freshly-created state
dir without a restart.
"""
from __future__ import annotations

import os

_LOADED = False


def _load_dotenv() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, ".env.local")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def state_dir() -> str:
    """The directory this process writes services/accounts files under."""
    _load_dotenv()
    configured = os.environ.get("PATHIEL_STATE_DIR")
    if configured:
        return configured
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def accounts_dir() -> str:
    """`$PATHIEL_STATE_DIR/accounts/` — created on first write by registry.py
    or vault.py."""
    return os.path.join(state_dir(), "accounts")


def registry_path() -> str:
    """`$PATHIEL_STATE_DIR/accounts/accounts.json` — the only file registry.py
    writes. Public metadata only (labels, addresses, mode, source,
    timestamps): never a private key."""
    return os.path.join(accounts_dir(), "accounts.json")


def vault_dir() -> str:
    """`$PATHIEL_STATE_DIR/accounts/keys/` — one encrypted keystore JSON file
    per account id, chmod 600."""
    return os.path.join(accounts_dir(), "keys")


def vault_secret_path() -> str:
    """`$PATHIEL_STATE_DIR/accounts/.vault_secret` — the machine-local
    passphrase used when `PATHIEL_VAULT_PASSPHRASE` is not set. Created once,
    chmod 600, by services.accounts.vault."""
    return os.path.join(accounts_dir(), ".vault_secret")
