"""Load `.env.local` before anything reads state paths.

`pathia.agents.rebalancer_owned` freezes `_STATE_DIR` at import time
from `PATHIA_STATE_DIR`, and that variable lives in `.env.local`. A CLI entry
point that skips this file silently reads a DIFFERENT shadow-ledger directory
than the running bot writes — which is exactly how the recorders lane first
reported "2 books, 4 signals" against a live tree holding 28 books. Wrong
directory, right code, no error anywhere.

Call `load()` at the top of every entry point, before importing anything from
`pathia`. `os.environ.setdefault` so a real environment variable always
outranks the file, matching `pathia/server.py`.
"""
from __future__ import annotations

import os
from typing import Optional

_LOADED = False


def load(path: Optional[str] = None) -> bool:
    """Merge `.env.local` into os.environ. Idempotent; True if a file was read."""
    global _LOADED
    if _LOADED and path is None:
        return True
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in ([path] if path else [".env.local", os.path.join(root, ".env.local")]):
        if p and os.path.exists(p):
            with open(p) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
            _LOADED = True
            return True
    _LOADED = True
    return False


def state_dir() -> str:
    """The state directory this process will actually use (after `load()`)."""
    load()
    return os.environ.get("PATHIA_STATE_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
