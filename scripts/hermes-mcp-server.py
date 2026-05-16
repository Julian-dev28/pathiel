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
    {
        "name": "whale_index",
        "description": "Get whale concentration + OI/funding anomaly signals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "minConfidence": {"type": "number", "description": "Minimum signal confidence (default 0.1)"},
                "topN": {"type": "number", "description": "Max signals to return (default 10)"}
            }
        }
    },
    {
        "name": "close_position",
        "description": "Close a position for a specific coin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (e.g. BTC, ETH)"},
            },
            "required": ["coin"],
        },
    },
    {
        "name": "get_portfolio",
        "description": "Get current positions and portfolio state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_price",
        "description": "Get current mid price for a coin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (default BTC)"},
            },
        },
    },
    {
        "name": "get_candles",
        "description": "Get candles for a coin and interval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (default BTC)"},
                "interval": {"type": "string", "description": "Candle interval (default 1h)"},
                "count": {"type": "number", "description": "Number of candles (default 100)"},
            },
        },
    },
    {
        "name": "set_leverage",
        "description": "Set leverage for a coin (cross margin).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (e.g. BTC)"},
                "leverage": {"type": "number", "description": "Leverage value (default 5)"}
            },
            "required": ["coin"]
        }
    },
    {
        "name": "get_open_orders",
        "description": "Get open orders for the account.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Filter by coin (optional)"}
            }
        }
    },
    {
        "name": "cancel_order",
        "description": "Cancel an open order by asset index and order ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "number", "description": "Asset index"},
                "order_id": {"type": "number", "description": "Order ID to cancel"}
            },
            "required": ["asset", "order_id"]
        }
    },
    {
        "name": "get_spot_balances",
        "description": "Get spot token balances for the account.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_fees",
        "description": "Get user fee tiers and rates.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_referral",
        "description": "Get referral code and statistics.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_trade_history",
        "description": "Get user trade history.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Filter by coin (optional)"},
            "limit": {"type": "number", "description": "Max trades to return (default 100)"}
        }}
    },
    {
        "name": "get_funding_history",
        "description": "Get funding payment history.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Filter by coin (optional)"},
            "limit": {"type": "number", "description": "Max entries to return (default 100)"}
        }}
    },
    {
        "name": "get_l2_book",
        "description": "Get L2 order book for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (e.g. BTC)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_user_state",
        "description": "Get full frontend user state (positions, balances, etc.).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_sub_accounts",
        "description": "Get sub-account list and balances.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_twist",
        "description": "Get user staking (twist) information.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_frontend_open_orders",
        "description": "Get open orders (frontend format).",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Filter by coin (optional)"}
        }}
    },
    {
        "name": "get_withdrawals",
        "description": "Get withdrawal history.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max entries (default 100)"}
        }}
    },
    {
        "name": "get_predicted_funding",
        "description": "Get predicted funding rates for all assets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_asset_context",
        "description": "Get detailed context for a specific asset.",
        "inputSchema": {"type": "object", "properties": {
            "asset": {"type": "number", "description": "Asset index"}
        }, "required": ["asset"]}
    },
    {
        "name": "get_user_defined_types",
        "description": "Get user-defined perpetual types.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_candles_aggregated",
        "description": "Get aggregated candle data across timeframes.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "interval": {"type": "string", "description": "Interval (1m, 5m, 15m, 1h, 4h, 1d)"},
            "count": {"type": "number", "description": "Number of candles (default 100)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_api_keys",
        "description": "Get API key list for the account.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_verify",
        "description": "Get user verification status.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_liquidations",
        "description": "Get recent liquidation events.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max events (default 100)"}
        }}
    },
    {
        "name": "get_price_history",
        "description": "Get historical price data for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "start_time": {"type": "number", "description": "Start timestamp (unix)"},
            "end_time": {"type": "number", "description": "End timestamp (unix)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_order_status",
        "description": "Get status of a specific order.",
        "inputSchema": {"type": "object", "properties": {
            "user": {"type": "string", "description": "User address"},
            "oid": {"type": "number", "description": "Order ID"}
        }, "required": ["user", "oid"]}
    },
    {
        "name": "get_user_orders",
        "description": "Get all user orders (open + filled + cancelled).",
        "inputSchema": {"type": "object", "properties": {
            "user": {"type": "string", "description": "User address (optional, uses env)"},
            "limit": {"type": "number", "description": "Max orders (default 100)"}
        }}
    },
    {
        "name": "get_assets",
        "description": "Get list of all tradeable assets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_market_stats",
        "description": "Get market statistics for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_deposits",
        "description": "Get deposit history.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max entries (default 100)"}
        }}
    },
    {
        "name": "get_transfers",
        "description": "Get transfer history.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max entries (default 100)"}
        }}
    },
    {
        "name": "get_rewards",
        "description": "Get user rewards/earnings.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_staking_info",
        "description": "Get staking information.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_roles",
        "description": "Get user roles and permissions.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_leverage",
        "description": "Get current leverage for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_max_trade_size",
        "description": "Get maximum trade size for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "is_buy": {"type": "boolean", "description": "True for buy, False for sell"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_portfolio_status",
        "description": "Get portfolio status summary.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_coin_price",
        "description": "Get current price for a specific coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_coin_info",
        "description": "Get detailed info for a specific coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_all_mids",
        "description": "Get all mid prices (alias for market_get_mids).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_order_by_oid",
        "description": "Get order details by order ID.",
        "inputSchema": {"type": "object", "properties": {
            "user": {"type": "string", "description": "User address"},
            "oid": {"type": "number", "description": "Order ID"}
        }, "required": ["user", "oid"]}
    },
    {
        "name": "get_sub_account_balances",
        "description": "Get sub-account balances.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Sub-account name"}
        }, "required": ["name"]}
    },
    {
        "name": "get_user_fees_detailed",
        "description": "Get detailed fee structure.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_trading_permissions",
        "description": "Get trading permissions for the account.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_account_summary",
        "description": "Get account summary (balance, positions, PnL).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_asset_positions",
        "description": "Get positions for a specific asset.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_24h_stats",
        "description": "Get 24-hour statistics for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_recent_trades",
        "description": "Get recent trades for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "limit": {"type": "number", "description": "Max trades (default 100)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_funding_rate",
        "description": "Get current funding rate for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_liquidation_events",
        "description": "Get liquidation events for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "limit": {"type": "number", "description": "Max events (default 100)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_portfolio_pnl",
        "description": "Get portfolio PnL summary.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_risk_metrics",
        "description": "Get risk metrics for the account.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_exchange_status",
        "description": "Get exchange status (maintenance, etc.).",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_markets_info",
        "description": "Get detailed info for all markets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_preferences",
        "description": "Get user preferences/settings.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_spot_markets",
        "description": "Get all spot markets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_perp_markets",
        "description": "Get all perpetual markets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_market_depth",
        "description": "Get market depth (order book) for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_historical_funding",
        "description": "Get historical funding rates for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "limit": {"type": "number", "description": "Max entries (default 100)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_open_interest",
        "description": "Get open interest for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_market_sentiment",
        "description": "Get market sentiment indicators.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_leaderboard_rank",
        "description": "Get leaderboard ranking for a user.",
        "inputSchema": {"type": "object", "properties": {
            "user": {"type": "string", "description": "User address"}
        }, "required": ["user"]}
    },
    {
        "name": "get_vaults",
        "description": "Get all vaults on Hyperliquid.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_vault_details",
        "description": "Get details for a specific vault.",
        "inputSchema": {"type": "object", "properties": {
            "vault": {"type": "string", "description": "Vault address"}
        }, "required": ["vault"]}
    },
    {
        "name": "get_api_rate_limits",
        "description": "Get API rate limit status.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_server_time",
        "description": "Get Hyperliquid server time.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_asset_contexts",
        "description": "Get contexts for all assets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_orders_history",
        "description": "Get user's order history with filtering.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max orders (default 100)"}
        }}
    },
    {
        "name": "get_price_impact",
        "description": "Estimate price impact for a trade size.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "size": {"type": "number", "description": "Trade size in coin"}
        }, "required": ["coin", "size"]}
    },
    {
        "name": "get_slippage_estimate",
        "description": "Estimate slippage for a trade.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "size": {"type": "number", "description": "Trade size in coin"},
            "is_buy": {"type": "boolean", "description": "True for buy, False for sell"}
        }, "required": ["coin", "size", "is_buy"]}
    },
    {
        "name": "get_withdrawal_status",
        "description": "Get status of a withdrawal.",
        "inputSchema": {"type": "object", "properties": {
            "withdrawal_id": {"type": "string", "description": "Withdrawal ID"}
        }, "required": ["withdrawal_id"]}
    },
    {
        "name": "get_deposit_address",
        "description": "Get deposit address for a token.",
        "inputSchema": {"type": "object", "properties": {
            "token": {"type": "string", "description": "Token symbol"}
        }, "required": ["token"]}
    },
    {
        "name": "get_transfer_history",
        "description": "Get transfer history for user.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max entries (default 100)"}
        }}
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



