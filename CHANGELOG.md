# Changelog

All notable changes to pathiel. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Connect your Hyperliquid wallet with one signature: the `wallet_connect`
  MCP tool (plugin installs only, 65 tools in total) and
  `scripts/connect_wallet.py` make a new API wallet key on your machine,
  open a one-time local page where your browser wallet signs Hyperliquid's
  `approveAgent` for it, check that Hyperliquid lists it, and write the key
  and both addresses into `~/.pathiel/.env.local`. Mainnet and testnet.
  `/pathiel:setup` offers it first; pasting an API wallet by hand stays as
  the fallback.
- `services/accounts/agent_approval.py` builds the `approveAgent` typed data
  and wire payload for testnet as well (`hyperliquidChain` "Testnet"),
  proven equal to the installed SDK's `sign_agent` on both networks.
  Mainnet output is unchanged.

### Security

- The agent private key is made locally and is never printed, logged,
  returned by the tool, or sent to the browser; only its address leaves the
  process. Tests and an eval scan every HTTP response, result and output
  stream for it.
- The signing page binds to 127.0.0.1 on a random port behind a secret path,
  accepts only a `Host` of `127.0.0.1:<port>` or `localhost:<port>` (DNS
  rebinding), requires a matching `Origin` on the POST, works once, closes
  after 10 minutes, and serves a CSP that allows only its own hashed inline
  script and style (no external script, style or frame).
- The signature must recover to the account the page reports, the typed
  data is rebuilt on the server (the page cannot change the agent, nonce or
  network), approveAgent is posted once, and nothing is written unless
  Hyperliquid then lists the agent. An existing key is never replaced
  without `replace`; the write keeps every other line, is atomic and leaves
  the file at mode 600.
- `wallet_connect` refuses on the maintainer's own checkout
  (`operator_install`) before any file is read or written, and refuses a
  checkout's `.env.local` as a target, so the live trading key cannot be
  overwritten through it.

## [0.5.0] - 2026-11-13

The opt-in research pool and its public scoreboard. Notes:
[docs/RELEASE_NOTES_v0.5.0.md](docs/RELEASE_NOTES_v0.5.0.md).

### Added

- Opt-in research pool: register a Hyperliquid address with one signed
  statement and pathiel grades its public fills on the pool coins
  (`xyz:BRENTOIL`, `xyz:CL`, `xyz:SILVER`, `xyz:GOLD`) with the same harness
  the project runs on its own trades. Nothing is uploaded; only fills after
  the join count.
- Four MCP tools: `pool_status`, `pool_report`, `pool_join`, `pool_leave`.
  Listed on a public install only (64 tools total); the maintainer's own
  checkout is unchanged at 60, and the pool tools refuse outright
  (`operator_install`) if ever called there.
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
- The four pool MCP tools refuse before touching any pool code whenever
  the process is not a public install (`contracts.runtime.is_public()`),
  regardless of whether an MCP client calls a listed tool name: a pool
  tool dispatched on the maintainer's own mainnet checkout used to run
  for real, including signing a join/leave statement with the
  maintainer's configured trading key.

## [0.4.1] - 2026-11-13

pathiel is free: the builder fee is waived. Notes:
[docs/RELEASE_NOTES_v0.4.1.md](docs/RELEASE_NOTES_v0.4.1.md).

### Changed

- **The builder fee is waived.** No order carries a Hyperliquid builder
  code, including for users who approved the 0.4.0 fee; there is nothing to
  approve, `/pathiel:setup` skips the approval step, and
  `scripts/approve_builder_fee.py` refuses to sign anything but a 0%
  revocation. One switch brings it back: `BUILDER_FEE_WAIVED` in
  `contracts/runtime` (contract 1.2.0), with the steps in
  `docs/BUILDER_FEE.md`.
- The Claude plugin directory reviews a bundle, not the repo:
  `scripts/build_plugin_bundle.py` publishes only the files the plugin runs
  (166 of 908) to the `plugin` branch. The repo-root review had flagged
  CLAUDE.md, `.mcp.json` declared twice, `.claude/skills`, and
  download-and-run lines in docs, CI and VPS scripts that never ship.
  `release.yml` refuses a version whose bundle is not on that branch.
- `plugin.json` starts the server as `bash scripts/pathiel-mcp`, naming the
  interpreter as the directory asks; `/pathiel:setup` does the same.
- The bundle's README, SECURITY, CHANGELOG and `docs/BUILDER_FEE.md` ship
  transformed: README blocks marked `<!-- bundle:omit -->` (the container
  command, the maintainer clone setup) become one line linking that section
  on GitHub, and links to files outside the bundle become GitHub URLs.
- `scripts/oil_levels.py` posts with urllib (certifi's CA bundle, else the
  OS one) instead of shelling out to curl, which the directory read as
  download-and-run.
- `build_plugin_bundle.py publish` refuses to run when `GIT_DIR`,
  `GIT_INDEX_FILE`, `GIT_WORK_TREE` or `GIT_COMMON_DIR` is set: inside a git
  hook those point at another repo, and a test's publish once wrote a
  `plugin` branch into the real one that way.
- Listing copy leads with what people search for: "AI trading agent for
  Hyperliquid" in the README headline, `plugin.json`, `marketplace.json` and
  `server.json` (the registry picks it up with the next version). New
  keywords: `ai-agent`, `trading-agent`, `ai-trading`.

### Added

- `.claude-plugin/icon.png`, the plugin directory icon: the PATHIEL wordmark,
  white on black, from `docs/brand/icon.svg`.
- `scripts/seo_rank.py` and a weekly read-only **SEO rank** workflow: pathiel's
  GitHub search rank on nine stranger queries. Baseline in
  `docs/seo/github_rank.jsonl`.
- `.github/workflows/release.yml`: publishing a GitHub release builds the MCP
  image natively on amd64 and arm64, smoke-tests each, pushes `<version>` and
  `latest`, and publishes `server.json` to the official MCP registry with
  GitHub OIDC. It is the one workflow allowed to write (`packages`,
  `id-token`, job-scoped); `scripts/supply_chain_check.py` refuses it if it
  gains a push or pull request trigger, and refuses those scopes anywhere
  else.
- `docs/LISTINGS.md`: every place pathiel is listed, and the copy for the
  forms only the maintainer can submit.

### Security

- `scripts/_plugin_env.sh` makes paths absolute without `eval`: a
  `PATHIEL_HOME` holding `$(...)` is now kept as text, never run.

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

[Unreleased]: https://github.com/Julian-dev28/pathiel/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.5.0
[0.4.1]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.1
[0.4.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.0
[0.3.0]: https://github.com/Julian-dev28/pathiel/commit/ae26611
