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
from hermes_agent.agents.hyperfeed import (
    leaderboard_get_markets,
    leaderboard_get_top as leaderboard_get_top_traders,
    leaderboard_get_trader_positions,
    discovery_get_top_traders,
    discovery_get_trader_state,
    market_get_asset_data,
    market_get_funding_regime,
    market_list_instruments,
    market_get_mids,
)

# Per-subprocess perception cache so research can access the data from last scan
_perception_cache: Dict[str, Dict[str, Any]] = {}

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
    {
        "name": "leaderboard_get_markets",
        "description": "Get Hyperliquid SM leaderboard market rankings by volume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "Max markets to return (default 100)"}
            }
        }
    },
    {
        "name": "leaderboard_get_top_traders",
        "description": "Get top traders from Hyperliquid leaderboard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_frame": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"], "default": "DAILY"},
                "sort_by": {"type": "string", "enum": ["PROFIT_AND_LOSS_UNREALIZED", "RETURN_ON_INVESTMENT"], "default": "PROFIT_AND_LOSS_UNREALIZED"},
                "limit": {"type": "number", "description": "Max traders to return (default 10)"},
                "open_position_filter": {"type": "boolean", "default": True}
            }
        }
    },
    {
        "name": "leaderboard_get_trader_positions",
        "description": "Get open positions for a specific trader address.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trader_id": {"type": "string", "description": "Trader wallet address"}
            },
            "required": ["trader_id"]
        }
    },
    {
        "name": "discovery_get_top_traders",
        "description": "Get top traders sorted by performance metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_frame": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"], "default": "MONTHLY"},
                "sort_by": {"type": "string", "enum": ["RETURN_ON_INVESTMENT", "PROFIT_AND_LOSS_UNREALIZED"], "default": "RETURN_ON_INVESTMENT"},
                "limit": {"type": "number", "description": "Max traders to return (default 60)"},
                "open_position_filter": {"type": "boolean", "default": True}
            }
        }
    },
    {
        "name": "discovery_get_trader_state",
        "description": "Get comprehensive state for multiple traders.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "trader_addresses": {"type": "array", "items": {"type": "string"}, "description": "List of trader wallet addresses"}
            },
            "required": ["trader_addresses"]
        }
    },
    {
        "name": "market_get_asset_data",
        "description": "Get comprehensive asset data: candles + funding + OI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Coin ticker (e.g. BTC)"},
                "intervals": {"type": "array", "items": {"type": "string"}, "description": "Candle intervals (default [\"5m\",\"15m\",\"1h\",\"4h\"])"}
            },
            "required": ["asset"]
        }
    },
    {
        "name": "market_get_funding_regime",
        "description": "Get market-wide funding regime analysis (crowded trades).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "market_list_instruments",
        "description": "List all tradable instruments (perps + spot).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "market_get_mids",
        "description": "Get all current mid prices for all assets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
]


def handle_scan(params: Dict[str, Any]) -> str:
    from hermes_agent.agents.config import get_config
    from hermes_agent.client.universe import get_universe

    min_score = params.get("minScore", 20)
    max_markets = params.get("maxMarkets")
    if max_markets:
        os.environ["HERMES_MAX_MARKETS"] = str(int(max_markets))

    universe = get_universe()
    results = scan_once(universe=universe, min_score=min_score, config=get_config())

    # Cache perceptions by coin so research can look them up
    for r in results:
        coin = r.get("coin", "")
        if coin:
            _perception_cache[coin] = r

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
    from hermes_agent.agents.research import research
    
    coin = params.get("coin", "")
    if not coin:
        return json.dumps({"status": "error", "error": "coin is required"})
    
    # Find matching perception from last scan
    perception = _perception_cache.get(coin)
    if not perception:
        # Build minimal perception from current mid price
        try:
            from hermes_agent.client.hl_client import fetch_all_mids
            mids = fetch_all_mids()
            for m in mids:
                if m.get("coin") == coin:
                    perception = {
                        "id": f"{coin}-{int(time.time()*1000)}",
                        "coin": coin,
                        "type": "perp",
                        "mid": float(m.get("mid", 0)),
                        "triggers": [],
                        "composite_score": 0,
                    }
                    break
        except Exception:
            perception = {
                "id": f"{coin}-{int(time.time()*1000)}",
                "coin": coin,
                "type": "perp",
                "mid": 0,
                "triggers": [],
                "composite_score": 0,
            }
    
    try:
        analysis = research(coin, perception)
        return json.dumps({
            "status": "complete",
            "analysisId": analysis["id"],
            "coin": coin,
            "verdict": analysis["verdict"],
            "confidence": analysis["confidence"],
            "side": analysis["side"],
            "entryPx": analysis["entry_px"],
            "stopPx": analysis["stop_px"],
            "tpPx": analysis["tp_px"],
            "reasoning": analysis["reasoning"],
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "coin": coin,
            "error": str(e),
        })


