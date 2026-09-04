#!/usr/bin/env python3
"""Grant or revoke the operator role for a wallet.

WHY THIS EXISTS
---------------
The first wallet to sign in on a fresh deployment claims operator, which closes
the open-kill-switch window without a bootstrap password to leak. The failure
mode is the obvious one: if anything else signs in first — a smoke check, a test
wallet, a curious visitor — the real operator is locked out of the house account
with no way back except deleting the database.

    python scripts/grant_operator.py --list
    python scripts/grant_operator.py 0xYourWallet
    python scripts/grant_operator.py 0xSomeoneElse --revoke

Reads PATHIA_STATE_DIR the same way the server does, so it edits the database
the running server is actually using.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.auth.store import AuthStore   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="grant_operator")
    ap.add_argument("address", nargs="?", help="0x wallet address")
    ap.add_argument("--revoke", action="store_true", help="demote to plain user")
    ap.add_argument("--list", action="store_true", help="show every account")
    a = ap.parse_args(argv)

    store = AuthStore()
    print(f"auth database: {store.path}\n")

    if a.list or not a.address:
        users = store.list_users()
        if not users:
            print("  no accounts yet — the first wallet to sign in becomes operator")
        for u in users:
            print(f"  {u.address}  role={u.role}"
                  f"{'  DISABLED' if u.disabled else ''}")
        return 0

    role = "user" if a.revoke else "operator"
    if store.get_user_by_address(a.address) is None:
        # Created rather than refused: granting before the first sign-in is the
        # normal way to set up a box, and requiring the person to log in once
        # (as a plain user) before they can be made operator is a worse dance.
        store.upsert_user(a.address)
    updated = store.set_role(a.address, role)
    if updated is None:
        print(f"  no such account: {a.address}")
        return 1
    print(f"  {updated.address} is now {updated.role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
