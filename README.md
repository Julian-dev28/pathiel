# Pathiel

> A headless trading desk on live Hyperliquid. Claude places every trade
> through an MCP server (`scripts/pathiel-mcp-server.py`, 59 tools over
> stdio); there is no autonomous bot and no standing process that trades on
> its own. Pure Python, FastAPI dashboard, real money.

## Read this first

- **Headless since 2026-10-23.** The autonomous scan/research/execute
  engine, the seven strategy books it ran, and the DSL exit loop are all
  deleted. Claude is the only trader on the wallet, calling `place_bracket`
  and `bracket_sweep` through the MCP server. See **What was removed**
  below.
- **One position per account.** The operator's live risk profile
  (`.agent-config.json`) sets `max_open_positions: 1`. `place_bracket`
  refuses to stack a second position or a second resting order on the same
  coin (`pathiel/agents/bracket.py`).
- **The trading loop is a Claude Code skill, not a script.** `.claude/skills/desk-tick/SKILL.md`,
  run on a schedule (`/loop 15m /desk-tick oil`, 30 minutes for metals, ai
  and gas). Nothing trades between ticks; nothing trades if no Claude
  session is running the tick.
- **No kill switch, no status pill, no operator-token UI.** All three were
  removed from the Desk and Thesis pages 2026-10-24 because nothing updated
  the pill and the switch did not touch the one trading path left. Live-money
  calls (`place_bracket`, `bracket_sweep`, `close_position`, `set_leverage`,
  `cancel_order`) are marked destructive in the MCP tool list, so an MCP
  client asks before each call.

## Desks: one market, one thesis, one decision

A desk is one market with its own instruments, news queries, policy watch,
catalyst calendar, leverage ladder and thesis journal. Six are registered in
`services/strategies/desks/*.py`; four are active (`.agent-config.json`'s
`active_desks`):

| desk | file | status | primary instrument |
|---|---|---|---|
| oil | `oil.py` | active, flagship | `xyz:BRENTOIL` (Brent; WTI and natgas tracked alongside) |
| metals | `metals.py` | active | gold / silver |
| ai | `ai.py` | active | AI-adjacent equities |
| gas | `gas.py` | active | natural gas |
| btc | `btc.py` | registered, off | BTC |
| top5 | `top5.py` | registered, off | top 5 crypto by volume |
| indices | `indices.py` | registered, off | broad indices |

An off desk still has a working route (`/desk/{id}`) and a working
`desk_brief` call; it is just not on the Desk page and nothing ticks it.

```
services/strategies   desk registry, deterministic analytics, journal, grader, HTTP routes
services/intel        news, policy, EIA, FRED, CBOE, EDGAR, positioning, prediction markets (zero LLM)
services/thesis       the only LLM step: writes the thesis, rejects any number not in the data
services/llm          gateway to the local Claude Code CLI, not a hosted API
contracts/strategies  the shared models every service above speaks
```

Intel sources (`services/intel/sources/`): `news.py` (deterministic
relevance and bull/bear/neutral stance scoring in `services/intel/scoring.py`),
`eia.py` (crude stocks, nat gas storage), `fred.py` (rates, macro series),
`cboe.py`, `edgar.py`, `positioning.py` (CFTC COT), `predictions.py`
(Polymarket/Kalshi), `policy.py` (bills, Federal Register, OFAC). Every
source degrades to "skipped" rather than failing the brief when its key is
missing; `scripts/intel_keys.py` reports which of `EIA_API_KEY`,
`FRED_API_KEY` and `CONGRESS_API_KEY` are set and where to get each free.

`services/thesis` writes the thesis: `prompt.py` builds the prompt,
`validator.py` rejects any number the LLM invented and enforces a 1.5R
minimum payoff (`DEFAULT_MIN_R = 1.5`) plus a stop on real market structure,
`render.py::quick_fields` turns a thesis into the handful of fields the Desk
page's call card and the MCP `desk_brief` quick mode both render from, so
the card and the text never say two different things.

