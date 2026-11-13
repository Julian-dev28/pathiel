# pathiel

**An AI trading agent for Hyperliquid: Claude trades perps from Claude Code, with an exact stop and target on every order.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version 0.4.0](https://img.shields.io/badge/version-0.4.0-informational.svg)](CHANGELOG.md)
[![MCP server](https://img.shields.io/badge/MCP-60_tools-6f42c1.svg)](docs/overhaul/MCP.md)
[![Claude Code plugin](https://img.shields.io/badge/Claude_Code-plugin-d97757.svg)](#install-in-30-seconds)
[![CI](https://github.com/Julian-dev28/pathiel/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Julian-dev28/pathiel/actions/workflows/ci.yml)

> **Real money.** A public install runs on Hyperliquid mainnet. With no key
> it only reads; once you add a key, orders are real. Read
> [Before you trade](#before-you-trade) first. Practice on testnet with one
> line: `PATHIEL_NETWORK=testnet`.

## What it does

pathiel is a free Claude Code plugin and MCP server: 60 tools that read Hyperliquid markets and your account, and place orders.
Every trade is one bracket: an entry, a stop and a target, sized from your risk profile (3x and 1% of equity per trade by default).
Desk briefs for oil and metals give Claude a written call to trade from; nothing trades unless you ask.

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

`/pathiel:setup` shows the fee and the risks, builds a private Python
environment, creates `~/.pathiel/.env.local` (mode 600) for your keys, seeds
the default risk profile, checks the install without printing any key, and
connects the MCP server. The steps are in `commands/setup.md`.

You need Claude Code, Python 3.11 or later, and for trading a Hyperliquid
account with an API wallet. Reading markets needs no account.
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) walks through the
account setup.

### Other MCP clients

Cursor, Claude Desktop, Pathiel Agent and any other MCP client register the
launcher `scripts/pathiel-mcp` from a clone as the server command, or run the
published container (amd64 and arm64). It is listed in the official MCP
registry as `io.github.Julian-dev28/pathiel`.

```bash
docker run -i --rm --user "$(id -u):$(id -g)" -v "$HOME/.pathiel:/data" \
  ghcr.io/julian-dev28/pathiel-mcp:0.4.0
```

To build it yourself: `docker build -f Dockerfile.mcp -t pathiel-mcp .`

Both are the public install: keys and config come from `~/.pathiel`, with
the same default network, profile and fee rules as the plugin. Starting
`scripts/pathiel-mcp-server.py` directly, or accepting the repo `.mcp.json`
when you open a clone in Claude Code, is the maintainer path (see
[Maintainer setup](#maintainer-setup)).

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

`scripts/pathiel-mcp-server.py` exposes 60 tools over stdio. The full table,
generated from the server's own tool list, is
[docs/overhaul/MCP.md](docs/overhaul/MCP.md).

| group | tools | examples |
|---|---|---|
| Desks | 6 | `desk_list`, `desk_brief`, `desk_refresh`, `desk_track_record` |
| Account, risk and config | 6 | `account_overview`, `risk_limits`, `risk_size_trade`, `state`, `config` |
| Orders (live money) | 6 | `place_bracket`, `bracket_sweep`, `set_target`, `close_position`, `set_leverage`, `cancel_order` |
| Market data | 19 | `get_price`, `get_candles`, `get_l2_book`, `get_funding_rate`, `market_list_instruments` |
| Account history | 21 | `get_open_orders`, `get_trade_history`, `get_user_fills_by_time`, `get_portfolio_pnl` |
| Wallet reads | 2 | `connected_wallets`, `wallet_account` |

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
| an API wallet key | mainnet (the default) | everything, with real money |
| a key and `PATHIEL_NETWORK=testnet` | testnet | everything, with test funds; the oil desk trades WTI (`xyz:CL`) |

State (theses, journals, caches) is kept per network, so testnet and mainnet
never share it. Testnet needs its own API wallet, created and approved in the
testnet app. The network is read once when the server starts: after editing
the env file, reconnect pathiel in `/mcp`.

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

pathiel is free. It adds a Hyperliquid builder fee of 0.01% of notional per
fill, 0.02% a round trip (in and out), to every fill pathiel routes for you,
once you approve it. That is 1 basis point on top of Hyperliquid's own
trading fee, paid to the project's builder address
`0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`. On a 2,000 USD position the
round trip costs 0.40 USD. The maintainer's own checkout never attaches one.

### Approving the fee

The fee applies only after you approve it once with your master wallet:
`/pathiel:setup` prints the command, a dry run by default, which you run in
your own terminal. Details: [docs/BUILDER_FEE.md](docs/BUILDER_FEE.md).
Declining is fine: if you never approve, orders go out with no builder fee
and everything else works the same.

### How Hyperliquid enforces the fee

Hyperliquid itself rejects any order whose builder fee is above the max you
approved. pathiel asks you to approve exactly the rate it charges, 0.01%, so
a higher rate would need a new signature from you. Hyperliquid caps a perp
builder fee at 0.1%. pathiel never blocks an order over the fee: with no
approval the order goes out without it, and the tool output says so.

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

## FAQ

**Does pathiel trade on its own?**
No. Claude places an order when you ask for one in the session, and Claude
Code asks before each live-money call. `/loop 15m /pathiel:desk-tick oil`
re-prompts Claude on a timer, only while that session stays open.

**Can it withdraw my money?**
No. pathiel signs with an API wallet, which Hyperliquid lets trade and never
withdraw. Your master key is never stored; the one step that needs it (the
fee approval) asks for it at a hidden prompt in your own terminal.

**Do I have to pay the builder fee?**
No. Skip the approval and every order goes out without it. To stop after
approving, set `PATHIEL_BUILDER_FEE_TENTHS_BP=0` in `~/.pathiel/.env.local`
and reconnect.

**How do I practice without real money?**
Add `PATHIEL_NETWORK=testnet` to `~/.pathiel/.env.local`, create an API
wallet in the testnet app (app.hyperliquid-testnet.xyz), and reconnect
pathiel in `/mcp`. The oil desk trades WTI (`xyz:CL`) there.

**Is there an edge?**
None is demonstrated. The desk briefs are a structured, written call with
levels, not a forecast. `desk_track_record` grades every call in the open.

**Does it need an Anthropic API key?**
No. The one model step, the desk thesis behind `desk_refresh`, runs through
your local Claude Code CLI. Every other tool is plain code.

**How do I remove it?**
Revoke the API wallet in the Hyperliquid app (More, API), then
`claude plugin uninstall pathiel@pathiel` and delete `~/.pathiel` if you want
the keys and state gone.

## Docs

| doc | for |
|---|---|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | account setup, permissions, prompts from first read to first trade |
| [docs/BUILDER_FEE.md](docs/BUILDER_FEE.md) | the fee: rate, approval, enforcement, how to stop |
| [docs/strategy/OIL_RULES.md](docs/strategy/OIL_RULES.md) | the rules every oil bracket must pass, and what is not known |
| [docs/overhaul/MCP.md](docs/overhaul/MCP.md) | all 60 tools |
| [SECURITY.md](SECURITY.md) | where keys live, what pathiel never does, how to report a problem |
| [CONTRIBUTING.md](CONTRIBUTING.md) | dev setup, the test gate, style rules |
| [CHANGELOG.md](CHANGELOG.md) | what changed per version |

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

## License

MIT. See [LICENSE](LICENSE).