def handle_execute(params: Dict[str, Any]) -> str:
    from hermes_agent.agents.executor import maybe_execute
    from hermes_agent.agents.memory import memory

    analysis_id = params.get("analysisId", "")
    if not analysis_id:
        return json.dumps({"status": "error", "error": "analysisId is required"})

    # Find the analysis in memory
    analyses = memory.get_recent_analyses(20)
    analysis = None
    for a in analyses:
        if a.get("id") == analysis_id:
            analysis = a
            break

    if not analysis:
        return json.dumps({
            "status": "error",
            "error": f"Analysis {analysis_id} not found",
        })

    if analysis.get("verdict") not in ("LONG", "SHORT"):
        return json.dumps({
            "status": "skipped",
            "coin": analysis.get("coin"),
            "verdict": analysis.get("verdict"),
            "reason": f"Verdict {analysis.get('verdict')} — no trade action needed",
        })

    try:
        result = maybe_execute(analysis)
        return json.dumps(result)
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        # Write to file for debugging
        with open('/tmp/hermes_execute_error.log', 'w') as f:
            f.write(f"Exception: {e}\n\nTraceback:\n{error_detail}\n\nAnalysis dict:\n{json.dumps(analysis, indent=2)}")
        return json.dumps({
            "status": "error",
            "coin": analysis.get("coin"),
            "error": str(e),
        })


def handle_leaderboard_get_markets(params: Dict[str, Any]) -> str:
    limit = params.get("limit", 100)
    return json.dumps(leaderboard_get_markets(limit=limit))


def handle_leaderboard_get_top_traders(params: Dict[str, Any]) -> str:
    return json.dumps(leaderboard_get_top_traders(
        time_frame=params.get("time_frame", "DAILY"),
        sort_by=params.get("sort_by", "PROFIT_AND_LOSS_UNREALIZED"),
        limit=params.get("limit", 10),
        open_position_filter=params.get("open_position_filter", True)
    ))


def handle_leaderboard_get_trader_positions(params: Dict[str, Any]) -> str:
    trader_id = params.get("trader_id", "")
    if not trader_id:
        return json.dumps({"status": "error", "error": "trader_id required"})
    return json.dumps(leaderboard_get_trader_positions(trader_id=trader_id))


def handle_discovery_get_top_traders(params: Dict[str, Any]) -> str:
    return json.dumps(discovery_get_top_traders(
        time_frame=params.get("time_frame", "MONTHLY"),
        sort_by=params.get("sort_by", "RETURN_ON_INVESTMENT"),
        limit=params.get("limit", 60),
        open_position_filter=params.get("open_position_filter", True)
    ))


def handle_discovery_get_trader_state(params: Dict[str, Any]) -> str:
    addresses = params.get("trader_addresses", [])
    if not addresses:
        return json.dumps({"status": "error", "error": "trader_addresses required"})
    return json.dumps(discovery_get_trader_state(trader_addresses=addresses))


def handle_market_get_asset_data(params: Dict[str, Any]) -> str:
    asset = params.get("asset", "")
    if not asset:
        return json.dumps({"status": "error", "error": "asset required"})
    intervals = params.get("intervals")
    return json.dumps(market_get_asset_data(asset=asset, intervals=intervals))


def handle_market_get_funding_regime(params: Dict[str, Any]) -> str:
    return json.dumps(market_get_funding_regime())


def handle_market_list_instruments(params: Dict[str, Any]) -> str:
    return json.dumps(market_list_instruments())


def handle_market_get_mids(params: Dict[str, Any]) -> str:
    return json.dumps(market_get_mids())


# MCP server loop
def run() -> None:
    # Initialize tool handlers
    tool_handlers = {
        "scan": handle_scan,
        "research": handle_research,
        "execute": handle_execute,
        "state": handle_state,
        "config": handle_config,
        "leaderboard_get_markets": handle_leaderboard_get_markets,
        "leaderboard_get_top_traders": handle_leaderboard_get_top_traders,
        "leaderboard_get_trader_positions": handle_leaderboard_get_trader_positions,
        "discovery_get_top_traders": handle_discovery_get_top_traders,
        "discovery_get_trader_state": handle_discovery_get_trader_state,
        "market_get_asset_data": handle_market_get_asset_data,
        "market_get_funding_regime": handle_market_get_funding_regime,
        "market_list_instruments": handle_market_list_instruments,
        "market_get_mids": handle_market_get_mids,
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
