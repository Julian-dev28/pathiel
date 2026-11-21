# pathiel research MCP

**The risk-gated research layer AI agents trade through.** A free,
read-only, keyless MCP server: six tools that answer "what does the market
look like right now," built from the same deterministic market intelligence
pathiel's own trading desks read (`services/intel`, `services/strategies`,
`services/thesis`); never from a hosted LLM call, and never touching a
key, an account, or an order.

v1 is local stdio only, packaged as a second Claude Code plugin in this same
repo. No billing, no hosting, free. See
[`docs/design/RESEARCH_MCP_PAID_TIER.md`](../../docs/design/RESEARCH_MCP_PAID_TIER.md)
for the hosted, metered plan this earns toward later.

## Install

In Claude Code:

```
/plugin marketplace add Julian-dev28/pathiel
/plugin install pathiel-research@pathiel
```

From a shell:

```bash
claude plugin marketplace add Julian-dev28/pathiel
claude plugin install pathiel-research@pathiel
```

The plugin builds its own Python environment on first use, under its own
data directory (`$CLAUDE_PLUGIN_DATA` or `~/.pathiel-research/data`),
**separate from the trading plugin's `~/.pathiel`**. This server never
reads that directory, or `.env.local` anywhere, at all.

Running from a checkout instead:

```bash
.venv/bin/python scripts/pathiel-research-mcp.py
```

or register `scripts/pathiel-research-mcp.sh` as the server command with
any other MCP client (Claude Desktop, Cursor, ...).

## Why a second server, not a new tool on the trading one

The trading MCP (`scripts/pathiel-mcp-server.py`) needs a key to place an
order and defaults to real money on mainnet. A research client (another
agent, a bot that only wants market context, a developer evaluating pathiel)
should never be one misread away from that. This server is a different
process, a different entrypoint, and (see
`tests/test_no_trading_imports.py`) structurally incapable of importing
order-placement or signing code: the trading tools do not exist here to be
called by accident.

## Tools

Every tool is read-only. Coins supported today: `BRENTOIL` (`xyz:BRENTOIL`),
`HYPE`, `BTC`, `ETH`, `SP500` (`xyz:SP500`), plus common aliases (`BRENT`,
`OIL`, `BITCOIN`, `ETHEREUM`, `SPX`, `S&P 500`, ...). `ETH` has no
standalone pathiel desk; its catalysts/headlines/odds/thesis come from the
`top5` basket desk (BTC, ETH, SOL, HYPE, ZEC), and `research_brief` says so
in its `note` field.

### `research_brief {coin}`

One call, everything: levels, the next scheduled catalysts, top headlines,
related prediction-market odds, and the latest **stored** desk thesis if
one exists. Never writes a new thesis; that needs an LLM call, which this
read-only server never makes (see Limits).

```json
// tools/call research_brief {"coin": "HYPE"}
{
  "coin": "HYPE", "desk_id": "hype", "as_of": "2026-11-21T18:12:40Z",
  "levels": {"mid": 42.87, "range_24h": {"high": 43.5, "low": 41.9}, "atr14_1h": 0.31, "...": "..."},
  "catalysts": [{"kind": "fomc", "title": "FOMC decision", "at": "2026-12-10T19:00:00Z", "importance": 3, "...": "..."}],
  "headlines": [{"title": "Hyperliquid daily volume hits a new high", "source": "...", "relevance": 0.81}],
  "odds": [{"venue": "polymarket", "question": "Will HYPE be above $45 on Dec 1?", "probability": 0.34}],
  "thesis": null,
  "note": null,
  "sources_failed": []
}
```

`sources_failed` lists which sub-source came back empty or errored (one
dead feed never sinks the whole brief); `thesis` is `null` whenever that
desk hasn't been ticked on whatever machine runs this server; correct
behavior, not a bug.

### `events {coins?, days?}`

The scheduled-event calendar (FOMC, CPI, jobs, PCE, earnings, elections,
OPEC+, holidays, ...) for one or more coins over the next `days` (default
7, max 60), plus any active blackout flag. Omit `coins` for every supported
coin.

