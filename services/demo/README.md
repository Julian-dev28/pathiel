# services/demo — synthetic data for the public demo deployment

Generates a session log, positions snapshot, agent config and shadow ledgers so
the dashboard can be deployed somewhere public without an exchange connection,
a private key, or the trading loop.

## Why this exists

The dashboard is worth showing and cannot be shown. Everything on it — equity,
open positions, closed trades, drawdown, the book league table — is the live
account's book. The 2026-09-04 audit closed those endpoints by default for
exactly that reason.

The fix is not to mock the dashboard. It is to mock its **inputs**. Every file
the dashboard reads is already env-overridable, because the Fly deployment needs
that for its mounted volume:

| Env var | What reads it |
|---|---|
| `SESSION_LOG_PATH` | `pathia/session_log.py` — equity, PnL, activity, closed trades |
| `PATHIA_POSITIONS_SNAPSHOT_FILE` | `pathia/positions_snapshot.py` — the positions table |
| `PATHIA_AGENT_CONFIG_FILE` | `pathia/agents/config_store.py` — the live-books table |
| `PATHIA_STATE_DIR` | `pathia/agents/shadow_ledger.py` — the book league table |

Point those four at generated files and `pathia/dashboard.py` runs unmodified.
The demo therefore exercises the production renderers — the funnel, the drawdown
walk, the fee-drag maths — rather than a parallel implementation that would rot.

## Why it generates rather than ships a fixture

The dashboard is time-aware:

- `_summary_payload` reports `offline` when the newest heartbeat is over 300s old.
- `read_position_snapshot` refuses a snapshot older than 120s.

A committed fixture is stale within minutes, so the demo would render perfectly
and report a dead bot. Timestamps have to be minted at boot. Vercel's filesystem
is read-only outside `/tmp` anyway, so runtime generation was the only option
that works.

## What it will not do

Emit a number the real system could not produce. The equity walk is held inside
a −6%/+11% envelope, position notionals stay inside the configured sizing, and
the win rate sits near 52% with winners modestly larger than losers. Those
bounds are asserted in `tests/test_generator.py`, not left to the seed.

The reasoning: a demo on a public URL showing a 400% month is not a screenshot
of a product, it is a performance claim with nothing behind it. The book theses
already carry real validation stats (`n=`, `mc_p=`); inventing returns next to
them would poison the honest numbers too.

## Usage

```python
from services.demo.generator import materialize

for key, path in materialize("/tmp/pathia-demo").items():
    os.environ[key] = path      # must happen BEFORE importing pathia.dashboard
```

Deterministic for a given `seed` and `now_ms`. `api/index.py` is the only caller.

## Tests

```
python -m pytest services/demo/tests/ -q
```

26 gate tests, no network, ~0.4s. They cover determinism, timeline freshness
(the regression that made every boot read "stale"), internal agreement between
the panels — open-position count on the heartbeat versus rows in the snapshot,
`leveraged_pct` versus `unrealized_pct × leverage` — and the bounds above.

## Deploying

```
vercel deploy --prod
```

`vercel.json` pins `builds` + `routes` rather than the newer `functions` +
`rewrites`, and sets `"framework": null`. This is deliberate. Vercel detects
FastAPI in this repo and applies its own preset, which builds a function it
names `fastapi` and routes to an entrypoint of its choosing — that overrode
`api/index.py` and served 404 on every path. The legacy keys disable zero-config
detection outright, which is the only way to be sure the deployed function is
the one in this repo.

Set no environment variables on the Vercel project. The entrypoint refuses to
boot if it finds an exchange credential, which is the intended behaviour: this
deployment must never be able to reach the live account.