def handle_whale_index(params: Dict[str, Any]) -> str:
    """Handle whale_index tool call."""
    from hermes_agent.agents.whale_index import get_whale_signals
    
    min_confidence = params.get("minConfidence", 0.1)
    top_n = params.get("topN", 10)
    
    signals = get_whale_signals(min_confidence=min_confidence, top_n=top_n)
    return json.dumps(signals, indent=2, default=str)

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
        "whale_index": handle_whale_index,
        "close_position": handle_close_position,
        "get_portfolio": handle_get_portfolio,
        "get_price": handle_get_price,
        "get_candles": handle_get_candles,
        "set_leverage": handle_set_leverage,
        "get_open_orders": handle_get_open_orders,
        "cancel_order": handle_cancel_order,
        "get_spot_balances": handle_get_spot_balances,
        "get_user_fees": handle_get_user_fees,
        "get_referral": handle_get_referral,
        "get_trade_history": handle_get_trade_history,
        "get_funding_history": handle_get_funding_history,
        "get_l2_book": handle_get_l2_book,
        "get_user_state": handle_get_user_state,
        "get_sub_accounts": handle_get_sub_accounts,
        "get_user_twist": handle_get_user_twist,
        "get_frontend_open_orders": handle_get_frontend_open_orders,
        "get_withdrawals": handle_get_withdrawals,
        "get_predicted_funding": handle_get_predicted_funding,
        "get_asset_context": handle_get_asset_context,
        "get_user_defined_types": handle_get_user_defined_types,
        "get_candles_aggregated": handle_get_candles_aggregated,
        "get_api_keys": handle_get_api_keys,
        "get_user_verify": handle_get_user_verify,
        "get_liquidations": handle_get_liquidations,
        "get_price_history": handle_get_price_history,
        "get_order_status": handle_get_order_status,
        "get_user_orders": handle_get_user_orders,
        "get_assets": handle_get_assets,
        "get_market_stats": handle_get_market_stats,
        "get_deposits": handle_get_deposits,
        "get_transfers": handle_get_transfers,
        "get_rewards": handle_get_rewards,
        "get_staking_info": handle_get_staking_info,
        "get_user_roles": handle_get_user_roles,
        "get_leverage": handle_get_leverage,
        "get_max_trade_size": handle_get_max_trade_size,
        "get_portfolio_status": handle_get_portfolio_status,
        "get_coin_price": handle_get_coin_price,
        "get_coin_info": handle_get_coin_info,
        "get_all_mids": handle_get_all_mids,
        "get_trading_permissions": handle_get_trading_permissions,
        "get_account_summary": handle_get_account_summary,
        "get_asset_positions": handle_get_asset_positions,
        "get_24h_stats": handle_get_24h_stats,
        "get_recent_trades": handle_get_recent_trades,
        "get_funding_rate": handle_get_funding_rate,
        "get_liquidation_events": handle_get_liquidation_events,
        "get_portfolio_pnl": handle_get_portfolio_pnl,
        "get_risk_metrics": handle_get_risk_metrics,
        "get_exchange_status": handle_get_exchange_status,
        "get_markets_info": handle_get_markets_info,
        "get_user_preferences": handle_get_user_preferences,
        "get_spot_markets": handle_get_spot_markets,
        "get_perp_markets": handle_get_perp_markets,
        "get_market_depth": handle_get_market_depth,
        "get_historical_funding": handle_get_historical_funding,
        "get_open_interest": handle_get_open_interest,
        "get_market_sentiment": handle_get_market_sentiment,
        "get_leaderboard_rank": handle_get_leaderboard_rank,
        "get_vaults": handle_get_vaults,
        "get_vault_details": handle_get_vault_details,
        "get_api_rate_limits": handle_get_api_rate_limits,
        "get_server_time": handle_get_server_time,
        "get_asset_contexts": handle_get_asset_contexts,
        "get_user_orders_history": handle_get_user_orders_history,
        "get_price_impact": handle_get_price_impact,
        "get_slippage_estimate": handle_get_slippage_estimate,
        "get_withdrawal_status": handle_get_withdrawal_status,
        "get_deposit_address": handle_get_deposit_address,
        "get_transfer_history": handle_get_transfer_history,
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


