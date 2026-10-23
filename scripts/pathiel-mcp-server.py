#!/usr/bin/env python3
"""Pathiel-Trader MCP server — stdio transport (JSON-RPC 2.0, one message per line).

Tool families (tools/list is the source of truth; SERVER_INSTRUCTIONS below is
what a client model reads first):
  desks      desk_list, desk_brief, desk_refresh, desk_submit_call, desk_track_record, desk_grade
  account    account_overview, accounts, state, wallet_account, get_* account reads
  risk       risk_limits, risk_size_trade
  market     get_price, get_candles, get_l2_book, market_*, ...
  trading    place_bracket, bracket_sweep, close_position, set_leverage,
             cancel_order (LIVE MONEY)

Headless oil desk (2026-10-23): the autonomous bot's brain path (scan /
research / submit_verdict / execute) is retired. Claude places trades through
place_bracket / bracket_sweep; the operator trades his own wallet by hand from
Claude's thesis. See .claude/skills/oil-tick/SKILL.md.

Transport (run()): the main thread reads stdin; every tools/call runs on a
worker thread with a deadline, so a slow call never blocks the others and
always gets exactly one answer. See "concurrent transport" below.

Run as: python scripts/pathiel-mcp-server.py
Automatically loads .env.local from the project root if present.
Live smoke over the real transport: python scripts/mcp_smoke.py
"""

import json
import queue
import sys
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

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
from pathiel.client.hl_client import fetch_account_state, resolve_user_address
from pathiel.agents.hyperfeed import (
    market_get_asset_data,
    market_get_funding_regime,
    market_list_instruments,
    market_get_mids,
)

# Set True at `initialize` if the connected client advertised the MCP `sampling`
# capability. No tool currently routes through it (the `research` tool that
# originally consumed this was retired with the bot's brain path on
# 2026-10-23), but the negotiation and the `_server_request` transport it
# would use stay: a future tool is free to ask the connected harness's own
# model a question via `sampling/createMessage` instead of a hosted API.
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


# What a client model reads at connect time (MCP initialize "instructions").
# Short on purpose: it rides in every session's context.
SERVER_INSTRUCTIONS = """pathiel: a headless oil desk on live Hyperliquid. Real money. Claude is the trader; there is no autonomous bot.

Start here
- desk_list, then desk_brief {desk} for that desk's call (oil is primary; btc, top5, ai, metals, indices also exist). Quick mode is short markdown, call first, and includes "Your size" for the active account.
- desk_refresh {desk} writes a NEW thesis (news + one local model call, about a minute). Use it when asked, or when the brief says the thesis is stale.
- place_bracket {coin, entry_px, stop_px, tp_px}: one entry with an exact stop and target, sized and leverage-checked by the tool. bracket_sweep {coin}: cancel a stranded sibling order and confirm an open position is covered by a stop. Run bracket_sweep after every close.
- account_overview: equity and where it sits. HIP-3 money (oil: xyz:BRENTOIL, xyz:CL; xyz:NVDA, ...) lives on its own dex (xyz), not main; main-dex-only reads look empty for it.
- risk_limits / risk_size_trade: what this account may trade and how big, from the operator's saved risk profile. Size every suggested trade with risk_size_trade; do not do sizing math by hand.

Coins: main-dex perps by name (BTC, ETH). HIP-3 perps as dex:SYMBOL (xyz:BRENTOIL = Brent, the oil desk's price; xyz:CL = WTI; xyz:GOLD).

LIVE MONEY: place_bracket, bracket_sweep, close_position, set_leverage, cancel_order act on the real account, and config with arguments rewrites the live trading config. Call them only when the operator asked for that exact action in this conversation. Every other tool only reads (desk_refresh, desk_submit_call and desk_grade write the desk journal; desk_grade never places or touches an order, it only grades what already happened)."""


