#!/usr/bin/env python3
"""Hermes-Trader MCP server — stdio transport for Hermes Agent.

Exposes trading tools to Hermes Agent:
  - scan(minScore, maxMarkets)
  - research(coin)
  - submit_verdict(coin, verdict, ...)
  - execute(analysisId)
  - close_position(coin)
  - state()
  - config()

Run as: python scripts/hermes-mcp-server.py

Automatically loads .env.local from project root if present.
"""

import json
import sys
import os
import time
import uuid
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

from hermes_trader import __version__
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.perception import scan_once
from hermes_trader.client.hl_client import fetch_account_state, resolve_user_address
from hermes_trader.agents.hyperfeed import (
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

# Set True at `initialize` if the connected client advertised the MCP `sampling`
# capability. When set, the `research` tool routes its verdict completion through
# the calling harness's own model (server -> client `sampling/createMessage`)
# instead of ai_brain/OpenRouter. So the harness that drives the bot is also the
# brain that researches — no sidestep. Falls back to ai_brain when unset/failed.
_CLIENT_SUPPORTS_SAMPLING = False
_server_request_seq = 0


def _norm_coin(raw: str) -> str:
    """Normalize a coin parameter without mangling HIP-3 dex prefixes.

    Bare crypto tickers are case-insensitive (`btc` → `BTC`) but HIP-3 perp
    names are `<lowercase-dex>:<uppercase-symbol>` (`xyz:MU`, `vntl:NVDA`).
    A naive `.upper()` produces `XYZ:MU` and the position lookup fails.
    Keep the dex prefix as-is and only uppercase the symbol.
    """
    if not raw:
        return ""
    if ":" in raw:
        dex, _, sym = raw.partition(":")
        return f"{dex}:{sym.upper()}"
    return raw.upper()

# Tools whose underlying SDK call is not yet wired up. Each one is registered
# with the MCP server so clients don't get a "tool not found" error, but
# instead of returning fake data (which an LLM would silently consume —
# things like `{'fear_greed': 50}` or `{'max_size': 0}` look like real
# numbers), the handler returns an explicit `not_implemented` error. This
# keeps tool discovery honest: an LLM that gets this response knows to skip
# the value rather than fold a placeholder into its reasoning.
_STUB_TOOL_NAMES = [
    'get_trade_history', 'get_funding_history', 'get_sub_accounts',
    'get_user_twist', 'get_withdrawals', 'get_predicted_funding',
    'get_asset_context', 'get_user_defined_types', 'get_api_keys',
    'get_user_verify', 'get_liquidations', 'get_order_status',
    'get_user_orders', 'get_assets', 'get_market_stats',
    'get_deposits', 'get_transfers', 'get_rewards',
    'get_staking_info', 'get_user_roles', 'get_leverage',
    'get_max_trade_size', 'get_portfolio_status', 'get_coin_price',
    'get_trading_permissions', 'get_recent_trades', 'get_funding_rate',
    'get_liquidation_events', 'get_exchange_status', 'get_user_preferences',
    'get_historical_funding', 'get_open_interest', 'get_market_sentiment',
    'get_leaderboard_rank', 'get_vaults', 'get_vault_details',
    'get_api_rate_limits', 'get_user_orders_history', 'get_price_impact',
    'get_slippage_estimate', 'get_withdrawal_status', 'get_deposit_address',
    'get_transfer_history', 'get_governance_proposals', 'get_validator_info',
    'get_network_stats', 'get_sub_account_balances',
]


def _make_stub_handler(tool_name: str):
    """Build a handler that explicitly reports the tool is not implemented.

    Returns an error payload instead of fake data so any LLM caller gets an
    unambiguous "don't use this" signal rather than a plausible-looking zero.
    """
    def handler(params: Dict[str, Any]) -> str:
        return json.dumps({
            "error": "not_implemented",
            "tool": tool_name,
            "reason": "stub — underlying Hyperliquid SDK method not yet wired up. Do not use the response as data.",
        })
    return handler


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
        "name": "submit_verdict",
        "description": (
            "Submit an agent-authored verdict as a stored analysis. Use this when "
            "Claude/Codex/OpenClaw is the brain. The returned analysisId can be "
            "passed to execute, which still routes through risk gates / close helper."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (e.g. BTC or xyz:NVDA)"},
                "verdict": {"type": "string", "enum": ["PASS", "LONG", "SHORT", "CLOSE"]},
                "confidence": {"type": "number", "description": "0.0-1.0"},
                "side": {"type": ["string", "null"], "enum": ["long", "short", None]},
                "entryPx": {"type": "number"},
                "stopPx": {"type": "number"},
                "tpPx": {"type": "number"},
                "reasoning": {"type": "string"},
                "newsRisk": {"type": "string", "enum": ["none", "positive", "negative"]},
                "compositeScore": {"type": "number"},
                "source": {"type": "string", "description": "Operator label, e.g. codex_mcp"},
            },
            "required": ["coin", "verdict", "confidence", "reasoning"],
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
                    "description": "Analysis ID from a research or submit_verdict call",
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
        "description": (
            "Get or set agent configuration. Call with no params to read the full "
            "config. Keys are snake_case and match .agent-config.json exactly. "
            "Covers mode, sizing, risk caps, regime gates, exits, GEX, and the "
            "live strategy blocks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["OFF", "LIVE"]},
                "enable_crypto": {"type": "boolean", "description": "Scan/trade native Hyperliquid crypto perps."},
                "enable_hip3": {"type": "boolean", "description": "Scan/trade HIP-3 tokenized-equity/commodity perps."},
                "hip3_dex_allowlist": {"type": "array", "items": {"type": "string"}},
                "hip3_dex_blocklist": {"type": "array", "items": {"type": "string"}},
                # ── Sizing / leverage ────────────────────────────────────
                "leverage": {"type": "number", "description": "Leverage ceiling per trade (min with coin max)."},
                "equity_fraction_per_trade": {"type": "number", "description": "Fraction of equity committed as margin per trade."},
                "max_trade_notional_usd": {"type": "number", "description": "Per-trade notional CEILING; sizing clamps to this."},
                "asset_notional_multiplier": {
                    "type": "object",
                    "description": "Post-sizing exposure scale by bucket, e.g. {\"crypto\":0.35,\"hip3\":1.0}.",
                },
                "tp_scale_fraction": {"type": "number", "description": "Fraction of a position auto-banked at the TP target (0=off, 0.5=half)."},
                # ── Concurrency / margin ─────────────────────────────────
                "max_concurrent": {"type": "number"},
                "max_total_notional_pct": {"type": "number", "description": "Ceiling on combined open notional as a multiple of equity."},
                "min_available_margin_pct": {"type": "number", "description": "Block new trades when free margin < this fraction of equity (caps stacking)."},
                "max_daily_loss_usd": {"type": "number", "description": "Daily-loss kill switch (negative)."},
                "daily_giveback_halt_pct": {"type": "number", "description": "Give-back breaker: halt new entries once the day retraces this fraction from its peak (0=off)."},
                "daily_giveback_min_peak_usd": {"type": "number", "description": "Arm threshold for the give-back breaker (day must peak >= this first)."},
                "crowded_with_min_conf": {"type": "number", "description": "Min conf for a with-the-crowd aligned trade (short into SHORT_CROWDED / long into LONG_CROWDED); 0=off."},
                # ── Signal / regime gates ────────────────────────────────
                "min_ai_confidence": {"type": "number"},
                "counter_regime_min_conf": {"type": "number", "description": "Confidence bar for a trade AGAINST the regime."},
                "aligned_min_conf": {"type": "number", "description": "Confidence bar for a trade WITH the regime."},
                "block_counter_trend_bypass": {"type": "boolean", "description": "Require counter-regime trades to clear confidence/score instead of binary trigger bypass."},
                "trend_surface_enabled": {"type": "boolean", "description": "Surface trend-only candidates below composite threshold."},
                "runner_mover_surface_enabled": {"type": "boolean", "description": "Surface large 24h movers to AI even when fresh spike triggers no longer fire."},
                # ── Liquidity floors ─────────────────────────────────────
                "min_market_volume_usd": {"type": "number"},
                "min_hip3_volume_usd": {"type": "number"},
                "min_short_volume_usd": {"type": "number", "description": "Extra 24h-volume floor for shorts (squeeze risk)."},
                "max_crypto_long_correlated": {"type": "number", "description": "Cap on simultaneous correlated crypto positions."},
                "cooldown_min": {"type": "number"},
                "held_research_interval_min": {"type": "number"},
                "loss_cooldown_min": {"type": "number"},
                "min_ai_close_hold_min": {"type": "number"},
                "override_max_daily_extension_pct": {"type": "number"},
                "sl_atr_mult": {"type": "number"},
                "backup_sl_max_frac_of_liq": {"type": "number"},
                "strategy_book_notional_usd": {"type": "number"},
                "strategy_book_equity_frac": {"type": "number"},
                "short_notional_usd": {"type": "number"},
                # ── Lists ────────────────────────────────────────────────
                "coin_allowlist": {"type": "array", "items": {"type": "string"}},
                "coin_blocklist": {"type": "array", "items": {"type": "string"}},
                # ── Nested blocks are deep-merged with existing config ────
                "dsl_exit": {"type": "object", "description": "DSL trailing-stop block; partial objects are deep-merged."},
                "atr_risk_sizing": {"type": "object", "description": "ATR equal-risk sizing block; partial objects are deep-merged."},
                "runner_entry_gate": {"type": "object", "description": "Runner gate block; partial objects are deep-merged."},
                "capital_rotation": {"type": "object", "description": "Live capital rotation block; partial objects are deep-merged."},
                "gex_signal": {"type": "object", "description": "HIP-3 GEX call-wall guardrail block; partial objects are deep-merged."},
                "runner_mover_surface": {"type": "object", "description": "Large-mover surfacing block; partial objects are deep-merged."},
                "trend_filter_200ma": {"type": "object", "description": "200MA trend filter block; partial objects are deep-merged."},
                "override_volume_confirm": {"type": "object", "description": "Volume-confirm override block; partial objects are deep-merged."},
                "late_chase_relax": {"type": "object", "description": "Late-chase live guardrail block; partial objects are deep-merged."},
                "reentry_cap": {"type": "object", "description": "Per-coin reentry cap block; partial objects are deep-merged."},
                "xs_momentum": {"type": "object", "description": "Live cross-sectional momentum block; partial objects are deep-merged."},
                "extreme_fade": {"type": "object", "description": "Live crash-fade block; partial objects are deep-merged."},
                "rally_exhaustion": {"type": "object", "description": "Live rally-exhaustion block; partial objects are deep-merged."},
                "data_logger": {"type": "object", "description": "Funding/OI data logger block; partial objects are deep-merged."},
                "ai_brain": {"type": "object", "description": "AI brain provider block; partial objects are deep-merged."},
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
    {
        "name": "get_governance_proposals",
        "description": "Get active governance proposals.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_validator_info",
        "description": "Get validator information.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_network_stats",
        "description": "Get network statistics.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_liquidation_price",
        "description": "Calculate liquidation price for a position.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"},
            "size": {"type": "number", "description": "Position size"},
            "leverage": {"type": "number", "description": "Leverage used"},
            "is_long": {"type": "boolean", "description": "True for long, False for short"}
        }, "required": ["coin", "size", "leverage", "is_long"]}
    },
    {
        "name": "get_max_leverage",
        "description": "Get maximum allowed leverage for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_user_fills",
        "description": "Get recent fills for the configured user (most recent first).",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max fills to return (default 100)"}
        }}
    },
    {
        "name": "get_user_fills_by_time",
        "description": "Get user fills within a time window (unix ms).",
        "inputSchema": {"type": "object", "properties": {
            "start_time": {"type": "number", "description": "Start time (unix ms)"},
            "end_time": {"type": "number", "description": "End time (unix ms, optional)"}
        }, "required": ["start_time"]}
    },
    {
        "name": "get_user_funding_history",
        "description": "Get user's funding-payment history within a time window.",
        "inputSchema": {"type": "object", "properties": {
            "start_time": {"type": "number", "description": "Start time (unix ms)"},
            "end_time": {"type": "number", "description": "End time (unix ms, optional)"}
        }, "required": ["start_time"]}
    },
    {
        "name": "get_historical_orders",
        "description": "Get historical (filled + cancelled) orders for the configured user.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "query_order_by_cloid",
        "description": "Query an order by its client order ID (cloid).",
        "inputSchema": {"type": "object", "properties": {
            "user": {"type": "string", "description": "User address (optional, uses env)"},
            "cloid": {"type": "string", "description": "Client order ID"}
        }, "required": ["cloid"]}
    },
]