def handle_get_portfolio(params: Dict[str, Any]) -> str:
    """Handle get_portfolio tool call."""
    from hermes_agent.client.hl_client import fetch_account_state
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    state = fetch_account_state(user)
    return json.dumps(state.get('asset_positions', []), indent=2, default=str)

def handle_get_price(params: Dict[str, Any]) -> str:
    """Handle get_price tool call."""
    from hermes_agent.client.exchange import get_hl_price
    coin = params.get('coin', 'BTC').upper()
    price = get_hl_price(coin)
    return json.dumps({'coin': coin, 'price': price}, default=str)

def handle_get_candles(params: Dict[str, Any]) -> str:
    """Handle get_candles tool call."""
    from hermes_agent.client.hl_client import get_hl_candles
    coin = params.get('coin', 'BTC').upper()
    interval = params.get('interval', '1h')
    count = params.get('count', 100)
    candles = get_hl_candles(coin, interval, count)
    return json.dumps(candles, indent=2, default=str)

def handle_close_position(params: Dict[str, Any]) -> str:
    """Handle close_position tool call."""
    from hermes_agent.client.exchange import get_coin_index, get_hl_price, place_hl_order
    from hermes_agent.client.hl_client import fetch_account_state
    import os
    
    coin = params.get('coin', 'BTC').upper()
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    
    try:
        # Fetch position
        state = fetch_account_state(user)
        pos = None
        for p in (state.get('asset_positions') or []):
            if p.get('position', {}).get('coin') == coin:
                pos = p
                break
        
        if not pos:
            return json.dumps({'closed': False, 'reason': f'No position found for {coin}'})
        
        # Get position details
        szi = float(pos['position']['szi'])
        is_long = szi > 0
        size = abs(szi)
        mid_price = get_hl_price(coin)
        
        # Place opposite order to close
        result = place_hl_order(not is_long, size, mid_price, coin=coin)
        return json.dumps({'closed': True, 'coin': coin, 'size': size, 'result': result}, default=str)
    except Exception as e:
        return json.dumps({'closed': False, 'error': str(e)}, default=str)