```json
// tools/call events {"coins": ["BRENTOIL"], "days": 21}
{
  "as_of": "2026-11-21T18:15Z", "window_days": 21.0,
  "coins": {"xyz:BRENTOIL": {
    "events": [
      {"title": "US GDP Q3 second estimate + PCE (Oct)", "kind": "pce", "impact": 2,
       "coins": ["xyz:BRENTOIL", "xyz:CL", "xyz:GOLD", "xyz:SILVER", "xyz:SP500"],
       "at_ms": 1795613400000, "all_day": false, "verified": true,
       "source": "https://www.bea.gov/news/schedule"},
      {"title": "US Thanksgiving: NYSE, CME, COMEX, ICE closed (thin liquidity)",
       "kind": "us_holiday", "impact": 2, "all_day": true}
    ],
    "active_blackout": null
  }},
  "sources_failed": []
}
```

`BTC` and `ETH` have no curated calendar group yet (only
oil/metals/sp500/hype coins do); asking for them returns `events: []` with
an explicit `note`, never a silent empty list.

### `prediction_odds {query? or coin?}`

Polymarket and Kalshi markets and odds for a coin's own research queries,
or a free-text search. Pass exactly one of `coin` or `query`.

```json
// tools/call prediction_odds {"coin": "BTC"}
{
  "query": "BTC",
  "odds": [
    {"venue": "polymarket", "question": "Will the price of Bitcoin be less than $56,000 on November 22?",
     "probability": 0.026, "volume_usd": 23773.09, "closes_at": "2026-11-22T17:00:00Z"},
    {"venue": "polymarket", "question": "Will the price of Bitcoin be less than $56,000 on November 23?",
     "probability": 0.0785, "volume_usd": 7704.25, "closes_at": "2026-11-23T17:00:00Z"}
  ],
  "sources_failed": []
}
```

### `fred_series {series_id, start?, end?}`

Any FRED (Federal Reserve Economic Data) series' observations, e.g.
`DGS10` (10y Treasury yield), `DFII10` (10y real yield), `T10YIE` (10y
breakeven inflation), `DTWEXBGS` (broad dollar index). Needs the caller's
own `FRED_API_KEY` in the server's environment; refuses cleanly without
one, and never logs the key:

```json
// tools/call fred_series {"series_id": "DGS10"}, no FRED_API_KEY set
{
  "error": "FRED_API_KEY_missing",
  "series_id": "DGS10",
  "help": "fred_series needs the user's own FRED_API_KEY in the environment. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html (no credit card), then export FRED_API_KEY=... before starting this server."
}
```

Get a free key (no credit card) at
[fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html),
`export FRED_API_KEY=...` before starting this server.

### `headlines {coin|topic, hours?}`

Recent headlines, scored and deduped, for a coin or a free-text topic.
Pass exactly one of `coin` or `topic`.

```json
// tools/call headlines {"coin": "BRENTOIL", "hours": 24}
{
  "query": "xyz:BRENTOIL", "hours": 24.0,
  "headlines": [
    {"title": "Goldman is out with its 2027 forecast for Brent oil prices", "source": "Yahoo Finance",
     "published_at": "2026-11-21T12:50:18Z", "relevance": 0.81},
    {"title": "IRGC Says It Shot Down Two US Drones Over Strait of Hormuz...", "source": "NewsCord",
     "published_at": "2026-11-21T17:22:15Z", "relevance": 0.64}
  ],
  "sources_failed": []
}
```

### `levels {coin}`

Public Hyperliquid candle math, no key needed: mid, the last closed 1h
candle, 8h/24h/48h range, ATR14 (1h), the last four closed 15m candles'
range.

```json
// tools/call levels {"coin": "BRENTOIL"}
{
  "coin": "xyz:BRENTOIL", "mid": 90.1185,
  "last_closed_1h": {"open": 90.068, "high": 90.078, "low": 90.042, "close": 90.073,
                     "closed_at_utc": "2026-11-21T18:00"},
  "range_8h": {"high": 90.202, "low": 89.895},
  "range_24h": {"high": 90.4, "low": 88.9},
  "range_48h": {"high": 90.4, "low": 87.707},
  "atr14_1h": 0.1394, "atr14_pct": 0.155,
  "range_15m_last4": {"high": 90.104, "low": 90.059},
  "n_closed_1h": 50, "mid_pos_in_24h": 0.812
}
```

## Data sources and attribution