# Tools definition
TOOLS = [
    {
        "name": "state",
        "description": "Get full agent state: mode, config, positions, recent trades, and account equity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "connected_wallets",
        "description": (
            "Wallets signed in to the dashboard, newest first. READ-ONLY: this server "
            "signs with its own key, so it can never place, close or size an order on "
            "any wallet listed here. Answers 'whose account am I looking at'; feed an "
            "address to wallet_account below."
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
            "Read-only Hyperliquid state for any address: equity, withdrawable, open "
            "positions. Needs no key (/info clearinghouseState takes a plain address). "
            "Omit `address` for the active account; an unfunded address returns "
            "funded=false, not an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string",
                            "description": "0x address. Defaults to the active account."},
            },
        },
    },
    {
        "name": "config",
        "description": (
            "Read the agent config (no arguments), or change it: passing ANY field "
            "rewrites the live config. Every top-level key in .agent-config.json is "
            "accepted (read first to see them); the ai_brain block is deep-merged. "
            "Sizing is the risk profile now (risk_limits), not a config knob."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["OFF", "LIVE"]},
                "enable_hip3": {"type": "boolean"},
                "hip3_dex_allowlist": {"type": "array", "items": {"type": "string"}},
                "hip3_dex_blocklist": {"type": "array", "items": {"type": "string"}},
                "coin_allowlist": {"type": "array", "items": {"type": "string"}},
                "coin_blocklist": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    },
    {
        "name": "market_get_asset_data",
        "description": "Get comprehensive asset data: candles + funding + OI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Coin ticker (e.g. BTC)"},
                "intervals": {"type": "array", "items": {"type": "string"}, "description": "Candle intervals (default [\"5m\",\"15m\",\"1h\",\"4h\"])"},
                "count": {"type": "integer", "description": "Newest candles kept per interval (default 48, max 500)."}
            },
            "required": ["asset"]
        }
    },
    {
        "name": "market_get_funding_regime",
        "description": "Market-wide funding regime (crowded trades): overall and per asset "
                       "class, plus the assets with the most extreme funding.",
        "inputSchema": {"type": "object", "properties": {
            "asset_class": {"type": "string", "enum": ["crypto", "equity", "commodity"],
                            "description": "Only list assets of this class."},
            "limit": {"type": "integer", "description": "Assets listed, most extreme funding first (default 25, max 1000)."},
        }}
    },
    {
        "name": "market_list_instruments",
        "description": "Tradable instruments (perps + spot), highest 24h volume first.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Substring of the symbol, e.g. \"xyz:\" or \"OIL\"."},
            "type": {"type": "string", "enum": ["perp", "spot"]},
            "limit": {"type": "integer", "description": "Max rows (default 50, max 1000)."},
        }}
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
        "description": ("Open orders (trigger type, tp/sl) on the main dex AND every HIP-3 dex "
                        "holding margin (xyz:CL orders live on xyz), optionally filtered by "
                        "coin. Also answers get_frontend_open_orders, get_user_orders."),
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
        "description": "Cancel an open order by order ID and coin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "number"},
                "coin": {"type": "string", "description": "e.g. xyz:BRENTOIL"},
                "asset": {"type": "number", "description": "index, if no coin"}
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_user_fees",
        "description": "Get user fee tiers and rates. Also answers get_user_fees_detailed.",
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
        "name": "get_l2_book",
        "description": "L2 order book for a coin. Also answers get_market_depth.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Coin ticker (e.g. BTC)"}
        }, "required": ["coin"]}
    },
    {
        "name": "get_user_state",
        "description": "Raw clearinghouse state for ONE perp dex (default: main). HIP-3 money "
                       "sits on its own dex (pass dex=\"xyz\" for xyz:CL); for every dex at once "
                       "use account_overview.",
        "inputSchema": {"type": "object", "properties": {
            "dex": {"type": "string", "description": "Perp dex name, e.g. \"xyz\" (default: main)."},
        }}
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
                        "prediction on HL; falls back to their current hourly rate, labeled."),
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
        "name": "get_max_trade_size",
        "description": "The MOST this account's margin allows on a coin (the coin's own dex "
                       "for HIP-3), at min(coin max, profile max) leverage, checked against the "
                       "book. A ceiling, not advice: size real trades with risk_size_trade.",
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
        "description": "Account value and PnL per HL window (day/week/month/allTime): latest "
                       "values by default, full=true for the whole series.",
        "inputSchema": {"type": "object", "properties": {
            "full": {"type": "boolean", "description": "Return every point of every series (large)."},
        }}
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
        "description": "Current mid prices, including HIP-3 (xyz:, vntl:, km:, ...). Prediction "
                       "(#id) markets are left out unless asked. Also answers market_get_mids.",
        "inputSchema": {"type": "object", "properties": {
            "coins": {"type": "array", "items": {"type": "string"}, "description": "Only these coins."},
            "include_outcomes": {"type": "boolean", "description": "Also return #id prediction markets."},
        }}
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
        "description": "Equity across every dex, account leverage, main-dex cross margin ratio "
                       "(1.0 = liquidation) and liquidation distance for every open position "
                       "(main and HIP-3).",
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
        "name": "get_historical_funding",
        "description": ("Funding this ACCOUNT paid or received (not the venue rate; see "
                        "get_funding_rate and get_predicted_funding). Also answers "
                        "get_user_funding_history."),
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
        "description": "Historical (filled + cancelled) orders, newest first.",
        "inputSchema": {"type": "object", "properties": {
            "coin": {"type": "string", "description": "Only this coin."},
            "limit": {"type": "integer", "description": "Max orders (default 50, max 2000)."},
            "user": {"type": "string", "description": "Account address (default: the trading account)."},
        }}
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
        "description": "One desk's current call. mode=quick (default): short markdown, the "
                       "call first, plus \"Your size\" for the active account. mode=full: the "
                       "whole structured brief (analytics, intel, thesis, history).",
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
        "description": "Refresh a desk: market data, news/policy/odds, then a NEW thesis via "
                       "the local model (~1 min; progress reported). Skips the model unless "
                       "the market moved, a big headline landed, the thesis expired, or "
                       "force=true.",
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
    {
        "name": "desk_grade",
        "description": "Grade now: expired theses, then accounts' trades vs each call. desk "
                       "narrows trade grading only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "desk": {"type": "string"},
            },
        },
    },
    {
        "name": "account_overview",
        "description": "The active account's money: total equity, where it sits (each perp "
                       "dex incl. HIP-3, plus spot), open positions, and whether it can trade "
                       "right now (plain-English blockers). Start here for \"how much do I have\".",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "accounts",
        "description": "Hyperliquid accounts registered in pathiel (connected wallets, API "
                       "keys, watch-only), which one is active, and which address the bot "
                       "trades with. Connect or switch in the dashboard's Account tab.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "risk_limits",
        "description": "What the account may trade under its risk profile: positions at once, "
                       "$ lost per stop-out, daily pause, max leverage. equity_usd/preset = what-if.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "equity_usd": {"type": "number"},
                "preset": {"type": "string", "enum": ["auto", "starter", "small", "standard", "pro"]},
            },
        },
    },
    {
        "name": "risk_size_trade",
        "description": "Size ONE trade for the active account: size, margin, $ at risk, payoff "
                       "per target, liquidation price. Same rules as the bot's books. Use it "
                       "before suggesting any size.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "e.g. xyz:CL (WTI), BTC"},
                "entry_px": {"type": "number"},
                "stop_px": {"type": "number", "description": "Below entry = long, above = short."},
                "leverage": {"type": "number", "description": "Capped by coin and profile max."},
                "targets": {"type": "array", "items": {"type": "number"}},
                "equity_usd": {"type": "number", "description": "What-if equity (default: live)."},
            },
            "required": ["coin", "entry_px", "stop_px"],
        },
    },
    {
        "name": "place_bracket",
        "description": "One trade with exact stop and take-profit, sized like "
                       "risk_size_trade. entry_px = resting limit, stop/target attached on "
                       "fill; omit for market. Refuses under min_r (1.5R) or an existing "
                       "position/entry on the coin. dry_run = size only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "e.g. xyz:BRENTOIL"},
                "entry_px": {"type": "number"},
                "stop_px": {"type": "number"},
                "tp_px": {"type": "number"},
                "leverage": {"type": "number"},
                "min_r": {"type": "number"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["coin", "stop_px", "tp_px"],
        },
    },
    {
        "name": "bracket_sweep",
        "description": "Flat: cancel leftover reduce-only orders (sibling of a fired "
                       "stop/target), list resting entries. In a position: say whether a "
                       "stop covers it. Never opens or closes.",
        "inputSchema": {
            "type": "object",
            "properties": {"coin": {"type": "string"}},
            "required": ["coin"],
        },
    },
]


# ── tool annotations (MCP 2025-06-18) ────────────────────────────────────────
# Clients use these to decide what needs a human's yes. Every tool lands in
# exactly one bucket; tests/test_mcp_shoreup.py fails on a tool with none.
LIVE_MONEY_TOOLS = frozenset({"close_position", "set_leverage", "cancel_order",
                              "place_bracket", "bracket_sweep"})
LIVE_CONFIG_TOOLS = frozenset({"config"})
LOCAL_WRITE_TOOLS = frozenset({"desk_refresh", "desk_submit_call", "desk_grade"})


def _annotate_tools(tools: List[Dict[str, Any]]) -> None:
    """Only the hints that differ from the spec defaults (readOnlyHint=false,
    destructiveHint=true, idempotentHint=false, openWorldHint=true): every
    char here rides in every session's tools/list."""
    for tool in tools:
        name = tool["name"]
        if name in LIVE_MONEY_TOOLS:
            ann: Dict[str, Any] = {"destructiveHint": True}
            if not tool["description"].startswith("LIVE MONEY"):
                tool["description"] = "LIVE MONEY: acts on the real account. " + tool["description"]
        elif name in LIVE_CONFIG_TOOLS:
            ann = {"destructiveHint": True, "idempotentHint": True}
        elif name in LOCAL_WRITE_TOOLS:
            ann = {"destructiveHint": False}
        else:
            ann = {"readOnlyHint": True}
        tool["annotations"] = ann


