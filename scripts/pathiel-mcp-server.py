#!/usr/bin/env python3
"""Pathiel-Trader MCP server — stdio transport for Pathiel Agent.

Exposes trading tools to Pathiel Agent:
  - scan(minScore, maxMarkets)
  - research(coin)
  - submit_verdict(coin, verdict, ...)
  - execute(analysisId)
  - close_position(coin)
  - state()
  - config()

Run as: python scripts/pathiel-mcp-server.py

Automatically loads .env.local from project root if present.
"""

import json
import sys
import os
import time
import traceback
import uuid
from typing import Any, Dict, List

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

from pathiel import __version__
from pathiel.agents.config_store import read_agent_config
from pathiel.agents.perception import scan_once
from pathiel.client.hl_client import fetch_account_state, resolve_user_address
from pathiel.agents.hyperfeed import (
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
# Empty since 2026-09-06: every advertised tool now has a real handler. The
# mechanism below is kept deliberately — it is the right shape for a tool whose
# endpoint genuinely does not exist yet, and returning a clean not_implemented
# beats fake zeros. What is NOT acceptable is leaving a name here when
# pathiel.client can already answer it, which is how six of these hid working
# capability for months.
_STUB_TOOL_NAMES: List[str] = [
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
        "name": "connected_wallets",
        "description": (
            "Wallets that have signed in to the dashboard with Sign-In With Ethereum, "
            "newest first. READ-ONLY and informational: this server signs with the "
            "deployment's own key, so it can never place, close or size an order on "
            "any wallet listed here. Use it to answer 'whose account am I looking at' "
            "and to feed an address to wallet_account below."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "Max wallets to return (default 10, max 100)."},
            },
        },
    },
    {
        "name": "wallet_account",
        "description": (
            "Read-only Hyperliquid state for any address: equity, withdrawable, and "
            "open positions. Needs no key, because /info clearinghouseState takes a "
            "plain address. Omit `address` to read the wallet most recently signed in "
            "to the dashboard; pass one to read a specific account. An unfunded "
            "address is not an error — it returns funded=false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string",
                            "description": "0x address. Defaults to the most recent dashboard sign-in."},
            },
        },
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
                "runner_entry_gate": {"type": "object", "description": "Runner gate block; partial objects are deep-merged."},
                "runner_mover_surface": {"type": "object", "description": "Large-mover surfacing block; partial objects are deep-merged."},
                "trend_filter_200ma": {"type": "object", "description": "200MA trend filter block; partial objects are deep-merged."},
                "override_volume_confirm": {"type": "object", "description": "Volume-confirm override block; partial objects are deep-merged."},
                "reentry_cap": {"type": "object", "description": "Per-coin reentry cap block; partial objects are deep-merged."},
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
        "description": "Top traders from Hyperliquid's public leaderboard, ranked over a window, with their live positions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_frame": {
                    "type": "string",
                    "enum": [
                        "DAILY",
                        "WEEKLY",
                        "MONTHLY",
                        "ALL_TIME"
                    ],
                    "default": "DAILY"
                },
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "PROFIT_AND_LOSS_UNREALIZED",
                        "RETURN_ON_INVESTMENT"
                    ],
                    "default": "PROFIT_AND_LOSS_UNREALIZED"
                },
                "limit": {
                    "type": "number",
                    "description": "Max traders (default 10, max 100)"
                },
                "open_position_filter": {
                    "type": "boolean",
                    "default": True,
                    "description": "Skip flat accounts"
                },
                "min_account_value": {
                    "type": "number",
                    "description": "Minimum account value in USD (default 0)"
                }
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
        "description": "Top traders with pnl/roi for every window from Hyperliquid's public leaderboard. ROI ranking defaults to a $10k account floor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time_frame": {
                    "type": "string",
                    "enum": [
                        "DAILY",
                        "WEEKLY",
                        "MONTHLY",
                        "ALL_TIME"
                    ],
                    "default": "MONTHLY"
                },
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "PROFIT_AND_LOSS_UNREALIZED",
                        "RETURN_ON_INVESTMENT"
                    ],
                    "default": "RETURN_ON_INVESTMENT"
                },
                "limit": {
                    "type": "number",
                    "description": "Max traders (default 60, max 100)"
                },
                "open_position_filter": {
                    "type": "boolean",
                    "default": True,
                    "description": "Skip flat accounts"
                },
                "min_account_value": {
                    "type": "number",
                    "description": "Minimum account value in USD (default 10000 for ROI, else 0)"
                }
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
        "name": "get_price",
        "description": "Get current mid price for a coin. Also answers get_coin_price.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Coin ticker (default BTC)"},
            },
        },
    },
    {
        "name": "get_candles",
        "description": "Candles for a coin and interval, lookback by count. Also answers "
                       "get_candles_aggregated. For an explicit time window see get_price_history.",
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
        "description": ("Open orders (frontend shape: trigger type, tp/sl), optionally "
                        "filtered by coin. Also answers get_frontend_open_orders, get_user_orders."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "Filter by coin (optional)"},
                "user": {"type": "string",
                        "description": "Account address (optional; defaults to this deployment's)"},
                "limit": {"type": "number", "description": "Max orders to return (default 100)"}
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
        "description": "Get user fee tiers and rates. Also answers get_user_fees_detailed.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_referral",
        "description": "Get referral code and statistics.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_trade_history",
        "description": ("This account's fills, newest last (HL has no public trade tape). "
                        "Also answers get_user_fills, get_recent_trades."),
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Filter by coin (optional)"},
            "limit": {"type": "number", "description": "Max trades to return (default 100)"},
            "user": {"type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"}
        }}
    },
    {
        "name": "get_funding_history",
        "description": "Venue funding rate history for one coin over a window (default last 7 days).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker (default BTC)"
                },
                "startTime": {
                    "type": "number",
                    "description": "Start time, unix ms (default end - 7d)"
                },
                "endTime": {
                    "type": "number",
                    "description": "End time, unix ms (default now)"
                },
                "limit": {
                    "type": "number",
                    "description": "Max entries to return (default 100)"
                }
            }
        }
    },
    {
        "name": "get_l2_book",
        "description": "L2 order book for a coin. Also answers get_market_depth.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (e.g. BTC)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_user_state",
        "description": "Full raw frontend clearinghouse state (main dex only). For a "
                       "normalized cross-dex view use get_asset_positions / get_account_summary.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_user_twist",
        "description": "Get user staking (twist) information.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_withdrawals",
        "description": "This account's withdrawals, from the non-funding ledger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "startTime": {
                    "type": "number",
                    "description": "Start time, unix ms (default: all history)"
                },
                "limit": {
                    "type": "number",
                    "description": "Max entries to return (default 100)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_predicted_funding",
        "description": ("Predicted next funding per venue for one coin, or every main-dex coin "
                        "when omitted. HIP-3 coins (xyz:, vntl:, km:, ...) have no cross-venue "
                        "prediction on HL; this falls back to their current hourly rate, "
                        "clearly labeled as such."),
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (optional)"}
        }}
    },
    {
        "name": "get_asset_context",
        "description": ("Funding, open interest, mark and day volume for one coin (HIP-3-aware: "
                        "xyz:CL, vntl:NVDA, ... route to their own dex), or every main-dex coin "
                        "when omitted. Also answers get_market_stats."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker (optional)"
                }
            }
        }
    },
    {
        "name": "get_user_defined_types",
        "description": "Get user-defined perpetual types.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_price_history",
        "description": "Candles for a coin over a time window (default last 100 hours).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker"
                },
                "interval": {
                    "type": "string",
                    "description": "Candle interval (default 1h)"
                },
                "start_time": {
                    "type": "number",
                    "description": "Start, unix seconds or ms"
                },
                "end_time": {
                    "type": "number",
                    "description": "End, unix seconds or ms (default now)"
                }
            },
            "required": [
                "coin"
            ]
        }
    },
    {
        "name": "get_assets",
        "description": ("Every tradeable perp (main dex) with size decimals and max leverage. "
                        "Also answers get_markets_info, get_perp_markets, get_asset_contexts."),
        "inputSchema": {"type": "object", "properties": {
            "fields": {"type": "array", "items": {"type": "string"},
                      "description": "Return only these keys per asset (optional, trims payload)"}
        }}
    },
    {
        "name": "get_deposits",
        "description": "This account's deposits, from the non-funding ledger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "startTime": {
                    "type": "number",
                    "description": "Start time, unix ms (default: all history)"
                },
                "limit": {
                    "type": "number",
                    "description": "Max entries to return (default 100)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_transfers",
        "description": "This account's internal, spot, sub-account and vault transfers. "
                       "Also answers get_transfer_history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "startTime": {
                    "type": "number",
                    "description": "Start time, unix ms (default: all history)"
                },
                "limit": {
                    "type": "number",
                    "description": "Max entries to return (default 100)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
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
        "name": "get_max_trade_size",
        "description": "Max notional this account can open on a coin, bounded by margin and checked against the book.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker"
                },
                "is_buy": {
                    "type": "boolean",
                    "description": "True to buy (default), False to sell"
                }
            },
            "required": [
                "coin"
            ]
        }
    },
    {
        "name": "get_portfolio_status",
        "description": "Get portfolio status summary.",
        "inputSchema": {"type": "object", "properties": {}}
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
        "description": "All current mid prices, including HIP-3 (xyz:, vntl:, km:, ...). Also answers market_get_mids.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_order_by_oid",
        "description": "Order status by order ID. Also answers get_order_status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                },
                "oid": {
                    "type": "number",
                    "description": "Order ID"
                },
                "orderId": {
                    "type": "number",
                    "description": "Alias of oid"
                }
            },
            "required": [
                "oid"
            ]
        }
    },
    {
        "name": "get_trading_permissions",
        "description": "Get trading permissions for the account.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_account_summary",
        "description": "Equity (main dex + HIP-3), available margin, withdrawable, notional, position count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_asset_positions",
        "description": "Open positions across main dex and HIP-3; pass coin to filter. "
                       "Also answers get_portfolio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker (optional filter)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_24h_stats",
        "description": "Get 24-hour statistics for a coin.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_funding_rate",
        "description": "Current hourly funding for one coin, or the whole board sorted by "
                       "it when omitted.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (optional)"}
        }}
    },
    {
        "name": "get_portfolio_pnl",
        "description": "Unrealized pnl per open position plus HL's day/week/month/all-time pnl.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_risk_metrics",
        "description": "Account leverage, cross margin ratio (1.0 = liquidation) and liquidation distance per main-dex position.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_spot_markets",
        "description": "Get all spot markets.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_historical_funding",
        "description": ("Funding this ACCOUNT paid or received (not the venue rate; see "
                        "get_funding_history). Also answers get_user_funding_history."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker (optional filter)"
                },
                "startTime": {
                    "type": "number",
                    "description": "Start time, unix ms (default: all history)"
                },
                "endTime": {
                    "type": "number",
                    "description": "End time, unix ms (optional)"
                },
                "limit": {
                    "type": "number",
                    "description": "Max entries to return (default 100)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
    },
    {
        "name": "get_open_interest",
        "description": "Open interest for a coin (HIP-3-aware), or the top 100 by OI when omitted.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (optional)"}
        }}
    },
    {
        "name": "get_vault_details",
        "description": "Details for one vault by address.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vaultAddress": {
                    "type": "string",
                    "description": "Vault address"
                }
            },
            "required": [
                "vaultAddress"
            ]
        }
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
        "name": "get_user_orders_history",
        "description": "Get user's order history with filtering.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "number", "description": "Max orders (default 100)"}
        }}
    },
    {
        "name": "get_price_impact",
        "description": "Walk the live L2 book for a trade and report avg fill, worst price, "
                       "slippage and unfilled size. Also answers get_slippage_estimate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker"
                },
                "size": {
                    "type": "number",
                    "description": "Trade size in COIN units"
                },
                "notionalUsd": {
                    "type": "number",
                    "description": "Trade size in USD (overrides size)"
                },
                "is_buy": {
                    "type": "boolean",
                    "description": "True to buy (default), False to sell"
                }
            },
            "required": [
                "coin"
            ]
        }
    },
    {
        "name": "get_withdrawal_status",
        "description": "Whether a withdrawal settled (by tx hash), or the 20 most recent withdrawals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "withdrawal_id": {
                    "type": "string",
                    "description": "Withdrawal tx hash (optional)"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            }
        }
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
        "description": "Venue liquidation price for an open position (pass coin only), or HL's isolated-margin estimate for a hypothetical one (pass leverage + is_long).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "description": "Coin ticker"
                },
                "leverage": {
                    "type": "number",
                    "description": "Leverage (hypothetical position)"
                },
                "is_long": {
                    "type": "boolean",
                    "description": "Side (hypothetical position)"
                },
                "entry_px": {
                    "type": "number",
                    "description": "Entry price (default current mid)"
                },
                "size": {
                    "type": "number",
                    "description": "Position size, echoed back; liquidation price does not depend on it"
                },
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                }
            },
            "required": [
                "coin"
            ]
        }
    },
    {
        "name": "get_max_leverage",
        "description": "Maximum allowed leverage for a coin (HIP-3-aware: xyz:CL, vntl:NVDA, "
                       "... route to their own dex). Also answers get_leverage.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker"}
        }, "required": ["coin"]}
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
        "name": "get_historical_orders",
        "description": "Get historical (filled + cancelled) orders for the configured user.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "query_order_by_cloid",
        "description": "Order status by client order ID (0x-prefixed 16-byte hex).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Account address (optional; defaults to this deployment's)"
                },
                "cloid": {
                    "type": "string",
                    "description": "Client order ID"
                }
            },
            "required": [
                "cloid"
            ]
        }
    },
    # ── desk tools (services.strategies facade; desk-core owns the service) ──
    {
        "name": "desk_list",
        "description": "Every desk's one-line card: tagline, primary coin, mid, 1d return, "
                       "latest call and track record. Start here before desk_brief.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "desk_brief",
        "description": "Full brief for one desk: spec, analytics, intel and latest thesis. "
                       "mode=quick renders a short markdown summary; mode=full returns the "
                       "raw structured payload.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desk": {"type": "string", "description": "Desk id, e.g. \"oil\""},
                "mode": {"type": "string", "enum": ["quick", "full"], "default": "quick"},
            },
            "required": ["desk"],
        },
    },
    {
        "name": "desk_refresh",
        "description": "Kick off (or reuse) a desk's data+thesis refresh. Returns a job you "
                       "can poll; the pipeline skips the LLM step unless the market moved, "
                       "a high-relevance headline arrived, the thesis expired, or force=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desk": {"type": "string", "description": "Desk id, e.g. \"oil\""},
                "force": {"type": "boolean", "default": False,
                         "description": "Rewrite the thesis even if nothing material changed"},
            },
            "required": ["desk"],
        },
    },
    {
        "name": "desk_submit_call",
        "description": "Record your own trade call on a desk (author=user), journaled "
                       "alongside the machine's theses for track-record comparison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desk": {"type": "string", "description": "Desk id, e.g. \"oil\""},
                "call": {
                    "type": "object",
                    "description": "The trade in one shot.",
                    "properties": {
                        "side": {"type": "string", "enum": ["long", "short", "flat", "pair"]},
                        "instrument": {"type": "string",
                                      "description": "Coin, or pair label when side=pair"},
                        "one_liner": {"type": "string", "description": "The whole trade, <=160 chars"},
                        "entry_px": {"type": "number"},
                        "entry_zone": {"type": "array", "items": {"type": "number"},
                                      "description": "[lo, hi]"},
                        "stop_px": {"type": "number"},
                        "targets": {"type": "array", "items": {"type": "number"}},
                        "leverage": {"type": "number"},
                        "confidence": {"type": "number", "description": "0-1"},
                        "invalidation": {"type": "string",
                                        "description": "The observable that kills the idea"},
                    },
                    "required": ["side", "instrument", "one_liner", "confidence", "invalidation"],
                },
                "scenarios": {
                    "type": "array",
                    "description": "Optional probability-weighted outcome scenarios, must sum to 1",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "probability": {"type": "number"},
                            "move_lo_pct": {"type": "number"},
                            "move_hi_pct": {"type": "number"},
                            "description": {"type": "string"},
                            "triggers": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "probability", "move_lo_pct", "move_hi_pct", "description"],
                    },
                },
                "notes": {"type": "string"},
            },
            "required": ["desk", "call"],
        },
    },
    {
        "name": "desk_track_record",
        "description": "Graded-thesis track record for one desk: hit rate, mean Brier, mean "
                       "return, split machine vs user calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desk": {"type": "string", "description": "Desk id, e.g. \"oil\""},
            },
            "required": ["desk"],
        },
    },
]


