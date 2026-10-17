# services/auth

Sessions, local-mode, the API keys customers use against
`services/pathiel_data_api` — and, as of 2026-10-17, no separate "Sign in"
step. `contracts/accounts/models.py` says it plainly: connecting a
Hyperliquid account IS the identity. The user proves ownership of an
address while authorising a trading key (`wallet_sign`) or importing a
verified API key (`api_key`), and that proof is exactly as good as a SIWE
signature — just reached through `services/accounts`' door instead of this
service's own `/auth/verify`. See "Local mode, and the retired sign-in step"
below for the model, and `integration.py` for the three functions
`services/accounts` calls to open a session from that door.

The SIWE routes (`/auth/nonce`, `/auth/verify`, …) are NOT removed. They
stay for API clients that still want wallet-signature login and for
back-compat, and the rest of this document (nonces, SIWE, storage, roles,
API keys) describes them unchanged.

## Why Sign-In With Ethereum, and not a vendor

`eth-account` is already a hard dependency — it signs the Hyperliquid orders —
and recovering a signer from a `personal_sign` signature is the same primitive.
So EIP-4361 cost **zero new dependencies**. Privy, Dynamic and Magic each add a
vendor, a bill, an outage surface we do not own, and a third party who learns
every user's wallet. None of them buy anything this service does not already do.

It also gets the identity model right for a non-custodial product: **a user is a
wallet**. There is no password to leak and no reset flow to phish, because there
is no password anywhere in the system.

## The routes

| route | who | what |
|---|---|---|
| `GET /auth/nonce?address=0x…` | anyone | mints a single-use nonce and returns the **exact** message to sign |
| `POST /auth/verify` | anyone | checks the signature, opens a session, sets the cookie |
| `GET /auth/me` | signed in | who am I |
| `PATCH /auth/profile` | signed in | display name, notification email |
| `POST /auth/logout` / `logout-all` | signed in | drop this session / every session |
| `GET POST DELETE /auth/keys` | signed in | list, mint, revoke your own API keys |

The **server composes the signed message**, in `/auth/nonce`. If the client
composed it, every security field inside — domain, nonce, expiry — would be
attacker-chosen, and verifying them afterwards would be checking the attacker's
homework against itself.

## Local mode, and the retired sign-in step

Julien, running this on his own box, never signs anything to use it. That is
the normal case, not a debug shortcut: `services.auth.integration.is_local_mode`
grants full access with **no session, no sign-in, ever** to a request that
itself arrived from this machine. `contracts/accounts/models.py`'s
`SessionInfo.local_mode` is this, surfaced to the UI so it never shows a sign-in
prompt it does not need.

Three functions, `services/auth/integration.py`, called by `services/accounts`:

| function | contract |
|---|---|
| `is_local_mode(request)` | true only for this machine — see below for exactly what that has to mean |
| `issue_session_for_address(response, address, request)` | opens a session for `address` and sets the cookie, **after the caller has already verified ownership** |
| `session_address(request)` | the address behind the caller's session, or `None` |

**`issue_session_for_address` verifies nothing.** It is the exact code
`/auth/verify` runs after `siwe.verify()` and the nonce burn succeed —
factored out so both doors open an identical session — and it trusts its
`address` argument completely. `services/accounts` must have already proven
ownership as strongly as SIWE does (a signature over a single-use, expiring,
challenge: `approveAgent`'s EIP-712 signature against the burned
`request_id` from `AgentPrepareOut`, or a verified API-key import checked
against Hyperliquid itself) **before** calling it. Calling it on an
unverified address is a complete account takeover for whichever address was
named. Two things follow from this that are the accounts service's job, not
this one's:

- **Replay of a confirm request.** `AgentConfirmIn.request_id` must be
  single-use, burned the moment it is checked, the same way
  `store.consume_nonce` burns a SIWE nonce. If it is not, a captured confirm
  request is a permanent way to open a session for that address.
- **Rate limits.** `issue_session_for_address` mints a session — a DB write —
  on every call, unconditionally, and has no rate limiter of its own:
  there is no secret to brute-force at this layer, only at the caller's
  verification step. `/auth/verify` rate-limits itself
  (`api.py:_rate_limit`, 20 attempts / 5 min / IP) precisely because *it*
  is the layer with a secret (the signature) to guess against; the accounts
  service's `agent/confirm` and `import` routes are that same layer for the
  new door and need the same guard.

**Session fixation does not apply here, by construction.** Neither
`issue_session_for_address` nor `/auth/verify` accepts a token from the
caller — every session is a fresh `secrets.token_urlsafe(32)` (or a fresh
stateless token) minted server-side. There is no code path that takes a
client-supplied identifier and "upgrades" it in place, which is the shape
every session-fixation attack needs.

### Why `is_local_mode` is not just "check the Host header"

