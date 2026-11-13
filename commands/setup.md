---
description: First-run setup for the pathiel plugin - show the network and the builder fee, build the Python environment, create ~/.pathiel/.env.local, check it, approve the fee if the user wants, connect the MCP server
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
   > - Builder fee: orders pathiel places for you can carry a Hyperliquid
   >   builder fee of **0.01% of notional per fill** (1 basis point; 0.02% for
   >   a round trip), paid to the pathiel builder address. It is charged only
   >   after you approve it once with your own signature, and Hyperliquid
   >   enforces the approved rate: it rejects any order with a higher fee. If
   >   you never approve it, orders go out without it and everything still
   >   works.
   > - Trading perpetual futures with leverage can lose all of the money in
   >   the account. pathiel is a tool, not financial advice, and it promises
   >   no return.
   > - Hyperliquid does not serve restricted jurisdictions, including US
   >   persons. Check Hyperliquid's terms for where you live before you
   >   trade.

1. **Build the environment** (idempotent, about a minute on the first run):

       bash "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp" --bootstrap

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
     desk trades WTI (`xyz:CL`) there as the Brent stand-in. A builder fee
     approval signed on testnet does not carry over: after switching back to
     mainnet, rerun `--commands` and the `approve-sign` line (step 5).

   Mainnet state lives in `~/.pathiel/state` (where pathiel 0.3.0 kept it),
   testnet state in `~/.pathiel/state-testnet`. Upgrading from 0.3.0 keeps
   mainnet, as before.

3. **The env file.** If `~/.pathiel/.env.local` does not exist, create it
   from the plugin's template and set its mode to 600:

       mkdir -p ~/.pathiel
       cp "${CLAUDE_PLUGIN_ROOT}/config/public/env.local.example" ~/.pathiel/.env.local
       chmod 600 ~/.pathiel/.env.local

   For read-only mode the file can stay as it is. For trading, tell the user
   to open `~/.pathiel/.env.local` and fill in:

   - `HYPERLIQUID_WALLET_ADDRESS` and `HYPERLIQUID_PRIVATE_KEY`: an **API
     wallet** (agent wallet) created in the Hyperliquid app under More, API
     and approved by their main account. It can trade and cannot withdraw.
     Never the main account's key.
   - `HYPERLIQUID_MASTER_ADDRESS`: the main account the API wallet trades
     for. A public address.

   Testnet has its own accounts. The API wallet for testnet must be created
   and approved in the testnet app, https://app.hyperliquid-testnet.xyz, and
   the testnet account funded with test USDC there. A mainnet API wallet does
   not work on testnet, and a testnet one does not work on mainnet.

   The intel keys (`EIA_API_KEY`, `FRED_API_KEY`, `CONGRESS_API_KEY`) are
   optional and free. Wait for the user to say the file is ready.

4. **Check the install** without reading any key:

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/public_status.py

   Every line must be PASS or WARN. Show the user the `network` and
   `trading` lines. A FAIL names the problem; fix it and rerun.

5. **Builder fee approval (optional).** Tell the user the exact rate again:
   0.01% of notional per fill. Then get the commands for their own terminal:

       bash "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp" --commands

   It prints three lines, `status`, `approve-dry` and `approve-sign`, each a
   label, a tab, then a command with absolute paths. Both approve lines run
   `scripts/approve_builder_fee.py`. Run the `approve-dry` command yourself
   and show its output: it says what would be signed and signs nothing. Then give the user the `approve-sign` command **verbatim**
   to paste into their own terminal. It asks for the main account's private
   key at a hidden prompt there, signs the approval once and keeps the key
   nowhere. Never run the `approve-sign` command yourself and never ask for
   that key in the chat.

   The approval is per network: it is signed on the network the env file
   names when the line runs (mainnet unless it says
   `PATHIEL_NETWORK=testnet`). Tell the user that an approval signed on
   testnet does not cover mainnet; after switching to mainnet, rerun
   `--commands` and the `approve-sign` line once more.

   Skipping this step is fine: orders then carry no builder fee and
   everything else works. The printed paths change after a plugin update;
   rerun `--commands` for fresh ones instead of reusing an old line.

6. **Connect the tools.** The pathiel server reads the keys and the network
   once, when it starts, and it started with this Claude Code session,
   before the env file existed. After creating or editing
   `~/.pathiel/.env.local`, tell the user to reconnect pathiel in `/mcp`
   (or restart Claude Code). Then confirm in `/mcp` that the `pathiel`
   server shows as connected with 60 tools. If it does not, the error is in
   the MCP panel; the usual cause is step 1 not having finished.

7. **Prove it.** Call `get_price` with `{coin: "BTC"}`, then `state`. The
   `state` output names the network, whether a key is set, and the builder
   fee status. Check that the network and key it shows match the `network`
   and `trading` lines from step 4. If they differ, the server still runs
   with the old env file: tell the user to reconnect pathiel in `/mcp`, then
   call `state` again.

8. **Say what is next.** `desk_list` then `desk_brief {desk: "oil"}` reads
   the current call. `/pathiel:desk-tick oil` runs one tick of the loop;
   `/loop 15m /pathiel:desk-tick oil` keeps it running for the session.
   Every order goes through `place_bracket` with an exact stop and target,
   sized by the risk profile, and the user confirms each one. On testnet
   the oil desk's brief opens with the line that it trades WTI (`xyz:CL`)
   as the stand-in for Brent (`xyz:BRENTOIL`, not listed there); other
   markets testnet does not list are refused for entries, with the reason.
