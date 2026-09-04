"""API keys, owned by the wallet that minted them.

THE CONTRACT, AND WHY IT IS SQL AND NOT AN IMPORT
--------------------------------------------------
`services/pathia_data_api` authenticates every request against an `api_keys`
table. It is its own deploy unit with its own Dockerfile and its own
requirements, none of which are installed in the trading image — so importing
its SQLAlchemy models from here would fail at runtime even if the source were
copied in. `test_dockerfile_does_not_bundle_pathia_data_api` exists to keep that
boundary, and it caught exactly this mistake on the first attempt.

So the contract between the two services is the thing they genuinely share: the
table, its column names, and the rule that a token is looked up by
`sha256(raw).hexdigest()`. This module speaks to it in plain SQL, the same way
`services/auth/store.py` speaks to the session tables. Neither service imports
the other.

That contract is asserted, not assumed:
`test_the_two_services_still_hash_a_token_the_same_way` checks both sides agree,
because if they ever drift a customer mints a key that opens nothing and neither
service logs a thing.

WHAT THE TABLE GAINED
---------------------
One column, `owner_address`. Without it a key belonged to the deployment rather
than a person, so nothing could answer "which keys are mine", "revoke that one",
or "this customer stopped paying".

WHY THE RAW KEY IS SHOWN EXACTLY ONCE
-------------------------------------
Only the hash is stored, so nobody, us included, can recover a key after
minting. A leaked database yields no usable credential. It also means "show me
my key again" is not a feature that can exist, and the caller has to say so at
the moment of minting rather than leaving it to be discovered.

Keys are prefixed `pk_live_` so one in a log, a paste or a public repository is
recognisable at a glance and greppable by a secret scanner.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

KEY_PREFIX = "pk_live_"
# What a fresh key may do. Deliberately not "*": the demo seed key carries a
# wildcard, and a customer key inheriting it would hand out every scope this API
# ever grows, including ones added years from now.
DEFAULT_SCOPES = ("signals:read", "candles:read", "track_record:read")
DEFAULT_RATE_PER_MIN = 120
MAX_KEYS_PER_OWNER = 10

# Mirrors services/pathia_data_api/app/db.py:ApiKey. Written out rather than
# imported for the reason in the module docstring. CREATE TABLE IF NOT EXISTS,
# so whichever service starts first wins and the other is a no-op.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id        VARCHAR(64) NOT NULL UNIQUE,
    token_hash    VARCHAR(64) NOT NULL UNIQUE,
    plan          VARCHAR(32) NOT NULL DEFAULT 'standard',
    scopes        JSON        NOT NULL,
    rate_per_min  INTEGER     NOT NULL DEFAULT 500,
    active        BOOLEAN     NOT NULL DEFAULT 1,
    created_at    DATETIME    NOT NULL,
    owner_address VARCHAR(64)
);
"""
# Deliberately NOT part of _SCHEMA. Against a table that predates ownership the
# index references a column that does not exist yet, and CREATE INDEX fails —
# which would crash ensure_schema on precisely the old deployment the ALTER
# below exists to upgrade. Index after the column, never before.
_OWNER_INDEX = "CREATE INDEX IF NOT EXISTS ix_api_keys_owner ON api_keys(owner_address)"


def db_path() -> str:
    """Where the data API keeps its SQLite file.

    Defaults to the same `./pathia_data.db` its settings default to, so a
    single-box deployment needs no configuration to line the two up.
    """
    url = os.environ.get("PATHIA_DATABASE_URL", "sqlite:///./pathia_data.db")
    return url.split("sqlite:///", 1)[-1] if url.startswith("sqlite:///") else url


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def hash_token(raw: str) -> str:
    """Must match services/pathia_data_api/app/auth.hash_token, or a minted key
    authenticates against nothing."""
    return hashlib.sha256(raw.encode()).hexdigest()


def ensure_schema() -> None:
    """Create the table if absent, and add `owner_address` if it predates it.

    The ALTER is the honest minimum, not a migration story: SQLAlchemy's
    create_all builds missing tables and never alters an existing one, so a
    deployment already holding pathia_data.db would keep a table with no owner
    column and every ownership query would raise. Additive and idempotent, so
    running it on each request path is free. A real migration tool is still owed
    and is still a P1 on the readiness review.
    """
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        if "owner_address" not in cols:
            conn.execute("ALTER TABLE api_keys ADD COLUMN owner_address VARCHAR(64)")
        conn.execute(_OWNER_INDEX)


@dataclass(frozen=True)
class MintedKey:
    """The one and only time the raw token exists outside the caller's hands."""
    key_id: str
    token: str
    label: Optional[str]


def _public(row: sqlite3.Row) -> Dict[str, Any]:
    """What the owner may see. Never token_hash: not secret enough to be worth
    showing, not useless enough to be safe to show."""
    try:
        scopes = json.loads(row["scopes"]) if row["scopes"] else []
    except (TypeError, ValueError):
        scopes = []
    return {
        "key_id": row["key_id"],
        "plan": row["plan"],
        "scopes": scopes,
        "rate_per_min": int(row["rate_per_min"]),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def mint(owner_address: str, *, label: Optional[str] = None,
         plan: str = "free", scopes=DEFAULT_SCOPES,
         rate_per_min: int = DEFAULT_RATE_PER_MIN) -> MintedKey:
    """Create a key for one wallet. The raw token is returned and then gone."""
    ensure_schema()
    owner = owner_address.lower()
    if count_for(owner) >= MAX_KEYS_PER_OWNER:
        raise ValueError(f"at most {MAX_KEYS_PER_OWNER} keys per account")

    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    digest = hash_token(raw)
    key_id = f"key_{digest[:16]}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (key_id, token_hash, plan, scopes, rate_per_min, "
            "active, created_at, owner_address) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (key_id, digest, plan, json.dumps(list(scopes)), int(rate_per_min),
             time.strftime("%Y-%m-%d %H:%M:%S"), owner))
    return MintedKey(key_id=key_id, token=raw, label=label)


def list_for(owner_address: str) -> List[Dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        return [_public(r) for r in conn.execute(
            "SELECT * FROM api_keys WHERE owner_address = ? ORDER BY id DESC",
            (owner_address.lower(),))]


def count_for(owner_address: str) -> int:
    ensure_schema()
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM api_keys WHERE owner_address = ?",
                            (owner_address.lower(),)).fetchone()["n"]


def revoke(owner_address: str, key_id: str) -> bool:
    """Deactivate one key.

    Ownership is in the WHERE clause, not a Python check after the row is
    loaded: a test that runs post-fetch is one early return away from being
    skipped, and this is the query that decides whether one customer can turn
    off another's access.
    """
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET active = 0 WHERE key_id = ? AND owner_address = ?",
            (key_id, owner_address.lower()))
        return cur.rowcount > 0
