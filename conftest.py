"""Pytest bootstrap — load .env.local before the test session starts.

hermes_trader.client.exchange reads Hyperliquid credentials at import time, so
the environment must be populated before any test imports the package. This is
a no-op when .env.local is absent (e.g. CI without secrets).
"""
import os
import pathlib

_ENV_FILE = pathlib.Path(__file__).parent / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
