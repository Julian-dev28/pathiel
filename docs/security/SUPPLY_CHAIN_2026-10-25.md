# Supply chain and web-service security review, 2026-10-25

Scope: the autumn 2026 npm compromise wave (and the 2025 incidents current
guidance is built on) against this repo's one Node tree, `services/wallet_ui`,
plus ordinary production risks in the FastAPI server, the deploy config and
the runtime. pathiel trades real money on Hyperliquid, so "risk" below means
"what an attacker could make the desk do or reveal".

Verdict: no compromised package version is anywhere in the dependency graph,
and the committed wallet bundle reproduces byte for byte from the lockfile.
The worst findings were in our own code, not in npm: an anonymous POST could
rewrite the live desk's call (P0-1), and the Python CA bundle trusted a root
whose intermediates are compromised (P0-2). Both are fixed in the repo; P0-2
also needs one command on the operator's machine (see "Do today").

Status legend: **FIXED** (in this branch, with a test), **OPERATOR** (needs a
command or decision only the operator can make), **OPEN** (documented, not
changed, reason given).

---

## Part 1. Research brief

Method: web research restricted to GitHub Security Advisories, OSV, CISA,
npm/GitHub blogs, Node.js security releases, vendor research posts (Socket,
Snyk, Aikido, Wiz, Endor Labs, StepSecurity, Elastic, Microsoft) and maintainer
postmortems. Every advisory ID in the tables below that is marked "OSV
verified" was fetched from `api.osv.dev` on 2026-10-25 by
`scripts/supply_chain_check.py` or a one-off query; the rest carry the source
the research pass found. Anything sourced only from press or a secondary
blog is marked UNCONFIRMED.

### "Blacktober"

UNCONFIRMED as a term. No authoritative source uses "Blacktober". The label
in circulation is "Black October" (Oct 2026), seen in trade press
(securitytoday.de, 2026-10-22) and a Wikipedia article, neither of which is
an authoritative source under this review's rules. Trackers disagree on how
many incidents it covers (one counts 15 in 17 days). Treat it as a press
grouping of the October 2026 incidents below, not a defined event.

### Incidents

