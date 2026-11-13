# Security

pathiel signs real orders on Hyperliquid with a key on your machine. This
page says where that key lives, how it is used, what pathiel never does, and
how to report a problem.

## Supported versions

| version | security fixes |
|---|---|
| 0.4.x | yes |
| 0.3.x and older | no, update to 0.4 |

## Where keys live

| what | where | notes |
|---|---|---|
| API wallet address and key | `~/.pathiel/.env.local` | created by `/pathiel:setup` with mode 600. The container reads the same folder, mounted at `/data` |
| Master account address | same file | a public address, not a secret |
| Master private key | nowhere | typed at a hidden prompt for one signature, then dropped (see below) |
| Intel keys (EIA, FRED, Congress) | same file, optional | free public-data keys; a missing one skips that source |

- The plugin folder is a cache Claude Code replaces on every update, so keys
  never go there. The launcher points pathiel at `~/.pathiel/.env.local`
  explicitly (`PATHIEL_ENV_FILE`).
- The env file is read once, when the server starts. A variable already set
  in the process environment wins over the file.
- Status output names keys and never prints a value.
  `scripts/public_status.py` runs every line through a redaction pass, and
  `scripts/preflight_secrets.py` fails on an env file that others can read.

## How each key is used

**The API wallet key** signs orders and cancels. An API wallet (agent
wallet) is created in the Hyperliquid app under More, API, and approved by
your master account there. Hyperliquid lets it trade and never lets it
withdraw or transfer funds. Revoke it in the app and it stops working at
once. On a public install, `scripts/approve_api_wallet.py` refuses to run:
API wallets are approved in the Hyperliquid app.

**The master key** is needed for one thing, and only if you choose it:
revoking a builder fee approval given to pathiel 0.4.0 with
`scripts/approve_builder_fee.py` (pathiel charges no fee now; see
[docs/BUILDER_FEE.md](docs/BUILDER_FEE.md)). On a public install:

- it is typed at a hidden prompt in your own terminal, and read from nowhere
  else;
- it is never printed, logged, written to a file or taken as an argument;
- a `HYPERLIQUID_MASTER_PRIVATE_KEY` in the env file is ignored with a
  warning, and `scripts/public_status.py` reports FAIL until you remove it;
- the script is a dry run unless you pass `--yes`, and it refuses before
  sending anything when the key's address is not your account, the rate is
  above 0.1%, or the network does not match.

`/pathiel:setup` never asks for a key in the chat, and Claude never runs the
signing line for you.

**No LLM API key.** The one model step (the desk thesis) runs through your
local Claude Code CLI. pathiel holds no Anthropic or other model key.

## What pathiel never does

- Withdraw, transfer or bridge funds. On a public install no tool can: the
  API wallet cannot, and the maintainer's treasury script refuses to run.
- Read, store or send your master key outside the one hidden prompt above.
- Place an order on any account but yours. `wallet_account` and
  `connected_wallets` only read.
- Trade in the background. There is no bot and no scheduler; an order goes
  out when Claude calls an order tool in your session, and Claude Code asks
  you before each of those calls unless you allow the tool for good.
- Send telemetry. Outbound calls go to Hyperliquid's API and to the public
  data sources the desk briefs read (news, EIA, FRED, Congress, prediction
  markets).
- Declare a key in any listing file (`server.json`,
  `glama.json`, the plugin manifests). A test enforces it.

## Checks that enforce this

| rule | check |
|---|---|
| Env file precedence and location | `scripts/_state_env.py`, `tests/test_plugin.py` |
| No master key in a deploy, no world-readable or tracked env file, no secret in the git index | `scripts/preflight_secrets.py` |
| Master key prompt-only on public installs; approval refusals send nothing | `tests/test_eval_builder_approval.py` |
| The test gate makes no network calls | `conftest.py` refuses every non-loopback host |
| No secret declared in listing files | `tests/test_listing_metadata.py` |
| Dependencies pinned with hashes; CI actions pinned to commit SHAs | `requirements.lock`, `scripts/supply_chain_check.py`, `tests/test_supply_chain.py` |

The secrets inventory, with how to rotate each one, is
[docs/SECRETS.md](docs/SECRETS.md).

## Reporting a vulnerability

Report it privately through GitHub:
[open a security advisory](https://github.com/Julian-dev28/pathiel/security/advisories/new)
on this repo. Do not open a public issue for a security problem.

Include what you found, how to reproduce it, and the version
(`claude plugin list` shows it). Never include a private key, even a
testnet one. The maintainer aims to reply within 7 days. Fixes ship as a patch
release with a `Security` entry in [CHANGELOG.md](CHANGELOG.md).

If a key of yours may have leaked: revoke the API wallet in the Hyperliquid
app (More, API) first, then report.