This service already had a loopback check — `deps._is_local`, used only to
decide whether to drop the cookie's `Secure` flag on plain HTTP to
`localhost`. It reads `request.url.hostname`, which is built from the
**`Host` header**, which is **attacker-controlled text**: any remote caller
can send `Host: localhost` and `request.url.hostname` says `"localhost"`
right back, regardless of where the connection actually came from (see
`test_the_host_header_is_attacker_text_but_the_peer_address_is_not` in
`tests/test_integration.py`, which proves this against a real request, not a
claim). That was always fine for a cookie flag — the worst case is an
attacker relaxing `Secure` on *their own* cookie, which protects no one else.
It would be a **complete, unauthenticated bypass of every trading route** if
reused for "skip auth entirely," on a deployment reachable from the network.

So `is_local_mode` is built on two independent checks, both in this module,
neither spoofable the way a header is:

1. **`deps.is_loopback_peer`** — reads `request.client.host`, the TCP peer
   address the ASGI server observed, never a header. A remote caller cannot
   make this say `127.0.0.1` by sending any text; it would have to actually
   hold a connection from `127.0.0.1`. (It fails closed — "not local" — if a
   reverse proxy is ever configured to rewrite this from a forwarded header;
   nothing in this repo's `uvicorn.run` call does that today. If a future
   deployment adds `--proxy-headers`, re-audit this function against that
   proxy's trust boundary.)
2. **The `Origin` check inside `is_local_mode` itself** — necessary because a
   loopback peer is not sufficient. A browser tab open on **any** website, on
   Julien's own machine, can make Julien's own browser issue a request to
   `http://127.0.0.1:<port>/...`; the TCP connection really is loopback,
   because the browser is local, even though the page that triggered it is
   `https://evil.example`. This is a known, named attack class against local
   tools that trust "it came from loopback" alone (Ollama's and Docker
   Desktop's local APIs have both shipped exactly this bug), and
   `pathiel/server.py` currently runs CORS wide open
   (`allow_origins=["*"]`), which does not leak the session cookie
   (`allow_credentials=False`) but puts nothing at all in front of a
   cross-site mutating request that relies on source IP alone — which is
   what local mode would be without this check. `Origin` is not a header
   page script can set; the browser sets it to the calling page's own
   origin and will not lie. So: present and not a loopback host (exact
   match on `localhost` / `127.0.0.1` / `::1`, any port — not a prefix or
   suffix match, which `localhost.evil.example` would sail through) means
   refused. Absent — curl, a script, the scheduler, anything that is not a
   browser enforcing CORS — means unaffected, which is exactly right: that
   is Julien's own tooling, the case this mode exists for.
   `test_is_local_mode_false_for_a_loopback_peer_with_a_cross_site_origin` in
   `tests/test_integration.py` is the regression test for this; it is the
   single most important test in this diff.

`deps._is_local` (the Host-header heuristic) is untouched and still used only
for the `Secure` cookie flag. The two checks are kept deliberately separate —
merging them would either weaken `is_local_mode` back into a bypass, or make
the cookie flag stricter than it needs to be for no benefit.

### Public mode: who may control which account

Outside local mode, a session proves an address, not a blanket license —
this product connects up to several Hyperliquid accounts per deployment.
`integration.session_may_control_account(request, main_address)` is the one
further question `services/accounts` asks before any mutating call on a
*specific* account (after loading it; this module has no notion of what an
`Account` is):

- local mode: yes, unconditionally.
- otherwise: yes iff the session's own address **is** `main_address`
  (case-insensitive), **or** the session belongs to the deployment's
  **operator**.
- no session at all: no.

"Keep the operator gating intact in spirit" means reusing the one
configured-operator concept this service already has — the `role` column
`AuthStore` bootstraps to the first wallet ever to open a session (through
either door now) and that `scripts/grant_operator.py` can hand to someone
else — rather than inventing a second operator list that could drift from
the first. It inherits that config's existing restriction for free, too: a
**stateless** session (`PATHIEL_AUTH_STATELESS_SESSION=1`) is hard-coded to
`role="user"` in `deps._user_from_stateless` regardless of what the database
row says, because minting an operator from a token that only needs to be
guessed or leaked is worse than one more sign-in prompt. This module adds no
second way to become operator, so a stateless caller can never use the
operator override to act on an account that is not its own —
`test_a_stateless_session_from_issue_session_for_address_can_never_be_operator`
is the regression test.

For the routes with no account to check yet — `contracts/accounts/models.py`'s
"initial connect routes" (`agent/prepare`, `agent/confirm`, `import`, `watch`:
there is nothing to own until these finish) — use
`integration.require_session_or_local(request)` instead: it returns the
caller's address, or `None` for local mode, and 401s a non-local caller with
no session.

### CSRF

Unchanged from the SIWE-only world, and it still covers the new accounts
routes without any new code: the session cookie is `SameSite=Lax`, which a
cross-site navigation cannot attach to anything but a top-level `GET`, and
**every mutating route in this product, old and new, is a `POST`, `PATCH` or
`DELETE`** (`contracts/accounts/models.py`'s route table has no mutating
`GET`). That pairing — `SameSite=Lax` plus "a mutation is never a `GET`" — is
the whole CSRF defense here, same as the existing docstring in
`deps.cookie_kwargs` already said. The invariant it depends on is explicit
now so whoever adds the next accounts route does not accidentally break it
by wiring a side effect behind a `GET`.

Local mode does **not** rely on the cookie at all, so `SameSite` protects
nothing there — which is exactly why local mode needs the separate `Origin`
check above instead.

## What a signature proves, and what it does not

It proves the holder of a private key signed *this* message. That is all. Every
other guarantee is built on top, and each check exists because leaving it out is
a named attack:

| check | without it |
|---|---|
| domain | a signature harvested by `evil.example` opens a session here |
| nonce, single-use | one captured signature is a permanent password |
| expiry + max age | a signature with no expiry is a bearer token forever |
| recovered address | the address line is attacker-written text; only the recovered signer is evidence |

Parsing is strict and allowlist-shaped: an unknown field is a **rejected**
message, never an ignored line. A lenient parser on a security boundary is how a
message means one thing to us and another to the wallet that showed it.

## Storage

Session tokens and login nonces are stored as **SHA-256 only**, never the value
the client holds. A leaked database — a backup on a laptop, a snapshot in object
storage, a stray `SELECT *` in a log — must not hand the reader a set of live
sessions.

SHA-256 and not bcrypt/argon2 deliberately: those exist to make *low-entropy
human passwords* expensive to brute-force. A 256-bit random token has no
guessable structure, so a slow KDF adds latency to every authenticated request
and buys nothing.

Nonces are burned with `DELETE … RETURNING`, so claim-and-check is one
statement and two requests racing the same nonce cannot both win.

## Roles

Two: `user` and `operator`.

The **first wallet to ever open a session on a fresh deployment becomes
operator** — through SIWE's `/auth/verify` or, now, through
`services/accounts`' connect flow calling `issue_session_for_address`, both
paths through the same `AuthStore.upsert_user` — which closes the
open-kill-switch window without a bootstrap password to leak. The failure
mode is that anything signing in first locks the real operator out —
`scripts/grant_operator.py` is the way back, and it can grant before a first
sign-in so setting up a box does not require logging in as a plain user.

Operator gates the **house** account (the deployment's own equity, positions,
P&L, funnel). Any signed-in wallet sees **its own** account at
`/api/dashboard/account`, read from Hyperliquid with no stored key.

## API keys

`api_keys.py` talks to the `api_keys` table in **plain SQL**, and does not
import `services/pathiel_data_api`. That service is a separate deploy unit with
its own Dockerfile and its own requirements, none of which are installed in the
trading image, so importing its models would fail at runtime even with the
source copied in. `test_dockerfile_does_not_bundle_pathiel_data_api` enforces
that boundary and caught exactly this mistake on the first attempt.

The contract between the two services is therefore the thing they genuinely
share: the table, its columns, and `sha256(raw)`. It is asserted, not assumed —
if the two ever drift, a customer mints a key that opens nothing and neither
service logs a thing.

The raw key exists once, in the mint response. Only its hash is stored, so a
leaked database yields no usable credential and **"show it to me again" is not a
feature that can be built**. Keys are prefixed `pk_live_` so one in a log, a
paste or a public repo is recognisable at a glance and greppable by a scanner.
New keys never inherit `*`: the demo seed key carries a wildcard, and a customer
key inheriting it would hold every scope this API ever grows.

## Config

| var | default | notes |
|---|---|---|
| `PATHIEL_AUTH_DOMAIN` | `localhost:8000` | **set this in production.** Must match the host the browser is on, or every signature is rejected. Never read from the request's `Host` header, which an attacker controls |
| `PATHIEL_AUTH_CHAIN_ID` | `999` (HyperEVM) | cosmetic; EIP-4361 chain id is informational and `personal_sign` is not chain-bound |
| `PATHIEL_AUTH_URI` | `https://<domain>` | cosmetic |
| `PATHIEL_AUTH_DB` | `$PATHIEL_STATE_DIR/auth.db` | users, sessions, nonces |
| `PATHIEL_INSECURE_COOKIES` | unset | forces `Secure` off. Rarely needed: plain HTTP to localhost is detected from the request |

Cookies are `httpOnly`, `SameSite=Lax`, and `Secure` everywhere except plain
HTTP to localhost — where a `Secure` cookie is silently dropped by the browser
and sign-in fails with a 200 and no session, which is the worst shape a failure
can take.

## Tests

```sh
python -m pytest services/auth/tests -q
```

132 tests (2026-10-17), each named for the attack it stops rather than the
feature it exercises: phishing, replay, impersonation, tampering, pre-dating,
clock skew, the login oracle (a bad nonce and a bad signature must be
indistinguishable), cross-tenant key access, a session token never touching
disk in the clear (checked across the WAL too, since a snapshot takes the
whole file set) — and, in `test_integration.py`, a forged `Host:` header not
buying local mode, a cross-site `Origin` not buying it either even from a
genuinely loopback peer, address A never controlling address B's account, and
a stateless session never inheriting the operator role.

A green auth suite that only proves the happy path works is the most dangerous
kind of green: login working is not evidence that login cannot be bypassed.
