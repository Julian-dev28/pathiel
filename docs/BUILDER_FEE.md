# Builder fee

**Status: waived since pathiel 0.4.1 (2026-11-13).** pathiel charges no fee.
No order it places carries a Hyperliquid builder fee, whether or not you
approved one, and there is nothing to approve. You pay only Hyperliquid's own
trading fee.

## Revoking an old approval

pathiel 0.4.0 asked users to approve 0.01%. That approval stays on
Hyperliquid, but pathiel no longer attaches the fee, so it costs nothing. To
remove it anyway, in your own terminal:

1. `bash "<plugin root>/scripts/pathiel-mcp" --commands` prints an
   `approve-dry` line. Run it with `--max-fee-rate 0%` added: a dry run that
   signs and sends nothing.
2. Run the same line with `--max-fee-rate 0% --yes`. It asks for your master
   private key at a hidden prompt, signs a 0% approval and keeps the key
   nowhere.

The 0% approval follows Hyperliquid's action format but has not been tried on
the live venue. Since nothing is charged, you lose nothing by leaving the old
approval in place.

## Turning the fee back on (maintainer)

1. `contracts/runtime/__init__.py`: `BUILDER_FEE_WAIVED = False`. The default
   rate becomes `LISTED_BUILDER_FEE_TENTHS_BP` (10, 0.01%).
2. Restore the fee copy: README "Builder fee" sections, the `plugin.json` and
   `marketplace.json` descriptions, `commands/setup.md` (the disclosure and
   step 5), `docs/GETTING_STARTED.md`, `SECURITY.md`,
   `config/public/env.local.example`, the research pool's "not a fee waiver"
   lines (README, `commands/share.md`, `docs/PRIVACY.md`) and the top of
   this page. The 0.4.0 text
   is in git history. `tests/test_public_copy.py`, `tests/test_plugin.py` and
   `tests/test_listing_metadata.py` pick their checks by the switch and fail
   until the copy matches it.
3. Bump the version, put the rate in the changelog and the release notes,
   publish the plugin bundle and the release (CONTRIBUTING.md, Releasing).

One thing to say in those notes: a user who approved 0.01% under 0.4.0 and
never revoked it is charged again from the first order, with no new
signature, because Hyperliquid's approval is still in place. A higher rate
than the one they approved is rejected by Hyperliquid until they sign again.

The rest of this page is how the fee works when it is on.

## What a builder fee is

Hyperliquid lets the software that routes an order add its own fee to that
order, on top of Hyperliquid's trading fee. The software names an address
(the builder) and a rate. The fee is taken from your account on each fill and
paid to the builder address. Hyperliquid's own fees are charged as usual and
are separate.

## The rate

| | |
|---|---|
| Rate | 0.01% of notional per fill (1 basis point) |
| In Hyperliquid's units | `f = 10` (tenths of a basis point) |
| Round trip (open and close) | 0.02% of notional |
| Hyperliquid's perp cap | 0.1% |

Worked example: you open a 2,000 USD position and later close it. The
opening fill costs 0.20 USD in builder fee, the closing fill 0.20 USD, so
0.40 USD for the round trip.

It applies to every fill of every order pathiel places for you: entries,
stops, targets, closes and the cleanup orders `bracket_sweep` places. Orders
you place yourself in the Hyperliquid app carry no pathiel fee.

## When you are charged

Only after you approve. Until then, pathiel places every order without the
builder fee. The same is true when you approved a lower rate, when the
approval cannot be read, or when Hyperliquid refuses an order that carried
the fee: the order is placed without it and the tool output says so. Builder
state never blocks or fails an order. An order Hyperliquid refuses for its own
reason (no match, margin, price) is not sent again and is never charged.

No fee is attached when no builder address is configured, or when your
trading account is the builder address (compared without regard to case).

## How to approve

1. Run `/pathiel:setup` in Claude Code. It prints the exact command lines for
   your machine (`scripts/pathiel-mcp --commands` prints the same lines).
2. Run the dry run line first. It signs and sends nothing and needs no key.
   It shows the network, your trading account, the builder address, the rate
   and what the fee costs on 100 USD of notional.
3. Run the signing line (the same command with `--yes`) in your own terminal.
   It asks for your master private key at a hidden prompt, signs one
   approval, sends it, then reads the approval back and prints it.

The script is `scripts/approve_builder_fee.py`:

```
approve_builder_fee.py [--max-fee-rate RATE] [--yes] [--json]
```

With no flags it is a dry run. `--max-fee-rate` defaults to the rate pathiel
charges. `--json` prints one JSON object.

### Which key

The master key: the key of the wallet that holds your funds. Not the API
wallet key pathiel trades with. Hyperliquid only accepts a builder approval
signed by the master wallet. The script refuses a key whose address is not
your trading account (`HYPERLIQUID_MASTER_ADDRESS`) before anything is sent.

The master key is typed at the prompt, used for one signature and dropped. It
is never printed, logged, written to a file or taken as an argument. Do not
put it in `~/.pathiel/.env.local`: the trading process reads that file. On a
public install the script ignores `HYPERLIQUID_MASTER_PRIVATE_KEY` if it is
set there and prints a warning that names the variable.

The script refuses, and sends nothing, when:

- no builder address is configured
- the builder address is your own trading account
- the rate is above 0.1%
- the venue URL this process would post to does not match the network
- the key's address is not your trading account
- there is no key to read (no terminal for the hidden prompt)

## How Hyperliquid enforces it

Your approval is a signed action that Hyperliquid stores: a maximum fee rate
for one builder address. Every order pathiel sends with the builder code
names its fee. Hyperliquid rejects any order whose builder fee is above the
maximum you approved. pathiel asks you to approve exactly the rate it
charges, 0.01%, so a higher fee would need a new signature from you.
Hyperliquid caps perp builder fees at 0.1%.

## Testnet and mainnet

A public install trades on mainnet, with real money, unless
`PATHIEL_NETWORK=testnet` is in `~/.pathiel/.env.local`. Approvals are
separate per network: an approval on testnet does nothing on mainnet. The
script prints the network first; run it again on each network you trade.

## How to stop

- Set `PATHIEL_BUILDER_FEE_TENTHS_BP=0` in `~/.pathiel/.env.local` and restart
  Claude Code. From then on every order goes out without a builder fee. This
  is the certain way.
- Or revoke the approval: run the signing line with `--max-fee-rate 0%`. This
  follows Hyperliquid's action format but has not been tried on the live
  venue; use the setting above if you want to be sure.

## Where to see it

- The `state` tool shows the builder address, the fee, the rate you approved
  and whether orders carry the fee right now, with the reason.
- Every order tool (`place_bracket`, `close_position`, `set_target`,
  `bracket_sweep`) says whether that order carried the fee.
- A running pathiel notices a new approval within about a minute. The
  `state` tool reads it at once: when it has not seen an approval yet, it asks
  Hyperliquid again, waiting at most 4 seconds.
  `scripts/public_status.py --online` reads it at once too.

## The builder address

The fee goes to `0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985`, the
maintainer's own account. The approval you sign names this address, and the
dry run prints it before anything is signed.

Hyperliquid pays builder fees only to an address that holds at least 100 USDC
of perps account value. pathiel does not check this. The builder address is
set in one place, `DEFAULT_BUILDER_ADDRESS` in `contracts/runtime/__init__.py`.
`PATHIEL_BUILDER_ADDRESS` overrides it: another address to send the fee
elsewhere (a fork), or any value that is not an address, such as `none`, to
attach no builder at all.
