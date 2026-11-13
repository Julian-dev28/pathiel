# pathiel 0.4.0

Released 2026-11-12. The full list of changes is in
[CHANGELOG.md](../CHANGELOG.md).

## What you get

- **A plugin a new user can install safely.** Three lines in Claude Code
  (`/plugin marketplace add Julian-dev28/pathiel`,
  `/plugin install pathiel@pathiel`, `/pathiel:setup`), then a guided setup
  that shows the fee and the risks before any key is involved.
- **Safe default sizing.** A fresh install starts at most 3x leverage, 1% of
  equity at risk per trade at the stop (up to 2% only when the venue minimum
  order forces it), one position at a time. Every order is still one
  bracket with an exact stop and target, refused under 2R.
- **Read-only without a key.** Prices, candles, books, funding and the desk
  briefs all work on mainnet data with nothing configured.
- **Testnet when you ask for it.** `PATHIEL_NETWORK=testnet` in
  `~/.pathiel/.env.local`. On testnet the oil desk trades WTI (`xyz:CL`),
  because Brent is not listed there.
- **Other MCP clients.** The launcher `scripts/pathiel-mcp` and a container
  built from `Dockerfile.mcp` run the same public install outside Claude
  Code.
- **A status check.** `scripts/public_status.py` prints one PASS, WARN or
  FAIL line per setup item, with key names and never values.

## Upgrading from 0.3.0

Update with `claude plugin update pathiel@pathiel`, or from the
**Installed** tab in `/plugin`. Third-party marketplaces do not auto-update
by default, so run it once yourself. Then reconnect pathiel in `/mcp`.

- **Network: nothing changes.** 0.3.0 ran on mainnet and 0.4.0 still
  defaults to mainnet. Your mainnet state stays in `~/.pathiel/state`.
- **Risk profile: check it.** If `~/.pathiel/.agent-config.json` already
  exists, setup does not overwrite it. Ask Claude for your risk limits
  (`risk_limits`) and lower them if they are above what you want. A missing
  or invalid profile falls back to the public default above.
- **Builder fee: your choice.** Approve it to support the project, or
  don't. Nothing is charged until you approve, and every order works either
  way.

## Builder fee disclosure

| | |
|---|---|
| Rate | 0.01% of notional per fill, 0.02% a round trip |
| In Hyperliquid's units | `f = 10` (tenths of a basis point) |
| Paid to | `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985` |
| When | only after you approve it once with your master key |
| Without approval | no fee; orders go out the same |
| Cap | Hyperliquid rejects any order whose fee is above the rate you approved |

On a 2,000 USD position the round trip costs 0.40 USD. To approve, run the
lines `/pathiel:setup` prints: a dry run first, then the signing line in
your own terminal, which asks for the master key at a hidden prompt. To stop
after approving, set `PATHIEL_BUILDER_FEE_TENTHS_BP=0` in
`~/.pathiel/.env.local` and reconnect. Full details:
[BUILDER_FEE.md](BUILDER_FEE.md).

## Known limits

- **No demonstrated edge.** The desk briefs are a written call with levels,
  not a forecast. No return is promised. This is not financial advice.
- **Restricted jurisdictions.** Hyperliquid is not available to US persons
  or residents of other restricted regions. pathiel does not check where
  you are.
- **The daily loss figure is shown, not enforced.** Nothing stops trading
  for the day after losses.
- **The network is read at start.** After editing the env file, reconnect
  pathiel in `/mcp`. An approval signed on one network does not cover the
  other.
- **The first order after a cold start may carry no fee** if the background
  approval read has not landed yet. Orders are never held for it.
- **The thesis needs the Claude Code CLI.** `desk_refresh` writes a new
  thesis through your local `claude` command; without it, `desk_refresh`
  stops at once and says so.
- **The container image is not on a registry yet.** Build it from
  `Dockerfile.mcp` until `ghcr.io/julian-dev28/pathiel-mcp:0.4.0` is
  published.
- **A clone is not the public install.** Opening a clone and accepting the
  repo `.mcp.json` runs the maintainer's mainnet config (20x, whole-equity
  sizing). Use the plugin, the launcher or the container.