| source | used by | key | notes |
|---|---|---|---|
| Hyperliquid public `/info` | `levels`, `research_brief` | none | candleSnapshot + allMids, the same public endpoint the trading server's reads use |
| Google News, Yahoo Finance, EIA, Investing.com, oilprice.com, Cointelegraph RSS | `headlines`, `research_brief` | none | `services/intel/sources/news.py` |
| Polymarket (`gamma-api.polymarket.com`), Kalshi (`api.elections.kalshi.com`) | `prediction_odds`, `research_brief` | none | `services/intel/sources/predictions.py` |
| FRED (`api.stlouisfed.org`) | `fred_series` | **your own free `FRED_API_KEY`** | `services/intel/sources/fred.py` |
| pathiel's own curated + generated event calendar | `events`, `research_brief` | none | `services/intel/data/scheduled_events.json` + `services/intel/calendar_us.py` |
| pathiel's own stored desk theses | `research_brief` | none | `services/strategies/journal.py`; read-only, never written by this server |

This server adds no API keys of its own and signs nothing. FRED is the one
source that needs a key, and it is always the caller's own.

## Usage counter

A local, privacy-preserving count of how many times each tool has been
called (no arguments, no results, no caller identity), written to
`usage.json` under this server's own state directory
(`$PATHIEL_RESEARCH_STATE_DIR`, default `~/.pathiel-research/state`;
`services/research_mcp/state.py`). Telemetry (sending this, or anything
else, off this machine) is off by default and **not implemented in v1**.
See `docs/design/RESEARCH_MCP_PAID_TIER.md`.

## Limits

- **No LLM calls.** `research_brief`'s `thesis` field serves whatever the
  trading desk already wrote and saved to disk; it is `null` on any machine
  where that desk has never been refreshed. This server never writes a new
  one (CLAUDE.md: no hosted LLM API; the local-Claude-Code route belongs to
  the trading server, not this one).
- **Five coins today**: BRENTOIL, HYPE, BTC, ETH, SP500. Adding one means a
  new entry in `coins.py`'s `_COIN_DESK`/`_ALIASES` maps and (if it should
  have a curated event calendar) a `COIN_GROUPS` entry in
  `services/intel/scheduled_events.py`.
- **`events`/`research_brief`'s calendar never includes the 12-hour scan
  overlay** (`services/intel/scheduled_events.py`'s `scanned_events.json`):
  this server always reads with `use_overlay=False` so it never has to call
  `env.state_dir()` (which would load the repo's own `.env.local`). You get
  the curated file and the generated FOMC/CPI/OPEC+ rows, not the
  operator's own live scan state.
- **No caching.** Every call is a live read; a `research_brief` typically
  takes a few seconds (several RSS/API calls fanned out concurrently, see
  `concurrent.py`) rather than milliseconds. v1 ships without a TTL cache on
  purpose; add one if call volume ever justifies it.
- **`events`' curated calendar** only covers coins in a scheduled-events
  group (oil, metals, sp500, hype); `BTC`/`ETH` get an explicit note instead
  of a silent empty list. FOMC/CPI still show up for every coin through
  `calendar_catalysts`.
- **Not financial advice.** Every number here is public data or pathiel's
  own prior analysis, never a live recommendation from this server.

## Architecture

```
services/research_mcp/
  coins.py        coin/desk resolution (BRENTOIL/HYPE/BTC/ETH/SP500 + aliases)
  concurrent.py    fetch_many(): fan a list of fetchers out, deadline-bounded, never raises
  state.py         this server's own state directory (never ~/.pathiel)
  usage.py         the local usage counter
  tools.py         the six tools' implementations
  schemas.py       the six tools' MCP tools/list entries
  server.py        the stdio JSON-RPC transport
  tests/           gate tests, offline, <2s each
  evals/
    brief_coverage.py   live, periodic: does research_brief really answer for every coin today
  .claude-plugin/
    plugin.json    the Claude Code plugin manifest (source: ./services/research_mcp)
```

Entrypoint: `scripts/pathiel-research-mcp.py` (a checkout) or
`scripts/pathiel-research-mcp.sh` (the plugin launcher; builds its own
venv, never touches the trading server's `~/.pathiel`).

## Tests and evals

```bash
.venv/bin/python -m pytest -q services/research_mcp/           # gate tests, offline, <2s each
.venv/bin/python -m services.research_mcp.evals.brief_coverage  # live, periodic
```