Grading runs hourly inside `pathiel/server.py` (`PATHIEL_GRADE_LOOP=0` turns
it off, `PATHIEL_GRADE_INTERVAL_MIN` sets the interval, default 60). It
expires due theses and scores real account fills against each call
(`services/strategies/trades.py`). Your own calls (the Thesis page's "make
your own call" form) are graded the same way, so the track record shows you
against the machine.

```bash
python -m services.strategies list
python -m services.strategies brief oil
python -m services.strategies grade
```

## Risk: the live profile and the rules place_bracket enforces

`contracts/risk/models.py` is contract `1.1.0`. `RiskProfile.risk_per_trade_pct`
and `.daily_loss_limit_pct` go up to 100 (100 = no cap), widened 2026-10-23
so the operator can opt all the way out of the per-trade and daily caps. The
operator's own live profile:

```json
{
  "preset": "auto",
  "risk_per_trade_pct": 100,
  "daily_loss_limit_pct": 100,
  "max_leverage": 20,
  "max_open_positions": 1,
  "allow_min_order_bump": true,
  "max_bumped_risk_pct": 100
}
```

One position, full stake, up to 20x. `pathiel/agents/bracket.py` enforces
the rest:

- `slot_taken_by` refuses a second position or resting entry while the
  account's one slot is held by another coin.
- `check_leverage` is the 20x rule: the stop must sit inside half the
  distance to liquidation at the requested leverage, or the tool refuses.
- `check_levels` refuses any bracket paying under 1.5R to its target.
- Never add to an open position. The next bracket after a close sizes
  full-port again; a filled position is left alone until its stop or
  target fires.

## The trading loop

`.claude/skills/desk-tick/SKILL.md` is the loop, run on a cron per desk
(`/loop 15m /desk-tick oil`; metals, ai and gas every 30 minutes). Each tick,
in order:

1. **Slot check** (`scripts/desk_slot.py`): if another desk's coin holds the
   account's one slot, log and stop. No brief, no levels, no MCP calls.
2. **`desk_brief`** (quick mode): the current call. `desk_refresh` only when
   the brief says the thesis is stale, a headline moved the market past
   1.5%, or the target already hit.
3. **`bracket_sweep`**: cancel a stranded sibling order, confirm an open
   position is covered by a stop.
4. **Levels** (`scripts/oil_levels.py`): mid, last closed 1h candle, 8h/24h/48h
   range, ATR14, 15m micro range; `--check ENTRY STOP TP --lev 20` judges one
   level against the R gate and the 20x line.
5. **The operator's own wallet, read-only** (`scripts/watch_wallet.py`):
   warns once if a hand-placed position is naked.
6. **One decision**: hold, cancel a stale resting entry, or place one new
   bracket anchored on structure. No setup, no trade.
7. **One log line** to `/tmp/oil-loop/progress.log`.

## MCP server

`scripts/pathiel-mcp-server.py` exposes **59 tools over stdio**. The full
table, regenerated by `scripts/mcp_tool_table.py` from the server's own
`TOOLS` list, lives in `docs/overhaul/MCP.md`. Claude Code registers it via
`.mcp.json`; enable it once with `/mcp`. Any MCP client, Claude Code,
Pathiel Agent, Cursor, Claude Desktop, can register the same server and
read the same tools.

| Tool | Description |
|---|---|
| `desk_list` | Every desk's one-line card: tagline, primary coin, mid, latest call |
| `desk_brief` | One desk's current call; quick mode is short markdown, full mode is the structured brief |
| `desk_refresh` | Write a new thesis (news, then one local model call); skipped unless stale or forced |
| `desk_submit_call` | Record your own trade call, journaled alongside the machine's theses |
| `desk_track_record` | Graded-thesis track record: hit rate, mean Brier, mean return |
| `desk_grade` | Grade expired theses, then accounts' trades against each call |
| `account_overview` | Active account's equity, where it sits (every dex), positions, can-trade |
| `accounts` | Registered accounts, which is active |
| `risk_limits` | Positions at once, $ per stop-out, daily pause, max leverage |
| `risk_size_trade` | Size one trade for this account: size, margin, $ at risk, payoff, liquidation |
| `place_bracket` | **Live money.** One entry with exact stop and target, refuses under 1.5R or a taken slot |
| `bracket_sweep` | **Live money.** Cancel a stranded sibling order, confirm stop coverage |
| `close_position` | **Live money.** Close a coin through the reduce-only close helper |
| `set_leverage` | **Live money.** Set leverage, keeping the position's own margin type |
| `cancel_order` | **Live money.** Cancel one resting order by id |
| `connected_wallets` | Wallets that signed in to the dashboard, read-only by construction |
| `wallet_account` | Read-only Hyperliquid state for any address |

`connected_wallets` and `wallet_account` report on wallets that signed in to
the dashboard. This server signs orders with its own key and holds no other,
so there is no path by which it places, closes or resizes an order on a
visitor's account; `connected_wallets` says so in its own response
(`can_this_server_trade_them: false`).

## Dashboard

`python -m pathiel.server`, three pages:

| Path | Page | What it shows |
|---|---|---|
| `/` | Desk | The active desk's call as a card (from `quick_fields`), a banner when the call drifts from the open position, one row per wallet with entry/mark/P&L/R/leverage/margin/liquidation/stop/target (a flat watch-only wallet gets no row), Levels, headlines and catalysts, the loop log, the track record |
| `/desk/{id}` | Thesis | The full reading room for one desk: the call, a leverage picker (5/10/20x, same 20x line as `/`), scenarios, bull vs bear, what changes the call, desk school, track record, make-your-own-call |
| `/account` | Account | Connect or switch a Hyperliquid account, risk preset with a live preview |

The leverage line reads "Stop clears 20x liquidation" or "Stop too close for
20x" depending on `check_leverage`'s verdict. Pages poll with `pollEvery`
(`pathiel/static/pathiel.js`); the next poll waits for the last answer, and
a hidden or prerendered tab stops polling and resumes on
`visibilitychange`/`prerenderingchange` (`tests/test_page_polling.py`).

## Venue budget

`pathiel/client/rate_limit.py` is a token bucket shared by every outbound
Hyperliquid call. The server runs at 200 burst + 8 weight/s (`scripts/restart.sh`),
sized for one headless desk plus the MCP server's own reads; Hyperliquid's
own ceiling is 1,200 weight/min. Every request is labelled
`<type><<module.function>` by the first caller frame outside
`pathiel.client`, so `GET /api/health/system` -> `hl_budget` shows weight
granted and refused per request type and caller. Check this first when a
page shows dashes instead of numbers.

No request handler blocks the server's event loop: every handler that calls
Hyperliquid, the model, or the exchange is a plain `def` (FastAPI's thread
pool) rather than `async def`; `tests/test_no_blocking_on_the_event_loop.py`
checks every route module.

## Connecting an account

There is no sign-in gate for the operator's own box. Three ways to connect a
Hyperliquid account (`services/accounts/api.py`):

- **Connect your wallet** (recommended). One signature approves a trading
  key pathiel generates and keeps encrypted at rest (`services/accounts/vault.py`).
  That key can trade but can never withdraw; the main key never leaves your
  wallet.
- **Use an API key** made on app.hyperliquid.xyz/API.
- **Watch an address**: read-only, no key needed.

Then pick a risk preset with a live preview and you are trading. Existing
`.env.local` keys keep working and appear as the read-only "From .env.local"
account. `services/accounts/positions.py` skips any dex holding under
`DUST_USD` ($1) with no position, so a wallet spread across six dexes reads
one dex instead of six; a failed read never reports as a flat account, and
the last good snapshot serves while a fresh one is fetched.

### Sizing any account, $10 to $100k

Sizing comes from four plain settings: risk per trade, daily pause, max
leverage and max positions. A preset picked from equity fills them in, and
`services/risk/derive.py` derives everything else:

| preset | equity | risk / trade | daily pause | max leverage | positions |
|---|---|---|---|---|---|
| starter | under $1k | 3% | 8% | 10x | 1 |
| small | $1k-$10k | 2% | 6% | 8x | up to 3 |
| standard | $10k-$50k | 1.5% | 5% | 6x | up to 6 |
| pro | $50k+ | 1% | 4% | 5x | up to 10 |

The operator's own account runs a custom profile outside this table (risk
100%, daily pause 100%, 20x, one position), not one of the four presets.
Three guarantees hold for every preset and for custom settings,
property-tested from $5 to $100k (`services/risk/tests/test_derive.py`):