_annotate_tools(TOOLS)


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
        # The active account (Account tab) first; wave 2 retired sign-in as
        # the way an account is chosen. A dashboard sign-in is the fallback.
        try:
            from services.accounts.view import get_effective_active_account
            active = get_effective_active_account()
            address = active.main_address if active else ""
        except Exception:
            address = ""
    if not address:
        store = _auth_store()
        try:
            users = sorted(store.list_users(), key=lambda u: (u.last_seen_at or 0),
                           reverse=True) if store else []
        except Exception:
            users = []
        if not users:
            return json.dumps({"error": "no address given, no active account, and no wallet "
                                        "has signed in. Connect one in the Account tab."})
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
    from pathiel.agents.config_store import read_agent_config, update_agent_config

    config = read_agent_config()

    # Snake_case scalar/bool/list keys that map 1:1 to .agent-config.json. Writing
    # snake_case (not the old camelCase) keeps a single canonical key per setting —
    # the previous handler wrote camelCase and silently created duplicate keys.
    #
    # 2026-10-23 (wave 2): every sizing/gating key the retired autonomous
    # executor read (leverage, equity_fraction_per_trade, the per-trade caps,
    # the confidence/volume/regime gates, dsl_exit, runner_entry_gate,
    # runner_mover_surface, ...) is dropped from this allowlist along with
    # DEFAULT_CONFIG — Claude sizes every trade through place_bracket /
    # risk_size_trade against the operator's risk profile now, not a global
    # config knob. See pathiel/agents/config_store.py's DEFAULT_CONFIG for
    # the full list of what left and why.
    _DIRECT_KEYS = [
        "mode", "enable_hip3",
        "hip3_dex_allowlist", "hip3_dex_blocklist",
        "coin_allowlist", "coin_blocklist",
    ]
    _NESTED_KEYS = [
        "ai_brain",
    ]

    updates: Dict[str, Any] = {}
    for key in _DIRECT_KEYS:
        if key in params and params[key] is not None:
            updates[key] = params[key]

    def _as_object(key: str, val: Any) -> Any:
        # MCP clients send untyped nested params as a JSON string; accept both.
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except ValueError:
                return None
        return val if isinstance(val, dict) else None

    for key in _NESTED_KEYS:
        if key not in params or params[key] is None:
            continue
        obj = _as_object(key, params[key])
        if obj is None:
            return json.dumps({"status": "error", "error": f"{key} must be an object"})
        updates[key] = obj

    # Any other key already in the live config (risk_profile, a hand-added
    # experiment block, ...) is accepted too, as the tool contract promises.
    # Before this, those keys were dropped silently and the call still
    # returned the unchanged config, which read as success.
    _handled = set(_DIRECT_KEYS) | set(_NESTED_KEYS)
    unknown = []
    for key, val in params.items():
        if key in _handled or val is None:
            continue
        if key not in config:
            unknown.append(key)
            continue
        if isinstance(config[key], dict):
            val = _as_object(key, val)
            if val is None:
                return json.dumps({"status": "error", "error": f"{key} must be an object"})
        updates[key] = val
    if unknown:
        # Fail loud and write nothing: a typo must never look like a saved change.
        return json.dumps({"status": "error",
                           "error": f"unknown config key(s): {', '.join(sorted(unknown))}"})

    if updates:
        try:
            config = update_agent_config(updates)
        except ValueError as e:
            return json.dumps({"status": "error", "error": str(e)})

    return json.dumps(config)


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


def handle_market_get_asset_data(params: Dict[str, Any]) -> str:
    asset = params.get("asset", "")
    if not asset:
        return json.dumps({"status": "error", "error": "asset required"})
    intervals = params.get("intervals")
    count = _limit({"limit": params.get("count")}, 48, cap=500)
    out = market_get_asset_data(asset=asset, intervals=intervals)
    candles = ((out or {}).get("data") or {}).get("candles") if isinstance(out, dict) else None
    if isinstance(candles, dict):
        for iv, rows in list(candles.items()):
            if isinstance(rows, list):
                candles[iv] = rows[-count:]
    return json.dumps(out)


def handle_market_get_funding_regime(params: Dict[str, Any]) -> str:
    out = market_get_funding_regime()
    assets = out.get("assets") if isinstance(out, dict) else None
    if isinstance(assets, list):
        cls = params.get("asset_class")
        rows = [a for a in assets if not cls or a.get("asset_class") == cls]
        rows.sort(key=lambda a: abs(_fnum(a.get("funding_rate"))), reverse=True)
        out = {**out, "assets": rows[:_limit(params, 25, cap=1000)], "assets_total": len(rows)}
    return json.dumps(out)


def handle_market_list_instruments(params: Dict[str, Any]) -> str:
    out = market_list_instruments()
    rows = out.get("instruments") if isinstance(out, dict) else None
    if isinstance(rows, list):
        q = str(params.get("query") or "").strip().lower()
        typ = params.get("type")
        rows = [r for r in rows if (not q or q in str(r.get("symbol", "")).lower())
                and (not typ or r.get("type") == typ)]
        rows.sort(key=lambda r: _fnum(r.get("volume_24h")), reverse=True)
        out = {**out, "matched": len(rows), "instruments": rows[:_limit(params, 50, cap=1000)]}
    return json.dumps(out)


