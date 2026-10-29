---
description: First-run setup for the pathiel plugin - build its Python environment, create ~/.pathiel/.env.local, check the keys, connect the MCP server
---

# /pathiel:setup

Walk the user through connecting pathiel to their Hyperliquid account. Do the
steps in order and stop at the first one that fails, with the exact error.
Never print the value of any key, ever. Names only.

1. **Build the environment** (idempotent, about a minute on the first run):

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp" --bootstrap

   It prints where the venv, the env file and the state directory live.

2. **Keys.** If `~/.pathiel/.env.local` does not exist, create it from the
   template and set its mode to 0600:

       mkdir -p ~/.pathiel
       cp "${CLAUDE_PLUGIN_ROOT}/.env.local.example" ~/.pathiel/.env.local
       chmod 600 ~/.pathiel/.env.local

   Then tell the user to open `~/.pathiel/.env.local` and fill in
   `HYPERLIQUID_WALLET_ADDRESS` and `HYPERLIQUID_PRIVATE_KEY` (an agent wallet
   created in the Hyperliquid UI under More, API; never the master key),
   and `HYPERLIQUID_MASTER_ADDRESS` (the account the agent acts for).
   The intel keys (`EIA_API_KEY`, `FRED_API_KEY`, `CONGRESS_API_KEY`) are
   optional and free. Wait for the user to say the file is filled in.

3. **Check the keys** without reading them:

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/preflight_secrets.py

   Every line must be PASS or WARN. A FAIL names the variable; fix it and rerun.

4. **Connect the tools.** Tell the user to run `/mcp` and confirm the
   `pathiel` server shows as connected with 60 tools. If it does not, the
   error is in the MCP panel; the usual cause is step 1 not having finished.

5. **Prove it end to end** (read-only calls against the live venue, about a
   minute):

       "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/mcp_smoke.py

   Expect `0 failure(s)`.

6. **Say what is next.** `desk_list` then `desk_brief {desk: "oil"}` reads the
   current call. `/pathiel:desk-tick oil` runs one tick of the loop;
   `/loop 15m /pathiel:desk-tick oil` keeps it running for the session. The
   account starts in mode OFF; the `config` tool flips it to LIVE when the
   user asks. Every order goes through `place_bracket` with an exact stop
   and target, sized by the risk profile, and the user confirms each one.
