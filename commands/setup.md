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

   > - pathiel places real orders on Hyperliquid when it runs on mainnet.
   >   It starts on the Hyperliquid **testnet**, where the money is not real.
   >   Mainnet is an explicit opt-in, one line in `~/.pathiel/.env.local`.
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

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp" --bootstrap

   It prints the Python it uses, the env file, the state directory, the
   config file and the network. A fresh install gets the public default risk
   profile in the config file: at most 3x leverage, 1% of equity at risk per
   trade, one position at a time.

2. **Choose a mode with the user.** Ask which one they want:

   - **Read-only** (no key): prices, candles, order books, funding and the
     desk briefs work; order tools answer "read-only mode". Nothing to fill
     in. For real market data instead of testnet data, add the line
     `PATHIEL_NETWORK=mainnet`; with no key it still cannot place an order.
   - **Testnet trading** (the default): an API wallet key from the testnet
     app. Test funds only.
   - **Mainnet trading**: real money. The user adds `PATHIEL_NETWORK=mainnet`
     to the env file themselves, and an API wallet key from the mainnet app.

   Upgrading from pathiel 0.3.0: it ran on mainnet, and 0.4.0 starts on
   testnet. To keep trading on mainnet, the user adds
   `PATHIEL_NETWORK=mainnet`; mainnet state stays in `~/.pathiel/state`,
   testnet state goes to `~/.pathiel/state-testnet`.

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

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp" --commands

   It prints three lines, `status`, `approve-dry` and `approve-sign`, each a
   label, a tab, then a command with absolute paths. Both approve lines run
   `scripts/approve_builder_fee.py`. Run the `approve-dry` command yourself
   and show its output: it says what would be signed and signs nothing. Then give the user the `approve-sign` command **verbatim**
   to paste into their own terminal. It asks for the main account's private
   key at a hidden prompt there, signs the approval once and keeps the key
   nowhere. Never run the `approve-sign` command yourself and never ask for
   that key in the chat.

   Skipping this step is fine: orders then carry no builder fee and
   everything else works. The printed paths change after a plugin update;
   rerun `--commands` for fresh ones instead of reusing an old line.

6. **Connect the tools.** Tell the user to run `/mcp` and confirm the
   `pathiel` server shows as connected with 60 tools. If it does not, the
   error is in the MCP panel; the usual cause is step 1 not having finished.

7. **Prove it.** Call `get_price` with `{coin: "BTC"}`, then `state`. The
   `state` output names the network, whether a key is set, and the builder
   fee status.

8. **Say what is next.** `desk_list` then `desk_brief {desk: "oil"}` reads
   the current call. `/pathiel:desk-tick oil` runs one tick of the loop;
   `/loop 15m /pathiel:desk-tick oil` keeps it running for the session.
   Every order goes through `place_bracket` with an exact stop and target,
   sized by the risk profile, and the user confirms each one. On testnet
   some markets, such as the oil desk's `xyz:BRENTOIL`, may not be listed;
   pathiel says so instead of trading them.