def handle_scan(params: Dict[str, Any]) -> str:
    from hermes_trader.agents.config import get_config
    from hermes_trader.client.universe import get_universe

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
    user = resolve_user_address()
    # include_hip3=True so the LLM sees aggregated equity and every HIP-3
    # position (xyz/vntl/km) when querying account state.
    account = fetch_account_state(user, include_hip3=True) if user else {"equity": 0, "total_ntl": 0, "asset_positions": []}
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
    from hermes_trader.agents.config_store import (
        merge_agent_config,
        read_agent_config,
        write_agent_config,
    )

    config = read_agent_config()

    # Snake_case scalar/bool/list keys that map 1:1 to .agent-config.json. Writing
    # snake_case (not the old camelCase) keeps a single canonical key per setting —
    # the previous handler wrote camelCase and silently created duplicate keys.
    _DIRECT_KEYS = [
        "mode", "enable_crypto", "enable_hip3",
        "hip3_dex_allowlist", "hip3_dex_blocklist",
        "leverage", "equity_fraction_per_trade", "max_trade_notional_usd",
        "asset_notional_multiplier",
        "tp_scale_fraction", "max_concurrent", "max_total_notional_pct",
        "min_available_margin_pct", "max_daily_loss_usd",
        "daily_giveback_halt_pct", "daily_giveback_min_peak_usd",
        "crowded_with_min_conf", "min_ai_confidence",
        "counter_regime_min_conf", "aligned_min_conf", "block_counter_trend_bypass",
        "trend_surface_enabled",
        "min_market_volume_usd", "min_hip3_volume_usd", "min_short_volume_usd",
        "max_crypto_long_correlated", "cooldown_min", "held_research_interval_min",
        "loss_cooldown_min", "min_ai_close_hold_min",
        "override_max_daily_extension_pct", "sl_atr_mult",
        "backup_sl_max_frac_of_liq", "strategy_book_notional_usd",
        "strategy_book_equity_frac", "short_notional_usd",
        "coin_allowlist", "coin_blocklist",
    ]
    _NESTED_KEYS = [
        "dsl_exit", "atr_risk_sizing", "runner_entry_gate",
        "capital_rotation", "gex_signal", "runner_mover_surface",
        "trend_filter_200ma", "override_volume_confirm", "late_chase_relax",
        "reentry_cap", "xs_momentum", "extreme_fade", "rally_exhaustion",
        "data_logger", "ai_brain",
    ]

    updates: Dict[str, Any] = {}
    for key in _DIRECT_KEYS:
        if key in params and params[key] is not None:
            updates[key] = params[key]

    for key in _NESTED_KEYS:
        if key not in params or params[key] is None:
            continue
        if not isinstance(params[key], dict):
            return json.dumps({"status": "error", "error": f"{key} must be an object"})
        updates[key] = params[key]

    # Save only if a setting was actually passed (a bare read must not rewrite).
    if params.get("runner_mover_surface_enabled") is not None:
        updates.setdefault("runner_mover_surface", {})["enabled"] = bool(
            params["runner_mover_surface_enabled"]
        )

    if updates:
        config = merge_agent_config(config, updates)
        write_agent_config(config)

    return json.dumps(config)


