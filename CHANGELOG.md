# Changelog

All notable changes to pathiel. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-11-13

pathiel is free: the builder fee is waived. Notes:
[docs/RELEASE_NOTES_v0.4.1.md](https://github.com/Julian-dev28/pathiel/blob/main/docs/RELEASE_NOTES_v0.4.1.md).

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
[docs/RELEASE_NOTES_v0.4.0.md](https://github.com/Julian-dev28/pathiel/blob/main/docs/RELEASE_NOTES_v0.4.0.md).

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

[Unreleased]: https://github.com/Julian-dev28/pathiel/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.1
[0.4.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.0
[0.3.0]: https://github.com/Julian-dev28/pathiel/commit/ae26611
