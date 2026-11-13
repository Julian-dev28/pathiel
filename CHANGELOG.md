# Changelog

All notable changes to pathiel. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Opt-in research pool: register a Hyperliquid address with one signed
  statement and pathiel grades its public fills on the pool coins
  (`xyz:BRENTOIL`, `xyz:CL`, `xyz:SILVER`, `xyz:GOLD`) with the same harness
  the project runs on its own trades. Nothing is uploaded; only fills after
  the join count.
- Four MCP tools: `pool_status`, `pool_report`, `pool_join`, `pool_leave`.
  Listed on a public install only (64 tools total); the maintainer's own
  checkout is unchanged at 60.
- `/pathiel:share`: walks a user through joining or leaving, shows the exact
  statement before it signs anything, and opens the pull request.
- A public scoreboard (static HTML and JSON, served from `docs/scoreboard/`
  by GitHub Pages) with graded per-member rows, published at least 24 hours
  late. [docs/PRIVACY.md](docs/PRIVACY.md) says what is public, what is
  not, and how to leave or come back.
- A read-only pull request check (`.github/workflows/pool-members.yml`)
  verifies every signed member and leave file offline and against
  Hyperliquid's own agent-approval reads.

### Changed

- `.dockerignore` lets `docs/scoreboard/` into the Docker build context
  (the `docs/` exclusion otherwise removes it); `Dockerfile.mcp` now ships
  `pool/` and `docs/scoreboard/` so `pool_status` has the member list and
  the scoreboard counts inside a container.

### Security

- `pool_join` and `pool_leave` sign a plain text statement, nothing else:
  no order, no transfer, no fee or payment is authorized, and the key is
  never printed or written anywhere.
- The membership pull request check is read-only (`contents: read`) and
  refuses any change that would bring an account back into the pool without
  a new signed join newer than every statement the repository ever held
  for it (deleting a leave file does not work).

## [0.4.0] - 2026-11-12

The first release built for strangers: safe public defaults, an optional
builder fee, and listing metadata for the MCP directories. Release notes:
[docs/RELEASE_NOTES_v0.4.0.md](docs/RELEASE_NOTES_v0.4.0.md).

### Added

- Hyperliquid builder fee on public installs: 0.01% of notional per fill
  (`f = 10`), paid to `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`, charged
  only after you approve it once. Without approval every order goes out with
  no fee.
- `scripts/approve_builder_fee.py`: approves the fee with your master key.
  Dry run by default; `--yes` signs. The key is typed at a hidden prompt and
  kept nowhere.
- Read-only mode: with no key every read tool works and the order tools say
  no key is configured.
- Testnet opt-in: `PATHIEL_NETWORK=testnet` in `~/.pathiel/.env.local`.
  State is kept per network. On testnet the oil desk trades WTI (`xyz:CL`)
  because Brent is not listed there.
- Public default risk profile: at most 3x leverage, 1% of equity at risk per
  trade at the stop (up to 2% only when the venue minimum order forces it),
  one position at a time.
- `scripts/public_status.py`: one PASS, WARN or FAIL line per setup check,
  names only, never a key value.
- `scripts/pathiel-mcp --commands`: paste-ready status and approval lines
  with absolute paths for your machine.
- Container image (`Dockerfile.mcp`) running the same public launcher, and
  listing files for the official MCP registry (`server.json`) and
  Glama.
- `docs/strategy/OIL_RULES.md`: the oil desk's rules with the code's own
  numbers, and what is not known.
- `SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates.

### Changed

- Public installs read their config from `~/.pathiel/.agent-config.json`,
  seeded once from `config/public/agent-config.json`, and never from the
  repo's tracked config.
- Leverage is capped at the profile on public installs: `set_leverage`
  refuses above it, and desk briefs offer only the rungs under the cap.
- Public tool descriptions no longer mention the dashboard; `risk_limits`
  says the daily loss figure is shown, not enforced.
- The thesis prompt uses a neutral persona on public installs.
- Docs: the README is rewritten for new users; public copy names no
  research or backtest doc.

### Security

- On a public install the master key is typed at a hidden prompt only. It is
  never read from the env file, printed, logged or stored, and
  `scripts/public_status.py` fails while `HYPERLIQUID_MASTER_PRIVATE_KEY` is
  set there.
- `scripts/approve_api_wallet.py` refuses on a public install (approve API
  wallets in the Hyperliquid app); `scripts/treasury.py` refuses anywhere
  but the maintainer's mainnet install.
- Builder state never blocks an order: the order path reads only a cache,
  and a refused order with a builder attached is sent once more without it,
  so a stop is never lost to the fee.
- No listing file declares a key or secret.
- A network line nobody can read never means real money. The launcher reads
  `PATHIEL_NETWORK` in any key case, past a UTF-8 BOM, CRLF endings and an
  empty first line, and warns when two lines disagree. A value that is
  neither network means testnet on a public install, in the launcher and in
  Python alike, and `public_status` reports it as a warning to fix.

## [0.3.0] - 2026-10-25

- First Claude Code plugin release: the MCP server, the desk-tick skill and
  `/pathiel:setup`, installed from this repo. Mainnet only.

[Unreleased]: https://github.com/Julian-dev28/pathiel/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.0
[0.3.0]: https://github.com/Julian-dev28/pathiel/commit/ae26611
