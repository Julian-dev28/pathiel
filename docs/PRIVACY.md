# Privacy

## The plugin

pathiel runs on your computer. It has no account system, no analytics and
no telemetry, and the project runs no server that receives your data.

- **Keys** stay in `~/.pathiel/.env.local` (mode 600) on your machine. The
  API wallet key signs requests to Hyperliquid and is never printed, logged
  or sent anywhere else. Your master key is never stored; wallet connect has
  your browser wallet sign one approval on a page served from 127.0.0.1.
- **What leaves your machine:** requests to the Hyperliquid API (market and
  account reads, the orders you ask for, the API wallet approval you sign),
  and reads of public data feeds for the desk briefs (EIA, FRED, CFTC,
  Congress and Federal Register, prediction markets, news). An optional API
  key for one of those feeds goes only to that feed.
- **What stays local:** theses, the desk journal, caches and your config,
  under `~/.pathiel`, until you delete them.
- **The research pool** below is the one thing that publishes anything, and
  only if you join.

Questions or a problem: <https://github.com/Julian-dev28/pathiel/issues>,
or privately through <https://github.com/Julian-dev28/pathiel/security/advisories/new>.

# The research pool

pathiel's research pool is opt-in. It is off by default: if you do not join,
nothing about how pathiel trades for you changes, and nothing about your
account is read for this reason. This page says what joining reads, what it
publishes, what it signs, and how to leave or come back.

This is not financial advice. No edge is demonstrated by joining, or by
anything the pool's stats show.

## What the research pool is

You register a Hyperliquid address with one signed statement. From the time
you sign, pathiel reads that address's public fills and public order history
and grades them with the same harness the project uses on its own trades.
Graded rows are published on a public scoreboard, at least 24 hours late.

You upload nothing. The fills and orders pathiel reads are already public on
Hyperliquid's own chain; joining only tells pathiel to read and grade yours.

## What is read

For the address you register, on the pool coins (`xyz:BRENTOIL`, `xyz:CL`,
`xyz:SILVER`, `xyz:GOLD`):

- Public fills and public order history, read from Hyperliquid's own public
  endpoint. No key is needed to read them and none is used.
- Candles for those coins, used to grade a trade's result.

The fills request Hyperliquid answers carries every coin that address
traded, not only the pool coins. Fills on any other coin are dropped before
anything is computed or written; they are read off the wire and discarded in
the same step.

## What is never read

- The account's balance, or its equity.
- Deposits and withdrawals.
- Funding payments.
- Open positions.
- Any key, for signing or for trading.

## What is published

- Your account address, shortened, with a link to its public explorer page.
- The date you joined.
- How many trades you closed on the pool coins since you joined, and how
  many were graded.
- Your graded row: win rate, mean result in R, their ranges, a p value, a
  label (EDGE, LEAK, LEANING, FLUKE or NOISE) and a luck score.
- Pool totals that include your trades.

## What is not published

- Anything closed in the last 24 hours, and any open position.
- Single trades, prices, sizes, dollar results, account value or leverage.
- Fills on coins outside the pool coins.
- Any key. Nothing is uploaded: pathiel reads fills that Hyperliquid
  already makes public.

## The 24 hour delay

A trade reaches the scoreboard only once it has closed and only once
midnight UTC of the day before the build has passed it by. That holds every
published trade to at least 24 hours old, and most of the time closer to 48.
Nothing from the last 24 hours, and no open position, is ever shown, no
matter when you look.

## What you sign

Joining signs one plain text message, in the EIP-191 personal-message
format. It is not a Hyperliquid order and cannot be replayed as one: an
order is a different, typed format, and this message names the network, the
account, the signer, the time and the project, so it cannot be moved to
another account, another time, or turned into something else later.

The exact text, with your account, your signing address and the time filled
in:

```
pathiel research pool: join
Version: 1
Network: hyperliquid-mainnet
Account: <your account>
Signer: <the signing address>
Joined at: <time>
Project: github.com/Julian-dev28/pathiel

I am the owner of this Hyperliquid account, or an API wallet it approved.
I ask pathiel to grade this account's public fills on the pool coins from the time above.
I agree that the graded results are published at least 24 hours late on the public scoreboard, next to this address.
No trade, transfer, fee or payment is authorized by this statement.
I can leave at any time with a signed leave statement.
```

Who may sign it: your account's own key, or an API wallet your account has
approved. When an API wallet signs, pathiel asks Hyperliquid once, when your
pull request is checked, whether your account currently approves that
wallet. A wallet that is not approved cannot join you.

## What joining reveals

Your fills are already public on the chain; anyone can read them today with
your address. Joining the pool adds two things the chain alone does not say:
that this address uses pathiel, and a graded summary of its trades in one
place next to it.

## How to leave

Leaving takes one more signed statement, the same way joining does: the next
scoreboard build drops your address and every row of it.

If you would rather not sign anything, ask in a GitHub issue instead. The
maintainer removes the member file on request, no signature needed. Either
way, the next build drops your row and the maintainer's local graded data
for your address.

## Coming back

Coming back takes a new signed join. Only trades after the new join count.

Nobody can put your account back in the pool by deleting your leave file:
the check that runs on every pull request refuses it. A leave stays in force
until a new, later join replaces it.

## Who can take you out

You, the maintainer on your request, and the API wallet that signed your
join, even after you revoked it, can all take your account out of the pool.
A leave only removes your published rows; it moves no money and reads
nothing, and you can join again with your current key whenever you want.

## What stays after leaving

Git history keeps every version of your member file and every old
scoreboard that ever carried your row; that is how git works for every file
in the repository, not something the pool adds. The fills themselves stay on
the chain regardless: Hyperliquid's public record does not change because
you left the pool.

## Where things live

Signed statements live at `pool/members/` and `pool/leaves/` in the pathiel
repository, added by pull request. The scoreboard is a static page and a
JSON file, published at https://julian-dev28.github.io/pathiel/scoreboard/.
Nothing here runs on a server, because there is no server: the repository,
GitHub's checks on it, and GitHub Pages are the whole system.

## No rewards tied to returns

No payout, refund or reward is ever tied to your returns. Joining never
changes what you pay: pathiel charges no fee to anyone right now, member or
not, and if a fee ever returns it will be the same for members and
everyone else.

## Who to ask

Open a GitHub issue in the pathiel repository. That works for leaving
without a signature, for a question about what is published, or for
anything else about the pool.