| Incident | Date | Packages and bad versions | Vector | Source | In our graph? |
|---|---|---|---|---|---|
| Qix maintainer phish ("chalk/debug") | 2025-09-08, about 2 h live | chalk 5.6.1, debug 4.4.2, ansi-styles 6.2.2, strip-ansi 7.1.1, color-convert 3.1.1, color-name 2.0.1, ansi-regex 6.2.1, wrap-ansi 9.0.1, supports-color 10.2.1, slice-ansi 7.1.1, chalk-template 1.1.1, simple-swizzle 0.2.3, is-arrayish 0.3.3, error-ex 1.3.3, has-ansi 6.0.1, backslash 0.2.1, color-string 2.1.1, supports-hyperlinks 4.1.1 | Phished npm account (lookalike npmjs.help); payload is a **browser wallet drainer** hooking `window.ethereum` and fetch/XHR | MAL-2025-46969 chalk (OSV verified, 2025-09-08); GHSA-4x49-vf9v-38px debug (OSV verified, published 2025-09-15); MAL-2025-46967 ansi-styles (OSV verified); https://github.com/chalk/chalk/issues/656 | Names yes, versions **no**: chalk 5.6.2 (the clean republish, OSV: no advisory), debug 4.3.4 and 4.4.3, ansi-styles 4.3.0, strip-ansi 6.0.1, color-convert 2.0.1, color-name 1.1.4, ansi-regex 5.0.1, wrap-ansi 6.2.0. None ships in `wallet.js` |
| Nx "s1ngularity" | 2025-08-26 | nx 20.9.0 to 20.12.0, 21.5.0 to 21.8.0, @nx/devkit, @nx/js, @nx/workspace, @nx/node, @nx/eslint, @nx/key, @nx/enterprise-cloud | GitHub Actions workflow injection stole the publish token; postinstall harvested secrets using local AI CLIs | GHSA-cxm3-wv7p-598c (OSV verified, published 2025-08-27); https://nx.dev/blog/s1ngularity-postmortem | No |
| eslint-config-prettier | 2025-07-18 | eslint-config-prettier 8.10.1, 9.1.1, 10.1.6, 10.1.7; eslint-plugin-prettier 4.2.2, 4.2.3; synckit 0.11.9; @pkgr/core 0.2.8; napi-postinstall 0.3.1 | Phished maintainer, install script dropped a Windows DLL | CVE-2025-54313, https://www.endorlabs.com/learn/cve-2025-54313-eslint-config-prettier-compromise----high-severity-but-windows-only | No |
| Shai-Hulud | 2025-09-15 onward; CISA alert 2025-09-23 | 500+ packages | Self-replicating worm: stolen npm tokens, `postinstall` running a 3 MB `bundle.js`, TruffleHog secret scan, pushes `.github/workflows/shai-hulud-workflow.yml` | https://www.cisa.gov/news-events/alerts/2025/09/23/widespread-supply-chain-compromise-impacting-npm-ecosystem | No |
| Shai-Hulud 2.0 | 2025-11-24 | about 800 packages, 25,000+ repos | `preinstall` installs Bun and runs `bun_environment.js`; registers a self-hosted runner `SHA1HULUD` | https://www.microsoft.com/en-us/security/blog/2025/12/09/shai-hulud-2-0-guidance-for-detecting-investigating-and-defending-against-the-supply-chain-attack/ (2025-12-09); https://www.wiz.io/blog/shai-hulud-2-0-ongoing-supply-chain-attack | No |
| keyv / cacheable ("ChainDrop", "Mini Shai-Hulud") | research pass found 2025-08-04 on vendor posts; one tracker dates it Aug 2026. Date UNCONFIRMED | keyv, cacheable, flat-cache, file-entry-cache, cache-manager, cacheable-request, @cacheable/* | Maintainer GitHub takeover, then republished through legitimate Actions, so the bad releases carried valid provenance | https://www.wiz.io/blog/keyv-and-cacheable-npm-supply-chain-attack ; https://www.elastic.co/security-labs/threat-command/shai-hulud-chaindrop-npm-supply-chain | No |
| MALFEX postinstall campaign | running since 2023; reports 2026-09-29 and 2026-10-05 | function-flag, cdn-img-fetch, function-color and about 9 more | `postinstall` downloads a Windows RAT/stealer | https://www.cloudsek.com/blog/malfex-malicious-npm-postinstall-supply-chain-campaign | No |
| tensorlake | 2026-10-08 | tensorlake 0.5.144 (npm only) | Shai-Hulud variant: steals npm/GitHub/cloud/SSH tokens and AI tool configs; wipes the machine if its token is revoked | MAL-2026-17650 (OSV verified, 2026-10-08). The research pass also cited GHSA-rqxj-g25x-4v9v: OSV has no such ID, UNCONFIRMED | No |
| indexed-btree typosquat | Sept 2026 | indexed-btree (impersonates sorted-btree) | No install hook; payload runs on `require`, so `--ignore-scripts` does not stop it | Press coverage only, UNCONFIRMED | No |
| @subql/common | 2026-10-05 | @subql/common 5.8.3 | Not established | Tracker roundup only, UNCONFIRMED | No |
| Zod repository commits | 2026-09-21 | none published to npm (reverted same day) | Malicious commits to `main` with a preinstall hook | Secondary sources only, UNCONFIRMED | zod 3.22.4 / 3.25.76 / 4.6.2 are installed (not shipped); no malicious zod release exists |
| Wallet-library typosquats | 2026 | viem-js, viem-toolkit, viem-core, wagmi-react, rainbownkit, kecak256 | Lookalike names | OSV MAL entries (names differ from ours) | No: we install `viem`, `wagmi`, `@rainbow-me/rainbowkit`, `keccak`, all clean on OSV |

`scripts/supply_chain_check.py --online` re-runs the OSV half of this every
night (`.github/workflows/nightly.yml`).

### Ordinary advisories on our exact pins (OSV, 2026-10-25)

| Package@version | Advisory | Severity | Fixed in | Ships to users? | Status |
|---|---|---|---|---|---|
| certifi 2026.7.22 (Python) | GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320, emSign root with compromised intermediates, published 2026-10-20 | Critical | 2026.10.19 | Yes: every httpx/requests TLS call, including Hyperliquid | **FIXED** in lock (2026.10.23); **OPERATOR** for the live `.venv` |
| urllib3 2.7.0 (Python) | GHSA-8988-9cw3-xx77, GHSA-vxq7-64xx-v4gw, GHSA-gh4c-6fx4-qh6g, published 2026-09-30 | High, High, Moderate | 2.8.0 (2026-09-15) | Yes (requests) | **FIXED** in lock; **OPERATOR** for `.venv` |
| esbuild 0.24.2 (npm, dev) | GHSA-67mh-4wv8-2f99, dev server answers any origin, published 2025-02-10 | Moderate | 0.25.0 | No: build tool; `esbuild serve` is never used | OPEN (P3-6) |
| ws 8.18.0, axios 1.16.0, follow-redirects 1.16.0, zod 4.6.2, @walletconnect/*, @reown/*, @metamask/sdk (npm, transitive) | 32 `npm audit` findings, 15 high | High/Moderate | mostly via wagmi 3 | **No.** None of these packages has a byte in `wallet.js` (the 33 that do are listed in `bundle.manifest.json`), and with install scripts off they never execute | OPEN (P3-5) |

### Node.js

Node 22 security release v22.23.4 (2026-10-21, verified on
https://nodejs.org/en/blog/vulnerability/october-2026-security-releases):
CVE-2026-105481, -105482, -106069, -106072, -106073, -106074 and the bundled
Brotli CVE-2026-111497 apply to the 22.x line. Node here only runs esbuild at
build time and never serves traffic. CI uses `node-version: "22"`, which
resolves to the newest 22.x. The operator's machine has v22.22.0 (P2-8).

### Published guidance and what this repo does with it

| Guidance | Source | Here |
|---|---|---|
| Install from the lockfile, with integrity hashes | npm docs; every vendor post above | `npm ci`; all 522 package entries in the lock carry `sha512` integrity from registry.npmjs.org (gate-checked) |
| Disable lifecycle scripts (`--ignore-scripts`) | CISA 2025-09-23; Elastic; Wiz | **New**: `.npmrc` `ignore-scripts=true`, CI `npm ci --ignore-scripts`, gate-checked |
| Pin exact versions, soak new releases before adopting | Elastic ("soak period"); pnpm `minimumReleaseAge` (name not re-verified this session, UNCONFIRMED) | **New**: exact versions in `package.json`, `save-exact=true`; README asks for 7 days. npm has no enforced equivalent we could confirm |
| Verify registry signatures and provenance | npm `audit signatures` | **New** CI step. 498/498 signatures valid, 130 with attestations (local run 2026-10-25) |
| Rotate npm/GitHub tokens after exposure | CISA; GitHub changelog 2025-09-29 (https://github.blog/changelog/2025-09-29-strengthening-npm-security-important-changes-to-authentication-and-token-management/) | No npm token exists anywhere (no `.npmrc` credentials, no publish step, no `NPM_TOKEN` secret in workflows). Nothing to rotate |
| Trusted publishing / 2FA for publishers | Same GitHub post; later GAT changes for Aug 2026 and Jan 2027 seen only in secondary sources (UNCONFIRMED) | Not applicable: `@pathiel/wallet-ui` is `private` and never published |
| Pin GitHub Actions to commit SHAs, least-privilege `GITHUB_TOKEN` | tj-actions/changed-files, CVE-2025-30066 (March 2025) | **New**: all `uses:` pinned to SHAs, `permissions: contents: read` |

### Composer / Packagist (context only)

This repo has no PHP and no `composer.json`. For context: Packagist and
Composer had their own 2026 incidents, reported by the research pass from
the Packagist blog (https://blog.packagist.com/restricting-composer-plugins-across-your-organization/)
and vendor posts: two Composer command-injection CVEs patched April 2026
(CVE-2026-40176, CVE-2026-40261), a malicious `intercom/intercom-php` 5.0.2
carrying a Composer-plugin payload tied to the Mini Shai-Hulud family (April
30), and a May 22 campaign that hid an npm `postinstall` hook in the
`package.json` of eight Packagist packages including `devdojo/wave`. The
guidance mirrors npm's: lock and hash, restrict which Composer plugins may
run (`allow-plugins`), soak new versions. None of it touches pathiel.

---

## Part 2. Audit with evidence

Line numbers refer to commit `9e1b475` (the review's starting point) unless
a file is named as "now".

### 2.1 Dependency and supply-chain hygiene

| # | Finding | Evidence |
|---|---|---|
| A1 | No compromised version anywhere in the npm graph | 444 unique `name@version` from `services/wallet_ui/package-lock.json` checked against the incident table and OSV |
| A2 | The committed bundle is what the lockfile produces | `npm ci --ignore-scripts && node build.mjs` reproduced `wallet.js` sha256 `dd7009a9e546...` and `wallet.css` `c2427222f368...` byte for byte (2026-10-25) |
| A3 | Only 33 packages ship to the browser, none with an advisory | esbuild metafile; list now in `services/wallet_ui/bundle.manifest.json` `bundledPackages`; no drainer indicator (`stealthProxyControl`, `checkethereumw`, `runmask`, `newdlocal`, the attacker address) in `wallet.js` |
| A4 | CI ran every dependency install script | `.github/workflows/ci.yml:56` `npm ci`. Four packages declare one: esbuild 0.24.2, bufferutil 4.1.0, keccak 3.0.4, utf-8-validate 5.0.10 (`package-lock.json` `hasInstallScript`). All four are clean versions, so nothing bad ran; nothing needs them (the build works with scripts off, A2) |
| A5 | `build.mjs --check` ignored the lockfile and the output bytes | `services/wallet_ui/build.mjs:40-50` hashed `src/`, `package.json`, `build.mjs` only. A dependency bump, or a hand edit to `wallet.js`, passed the gate |
| A6 | Caret ranges in `package.json` | `services/wallet_ui/package.json:12-20` (`^2.2.0`, `^2.37.0`, ...). `npm ci` honours the lock, but any `npm install` could take a newer release during an attack window |
| A7 | Actions pinned by mutable tag, no `permissions:` | `ci.yml:19,22,48`, `nightly.yml:20,22` (`@v4`, `@v5`); neither workflow declared `permissions` |
| A8 | Python lock has no hashes | `requirements.lock` (54 pins, 0 `--hash` lines) |
| A9 | Two vulnerable Python pins | `requirements.lock:26` certifi 2026.7.22, `:162` urllib3 2.7.0 (see Part 1) |
| A10 | Docker image resolved Python deps fresh | `Dockerfile:20` `RUN pip install -e .` resolves pyproject ranges at build time, unhashed, not the set CI tests. The lock's own header claimed the image used it |
| A11 | npm audit: 32 findings, all in non-shipping transitive trees | `npm audit --json` 2026-10-25: 15 high, 17 moderate, every one via @wagmi/connectors' WalletConnect/Reown/MetaMask/Coinbase subtrees or esbuild |
| A12 | No npm or GitHub token exposure | no `.npmrc` with credentials, no `NPM_TOKEN`/`secrets.` in workflows, no publish step; tracked-file secret scan (patterns for hex keys, `ghp_`, `npm_`, `sk-`, PEM) found only test placeholders and public tx hashes in fixtures |

### 2.2 Secrets and configuration

| # | Finding | Evidence |
|---|---|---|
| S1 | No committed secrets; `.env.local` is mode 600 and ignored | secret scan above; `.gitignore:35-43`; history has only `.env.local.example` and an env-reading `route.ts` removed 2026-05 |
| S2 | CORS `*` for every method and header | `pathiel/server.py:351-357` |
| S3 | API docs open everywhere | FastAPI defaults; `server.py:237` lists `/docs`, `/openapi.json` as open prefixes |
| S4 | Container runs as root | `Dockerfile` has no `USER`; `k8s/statefulset.yaml:34-39` says so in a comment |
| S5 | Docker image could not start | `Dockerfile` never copied `contracts/`; `pathiel/agents/config_store.py:20` imports `contracts.risk.models`, so `python -m pathiel.server` died with `ModuleNotFoundError` (reproduced with `docker run`, 2026-10-25). `tests/test_deploy_config.py` only scanned `pathiel`/`services` imports |
| S6 | Fresh deployment hands the operator role to the first caller | `services/auth/integration.py` `require_operator_or_local(allow_bootstrap=True)`; neither `fly.toml` nor `k8s/configmap.yaml` sets `PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR` |
| S7 | `/metrics` open with no option to close it | `server.py:857`; exposes drawdown, LIVE mode, can-trade |
| S8 | Operator token accepted in the query string | `pathiel/dashboard.py:866` `request.query_params.get("token")`. Mitigated: `access_log=False` (`server.py:881`), `Referrer-Policy: same-origin` |
| S9 | Local logs world-readable | `logs/*.log` mode 644; `scripts/restart.sh` sets no umask |

### 2.3 Application: Python server and wallet UI

| # | Finding | Evidence |
|---|---|---|
| W1 | **Anonymous write to the live desk's call** | `services/strategies/api.py:148` `POST /api/desks/{id}/refresh` and `:212` `POST /api/desks/{id}/theses` had no gate. `services/strategies/facade.py:237` serves `journal.latest_thesis(spec.id)` with no author filter, so a user-authored thesis becomes the call `/api/desks/{id}` and the MCP `desk_brief` hand the trader. Reproduced 2026-10-25: a TestClient from 203.0.113.9 with `Origin: https://evil.example` got 200, the preflight answered `Access-Control-Allow-Origin: *`, and `latest_thesis("oil").author == "user"`. Reach: from the internet on any deployment; on the operator's Mac, from any web page in a browser that does not block public-to-loopback requests (S2 let the JSON preflight through) |
| W2 | Loop log readable by anyone | `services/strategies/api.py:273` `GET /api/desks/{id}/log`, Claude's own trade journal |
| W3 | DNS rebinding reads local-mode data | `services/auth/integration.py:158-200` `is_local_mode` checked peer and Origin but not Host. A rebinding page's GETs are same-origin (no Origin), so `/api/accounts`, `/api/dashboard/positions`, `/api/risk/limits` were readable. Mutations still needed an Origin and were refused |
| W4 | Login rate limits keyed on caller-written header | `services/auth/api.py:67-69`, `services/accounts/api.py:66-68` used the leftmost `X-Forwarded-For`, so a new fake value per request reset the bucket |
| W5 | No request-body ceiling | uvicorn sets none; open POSTs (`/auth/verify`, `/api/risk/preview`) read the whole body before validation |
| W6 | CSP allows inline script | `server.py:289` `script-src 'self' 'unsafe-inline'` (pre-existing, documented in code) |
| W7 | Mutating-route authorization otherwise sound | Route inventory (now gate-tested by `tests/test_http_hardening.py::test_every_mutating_route_is_gated`): every POST/PUT/PATCH/DELETE has a dependency or inline gate except `/auth/verify`, `/auth/logout`, `/api/risk/preview` (open by design) |
| W8 | Session handling sound | httpOnly, SameSite=Lax, Secure except plain-http localhost (`services/auth/deps.py:180-197`); tokens and nonces stored as SHA-256; SIWE domain from config, not Host |
| W9 | Security headers present | `server.py:287-316`: CSP with `frame-ancestors 'none'`, `connect-src 'self'`, nosniff, DENY, Referrer-Policy, Permissions-Policy, HSTS on https; applied to errors (existing test) |
| W10 | Wallet UI | No WalletConnect relay, no remote script; bundle loaded on demand from `/static`; `connect-src 'self'` stops an injected script exfiltrating. Its supply chain is A1 to A6 |

### 2.4 Runtime

| # | Finding | Evidence |
|---|---|---|
| R1 | Python 3.13.7 on the operator's `.venv`, 3.11 in the image, 3.11 to 3.13 in CI | `ci.yml` matrix; `Dockerfile:1` |
| R2 | Node v22.22.0 locally, behind 22.23.4 | `node --version`; Part 1 |
| R3 | Base image by tag | `Dockerfile:1` `python:3.11-slim` |
| R4 | Bind 127.0.0.1 locally, 0.0.0.0 in containers behind Fly/k8s | `server.py:879`, `Dockerfile` ENV |
| R5 | k8s: resource limits set; no securityContext beyond `fsGroup: 1000`; service-account token mounted | `k8s/statefulset.yaml:34-39,94-99` |
| R6 | Logging: access log off, app logs to stdout in containers and `logs/` locally; rotation by `log_rotate.py` | `server.py:881`; `pathiel/log_setup.py` |
| R7 | Rollback | Image tags plus state on the volume (`DEPLOY.md`); local: `git checkout <sha> && scripts/restart.sh`. Nothing here changes that |

---

## Part 3. Remediation plan

Each row: risk, evidence (Part 2 ID), the fix as made, how it was verified,
and what it can break.

### P0, urgent

| ID | Finding | Fix | Verification | Compatibility | Status |
|---|---|---|---|---|---|
| P0-1 | W1, W2: anonymous thesis write / model-call trigger / log read | `services/strategies/api.py` `_require_operator_or_local` on POST `/refresh`, POST `/theses`, GET `/log` (same gate as accounts and risk) | `tests/test_http_hardening.py`: remote anon 401, cross-site Origin from loopback 401/403, local operator passes; inventory test fails on any new ungated mutator. All 11 new HTTP tests fail on `9e1b475` | Desk page on the operator's box is unchanged (same-origin, local mode). A deployment needs an operator session for those three routes. MCP tools call the facade directly, unaffected | **FIXED** |
| P0-2 | A9 certifi: compromised emSign intermediates | `requirements.lock` certifi 2026.10.23. Checked before adopting a 2-day-old release: its `cacert.pem` only removes three emSign roots, adds none; code identical except `__version__` | `--require-hashes` install into a scratch venv, full gate there: 2776 passed | A host chaining to an emSign root would now fail TLS (Hyperliquid, OpenRouter, the intel feeds do not; gate and offline suite green) | **FIXED** in lock, **OPERATOR** for `.venv` |

### P1, high

| ID | Finding | Fix | Verification | Compatibility | Status |
|---|---|---|---|---|---|
| P1-1 | S2 CORS `*` | `server.py` `cors_origins()`: no CORS middleware unless `PATHIEL_CORS_ORIGINS` names exact origins; `*` refused | `test_a_foreign_origin_gets_no_cors_grant`, `test_cors_origins_refuses_a_wildcard` | Nothing in the repo reads the API cross-origin (grep); a third-party page that did must be listed | **FIXED** |
| P1-2 | W3 DNS rebinding | `integration._host_is_local`: local mode also needs Host to be a loopback name (or `PATHIEL_LOCAL_HOSTNAMES`) | 4 new tests in `services/auth/tests/test_integration.py` incl. a real ASGI request with `Host: evil.example:8000` | Opening the dashboard by LAN IP or a custom hostname now needs that name in `PATHIEL_LOCAL_HOSTNAMES` (the server binds 127.0.0.1 by default, so this was not possible anyway) | **FIXED** |
| P1-3 | A4 install scripts | `services/wallet_ui/.npmrc` `ignore-scripts=true`; CI `npm ci --ignore-scripts`; `KNOWN_INSTALL_SCRIPTS` allow-list | `scripts/supply_chain_check.py` + `tests/test_supply_chain.py` (planted bad lock entry and bad workflow fail); bundle builds and reproduces with scripts off | None: no package needs its script | **FIXED** |
| P1-4 | S4, S5, A10, R3 image | Non-root uid 10001; entrypoint drops root with `setpriv --no-new-privs --bounding-set=-all` after handing `/data` over; deps from hashed lock then `--no-deps -e .`; `COPY contracts/`; `HOME=/data/home`; base pinned by digest | `docker build` + `docker run --read-only`: uid 10001, CapEff 0, every page 200 (health/system 503 as expected without a loop), rotator runs; `tests/test_deploy_config.py` 6 new tests; the import scan now covers `contracts` and fails on `9e1b475` | Existing Fly volumes are root-owned: the entrypoint chowns them on first boot, no manual step. `~/.pathiel` caches move to `/data/home` (rebuilt on demand) | **FIXED** |
| P1-5 | A5 bundle provenance | `build.mjs`: hash includes `package-lock.json`; manifest records `artifactSha256` and `bundledPackages`; `--check` verifies the committed bytes; new `--verify` rebuilds to a temp dir and requires identical bytes and package list; CI runs it | `test_bundle_reproduces_from_the_lockfile`; supply-chain tests plant a hand edit and an unpinned bundled package | Contributors must commit the regenerated manifest (they already had to) | **FIXED** |
| P1-6 | A9 urllib3 | `requirements.lock` urllib3 2.8.0 (released 2026-09-15) | scratch-venv gate as P0-2 | Minor version | **FIXED** in lock, **OPERATOR** for `.venv` |
| P1-7 | A7 Actions | `permissions: contents: read` in both workflows; actions pinned to `actions/checkout@11d5960a...` (v4.4.0), `setup-python@a26af69b...` (v5.6.0), `setup-node@49933ea5...` (v4.4.0), resolved with `gh api` on 2026-10-25; `npm audit signatures` step | `check_workflows` in the gate; planted bad workflow fails | Dependabot can still bump SHA pins | **FIXED** |

### P2, important

| ID | Finding | Fix | Verification | Compatibility | Status |
|---|---|---|---|---|---|
| P2-1 | W4 rate-limit key | `integration.client_key`: TCP peer; rightmost `X-Forwarded-For` only behind a proxy (deployment marker or `PATHIEL_TRUST_FORWARDED_FOR=1`) | `test_a_made_up_forwarded_for_does_not_reset_the_bucket`, two `client_key` tests | Behind more than one proxy hop the rightmost entry is the inner proxy; set the edge to overwrite XFF | **FIXED** |
| P2-2 | W5 body size | Pure-ASGI `_BodySizeLimit` in `server.py`, 1 MiB default (`PATHIEL_MAX_BODY_BYTES`), 413 for Content-Length and for chunked bodies | 3 tests incl. chunked and "security headers still on the 413" | Largest legitimate body is a few KB | **FIXED** |
| P2-3 | S3 API docs | Off on any deployment unless `PATHIEL_API_DOCS=1` | `test_api_docs_are_off_on_any_deployment`; container with `FLY_APP_NAME` gives 404 on `/docs` | Local `/docs` unchanged | **FIXED** |
| P2-4 | A8 Python hashes | `uv pip compile --generate-hashes`; CI and Dockerfile install hash-checked | `check_python_lock_hashes`; `--require-hashes` install succeeded | Regenerating needs the same uv flags (header says so) | **FIXED** |
| P2-5 | R5 k8s pod | `runAsNonRoot`, uid/gid/fsGroup 10001, `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `readOnlyRootFilesystem` with `logs`/`tmp` emptyDirs, `automountServiceAccountToken: false` | `test_k8s_pods_run_non_root_with_no_privileges`; read-only `docker run` above | `fsGroup` changed 1000 to 10001: the kubelet re-chowns the PVC on next mount | **FIXED** |
| P2-6 | S6 first-caller operator bootstrap | Proposed: set `PATHIEL_AUTH_NO_BOOTSTRAP_OPERATOR=1` in `fly.toml`/`k8s/configmap.yaml` and grant with `scripts/grant_operator.py` | n/a | Changes the first-deploy flow; on a deployment that already has an operator it changes nothing. Operator's call | **OPEN** |
| P2-7 | W6 inline script in CSP | Move each page's inline `<script>` to a file, drop `'unsafe-inline'` | n/a | Touches five templates and their browser tests; a UI change, out of this review's scope | **OPEN** |
| P2-8 | R2 local Node | `brew upgrade node@22` (or nvm) to 22.23.4 | `node --version` | Build-time only | **OPERATOR** |

### P3, hardening

| ID | Finding | Fix | Status |
|---|---|---|---|
| P3-1 | S7 `/metrics` | Optional `PATHIEL_METRICS_TOKEN` bearer (constant-time compare); `servicemonitor.yaml` shows `bearerTokenSecret`; listed in `docs/SECRETS.md` and `scripts/preflight_secrets.py`. Set it on Fly | **FIXED** (opt-in), **OPERATOR** to set |
| P3-2 | A6 ranges | Exact versions in `package.json`, `save-exact=true`, gate-checked against the lock | **FIXED** |
| P3-3 | S9 log modes | `umask 077` in `scripts/restart.sh` (gate-checked). Existing files keep 644 | **FIXED**, **OPERATOR** for `chmod -R go-rwx logs` |
| P3-4 | S8 `?token=` | Drop query-string support, header only. No tracked code sends it, but the documented operator workflow does (`DEPLOY.md:158`, `docs/ARCHITECTURE.md:731`), so removing it breaks bookmarks the operator may have. Operator's call | **OPEN** |
| P3-5 | A11 transitive advisories | They clear with wagmi 3.x, a major with a new connector API, published 2026-10-21 (4 days old). The case for waiting: no flagged package ships or executes, and wagmi 3 would change the connect flow the browser suite guards. `npm overrides` for ws/axios/follow-redirects/zod was tried and reverted: it produced ERESOLVE peer overrides inside the Reown tree for no change to the bundle | **OPEN**, revisit with the wagmi 3 upgrade |
| P3-6 | esbuild 0.24.2 dev-server advisory | Upgrade to 0.25+ with a rebuild and browser run; not exploitable here (`serve` unused) | **OPEN** |
| P3-7 | Image carries pytest and ruff (dev extras in the one lock) | Split a runtime lock if image size or surface matters | **OPEN** |
| P3-8 | `requirements.txt` (Vercel demo) is ranges, no hashes | Generate it from the lock or retire the demo | **OPEN** |

---

## Do today

1. Update the live interpreter to the fixed lock (certifi, urllib3). Operator runs:
   `/Users/julian_dev/Documents/code/pathiel/.venv/bin/pip install --require-hashes -r requirements.lock`
   then `scripts/restart.sh restart` so the server loads the new CA bundle.
2. Merge this branch; confirm CI's new steps pass on GitHub (`npm ci --ignore-scripts`, `npm audit signatures`, `build.mjs --verify`).
3. In `services/wallet_ui`, reinstall once with `npm ci --ignore-scripts` (the new `.npmrc` makes that the default from now on).
4. If a Fly app is live: `flyctl secrets set PATHIEL_METRICS_TOKEN=$(openssl rand -hex 24)`, then redeploy (the image now runs as uid 10001 and fixes its own volume ownership).

## Do this week

1. `chmod -R go-rwx logs .state` in the repo root (old files predate the umask).
2. Upgrade local Node to 22.23.4.
3. Decide P2-6 (operator bootstrap on deployments).
4. Plan the wagmi 3 upgrade as its own change with the browser suite (P3-5), and bump esbuild in the same pass (P3-6).
5. Look at the nightly `Advisories on what ships` result each morning for a week to confirm it is quiet.
6. Move inline page scripts to files and drop `'unsafe-inline'` (P2-7).

## How to re-run this review

```
python scripts/supply_chain_check.py            # offline, also in the gate
python scripts/supply_chain_check.py --online   # + OSV on what ships and every Python pin
cd services/wallet_ui && npm ci --ignore-scripts && npm audit signatures && node build.mjs --verify
```
