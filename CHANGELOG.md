# Changelog

All notable changes to pathiel. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.0] - 2026-11-21

### Security

- The order tools leave alone what pathiel did not place. Every order now
  carries a pathiel client order id (`cloid`), and `close_position`,
  `set_stop`, `set_target`, `cancel_order`, `place_bracket` with `add` and
  `sweep_brackets` refuse to act on a position or order you placed
  yourself (in the Hyperliquid app, or any other way) unless the call
  passes `operator_override: true`. `sweep_brackets` only reports on such a
  position. A failed ownership read refuses rather than guesses.

### Added

- A HYPE desk, `hype`, trading HYPE (Hyperliquid's own token, main dex)
  long or short. Off by default. Its stop is a pure ATR multiple with no
  price floor and its own leverage cap is the venue's max, 10x (config
  `desk_max_leverage`). New entries are vetoed against the sign of the
  120-hour return (`next_trade.trend_gate`, built for oil 2026-11-13 and
  found not to help there; it does here). Its backtest found the plain
  rule every other desk uses loses on HYPE and the trend-gated version
  has an edge.
- On HYPE the desk does nothing while a position or resting order it did
  not place sits on the coin, the same guard the S&P desk has:
  `desk_slot.py` answers `slot: operator`, and `place_bracket`,
  `sweep_brackets` and `set_leverage` refuse.
- The event calendar and the headline watcher cover HYPE: FOMC and CPI
  hold new entries, token unlocks reach the thesis as a catalyst
  automatically (any crypto-ticker desk), and the headline watcher has its
  own patterns (protocol actions, an exploit, a token unlock, exchange
  competition, crypto regulation, a bitcoin ETF/liquidation shock).
- An S&P 500 desk, `sp500`, trading `xyz:SP500` long or short. Off by
  default. It has its own stop and target numbers and its own leverage cap
  (config `desk_max_leverage`); every other desk keeps the risk profile's
  `max_leverage`. Its backtest found no edge in the rule alone.
- On `xyz:SP500` the desk does nothing while a position or resting order it
  did not place sits on the coin: `desk_slot.py` answers `slot: operator`,
  and `place_bracket`, `sweep_brackets` and `set_leverage` refuse.
- The event calendar and the headline watcher cover the S&P: FOMC, CPI and
  the monthly jobs report hold new entries, and megacap earnings rows can
  be added with coins `sp500`.
- `scripts/glama_health.py` (maintainers): Glama's maintenance data for the
  server next to GitHub's own Dependabot, code scanning and CI state, so an
  "unavailable" line on Glama traces to a GitHub setting or to the Glama AI
  app not being installed on the repo.

### Changed

- The README opens with the two ways pathiel works out of the box:
  research only (no account, no key) and research plus trading (an API
  wallet connected with one signature), and lists all four desk briefs.

### Fixed

- The Levels-card route tests pinned the event-blackout clock. They read
  the real clock and went red on weekends, inside the oil weekend window.

## [0.7.2] - 2026-11-17

### Security

- A main account key set as the trading key is refused. On a public install
  the live-money tools refuse when the signing key's address is your
  `HYPERLIQUID_MASTER_ADDRESS` or Hyperliquid reports it as a main account,
  and `state` says "Trading: BLOCKED". Use an API wallet key, which can
  trade and never withdraw.
- No pathiel process loads `HYPERLIQUID_MASTER_PRIVATE_KEY` unless it signs
  with it. The MCP server, the dashboard and the desk scripts skip it; the
  scripts that need it ask for it at a hidden prompt when it is not in the
  env file.
- Every `claude` process pathiel starts runs without keys, tokens or
  passwords in its environment, so a prompt injection in a news headline
  finds nothing to steal.
- Code scanning fixes: a post filter in the headline watch could backtrack
  exponentially on a crafted post (now linear); the desk page escapes the
  desk id it echoes; HTTP answers name a failure's exception class and keep
  its text in the log; the Telegram webhook check matches the host exactly.

### Added

- `scripts/master_key_to_keychain.py` (macOS, maintainers): moves
  `HYPERLIQUID_MASTER_PRIVATE_KEY` from the env file into the login Keychain.
  It stores the key through `security -i` on stdin, reads it back and
  compares before it removes the line, and never prints the key. The README
  and `docs/SECRETS.md` show how to pass it to one `treasury.py` command.
- `close_position` takes an optional `fraction` (0.5 closes half). A partial
  close keeps the stop and target, which are reduce-only and still cover
  what is left; the full close cancels them as before.

### Changed

- Tool descriptions name the sibling for each job that is easy to confuse:
  `set_stop` and `set_target` point at `sweep_brackets` for duplicate
  levels, and `cancel_order` says how to change a resting entry (cancel,
  then `place_bracket`). The server instructions say the same in four lines.
- The public instructions cap is 3000 characters (was 2500), so a long
  trading or fee line can never cut the LIVE MONEY rule off the end.

### Fixed

- A take-profit or stop placed for the whole position (Hyperliquid's app
  sends it with size 0) now counts as covering the position. `sweep_brackets`
  and the positions view read such a stop as covering nothing, and the desk
  tick answers an uncovered position with a close.
- Polymarket markets past their end date no longer reach the thesis or the
  odds watcher. One listed as open eight days after it ended woke the oil
  desk and fed a thesis rewrite.
- A new thesis must pay at least 2R, the floor `place_bracket` enforces. The
  prompt and validator asked for 1.5R, so a thesis between 1.5R and 2R passed
  and was then refused when placed.

## [0.7.1] - 2026-11-14

### Changed

