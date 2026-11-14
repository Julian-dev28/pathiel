# Terms of use

These terms cover using pathiel: the Claude Code plugin, the MCP server, the
container image and the website. By installing or running pathiel you accept
them.

## The software

pathiel is open-source software under the [MIT license](../LICENSE). It is
provided as is, without warranty of any kind, as that license says. The
maintainer is not liable for any loss that comes from using it, including
trading losses, missed or failed orders, bugs, outages of Hyperliquid or of
any data feed, or a key you lose.

## Not financial advice

pathiel is a tool, not an adviser, broker or fund. Desk briefs, levels,
grades and scoreboard rows are information, not recommendations. No edge is
demonstrated and no return is promised. Leveraged perpetual futures can lose
the whole account. You decide every trade and carry its result.

## Hyperliquid

pathiel places orders on Hyperliquid for your own account, with an API
wallet you approve. Your use of Hyperliquid is under Hyperliquid's own terms,
including its list of restricted jurisdictions, which bars US persons among
others. pathiel does not check where you are; staying within the rules where
you live is your responsibility.

## Your keys

Keys stay on your machine, in `~/.pathiel/.env.local`. You are responsible
for keeping them safe and for revoking an API wallet you no longer use, in
the Hyperliquid app (More, API). The API wallet can trade and can never
withdraw. pathiel never stores your master key.

## Fees

pathiel charges no fee: no order carries a Hyperliquid builder fee. If that
ever changes, it ships as a new version with the rate in the changelog, and
Hyperliquid charges a builder fee only after you approve that exact rate
yourself.

## The research pool

The research pool is opt-in. Joining publishes the graded results described
in [PRIVACY.md](PRIVACY.md), at least 24 hours late; you can leave at any
time. No payout, refund or reward is tied to your returns.

## Changes

These terms can change with a new version of pathiel. Changes are listed in
the [changelog](../CHANGELOG.md). Questions:
<https://github.com/Julian-dev28/pathiel/issues>.
