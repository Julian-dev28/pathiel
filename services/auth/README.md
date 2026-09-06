# services/auth

Wallet sign-in, sessions, and the API keys customers use against
`services/pathia_data_api`.

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

The **first wallet to sign in on a fresh deployment becomes operator**, which
closes the open-kill-switch window without a bootstrap password to leak. The
failure mode is that anything signing in first locks the real operator out —
`scripts/grant_operator.py` is the way back, and it can grant before a first
sign-in so setting up a box does not require logging in as a plain user.

Operator gates the **house** account (the deployment's own equity, positions,
P&L, funnel). Any signed-in wallet sees **its own** account at
`/api/dashboard/account`, read from Hyperliquid with no stored key.

## API keys

`api_keys.py` talks to the `api_keys` table in **plain SQL**, and does not
import `services/pathia_data_api`. That service is a separate deploy unit with
its own Dockerfile and its own requirements, none of which are installed in the
trading image, so importing its models would fail at runtime even with the
source copied in. `test_dockerfile_does_not_bundle_pathia_data_api` enforces
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
| `PATHIA_AUTH_DOMAIN` | `localhost:8000` | **set this in production.** Must match the host the browser is on, or every signature is rejected. Never read from the request's `Host` header, which an attacker controls |
| `PATHIA_AUTH_CHAIN_ID` | `999` (HyperEVM) | cosmetic; EIP-4361 chain id is informational and `personal_sign` is not chain-bound |
| `PATHIA_AUTH_URI` | `https://<domain>` | cosmetic |
| `PATHIA_AUTH_DB` | `$PATHIA_STATE_DIR/auth.db` | users, sessions, nonces |
| `PATHIA_INSECURE_COOKIES` | unset | forces `Secure` off. Rarely needed: plain HTTP to localhost is detected from the request |

Cookies are `httpOnly`, `SameSite=Lax`, and `Secure` everywhere except plain
HTTP to localhost — where a `Secure` cookie is silently dropped by the browser
and sign-in fails with a 200 and no session, which is the worst shape a failure
can take.

## Tests

```sh
python -m pytest services/auth/tests -q
```

Fifty tests, each named for the attack it stops rather than the feature it
exercises: phishing, replay, impersonation, tampering, pre-dating, clock skew,
the login oracle (a bad nonce and a bad signature must be indistinguishable),
cross-tenant key access, and that a session token never touches disk in the
clear — checked across the WAL too, since a snapshot takes the whole file set.

A green auth suite that only proves the happy path works is the most dangerous
kind of green: login working is not evidence that login cannot be bypassed.
