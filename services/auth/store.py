"""Users, sessions and login nonces. SQLite, no ORM.

WHY SQLITE AND NO ORM
---------------------
The rest of the system already keeps state in SQLite (`pathia_data.db`) and
JSONL. Adding SQLAlchemy or Postgres for three small tables buys migrations and
connection pooling this does not need yet, at the cost of a dependency on the
authentication path. When a second process needs concurrent writes, or the row
counts stop fitting one box, that is the moment to move — and the schema here is
plain enough to lift.

WHAT IS STORED, AND WHAT DELIBERATELY IS NOT
--------------------------------------------
Session tokens and nonces are stored as SHA-256 hashes, never as the value the
client holds. A database file that leaks — a backup on a laptop, a snapshot in
object storage, an errant `SELECT *` in a log — must not hand the reader a set
of live sessions. Hashing makes the stored row useless for impersonation.

SHA-256 and not bcrypt/argon2, on purpose: those exist to make *low-entropy
human passwords* expensive to brute-force. A 256-bit random token has no
guessable structure, so a slow KDF adds latency to every authenticated request
and buys nothing. There are no passwords in this system at all — the only
credential is a wallet signature.

Lookup is by the hash, so it is an indexed point query rather than a scan with a
constant-time compare on every row.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Sessions outlive a page refresh but not a forgotten laptop. Trading controls
# sit behind this, so the window is deliberately short of the "stay logged in
# forever" a content site would pick.
SESSION_TTL_S = 14 * 24 * 3600
# A nonce only has to survive the round trip to the wallet and back.
NONCE_TTL_S = 600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    address       TEXT    NOT NULL UNIQUE,   -- lowercase 0x…, the identity
    display_name  TEXT,
    email         TEXT,                      -- optional, for notifications only
    role          TEXT    NOT NULL DEFAULT 'user',   -- 'user' | 'operator'
    created_at    REAL    NOT NULL,
    last_seen_at  REAL    NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT    PRIMARY KEY,       -- sha256 of the cookie value
    user_id       INTEGER NOT NULL,
    created_at    REAL    NOT NULL,
    expires_at    REAL    NOT NULL,
    user_agent    TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS login_nonces (
    nonce_hash    TEXT    PRIMARY KEY,
    created_at    REAL    NOT NULL,
    expires_at    REAL    NOT NULL
);
"""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    address: str
    display_name: Optional[str]
    email: Optional[str]
    role: str
    created_at: float
    last_seen_at: float
    disabled: bool

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def public(self) -> Dict[str, Any]:
        """What the browser is allowed to see. Never the raw row."""
        return {
            "address": self.address,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "created_at": self.created_at,
        }


