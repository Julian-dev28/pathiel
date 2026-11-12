# Builder fee

pathiel is free to install. On a public install it adds a small Hyperliquid
builder fee to the orders it places for you, once you approve it. This page
says what the fee is, what it costs, how you approve it, how Hyperliquid
enforces it and how to turn it off.

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
state never blocks or fails an order.

No fee is attached when no builder address is configured, or when your
trading account is the builder address.

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

A public install trades on testnet until you switch it to mainnet. Approvals
are separate per network: an approval on testnet does nothing on mainnet. The
script prints the network first; run it again after you switch to mainnet.

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
- A running pathiel notices a new approval within about a minute, or at once
  on the next `state` call.

## For whoever runs the builder address

Hyperliquid pays builder fees only to an address that holds at least 100 USDC
of perps account value. pathiel does not check this. The builder address is
set in one place, `DEFAULT_BUILDER_ADDRESS` in `contracts/runtime/__init__.py`,
and `PATHIEL_BUILDER_ADDRESS` overrides it.
