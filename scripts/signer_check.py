"""Is the API wallet that signs pathiel's orders still approved? Read only.

    python3 scripts/signer_check.py

One JSON line: ok, signer address, approval name, days left, reason. Exit 0
when orders will be accepted, 1 when every order will be refused, 2 when
Hyperliquid could not be read. Never prints the key
(services/accounts/signer_status.py derives the address in memory).
"""
from __future__ import annotations

import json
import os
import sys

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))
sys.path.insert(0, _SCRIPTS)


def main() -> int:
    import _state_env
    _state_env.load_env_local(os.path.dirname(_SCRIPTS))
    from services.accounts import signer_status
    v = signer_status.live(max_age_s=0)
    print(json.dumps(v))
    return 0 if v.get("ok") else (2 if v.get("ok") is None else 1)


if __name__ == "__main__":
    raise SystemExit(main())