1. A trade never plans to risk more than the daily pause.
2. The daily pause always fits one minimum-size trade, or a $15 account
   could never trade at all.
3. Max positions times the worst stop-out fits inside the daily pause.

Old configs migrate automatically into a custom profile, shown as "Custom
(from your old settings)" on `/account`. Fresh installs start on "auto".

## Configuration

Two files, and they bootstrap differently:

| File | Ships with repo? | You... | Read |
|---|---|---|---|
| `.agent-config.json` | Yes, tracked | edit it (don't create) | fresh on every trade, no restart |
| `.env.local` | No, gitignored | create it: `cp .env.local.example .env.local` | at process start, restart to apply |

`.agent-config.json` today carries 8 top-level keys: `mode`, `ai_brain`,
`enable_hip3`, `coin_allowlist`, `coin_blocklist`, `hip3_dex_allowlist`,
`hip3_dex_blocklist`, `risk_profile`, plus `active_desks`. Everything the
old scan/research/execute engine read (sizing fractions, gate thresholds,
DSL exit tuning) is gone with that engine.

### `.env.local`: credentials

```bash
HYPERLIQUID_WALLET_ADDRESS=0x...   # required: the agent wallet that signs orders
HYPERLIQUID_PRIVATE_KEY=0x...      # required: that wallet's key
HYPERLIQUID_MASTER_ADDRESS=0x...   # optional: set for an agent-wallet setup

# services/intel: every one of these is optional; a missing key skips that
# source cleanly rather than failing the brief. Run scripts/intel_keys.py to
# see which are set.
EIA_API_KEY=...        # free, eia.gov/opendata/register.php
FRED_API_KEY=...       # free, fred.stlouisfed.org/docs/api/api_key.html
CONGRESS_API_KEY=...   # free, api.congress.gov/sign-up/

AI_BRAIN_PROVIDER=claude_cli   # claude_cli routes through the local Claude Code
                                 # CLI, not a hosted API; openrouter/codex_cli
                                 # are the other options in pathiel/agents/ai_brain.py
```

`.env.local.example` at the repo root does not yet list the `services/intel`
keys above; they are real, read by `services/intel/sources/*.py` directly
from the environment, just undocumented in that file today.

## Running it

```bash
scripts/restart.sh          # stop + start the FastAPI server and the log rotator
scripts/restart.sh status   # what's running
```

The MCP server is not managed by `restart.sh`: it is a transient stdio
process Claude Code spawns on each tool call, not a long-running daemon.
There is no autonomous trading loop or scheduler process any more; the
desk-tick skill is what re-prompts Claude on an interval, and it does
nothing while no Claude session is running it.

```bash
PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest -q   # gate: offline, ~2,700 tests
pytest -m online        # read-only tests against the live Hyperliquid API
pytest -m browser       # Playwright e2e for every page
.venv/bin/python scripts/mcp_smoke.py   # live, read-only MCP smoke
```

The gate suite runs in `scripts/hooks/pre-commit` on every commit
(`scripts/install_hooks.sh` to wire it in): no absolute paths bound to one
machine, staged Python parses, ruff is clean, then the full offline suite.
Hooks are never skipped with `--no-verify`; a failing hook gets fixed.
`conftest.py` refuses any host that is not loopback, so the default lane
can place no orders by construction.

## What was removed

Two waves, both 2026-10-23, both operator decisions, both documented in
full:

- **The seven autonomous strategy books** and the claims registry that
  coordinated them. `docs/overhaul/HEADLESS_2026-10-23.md`.
- **The autonomous engine itself**: the scan/research/execute loop, the 11
  risk gates, the DSL exit engine, the trade-ledger memory, the scheduler.
  Claude is the only trader left, placing every order through
  `place_bracket`/`bracket_sweep`/`close_position`. Same doc, wave 2
  section.
- **The bot-watching dashboard pages** (`/activity`, `/news`, `/trends`,
  `/analytics`, the `/desks` grid): nothing left to watch once the books
  and the engine were gone. `docs/overhaul/DESK_UI_2026-10-23.md`.
- **The kill switch, the status pill, the operator-token popover**: removed
  from the Desk and Thesis pages 2026-10-24, same commit that turned the
  call into a card.

## Project structure

```
pathiel/
├── pathiel/
│   ├── server.py                  # FastAPI app, page routes, health, grading loop
│   ├── dashboard.py                # Desk and Account page handlers, payload builders
│   ├── state.py                    # shared state-directory resolver
│   ├── agents/
│   │   ├── bracket.py              # place_bracket, bracket_sweep, close_position, the 20x rule
│   │   ├── ai_brain.py              # pluggable verdict providers (openrouter, claude_cli, codex_cli)
│   │   ├── book_params.py           # the sizing-floor resolver place_bracket still prices against
│   │   ├── capital_flows.py         # deposits/withdrawals, flow-neutral drawdown
│   │   ├── hyperfeed.py             # leaderboard, whale index, funding-regime classification
│   │   └── news_catalyst.py         # desk news-headline fetch
│   ├── client/
│   │   ├── hl_client.py             # Hyperliquid REST client
│   │   ├── exchange.py              # order placement, leverage setting
│   │   ├── account_snapshot.py      # shared, cached account reads
│   │   └── rate_limit.py            # the venue token bucket
│   └── templates/, static/          # oil_desk.html/.js, desk.html, account.html
├── services/
│   ├── strategies/                  # desk registry, analytics, journal, grader, trade grading
│   ├── thesis/                      # prompt, validator, render (the one LLM step)
│   ├── intel/                       # news, EIA, FRED, CBOE, EDGAR, positioning, predictions
│   ├── llm/                         # gateway to the local Claude Code CLI
│   ├── risk/                        # RiskProfile -> derive_limits, presets
│   ├── accounts/                    # registry, vault, positions, credentials
│   └── auth/                        # SIWE sessions, operator role
├── contracts/                       # shared pydantic models (risk, strategies, accounts)
├── scripts/
│   ├── pathiel-mcp-server.py        # MCP server (stdio, 59 tools)
│   ├── desk_slot.py, oil_levels.py, watch_wallet.py   # desk-tick's own tools
│   ├── restart.sh                   # server + log rotator
│   └── mcp_tool_table.py            # regenerates docs/overhaul/MCP.md
├── docs/overhaul/                   # the headless-desk and desk-ui removal writeups
└── tests/                           # pytest: offline gate, online, browser, live e2e
```

## Built with

- FastAPI, vanilla HTML/CSS/JS (no frontend framework)
- Hyperliquid Python SDK
- `services/llm`, a gateway to the local Claude Code CLI, the project's one
  sanctioned path to an LLM
- Pydantic contracts (`contracts/`) as the shared schema between services