- Plugin content names only tools a plugin install lists: the desk-tick
  agent read the balance with `account_overview`, which 0.7.0 no longer
  lists, so the model could not call it; it now uses `get_account`. The
  README, the `pathiel-agent` skill and run-time tool messages use the new
  names and counts too.
- Every pull request builds both container images (`build.yml`); the five
  test and build checks are required before anything merges to `main`.

### Added

- `.github/workflows/freshness.yml`, daily: `scripts/plugin_freshness.py`
  fails when a change to a shipped file has waited on `main` more than 7
  days without a release (2 days for `requirements.lock`), and
  `scripts/link_check.py` fails on any dead link in the README, the plugin's
  Markdown, the docs or the site. A gate test keeps shipped agent-facing
  Markdown on listed tool names.
- `scripts/seo_compare.py`: GitHub signals, search ranks and directory
  listings for pathiel against another repository.

### Security

- msgpack 1.2.2 -> 1.2.3 (GHSA-j586-36cw-2gc2, a memory leak in the C
  decoder on malformed input). Order signing bytes are unchanged, checked on
  six action shapes and pinned by `tests/test_signing_bytes.py`.
- The Docker base image is back on the python:3.11 digest the lock is built
  for; a Dependabot bump to 3.15 had broken both images.

## [0.7.0] - 2026-11-13

### Changed

- A plugin install lists 30 tools instead of 65 (`contracts/mcp_public`):
  one tool per job, every name verb_noun. `get_market` answers what
  `get_price`, `get_asset_context`, `get_predicted_funding` and
  `get_max_leverage` did; `get_account`, `get_orders` and `get_history`
  replace six equity views, five order lookups and seven ledgers. Every
  description says when to use the tool, which sibling covers the next job,
  what it refuses and what it returns. Every old name still answers when
  called, and the operator's install keeps its 60 unchanged. Directory
  graders scored the old list 3.6/5 (Glama TDQS); `scripts/tdqs_eval.py`
  runs the same six-part rubric locally through Claude Code.

### Added

- The operator's install can list the scoped toolset too: `PATHIEL_TOOLSET=v2`
  in `.mcp.json`'s env lists 26 of the 30 (not wallet connect or the pool,
  which refuse there) with instructions in the new names. Operator behavior
  is unchanged: no public gate, no public decoration. Unset, the 60 tools
  and their instructions are byte for byte what they were. The desk-tick
  agent, its skill and the plugin's commands use the new names.
- `set_stop`: move an open position's stop tighter, never wider. The new
  stop goes on before the old one is cancelled, so the position is never
  unprotected.

- The Pages site looks like a trading terminal: near-black with a chart
  grid, green and red for long and short, a ticker tape of the default risk
  limits, panels with tab headers, a risk-engine table and a markets
  watchlist. The hero chart draws a long bracket (entry, stop zone, target
  zone) from `scripts/site_chart.py`, seeded; it is labelled an
  illustration, with no candles after the entry. Still no script and no web
  font; motion is CSS only and stops under reduced motion. The scoreboard
  matches it: label chips (EDGE, LEAK, LEANING, FLUKE, NOISE) and mean R in
  green or red by sign; the word and the number stay the data.
- The plugin starts its server as `bash scripts/pathiel-mcp.sh`, a name that
  matches its interpreter as the plugin directory asks.
  `scripts/pathiel-mcp` stays as a one-line shim for other MCP clients and
  the container.
- The plugin bundle no longer ships `CHANGELOG.md`; links to it point at
  GitHub. The directory held it for naming the icon file and for an old
  install line.

### Removed

- `docs/LISTINGS.md`, the maintainer's listing notes and form answers. They
  live outside the repository now.

### Security

- `set_stop` can only reduce risk: it refuses a wider stop and a stop past
  the mark, and places the new stop before cancelling the old one. On a
  public install it passes the same gate as every live-money tool (no key:
  read-only answer, nothing sent; a coin not listed on the network refused).
- Every public name reaches its handler through the same safety gate as
  the old name (`_canonical`), and a composite tool's inner calls are gated
  one by one; `_check_public_toolset` refuses to start a public install
  whose toolset points at an unknown or misclassed tool.

## [0.6.0] - 2026-11-13

Connect your Hyperliquid wallet with one signature, and a website. Notes:
[docs/RELEASE_NOTES_v0.6.0.md](docs/RELEASE_NOTES_v0.6.0.md).

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
- A landing page, https://julian-dev28.github.io/pathiel/ (`site/`): what
  pathiel is, the install lines, the safety rules, the risks, and search
  metadata (description, canonical, Open Graph, schema.org JSON-LD,
  sitemap). It loads nothing from anywhere else.

### Changed

- GitHub Pages serves the `gh-pages` branch, built by
  `scripts/publish_site.py` from the landing page and `docs/scoreboard/`
  only. It used to serve all of `docs/` from `main`, research docs included.

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
- Hyperliquid keeps an API wallet approved for 90 days (seen live
  2026-11-13). The connect result and page give the end date and say to
  connect again before it.
- The new API wallet is approved as `pathiel-plugin`, never `pathiel`: the
  maintainer's live API wallet carries that name, and Hyperliquid replaces
  an agent when a new one is approved under the same name. Reconnecting
  replaces only the earlier `pathiel-plugin` wallet; the page says so.
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

[Unreleased]: https://github.com/Julian-dev28/pathiel/compare/v0.7.2...HEAD
[0.8.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.8.0
[0.7.2]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.7.2
[0.7.1]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.7.1
[0.7.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.7.0
[0.6.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.6.0
[0.5.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.5.0
[0.4.1]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.1
[0.4.0]: https://github.com/Julian-dev28/pathiel/releases/tag/v0.4.0
[0.3.0]: https://github.com/Julian-dev28/pathiel/commit/ae26611