def _tool_handlers() -> Dict[str, Any]:
    """tool name -> handler. Built on first use because the handlers are
    defined below run() in this file."""
    global _TOOL_HANDLERS
    if _TOOL_HANDLERS is not None:
        return _TOOL_HANDLERS
    tool_handlers = {
        "get_assets": handle_get_assets,
        "get_funding_rate": handle_get_funding_rate,
        "get_historical_funding": handle_get_historical_funding,
        "get_trade_history": handle_get_trade_history,
        "get_user_orders_history": handle_get_user_orders_history,
        "get_deposits": handle_get_deposits,
        "get_withdrawals": handle_get_withdrawals,
        "get_transfers": handle_get_transfers,
        "get_withdrawal_status": handle_get_withdrawal_status,
        "get_portfolio_status": handle_get_portfolio_status,
        "get_api_rate_limits": handle_get_api_rate_limits,
        "get_price_impact": handle_get_price_impact,
        "get_max_trade_size": handle_get_max_trade_size,
        "get_predicted_funding": handle_get_predicted_funding,
        "get_asset_context": handle_get_asset_context,
        "get_open_interest": handle_get_open_interest,
        "state": handle_state,
        "connected_wallets": handle_connected_wallets,
        "wallet_account": handle_wallet_account,
        "config": handle_config,
        "market_get_asset_data": handle_market_get_asset_data,
        "market_get_funding_regime": handle_market_get_funding_regime,
        "market_list_instruments": handle_market_list_instruments,
        "close_position": handle_close_position,
        "get_price": handle_get_price,
        "get_candles": handle_get_candles,
        "set_leverage": handle_set_leverage,
        "get_open_orders": handle_get_open_orders,
        "cancel_order": handle_cancel_order,
        "get_user_fees": handle_get_user_fees,
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
        "desk_grade": handle_desk_grade,
        "account_overview": handle_account_overview,
        "accounts": handle_accounts,
        "risk_limits": handle_risk_limits,
        "risk_size_trade": handle_risk_size_trade,
        "place_bracket": handle_place_bracket,
        "bracket_sweep": handle_bracket_sweep,
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


# A tool result lands in the client model's context. Claude Code warns past
# ~10k tokens and truncates at 25k; a raw historicalOrders dump was 784 KB.
# Handlers narrow their own output (limit/coin/query/full params); this is the
# backstop for anything that still comes back too big.
MAX_OUTPUT_CHARS = int(os.environ.get("PATHIEL_MCP_MAX_OUTPUT_CHARS", "40000"))


def _guard_output(name: str, text: str) -> str:
    if MAX_OUTPUT_CHARS <= 0 or len(text) <= MAX_OUTPUT_CHARS:
        return text
    return json.dumps({
        "truncated": True, "tool": name, "original_chars": len(text),
        "hint": ("Too large for a model context. Narrow it with this tool's filters "
                 "(limit, coin, query, full=false) and call again."),
        "head": text[: MAX_OUTPUT_CHARS // 2],
    })


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
    is_error = _payload_is_error(text)
    return {"content": [{"type": "text", "text": _guard_output(name, text)}],
            "isError": is_error}


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
            "instructions": SERVER_INSTRUCTIONS,
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


# ── concurrent transport ────────────────────────────────────────────────────
# run() reads stdin on the main thread and answers everything except
# tools/call inline. Each tools/call runs on a worker thread, so a minute-long
# desk_refresh no longer blocks a get_price sent next to it, and every call is
# answered by its deadline (_tool_timeout) even when the work behind it is
# still running. A client response to one of the server's own requests
# (sampling) is routed to the waiting handler by id. notifications/cancelled
# suppresses the cancelled call's reply, as the spec asks.
#
# Where it breaks: Python cannot kill a thread, so a call that times out keeps
# its worker until the handler returns. MAX_CONCURRENT_CALLS bounds the damage;
# every network call underneath has its own (seconds-scale) timeout.

MAX_CONCURRENT_CALLS = int(os.environ.get("PATHIEL_MCP_MAX_CONCURRENT", "6"))
DEFAULT_TOOL_TIMEOUT_S = 120.0
TOOL_TIMEOUT_S: Dict[str, float] = {"desk_refresh": 300.0}

_ROUTER_ACTIVE = False                     # True only inside run()
_ROUTE_LOCK = threading.Lock()
_WAITERS: Dict[str, "queue.Queue[Dict[str, Any]]"] = {}
_EARLY: Dict[str, Dict[str, Any]] = {}     # responses that beat their waiter (bounded)
_INFLIGHT: Dict[str, "_InflightCall"] = {}
_TLS = threading.local()


def _tool_timeout(name: str) -> float:
    raw = os.environ.get("PATHIEL_MCP_TOOL_TIMEOUT_S")
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return TOOL_TIMEOUT_S.get(name, DEFAULT_TOOL_TIMEOUT_S)


def _id_key(msg_id: Any) -> str:
    return json.dumps(msg_id, sort_keys=True, default=str)


class _InflightCall:
    """One tools/call between receipt and its single response."""

    def __init__(self, msg_id: Any, name: str, progress_token: Any):
        self.msg_id, self.name, self.progress_token = msg_id, name, progress_token
        self.answered = threading.Event()
        self._lock = threading.Lock()
        self._step = 0

    def answer(self, response: "Dict[str, Any] | None") -> bool:
        """Send `response` (None = say nothing, for a cancelled call) unless
        this call was already answered. True if this was the answer."""
        with self._lock:
            if self.answered.is_set():
                return False
            self.answered.set()
        if response is not None:
            _send(response)
        return True

    def progress(self, message: str, total: "float | None" = None) -> None:
        if self.progress_token is None or self.answered.is_set():
            return
        with self._lock:
            self._step += 1
            step = self._step
        params: Dict[str, Any] = {"progressToken": self.progress_token,
                                  "progress": step, "message": message}
        if total is not None:
            params["total"] = total
        _send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


def _report_progress(message: str, total: "float | None" = None) -> None:
    """Progress for the tool call running on this thread. A no-op outside
    run() or when the client sent no progressToken."""
    call = getattr(_TLS, "call", None)
    if call is not None:
        call.progress(message, total)


def _timeout_result(name: str, seconds: float) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({
        "error": (f"{name} did not finish within {seconds:.0f}s. The work may still "
                  "complete in the background; check again in a moment."),
        "tool": name, "timed_out": True})}], "isError": True}


def _deliver_response(msg: Dict[str, Any]) -> None:
    key = str(msg.get("id"))
    with _ROUTE_LOCK:
        waiter = _WAITERS.pop(key, None)
        if waiter is None:
            if len(_EARLY) >= 64:
                _EARLY.pop(next(iter(_EARLY)))
            _EARLY[key] = msg
            return
    waiter.put(msg)


def _cancel(request_id: Any) -> None:
    with _ROUTE_LOCK:
        call = _INFLIGHT.get(_id_key(request_id))
    if call is not None and call.answer(None):
        sys.stderr.write(f"[mcp] cancelled {call.name} (id {request_id})\n")
        sys.stderr.flush()


def _start_tool_call(msg: Dict[str, Any], pool: ThreadPoolExecutor) -> None:
    msg_id = msg.get("id")
    params = msg["params"]
    name = params.get("name")
    if not isinstance(name, str) or name not in _tool_handlers():
        _send(_error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}"))
        return
    meta = params.get("_meta")
    call = _InflightCall(msg_id, name, meta.get("progressToken") if isinstance(meta, dict) else None)
    key = _id_key(msg_id)
    with _ROUTE_LOCK:
        _INFLIGHT[key] = call
    seconds = _tool_timeout(name)
    timer = threading.Timer(seconds, lambda: call.answer(
        _result_msg(msg_id, _timeout_result(name, seconds))))
    timer.daemon = True
    timer.start()

    def work() -> None:
        _TLS.call = call
        try:
            response = _result_msg(msg_id, _call_tool(name, params.get("arguments") or {}))
        except KeyError:
            response = _error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}")
        except Exception as e:  # _call_tool already folds handler errors into a result
            traceback.print_exc(file=sys.stderr)
            response = _error_msg(msg_id, -32603, f"Internal error: {e}")
        finally:
            _TLS.call = None
            timer.cancel()
        call.answer(response)
        with _ROUTE_LOCK:
            _INFLIGHT.pop(key, None)

    pool.submit(work)


def _route(msg: Any, pool: ThreadPoolExecutor) -> None:
    if not isinstance(msg, dict):
        _send(_error_msg(None, _INVALID_REQUEST, "message must be a JSON object"))
        return
    method = msg.get("method")
    if method is None and "id" in msg and ("result" in msg or "error" in msg):
        _deliver_response(msg)
        return
    if "id" not in msg:
        if method == "notifications/cancelled":
            _cancel((msg.get("params") or {}).get("requestId"))
        return
    if method == "tools/call" and isinstance(msg.get("params"), dict):
        _start_tool_call(msg, pool)
        return
    try:
        response = dispatch_message(msg)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        response = _error_msg(msg.get("id"), -32603, f"Internal error: {e}")
    if response is not None:
        _send(response)


def run() -> None:
    global _PROTO_OUT, _ROUTER_ACTIVE
    # stdout IS the protocol channel. Anything else that prints to it (a stray
    # print() in an engine module, a library banner) splices garbage into the
    # JSON-RPC stream and the client drops the connection. Keep the real
    # stdout for frames and point sys.stdout at stderr for everyone else.
    _PROTO_OUT = sys.stdout
    sys.stdout = sys.stderr
    _tool_handlers()
    pool = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT_CALLS),
                              thread_name_prefix="mcp-tool")
    _ROUTER_ACTIVE = True
    try:
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
            _route(msg, pool)
    finally:
        # Every accepted call is answered by its own deadline (its timer), so
        # this wait is bounded even when a handler is stuck.
        with _ROUTE_LOCK:
            pending = list(_INFLIGHT.values())
        for call in pending:
            call.answered.wait()
        _ROUTER_ACTIVE = False
        pool.shutdown(wait=False, cancel_futures=True)