def handle_research(params: Dict[str, Any]) -> str:
    from hermes_trader.agents.research import research
    
    coin = params.get("coin", "")
    if not coin:
        return json.dumps({"status": "error", "error": "coin is required"})
    
    # Find matching perception from last scan
    perception = _perception_cache.get(coin)
    if not perception:
        # Build a minimal perception from the current mid price.
        from hermes_trader.client.hl_client import fetch_all_mids
        mids = fetch_all_mids()
        perception = {
            "id": f"{coin}-{int(time.time()*1000)}",
            "coin": coin,
            "type": "perp",
            "mid": float(mids.get(coin, 0)),
            "triggers": [],
            "composite_score": 0,
        }
    
    try:
        analysis = research(coin, perception, brain=_research_brain())
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


def _float_param(params: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key not in params or params[key] is None:
            continue
        try:
            return float(params[key])
        except (TypeError, ValueError):
            return default
    return default


def _clamp_confidence(raw: Any) -> float:
    try:
        conf = float(raw)
    except (TypeError, ValueError):
        conf = 0.0
    return max(0.0, min(1.0, conf))


def _analysis_flags_from_perception(perception: Dict[str, Any]) -> Dict[str, Any]:
    triggers = perception.get("triggers") or []
    return {
        "momentum_burst_fired": any(
            t.get("name") == "momentumBurst" and t.get("fired") for t in triggers
        ),
        "slow_burn_fired": any(
            t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h") and t.get("fired")
            for t in triggers
        ),
        "slow_burn_count": sum(
            1 for t in triggers
            if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h") and t.get("fired")
        ),
        "daily_mover_fired": any(
            t.get("name") == "dailyMover" and t.get("fired") for t in triggers
        ),
        "breakout_fired": any(
            t.get("name") == "breakout" and t.get("fired") for t in triggers
        ),
        "shock_day_fired": any(
            t.get("name") == "shockDay" and t.get("fired") for t in triggers
        ),
        "volume_spike_fired": any(
            t.get("name") == "volumeSpike" and t.get("fired") for t in triggers
        ),
        "uptrend_momentum_fired": any(
            t.get("name") == "uptrendMomentum" and t.get("fired") for t in triggers
        ),
        "downtrend_momentum_fired": any(
            t.get("name") == "downtrendMomentum" and t.get("fired") for t in triggers
        ),
    }


def handle_submit_verdict(params: Dict[str, Any]) -> str:
    """Store an agent-authored verdict as an analysis for execute()."""
    from hermes_trader.agents.memory import memory

    coin = _norm_coin(params.get("coin", ""))
    if not coin:
        return json.dumps({"status": "error", "error": "coin is required"})

    verdict = str(params.get("verdict", "")).upper()
    if verdict not in {"PASS", "LONG", "SHORT", "CLOSE"}:
        return json.dumps({
            "status": "error",
            "error": "verdict must be PASS, LONG, SHORT, or CLOSE",
        })

    confidence = _clamp_confidence(params.get("confidence"))
    raw_side = params.get("side")
    side = raw_side if raw_side in ("long", "short") else None
    if verdict == "LONG":
        side = "long"
    elif verdict == "SHORT":
        side = "short"

    perception = _perception_cache.get(coin) or {}
    mid = _float_param(params, "entryPx", "entry_px", default=float(perception.get("mid", 0) or 0))
    news_risk = str(params.get("newsRisk") or params.get("news_risk") or "none").lower()
    if news_risk not in {"none", "positive", "negative"}:
        news_risk = "none"

    composite_score = _float_param(
        params,
        "compositeScore",
        "composite_score",
        default=float(perception.get("composite_score", 0) or 0),
    )
    analysis = {
        "id": str(uuid.uuid4()),
        "perception_id": params.get("perceptionId") or perception.get("id", "mcp-submitted"),
        "coin": coin,
        "verdict": verdict,
        "confidence": confidence,
        "side": side,
        "entry_px": mid,
        "stop_px": _float_param(params, "stopPx", "stop_px", default=0.0),
        "tp_px": _float_param(params, "tpPx", "tp_px", default=0.0),
        "reasoning": str(params.get("reasoning") or ""),
        "news_context": params.get("newsContext") or "",
        "news_risk": news_risk,
        "ai_down": False,
        "ai_brain_provider": str(params.get("source") or "mcp_agent"),
        "created_at": int(time.time() * 1000),
        "composite_score": composite_score,
        "daily_move_pct": params.get("dailyMovePct") or perception.get("daily_move_pct"),
        "daily_volume_usd": params.get("dailyVolumeUsd") or perception.get("daily_volume_usd"),
        "mcp_submitted": True,
    }
    analysis.update(_analysis_flags_from_perception(perception))

    memory.record_analysis(analysis)
    return json.dumps({
        "status": "complete",
        "analysisId": analysis["id"],
        "coin": coin,
        "verdict": verdict,
        "confidence": confidence,
        "side": side,
        "entryPx": analysis["entry_px"],
        "stopPx": analysis["stop_px"],
        "tpPx": analysis["tp_px"],
        "reasoning": analysis["reasoning"],
        "ai_brain_provider": analysis["ai_brain_provider"],
    })


def handle_execute(params: Dict[str, Any]) -> str:
    from hermes_trader.agents.executor import route_verdict
    from hermes_trader.agents.memory import memory

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

    try:
        routed = route_verdict(analysis)
        if routed.get("action") == "execute":
            return json.dumps(routed.get("result") or {})
        if routed.get("action") == "close":
            return json.dumps({
                "status": "closed" if (routed.get("result") or {}).get("ok") else "error",
                "action": "close",
                "coin": analysis.get("coin"),
                "result": routed.get("result") or {},
            })
        return json.dumps({
            "status": "skipped",
            "action": routed.get("action"),
            "coin": analysis.get("coin"),
            "verdict": analysis.get("verdict"),
            "reason": "no executable action for verdict",
        })
    except Exception as e:
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
        "submit_verdict": handle_submit_verdict,
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
        "get_l2_book": handle_get_l2_book,
        "get_user_state": handle_get_user_state,
        "get_frontend_open_orders": handle_get_frontend_open_orders,
        "get_candles_aggregated": handle_get_candles_aggregated,
        "get_price_history": handle_get_price_history,
        "get_coin_info": handle_get_coin_info,
        "get_all_mids": handle_get_all_mids,
        "get_account_summary": handle_get_account_summary,
        "get_asset_positions": handle_get_asset_positions,
        "get_24h_stats": handle_get_24h_stats,
        "get_portfolio_pnl": handle_get_portfolio_pnl,
        "get_risk_metrics": handle_get_risk_metrics,
        "get_markets_info": handle_get_markets_info,
        "get_spot_markets": handle_get_spot_markets,
        "get_perp_markets": handle_get_perp_markets,
        "get_market_depth": handle_get_market_depth,
        "get_server_time": handle_get_server_time,
        "get_asset_contexts": handle_get_asset_contexts,
        "get_liquidation_price": handle_get_liquidation_price,
        "get_max_leverage": handle_get_max_leverage,
        "get_order_by_oid": handle_get_order_by_oid,
        "get_user_fees_detailed": handle_get_user_fees_detailed,
        "get_user_fills": handle_get_user_fills,
        "get_user_fills_by_time": handle_get_user_fills_by_time,
        "get_user_funding_history": handle_get_user_funding_history,
        "get_historical_orders": handle_get_historical_orders,
        "query_order_by_cloid": handle_query_order_by_cloid,
    }

    for _name in _STUB_TOOL_NAMES:
        tool_handlers[_name] = _make_stub_handler(_name)

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
                global _CLIENT_SUPPORTS_SAMPLING
                _client_caps = (params or {}).get("capabilities") or {}
                _CLIENT_SUPPORTS_SAMPLING = "sampling" in _client_caps
                sys.stderr.write(
                    f"[mcp] client sampling capability: {_CLIENT_SUPPORTS_SAMPLING}\n"
                )
                sys.stderr.flush()
                write_response(msg_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "hermes-trader",
                        "version": __version__,
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
    from hermes_trader.client.hl_client import fetch_account_state
    user = resolve_user_address()
    state = fetch_account_state(user, include_hip3=True)
    return json.dumps(state.get('asset_positions', []), indent=2, default=str)

def handle_get_price(params: Dict[str, Any]) -> str:
    """Handle get_price tool call."""
    from hermes_trader.client.exchange import get_hl_price
    coin = _norm_coin(params.get('coin', 'BTC'))
    price = get_hl_price(coin)
    return json.dumps({'coin': coin, 'price': price}, default=str)

def handle_get_candles(params: Dict[str, Any]) -> str:
    """Handle get_candles tool call."""
    from hermes_trader.client.hl_client import fetch_hl_candles
    coin = _norm_coin(params.get('coin', 'BTC'))
    interval = params.get('interval', '1h')
    count = params.get('count', 100)
    candles = fetch_hl_candles(coin, interval, count)
    return json.dumps([c.model_dump() for c in candles], indent=2, default=str)

def handle_close_position(params: Dict[str, Any]) -> str:
    """Handle close_position tool call."""
    from hermes_trader.agents.executor import close_position_market
    
    coin = _norm_coin(params.get('coin', 'BTC'))
    
    try:
        result = close_position_market(coin)
        closed = bool(result.get('ok')) and not bool(result.get('noop'))
        payload = {'closed': closed, 'coin': coin, 'result': result}
        if not closed:
            payload['reason'] = result.get('noop') or result.get('error') or 'close_failed'
        return json.dumps(payload, default=str)
    except Exception as e:
        return json.dumps({'closed': False, 'error': str(e)}, default=str)

def handle_set_leverage(params: Dict[str, Any]) -> str:
    """Handle set_leverage tool call."""
    from hermes_trader.client.exchange import set_leverage as set_leverage_fn
    coin = _norm_coin(params.get('coin', 'BTC'))
    leverage = params.get('leverage', 5)
    result = set_leverage_fn(coin, int(leverage))
    return json.dumps(result, default=str)

def handle_get_open_orders(params: Dict[str, Any]) -> str:
    """Handle get_open_orders tool call."""
    from hermes_trader.client.hl_client import fetch_account_state
    user = resolve_user_address()
    state = fetch_account_state(user)
    orders = state.get('open_orders', [])
    coin_filter = _norm_coin(params.get('coin', ''))
    if coin_filter:
        orders = [o for o in orders if _norm_coin(o.get('coin', '')) == coin_filter]
    return json.dumps(orders, indent=2, default=str)

def handle_cancel_order(params: Dict[str, Any]) -> str:
    """Handle cancel_order tool call."""
    from hermes_trader.client.exchange import _make_exchange
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
    from hermes_trader.client.exchange import _get_info
    user = resolve_user_address()
    try:
        info = _get_info()
        spot_state = info.spot_user_state(user)
        return json.dumps(spot_state.get('balances', []), indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_fees(params: Dict[str, Any]) -> str:
    """Handle get_user_fees tool call."""
    from hermes_trader.client.exchange import _get_info
    user = resolve_user_address()
    try:
        info = _get_info()
        fees = info.user_fees(user)
        return json.dumps(fees, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_referral(params: Dict[str, Any]) -> str:
    """Handle get_referral tool call."""
    from hermes_trader.client.exchange import _get_info
    user = resolve_user_address()
    try:
        info = _get_info()
        referral = info.referral(user)
        return json.dumps(referral, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_l2_book(params: Dict[str, Any]) -> str:
    """Handle get_l2_book tool call."""
    from hermes_trader.client.exchange import _get_info
    coin = _norm_coin(params.get('coin', 'BTC'))
    try:
        info = _get_info()
        # L2 book snapshot
        book = info.l2_snapshot(coin)
        return json.dumps(book, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_state(params: Dict[str, Any]) -> str:
    """Handle get_user_state tool call."""
    from hermes_trader.client.exchange import _get_info
    user = resolve_user_address()
    try:
        info = _get_info()
        state = info.frontend_user_state(user)
        return json.dumps(state, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_frontend_open_orders(params: Dict[str, Any]) -> str:
    """Handle get_frontend_open_orders tool call."""
    from hermes_trader.client.exchange import _get_info
    user = resolve_user_address()
    coin = _norm_coin(params.get('coin', ''))
    try:
        info = _get_info()
        orders = info.frontend_open_orders(user)
        if coin:
            orders = [o for o in orders if _norm_coin(o.get('coin', '')) == coin]
        return json.dumps(orders, indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_candles_aggregated(params: Dict[str, Any]) -> str:
    """Handle get_candles_aggregated tool call."""
    from hermes_trader.client.hl_client import fetch_hl_candles
    coin = _norm_coin(params.get('coin', 'BTC'))
    interval = params.get('interval', '1h')
    count = params.get('count', 100)
    try:
        candles = fetch_hl_candles(coin, interval, count)
        return json.dumps([c.model_dump() for c in candles], indent=2, default=str)
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


def _server_request(method: str, params: Dict[str, Any], timeout_s: float = 120.0) -> Dict[str, Any]:
    """Send a server -> client JSON-RPC request and block for its response.

    Safe to call from inside a tools/call handler: the main loop is parked in the
    handler, so reading stdin here consumes the client's reply without racing it.
    Non-matching messages (notifications, stray requests) are skipped.
    """
    global _server_request_seq
    _server_request_seq += 1
    req_id = f"srv-{_server_request_seq}"
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0", "id": req_id, "method": method, "params": params,
    }) + "\n")
    sys.stdout.flush()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("stdin closed while awaiting server request response")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == req_id:
            if "error" in msg:
                raise RuntimeError(f"client returned error for {method}: {msg['error']}")
            return msg.get("result") or {}
        sys.stderr.write(f"[mcp] skipped non-matching message while awaiting {req_id}\n")
        sys.stderr.flush()
    raise TimeoutError(f"server request {method} timed out after {timeout_s:.0f}s")


class _McpSamplingBrain:
    """AiBrain backend that asks the connected harness's model for the verdict via
    MCP `sampling/createMessage`. Routes research through the caller, not OpenRouter."""

    provider = "mcp_sampling"

    def complete(self, system_prompt: str, user_message: str) -> str:
        params = {
            "messages": [
                {"role": "user", "content": {"type": "text", "text": user_message}}
            ],
            "systemPrompt": system_prompt,
            "maxTokens": int(os.environ.get("HERMES_MCP_SAMPLING_MAX_TOKENS", "2048")),
            "temperature": 0.1,
        }
        result = _server_request("sampling/createMessage", params)
        content = result.get("content")
        if isinstance(content, list):
            return " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            ).strip()
        if isinstance(content, dict):
            return str(content.get("text", "") or "")
        return ""


def _sampling_disabled() -> bool:
    return os.environ.get("HERMES_MCP_DISABLE_SAMPLING", "").strip().lower() in (
        "1", "true", "yes",
    )


def _research_brain():
    """Sampling brain when the harness can be the model, else None (configured provider)."""
    if _CLIENT_SUPPORTS_SAMPLING and not _sampling_disabled():
        return _McpSamplingBrain()
    return None


def handle_get_price_history(params: Dict[str, Any]) -> str:
    """Handle get_price_history tool call."""
    from hermes_trader.client.hl_client import fetch_hl_candles
    coin = _norm_coin(params.get('coin', 'BTC'))
    try:
        candles = fetch_hl_candles(coin, '1h', 100)
        return json.dumps([c.model_dump() for c in candles], indent=2, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_coin_info(params: Dict[str, Any]) -> str:
    """Handle get_coin_info tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from hermes_trader.client.exchange import get_coin_index
        idx, _, _ = get_coin_index(coin)
        return json.dumps({'coin': coin, 'index': idx}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_all_mids(params: Dict[str, Any]) -> str:
    """Handle get_all_mids tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        mids = info.all_mids()
        return json.dumps({'mids': mids}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_account_summary(params: Dict[str, Any]) -> str:
    """Handle get_account_summary tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'summary': state}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_asset_positions(params: Dict[str, Any]) -> str:
    """Handle get_asset_positions tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        positions = info.frontend_open_positions() if hasattr(info, 'frontend_open_positions') else []
        if coin:
            positions = [p for p in positions if _norm_coin(p.get('coin', '')) == coin]
        return json.dumps({'coin': coin, 'positions': positions}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_24h_stats(params: Dict[str, Any]) -> str:
    """Handle get_24h_stats tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from hermes_trader.client.hl_client import fetch_hl_candles
        # Get 24h of 1h candles for stats
        candles = fetch_hl_candles(coin, '1h', 24)
        return json.dumps({'coin': coin, 'stats_24h': [c.model_dump() for c in candles]}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_portfolio_pnl(params: Dict[str, Any]) -> str:
    """Handle get_portfolio_pnl tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'pnl': state}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_risk_metrics(params: Dict[str, Any]) -> str:
    """Handle get_risk_metrics tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        state = info.frontend_user_state() if hasattr(info, 'frontend_user_state') else {}
        return json.dumps({'risk': state}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_markets_info(params: Dict[str, Any]) -> str:
    """Handle get_markets_info tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'markets': meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_spot_markets(params: Dict[str, Any]) -> str:
    """Handle get_spot_markets tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        spot_meta = info.spot_meta() if hasattr(info, 'spot_meta') else {}
        return json.dumps({'spot_markets': spot_meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_perp_markets(params: Dict[str, Any]) -> str:
    """Handle get_perp_markets tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'perp_markets': meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_market_depth(params: Dict[str, Any]) -> str:
    """Handle get_market_depth tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        l2 = info.l2_snapshot(coin) if hasattr(info, 'l2_snapshot') else {}
        return json.dumps({'coin': coin, 'depth': l2}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_server_time(params: Dict[str, Any]) -> str:
    """Handle get_server_time tool call."""
    try:
        import time
        return json.dumps({'server_time': int(time.time() * 1000)}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_asset_contexts(params: Dict[str, Any]) -> str:
    """Handle get_asset_contexts tool call."""
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        return json.dumps({'contexts': meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_liquidation_price(params: Dict[str, Any]) -> str:
    """Handle get_liquidation_price tool call."""
    coin = _norm_coin(params.get('coin', ''))
    size = params.get('size')
    leverage = params.get('leverage')
    is_long = params.get('is_long')
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        mids = info.all_mids()
        mark = float(mids.get(coin, 0)) if mids else 0.0
        if not mark or not leverage:
            return json.dumps({'error': 'unable to compute', 'coin': coin}, default=str)
        # Simple liq estimate (1/leverage haircut, ignoring fees/maintenance)
        if is_long:
            liq = mark * (1 - 1.0/float(leverage))
        else:
            liq = mark * (1 + 1.0/float(leverage))
        return json.dumps({'coin': coin, 'size': size, 'leverage': leverage,
                           'is_long': is_long, 'mark': mark,
                           'liquidation_price': liq,
                           'note': 'simple estimate; ignores maintenance margin & funding'},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_max_leverage(params: Dict[str, Any]) -> str:
    """Handle get_max_leverage tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        meta = info.meta() if hasattr(info, 'meta') else {}
        universe = meta.get('universe', []) if isinstance(meta, dict) else []
        for asset in universe:
            if _norm_coin(asset.get('name', '')) == coin:
                return json.dumps({'coin': coin, 'max_leverage': asset.get('maxLeverage')},
                                  default=str)
        return json.dumps({'coin': coin, 'max_leverage': None, 'note': 'not found'},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_order_by_oid(params: Dict[str, Any]) -> str:
    """Handle get_order_by_oid tool call."""
    user = params.get('user', '')
    oid = params.get('oid')
    try:
        from hermes_trader.client.exchange import _get_info
        info = _get_info()
        if hasattr(info, 'query_order_by_oid'):
            res = info.query_order_by_oid(user, int(oid))
            return json.dumps({'order': res}, default=str)
        return json.dumps({'order': None}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_fees_detailed(params: Dict[str, Any]) -> str:
    """Handle get_user_fees_detailed tool call."""
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        if hasattr(info, 'user_fees') and HL_ACCOUNT:
            res = info.user_fees(HL_ACCOUNT)
            return json.dumps({'fees': res}, default=str)
        return json.dumps({'fees': {}, 'note': 'no address or SDK method pending'}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_fills(params: Dict[str, Any]) -> str:
    """Handle get_user_fills tool call."""
    limit = int(params.get('limit', 100))
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        if not HL_ACCOUNT:
            return json.dumps({'error': 'no configured user address'}, default=str)
        fills = info.user_fills(HL_ACCOUNT)
        if isinstance(fills, list):
            fills = fills[:limit]
        return json.dumps({'fills': fills, 'count': len(fills) if isinstance(fills, list) else 0}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_fills_by_time(params: Dict[str, Any]) -> str:
    """Handle get_user_fills_by_time tool call."""
    start_time = int(params.get('start_time', 0))
    end_time = params.get('end_time')
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        if not HL_ACCOUNT:
            return json.dumps({'error': 'no configured user address'}, default=str)
        if end_time is not None:
            fills = info.user_fills_by_time(HL_ACCOUNT, start_time, int(end_time))
        else:
            fills = info.user_fills_by_time(HL_ACCOUNT, start_time)
        return json.dumps({'fills': fills, 'count': len(fills) if isinstance(fills, list) else 0,
                           'start_time': start_time, 'end_time': end_time}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_funding_history(params: Dict[str, Any]) -> str:
    """Handle get_user_funding_history tool call."""
    start_time = int(params.get('start_time', 0))
    end_time = params.get('end_time')
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        if not HL_ACCOUNT:
            return json.dumps({'error': 'no configured user address'}, default=str)
        if end_time is not None:
            hist = info.user_funding_history(HL_ACCOUNT, start_time, int(end_time))
        else:
            hist = info.user_funding_history(HL_ACCOUNT, start_time)
        return json.dumps({'funding': hist, 'count': len(hist) if isinstance(hist, list) else 0,
                           'start_time': start_time, 'end_time': end_time}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_historical_orders(params: Dict[str, Any]) -> str:
    """Handle get_historical_orders tool call."""
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        if not HL_ACCOUNT:
            return json.dumps({'error': 'no configured user address'}, default=str)
        orders = info.historical_orders(HL_ACCOUNT)
        return json.dumps({'orders': orders, 'count': len(orders) if isinstance(orders, list) else 0},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_query_order_by_cloid(params: Dict[str, Any]) -> str:
    """Handle query_order_by_cloid tool call."""
    cloid = params.get('cloid', '')
    user = params.get('user')
    try:
        from hermes_trader.client.exchange import _get_info, HL_ACCOUNT
        info = _get_info()
        addr = user or HL_ACCOUNT
        if not addr:
            return json.dumps({'error': 'no user address provided or configured'}, default=str)
        res = info.query_order_by_cloid(addr, cloid)
        return json.dumps({'order': res, 'cloid': cloid, 'user': addr}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


if __name__ == "__main__":
    run()
