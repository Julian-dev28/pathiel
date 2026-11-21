"""The six research MCP tools' MCP tools/list entries: name, description,
inputSchema -- same shape and tone as scripts/pathiel-mcp-server.py's own
TOOLS list, trimmed to a read-only, keyless tool family. tools/list is the
source of truth a client reads; SERVER_INSTRUCTIONS (server.py) is what a
client model reads first.
"""
from __future__ import annotations

from services.research_mcp import coins as coins_mod

_COINS_LINE = f"Supported coins today: {', '.join(coins_mod.SUPPORTED_COINS)} (also takes BRENT, OIL, " \
             "BITCOIN, ETHEREUM, SPX, S&P 500 as aliases)."

TOOLS = [
    {
        "name": "research_brief",
        "description": (
            "One-call research summary for a coin: levels (mid, 8h/24h/48h range, ATR14), the "
            "next scheduled catalysts, top headlines, related Polymarket/Kalshi odds, and the "
            "latest stored pathiel desk thesis if one exists (never writes a new one -- that "
            "needs an LLM, which this read-only server never calls). " + _COINS_LINE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "e.g. BRENTOIL, HYPE, BTC, ETH, SP500."},
            },
            "required": ["coin"],
        },
    },
    {
        "name": "events",
        "description": (
            "The scheduled-event calendar (FOMC, CPI, jobs, earnings, elections, OPEC+, ...) for "
            "one or more coins over the next N days, plus any active event blackout flag. "
            + _COINS_LINE + " Omit coins for every supported coin."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coins": {"type": "array", "items": {"type": "string"},
                          "description": "Coins to check (default: every supported coin)."},
                "days": {"type": "number", "description": "Lookahead window in days (default 7, max 60)."},
            },
        },
    },
    {
        "name": "prediction_odds",
        "description": (
            "Polymarket and Kalshi markets and odds matching a coin's own research queries, or a "
            "free-text query. Pass exactly one of coin or query. " + _COINS_LINE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "A supported coin; uses that desk's own prediction-market queries."},
                "query": {"type": "string", "description": "Free-text search instead of a coin, e.g. \"Fed rate cut\"."},
            },
        },
    },
    {
        "name": "fred_series",
        "description": (
            "A FRED (Federal Reserve Economic Data) series' observations, e.g. DGS10 (10y Treasury "
            "yield), DFII10 (10y real yield), T10YIE (10y breakeven inflation), DTWEXBGS (broad "
            "dollar index) -- any FRED series id works. Needs the caller's own FRED_API_KEY in the "
            "server's environment (free, no credit card: "
            "https://fred.stlouisfed.org/docs/api/api_key.html); refuses cleanly and says so "
            "without one. Never logs the key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "series_id": {"type": "string", "description": "FRED series id, e.g. DGS10."},
                "start": {"type": "string", "description": "observation_start, YYYY-MM-DD (optional)."},
                "end": {"type": "string", "description": "observation_end, YYYY-MM-DD (optional)."},
            },
            "required": ["series_id"],
        },
    },
    {
        "name": "headlines",
        "description": (
            "Recent headlines (Google News, Yahoo Finance, EIA, Investing.com, oilprice.com, "
            "Cointelegraph), scored and deduped, for a coin or a free-text topic. Pass exactly one "
            "of coin or topic. " + _COINS_LINE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "A supported coin; uses that desk's own news queries."},
                "topic": {"type": "string", "description": "Free-text topic instead of a coin, e.g. \"OPEC+ meeting\"."},
                "hours": {"type": "number", "description": "How far back to keep headlines (default 24, max 168)."},
            },
        },
    },
    {
        "name": "levels",
        "description": (
            "Public Hyperliquid candle math for a coin: mid, the last closed 1h candle, 8h/24h/48h "
            "range, ATR14 (1h), and the last four closed 15m candles' range. No key needed. "
            + _COINS_LINE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coin": {"type": "string", "description": "e.g. BRENTOIL, HYPE, BTC, ETH, SP500."},
            },
            "required": ["coin"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]

__all__ = ["TOOLS", "TOOL_NAMES"]
