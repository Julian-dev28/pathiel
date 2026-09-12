# services/wallet_ui — the wallet picker

RainbowKit, bundled to a single vendored asset that the Jinja dashboard loads on
demand. Source here, output at `pathia/static/wallet.js`.

## What it replaced, and why

`pathia/static/pathia.js` used to call `window.ethereum` directly. With two
extensions installed that object is whichever one won a race at page load, there
was no way to choose between them, and the failure text told people who already
had a wallet to "install MetaMask or Rabby".

RainbowKit resolves wallets over EIP-6963, so each installed wallet announces
itself and the user picks the one they meant.

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

**The bundle is loaded on demand, not on page load.** It is ~380 KB brotli
against ~30 KB for the dashboard's entire vanilla JS. `pathia.js` injects it on
the first click that needs a wallet, and a signed-in operator reloading the
dashboard never fetches it at all. `test_wallet_bundle.py` fails if a template
starts loading it eagerly.

## The committed artifact

`pathia/static/wallet.js` and `wallet.css` are build outputs committed to the
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
npm run build        # writes ../../pathia/static/wallet.{js,css} + the manifest
npm run check        # what the gate test runs
```

Commit `pathia/static/wallet.js`, `pathia/static/wallet.css` and
`bundle.manifest.json` together. `node_modules/` is 644 MB and gitignored.

## Tests

```
.venv/bin/python -m pytest services/wallet_ui/tests/ -q                 # gate, <1s
.venv/bin/python -m pytest -m browser services/wallet_ui/tests/ -q      # real Chromium, ~16s
```

The browser suite is deselected by default and is the only thing that can catch
what actually broke here: the first build emitted `React.createElement` into
files that never import React, and every unit test still passed because the
failure only exists once a browser evaluates the file. It also asserts the
wallet signs the **server's** SIWE message rather than one the client composed —
a client-composed message that happened to verify would be a silent downgrade of
the auth model in `services/auth`.