def handle_get_price(params: Dict[str, Any]) -> str:
    """Mid price for one coin: one rate-limited allMids POST on the coin's
    own dex (weight 2). It used to go through exchange.get_hl_price, whose
    SDK Info builds every dex's metadata on first use (2.5s for one price),
    and answered 0.0 for a coin it could not find."""
    coin = _norm_coin(params.get('coin', 'BTC'))
    try:
        price = _mid_price(coin)
    except Exception as e:
        return json.dumps({'coin': coin, 'error': str(e)})
    if price <= 0:
        return json.dumps({'coin': coin, 'error': f'no price for {coin} (unknown coin, or '
                                                  'HIP-3 coins need their dex prefix, e.g. xyz:CL)'})
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
    from pathiel.agents.bracket import close_position as close_position_market

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
        dexes = _dexes_for(coin_filter, user)
        orders: List[Dict[str, Any]] = []
        for dex in dexes:
            orders.extend(_info("frontendOpenOrders", user=user, **({"dex": dex} if dex else {})) or [])
        if coin_filter:
            orders = [o for o in orders if _norm_coin(o.get('coin', '')) == coin_filter]
        return json.dumps({"count": len(orders), "dexes": [d or "main" for d in dexes],
                           "orders": orders[:_limit(params, 100)]}, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


# An order needs margin on its own dex (HIP-3 dexes margin separately), and
# the venue minimum is $10 at up to ~40x, so a dex holding under this much
# cannot back one. Skipping those saves a weight-20 request per dust dex.
_MIN_DEX_MARGIN_FOR_ORDERS = 0.25


def _dexes_for(coin: str, user: str) -> List[str]:
    """The perp dexes to ask about `user`'s orders: the coin's own dex when a
    coin is given, else main plus every HIP-3 dex with margin on it.
    FIXED 2026-10-18: frontendOpenOrders with no `dex` covers the main dex
    (and spot) only, so every xyz:CL order was invisible to this tool."""
    if coin:
        return [_coin_dex(coin)]
    acct = fetch_account_state(user, include_hip3=True) or {}
    extra = sorted(d for d, eq in (acct.get("dex_equity") or {}).items()
                   if d and _fnum(eq) >= _MIN_DEX_MARGIN_FOR_ORDERS)
    return [""] + extra

def handle_cancel_order(params: Dict[str, Any]) -> str:
    """Cancel one order by coin (preferred) or asset index. The SDK's cancel
    takes a coin NAME; passing the index straight through (the old code)
    raised on every call, so this tool never worked for xyz:* orders."""
    from pathiel.client.exchange import _has_usable_credential, cancel_orders
    order_id = params.get('order_id')
    coin = _norm_coin(str(params.get('coin') or '')) or None
    asset = params.get('asset')
    if order_id is None or (coin is None and asset is None):
        return json.dumps({'cancelled': False, 'error': 'order_id plus coin (or asset index) required'})
    if not _has_usable_credential():
        return json.dumps({'cancelled': False, 'error': 'HYPERLIQUID_PRIVATE_KEY not set'})
    try:
        res = cancel_orders(int(order_id), coin=coin, asset_idx=int(asset) if asset is not None and coin is None else None)
        return json.dumps({'cancelled': bool(res.get('ok')), 'order_id': int(order_id), 'coin': coin,
                           **({} if res.get('ok') else {'error': res.get('error')})}, default=str)
    except Exception as e:
        return json.dumps({'cancelled': False, 'error': str(e)}, default=str)


def handle_get_user_fees(params: Dict[str, Any]) -> str:
    """Handle get_user_fees tool call."""
    user = resolve_user_address()
    try:
        info = _limited_info()
        fees = info.user_fees(user)
        return json.dumps({"fees": fees}, default=str)
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
    """Funding this ACCOUNT actually paid or received. Distinct from the venue
    rate for a coin (get_funding_rate, get_predicted_funding). Also answers
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


def handle_get_portfolio_status(params: Dict[str, Any]) -> str:
    """Account value and PnL history across HL's own windows."""
    try:
        raw = _info("portfolio", user=_user())
        return json.dumps(raw if params.get("full") else _latest_points(raw), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, default=str)


def _latest_points(portfolio: Any) -> Any:
    """HL's portfolio shape, [[window, {series: [[t, v], ...]}], ...], cut to
    each series' last point plus its length. The full series is ~20 KB."""
    if not isinstance(portfolio, list):
        return portfolio
    out = []
    for pair in portfolio:
        if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], dict):
            window, series = pair
            out.append([window, {k: ({"last": v[-1], "points": len(v)} if isinstance(v, list) and v
                                     else v) for k, v in series.items()}])
        else:
            out.append(pair)
    return out


