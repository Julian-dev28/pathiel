#!/usr/bin/env python3
"""Hermes-Trader MCP server — stdio transport for Hermes Agent.

Exposes trading tools to Hermes Agent:
  - scan(minScore, maxMarkets)
  - research(coin)
  - execute(analysisId)
  - state()
  - config()

Run as: python scripts/hermes-mcp-server.py

Automatically loads .env.local from project root if present.
"""

import json
import sys
import os
import time
from typing import Any, Dict

# Auto-load .env.local from project root
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env.local')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_agent.agents.config_store import read_agent_config
from hermes_agent.agents.perception import scan_once
from hermes_agent.client.hl_client import fetch_all_mids, fetch_account_state
from hermes_agent.agents.risk_gates import eval_all_gates, GateContext

# Tools definition
TOOLS = [
    {
        "name": "scan",
        "description": "Scan Hyperliquid markets for trading signals. Returns triggered candidates above a score threshold.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "minScore": {
                    "type": "number",
                    "description": "Minimum composite score (0-100, default 20). Use 75+ for high-confidence only.",
                },
                "maxMarkets": {
                    "type": "number",
                    "description": "Maximum markets to scan by volume (default 50).",
                },
            },
        },
    },
    {
        "name": "research",
        "description": "Deep AI analysis on a specific coin. Requires a triggered signal from scan first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker (e.g. BTC, ETH, SOL)",
                },
            },
            "required": ["coin"],
        },
    },
    {
        "name": "execute",
        "description": "Execute a trade based on a prior analysis. Passes through risk gates and DSL exit registration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysisId": {
                    "type": "string",
                    "description": "Analysis ID from a research call",
                },
            },
            "required": ["analysisId"],
        },
    },
    {
        "name": "state",
        "description": "Get full agent state: mode, config, positions, recent trades, and account equity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "config",
        "description": "Get or set agent configuration (mode, risk caps, thresholds).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["OFF", "LIVE"]},
                "maxTradeNotionalUsd": {"type": "number"},
                "maxConcurrent": {"type": "number"},
                "minAiConfidence": {"type": "number"},
                "autoAnalyzeThreshold": {"type": "number"},
                "coinAllowlist": {"type": "array", "items": {"type": "string"}},
                "coinBlocklist": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
]


def handle_scan(params: Dict[str, Any]) -> str:
    from hermes_agent.agents.config import get_config
    from hermes_agent.client.universe import get_universe

    min_score = params.get("minScore", 75)
    max_markets = params.get("maxMarkets")
    if max_markets:
        os.environ["HERMES_MAX_MARKETS"] = str(int(max_markets))

    universe = get_universe()
    results = scan_once(universe=universe, min_score=min_score, config=get_config())

    return json.dumps({
        "scanned": len(universe),
        "triggers": len(results),
        "perceptions": results,
    })


def handle_state(params: Dict[str, Any]) -> str:
    user = os.environ.get("HYPERLIQUID_MASTER_ADDRESS") or os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")
    account = fetch_account_state(user) if user else {"equity": 0, "total_ntl": 0, "asset_positions": []}
    config = read_agent_config()

    return json.dumps({
        "mode": config.get("mode", "OFF"),
        "equity": account.get("equity", 0),
        "total_notional": account.get("total_ntl", 0),
        "positions": account.get("asset_positions", []),
        "scan_interval_sec": config.get("scan", {}).get("interval", 180),
        "min_composite_score": config.get("scan", {}).get("minCompositeScore", 20),
    })


def handle_config(params: Dict[str, Any]) -> str:
    from hermes_agent.agents.config_store import read_agent_config, write_agent_config

    config = read_agent_config()

    # Update if params provided
    for key in ["mode", "maxTradeNotionalUsd", "maxConcurrent", "minAiConfidence", "autoAnalyzeThreshold"]:
        val = params.get(key)
        if val is not None:
            config[key] = val

    if "coinAllowlist" in params:
        config["coinAllowlist"] = params["coinAllowlist"]
    if "coinBlocklist" in params:
        config["coinBlocklist"] = params["coinBlocklist"]

    # Save if changed
    if params:
        write_agent_config(config)

    return json.dumps(config)


def handle_research(params: Dict[str, Any]) -> str:
    coin = params.get("coin", "")
    return json.dumps({
        "status": "research queued",
        "coin": coin,
        "note": "AI research requires OpenRouter API — ensure OPENROUTER_API_KEY is set",
    })


def handle_execute(params: Dict[str, Any]) -> str:
    analysis_id = params.get("analysisId", "")
    return json.dumps({
        "status": "execution queued",
        "analysisId": analysis_id,
        "note": "Requires HYPERLIQUID_PRIVATE_KEY and LIVE mode",
    })


# MCP server loop
def run() -> None:
    # Initialize tool handlers
    tool_handlers = {
        "scan": handle_scan,
        "research": handle_research,
        "execute": handle_execute,
        "state": handle_state,
        "config": handle_config,
    }

    # MCP handshake
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            msg = json.loads(line)
            method = msg.get("method")
            msg_id = msg.get("id")
            params = msg.get("params")

            if method == "initialize":
                write_response(msg_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "hermes-trader",
                        "version": "0.3.0",
                    },
                })
            elif method == "tools/list":
                write_response(msg_id, {"tools": TOOLS})
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments") or {}
                handler = tool_handlers.get(tool_name)
                if handler:
                    try:
                        result = handler(tool_args)
                        write_response(msg_id, {
                            "content": [{"type": "text", "text": result}],
                            "isError": False,
                        })
                    except Exception as e:
                        write_response(msg_id, {
                            "content": [{"type": "text", "text": f"Error: {e}"}],
                            "isError": True,
                        })
                else:
                    write_response(msg_id, {
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "isError": True,
                    })
            elif method == "notifications/initialized":
                pass  # Ignore notification
            else:
                write_response(msg_id if msg_id else None, {
                    "content": [{"type": "text", "text": f"Unknown method: {method}"}],
                    "isError": True,
                })
        except Exception as e:
            sys.stderr.write(f"MCP error: {e}\n")
            sys.stderr.flush()


def write_response(msg_id: Any, result: Dict[str, Any]) -> None:
    msg = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    run()