class AuthStore:
    """All reads and writes go through here. One connection per instance."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get(
            "PATHIA_AUTH_DB",
            os.path.join(os.environ.get("PATHIA_STATE_DIR", "."), "auth.db"))
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        # WAL: the API reads sessions on every request while the login path
        # writes. Without it a write blocks every concurrent read.
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ── nonces ──────────────────────────────────────────────────────────────

    def issue_nonce(self, now: Optional[float] = None) -> str:
        """Mint a single-use login nonce. Returns the value the client signs."""
        now = now if now is not None else time.time()
        nonce = secrets.token_urlsafe(24)
        self._db.execute(
            "INSERT INTO login_nonces (nonce_hash, created_at, expires_at) "
            "VALUES (?, ?, ?)", (_hash(nonce), now, now + NONCE_TTL_S))
        self._db.commit()
        return nonce

    def consume_nonce(self, nonce: str, now: Optional[float] = None) -> bool:
        """Burn a nonce. True only the first time, and only before it expires.

        DELETE ... RETURNING makes claim-and-check one statement, so two
        requests racing the same nonce cannot both win. A SELECT-then-DELETE
        would let a replay through whenever the two interleaved.
        """
        now = now if now is not None else time.time()
        cur = self._db.execute(
            "DELETE FROM login_nonces WHERE nonce_hash = ? RETURNING expires_at",
            (_hash(nonce),))
        row = cur.fetchone()
        self._db.commit()
        return bool(row) and float(row["expires_at"]) > now

    def purge_expired(self, now: Optional[float] = None) -> int:
        """Drop dead nonces and sessions. Cheap; call it on the login path."""
        now = now if now is not None else time.time()
        n = self._db.execute("DELETE FROM login_nonces WHERE expires_at <= ?",
                             (now,)).rowcount
        n += self._db.execute("DELETE FROM sessions WHERE expires_at <= ?",
                              (now,)).rowcount
        self._db.commit()
        return n

    # ── users ───────────────────────────────────────────────────────────────

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(id=row["id"], address=row["address"],
                    display_name=row["display_name"], email=row["email"],
                    role=row["role"], created_at=row["created_at"],
                    last_seen_at=row["last_seen_at"],
                    disabled=bool(row["disabled"]))

    def upsert_user(self, address: str, now: Optional[float] = None) -> User:
        """Find or create the user for a wallet address.

        Addresses are lowercased on the way in. EIP-55 checksummed and
        all-lowercase forms are the same account, and storing both would let one
        wallet hold two identities with different balances attached.
        """
        now = now if now is not None else time.time()
        addr = address.lower()
        # The first account to sign in owns the deployment. A fresh box with an
        # open operator role would let whoever finds the URL first claim the
        # kill switch; seeding it from the installer's own login closes that
        # window without a bootstrap password to leak.
        first = self._db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        self._db.execute(
            "INSERT INTO users (address, role, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET last_seen_at = excluded.last_seen_at",
            (addr, "operator" if first else "user", now, now))
        self._db.commit()
        row = self._db.execute("SELECT * FROM users WHERE address = ?", (addr,)).fetchone()
        return self._row_to_user(row)

    def get_user(self, user_id: int) -> Optional[User]:
        row = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_address(self, address: str) -> Optional[User]:
        row = self._db.execute("SELECT * FROM users WHERE address = ?",
                               (address.lower(),)).fetchone()
        return self._row_to_user(row) if row else None

    def update_profile(self, user_id: int, *, display_name: Optional[str] = None,
                       email: Optional[str] = None) -> Optional[User]:
        sets, args = [], []
        if display_name is not None:
            sets.append("display_name = ?"); args.append(display_name.strip() or None)
        if email is not None:
            sets.append("email = ?"); args.append(email.strip() or None)
        if not sets:
            return self.get_user(user_id)
        args.append(user_id)
        self._db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", args)
        self._db.commit()
        return self.get_user(user_id)

    def set_role(self, address: str, role: str) -> Optional[User]:
        if role not in ("user", "operator"):
            raise ValueError(f"unknown role: {role!r}")
        self._db.execute("UPDATE users SET role = ? WHERE address = ?",
                         (role, address.lower()))
        self._db.commit()
        return self.get_user_by_address(address)

    def list_users(self) -> List[User]:
        return [self._row_to_user(r) for r in
                self._db.execute("SELECT * FROM users ORDER BY created_at")]

    # ── sessions ────────────────────────────────────────────────────────────

    def create_session(self, user_id: int, *, user_agent: str = "",
                       now: Optional[float] = None) -> str:
        """Open a session. Returns the token; only its hash is persisted."""
        now = now if now is not None else time.time()
        token = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash(token), user_id, now, now + SESSION_TTL_S, user_agent[:200]))
        self._db.commit()
        return token

    def session_user(self, token: str, now: Optional[float] = None) -> Optional[User]:
        """The live user behind a session token, or None.

        Expiry is enforced in the query, and a disabled account resolves to None
        so that revoking access does not depend on also hunting down every
        session row the user holds.
        """
        now = now if now is not None else time.time()
        row = self._db.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (_hash(token), now)).fetchone()
        if not row:
            return None
        self._db.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
        self._db.commit()
        return self._row_to_user(row)

    def revoke_session(self, token: str) -> bool:
        cur = self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))
        self._db.commit()
        return cur.rowcount > 0

    def revoke_all_for_user(self, user_id: int) -> int:
        """Every session for one user. The "sign out everywhere" primitive, and
        what a compromise response needs."""
        cur = self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._db.commit()
        return cur.rowcount