def handle_scan(params: Dict[str, Any]) -> str:
    from pathiel.agents.config import get_config
    from pathiel.client.universe import get_universe

    try:
        min_score = float(params.get("minScore", 20))
        max_markets = int(params["maxMarkets"]) if params.get("maxMarkets") else None
    except (TypeError, ValueError):
        return json.dumps({"status": "error", "error": "minScore and maxMarkets must be numbers"})
    if max_markets is not None and max_markets < 1:
        return json.dumps({"status": "error", "error": "maxMarkets must be >= 1"})

    # maxMarkets reaches perception through the environment. It used to be set
    # and never restored, so one scan(maxMarkets=5) shrank every later scan in
    # this process to 5 markets.
    prior = os.environ.get("PATHIEL_MAX_MARKETS")
    if max_markets is not None:
        os.environ["PATHIEL_MAX_MARKETS"] = str(max_markets)
    try:
        universe = get_universe()
        results = scan_once(universe=universe, min_score=min_score, config=get_config())
    finally:
        if max_markets is not None:
            if prior is None:
                os.environ.pop("PATHIEL_MAX_MARKETS", None)
            else:
                os.environ["PATHIEL_MAX_MARKETS"] = prior

    # Cache perceptions by coin so research can look them up
    for r in results:
        coin = _norm_coin(r.get("coin", ""))
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
        "equity_by_dex": {k: v for k, v in (account.get("dex_equity") or {}).items() if v},
        "total_notional": account.get("total_ntl", 0),
        "positions": account.get("asset_positions", []),
        "scan_interval_sec": config.get("scan", {}).get("interval", 180),
        "min_composite_score": config.get("scan", {}).get("minCompositeScore", 20),
    })


def _auth_store():
    """The dashboard's auth database, or None where there is not one.

    MCP often runs beside a deployment rather than inside it, so a missing or
    unreadable store is an ordinary state and not a failure to report upward.
    """
    try:
        from services.auth.store import AuthStore
        return AuthStore()
    except Exception:
        return None


def handle_connected_wallets(params: Dict[str, Any]) -> str:
    """Who has signed in to the dashboard.

    Deliberately read-only, and deliberately not wired into any tool that
    signs. This server holds the deployment's own key and no other, so a
    connected wallet is something it can LOOK AT and nothing it can trade —
    which is the security model, not a gap in it. An agent that wants to act on
    one of these accounts cannot, and should say so rather than reaching for
    the house key on the user's behalf.
    """
    limit = max(1, min(100, int(params.get("limit") or 10)))
    store = _auth_store()
    if store is None:
        return json.dumps({"wallets": [], "status": "no auth database reachable"})
    try:
        users = store.list_users()
    except Exception as e:
        return json.dumps({"wallets": [], "status": f"auth database unreadable: {e}"})
    users.sort(key=lambda u: (u.last_seen_at or 0), reverse=True)
    return json.dumps({
        "wallets": [{"address": u.address, "role": u.role,
                     "last_seen_at": u.last_seen_at, "disabled": bool(u.disabled)}
                    for u in users[:limit]],
        "can_this_server_trade_them": False,
        "why_not": ("this server signs with the deployment's own key; a wallet "
                    "that signed in to the dashboard granted a session, not a key"),
    })


