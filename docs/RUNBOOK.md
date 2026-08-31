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
