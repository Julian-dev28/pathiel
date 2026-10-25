# The wallet connect UI: what it was, how to bring it back, how to test it

Removed 2026-10-25. Operator: "delete it ... if this gets removed, make a doc
on how to restore and test it in the future, seems cool but for now i just
want the personal trading thing."

## What it was

A browser modal titled **Trading wallet**, opened from the Desk page's
onboarding card, the Account page's "Switch or add account" button, and the
"Connect" button on the Thesis page's trading banner. Three choices:

| choice | what happened |
|---|---|
| Connect your Hyperliquid wallet (recommended) | EIP-6963 wallet discovery, one signature approving an agent key pathiel generated and kept encrypted at rest (`services/accounts/vault.py`). The key could trade and never withdraw; the main key never left the wallet |
| Use an API key | paste the main address plus an API wallet key made on app.hyperliquid.xyz |
| Just watch an address | read-only account, no key |

Footer: "pathiel never custodies your funds. We skip the builder-fee step,
so your trades carry no added fee." The modal registered the account through
`services/accounts/api.py`, signed the operator in through `services/auth`
(SIWE), and fired a `pathiel:account-changed` event the pages listened to.

Why it went: the live desk's account comes from `.env.local` (shown as the
`source="env"` account), the registry held no connected account, and the
bundle carried the repo's only Node dependency tree, 498 packages, a 3.1 MB
committed `wallet.js`, Node in CI, and 32 transitive npm advisories whose fix
was a wagmi 3 upgrade with a new connector API.

## Where it lives in history

| what | where |
|---|---|
| last commit that has it all | `225acf4` (2026-10-25, before the removal commit) |
| the source tree | `services/wallet_ui/` (`src/`, `build.mjs`, `package.json`, `package-lock.json`, `.npmrc`, `bundle.manifest.json`, `README.md`, `tests/`) |
| the committed build | `pathiel/static/wallet.js`, `pathiel/static/wallet.css` |
| page hooks | `pathiel/static/pathiel.js` (`loadWalletUI`, `openWallet`, the `pathiel:account-changed` listener), `pathiel/templates/{account,desk,oil_desk}.html` (`<div id="wallet-connect-root">`), `account.html` (`#acct-switch`), `oil_desk.html` and `oil_desk.js` (`#onb-connect`, `#onb-watch`, the account-changed listener), the trading banner's Connect button |
| tests | `services/wallet_ui/tests/` (bundle, contract sync, fake accounts server, the browser flow), `tests/browser/test_connect_flow_real_app.py`, the wallet parts of `tests/test_dashboard_pages.py`, `tests/test_supply_chain.py`, `tests/test_deploy_config.py` |
| CI | the `Set up Node`, `npm ci --ignore-scripts`, `npm audit signatures` and `node build.mjs --verify` steps in `.github/workflows/ci.yml` |
| supply-chain checks | the npm half of `scripts/supply_chain_check.py` (lockfile integrity, install-script allow-list, exact pins, bundle manifest, drainer indicators) |

What stayed: every server-side piece. `services/accounts` (registry, vault,
positions, the connect routes), `services/auth` (SIWE sessions, the operator
role, API keys), `contracts/accounts` (the request and response models the
modal used, with their fixtures). A scripted client can still register an
account and sign in; only the browser modal is gone.

## How to restore it

```bash
# 1. bring the tree and the hooks back from the last commit that had them
git checkout 225acf4 -- services/wallet_ui pathiel/static/wallet.js pathiel/static/wallet.css \
    tests/browser/test_connect_flow_real_app.py
git show 225acf4:pathiel/static/pathiel.js > /tmp/pathiel.js.old      # diff the wallet loader back in
git show 225acf4:pathiel/templates/account.html > /tmp/account.html.old
git show 225acf4:pathiel/templates/oil_desk.html > /tmp/oil_desk.html.old
git show 225acf4:pathiel/static/oil_desk.js > /tmp/oil_desk.js.old
git show 225acf4:.github/workflows/ci.yml > /tmp/ci.yml.old            # the four Node steps
git show 225acf4:scripts/supply_chain_check.py > /tmp/supply_chain_check.py.old
git show 225acf4:tests/test_supply_chain.py > /tmp/test_supply_chain.py.old

# 2. install the toolchain without running any package's install scripts
cd services/wallet_ui && npm ci --ignore-scripts && cd -

# 3. do the wagmi 3 upgrade this removal skipped (docs/security/SUPPLY_CHAIN_2026-10-25.md, P3-5):
#    wagmi, viem, @wagmi/core, @wagmi/connectors and the Reown packages to versions that
#    resolve with no overrides; port src/ to the wagmi 3 connector API; keep the modal's
#    three choices and the account-changed event unchanged
```

Re-add the page hooks by hand from the diffs in step 1: the mount div in the
three templates, `loadWalletUI` / `openWallet` and the export in `pathiel.js`,
the two onboarding buttons and their listeners, the Account page button and
its listener. Put the Node steps back in `ci.yml` and the npm checks back in
`scripts/supply_chain_check.py` and `tests/test_supply_chain.py`. Reverse the
test edits of the removal commit (`git show <removal commit> -- tests/` lists
them): `test_every_page_loads_the_shared_script_and_no_wallet_picker` becomes
the old mount assertion again, `tests/test_page_polling.py` excludes
`wallet.js` again, `tests/test_deploy_config.py` excludes `services/wallet_ui`
from the import scan again.

The operator token does not need to come back with it: the modal signed the
operator in with a wallet, which is the role gate the routes still use.

## How to test it

```bash
# build: committed bytes must equal a fresh build from the lockfile
cd services/wallet_ui && node build.mjs && node build.mjs --verify && cd -

# offline gate (bundle manifest, contract sync, fake accounts server, supply chain)
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q -p no:cacheprovider \
    services/wallet_ui/tests tests/test_supply_chain.py tests/test_dashboard_pages.py

# the real flow in a real Chromium (Playwright is in .venv)
.venv/bin/python -m pytest -q -p no:cacheprovider -m browser \
    services/wallet_ui/tests/test_wallet_account_flow_browser.py \
    tests/browser/test_connect_flow_real_app.py tests/browser/test_pages_browser.py

# advisories on what ships
cd services/wallet_ui && npm audit --omit=dev && cd -
```

Acceptance: `--verify` passes, `wallet.js` stays under the 5 MB ceiling in
`tests/test_repo_hygiene.py`, the browser flow connects a mock wallet, signs,
registers the account and shows it on the Account page with zero console
errors, and `npm audit --omit=dev` reports no advisory in code that ships to
the browser.