def handle_wallet_account(params: Dict[str, Any]) -> str:
    """Read-only Hyperliquid state for one address."""
    address = str(params.get("address") or "").strip()
    if not address:
        store = _auth_store()
        try:
            users = sorted(store.list_users(), key=lambda u: (u.last_seen_at or 0),
                           reverse=True) if store else []
        except Exception:
            users = []
        if not users:
            return json.dumps({"error": "no address given and no wallet has signed in"})
        address = users[0].address
    if not address.startswith("0x") or len(address) != 42:
        return json.dumps({"error": f"malformed address: {address!r}"})
    try:
        # include_hip3: a HIP-3 account (all its margin on xyz:) read as
        # $0.0086 here while `state` said $15.21 for the same wallet, and an
        # agent reported two balances for one account.
        state = fetch_account_state(address, include_hip3=True) or {}
    except Exception as e:
        return json.dumps({"address": address, "status": "unavailable", "error": str(e)})
    equity = float(state.get("equity") or 0.0)
    return json.dumps({
        "address": address,
        "funded": equity > 0,
        "equity": equity,
        "equity_by_dex": {k: v for k, v in (state.get("dex_equity") or {}).items() if v},
        "withdrawable": state.get("withdrawable"),
        "positions": state.get("asset_positions", []),
        "read_only": True,
    })


def handle_config(params: Dict[str, Any]) -> str:
    from pathiel.agents.config_store import (
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
        "dsl_exit", "runner_entry_gate", "runner_mover_surface",
        "trend_filter_200ma", "override_volume_confirm",
        "reentry_cap",
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
    from pathiel.agents.research import research

    coin = _norm_coin(params.get("coin", ""))
    if not coin:
        return json.dumps({"status": "error", "error": "coin is required"})
    
    # Find matching perception from last scan
    perception = _perception_cache.get(coin)
    if not perception:
        # Build a minimal perception from the current mid price.
        from pathiel.client.hl_client import fetch_all_mids
        mids = fetch_all_mids()
        if coin not in mids:
            return json.dumps({"status": "error", "coin": coin,
                               "error": f"unknown coin {coin}: no mid price"})
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
    from pathiel.agents.memory import memory

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
    from pathiel.agents.executor import route_verdict
    from pathiel.agents.memory import memory

    analysis_id = params.get("analysisId", "")
    if not analysis_id:
        return json.dumps({"status": "error", "error": "analysisId is required"})

    # By id over the whole buffer. This scanned only the newest 20, so after a
    # wide research pass an analysis could be "not found" while still held.
    analysis = memory.get_analysis_by_id(analysis_id)

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
    return json.dumps(leaderboard_get_markets(limit=_limit(params, 100, cap=500)))


def handle_leaderboard_get_top_traders(params: Dict[str, Any]) -> str:
    try:
        return json.dumps(leaderboard_get_top_traders(
            time_frame=params.get("time_frame") or "DAILY",
            sort_by=params.get("sort_by") or "PROFIT_AND_LOSS_UNREALIZED",
            limit=_limit(params, 10, cap=100),
            open_position_filter=params.get("open_position_filter", True) is not False,
            min_account_value=_fnum(params.get("min_account_value")),
        ), default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def handle_leaderboard_get_trader_positions(params: Dict[str, Any]) -> str:
    trader_id = params.get("trader_id", "")
    if not trader_id:
        return json.dumps({"status": "error", "error": "trader_id required"})
    return json.dumps(leaderboard_get_trader_positions(trader_id=trader_id))


def handle_discovery_get_top_traders(params: Dict[str, Any]) -> str:
    try:
        mav = params.get("min_account_value")
        return json.dumps(discovery_get_top_traders(
            time_frame=params.get("time_frame") or "MONTHLY",
            sort_by=params.get("sort_by") or "RETURN_ON_INVESTMENT",
            limit=_limit(params, 60, cap=100),
            open_position_filter=params.get("open_position_filter", True) is not False,
            min_account_value=None if mav is None else _fnum(mav),
        ), default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


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



def _tool_handlers() -> Dict[str, Any]:
    """tool name -> handler. Built on first use because the handlers are
    defined below run() in this file."""
    global _TOOL_HANDLERS
    if _TOOL_HANDLERS is not None:
        return _TOOL_HANDLERS
    tool_handlers = {
        "get_assets": handle_get_assets,
        "get_user_defined_types": handle_get_user_defined_types,
        "get_funding_rate": handle_get_funding_rate,
        "get_historical_funding": handle_get_historical_funding,
        "get_trade_history": handle_get_trade_history,
        "get_user_orders_history": handle_get_user_orders_history,
        "get_deposits": handle_get_deposits,
        "get_withdrawals": handle_get_withdrawals,
        "get_transfers": handle_get_transfers,
        "get_withdrawal_status": handle_get_withdrawal_status,
        "get_staking_info": handle_get_staking_info,
        "get_user_twist": handle_get_user_twist,
        "get_rewards": handle_get_rewards,
        "get_user_roles": handle_get_user_roles,
        "get_trading_permissions": handle_get_trading_permissions,
        "get_portfolio_status": handle_get_portfolio_status,
        "get_api_rate_limits": handle_get_api_rate_limits,
        "get_validator_info": handle_get_validator_info,
        "get_network_stats": handle_get_network_stats,
        "get_price_impact": handle_get_price_impact,
        "get_max_trade_size": handle_get_max_trade_size,
        "get_vault_details": handle_get_vault_details,
        "get_funding_history": handle_get_funding_history,
        "get_predicted_funding": handle_get_predicted_funding,
        "get_asset_context": handle_get_asset_context,
        "get_open_interest": handle_get_open_interest,
        "scan": handle_scan,
        "research": handle_research,
        "submit_verdict": handle_submit_verdict,
        "execute": handle_execute,
        "state": handle_state,
        "connected_wallets": handle_connected_wallets,
        "wallet_account": handle_wallet_account,
        "config": handle_config,
        "leaderboard_get_markets": handle_leaderboard_get_markets,
        "leaderboard_get_top_traders": handle_leaderboard_get_top_traders,
        "leaderboard_get_trader_positions": handle_leaderboard_get_trader_positions,
        "discovery_get_top_traders": handle_discovery_get_top_traders,
        "discovery_get_trader_state": handle_discovery_get_trader_state,
        "market_get_asset_data": handle_market_get_asset_data,
        "market_get_funding_regime": handle_market_get_funding_regime,
        "market_list_instruments": handle_market_list_instruments,
        "close_position": handle_close_position,
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
        "get_price_history": handle_get_price_history,
        "get_coin_info": handle_get_coin_info,
        "get_all_mids": handle_get_all_mids,
        "get_account_summary": handle_get_account_summary,
        "get_asset_positions": handle_get_asset_positions,
        "get_24h_stats": handle_get_24h_stats,
        "get_portfolio_pnl": handle_get_portfolio_pnl,
        "get_risk_metrics": handle_get_risk_metrics,
        "get_spot_markets": handle_get_spot_markets,
        "get_server_time": handle_get_server_time,
        "get_liquidation_price": handle_get_liquidation_price,
        "get_max_leverage": handle_get_max_leverage,
        "get_order_by_oid": handle_get_order_by_oid,
        "get_user_fills_by_time": handle_get_user_fills_by_time,
        "get_historical_orders": handle_get_historical_orders,
        "query_order_by_cloid": handle_query_order_by_cloid,
        "desk_list": handle_desk_list,
        "desk_brief": handle_desk_brief,
        "desk_refresh": handle_desk_refresh,
        "desk_submit_call": handle_desk_submit_call,
        "desk_track_record": handle_desk_track_record,
    }

    for _name in _STUB_TOOL_NAMES:
        tool_handlers[_name] = _make_stub_handler(_name)

    # Hidden aliases: old tool names from before the 2026-10-17 consolidation,
    # dispatched to the handler that now covers them but no longer listed in
    # TOOLS (tools/list). Every existing client that calls one of these by
    # name keeps working; new discovery (tools/list) only sees the lean set.
    # A name on the right MUST be a key already in tool_handlers above.
    for _alias, _canonical in ALIASES.items():
        tool_handlers[_alias] = tool_handlers[_canonical]

    _TOOL_HANDLERS = tool_handlers
    return tool_handlers


_TOOL_HANDLERS: "Dict[str, Any] | None" = None

# old tool name -> canonical tool name. See the comment in _tool_handlers().
ALIASES: Dict[str, str] = {
    "get_coin_price": "get_price",
    "market_get_mids": "get_all_mids",
    "get_markets_info": "get_assets",
    "get_perp_markets": "get_assets",
    "get_asset_contexts": "get_assets",
    "get_market_stats": "get_asset_context",
    "get_market_depth": "get_l2_book",
    "get_candles_aggregated": "get_candles",
    "get_user_fees_detailed": "get_user_fees",
    "get_user_fills": "get_trade_history",
    "get_recent_trades": "get_trade_history",
    "get_user_funding_history": "get_historical_funding",
    "get_order_status": "get_order_by_oid",
    "get_portfolio": "get_asset_positions",
    "get_frontend_open_orders": "get_open_orders",
    "get_user_orders": "get_open_orders",
    "get_slippage_estimate": "get_price_impact",
    "get_transfer_history": "get_transfers",
    "get_leverage": "get_max_leverage",
}

# Protocol versions this server speaks. The tools surface is identical across
# them, so a client asking for any of these gets its own version echoed back;
# anything else gets the newest, which the spec lets the client reject.
_SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# JSON-RPC 2.0 error codes.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _payload_is_error(text: str) -> bool:
    """True when a handler's JSON reports failure.

    Handlers return {"error": ...} or {"status": "error"} instead of raising,
    so an agent reads the reason. That payload used to ship with
    isError:false, which told the client the call worked.
    """
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(obj, dict) and (
        obj.get("error") not in (None, "", False) or obj.get("status") == "error")


def _call_tool(name: Any, args: Any) -> Dict[str, Any]:
    handler = _tool_handlers().get(name) if isinstance(name, str) else None
    if handler is None:
        raise KeyError(name)
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": json.dumps(
            {"error": "arguments must be an object"})}], "isError": True}
    try:
        text = handler(args)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        text = json.dumps({"error": f"{type(e).__name__}: {e}", "tool": name})
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return {"content": [{"type": "text", "text": text}],
            "isError": _payload_is_error(text)}