def handle_get_api_rate_limits(params: Dict[str, Any]) -> str:
    """Request budget left. Worth reading before a wide scan — this repo has a
    history of scans dying on 429s that looked like a quiet market."""
    try:
        return json.dumps(_info("userRateLimit", user=_user()), default=str)
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
    coin = _norm_coin(params.get("coin", "BTC"))
    dex = _coin_dex(coin)
    try:
        from services.risk.derive import derive_limits
        acct = fetch_account_state(_user(), include_hip3=True) or {}
        # FIXED 2026-10-18: read main-dex margin for every coin and used the
        # retired config `leverage` (3) as the ceiling, so xyz:CL showed $0.01
        # of margin on an account with $15.18 on xyz.
        if dex:
            equity = _fnum((acct.get("dex_equity") or {}).get(dex))
            available = _fnum((acct.get("dex_available") or {}).get(dex, equity))
        else:
            equity = _fnum((acct.get("dex_equity") or {}).get("", acct.get("equity")))
            available = _fnum(acct.get("available"))
        profile, books, venue = _risk_inputs()
        resolved_max = _fnum(derive_limits(_fnum(acct.get("equity")), profile, books, venue)
                             .profile.max_leverage)
        coin_max, _ = _coin_specs(coin)
        caps = [c for c in (coin_max, resolved_max) if c and c > 0]
        lev = min(caps) if caps else 1.0
        by_margin = max(0.0, available * (1 - venue.free_margin_floor_frac)) * lev
        depth = _walk_book(coin, by_margin, _is_buy(params)) if by_margin > 0 else {}
        return json.dumps({
            "coin": coin, "dex": dex or "main", "equity": round(equity, 2),
            "margin_available": round(available, 2),
            "max_notional_by_margin": round(by_margin, 2),
            "leverage_ceiling": lev, "coin_max_leverage": coin_max,
            "profile_max_leverage": resolved_max,
            "book_check": depth,
            "note": "the most the margin allows, not a recommendation: use risk_size_trade",
        }, default=str)
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
    """Raw clearinghouse state for one perp dex (default main). A HIP-3
    account read here with no dex showed $0.0086 while $15.18 sat on xyz."""
    user = resolve_user_address()
    dex = str(params.get("dex") or "").strip()
    try:
        state = _info("clearinghouseState", user=user, **({"dex": dex} if dex else {}))
        if isinstance(state, dict):
            state = {**state, "dex": dex or "main"}
        return json.dumps(state, default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


# The real stdout, captured by run() before it redirects sys.stdout to stderr.
# None outside run() (tests, imports), where sys.stdout is used as-is.
_PROTO_OUT = None


def _out():
    return _PROTO_OUT if _PROTO_OUT is not None else sys.stdout


_SEND_LOCK = threading.Lock()


def _send(msg: Dict[str, Any]) -> None:
    """One frame, whole. Worker threads answer concurrently; the lock keeps
    two frames from interleaving on the wire."""
    line = json.dumps(msg, default=str) + "\n"
    with _SEND_LOCK:
        out = _out()
        out.write(line)
        out.flush()


def _result_msg(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_msg(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def write_response(msg_id: Any, result: Dict[str, Any]) -> None:
    _send(_result_msg(msg_id, result))


def _server_request(method: str, params: Dict[str, Any], timeout_s: float = 120.0) -> Dict[str, Any]:
    """Send a server -> client JSON-RPC request and block for its response.

    Inside run(), the main thread owns stdin and routes the reply here by id
    (_deliver_response); the client's other requests keep being served while
    this one waits. Outside run() (in-process callers, tests), there is no
    reader thread, so this reads stdin itself: _server_request_inline.
    """
    global _server_request_seq
    with _ROUTE_LOCK:
        _server_request_seq += 1
        req_id = f"srv-{_server_request_seq}"
    if not _ROUTER_ACTIVE:
        return _server_request_inline(req_id, method, params, timeout_s)
    waiter: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
    with _ROUTE_LOCK:
        early = _EARLY.pop(req_id, None)
        if early is None:
            _WAITERS[req_id] = waiter
    _send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
    if early is not None:
        msg = early
    else:
        try:
            msg = waiter.get(timeout=timeout_s)
        except queue.Empty:
            with _ROUTE_LOCK:
                _WAITERS.pop(req_id, None)
            raise TimeoutError(f"server request {method} timed out after {timeout_s:.0f}s")
    if "error" in msg:
        raise RuntimeError(f"client returned error for {method}: {msg['error']}")
    return msg.get("result") or {}


def _server_request_inline(req_id: str, method: str, params: Dict[str, Any],
                           timeout_s: float) -> Dict[str, Any]:
    """The pre-run() path: the caller's thread reads stdin directly, so it
    answers any client request that lands meanwhile (ping) or turns it away
    (anything else) rather than leaving it unanswered."""
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
        mids = market_get_mids() or {}
        wanted = params.get("coins")
        if isinstance(wanted, list) and wanted:
            keep = {_norm_coin(str(c)) for c in wanted}
            mids = {k: v for k, v in mids.items() if k in keep}
        elif not params.get("include_outcomes"):
            mids = {k: v for k, v in mids.items() if not str(k).startswith("#")}
        return json.dumps({'count': len(mids), 'mids': mids}, default=str)
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
        # FIXED 2026-10-18: equity and positions were main-dex only, so an
        # account with its money on xyz read as equity $0.0086 and its oil
        # position was missing from the liquidation-distance list.
        acct = fetch_account_state(_user(params), include_hip3=True) or {}
        main = _clearinghouse(params)
        equity = _fnum(acct.get("equity"))
        total_ntl = _fnum(acct.get("total_ntl"))
        rows = []
        for p in acct.get("asset_positions") or []:
            pos = p.get("position") or {}
            coin = _norm_coin(pos.get("coin", ""))
            if not coin or _fnum(pos.get("szi")) == 0:
                continue
            mark = _mid_price(coin)
            liq = _fnum(pos.get("liquidationPx")) if pos.get("liquidationPx") else None
            rows.append({
                "coin": coin, "dex": _coin_dex(coin) or "main", "szi": _fnum(pos.get("szi")),
                "mark": mark, "leverage": (pos.get("leverage") or {}).get("value"),
                "liquidation_px": liq,
                "liq_distance_pct": round(abs(mark - liq) / mark * 100, 3) if liq and mark else None,
            })
        mm = _fnum(main.get("crossMaintenanceMarginUsed"))
        main_equity = _fnum((main.get("crossMarginSummary") or {}).get("accountValue"))
        return json.dumps({"risk": {
            "equity": equity,
            "equity_by_dex": {(k or "main"): v for k, v in (acct.get("dex_equity") or {}).items() if v},
            "total_notional": total_ntl,
            "account_leverage": round(total_ntl / equity, 4) if equity else None,
            "main_cross_maintenance_margin_used": mm,
            "main_margin_ratio": round(mm / main_equity, 6) if main_equity else None,
            "positions": rows,
            "scope": "equity and positions: every dex. margin_ratio: main dex cross "
                     "margin only (each HIP-3 dex margins separately).",
        }}, default=str)
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
        mmr = 1.0 / (2.0 * max_lev)
        liq = round(_liq_estimate(entry, leverage, max_lev, bool(is_long)), 6)
        return json.dumps({'coin': coin, 'source': 'estimate', 'size': params.get('size'),
                           'leverage': leverage, 'is_long': bool(is_long), 'entry_px': entry,
                           'maintenance_margin_rate': mmr,
                           'liquidation_price': liq,
                           'note': 'isolated-margin formula; cross positions liquidate on '
                                   'account equity, so use get_risk_metrics for those'},
                          default=str)
    except Exception as e:
        return json.dumps({'error': str(e)}, default=str)


def _liq_estimate(entry: float, leverage: float, max_leverage: float, is_long: bool) -> float:
    """HL isolated-margin liquidation price. One formula for the whole repo:
    pathiel.agents.bracket.liq_estimate (the leverage gate uses the same one)."""
    from pathiel.agents.bracket import liq_estimate
    return liq_estimate(entry, leverage, max_leverage, is_long)


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
        user = _user(params)
        if not user:
            return json.dumps({'error': 'no configured user address'}, default=str)
        orders = _limited_info().historical_orders(user)
        if not isinstance(orders, list):
            return json.dumps({'orders': [], 'count': 0}, default=str)
        coin = _norm_coin(params.get("coin", ""))
        if coin:
            orders = [o for o in orders
                      if _norm_coin(((o.get("order") or {}).get("coin")) or "") == coin]
        orders.sort(key=lambda o: o.get("statusTimestamp") or 0, reverse=True)
        return json.dumps({'count': len(orders), 'orders': orders[:_limit(params, 50)]},
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
        # wait=True: an MCP caller has no page to update later, so analytics
        # older than DESK_BRIEF_MAX_AGE_S are rebuilt inline (fast: candles
        # come from the shared on-disk store).
        brief = facade.get_brief(desk_id, wait=True, max_age_s=DESK_BRIEF_MAX_AGE_S)
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})
    sizing = _thesis_sizing(brief)
    if mode == "full":
        return json.dumps({"desk": desk_id, "mode": "full", "your_size": sizing,
                           "brief": _compact_brief(brief)})
    try:
        # FIXED 2026-10-18: the real renderer requires a thesis, so a desk
        # that had never been refreshed crashed here ('NoneType' object has
        # no attribute 'call') instead of saying "no call yet".
        if brief.thesis is None or brief.analytics is None:
            markdown = _render_quick_fallback(brief.thesis, brief.analytics, mode)
        else:
            markdown = _render_quick(brief.thesis, brief.analytics, mode)
    except Exception as e:
        return json.dumps({"error": f"render failed: {type(e).__name__}: {e}", "desk": desk_id})
    if sizing and sizing.get("reason"):
        markdown += "\nYour size: " + sizing["reason"]
    return json.dumps({"desk": desk_id, "mode": "quick", "markdown": markdown,
                       "stale": brief.stale, "your_size": sizing})


DESK_BRIEF_MAX_AGE_S = 120.0

# Intel rows kept in a full brief, most relevant first. A live oil packet was
# 70 KB (40 headlines, 25 policy items, 79 odds markets) and pushed the whole
# brief to 99 KB, over the output guard. The thesis already cites what it used.
_BRIEF_INTEL_CAPS = {"headlines": 12, "policy": 8, "odds": 12, "catalysts": 14}
_POLICY_SUMMARY_CHARS = 300


def _compact_brief(brief: Any) -> Dict[str, Any]:
    """brief.model_dump() cut to fit a model context: top intel rows (odds by
    volume), short policy summaries, and the thesis history as one line each
    (the latest thesis is already in full under "thesis")."""
    d = brief.model_dump(mode="json")
    intel = d.get("intel")
    if isinstance(intel, dict):
        odds = intel.get("odds")
        if isinstance(odds, list):
            odds.sort(key=lambda o: _fnum(o.get("volume_usd")), reverse=True)
        for key, cap in _BRIEF_INTEL_CAPS.items():
            rows = intel.get(key)
            if isinstance(rows, list) and len(rows) > cap:
                intel[key] = rows[:cap]
                intel[f"{key}_total"] = len(rows)
        for row in intel.get("policy") or []:
            summary = row.get("summary")
            if isinstance(summary, str) and len(summary) > _POLICY_SUMMARY_CHARS:
                row["summary"] = summary[:_POLICY_SUMMARY_CHARS] + "..."
    d["history"] = [{"id": t.get("id"), "author": t.get("author"), "created_at": t.get("created_at"),
                     "side": (t.get("call") or {}).get("side"),
                     "one_liner": (t.get("call") or {}).get("one_liner"), "grade": t.get("grade")}
                    for t in d.get("history") or []]
    return d


def _thesis_sizing(brief: Any) -> "Dict[str, Any] | None":
    """The desk's current call sized for the active account, by the same
    rules as risk_size_trade. None when there is no long/short call with an
    entry and a stop; {"error": ...} when the inputs could not be read."""
    thesis = getattr(brief, "thesis", None)
    call = getattr(thesis, "call", None)
    if call is None or call.side not in ("long", "short") or not call.entry_px or not call.stop_px:
        return None
    try:
        from services.risk.size import size_trade
        profile, books, venue = _risk_inputs()
        acct = getattr(brief.analytics, "account", None) if brief.analytics else None
        equity = acct.equity_usd if acct is not None and acct.source == "venue" else _live_equity()
        coin_max, sz_dec = _coin_specs(call.instrument)
        sized = size_trade(equity, profile, books, entry_px=call.entry_px, stop_px=call.stop_px,
                           leverage=call.leverage, coin_max_leverage=coin_max,
                           targets=call.targets or [], venue=venue, sz_decimals=sz_dec)
        out = {"coin": call.instrument, "equity_usd": equity, **sized.model_dump(mode="json")}
        if sized.warnings:
            out["reason"] = " ".join([out["reason"], *sized.warnings])
        return out
    except Exception as e:
        return {"error": f"could not size this call: {e}"}


def handle_desk_refresh(params: Dict[str, Any]) -> str:
    """Kick off (or reuse) a desk's data+thesis refresh."""
    desk_id = str(params.get("desk") or "").strip()
    if not desk_id:
        return json.dumps({"error": "desk is required"})
    force = bool(params.get("force", False))
    try:
        facade = _strategies_facade()
        job = facade.refresh(desk_id, force=force,
                             on_stage=lambda label: _report_progress(label, total=4))
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


# ── accounts + risk (wave-2 services) ───────────────────────────────────────

def _risk_inputs():
    """(saved RiskProfile, live book roster, venue rules): the exact inputs
    the dashboard's risk panel and the executor size with."""
    from pathiel.agents.config_store import read_agent_config, read_risk_profile
    from pathiel.client.exchange import MIN_ORDER_USD
    from services.risk import books as risk_books
    from services.risk.derive import VenueRules
    cfg = read_agent_config()
    return (read_risk_profile(cfg), risk_books.all_book_specs(cfg),
            VenueRules(min_order_notional_usd=MIN_ORDER_USD))


def _live_equity() -> float:
    from services.accounts.summary import get_active_summary
    summary = get_active_summary(max_age_s=0)   # a tool result is acted on: always a new read
    if summary.equity_usd is None:
        raise RuntimeError("could not read the active account's equity"
                           + (": " + "; ".join(summary.blockers) if summary.blockers else ""))
    return float(summary.equity_usd)


def _coin_specs(coin: str) -> "Tuple[float | None, int | None]":
    """(venue max leverage, size decimals) for a coin, either None when the
    venue metadata could not be read."""
    from pathiel.client.exchange import get_coin_index, get_max_leverage
    try:
        max_lev: "float | None" = float(get_max_leverage(coin))
    except Exception:
        max_lev = None
    try:
        sz_dec: "int | None" = int(get_coin_index(coin)[1])
    except Exception:
        sz_dec = None
    return max_lev, sz_dec


def _position_row(pos: Dict[str, Any]) -> Dict[str, Any]:
    szi = _fnum(pos.get("szi"))
    return {"coin": _norm_coin(pos.get("coin", "")), "side": "long" if szi > 0 else "short",
            "size": abs(szi), "entry_px": _fnum(pos.get("entryPx")),
            "value_usd": _fnum(pos.get("positionValue")),
            "upnl_usd": _fnum(pos.get("unrealizedPnl")),
            "liq_px": _fnum(pos.get("liquidationPx")) or None,
            "leverage": (pos.get("leverage") or {}).get("value")}


def handle_account_overview(params: Dict[str, Any]) -> str:
    """Equity and where it sits, positions, can-trade, for the active account."""
    try:
        from services.accounts.summary import get_active_snapshot
        summary, state = get_active_snapshot(max_age_s=0)
    except Exception as e:
        return json.dumps({"error": f"account read failed: {e}"})
    acct = summary.account
    out: Dict[str, Any] = {
        "account": None if acct is None else {
            "label": acct.label, "address": acct.main_address, "mode": acct.mode,
            "source": acct.source, "agent_approved": acct.verified_at is not None},
        "equity_usd": summary.equity_usd, "withdrawable_usd": summary.withdrawable_usd,
        "can_trade": summary.can_trade, "blockers": summary.blockers,
    }
    if state:
        by_dex = {(d or "main"): round(_fnum(v), 4) for d, v in (state.get("dex_equity") or {}).items()}
        out["equity_by_dex"] = {d: v for d, v in by_dex.items() if v >= 0.01}
        out["dust_usd"] = round(sum(v for v in by_dex.values() if 0 < v < 0.01), 4)
        out["spot_usdc"] = state.get("spot_usdc")
        out["positions"] = [_position_row(p.get("position") or {})
                            for p in state.get("asset_positions") or []
                            if _fnum((p.get("position") or {}).get("szi")) != 0]
        out["note"] = ("Each HIP-3 dex margins separately: money on xyz only backs xyz:* "
                       "trades. Move funds between dexes in the Hyperliquid app.")
    return json.dumps(out, default=str)


def handle_accounts(params: Dict[str, Any]) -> str:
    """The accounts registry plus the address the bot actually trades with."""
    try:
        from services.accounts.view import list_all_accounts
        rows = list_all_accounts()
    except Exception as e:
        return json.dumps({"error": f"accounts registry unreadable: {e}"})
    try:
        trades_with = resolve_user_address() or None
    except Exception:
        trades_with = None
    return json.dumps({
        "count": len(rows),
        "accounts": [{"id": a.id, "label": a.label, "address": a.main_address, "mode": a.mode,
                      "source": a.source, "active": a.is_active,
                      "agent_approved": a.verified_at is not None} for a in rows],
        "bot_trades_with": trades_with,
        "manage": "Account tab in the dashboard: connect a wallet, add a watch-only address, switch.",
    }, default=str)


def handle_risk_limits(params: Dict[str, Any]) -> str:
    """derive_limits for the active account (or a what-if equity/preset)."""
    try:
        from contracts.risk.models import RiskProfile
        from services.risk.derive import derive_limits
        profile, books, venue = _risk_inputs()
        preset = params.get("preset")
        if preset:
            profile = RiskProfile(preset=preset)
        what_if_equity = params.get("equity_usd")
        equity = _fnum(what_if_equity) if what_if_equity is not None else _live_equity()
        lim = derive_limits(equity, profile, books, venue)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({
        "what_if": bool(preset) or what_if_equity is not None,
        "equity_usd": lim.equity_usd, "preset": lim.preset_applied, "headline": lim.headline,
        "can_trade": lim.can_trade, "max_open_positions": lim.max_open_positions,
        "risk_per_trade_usd": lim.risk_per_trade_usd,
        "daily_loss_limit_usd": lim.daily_loss_limit_usd,
        "max_leverage": lim.profile.max_leverage,
        "min_equity_to_trade_usd": lim.min_equity_to_trade_usd,
        "books": [{"book": b.book, "can_trade": b.can_trade, "reason": b.reason}
                  for b in lim.books if b.enabled],
        "notes": lim.notes,
        "change_it": "Account tab in the dashboard (risk profile), or PUT /api/risk/profile.",
    }, default=str)


def handle_place_bracket(params: Dict[str, Any]) -> str:
    """Size with services.risk.size.size_trade, then pathiel.agents.bracket.place_bracket."""
    from pathiel.agents import bracket
    coin = _norm_coin(str(params.get("coin") or ""))
    if not coin:
        return json.dumps({"ok": False, "error": "coin is required"})
    try:
        stop = float(params["stop_px"])
        tp = float(params["tp_px"])
        entry = float(params["entry_px"]) if params.get("entry_px") not in (None, "") else None
    except (KeyError, TypeError, ValueError):
        return json.dumps({"ok": False, "coin": coin, "error": "stop_px and tp_px are required numbers"})
    min_r = _fnum(params.get("min_r")) if params.get("min_r") not in (None, "") else bracket.DEFAULT_MIN_R
    lev_raw = params.get("leverage")
    try:
        from pathiel.client.exchange import get_hl_price
        from services.risk.size import size_trade
        profile, books, venue = _risk_inputs()
        coin_max, sz_dec = _coin_specs(coin)
        if not coin_max:
            # Refuse rather than guess: without the venue's max leverage the
            # liquidation estimate and the 20x rule cannot run, and a trade
            # placed blind is the failure this desk exists to prevent.
            return json.dumps({"ok": False, "coin": coin, "placed": False,
                               "error": f"venue max leverage for {coin} could not be read; not trading blind"})
        # One wallet, N slots (risk_profile.max_open_positions): a position or a
        # resting entry on another coin takes a slot. Three desks share this
        # wallet since 2026-10-23; without this, two desks could both rest an
        # entry and the second fill would be refused by the venue for margin,
        # or worse, half-fill.
        from pathiel.client.exchange import resolve_user_address
        slots = int(getattr(profile, "max_open_positions", None) or 1)
        taken = bracket.slot_taken_by(resolve_user_address(), coin, slots)
        if taken:
            return json.dumps({"ok": False, "coin": coin, "placed": False,
                               "error": f"slot taken by {taken}: the account allows {slots} open position(s)"})
        ref = entry if entry is not None else float(get_hl_price(coin))
        leverage = _fnum(lev_raw) if lev_raw not in (None, "") else (profile.max_leverage or None)
        sized = size_trade(_live_equity(), profile, books, entry_px=ref, stop_px=stop,
                           leverage=leverage, coin_max_leverage=coin_max, targets=[tp],
                           venue=venue, sz_decimals=sz_dec)
    except Exception as e:
        return json.dumps({"ok": False, "coin": coin, "error": f"sizing failed: {e}"})
    if not sized.can_trade or not sized.size:
        return json.dumps({"ok": False, "coin": coin, "placed": False,
                           "error": f"sizing refused: {sized.reason}"}, default=str)
    out = bracket.place_bracket(coin, entry_px=entry, stop_px=stop, tp_px=tp, size=float(sized.size),
                                leverage=int(sized.leverage or 1), min_r=min_r,
                                coin_max_leverage=coin_max, dry_run=bool(params.get("dry_run")))
    out["sizing"] = {"notional_usd": sized.notional_usd, "margin_usd": sized.margin_usd,
                     "risk_usd": sized.risk_usd, "risk_pct": sized.risk_pct}
    return json.dumps(out, default=str)


def handle_bracket_sweep(params: Dict[str, Any]) -> str:
    from pathiel.agents import bracket
    coin = _norm_coin(str(params.get("coin") or ""))
    if not coin:
        return json.dumps({"ok": False, "error": "coin is required"})
    try:
        return json.dumps(bracket.sweep(coin), default=str)
    except Exception as e:
        return json.dumps({"ok": False, "coin": coin, "error": str(e)})


def handle_risk_size_trade(params: Dict[str, Any]) -> str:
    """One trade sized by services.risk.size.size_trade, plus its liquidation price."""
    coin = _norm_coin(str(params.get("coin") or ""))
    if not coin:
        return json.dumps({"error": "coin is required"})
    try:
        entry = float(params.get("entry_px"))
        stop = float(params.get("stop_px"))
    except (TypeError, ValueError):
        return json.dumps({"error": "entry_px and stop_px must be numbers", "coin": coin})
    lev_raw = params.get("leverage")
    leverage = _fnum(lev_raw) if lev_raw not in (None, "") else None
    targets = [_fnum(t) for t in (params.get("targets") or []) if _fnum(t) > 0]
    try:
        from services.risk.size import size_trade
        profile, books, venue = _risk_inputs()
        eq_raw = params.get("equity_usd")
        equity = _fnum(eq_raw) if eq_raw is not None else _live_equity()
        coin_max, sz_dec = _coin_specs(coin)
        sized = size_trade(equity, profile, books, entry_px=entry, stop_px=stop,
                           leverage=leverage or None, coin_max_leverage=coin_max,
                           targets=targets, venue=venue, sz_decimals=sz_dec)
    except Exception as e:
        return json.dumps({"error": str(e), "coin": coin})
    out = {"coin": coin, "equity_usd": equity, "coin_max_leverage": coin_max,
           **sized.model_dump(mode="json")}
    if sized.can_trade and sized.leverage and coin_max:
        liq = _liq_estimate(entry, sized.leverage, coin_max, sized.side == "long")
        out["liq_px"] = round(liq, 6)
        out["stop_before_liq"] = stop > liq if sized.side == "long" else stop < liq
        if not out["stop_before_liq"]:
            out["warning"] = ("Liquidation comes before your stop at this leverage. "
                              "Lower the leverage or move the stop closer.")
    return json.dumps(out, default=str)


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


def handle_desk_grade(params: Dict[str, Any]) -> str:
    """Grade every expired thesis (one desk, or every desk), then every
    registered account's trades against each graded call, right now --
    the same two calls the server's hourly background sweep makes."""
    desk_id = str(params.get("desk") or "").strip() or None
    try:
        facade = _strategies_facade()
        n_theses = facade.grade_due()
        n_trades = facade.grade_due_trades(desk_id)
        return json.dumps({"desk": desk_id, "theses_graded": n_theses, "trades_graded": n_trades})
    except Exception as e:
        return json.dumps({"error": str(e), "desk": desk_id})


if __name__ == "__main__":
    run()
    # A tool call that timed out may still hold a worker thread; the client
    # is gone (stdin closed), so leave without joining it.
    sys.stderr.flush()
    os._exit(0)