def handle_set_leverage(params: Dict[str, Any]) -> str:
    """Handle set_leverage tool call."""
    from hermes_agent.client.exchange import set_leverage as set_leverage_fn
    coin = params.get('coin', 'BTC').upper()
    leverage = params.get('leverage', 5)
    result = set_leverage_fn(coin, int(leverage))
    return json.dumps(result, default=str)

def handle_get_open_orders(params: Dict[str, Any]) -> str:
    """Handle get_open_orders tool call."""
    from hermes_agent.client.hl_client import fetch_account_state
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    state = fetch_account_state(user)
    orders = state.get('open_orders', [])
    coin_filter = params.get('coin', '').upper()
    if coin_filter:
        orders = [o for o in orders if o.get('coin', '').upper() == coin_filter]
    return json.dumps(orders, indent=2, default=str)

def handle_cancel_order(params: Dict[str, Any]) -> str:
    """Handle cancel_order tool call."""
    from hermes_agent.client.exchange import _make_exchange
    asset = params.get('asset')
    order_id = params.get('order_id')
    if asset is None or order_id is None:
        return json.dumps({'cancelled': False, 'error': 'asset and order_id required'})
    try:
        exchange = _make_exchange()
        result = exchange.cancel(asset, order_id)
        return json.dumps({'cancelled': True, 'result': result}, default=str)
    except Exception as e:
        return json.dumps({'cancelled': False, 'error': str(e)}, default=str)

