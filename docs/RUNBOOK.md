# Runbook

Operator procedures for the things that only matter when something has
already gone wrong.

## Restoring state from a backup

`scripts/backup_state.py` keeps 14 archives in `~/pathiel-backups` (override
with `PATHIEL_BACKUP_DIR`). It captures the three things that cannot be
recreated — `.agent-config.json`, the `.state/strategies/` desk-journal
evidence base, and `capital_flows.jsonl` — and never captures `.env.local`
or any key material.

2026-10-23 (wave 2): nothing runs this automatically any more —
scripts/scheduler.py, which used to dispatch it daily, is retired with the
rest of the autonomous engine. Run it by hand, or add your own cron/launchd
entry.

To restore, with the server stopped:

```sh
scripts/restart.sh stop
tar tzf ~/pathiel-backups/pathiel-state-YYYYMMDD-HHMMSS.tar.gz   # look first
tar xzf ~/pathiel-backups/pathiel-state-YYYYMMDD-HHMMSS.tar.gz -C .
scripts/restart.sh server
```

Paths inside the archive are relative to the repo root, so extract from there.

Check the last backup at any time:

```sh
python scripts/backup_state.py          # writes and verifies a fresh one
python scripts/preflight_live.py        # reports age, size and verification
```

An archive that fails verification is renamed `.tar.gz.corrupt` and the receipt
records `verified: false`, which reports as *no backup* to both the metric and
`PathielBackupStale` — a broken backup must never read as a working one.


## Before funding the account

The executor refuses every order under the structural floor
(`min_tradable_equity`). `scripts/preflight_live.py` reports whether the one
entry path left (main-engine / MCP-submitted, since the strategy books were
retired 2026-10-23) can actually place a trade at the account's current
equity — derived from the same `services/risk` config the executor reads,
read-only, no order ever placed.

```sh
python scripts/preflight_live.py
```

Its `equity` and `trade capacity` lines answer "can this account trade right
now, and if not, what is short" directly from `GET /api/risk/limits`'s own
arithmetic — no separate simulation script to keep in sync with the executor.

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

**Set `PATHIEL_AUTH_DOMAIN` to the deployed host before anyone tries to log in.**
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

2026-10-23 (wave 2): the process supervisor script and the `loop`/`sched`
processes it used to watch are retired with the rest of the autonomous
engine. `scripts/restart.sh stop` now halts only `server` and `rotator`, and
writes each to `supervisor_halt.json` as a record of operator intent (there
is no automated supervisor reading that file today). Every start clears
that component's marker.

```sh
bash scripts/restart.sh status     # what is up, what is halted
bash scripts/restart.sh server     # dashboard + JSON API only
bash scripts/restart.sh restart    # server + rotator
```

Trading itself has no process to stop or start: Claude places every trade
through the MCP server (`scripts/pathiel-mcp-server.py`), a transient stdio
process Claude Code respawns per tool call. Stopping the loop that used to
guard `/api/agent/start`/`stop` for the retired autonomous bot no longer
applies — the dashboard's STOP TRADING button sets `mode: OFF` in
`.agent-config.json` instead (see docs/overhaul/DESK_UI_2026-10-23.md).
