---
description: First-run setup for pathiel from the plugin directory - show the network and the risks, install the pathiel MCP server with Docker, create ~/.pathiel/.env.local, connect and check it
---

# /pathiel:setup

Walk the user through connecting pathiel to Hyperliquid. Do the steps in
order and stop at the first one that fails, with the exact error.
Never print the value of any key, ever. Names only. Never ask for a key in
the chat; the user types keys into a file on their own machine.

This plugin carries no server and runs no program by itself. The pathiel
MCP server is a container the user installs once, in step 1, from
`ghcr.io/julian-dev28/pathiel-mcp` (source: github.com/Julian-dev28/pathiel).

0. **Tell the user this first, before any key is involved.** Show it as it
   is:

   > - pathiel runs on Hyperliquid **mainnet** by default. With a key it
   >   places real orders with **real money**. To practise first with test
   >   money, add one line, `PATHIEL_NETWORK=testnet`, to
   >   `~/.pathiel/.env.local`.
   > - Fee: **none**. pathiel charges no fee and its orders carry no
   >   Hyperliquid builder fee; you pay only Hyperliquid's own trading fee.
   > - Trading perpetual futures with leverage can lose all of the money in
   >   the account. pathiel is a tool, not financial advice, and it promises
   >   no return.
   > - Hyperliquid does not serve restricted jurisdictions, including US
   >   persons. Check Hyperliquid's terms for where you live before you
   >   trade.

1. **Install the server** (needs Docker; about a minute the first time).
   Ask the user to run these in their own terminal:

       mkdir -p ~/.pathiel && chmod 700 ~/.pathiel
       claude mcp add pathiel -s user -- docker run -i --rm \
         --user "$(id -u):$(id -g)" -v "$HOME/.pathiel:/data" \
         ghcr.io/julian-dev28/pathiel-mcp:0.7.0

   Without Docker, point them to "Other MCP clients" in the project README
   (github.com/Julian-dev28/pathiel) and stop here.

2. **Choose a mode with the user.** Read-only (no key: prices, candles,
   books, funding, desk briefs, any wallet's positions) or trading (an API
   wallet key; orders are real on mainnet). For read-only, skip to step 4.

3. **The env file, for trading.** Ask the user to create
   `~/.pathiel/.env.local` with mode 600 (`touch` it, then `chmod 600`) and
   to type these lines into it themselves:

       HYPERLIQUID_WALLET_ADDRESS=
       HYPERLIQUID_PRIVATE_KEY=
       HYPERLIQUID_MASTER_ADDRESS=

   - `HYPERLIQUID_WALLET_ADDRESS` and `HYPERLIQUID_PRIVATE_KEY`: an **API
     wallet** (agent wallet) created in the Hyperliquid app under More, API
     and approved by their main account. It can trade and cannot withdraw.
     Never the main account's key.
   - `HYPERLIQUID_MASTER_ADDRESS`: the main account the API wallet trades
     for. A public address.
   - For testnet, add `PATHIEL_NETWORK=testnet`, and make and approve the API
     wallet in the testnet app, https://app.hyperliquid-testnet.xyz. A
     mainnet API wallet does not work on testnet, and a testnet one does not
     work on mainnet.

   Wait for the user to say the file is ready.

4. **Connect the tools.** The server reads the file once, when it starts.
   Tell the user to reconnect pathiel in `/mcp` (or restart Claude Code),
   then confirm in `/mcp` that `pathiel` shows as connected with 30 tools.
   If it does not, the error is in the MCP panel; the usual causes are
   Docker not running or the image still downloading.

5. **Prove it.** Call `get_market` with `{coin: "BTC"}`, then `get_status`.
   `get_status` names the network and whether trading is enabled. Show the
   user both. If trading shows read-only after they filled the file, the
   server started before the file changed: reconnect pathiel in `/mcp`
   again, then call `get_status` again.

6. **Say what is next.** `list_desks`, then `get_desk_brief {desk: "oil"}`,
   reads a desk's current call with "Your size" for this account. Size any
   trade with `size_trade` before `place_bracket`, and run `sweep_brackets`
   on the coin after every close.