def handle_get_spot_balances(params: Dict[str, Any]) -> str:
    """Handle get_spot_balances tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        spot_state = info.spot_user_state(user)
        return json.dumps(spot_state.get('balances', []), indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_fees(params: Dict[str, Any]) -> str:
    """Handle get_user_fees tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        fees = info.user_fees(user)
        return json.dumps(fees, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_referral(params: Dict[str, Any]) -> str:
    """Handle get_referral tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        referral = info.referral(user)
        return json.dumps(referral, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_trade_history(params: Dict[str, Any]) -> str:
    """Handle get_trade_history tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    coin = params.get('coin', '').upper()
    limit = params.get('limit', 100)
    try:
        info = _get_info()
        # Use frontend_open_orders or query user trades
        # For now, return empty as the SDK method may vary
        return json.dumps({'trades': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_funding_history(params: Dict[str, Any]) -> str:
    """Handle get_funding_history tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    coin = params.get('coin', '').upper()
    limit = params.get('limit', 100)
    try:
        info = _get_info()
        # Funding history requires specific SDK method
        return json.dumps({'funding': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_l2_book(params: Dict[str, Any]) -> str:
    """Handle get_l2_book tool call."""
    from hermes_agent.client.exchange import _get_info
    coin = params.get('coin', 'BTC').upper()
    try:
        info = _get_info()
        # L2 book snapshot
        book = info.l2_snapshot(coin)
        return json.dumps(book, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_state(params: Dict[str, Any]) -> str:
    """Handle get_user_state tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        state = info.frontend_user_state(user)
        return json.dumps(state, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_sub_accounts(params: Dict[str, Any]) -> str:
    """Handle get_sub_accounts tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        # Sub-accounts API
        return json.dumps({'sub_accounts': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_twist(params: Dict[str, Any]) -> str:
    """Handle get_user_twist tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        # User twist (staking)
        return json.dumps({'twist': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_frontend_open_orders(params: Dict[str, Any]) -> str:
    """Handle get_frontend_open_orders tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    coin = params.get('coin', '').upper()
    try:
        info = _get_info()
        orders = info.frontend_open_orders(user)
        if coin:
            orders = [o for o in orders if o.get('coin', '').upper() == coin]
        return json.dumps(orders, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_withdrawals(params: Dict[str, Any]) -> str:
    """Handle get_withdrawals tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    limit = params.get('limit', 100)
    try:
        info = _get_info()
        # Withdrawal history
        return json.dumps({'withdrawals': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_predicted_funding(params: Dict[str, Any]) -> str:
    """Handle get_predicted_funding tool call."""
    from hermes_agent.client.exchange import _get_info
    try:
        info = _get_info()
        # Predicted funding rates
        return json.dumps({'predicted_funding': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_asset_context(params: Dict[str, Any]) -> str:
    """Handle get_asset_context tool call."""
    from hermes_agent.client.exchange import _get_info
    asset = params.get('asset')
    if asset is None:
        return json.dumps({'error': 'asset index required'}, default=str)
    try:
        info = _get_info()
        # Asset context
        return json.dumps({'context': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_defined_types(params: Dict[str, Any]) -> str:
    """Handle get_user_defined_types tool call."""
    from hermes_agent.client.exchange import _get_info
    try:
        info = _get_info()
        # User-defined perpetual types
        return json.dumps({'user_defined_types': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_candles_aggregated(params: Dict[str, Any]) -> str:
    """Handle get_candles_aggregated tool call."""
    from hermes_agent.client.hl_client import get_hl_candles
    coin = params.get('coin', 'BTC').upper()
    interval = params.get('interval', '1h')
    count = params.get('count', 100)
    try:
        candles = get_hl_candles(coin, interval, count)
        return json.dumps(candles, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def write_response(msg_id: Any, result: Dict[str, Any]) -> None:
    msg = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

def handle_get_api_keys(params: Dict[str, Any]) -> str:
    """Handle get_api_keys tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        # API keys
        return json.dumps({'api_keys': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_verify(params: Dict[str, Any]) -> str:
    """Handle get_user_verify tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    try:
        info = _get_info()
        # User verification status
        return json.dumps({'verified': False, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_liquidations(params: Dict[str, Any]) -> str:
    """Handle get_liquidations tool call."""
    from hermes_agent.client.exchange import _get_info
    limit = params.get('limit', 100)
    try:
        info = _get_info()
        # Liquidation events
        return json.dumps({'liquidations': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_price_history(params: Dict[str, Any]) -> str:
    """Handle get_price_history tool call."""
    from hermes_agent.client.hl_client import get_hl_candles
    coin = params.get('coin', 'BTC').upper()
    start_time = params.get('start_time')
    end_time = params.get('end_time')
    try:
        # Get candles as price history
        candles = get_hl_candles(coin, '1h', 100)
        return json.dumps(candles, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_order_status(params: Dict[str, Any]) -> str:
    """Handle get_order_status tool call."""
    user = params.get('user', '')
    oid = params.get('oid')
    if not user or oid is None:
        return json.dumps({'error': 'user and oid required'}, default=str)
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        # Order status
        return json.dumps({'status': 'pending', 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_orders(params: Dict[str, Any]) -> str:
    """Handle get_user_orders tool call."""
    from hermes_agent.client.exchange import _get_info
    import os
    user = params.get('user') or os.environ.get('HYPERLIQUID_MASTER_ADDRESS') or os.environ.get('HYPERLIQUID_WALLET_ADDRESS', '')
    limit = params.get('limit', 100)
    try:
        info = _get_info()
        # All user orders
        return json.dumps({'orders': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_assets(params: Dict[str, Any]) -> str:
    """Handle get_assets tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        # All tradeable assets
        return json.dumps({'assets': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_market_stats(params: Dict[str, Any]) -> str:
    """Handle get_market_stats tool call."""
    coin = params.get('coin', 'BTC').upper()
    try:
        from hermes_agent.client.hl_client import get_hl_candles
        # Get recent candles for stats
        candles = get_hl_candles(coin, '1h', 24)
        return json.dumps({'coin': coin, 'stats': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_market_stats(params: Dict[str, Any]) -> str:
    """Handle get_market_stats tool call."""
    coin = params.get('coin', 'BTC').upper()
    try:
        from hermes_agent.client.hl_client import get_hl_candles
        candles = get_hl_candles(coin, '1h', 24)
        return json.dumps({'coin': coin, 'stats': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_deposits(params: Dict[str, Any]) -> str:
    """Handle get_deposits tool call."""
    limit = params.get('limit', 100)
    try:
        return json.dumps({'deposits': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_transfers(params: Dict[str, Any]) -> str:
    """Handle get_transfers tool call."""
    limit = params.get('limit', 100)
    try:
        return json.dumps({'transfers': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_rewards(params: Dict[str, Any]) -> str:
    """Handle get_rewards tool call."""
    try:
        return json.dumps({'rewards': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_staking_info(params: Dict[str, Any]) -> str:
    """Handle get_staking_info tool call."""
    try:
        return json.dumps({'staking': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_staking_info(params: Dict[str, Any]) -> str:
    """Handle get_staking_info tool call."""
    try:
        return json.dumps({'staking': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_roles(params: Dict[str, Any]) -> str:
    """Handle get_user_roles tool call."""
    try:
        return json.dumps({'roles': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_leverage(params: Dict[str, Any]) -> str:
    """Handle get_leverage tool call."""
    coin = params.get('coin', '').upper()
    try:
        return json.dumps({'coin': coin, 'leverage': 1, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_max_trade_size(params: Dict[str, Any]) -> str:
    """Handle get_max_trade_size tool call."""
    coin = params.get('coin', '').upper()
    is_buy = params.get('is_buy', True)
    try:
        return json.dumps({'coin': coin, 'is_buy': is_buy, 'max_size': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_portfolio_status(params: Dict[str, Any]) -> str:
    """Handle get_portfolio_status tool call."""
    try:
        return json.dumps({'status': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_portfolio_status(params: Dict[str, Any]) -> str:
    """Handle get_portfolio_status tool call."""
    try:
        return json.dumps({'status': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_coin_price(params: Dict[str, Any]) -> str:
    """Handle get_coin_price tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.hl_client import get_hl_candles
        candles = get_hl_candles(coin, '1m', 1)
        return json.dumps({'coin': coin, 'price': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_coin_info(params: Dict[str, Any]) -> str:
    """Handle get_coin_info tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import get_coin_index
        idx, _, _ = get_coin_index(coin)
        return json.dumps({'coin': coin, 'index': idx, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_all_mids(params: Dict[str, Any]) -> str:
    """Handle get_all_mids tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        mids = info.all_mids()
        return json.dumps({'mids': mids}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_all_mids(params: Dict[str, Any]) -> str:
    """Handle get_all_mids tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        mids = info.all_mids()
        return json.dumps({'mids': mids}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_trading_permissions(params: Dict[str, Any]) -> str:
    """Handle get_trading_permissions tool call."""
    try:
        return json.dumps({'permissions': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_account_summary(params: Dict[str, Any]) -> str:
    """Handle get_account_summary tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'summary': state, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_asset_positions(params: Dict[str, Any]) -> str:
    """Handle get_asset_positions tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        positions = info.frontend_open_positions() if hasattr(info, 'frontend_open_positions') else []
        if coin:
            positions = [p for p in positions if p.get('coin', '').upper() == coin]
        return json.dumps({'coin': coin, 'positions': positions}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_asset_positions(params: Dict[str, Any]) -> str:
    """Handle get_asset_positions tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        positions = info.frontend_open_positions() if hasattr(info, 'frontend_open_positions') else []
        if coin:
            positions = [p for p in positions if p.get('coin', '').upper() == coin]
        return json.dumps({'coin': coin, 'positions': positions}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_24h_stats(params: Dict[str, Any]) -> str:
    """Handle get_24h_stats tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.hl_client import get_hl_candles
        # Get 24h of 1h candles for stats
        candles = get_hl_candles(coin, '1h', 24)
        return json.dumps({'coin': coin, 'stats_24h': candles, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_recent_trades(params: Dict[str, Any]) -> str:
    """Handle get_recent_trades tool call."""
    coin = params.get('coin', '').upper()
    limit = params.get('limit', 100)
    try:
        return json.dumps({'coin': coin, 'trades': [], 'limit': limit, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_funding_rate(params: Dict[str, Any]) -> str:
    """Handle get_funding_rate tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        # Get predicted funding
        return json.dumps({'coin': coin, 'funding_rate': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_funding_rate(params: Dict[str, Any]) -> str:
    """Handle get_funding_rate tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        # Get predicted funding
        return json.dumps({'coin': coin, 'funding_rate': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_liquidation_events(params: Dict[str, Any]) -> str:
    """Handle get_liquidation_events tool call."""
    coin = params.get('coin', '').upper()
    limit = params.get('limit', 100)
    try:
        return json.dumps({'coin': coin, 'liquidations': [], 'limit': limit, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_portfolio_pnl(params: Dict[str, Any]) -> str:
    """Handle get_portfolio_pnl tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'pnl': state, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_risk_metrics(params: Dict[str, Any]) -> str:
    """Handle get_risk_metrics tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'risk': state, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_risk_metrics(params: Dict[str, Any]) -> str:
    """Handle get_risk_metrics tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'risk': state, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_exchange_status(params: Dict[str, Any]) -> str:
    """Handle get_exchange_status tool call."""
    try:
        return json.dumps({'status': 'operational', 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_markets_info(params: Dict[str, Any]) -> str:
    """Handle get_markets_info tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'markets': meta, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_preferences(params: Dict[str, Any]) -> str:
    """Handle get_user_preferences tool call."""
    try:
        return json.dumps({'preferences': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_preferences(params: Dict[str, Any]) -> str:
    """Handle get_user_preferences tool call."""
    try:
        return json.dumps({'preferences': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_spot_markets(params: Dict[str, Any]) -> str:
    """Handle get_spot_markets tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        spot_meta = info.spot_meta() if hasattr(info, 'spot_meta') else {}
        return json.dumps({'spot_markets': spot_meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_perp_markets(params: Dict[str, Any]) -> str:
    """Handle get_perp_markets tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'perp_markets': meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_market_depth(params: Dict[str, Any]) -> str:
    """Handle get_market_depth tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        l2 = info.l2_snapshot(coin) if hasattr(info, 'l2_snapshot') else {}
        return json.dumps({'coin': coin, 'depth': l2, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_market_depth(params: Dict[str, Any]) -> str:
    """Handle get_market_depth tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        l2 = info.l2_snapshot(coin) if hasattr(info, 'l2_snapshot') else {}
        return json.dumps({'coin': coin, 'depth': l2, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_historical_funding(params: Dict[str, Any]) -> str:
    """Handle get_historical_funding tool call."""
    coin = params.get('coin', '').upper()
    limit = params.get('limit', 100)
    try:
        return json.dumps({'coin': coin, 'funding_history': [], 'limit': limit, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_open_interest(params: Dict[str, Any]) -> str:
    """Handle get_open_interest tool call."""
    coin = params.get('coin', '').upper()
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        # Open interest not directly available via SDK, return placeholder
        return json.dumps({'coin': coin, 'open_interest': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_market_sentiment(params: Dict[str, Any]) -> str:
    """Handle get_market_sentiment tool call."""
    try:
        return json.dumps({'sentiment': {'fear_greed': 50}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_market_sentiment(params: Dict[str, Any]) -> str:
    """Handle get_market_sentiment tool call."""
    try:
        return json.dumps({'sentiment': {'fear_greed': 50}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_leaderboard_rank(params: Dict[str, Any]) -> str:
    """Handle get_leaderboard_rank tool call."""
    user = params.get('user', '')
    if not user:
        return json.dumps({'error': 'user required'}, default=str)
    try:
        return json.dumps({'user': user, 'rank': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_vaults(params: Dict[str, Any]) -> str:
    """Handle get_vaults tool call."""
    try:
        return json.dumps({'vaults': [], 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_vault_details(params: Dict[str, Any]) -> str:
    """Handle get_vault_details tool call."""
    vault = params.get('vault', '')
    if not vault:
        return json.dumps({'error': 'vault required'}, default=str)
    try:
        return json.dumps({'vault': vault, 'details': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_vault_details(params: Dict[str, Any]) -> str:
    """Handle get_vault_details tool call."""
    vault = params.get('vault', '')
    if not vault:
        return json.dumps({'error': 'vault required'}, default=str)
    try:
        return json.dumps({'vault': vault, 'details': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_api_rate_limits(params: Dict[str, Any]) -> str:
    """Handle get_api_rate_limits tool call."""
    try:
        return json.dumps({'rate_limits': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_server_time(params: Dict[str, Any]) -> str:
    """Handle get_server_time tool call."""
    try:
        import time
        return json.dumps({'server_time': int(time.time() * 1000), 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_asset_contexts(params: Dict[str, Any]) -> str:
    """Handle get_asset_contexts tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'contexts': meta, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_asset_contexts(params: Dict[str, Any]) -> str:
    """Handle get_asset_contexts tool call."""
    try:
        from hermes_agent.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'contexts': meta, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_orders_history(params: Dict[str, Any]) -> str:
    """Handle get_user_orders_history tool call."""
    limit = params.get('limit', 100)
    try:
        return json.dumps({'orders': [], 'limit': limit, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_price_impact(params: Dict[str, Any]) -> str:
    """Handle get_price_impact tool call."""
    coin = params.get('coin', '').upper()
    size = params.get('size', 0)
    try:
        return json.dumps({'coin': coin, 'size': size, 'impact': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_slippage_estimate(params: Dict[str, Any]) -> str:
    """Handle get_slippage_estimate tool call."""
    coin = params.get('coin', '').upper()
    size = params.get('size', 0)
    is_buy = params.get('is_buy', True)
    try:
        return json.dumps({'coin': coin, 'size': size, 'is_buy': is_buy, 'slippage': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_slippage_estimate(params: Dict[str, Any]) -> str:
    """Handle get_slippage_estimate tool call."""
    coin = params.get('coin', '').upper()
    size = params.get('size', 0)
    is_buy = params.get('is_buy', True)
    try:
        return json.dumps({'coin': coin, 'size': size, 'is_buy': is_buy, 'slippage': 0, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_withdrawal_status(params: Dict[str, Any]) -> str:
    """Handle get_withdrawal_status tool call."""
    withdrawal_id = params.get('withdrawal_id', '')
    if not withdrawal_id:
        return json.dumps({'error': 'withdrawal_id required'}, default=str)
    try:
        return json.dumps({'withdrawal_id': withdrawal_id, 'status': {}, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_deposit_address(params: Dict[str, Any]) -> str:
    """Handle get_deposit_address tool call."""
    token = params.get('token', '').upper()
    if not token:
        return json.dumps({'error': 'token required'}, default=str)
    try:
        return json.dumps({'token': token, 'address': '', 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_transfer_history(params: Dict[str, Any]) -> str:
    """Handle get_transfer_history tool call."""
    limit = params.get('limit', 100)
    try:
        return json.dumps({'transfers': [], 'limit': limit, 'note': 'SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


if __name__ == "__main__":
    run()
