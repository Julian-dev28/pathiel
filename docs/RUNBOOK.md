# Runbook

Operator procedures for the things that only matter when something has
already gone wrong.

## Restoring state from a backup

`scripts/backup_state.py` runs daily at 04:30 from the scheduler and keeps 14
archives in `~/pathia-backups` (override with `PATHIA_BACKUP_DIR`). It captures
the three things that cannot be recreated — `.agent-memory.json`, the
`shadow_ledger` evidence base, and `capital_flows.jsonl` — and never captures
`.env.local` or any key material.

To restore, with the loop stopped:

```sh
scripts/restart.sh stoploop
tar tzf ~/pathia-backups/pathia-state-YYYYMMDD-HHMMSS.tar.gz   # look first
tar xzf ~/pathia-backups/pathia-state-YYYYMMDD-HHMMSS.tar.gz -C .
scripts/restart.sh loop
```

Paths inside the archive are relative to the repo root, so extract from there.
`stoploop` writes a halt marker, so the supervisor will not restart the loop
underneath you; `restart.sh loop` clears it.

Check the last backup at any time:

```sh
python scripts/backup_state.py          # writes and verifies a fresh one
python scripts/preflight_live.py        # reports age, size and verification
```

An archive that fails verification is renamed `.tar.gz.corrupt` and the receipt
records `verified: false`, which reports as *no backup* to both the metric and
`PathiaBackupStale` — a broken backup must never read as a working one.


## Before funding the account

The executor refuses every order under the structural floor, so at the current
balance the books have never actually run. `scripts/funded_dry_run.py` answers
what happens the moment money lands, derived from the same config the executor
reads and the books' own forward ledgers. It never invokes the order path —
mode is LIVE, so "simulating" through `executor.maybe_execute` would place real
orders.

```sh
python scripts/funded_dry_run.py              # at the derived floor
python scripts/funded_dry_run.py --equity 50  # at a partial deposit
```

At partial funding the report says which books can hold a position and which
wait. Read that carefully: concurrency is first-come, so the account trades
whatever signals soonest, not whatever signals best. Funding to the derived
floor is what makes the book set behave as configured rather than as a race.

---

## Signing in for the first time

Human access is a wallet signature; there is no password anywhere in the
system. The **first wallet to sign in on a fresh deployment claims the operator
role**, which closes the open-kill-switch window without a bootstrap credential
to leak.

That has one failure mode worth knowing before it bites: if anything else signs
in first — a smoke check, a test wallet, someone who found the URL — the real
operator is a plain user, gets 403 on the house account, and cannot reach the
kill switch. It happened during development on 2026-09-04.

```sh
python scripts/grant_operator.py --list          # who holds what
python scripts/grant_operator.py 0xYourWallet    # claim it (works before first sign-in)
python scripts/grant_operator.py 0xOther --revoke
```

`--list` is safe to run any time and reads the same database the server uses.

**Set `PATHIA_AUTH_DOMAIN` to the deployed host before anyone tries to log in.**
The domain is inside the signed bytes and is checked against config, never
against the request's own `Host` header (an attacker controls that). If it does
not match what the browser is on, every signature is rejected for a domain
mismatch and the failure looks like "sign-in is broken".

Two tiers exist once signed in. The **house** account — the deployment's own
equity, positions, P&L and funnel — is operator-only. Any signed-in wallet sees
**its own** account at `/api/dashboard/account`, read from Hyperliquid with no
stored key. If a customer reports seeing someone else's balance, that is a
cross-tenant leak and not a display bug; it was one on 2026-09-04.

## Restarting after a full stop

`scripts/restart.sh stop` halts the loop, server, scheduler and rotator, and
writes each to `.state/supervisor_halt.json` so `supervise_processes` will not
bring them back. Every start clears that component's marker.

```sh
bash scripts/restart.sh status     # what is up, what is halted
bash scripts/restart.sh server     # dashboard only, no trading
bash scripts/restart.sh restart    # everything, including live trading
```

One consequence of a long stop: `data_logger` writes the funding/OI panel from
inside the loop, so while the loop is down that panel stops growing.
`xs_reversal` needs ~7 days of trailing funding history to judge a coin's market
awake, so after a pause longer than that it will decline to rank until the panel
refills. It takes no trade rather than trading on partial data, which is the
correct failure.
