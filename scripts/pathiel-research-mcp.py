#!/usr/bin/env python3
"""pathiel research MCP -- entrypoint.

A separate process and a separate entrypoint from the trading server
(scripts/pathiel-mcp-server.py): this script does nothing but put the repo
root on sys.path and hand off to services/research_mcp/server.py, which
holds every tool and the transport. It imports NOTHING else -- no env
loader, no .env.local, no ~/.pathiel, no account or order code. See
services/research_mcp/README.md for the tool list and the install command,
and services/research_mcp/tests/test_no_trading_imports.py for the gate
that proves this whole import graph stays clean.

Run as: .venv/bin/python scripts/pathiel-research-mcp.py (a checkout), or
through scripts/pathiel-research-mcp.sh (the Claude Code plugin launcher,
which builds its own venv and runs this same file).
"""
from __future__ import annotations

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.research_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
    sys.stderr.flush()
    os._exit(0)
