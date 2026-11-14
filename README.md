# pathiel

**An AI trading agent for Hyperliquid: Claude trades perps from Claude Code, with an exact stop and target on every order.**

[![Claude Code plugin](https://img.shields.io/badge/Claude_Code-plugin-d97757.svg)](#install-in-30-seconds)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/julian-dev28/pathiel)
[![Version 0.6.0](https://img.shields.io/badge/version-0.6.0-informational.svg)](CHANGELOG.md)
[![MCP server](https://img.shields.io/badge/MCP-65_tools-6f42c1.svg)](docs/overhaul/MCP.md)
[![CI](https://github.com/Julian-dev28/pathiel/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Julian-dev28/pathiel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)


> **Real money.** A public install runs on Hyperliquid mainnet. With no key
> it only reads; once you add a key, orders are real. Read
> [Before you trade](#before-you-trade) first. Practice on testnet with one
> line: `PATHIEL_NETWORK=testnet`.

## What it does

pathiel is a free, open-source Hyperliquid MCP server and Claude Code plugin: 65 tools that read Hyperliquid perpetual futures (perps) markets and your account, and place orders, from Claude Code, Claude Desktop, Cursor or any MCP client.
Every trade is one bracket order: an entry, a stop-loss and a take-profit target, sized from your risk profile (3x and 1% of equity per trade by default).
Desk briefs for crude oil (Brent and WTI) and metals (gold and silver) on Hyperliquid's HIP-3 markets give Claude a written call to trade from; nothing trades unless you ask.

## Install in 30 seconds

In Claude Code:

```
/plugin marketplace add Julian-dev28/pathiel
/plugin install pathiel@pathiel
/pathiel:setup
```

From a shell instead:

```bash
claude plugin marketplace add Julian-dev28/pathiel
claude plugin install pathiel@pathiel
```

Claude Code v2.1.275 or later also takes the first two lines as one:
`/plugin install pathiel --marketplace Julian-dev28/pathiel`. If Claude Code
says `Run /reload-plugins to apply`, do that, then run `/pathiel:setup`.

`/pathiel:setup` shows the network and the risks, builds a private Python
environment, creates `~/.pathiel/.env.local` (mode 600) for your keys, seeds
the default risk profile, checks the install without printing any key, and
connects the MCP server. The steps are in `commands/setup.md`.

You need Claude Code, Python 3.11 or later, and for trading a Hyperliquid
account and a browser wallet (or an API wallet you make in the Hyperliquid
app). Reading markets needs no account.
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) walks through the
account setup.

<!-- bundle:omit -->
### Other MCP clients

Cursor, Claude Desktop, Pathiel Agent and any other MCP client register the
launcher `scripts/pathiel-mcp` from a clone as the server command, or run the
published container (amd64 and arm64). It is listed in the official MCP
registry as `io.github.Julian-dev28/pathiel`.

```bash
docker run -i --rm --user "$(id -u):$(id -g)" -v "$HOME/.pathiel:/data" \
  ghcr.io/julian-dev28/pathiel-mcp:0.6.0
```

To build it yourself: `docker build -f Dockerfile.mcp -t pathiel-mcp .`

Both are the public install: keys and config come from `~/.pathiel`, with
the same default network, profile and fee rules as the plugin. Starting
`scripts/pathiel-mcp-server.py` directly, or accepting the repo `.mcp.json`
when you open a clone in Claude Code, is the maintainer path (see
[Maintainer setup](#maintainer-setup)).
<!-- /bundle:omit -->

## First prompts to try

Paste these into Claude Code once `/mcp` shows `pathiel` connected.

| you type | what answers |
|---|---|
| What's the oil desk's call right now? | `desk_brief`: direction, entry, stop, target, the R it pays, and "Your size" |
| Show me BTC's order book and funding. | `get_l2_book`, `get_funding_rate` |
| What's my balance and what's open? | `account_overview` |
| What are my risk limits? | `risk_limits` |
| Size a long on gold with the stop under today's low. | `risk_size_trade` |
| Dry-run that as a bracket. | `place_bracket` with `dry_run: true`: judged and sized, nothing sent |
| Place it. | `place_bracket`: **live money**, Claude Code asks you first |
| Run one oil desk tick. | `/pathiel:desk-tick oil`: brief, sweep, levels, one decision |

## What Claude can do

`scripts/pathiel-mcp-server.py` exposes 65 tools over stdio to a plugin
install: the 60 trading tools, `wallet_connect`, and the opt-in research
pool's 4. The maintainer's own checkout lists the 60 only. The full table, generated from
the server's own tool list, is [docs/overhaul/MCP.md](docs/overhaul/MCP.md).

| group | tools | examples |
|---|---|---|
| Desks | 6 | `desk_list`, `desk_brief`, `desk_refresh`, `desk_track_record` |
| Account, risk and config | 6 | `account_overview`, `risk_limits`, `risk_size_trade`, `state`, `config` |
| Orders (live money) | 6 | `place_bracket`, `bracket_sweep`, `set_target`, `close_position`, `set_leverage`, `cancel_order` |
| Market data | 19 | `get_price`, `get_candles`, `get_l2_book`, `get_funding_rate`, `market_list_instruments` |
| Account history | 21 | `get_open_orders`, `get_trade_history`, `get_user_fills_by_time`, `get_portfolio_pnl` |
| Wallet reads | 2 | `connected_wallets`, `wallet_account` |
| Wallet connect | 1 | `wallet_connect`: approve a new API wallet with one signature |
| Research pool | 4 | `pool_status`, `pool_report`, `pool_join`, `pool_leave` |

`connected_wallets` and `wallet_account` read other addresses and nothing
else. The server signs with one key, yours, so it has no path to place,
close or resize an order on any other account; `connected_wallets` says so
in its own output (`can_this_server_trade_them: false`).

The oil desk is the one active desk on a fresh install. It trades Brent
(`xyz:BRENTOIL`) on mainnet, and WTI (`xyz:CL`) on testnet, where Brent is
not listed. The metals desk (gold, silver) is registered and off by default;
`desk_brief` still reads it. The rules each bracket must pass are in
[docs/strategy/OIL_RULES.md](docs/strategy/OIL_RULES.md).

## Before you trade

### Networks and modes

The mode follows from what is in `~/.pathiel/.env.local`:

| you have | mode | what works |
|---|---|---|
| no key | read-only, mainnet (the default) data | every read tool; order tools answer that no key is configured |
| an [API wallet](https://app.hyperliquid.xyz/API) key | mainnet (the default) | everything, with real money |
| a key and `PATHIEL_NETWORK=testnet` | testnet | everything, with test funds; the oil desk trades WTI (`xyz:CL`) |

**Connect your wallet** is the quick way to a key: `/pathiel:setup` (or
asking Claude to connect your wallet) runs `wallet_connect`. It makes a new
API wallet key on your machine and opens a local page where your browser
wallet approves it with one signature, on the network you run. The key goes
into `~/.pathiel/.env.local` (mode 600) and nowhere else: it can trade, never
withdraw, and your main key never leaves the wallet. Pasting an API wallet
from the Hyperliquid app by hand still works.

State (theses, journals, caches) is kept per network, so testnet and mainnet
never share it. Testnet needs its own API wallet, created and approved in the
[testnet app](https://app.hyperliquid-testnet.xyz/API). How to create one,
step by step: [Connect your Hyperliquid account](docs/GETTING_STARTED.md#connect-your-hyperliquid-account).
The network is read once when the server starts: after editing the env file,
reconnect pathiel in `/mcp`.

### How the safety works

- **An exact stop on every order.** `place_bracket` is the one entry path:
  entry, stop and target together. It refuses a bracket that pays under 2R,
  and a stop that sits outside half the distance to liquidation. A market
  entry fills first and its stop goes on right after; if the venue refuses
  that stop, the output says `FILLED WITHOUT A STOP` and `bracket_sweep`
  checks every open position for one.
- **Default profile: 3x, 1%, one position.** At most 3x leverage, 1% of
  equity at the stop per trade (up to 2% only when the venue's minimum order
  size forces it), one position at a time. `set_leverage` refuses above the
  cap. You change the profile with the `config` tool or in
  `~/.pathiel/.agent-config.json`; nothing raises it for you.
- **Read-only without a key.** No key, no orders. The API wallet key can
  trade and can never withdraw.
- **Testnet when you want it.** `PATHIEL_NETWORK=testnet` in
  `~/.pathiel/.env.local` switches to test funds.
- **Claude Code asks before each live-money call.** The six order tools,
  and `config`, are marked destructive, so Claude Code asks you before each
  call unless you allow the tool for good. Nothing runs in the background:
  no bot, no scheduler. The `/loop` desk tick trades only while your session runs it.

### Builder fee

None. pathiel is free: no order it places carries a Hyperliquid builder fee,
so you pay only Hyperliquid's own trading fee. There is nothing to approve.

### If a fee ever comes back

It would ship as a new version with the rate in the changelog, and it could
not touch your orders on its own: Hyperliquid charges a builder fee only
after you approve that exact rate with your master wallet, and rejects any
order whose builder fee is above the max you approved. Hyperliquid caps a
perp builder fee at 0.1%. If you approved the 0.01% fee of pathiel 0.4.0,
it is no longer charged; [docs/BUILDER_FEE.md](docs/BUILDER_FEE.md) shows
how to revoke it anyway.

### Risk

Leveraged perps can lose the whole account, fast. The default profile limits
how much one trade can lose at its stop; it does not limit gaps, slippage or
a run of losses. The profile's daily loss figure is shown, not enforced.
There is no demonstrated edge and no return is promised. This is software,
not financial advice. You decide every trade and carry its result.

### Restricted jurisdictions

Hyperliquid's terms bar use from restricted jurisdictions, including by US
persons. The current list is in Hyperliquid's terms. pathiel does not check
where you are; staying within the rules where you live is on you.

### Your data

pathiel runs on your computer and the project runs no server, so nothing is
sent to the maintainer. No analytics, no telemetry. What does leave your
machine:

- **Hyperliquid API** (api.hyperliquid.xyz, or the testnet API): market and
  account reads, the orders you ask for, the API wallet approval you sign.
- **Public data feeds** the desk briefs read: EIA, FRED, CFTC, Congress and
  the Federal Register, prediction markets and news. An optional API key
  for one of them goes only to that feed.
- **GitHub**, only if you join the research pool: the signed statement you
  submit as a pull request.

Keys, theses, the desk journal and your config stay in `~/.pathiel`.
Details: [docs/PRIVACY.md](docs/PRIVACY.md).

## Research pool (opt-in)

pathiel can grade your trades alongside everyone else's. You register your
Hyperliquid address with one signed statement; you upload nothing, since
pathiel only reads fills Hyperliquid already makes public. From the time
you join, your public fills on the pool coins (`xyz:BRENTOIL`, `xyz:CL`,
`xyz:SILVER`, `xyz:GOLD`) are graded with the same harness the project runs
on its own trades, and published on a public scoreboard at least 24 hours
late.

What you get: your own graded report (fluke or edge on your trades, with
ranges, through `pool_report`), and pool-level stats on which desks and
setups hold up across members.

What it is not: not a fee waiver, since pathiel charges no fee to anyone,
member or not, and joining never changes what you pay. No payout, refund or reward is ever
tied to your returns. Not a copy-trading feed: results are published at
least 24 hours late, never live. No edge is demonstrated by joining, or by
anything the pool's stats show.

Run `/pathiel:share` to join or leave. It shows you the exact statement
before it signs anything, and the pull request it opens for you.

Scoreboard: https://julian-dev28.github.io/pathiel/scoreboard/

### Privacy

What is public: your account address (shortened, with an explorer link),
the date you joined, your closed and graded trade counts, your graded row
(win rate, mean result in R, a label, a luck score), and pool totals that
include your trades. What is never public: anything closed in the last 24
hours, any open position, single trades, prices, sizes, dollar results,
account value, leverage, or any key.

Leaving is one more signed statement, or a request in a GitHub issue with
no signature. Coming back takes a new signed join; only trades after the
new join count, and deleting a leave file never brings an account back on
its own.

Full details, including the exact text you sign: [docs/PRIVACY.md](docs/PRIVACY.md).

## FAQ

**How do I connect Claude to Hyperliquid?**
Install pathiel as a Claude Code plugin with the three lines in
[Install in 30 seconds](#install-in-30-seconds). Market data works at once
with no key; add an API wallet key to `~/.pathiel/.env.local` to trade.
Claude Desktop, Cursor and other MCP clients use the launcher or the
container described in [Other MCP clients](#other-mcp-clients). No
Anthropic API key is needed: the one model step, the desk thesis behind
`desk_refresh`, runs through your local Claude Code CLI.

**Is pathiel a Hyperliquid trading bot?**
No, and it never trades on its own. It is an MCP server that gives Claude
tools: Claude places an order when you ask for one in the session, and
Claude Code asks before each live-money call. `/loop 15m /pathiel:desk-tick oil`
re-prompts Claude on a timer, only while that session stays open.

**Can it withdraw my money?**
No. pathiel signs with an API wallet, which Hyperliquid lets trade and never
withdraw. Your master key is never stored, and pathiel needs it for nothing:
the only script that takes it revokes a builder fee approval, at a hidden
prompt in your own terminal.

**Does pathiel charge a fee?**
No. Orders carry no builder fee; you pay Hyperliquid's own trading fee
only.

**How do I practice without real money?**
Add `PATHIEL_NETWORK=testnet` to `~/.pathiel/.env.local`, create an API
wallet in the testnet app (app.hyperliquid-testnet.xyz), and reconnect
pathiel in `/mcp`. The oil desk trades WTI (`xyz:CL`) there.

**Is there an edge?**
None is demonstrated. The desk briefs are a structured, written call with
levels, not a forecast. `desk_track_record` grades every call in the open.

**How do I remove it?**
Revoke the API wallet in the Hyperliquid app (More, API), then
`claude plugin uninstall pathiel@pathiel` and delete `~/.pathiel` if you want
the keys and state gone.

## Docs

| doc | for |
|---|---|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | account setup, permissions, prompts from first read to first trade |
| [docs/BUILDER_FEE.md](docs/BUILDER_FEE.md) | the fee is waived; revoking a 0.4.0 approval; how it works if it returns |
| [docs/strategy/OIL_RULES.md](docs/strategy/OIL_RULES.md) | the rules every oil bracket must pass, and what is not known |
| [docs/overhaul/MCP.md](docs/overhaul/MCP.md) | all 65 tools |
| [docs/PRIVACY.md](docs/PRIVACY.md) | the research pool: what is public, what is not, how to join, leave and come back |
| [Scoreboard](https://julian-dev28.github.io/pathiel/scoreboard/) | the research pool's public, graded results |
| [SECURITY.md](SECURITY.md) | where keys live, what pathiel never does, how to report a problem |
| [CONTRIBUTING.md](CONTRIBUTING.md) | dev setup, the test gate, style rules |
| [CHANGELOG.md](CHANGELOG.md) | what changed per version |

<!-- bundle:omit -->
## Maintainer setup

> Maintainer path: this runs mainnet with the tracked repo config (20x, whole-equity sizing). It is not the public install.

This is how the maintainer runs pathiel on his own account, from a checkout.
It is not the public install and its numbers are not the defaults.

```bash
git clone https://github.com/Julian-dev28/pathiel
cd pathiel
cp .env.local.example .env.local
scripts/restart.sh          # FastAPI Desk page on http://localhost:8000/ and the log rotator
scripts/restart.sh status
```

- **MCP server.** The repo `.mcp.json` starts
  `scripts/pathiel-mcp-server.py` directly, with no install marker, so it
  runs mainnet on the tracked `.agent-config.json`. Opening a clone in
  Claude Code offers that server; accepting it is the maintainer path, not
  the public install. Other clients that want the public path register
  `scripts/pathiel-mcp`.
- **Risk profile.** The tracked config sets 20x, the whole equity per
  position less the opening fee, one position. The rules `place_bracket`
  enforces (2R minimum, stop inside half the distance to liquidation, one
  slot) hold on both paths.
- **Desk loop.** `.claude/skills/desk-tick/SKILL.md` on `/loop 15m /desk-tick oil`.
  `scripts/build_plugin_skill.py` generates the plugin copies under
  `skills/` and `agents/`; the gate fails when a copy is stale.
- **Gate.** `PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest -q`,
  offline, run by `scripts/hooks/pre-commit` (`scripts/install_hooks.sh`
  wires it in). `scripts/mcp_tool_table.py` regenerates the tool table;
  `.venv/bin/python scripts/mcp_smoke.py` is a live, read-only MCP smoke.
- **Keys.** `scripts/preflight_secrets.py` checks the env file without
  printing it; `docs/SECRETS.md` lists every secret and how to rotate it.

More: [CONTRIBUTING.md](CONTRIBUTING.md) for the code layout,
[SECURITY.md](SECURITY.md) for the hardening checks.
<!-- /bundle:omit -->

## License

MIT. See [LICENSE](LICENSE).
