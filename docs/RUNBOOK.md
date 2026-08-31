# Runbook

Operator procedures for the things that only matter when something has
already gone wrong.

## Restoring state from a backup

`scripts/backup_state.py` runs daily at 04:30 from the scheduler and keeps 14
archives in `~/hermes-backups` (override with `HERMES_BACKUP_DIR`). It captures
the three things that cannot be recreated — `.agent-memory.json`, the
`shadow_ledger` evidence base, and `capital_flows.jsonl` — and never captures
`.env.local` or any key material.

To restore, with the loop stopped:

```sh
scripts/restart.sh stoploop
tar tzf ~/hermes-backups/hermes-state-YYYYMMDD-HHMMSS.tar.gz   # look first
tar xzf ~/hermes-backups/hermes-state-YYYYMMDD-HHMMSS.tar.gz -C .
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
`HermesBackupStale` — a broken backup must never read as a working one.