def dispatch_message(msg: Any) -> "Dict[str, Any] | None":
    """One inbound JSON-RPC message -> the response to send, or None.

    Notifications (no "id") never get a response: answering one is a protocol
    violation some clients treat as fatal. Every request with an id gets
    exactly one response, success or error, so a client is never left waiting.
    """
    global _CLIENT_SUPPORTS_SAMPLING
    if not isinstance(msg, dict):
        return _error_msg(None, _INVALID_REQUEST, "message must be a JSON object")
    method = msg.get("method")
    is_notification = "id" not in msg
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if is_notification:
        return None
    if not isinstance(method, str):
        return _error_msg(msg_id, _INVALID_REQUEST, "method must be a string")
    if not isinstance(params, dict):
        return _error_msg(msg_id, _INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        _client_caps = params.get("capabilities") or {}
        _CLIENT_SUPPORTS_SAMPLING = isinstance(_client_caps, dict) and "sampling" in _client_caps
        sys.stderr.write(f"[mcp] client sampling capability: {_CLIENT_SUPPORTS_SAMPLING}\n")
        sys.stderr.flush()
        asked = params.get("protocolVersion")
        version = asked if asked in _SUPPORTED_PROTOCOL_VERSIONS else _SUPPORTED_PROTOCOL_VERSIONS[0]
        return _result_msg(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pathiel", "version": __version__},
        })
    if method == "ping":
        return _result_msg(msg_id, {})
    if method == "tools/list":
        return _result_msg(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        try:
            return _result_msg(msg_id, _call_tool(name, params.get("arguments") or {}))
        except KeyError:
            return _error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}")
    return _error_msg(msg_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


# MCP server loop
def run() -> None:
    global _PROTO_OUT
    # stdout IS the protocol channel. Anything else that prints to it (a stray
    # print() in an engine module, a library banner) splices garbage into the
    # JSON-RPC stream and the client drops the connection. Keep the real
    # stdout for frames and point sys.stdout at stderr for everyone else.
    _PROTO_OUT = sys.stdout
    sys.stdout = sys.stderr
    _tool_handlers()

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _send(_error_msg(None, _PARSE_ERROR, f"Parse error: {e}"))
            continue
        try:
            response = dispatch_message(msg)
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            msg_id = msg.get("id") if isinstance(msg, dict) else None
            response = _error_msg(msg_id, -32603, f"Internal error: {e}")
        if response is not None:
            _send(response)


def handle_get_price(params: Dict[str, Any]) -> str:
    """Handle get_price tool call."""
    from pathiel.client.exchange import get_hl_price
    coin = _norm_coin(params.get('coin', 'BTC'))
    price = get_hl_price(coin)
    return json.dumps({'coin': coin, 'price': price}, default=str)

def handle_get_candles(params: Dict[str, Any]) -> str:
    """Handle get_candles tool call."""
    from pathiel.client.hl_client import fetch_hl_candles
    coin = _norm_coin(params.get('coin', 'BTC'))
    interval = params.get('interval', '1h')
    count = params.get('count', 100)
    candles = fetch_hl_candles(coin, interval, count)
    return json.dumps([c.model_dump() for c in candles], default=str)

def handle_close_position(params: Dict[str, Any]) -> str:
    """Handle close_position tool call."""
    from pathiel.agents.executor import close_position_market
    
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
    from pathiel.client.exchange import set_leverage as set_leverage_fn
    coin = _norm_coin(params.get('coin', 'BTC'))
    leverage = params.get('leverage', 5)
    result = set_leverage_fn(coin, int(leverage))
    return json.dumps(result, default=str)

def handle_get_open_orders(params: Dict[str, Any]) -> str:
    """Open orders (frontend shape: trigger type, tp/sl). Also answers
    get_frontend_open_orders, get_user_orders.

    FIXED 2026-10-17: this read fetch_account_state(user)['open_orders'], a
    key that function never returns (it has no such field), so every call
    silently answered [] regardless of real open orders — a silent lie, not
    an error. The real data is frontendOpenOrders, which get_frontend_open_orders
    and get_user_orders already called correctly; this now does the same.
    """
    user = _user(params)
    coin_filter = _norm_coin(params.get('coin', ''))
    try:
        orders = _info("frontendOpenOrders", user=user) or []
        if coin_filter:
            orders = [o for o in orders if _norm_coin(o.get('coin', '')) == coin_filter]
        return json.dumps({"count": len(orders), "orders": orders[:_limit(params, 100)]},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_cancel_order(params: Dict[str, Any]) -> str:
    """Handle cancel_order tool call."""
    from pathiel.client.exchange import _make_exchange
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
    user = resolve_user_address()
    try:
        info = _limited_info()
        spot_state = info.spot_user_state(user)
        return json.dumps(spot_state.get('balances', []), default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_fees(params: Dict[str, Any]) -> str:
    """Handle get_user_fees tool call."""
    user = resolve_user_address()
    try:
        info = _limited_info()
        fees = info.user_fees(user)
        return json.dumps({"fees": fees}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_referral(params: Dict[str, Any]) -> str:
    """Handle get_referral tool call."""
    user = resolve_user_address()
    try:
        info = _limited_info()
        # `info.referral` does not exist on the SDK's Info class; the real
        # method is query_referral_state. This handler raised on every call.
        referral = info.query_referral_state(user)
        return json.dumps(referral, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


# ── formerly stubs ───────────────────────────────────────────────────────────
# Each of the six below advertised `not_implemented` while the capability sat
# one call away in pathiel.client. That is worse than a missing tool: an agent
# reads "not implemented" as "this data does not exist here" and goes without
# it, or invents a workaround. Audited 2026-09-06 by checking every stub name
# against what hl_client and exchange already expose.


def handle_get_max_leverage(params: Dict[str, Any]) -> str:
    """The venue's max leverage for a coin (HIP-3-aware: xyz:CL, vntl:NVDA, ...
    route to their own dex's meta). Also answers get_leverage.

    FIXED 2026-10-17: this used to re-scan info.meta()["universe"] (main dex
    only) by hand and answer "not found" for every HIP-3 coin. Delegates to
    pathiel.client.exchange.get_max_leverage, which already resolves the
    dex from the coin's namespace prefix, caches per-dex, and refuses to
    silently return a wrong/missing value as 1x.

    Coin maxes differ (BTC 40x, BOME 3x), and sizing that assumes a global
    ceiling silently over-levers the small ones.
    """
    from pathiel.client.exchange import get_max_leverage
    coin = _norm_coin(params.get("coin", "BTC"))
    try:
        return json.dumps({"coin": coin, "max_leverage": get_max_leverage(coin)},
                          default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_funding_history(params: Dict[str, Any]) -> str:
    """Historical funding for a coin over a window, default the last 7 days."""
    from pathiel.client.hl_client import fetch_funding_history
    coin = _norm_coin(params.get("coin", "BTC"))
    try:
        end = int(params.get("endTime") or time.time() * 1000)
        start = int(params.get("startTime") or end - 7 * 86_400_000)
        rows = fetch_funding_history(coin, start, end) or []
        return json.dumps({"coin": coin, "start": start, "end": end,
                           "count": len(rows), "rows": rows[-_limit(params, 100):]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def _coin_dex(coin: str) -> str:
    """The HIP-3 perpDex name for a namespaced coin ("xyz:CL" -> "xyz"), or ""
    for a bare main-dex coin ("BTC" -> "")."""
    return coin.split(":", 1)[0] if ":" in coin else ""


def _asset_contexts(dex: str = "") -> Dict[str, Dict[str, Any]]:
    """coin -> its asset context (funding, open interest, mark, day volume).

    One `metaAndAssetCtxs` POST, optionally scoped to a HIP-3 perpDex via
    `dex` (e.g. "xyz"). The response is a two-element array whose halves are
    positionally aligned — universe[i] describes ctxs[i] — so they are
    zipped rather than indexed independently.

    HIP-3: verified live against /info on 2026-10-17 that a dex-scoped
    metaAndAssetCtxs already returns dex-prefixed names ("xyz:CL", not
    "CL") -- unlike clearinghouseState, which returns bare names for a
    dex-scoped position. No renaming needed here; the bug this fixes was
    simply never passing `dex` at all, so a HIP-3 coin was never in the
    (main-dex-only) map to begin with.
    """
    raw = _info("metaAndAssetCtxs", **({"dex": dex} if dex else {}))
    if not isinstance(raw, list) or len(raw) != 2:
        # Raise, not {}: an empty map makes every caller answer
        # "unknown coin: BTC" for what was a 429 or a timeout.
        raise RuntimeError(
            f"metaAndAssetCtxs unavailable for dex={dex!r} (rate limit or timeout); retry")
    universe = (raw[0] or {}).get("universe") or []
    ctxs = raw[1] or []
    return {a.get("name"): c for a, c in zip(universe, ctxs) if a.get("name")}


def _mid_price(coin: str) -> float:
    """Current mid for one coin, routed to its HIP-3 dex when namespaced.

    `allMids` with no `dex` only covers the main perp universe — verified
    live 2026-10-17 that a HIP-3 coin like "xyz:CL" is simply absent from
    it. This is the same fix as pathiel.client.exchange.get_hl_price,
    applied through this server's own rate-limited `_info` instead of a
    second SDK Info instance.
    """
    mids = _info("allMids", **({"dex": _coin_dex(coin)} if ":" in coin else {})) or {}
    try:
        return float(mids.get(coin) or 0)
    except (TypeError, ValueError):
        return 0.0


def _hip3_predicted_funding(coin: str) -> "Dict[str, Any] | None":
    """Best-available predicted-funding substitute for a HIP-3 coin.

    HL's `predictedFundings` has no cross-venue data for HIP-3 perpDexs —
    there is no other venue to compare against, these are HL-only synthetic
    markets — and verified live 2026-10-17 that it ignores a `dex` param
    entirely (identical main-universe rows come back regardless). Returns
    the dex's own current hourly funding as a single HlPerp-shaped venue
    entry, explicitly labeled as current rather than predicted so a caller
    can't mistake it for the real thing.
    """
    dex = _coin_dex(coin)
    if not dex:
        return None
    try:
        ctx = _asset_contexts(dex)
    except Exception:
        return None
    rate = (ctx.get(coin) or {}).get("funding")
    if rate is None:
        return None
    return {
        "coin": coin,
        "venues": [["HlPerp", {"fundingRate": str(rate), "fundingIntervalHours": 1}]],
        "source": "hip3_current_funding",
        "note": "HIP-3 perpDexs have no predictedFundings coverage on HL; this is "
                "the current hourly funding rate, not a forward prediction.",
    }


def handle_get_asset_context(params: Dict[str, Any]) -> str:
    """Funding, open interest, mark and day volume for one coin, or all.
    Also answers get_market_stats."""
    coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
    try:
        ctx = _asset_contexts(_coin_dex(coin))
        if coin:
            if coin not in ctx:
                return json.dumps({"error": f"unknown coin: {coin}"})
            return json.dumps({"coin": coin, **ctx[coin]}, default=str)
        return json.dumps({"count": len(ctx), "contexts": ctx}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_open_interest(params: Dict[str, Any]) -> str:
    """Open interest, from the same asset contexts (HIP-3-aware). Sorted so
    the crowded names are at the top rather than buried in an alphabetical
    list."""
    coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
    try:
        ctx = _asset_contexts(_coin_dex(coin))
        if coin:
            if coin not in ctx:
                return json.dumps({"error": f"unknown coin: {coin}"})
            return json.dumps({"coin": coin,
                               "open_interest": ctx[coin].get("openInterest")},
                              default=str)
        rows = []
        for c, v in ctx.items():
            try:
                rows.append({"coin": c, "open_interest": float(v.get("openInterest") or 0)})
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda r: -r["open_interest"])
        return json.dumps({"count": len(rows), "rows": rows[:100]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_predicted_funding(params: Dict[str, Any]) -> str:
    """Predicted next funding per venue, straight from /info. Falls back to
    the current hourly rate for HIP-3 coins, which HL does not predict."""
    coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
    try:
        raw = _info("predictedFundings") or []
        if coin:
            for entry in raw:
                if isinstance(entry, list) and entry and entry[0] == coin:
                    return json.dumps({"coin": coin, "venues": entry[1]}, default=str)
            hip3 = _hip3_predicted_funding(coin)
            if hip3 is not None:
                return json.dumps(hip3, default=str)
            return json.dumps({"error": f"no predicted funding for {coin}"})
        return json.dumps({"count": len(raw), "rows": raw}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


# ── formerly stubs, batch two (2026-09-06) ───────────────────────────────────
# Every /info request type below was probed against the live venue before the
# handler was written. The ones with no endpoint were DELETED rather than left
# stubbed: a permanently unimplementable tool is a promise the venue cannot
# keep, and it costs an agent a call to find that out.


def _user(params: "Dict[str, Any] | None" = None) -> str:
    """The address to query: params["user"] when given, else this deployment's."""
    from pathiel.client.hl_client import resolve_user_address
    given = str((params or {}).get("user") or "").strip()
    return given or resolve_user_address()


class _LimitedInfo:
    """The slice of hyperliquid-python-sdk's Info these handlers use, routed
    through `_info`.

    The SDK's Info posts on its own session, outside pathiel's token bucket,
    so a burst of these tools drew 429s that the rate-limited calls next to
    them never saw. Same method names and return shapes; the request types
    are the ones the SDK sends.
    """

    def all_mids(self):
        return _info("allMids")

    def meta(self):
        return _info("meta")

    def spot_meta(self):
        return _info("spotMeta")

    def l2_snapshot(self, coin: str):
        return _info("l2Book", coin=coin)

    def user_state(self, user: str):
        return _info("clearinghouseState", user=user)

    def spot_user_state(self, user: str):
        return _info("spotClearinghouseState", user=user)

    def user_fees(self, user: str):
        return _info("userFees", user=user)

    def query_referral_state(self, user: str):
        return _info("referral", user=user)

    def frontend_open_orders(self, user: str):
        return _info("frontendOpenOrders", user=user)

    def user_fills(self, user: str):
        return _info("userFills", user=user)

    def user_fills_by_time(self, user: str, start: int, end: "int | None" = None):
        kw = {"user": user, "startTime": start}
        if end is not None:
            kw["endTime"] = end
        return _info("userFillsByTime", **kw)

    def user_funding_history(self, user: str, start: int, end: "int | None" = None):
        kw = {"user": user, "startTime": start}
        if end is not None:
            kw["endTime"] = end
        return _info("userFunding", **kw)

    def historical_orders(self, user: str):
        return _info("historicalOrders", user=user)


def _limited_info() -> _LimitedInfo:
    return _LimitedInfo()


def _limit(params: Dict[str, Any], default: int, cap: int = 2000) -> int:
    """A positive row limit from params["limit"], clamped to `cap`."""
    try:
        n = int(params.get("limit") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(cap, n))


def _info(typ: str, **kw) -> Any:
    """POST /info. Raises on a request that never landed (rate budget, 429,
    timeout) so a handler reports the failure instead of "0 rows"."""
    from pathiel.client.hl_client import _http_post_strict
    return _http_post_strict("/info", {"type": typ, **kw})


def _ledger(kinds: tuple, params: Dict[str, Any]) -> str:
    """userNonFundingLedgerUpdates, filtered by delta type.

    Deposits, withdrawals and transfers are one endpoint distinguished only by
    `delta.type`, so they share this rather than three near-identical handlers
    drifting apart.
    """
    try:
        start = int(params.get("startTime") or 0)
        rows = _info("userNonFundingLedgerUpdates", user=_user(params), startTime=start) or []
        hits = [r for r in rows
                if str(((r or {}).get("delta") or {}).get("type", "")) in kinds]
        return json.dumps({"kinds": list(kinds), "count": len(hits),
                           "rows": hits[-_limit(params, 100):]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def _walk_book(coin: str, usd: float, is_buy: bool) -> Dict[str, Any]:
    """Walk the L2 book for `usd` of notional and report what it really costs.

    The mid is what a quote screen shows; this is what the fill looks like. On a
    thin alt the difference is the whole trade, which is why sizing that assumes
    the mid silently over-sizes exactly the coins it should not.
    """
    book = _info("l2Book", coin=coin) or {}
    levels = book.get("levels") or []
    if len(levels) != 2:
        raise ValueError(f"no book for {coin}")
    # levels[0] is bids, levels[1] asks. A buy lifts asks; a sell hits bids.
    side = levels[1] if is_buy else levels[0]
    if not side:
        raise ValueError(f"empty {'ask' if is_buy else 'bid'} side for {coin}")
    best = float(side[0]["px"])
    remaining, spend, filled = usd, 0.0, 0.0
    worst = best
    for lvl in side:
        px, sz = float(lvl["px"]), float(lvl["sz"])
        avail = px * sz
        take = min(avail, remaining)
        if take <= 0:
            break
        spend += take
        filled += take / px
        worst = px
        remaining -= take
        if remaining <= 0:
            break
    avg = spend / filled if filled else best
    return {
        "coin": coin, "side": "buy" if is_buy else "sell",
        "notional_usd": usd,
        "best_px": best, "avg_fill_px": round(avg, 8), "worst_px": worst,
        "slippage_pct": round((avg / best - 1) * 100 * (1 if is_buy else -1), 4),
        "filled_usd": round(spend, 2),
        # Unfilled notional is the honest part: the book ran out before the
        # size did, and a caller that ignores this thinks it got a price.
        "unfilled_usd": round(max(0.0, remaining), 2),
        "book_exhausted": remaining > 0,
    }


def handle_get_assets(params: Dict[str, Any]) -> str:
    """Every tradeable perp (main dex) with its size decimals and max
    leverage. Also answers get_markets_info, get_perp_markets,
    get_asset_contexts. HIP-3 coins aren't in this bulk listing (an extra
    HTTP call per dex); look one up with get_asset_context/get_max_leverage."""
    try:
        meta = _info("meta") or {}
        uni = meta.get("universe") or []
        fields = params.get("fields")
        if isinstance(fields, list) and fields:
            keep = [str(f) for f in fields]
            uni = [{k: a.get(k) for k in keep} for a in uni]
        return json.dumps({"count": len(uni), "assets": uni}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_user_defined_types(params: Dict[str, Any]) -> str:
    """HIP-3 user-defined perp dexes — the `xyz:` style venues."""
    try:
        dexes = _info("perpDexs") or []
        return json.dumps({"count": len(dexes), "dexes": dexes}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_funding_rate(params: Dict[str, Any]) -> str:
    """Current hourly funding for one coin (HIP-3-aware), or the whole
    main-dex board sorted by it when omitted."""
    coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
    try:
        ctx = _asset_contexts(_coin_dex(coin))
        if coin:
            if coin not in ctx:
                return json.dumps({"error": f"unknown coin: {coin}"})
            return json.dumps({"coin": coin, "funding": ctx[coin].get("funding")},
                              default=str)
        rows = []
        for c, v in ctx.items():
            try:
                rows.append({"coin": c, "funding": float(v.get("funding") or 0)})
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda r: -r["funding"])
        return json.dumps({"count": len(rows), "rows": rows}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_historical_funding(params: Dict[str, Any]) -> str:
    """Funding this ACCOUNT actually paid or received. Distinct from
    get_funding_history, which is the venue rate for a coin. Also answers
    get_user_funding_history."""
    try:
        start = int(params.get("startTime") if params.get("startTime") is not None
                    else params.get("start_time") or 0)
        end = params.get("endTime") if params.get("endTime") is not None else params.get("end_time")
        kw: Dict[str, Any] = {"user": _user(params), "startTime": start}
        if end is not None:
            kw["endTime"] = int(end)
        rows = _info("userFunding", **kw) or []
        coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
        if coin:
            rows = [r for r in rows
                    if _norm_coin(str(((r or {}).get("delta") or {}).get("coin", ""))) == coin]
        return json.dumps({"coin": coin or "all", "count": len(rows),
                           "rows": rows[-_limit(params, 100):]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_trade_history(params: Dict[str, Any]) -> str:
    """This account's fills, newest last (HL has no public trade tape). Also
    answers get_user_fills, get_recent_trades.

    FIXED 2026-10-17: called `_user()` with no params, so a `user` override
    was silently ignored even once advertised.
    """
    coin = _norm_coin(params.get("coin", "")) if params.get("coin") else ""
    try:
        rows = _info("userFills", user=_user(params)) or []
        if coin:
            rows = [r for r in rows if r.get("coin") == coin]
        return json.dumps({"coin": coin or "all", "count": len(rows),
                           "fills": rows[-_limit(params, 100):]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_user_orders_history(params: Dict[str, Any]) -> str:
    """Historical orders, reconstructed from fills.

    HL exposes no order-history endpoint; fills are the durable record. Grouped
    by oid so partial fills read as one order rather than several.
    """
    try:
        rows = _info("userFills", user=_user()) or []
        by_oid: Dict[Any, Dict[str, Any]] = {}
        for f in rows:
            oid = f.get("oid")
            g = by_oid.setdefault(oid, {"oid": oid, "coin": f.get("coin"),
                                        "side": f.get("side"), "fills": 0,
                                        "sz": 0.0, "first_ts": f.get("time")})
            g["fills"] += 1
            try:
                g["sz"] += float(f.get("sz") or 0)
            except (TypeError, ValueError):
                pass
            g["last_ts"] = f.get("time")
        out = sorted(by_oid.values(), key=lambda g: g.get("last_ts") or 0)
        return json.dumps({"count": len(out), "orders": out[-_limit(params, 100):]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_deposits(params: Dict[str, Any]) -> str:
    return _ledger(("deposit",), params)


def handle_get_withdrawals(params: Dict[str, Any]) -> str:
    return _ledger(("withdraw",), params)


def handle_get_transfers(params: Dict[str, Any]) -> str:
    return _ledger(("internalTransfer", "accountClassTransfer", "spotTransfer",
                    "subAccountTransfer", "vaultTransfer"), params)




def handle_get_withdrawal_status(params: Dict[str, Any]) -> str:
    """The most recent withdrawals. HL settles these on-chain, so there is no
    per-request status endpoint — presence in the ledger IS the confirmation."""
    try:
        rows = _info("userNonFundingLedgerUpdates", user=_user(params), startTime=0) or []
        w = [r for r in rows
             if str(((r or {}).get("delta") or {}).get("type", "")) == "withdraw"]
        wid = str(params.get("withdrawal_id") or params.get("hash") or "").strip().lower()
        if wid:
            hit = [r for r in w if str((r or {}).get("hash", "")).lower() == wid]
            return json.dumps({"withdrawal_id": wid, "found": bool(hit),
                               "status": "settled" if hit else "not in ledger",
                               "note": "presence in the ledger is the confirmation; "
                                       "HL has no per-withdrawal status endpoint",
                               "row": hit[0] if hit else None}, default=str)
        return json.dumps({"note": "presence in the ledger is the confirmation; "
                                   "HL has no per-withdrawal status endpoint",
                           "count": len(w), "recent": w[-20:]}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_staking_info(params: Dict[str, Any]) -> str:
    """Delegated stake and its summary."""
    try:
        return json.dumps({"summary": _info("delegatorSummary", user=_user()),
                           "delegations": _info("delegations", user=_user())},
                          default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_user_twist(params: Dict[str, Any]) -> str:
    """Per-validator delegations."""
    try:
        rows = _info("delegations", user=_user()) or []
        return json.dumps({"count": len(rows), "delegations": rows}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_rewards(params: Dict[str, Any]) -> str:
    """Staking rewards, from the delegator summary. HL has no trading-rewards
    endpoint, so this is staking only and says so."""
    try:
        s = _info("delegatorSummary", user=_user()) or {}
        return json.dumps({"scope": "staking rewards only — HL exposes no "
                                    "trading-rewards endpoint", **s}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_user_roles(params: Dict[str, Any]) -> str:
    try:
        return json.dumps(_info("userRole", user=_user()), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_trading_permissions(params: Dict[str, Any]) -> str:
    """Role plus fee tier — what this account is actually allowed and charged."""
    try:
        return json.dumps({"role": _info("userRole", user=_user()),
                           "fees": _info("userFees", user=_user())}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_portfolio_status(params: Dict[str, Any]) -> str:
    """Account value and PnL history across HL's own windows."""
    try:
        return json.dumps(_info("portfolio", user=_user()), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_api_rate_limits(params: Dict[str, Any]) -> str:
    """Request budget left. Worth reading before a wide scan — this repo has a
    history of scans dying on 429s that looked like a quiet market."""
    try:
        return json.dumps(_info("userRateLimit", user=_user()), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_validator_info(params: Dict[str, Any]) -> str:
    try:
        rows = _info("validatorSummaries") or []
        return json.dumps({"count": len(rows), "validators": rows}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_network_stats(params: Dict[str, Any]) -> str:
    """Aggregate validator picture — the closest thing /info offers to a
    network view."""
    try:
        rows = _info("validatorSummaries") or []
        total = 0.0
        active = 0
        for v in rows:
            try:
                total += float(v.get("stake") or 0)
            except (TypeError, ValueError):
                pass
            if v.get("isActive"):
                active += 1
        return json.dumps({"validators": len(rows), "active": active,
                           "total_stake": total,
                           "perp_dexes": len(_info("perpDexs") or [])}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_price_impact(params: Dict[str, Any]) -> str:
    """What a given notional actually costs against the current book
    (HIP-3-aware). Also answers get_slippage_estimate."""
    coin = _norm_coin(params.get("coin", "BTC"))
    try:
        is_buy = _is_buy(params)
        if params.get("notionalUsd") is not None:
            usd = float(params["notionalUsd"])
        elif params.get("size") is not None:
            # `size` is in COIN units, as the schema says. It used to be read
            # as USD, so 0.5 BTC was priced as a $0.50 order: zero impact on
            # every request, on exactly the tool meant to warn about impact.
            # And bare allMids() misses HIP-3 coins entirely (_mid_price
            # routes to the coin's own dex), so a HIP-3 size conversion used
            # to always answer "no mid for xyz:CL".
            mid = _mid_price(coin)
            if mid <= 0:
                return json.dumps({"error": f"no mid for {coin}; cannot convert size to notional"})
            usd = float(params["size"]) * mid
        else:
            usd = 1000.0
        if usd <= 0:
            return json.dumps({"error": "size/notionalUsd must be positive"})
        out = _walk_book(coin, usd, is_buy)
        if params.get("size") is not None and params.get("notionalUsd") is None:
            out["size_coin"] = float(params["size"])
        return json.dumps(out, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def _is_buy(params: Dict[str, Any]) -> bool:
    """Trade direction from `is_buy` (bool, the schema field) or `side`."""
    if params.get("is_buy") is not None:
        v = params["is_buy"]
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "buy")
    return str(params.get("side", "buy")).lower() not in ("sell", "short")


def handle_get_max_trade_size(params: Dict[str, Any]) -> str:
    """How much this account could put on, bounded by BOTH margin and the book.

    Equity alone is the wrong answer on a thin alt: the size that fits the
    margin can be several percent of slippage away from the mid.
    """
    from pathiel.agents.config_store import read_agent_config
    coin = _norm_coin(params.get("coin", "BTC"))
    try:
        cfg = read_agent_config() or {}
        state = _info("clearinghouseState", user=_user()) or {}
        equity = float((state.get("marginSummary") or {}).get("accountValue") or 0)
        free_pct = float(cfg.get("min_available_margin_pct", 0.10) or 0.0)
        lev = int(cfg.get("leverage", 1) or 1)
        by_margin = max(0.0, equity * (1 - free_pct)) * lev
        depth = _walk_book(coin, by_margin, _is_buy(params)) if by_margin > 0 else {}
        return json.dumps({
            "coin": coin, "equity": round(equity, 2),
            "max_notional_by_margin": round(by_margin, 2),
            "leverage_ceiling": lev,
            "book_check": depth,
        }, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_vault_details(params: Dict[str, Any]) -> str:
    """One vault by address. HL has no list-all-vaults endpoint, which is why
    get_vaults was removed rather than left stubbed."""
    addr = params.get("vaultAddress") or params.get("vault") or params.get("address")
    if not addr:
        return json.dumps({"error": "vaultAddress is required"})
    try:
        got = _info("vaultDetails", vaultAddress=str(addr))
        if got is None:
            # HL answers a valid request for an unknown vault with a bare null.
            # Returning that verbatim makes "no such vault" indistinguishable
            # from "the call failed" to anything reading the response.
            return json.dumps({"error": "no such vault", "vaultAddress": str(addr)})
        return json.dumps(got, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def handle_get_l2_book(params: Dict[str, Any]) -> str:
    """L2 order book for a coin. Also answers get_market_depth."""
    coin = _norm_coin(params.get('coin', 'BTC'))
    try:
        info = _limited_info()
        # L2 book snapshot
        book = info.l2_snapshot(coin)
        return json.dumps(book, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_user_state(params: Dict[str, Any]) -> str:
    """Handle get_user_state tool call."""
    user = resolve_user_address()
    try:
        info = _limited_info()
        # `frontend_user_state` does not exist in ANY hyperliquid-python-sdk
        # version checked back to 0.5.0, so this handler raised on every call
        # and returned an {'error': ...} payload that looked like an API
        # failure. `user_state` is the real method and returns the same
        # clearinghouse state the rest of this repo reads.
        state = info.user_state(user)
        return json.dumps(state, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


# The real stdout, captured by run() before it redirects sys.stdout to stderr.
# None outside run() (tests, imports), where sys.stdout is used as-is.
_PROTO_OUT = None


def _out():
    return _PROTO_OUT if _PROTO_OUT is not None else sys.stdout


def _send(msg: Dict[str, Any]) -> None:
    out = _out()
    out.write(json.dumps(msg, default=str) + "\n")
    out.flush()


def _result_msg(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_msg(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def write_response(msg_id: Any, result: Dict[str, Any]) -> None:
    _send(_result_msg(msg_id, result))


def _server_request(method: str, params: Dict[str, Any], timeout_s: float = 120.0) -> Dict[str, Any]:
    """Send a server -> client JSON-RPC request and block for its response.

    Safe to call from inside a tools/call handler: the main loop is parked in the
    handler, so reading stdin here consumes the client's reply without racing it.
    Non-matching messages (notifications, stray requests) are skipped.
    """
    global _server_request_seq
    _server_request_seq += 1
    req_id = f"srv-{_server_request_seq}"
    _send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
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
        if isinstance(msg.get("method"), str) and "id" in msg:
            # A client request that arrived while this one is parked. It still
            # needs exactly one reply or the client waits on it forever.
            if msg["method"] == "ping":
                _send(_result_msg(msg["id"], {}))
            else:
                _send(_error_msg(msg["id"], -32603,
                                 f"server busy awaiting {method}; retry"))
            continue
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
            "maxTokens": int(os.environ.get("PATHIEL_MCP_SAMPLING_MAX_TOKENS", "2048")),
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
    return os.environ.get("PATHIEL_MCP_DISABLE_SAMPLING", "").strip().lower() in (
        "1", "true", "yes",
    )


def _research_brain():
    """Sampling brain when the harness can be the model, else None (configured provider)."""
    if _CLIENT_SUPPORTS_SAMPLING and not _sampling_disabled():
        return _McpSamplingBrain()
    return None


def handle_get_price_history(params: Dict[str, Any]) -> str:
    """Candles over [start_time, end_time] (unix seconds or ms), default the
    last 100 hours. For a lookback-by-count view see get_candles."""
    coin = _norm_coin(params.get('coin', 'BTC'))
    interval = str(params.get('interval') or '1h')
    try:
        def _ms(v: Any) -> int:
            n = int(float(v))
            return n if n > 10**11 else n * 1000
        end = _ms(params["end_time"]) if params.get("end_time") else int(time.time() * 1000)
        start = _ms(params["start_time"]) if params.get("start_time") else end - 100 * 3_600_000
        if start >= end:
            return json.dumps({'error': 'start_time must be before end_time'})
        raw = _info("candleSnapshot", req={"coin": coin, "interval": interval,
                                           "startTime": start, "endTime": end})
        if not isinstance(raw, list):
            return json.dumps({'error': f'no candles for {coin}'})

        def _f(v: Any) -> "float | None":
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        # Trim the venue's raw candle (t,T,s,i,o,c,h,l,v,n) down to the same
        # lean {t,o,h,l,c,v} shape get_candles already uses: T/s/i are
        # redundant with the request, and dropping them plus the string ->
        # float conversion materially shrinks a multi-hundred-row response.
        rows = [{"t": r.get("t"), "o": _f(r.get("o")), "h": _f(r.get("h")),
                "l": _f(r.get("l")), "c": _f(r.get("c")), "v": _f(r.get("v"))}
               for r in raw]
        return json.dumps({'coin': coin, 'interval': interval, 'start': start, 'end': end,
                           'count': len(rows), 'candles': rows}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_coin_info(params: Dict[str, Any]) -> str:
    """Handle get_coin_info tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from pathiel.client.exchange import get_coin_index
        idx, _, _ = get_coin_index(coin)
        return json.dumps({'coin': coin, 'index': idx}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)

def handle_get_all_mids(params: Dict[str, Any]) -> str:
    """All current mid prices, including HIP-3 (xyz:, vntl:, km:, ...). Also
    answers market_get_mids.

    FIXED 2026-10-17: used to call the SDK's bare info.all_mids() (main dex
    only), so every HIP-3 coin was simply missing. Delegates to hyperfeed's
    market_get_mids(), which already merges allMids(dex=...) per registered
    HIP-3 dex.
    """
    try:
        return json.dumps({'mids': market_get_mids()}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def _clearinghouse(params: Dict[str, Any]) -> Dict[str, Any]:
    state = _info("clearinghouseState", user=_user(params))
    if not isinstance(state, dict):
        raise RuntimeError("clearinghouseState returned no data")
    return state


def _fnum(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _positions(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p.get("position") or {} for p in (state.get("assetPositions") or [])
            if _fnum((p.get("position") or {}).get("szi")) != 0]


def handle_get_account_summary(params: Dict[str, Any]) -> str:
    """Equity, margin, withdrawable and position count, main dex plus HIP-3.

    These three tools (summary / pnl / risk) returned a hard-coded {} for
    their whole lives: they called an SDK method that never existed and then
    had the call deleted instead of replaced. They now read the clearinghouse.
    """
    try:
        user = _user(params)
        acct = fetch_account_state(user, include_hip3=True) or {}
        main = _clearinghouse(params)
        ms = main.get("marginSummary") or {}
        return json.dumps({"summary": {
            "user": user,
            "equity": acct.get("equity", 0),
            "equity_by_dex": acct.get("dex_equity", {}),
            "available_margin": acct.get("available", 0),
            "withdrawable": _fnum(main.get("withdrawable")),
            "total_notional": acct.get("total_ntl", 0),
            "total_margin_used": _fnum(ms.get("totalMarginUsed")),
            "position_count": len(acct.get("asset_positions") or []),
        }}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_asset_positions(params: Dict[str, Any]) -> str:
    """Open positions across main dex and HIP-3, optionally one coin."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        acct = fetch_account_state(_user(params), include_hip3=True) or {}
        positions = [p.get("position") or {} for p in (acct.get("asset_positions") or [])]
        if coin:
            positions = [p for p in positions if _norm_coin(p.get('coin', '')) == coin]
        return json.dumps({'coin': coin, 'count': len(positions),
                           'positions': positions}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_24h_stats(params: Dict[str, Any]) -> str:
    """Handle get_24h_stats tool call."""
    coin = _norm_coin(params.get('coin', ''))
    try:
        from pathiel.client.hl_client import fetch_hl_candles
        # Get 24h of 1h candles for stats
        candles = fetch_hl_candles(coin, '1h', 24)
        return json.dumps({'coin': coin, 'stats_24h': [c.model_dump() for c in candles]}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_portfolio_pnl(params: Dict[str, Any]) -> str:
    """Unrealized pnl per open position plus HL's own day/week/month/all-time pnl."""
    try:
        acct = fetch_account_state(_user(params), include_hip3=True) or {}
        open_rows = []
        for p in acct.get("asset_positions") or []:
            pos = p.get("position") or {}
            open_rows.append({"coin": pos.get("coin"), "szi": _fnum(pos.get("szi")),
                              "unrealized_pnl": _fnum(pos.get("unrealizedPnl")),
                              "return_on_equity": _fnum(pos.get("returnOnEquity"))})
        windows: Dict[str, Any] = {}
        hist = _info("portfolio", user=_user(params)) or []
        for pair in hist:
            if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], dict):
                series = pair[1].get("pnlHistory") or []
                # pnlHistory is cumulative within the window; the last point is the window's pnl.
                windows[pair[0]] = _fnum(series[-1][1]) if series else 0.0
        return json.dumps({"pnl": {
            "unrealized_total": round(sum(r["unrealized_pnl"] for r in open_rows), 6),
            "open_positions": open_rows,
            "window_pnl": windows,
        }}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_risk_metrics(params: Dict[str, Any]) -> str:
    """Account leverage, cross maintenance-margin ratio, and liquidation distance
    per main-dex position. margin_ratio is HL's own liquidation trigger: at 1.0
    the account is liquidated."""
    try:
        main = _clearinghouse(params)
        cross = main.get("crossMarginSummary") or {}
        equity = _fnum(cross.get("accountValue"))
        mids = _info("allMids") or {}
        rows = []
        for pos in _positions(main):
            coin = pos.get("coin")
            mark = _fnum(mids.get(coin))
            liq = _fnum(pos.get("liquidationPx")) if pos.get("liquidationPx") else None
            rows.append({
                "coin": coin, "szi": _fnum(pos.get("szi")), "mark": mark,
                "leverage": (pos.get("leverage") or {}).get("value"),
                "liquidation_px": liq,
                "liq_distance_pct": round(abs(mark - liq) / mark * 100, 3) if liq and mark else None,
            })
        mm = _fnum(main.get("crossMaintenanceMarginUsed"))
        return json.dumps({"risk": {
            "equity": equity,
            "total_notional": _fnum(cross.get("totalNtlPos")),
            "account_leverage": round(_fnum(cross.get("totalNtlPos")) / equity, 4) if equity else None,
            "cross_maintenance_margin_used": mm,
            "margin_ratio": round(mm / equity, 6) if equity else None,
            "positions": rows,
            "scope": "main dex; HIP-3 dexes margin separately",
        }}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_spot_markets(params: Dict[str, Any]) -> str:
    """Handle get_spot_markets tool call."""
    try:
        info = _limited_info()
        spot_meta = info.spot_meta()
        return json.dumps({'spot_markets': spot_meta}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_server_time(params: Dict[str, Any]) -> str:
    """Handle get_server_time tool call."""
    try:
        import time
        return json.dumps({'server_time': int(time.time() * 1000)}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_liquidation_price(params: Dict[str, Any]) -> str:
    """Liquidation price: the venue's own figure for an open position, else
    HL's isolated-margin formula for a hypothetical one.

    liq = entry - side * (entry / leverage - mmr * entry) / (1 - mmr * side),
    HL's documented formula: margin available is initial margin minus
    maintenance margin, and mmr is 1 / (2 * max_leverage). The old estimate,
    entry * (1 - 1/leverage), ignored maintenance margin and put liquidation
    further away than it is, which is the dangerous direction to be wrong.
    """
    coin = _norm_coin(params.get('coin', ''))
    if not coin:
        return json.dumps({'error': 'coin is required'})
    try:
        if params.get("leverage") is None and params.get("is_long") is None:
            acct = fetch_account_state(_user(params), include_hip3=True) or {}
            for p in acct.get("asset_positions") or []:
                pos = p.get("position") or {}
                if _norm_coin(pos.get("coin", "")) == coin:
                    return json.dumps({"coin": coin, "source": "venue",
                                       "szi": _fnum(pos.get("szi")),
                                       "entry_px": _fnum(pos.get("entryPx")),
                                       "liquidation_price": pos.get("liquidationPx")},
                                      default=str)
            return json.dumps({"error": f"no open {coin} position; pass leverage and "
                                        "is_long to estimate a hypothetical one", "coin": coin})
        leverage = _fnum(params.get("leverage"))
        if leverage <= 0:
            return json.dumps({"error": "leverage must be positive", "coin": coin})
        is_long = params.get("is_long")
        is_long = is_long if isinstance(is_long, bool) else str(is_long).lower() in ("1", "true", "yes", "long")
        from pathiel.client.exchange import get_max_leverage
        max_lev = _fnum(get_max_leverage(coin)) or leverage
        if leverage > max_lev:
            return json.dumps({"error": f"leverage {leverage:g}x exceeds {coin} max {max_lev:g}x",
                               "coin": coin})
        # _mid_price (not a bare allMids lookup) routes a HIP-3 coin to its
        # own dex -- bare allMids() has no HIP-3 coverage at all, which is
        # exactly why this previously failed for every xyz:/vntl:/km: coin
        # unless the caller supplied entry_px by hand.
        entry = _fnum(params.get("entry_px")) or _mid_price(coin)
        if entry <= 0:
            return json.dumps({"error": f"no price for {coin}", "coin": coin})
        side = 1.0 if is_long else -1.0
        mmr = 1.0 / (2.0 * max_lev)
        liq = round(entry - side * (entry / leverage - mmr * entry) / (1.0 - mmr * side), 6)
        return json.dumps({'coin': coin, 'source': 'estimate', 'size': params.get('size'),
                           'leverage': leverage, 'is_long': bool(is_long), 'entry_px': entry,
                           'maintenance_margin_rate': mmr,
                           'liquidation_price': liq,
                           'note': 'isolated-margin formula; cross positions liquidate on '
                                   'account equity, so use get_risk_metrics for those'},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_order_by_oid(params: Dict[str, Any]) -> str:
    """Order status by order ID. Also answers get_order_status."""
    oid = params.get('oid')
    if oid is None:
        oid = params.get('orderId')
    if oid is None:
        return json.dumps({'error': 'oid is required'})
    user = _user(params)
    if not user:
        return json.dumps({'error': 'no user address provided or configured'})
    try:
        # Was info.query_order_by_oid(params.get('user', ''), ...): an omitted
        # user went to HL as "" and came back as a 422 deserialization error.
        res = _info("orderStatus", user=user, oid=int(oid))
        return json.dumps({'order': res, 'user': user}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_get_user_fills_by_time(params: Dict[str, Any]) -> str:
    """Handle get_user_fills_by_time tool call."""
    start_time = int(params.get('start_time', 0))
    end_time = params.get('end_time')
    try:
        from pathiel.client.exchange import HL_ACCOUNT
        info = _limited_info()
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


def handle_get_historical_orders(params: Dict[str, Any]) -> str:
    """Handle get_historical_orders tool call."""
    try:
        from pathiel.client.exchange import HL_ACCOUNT
        info = _limited_info()
        if not HL_ACCOUNT:
            return json.dumps({'error': 'no configured user address'}, default=str)
        orders = info.historical_orders(HL_ACCOUNT)
        return json.dumps({'orders': orders, 'count': len(orders) if isinstance(orders, list) else 0},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def handle_query_order_by_cloid(params: Dict[str, Any]) -> str:
    """Handle query_order_by_cloid tool call."""
    cloid = str(params.get('cloid') or '').strip()
    if not cloid:
        return json.dumps({'error': 'cloid is required'})
    addr = _user(params)
    if not addr:
        return json.dumps({'error': 'no user address provided or configured'}, default=str)
    try:
        # orderStatus takes a cloid in the oid slot. The SDK wrapper wanted a
        # Cloid object and crashed on the string every caller passes.
        res = _info("orderStatus", user=addr, oid=cloid)
        return json.dumps({'order': res, 'cloid': cloid, 'user': addr}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


# ── desk tools ────────────────────────────────────────────────────────────
# Client of services.strategies.facade (owned by the desk-core agent, built
# in parallel; it does not exist in this worktree). Every number an agent
# sees here was computed by that service's DeskAnalytics/IntelPacket — this
# file only renders what the facade returns, per the contract in
# contracts/strategies/models.py. Tested against a fake facade.


def _strategies_facade():
    """Import services.strategies.facade lazily so a missing desk-core
    service degrades every desk_* tool to a clear error instead of an
    import-time crash of the whole MCP server. desk-core is a separate,
    parallel-owned service that may not exist yet in every worktree/deploy
    (it does not exist in this one)."""
    try:
        import services.strategies.facade as facade
    except Exception as e:
        raise RuntimeError(
            f"services.strategies facade unavailable ({type(e).__name__}: {e}). "
            "desk-core has not shipped this service in this environment yet."
        ) from e
    return facade


def _render_quick_fallback(thesis: Any, analytics: Any, mode: str) -> str:
    """Minimal markdown used only when services.thesis.api.render_markdown is
    not importable (that service is owned by a different agent/worktree).

    Mirrors the real renderer's contract (contracts/strategies/models.py):
    every number comes from `analytics`/`thesis` as computed upstream, never
    invented here.
    """
    lines: List[str] = []
    if analytics is not None:
        lines.append(f"as of {analytics.as_of.isoformat()}")
        for inst in analytics.instruments:
            lines.append(
                f"- {inst.label} ({inst.coin}): {inst.mid:g}  "
                f"1d {inst.ret_1d:+.2%}  5d {inst.ret_5d:+.2%}"
            )
    if thesis is None:
        lines.append("")
        lines.append("_no thesis yet -- call desk_refresh_")
        return "\n".join(lines)
    c = thesis.call
    lines.append("")
    lines.append(f"**{c.side.upper()} {c.instrument}** (confidence {c.confidence:.0%})")
    lines.append(c.one_liner)
    if c.entry_px is not None:
        lines.append(f"entry {c.entry_px:g}")
    if c.stop_px is not None:
        lines.append(f"stop {c.stop_px:g}")
    if c.targets:
        lines.append(f"targets {', '.join(f'{t:g}' for t in c.targets)}")
    lines.append(f"invalidation: {c.invalidation}")
    if mode == "full":
        for b in thesis.bull:
            lines.append(f"+ {b.text}")
        for b in thesis.bear:
            lines.append(f"- {b.text}")
        for lv in thesis.leverage_view:
            lines.append(f"{lv.leverage:g}x: {lv.verdict} -- {lv.reason}")
    return "\n".join(lines)


def _render_quick(thesis: Any, analytics: Any, mode: str) -> str:
    """services.thesis.api.render_markdown when importable, else the
    fallback above. Only an import failure falls back -- an exception
    raised BY the real renderer propagates, so a real bug there surfaces as
    an error instead of being masked by the fallback."""
    try:
        from services.thesis.api import render_markdown
    except Exception:
        return _render_quick_fallback(thesis, analytics, mode)
    return render_markdown(thesis, analytics, mode)


def handle_desk_list(params: Dict[str, Any]) -> str:
    """Every desk's one-line card. Start here before desk_brief."""
    try:
        facade = _strategies_facade()
        cards = facade.list_cards()
        return json.dumps({"count": len(cards),
                           "desks": [c.model_dump(mode="json") for c in cards]})
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_desk_brief(params: Dict[str, Any]) -> str:
    """Full brief for one desk. mode=quick renders short markdown (the call
    first); mode=full returns the raw structured payload (details on
    demand)."""
    desk_id = str(params.get("desk") or "").strip()
    if not desk_id:
        return json.dumps({"error": "desk is required"})
    mode = str(params.get("mode") or "quick").lower()
    if mode not in ("quick", "full"):
        return json.dumps({"error": f"mode must be quick or full, got {mode!r}", "desk": desk_id})
    try:
        facade = _strategies_facade()
        brief = facade.get_brief(desk_id)
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})
    if mode == "full":
        return json.dumps({"desk": desk_id, "mode": "full",
                           "brief": brief.model_dump(mode="json")})
    try:
        markdown = _render_quick(brief.thesis, brief.analytics, mode)
    except Exception as e:
        return json.dumps({"error": f"render failed: {type(e).__name__}: {e}", "desk": desk_id})
    return json.dumps({"desk": desk_id, "mode": "quick", "markdown": markdown,
                       "stale": brief.stale})


def handle_desk_refresh(params: Dict[str, Any]) -> str:
    """Kick off (or reuse) a desk's data+thesis refresh."""
    desk_id = str(params.get("desk") or "").strip()
    if not desk_id:
        return json.dumps({"error": "desk is required"})
    force = bool(params.get("force", False))
    try:
        facade = _strategies_facade()
        job = facade.refresh(desk_id, force=force)
        return json.dumps({"desk": desk_id, "job": job.model_dump(mode="json")})
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})


def handle_desk_submit_call(params: Dict[str, Any]) -> str:
    """Record a user trade call on a desk, journaled alongside the
    machine's theses."""
    desk_id = str(params.get("desk") or "").strip()
    if not desk_id:
        return json.dumps({"error": "desk is required"})
    call = params.get("call")
    if not isinstance(call, dict):
        return json.dumps({"error": "call object is required", "desk": desk_id})
    try:
        from contracts.strategies.models import UserThesisIn
        body = UserThesisIn.model_validate({
            "call": call,
            "scenarios": params.get("scenarios") or [],
            "notes": params.get("notes"),
        })
    except Exception as e:
        return json.dumps({"error": f"invalid call: {e}", "desk": desk_id})
    try:
        facade = _strategies_facade()
        thesis = facade.submit_user_thesis(desk_id, body)
        return json.dumps({"desk": desk_id, "thesis": thesis.model_dump(mode="json")})
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})


def handle_desk_track_record(params: Dict[str, Any]) -> str:
    """Graded-thesis track record for one desk."""
    desk_id = str(params.get("desk") or "").strip()
    if not desk_id:
        return json.dumps({"error": "desk is required"})
    try:
        facade = _strategies_facade()
        tr = facade.track_record(desk_id)
        return json.dumps({"desk": desk_id, "track_record": tr.model_dump(mode="json")})
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})


if __name__ == "__main__":
    run()
