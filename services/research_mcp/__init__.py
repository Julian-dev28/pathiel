"""pathiel research MCP: a read-only, keyless MCP server that serves
pathiel's deterministic market research (levels, scheduled catalysts,
headlines, prediction-market odds, FRED series, and the latest stored desk
thesis) to any MCP client.

Hard boundary (see tests/test_no_trading_imports.py, the gate that proves
it): nothing here imports order-placement or signing code
(pathiel.agents.bracket, pathiel.client.exchange, services.strategies.
next_trade, the trading MCP's own scripts/pathiel-mcp-server.py), and
nothing here opens .env.local or ~/.pathiel. It imports the rest of pathiel
(services/intel, services/strategies, services/thesis, pathiel.client.
hl_client) only through their public functions, never their private (`_`)
internals. See README.md for the tool list and the install command.
"""
from __future__ import annotations
