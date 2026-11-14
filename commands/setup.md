---
description: First-run setup for the pathiel plugin - show the network and the risks, build the Python environment, create ~/.pathiel/.env.local, check it, connect the MCP server
---

# /pathiel:setup

Walk the user through connecting pathiel to Hyperliquid. Do the steps in
order and stop at the first one that fails, with the exact error.
Never print the value of any key, ever. Names only. Never ask for a key in
the chat; the user types keys into files and prompts on their own machine.

0. **Tell the user this first, before any key is involved.** Show it as it
   is:

   > - pathiel runs on Hyperliquid **mainnet** by default. With a key it
   >   places real orders with **real money**. To practise first with test
   >   money, add one line, `PATHIEL_NETWORK=testnet`, to
   >   `~/.pathiel/.env.local`. On testnet the oil desk trades WTI (`xyz:CL`)
   >   as the stand-in for Brent, which testnet does not list.
   > - Fee: **none**. pathiel charges no fee and its orders carry no
   >   Hyperliquid builder fee; you pay only Hyperliquid's own trading fee.
   >   If that ever changes it ships as a new version, and Hyperliquid
   >   charges nothing until you approve that exact rate yourself.
   > - Trading perpetual futures with leverage can lose all of the money in
   >   the account. pathiel is a tool, not financial advice, and it promises
   >   no return.
   > - Hyperliquid does not serve restricted jurisdictions, including US
   >   persons. Check Hyperliquid's terms for where you live before you
   >   trade.

1. **Build the environment** (idempotent, about a minute on the first run):

       bash "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp.sh" --bootstrap

   It prints the Python it uses, the env file, the state directory, the
   config file and the network. A fresh install gets the public default risk
   profile in the config file: at most 3x leverage, 1% of equity at risk per
   trade, one position at a time.

2. **Choose a mode with the user.** Ask which one they want:

   - **Read-only** (no key): prices, candles, order books, funding and the
     desk briefs work, with mainnet market data; order tools answer
     "read-only mode". Nothing to fill in, and nothing can be traded.
   - **Mainnet trading** (the default network): **real money**. An API
     wallet key from the mainnet app. Every order risks the account's real
     funds.
   - **Testnet trading** (practice): the user adds `PATHIEL_NETWORK=testnet`
     to the env file themselves (the template has the line, commented out),
     and an API wallet key from the testnet app. Test funds only. The oil
     desk trades WTI (`xyz:CL`) there as the Brent stand-in.

   Mainnet state lives in `~/.pathiel/state` (where pathiel 0.3.0 kept it),
   testnet state in `~/.pathiel/state-testnet`. Upgrading from 0.3.0 keeps
   mainnet, as before.

3. **The env file.** If `~/.pathiel/.env.local` does not exist, create it
   from the plugin's template and set its mode to 600:

       mkdir -p ~/.pathiel
       cp "${CLAUDE_PLUGIN_ROOT}/config/public/env.local.example" ~/.pathiel/.env.local
       chmod 600 ~/.pathiel/.env.local

   For read-only mode the file can stay as it is. For trading, a testnet
   user first adds `PATHIEL_NETWORK=testnet` to the file and reconnects
   pathiel in `/mcp`, so the key is made for the network they will trade
   on. Then offer two ways to add the key, in this order:

   - **Connect your wallet (recommended).** Call the `connect_wallet` tool;
     Claude Code asks the user first. It makes a new API wallet key on this
     machine and opens a local page in the browser. The page shows the
     network (MAINNET in red) and the new API wallet's address, and the
     user approves it with one signature in their browser wallet
     (MetaMask, Rabby, Coinbase Wallet or any other). The key can trade and
     never withdraw; the main key stays in the wallet. Give the user the
     `url` it returns in case no browser opened, then call `connect_wallet`
     with `{"status": true}` until the state is `done` or `failed`. `done`
     means the key and both addresses are in `~/.pathiel/.env.local`: go to
     step 4. A `failed` result names the reason in `error`; fix it and call
     again. `key_exists` means a key is set already: ask the user before
     calling again with `{"replace": true}`. If the tool is not listed, run
     the same flow as a script in the background (it waits up to 10
     minutes) and show the user the URL it prints:

         "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/connect_wallet.py

   - **Paste an API wallet (fallback),** for a user without a browser
     wallet or who prefers the Hyperliquid app. Tell the user to open
     `~/.pathiel/.env.local` and fill in:

     - `HYPERLIQUID_WALLET_ADDRESS` and `HYPERLIQUID_PRIVATE_KEY`: an **API
       wallet** (agent wallet) created in the Hyperliquid app under More,
       API and approved by their main account. It can trade and cannot
       withdraw. Never the main account's key.
     - `HYPERLIQUID_MASTER_ADDRESS`: the main account the API wallet trades
       for. A public address.

   Testnet has its own accounts. Pasted by hand, the API wallet for testnet
   must be created and approved in the testnet app,
   https://app.hyperliquid-testnet.xyz, and the testnet account funded with
   test USDC there. A mainnet API wallet does
   not work on testnet, and a testnet one does not work on mainnet.

   The intel keys (`EIA_API_KEY`, `FRED_API_KEY`, `CONGRESS_API_KEY`) are
   optional and free. Wait for the user to say the file is ready.

4. **Check the install** without reading any key:

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/public_status.py

   Every line must be PASS or WARN. Show the user the `network` and
   `trading` lines. A FAIL names the problem; fix it and rerun.

5. **Builder fee: nothing to do.** pathiel charges no fee, so there is
   nothing to approve and no master key to ask for. Never ask for the main
   account's private key in the chat. If the user approved the 0.01% fee of
   pathiel 0.4.0 and wants to revoke it, point them to "Revoking an old
   approval" in `docs/BUILDER_FEE.md`: the `approve-dry` line that
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp.sh" --commands` prints, run
   with `--max-fee-rate 0%` (`scripts/approve_builder_fee.py`), in their own
   terminal.

6. **Connect the tools.** The pathiel server reads the keys and the network
   once, when it starts, and it started with this Claude Code session,
   before the env file existed. After creating or editing
   `~/.pathiel/.env.local`, tell the user to reconnect pathiel in `/mcp`
   (or restart Claude Code). Then confirm in `/mcp` that the `pathiel`
   server shows as connected with 30 tools. If it does not, the error is in
   the MCP panel; the usual cause is step 1 not having finished.

7. **Prove it.** Call `get_market` with `{coin: "BTC"}`, then `get_status`. The
   `get_status` output names the network, whether a key is set, and the builder
   fee status. Check that the network and key it shows match the `network`
   and `trading` lines from step 4. If they differ, the server still runs
   with the old env file: tell the user to reconnect pathiel in `/mcp`, then
   call `get_status` again.

8. **Say what is next.** `list_desks` then `get_desk_brief {desk: "oil"}` reads
   the current call. `/pathiel:desk-tick oil` runs one tick of the loop;
   `/loop 15m /pathiel:desk-tick oil` keeps it running for the session.
   Every order goes through `place_bracket` with an exact stop and target,
   sized by the risk profile, and the user confirms each one. On testnet
   the oil desk's brief opens with the line that it trades WTI (`xyz:CL`)
   as the stand-in for Brent (`xyz:BRENTOIL`, not listed there); other
   markets testnet does not list are refused for entries, with the reason.
