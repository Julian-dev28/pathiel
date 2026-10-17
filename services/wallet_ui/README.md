# services/wallet_ui — the trading wallet

React + RainbowKit + wagmi, bundled to a single vendored asset that the Jinja
dashboard loads on demand. Source here, output at `pathiel/static/wallet.js`
(+`wallet.css`).

Per contracts/accounts/models.py: connecting a Hyperliquid account IS the
identity, so there is no separate sign-in step. This bundle owns the whole
accounts surface — the header control (a "Connect" button with no accounts,
an account chip with one or more), the "Trading wallet" modal (connect a
wallet and authorize a trading key / paste an API key / watch an address),
and the account switcher (switch, rename, remove, disconnect).

```
src/
  lib/
    contract.ts   hand-mirrored types for contracts/accounts/models.py
    api.ts        fetch wrappers for every /api/accounts/* route
    validate.ts   address/key regexes, copied from the Python patterns
    errors.ts     classifies a failure (rejected / expired / chain mismatch / network)
    bridge.ts     the window.PathielWallet.open(view) mailbox
    focusTrap.ts  the modal's keyboard trap
  components/     WalletRoot (header + modal state), AccountChip, the modal
                  shell, and its four views (choose/connect/apikey/watch)
  App.tsx         wagmi/RainbowKit/react-query providers
  index.tsx       mount point + the window.PathielWallet bridge
```

`tests/test_contract_sync.py` is the thing that keeps `lib/contract.ts` and
`lib/validate.ts` honest against the Python side — see its docstring.

## What it replaced, and why

`pathiel/static/pathiel.js` used to call `window.ethereum` directly. With two
extensions installed that object is whichever one won a race at page load, there
was no way to choose between them, and the failure text told people who already
had a wallet to "install MetaMask or Rabby".

RainbowKit resolves wallets over EIP-6963, so each installed wallet announces
itself and the user picks the one they meant. That part is unchanged from the
first version of this bundle — what changed is everything built on top of a
connected wallet. The first version drove a SIWE sign-in flow against
services/auth (`src/auth.ts`, now deleted); this one drives agent-key
authorization against services/accounts instead, because the whole concept of
a separate "sign in" step went away with it. If you're reading old context
about `/auth/nonce` or `/auth/verify` from this bundle, it's stale — those
calls are gone.

## The contract this bundle provides (SWARM_RULES section 10)

- Mounts into `<div id="wallet-connect-root">` in the masthead.
- `window.PathielWallet.open(view)`, `view` one of `"choose" | "connect" |
  "apikey" | "watch"`. Safe to call before React has committed — see
  `lib/bridge.ts`'s docstring for why (a one-slot mailbox, not a click-retry
  loop like the version this replaced).
- `document` gets a `pathiel:account-changed` `CustomEvent` after any
  connect, switch, or removal; `detail` is the fresh `SessionInfo` from
  `GET /api/session`.

## Three decisions worth knowing before you change this

**Wallet discovery is wagmi's, not RainbowKit's registry.** The documented way
to build the list is `connectorsForWallets` with named wallets from
`@rainbow-me/rainbowkit/wallets`. Importing two names from that barrel cost
4.3 MB: it re-exports every wallet RainbowKit supports and esbuild cannot
tree-shake past the dynamic icon imports, so `@metamask/sdk` and the whole
`@reown`/`@walletconnect` relay stack come along whether or not anything can
reach them. wagmi's own `multiInjectedProviderDiscovery` builds one connector
per announced wallet and RainbowKit renders them, which is the same menu for
1.3 MB less.

**No WalletConnect, so no relay.** That drops phone wallets. It also drops a
vendor, a project ID, and a third party sitting between the user and their
signer — on an operator console opened from a desktop browser, for a product
whose whole posture is that it holds no key.

**The bundle is loaded on demand, not on page load.** It is ~390 KB brotli
against ~30 KB for the dashboard's entire vanilla JS. `pathiel.js` injects it on
the first click that needs a wallet, and a signed-in operator reloading the
dashboard never fetches it at all. `test_wallet_bundle.py` fails if a template
starts loading it eagerly.

## The committed artifact

`pathiel/static/wallet.js` and `wallet.css` are build outputs committed to the
repo, which this project otherwise refuses to do. The reason: `api/index.py` is
a Python function on Vercel and the deploy has no Node step, so adding one to
produce a single static file would put a second toolchain in the deploy path for
nothing.

The cost of that is drift, and `build.mjs --check` is what makes it safe. It
hashes every file in `src/` plus `package.json` and `build.mjs`, compares against
`bundle.manifest.json`, and fails. `test_wallet_bundle.py` runs it, so a stale
bundle breaks the commit gate rather than breaking sign-in in production.

## Working on it

```
npm install
npm run build        # writes ../../pathiel/static/wallet.{js,css} + the manifest
npm run check        # what the gate test runs
```

Commit `pathiel/static/wallet.js`, `pathiel/static/wallet.css` and
`bundle.manifest.json` together. `node_modules/` is 644 MB and gitignored.

## Tests

```
.venv/bin/python -m pytest services/wallet_ui/tests/ -q                 # gate, <1s
.venv/bin/python -m pytest -m browser services/wallet_ui/tests/ -q      # real Chromium, ~10s
```

Gate tests (`test_wallet_bundle.py`, `test_contract_sync.py`,
`test_fake_accounts_server.py`): offline, deterministic. The last two are new
with the accounts rewrite — one guards `lib/contract.ts` / `lib/validate.ts`
against drifting from `contracts/accounts/models.py`, the other unit-tests
the fake FastAPI app the browser suite runs against (its own signature
verification and expiry check, exercised directly, not just through a
browser).

The browser suite (`test_wallet_account_flow_browser.py`) is deselected by
default and is the only thing that can catch what actually broke here before:
the first build emitted `React.createElement` into files that never import
React, and every other test still passed because the failure only exists
once a browser evaluates the file. It runs against a real FastAPI app
implementing the contract routes (`fake_accounts_server.py`) and a real
EIP-1193 provider announced over EIP-6963 (`wallet_harness.py`) whose
signatures come from actual `eth_account` signing in Python — so it proves
the bundle hands the wallet the server's `typed_data` **unmodified**: a
tampered payload signs to a different EIP-712 hash, and the fake server's
signature recovery catches it (see `test_fake_accounts_server.py::
test_confirm_rejects_a_mutated_payload` for the same property, exercised
without a browser). It covers connect → authorize → done, a rejected
signature, an expired request's one silent auto-retry, a chain-switch
prompt, API-key validation (and that the key never reaches storage, the
DOM, or the console), the watch flow, the account switcher, the
`pathiel:account-changed` event shape, keyboard-only navigation of the
modal, a 375px viewport, and zero console errors.
