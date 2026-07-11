# Hermes Quant Integrity and CI/CD Handoff

**Audience:** Fable 5, as downstream verifier and implementer

**Audit snapshot:** 2026-07-11, branch `able`, commit `d903f025f523ed3d32e9fd15fa08da8ed373f766`

**Scope:** verification and implementation brief only. This handoff does not authorize changes to live configuration, trading logic, persisted state, credentials, or running processes.

## 1. Executive decision

Hermes has a healthy offline unit-test baseline, several useful point-in-time and portfolio simulation primitives, exact-first book-open logging for newer trades, and basic operational health endpoints. It does **not** yet have one reconciled accounting source of truth or a CI/CD path capable of proving that a strategy, release artifact, and deployed local-Mac process are the same verified system.

Fable 5 should implement this in four dependency-ordered layers:

1. Make exchange facts immutable and reconciled.
2. Make every PnL report and strategy score a view of those facts.
3. Make portfolio backtests deterministic, lookahead-safe, cost-stressed, and promotion-gated.
4. Gate a versioned artifact in CI, then deploy it manually in `OFF` mode with explicit activation and code-only rollback.

No strategy may become live merely because CI passes. Eligibility requires the quantitative gates in section 6, followed by explicit operator approval.

## 2. Verified baseline

The following results were reproduced in the local project virtual environment; they are observations, not estimates.

| Check | Verified result | Evidence |
|---|---:|---|
| Initial offline test baseline | `495 passed, 14 deselected, 1 warning in 36.30s` | Pytest defaults exclude `online` and `live` in [`pyproject.toml`](../../../pyproject.toml#L38); the workflow relies on that default in [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml#L28). |
| Post-remediation offline suite | `496 passed, 14 deselected, 1 warning in 36.91s` | The additional passing test is the portfolio CLI regression in [`tests/test_backtest_portfolio.py`](../../../tests/test_backtest_portfolio.py#L15). |
| Excluded tests | 12 `online`, 2 `live` | Module-level markers are in [`tests/test_online.py`](../../../tests/test_online.py#L17) and [`tests/test_e2e_live.py`](../../../tests/test_e2e_live.py#L18). The README states that live tests place a real order and make a billable model call in [`README.md`](../../../README.md#L444). |
| Dependency warning | One `StarletteDeprecationWarning`: FastAPI's test client imports the deprecated httpx integration and recommends `httpx2` | Local venv was Python `3.13.7`; project support starts at 3.11 in [`pyproject.toml`](../../../pyproject.toml#L5). This warning must be rechecked independently on the supported 3.11/3.12 CI matrix before changing dependencies. |
| Ruff | Fails with exit 1: `Found 1592 errors`; 172 are reported auto-fixable | Ruff is a dev dependency in [`pyproject.toml`](../../../pyproject.toml#L23), but no lint step exists in [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml#L7). |
| Current CI | One pytest job on Python 3.11 and 3.12 | [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml#L7) contains the only tracked workflow and its only gate is `pytest`. |
| Portfolio replay entry point | Remediated in this worktree: `--help` exits successfully | The stale capital-rotation import and CLI arm were removed from [`scripts/backtest_portfolio.py`](../../../scripts/backtest_portfolio.py); [`tests/test_backtest_portfolio.py`](../../../tests/test_backtest_portfolio.py#L15) prevents regression. Capital rotation itself remains intentionally retired. |

Reproduction, from repository root:

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
.venv/bin/python --version
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest --collect-only -q -m online
.venv/bin/python -m pytest --collect-only -q -m live
.venv/bin/ruff check .
git ls-files '.github/workflows/*'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/backtest_portfolio.py --help
```

Do not run `pytest -m live` as part of verification: that suite is intentionally capable of placing real orders. The offline suite redirects memory, config, DSL, strategy state, and the session log to temporary paths before collection; see [`tests/conftest.py`](../../../tests/conftest.py#L1).

## 3. Confirmed findings

Each item below is directly supported by checked-in code. Hypotheses and consequences that still need measurement are separated in section 4.

### F1 — CI is an offline pytest matrix, not a release gate

The workflow installs editable dev dependencies and runs `pytest` on Python 3.11/3.12. It has no Ruff, type-check, wheel/sdist build, installed-package import, Docker build/startup, deterministic backtest, ledger reconciliation, artifact upload, deployment promotion, or rollback stage. The project metadata contains pytest and Ruff but no type checker; the Dockerfile builds a runtime image but has no `HEALTHCHECK`. Sources: [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml#L7), [`pyproject.toml`](../../../pyproject.toml#L23), and [`Dockerfile`](../../../Dockerfile#L1).

Reproduce:

```bash
nl -ba .github/workflows/ci.yml
nl -ba pyproject.toml
nl -ba Dockerfile
rg -n 'ruff|mypy|pyright|build|docker|upload-artifact|deploy|rollback|backtest|reconcil' .github pyproject.toml
```

### F2 — Exchange attribution and local outcome reporting use different accounting models

`pnl_by_book.py` fetches exchange fills, deduplicates them by `tid`, sorts them, reconstructs per-coin flat-to-flat episodes, accumulates exchange `closedPnl` and `fee`, and reports `net = closedPnl - fee`. Sources: [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L114), [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L139), and [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L297).

The local outcome store is instead a capped list of at most 500 close records in a process-local singleton persisted to `.agent-memory.json`. Bot-routed closes estimate gross PnL from entry price, returned average fill price, and stored size; assume a fixed 2.5 bps taker fee per side; and optionally estimate all funding by holding the **entry** hourly rate constant. External/vanished closes inspect at most 100 recent fills, select one close-like fill, and may fall back to an estimated price/notional. Sources: [`hermes_trader/agents/memory.py`](../../../hermes_trader/agents/memory.py#L23), [`hermes_trader/agents/memory.py`](../../../hermes_trader/agents/memory.py#L181), [`hermes_trader/agents/executor.py`](../../../hermes_trader/agents/executor.py#L1470), and [`hermes_trader/agents/executor.py`](../../../hermes_trader/agents/executor.py#L1546).

`pnl_attribution.py` consumes only those local close rows and sums `realized_pnl_usd`; it does not reconcile them to exchange fill IDs. Source: [`scripts/pnl_attribution.py`](../../../scripts/pnl_attribution.py#L48).

Reproduce statically and with existing offline regression fixtures:

```bash
nl -ba scripts/pnl_by_book.py | sed -n '114,189p;297,340p'
nl -ba hermes_trader/agents/executor.py | sed -n '1470,1652p'
nl -ba scripts/pnl_attribution.py | sed -n '48,69p'
.venv/bin/python -m pytest -q tests/test_pnl_by_book.py tests/test_outcome_store.py
```

### F3 — Strategy attribution is exact-first only for the newer event history

Exchange fills do not contain the Hermes strategy book. Current attribution first joins a flat-to-flat episode to `book_open` session events or parsed `LIVE opened` loop-log lines, using coin, side, and a ±15-minute window. It then falls back to legacy intent/candidate events, which the script itself marks as over-attributing, and defaults unmatched episodes to `main-engine`. Sources: [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L18), [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L71), [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L198), and [`scripts/pnl_by_book.py`](../../../scripts/pnl_by_book.py#L273).

Newer live books emit `book_open` after successful execution—for example [`hermes_trader/agents/engulf_short_live.py`](../../../hermes_trader/agents/engulf_short_live.py#L353)—and the regression test proves exact records outrank fuzzy records. The same test documents a historical misattribution that changed a sizing decision: [`tests/test_pnl_by_book.py`](../../../tests/test_pnl_by_book.py#L1).

Reproduce:

```bash
.venv/bin/python -m pytest -q tests/test_pnl_by_book.py
rg -n 'event.*book_open' hermes_trader/agents tests
nl -ba scripts/pnl_by_book.py | sed -n '18,99p;198,294p'
```

### F4 — Backtest capabilities are fragmented and do not form one promotion contract

There are useful pieces:

- `backtest.py` evaluates signals on data through bar *t* and enters at the next bar's open, but holds a fixed input equity independently per coin, charges only a fixed 5 bps round trip, omits funding, and does not model portfolio contention. See [`scripts/backtest.py`](../../../scripts/backtest.py#L180) and its own caveats in [`scripts/backtest.py`](../../../scripts/backtest.py#L278).
- `backtest_logged.py` enters on the first forward bar's open and removes that bar before exit simulation, with configurable taker fee and per-side slippage. It sizes every isolated trade from the same starting equity. See [`scripts/backtest_logged.py`](../../../scripts/backtest_logged.py#L415) and [`scripts/backtest_logged.py`](../../../scripts/backtest_logged.py#L539).
- `backtest_portfolio.py` walks a shared clock, updates realized equity, and enforces concurrency, notional, and margin gates. Its stale dependency on the retired capital-rotation feature was removed in this worktree. Its drawdown calculation still uses realized equity only, so open-position mark-to-market drawdown is absent. See [`scripts/backtest_portfolio.py`](../../../scripts/backtest_portfolio.py#L185) and [`scripts/backtest_portfolio.py`](../../../scripts/backtest_portfolio.py#L275).
- `shadow_ledger.py` has lookahead-safe forward bars, funding, de-duplication, chronological halves, and 0/6/12/25/50 bps stress tiers, but it grades independent signals rather than a shared live portfolio. See [`hermes_trader/agents/shadow_ledger.py`](../../../hermes_trader/agents/shadow_ledger.py#L31), [`hermes_trader/agents/shadow_ledger.py`](../../../hermes_trader/agents/shadow_ledger.py#L167), and [`hermes_trader/agents/shadow_ledger.py`](../../../hermes_trader/agents/shadow_ledger.py#L240).

No checked-in component combines all of: next-bar execution, shared equity, mark-to-market drawdown, real portfolio gates, actual funding, deterministic fixtures, the full cost sweep, and the required promotion thresholds.

Reproduce:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/backtest_portfolio.py --help
.venv/bin/python -m pytest -q tests/test_shadow_ledger.py
rg -n 'ROUND_TRIP|funding|slip|next_bar|entry_px|peak_eq|max_dd|max_concurrent' scripts/backtest*.py hermes_trader/agents/shadow_ledger.py
```

### F5 — Runtime correctness depends on a single state-owning loop, with partial mitigations rather than an atomic singleton guarantee

Agent memory is a mutable, process-local singleton backed by a shared JSON file. Flush uses a fixed `.tmp` path plus `os.replace`, without a cross-process writer lock; comments document prior truncation by an unhydrated process. The server is now started with `HERMES_STATE_READONLY=1`, and its lifespan deliberately avoids shutdown flush because its in-memory copy becomes stale while the loop writes. Sources: [`hermes_trader/agents/memory.py`](../../../hermes_trader/agents/memory.py#L29), [`hermes_trader/agents/memory.py`](../../../hermes_trader/agents/memory.py#L98), [`hermes_trader/server.py`](../../../hermes_trader/server.py#L104), and [`scripts/restart.sh`](../../../scripts/restart.sh#L150).

`restart.sh` detects loop processes by command-line pattern and avoids starting when it sees one, but check-then-spawn is not an atomic host lock. Concurrent invocations can therefore race. The loop also keeps mutable module globals and a watchdog thread, reinforcing the one-loop ownership model. Sources: [`scripts/restart.sh`](../../../scripts/restart.sh#L61), [`scripts/restart.sh`](../../../scripts/restart.sh#L115), and [`scripts/trading_loop.py`](../../../scripts/trading_loop.py#L123).

Reproduce without changing process state:

```bash
nl -ba hermes_trader/agents/memory.py | sed -n '29,132p'
nl -ba hermes_trader/server.py | sed -n '104,117p'
nl -ba scripts/restart.sh | sed -n '61,148p;150,177p'
rg -n '^_[A-Za-z].*=|global ' scripts/trading_loop.py hermes_trader/server.py
```

Do **not** race two restart commands merely to prove this risk on a live host. Prove the future lock with isolated subprocess tests and temporary state.

### F6 — Cached/local account state can drift from live state, and existing guards demonstrate the operational sensitivity

The server notes that its memory copy becomes stale after boot. `reload_entry_ctx()` exists because one-shot `load()` otherwise freezes entry context in another process. The trading loop explicitly rejects zero-equity and missing-dex reads to avoid poisoning memory or falsely tripping the kill switch, while keeping the last known value. These are valuable mitigations, but they also establish that local state, snapshots, and partial API reads are not automatically authoritative. Sources: [`hermes_trader/server.py`](../../../hermes_trader/server.py#L104), [`hermes_trader/agents/memory.py`](../../../hermes_trader/agents/memory.py#L169), and [`scripts/trading_loop.py`](../../../scripts/trading_loop.py#L436).

Reproduce:

```bash
nl -ba hermes_trader/agents/memory.py | sed -n '169,179p;199,265p'
nl -ba scripts/trading_loop.py | sed -n '436,525p'
.venv/bin/python -m pytest -q tests/test_dashboard_perf.py tests/test_open_reason.py
```

### F7 — Production metrics are account/process-level, not strategy/accounting-level

The `/metrics` implementation exports equity, open-position count, open notional, unrealized PnL, recorded-trade count, and live-mode status. It does not expose realized net PnL after all costs, expectancy by book, drawdown, turnover, fee/PnL ratio, rejection rates, heartbeat age, process duplication, reconciliation drift, or sample sizes. Sources: [`hermes_trader/metrics.py`](../../../hermes_trader/metrics.py#L20) and the endpoint in [`hermes_trader/server.py`](../../../hermes_trader/server.py#L695).

Reproduce:

```bash
nl -ba hermes_trader/metrics.py
rg -n 'Gauge|Counter|Histogram' hermes_trader/metrics.py
.venv/bin/python -m pytest -q tests/test_metrics.py
```

### F8 — Local-Mac operation is manual and has no versioned promotion/rollback protocol

The documented normal local operation is `scripts/restart.sh`; that script stops command-pattern matches, starts the current working tree with `nohup`, waits only for the PID to remain alive briefly, and prints status. It does not select a content-addressed release, back up state, force `OFF`, validate an application heartbeat, promote a tested artifact, or retain/activate a prior code artifact. Sources: [`README.md`](../../../README.md#L421) and [`scripts/restart.sh`](../../../scripts/restart.sh#L87).

The existing deployment guide is for Fly.io rather than the authoritative local-Mac runtime and likewise describes a manual `flyctl deploy`; it should not be mistaken for local CD. Source: [`DEPLOY.md`](../../../DEPLOY.md#L77).

Reproduce without restarting anything:

```bash
scripts/restart.sh status
nl -ba scripts/restart.sh
rg -n 'release|artifact|backup|rollback|health|heartbeat|mode' scripts/restart.sh README.md DEPLOY.md
```

## 4. Hypotheses to test, not facts to assume

| ID | Hypothesis | Why it is plausible | Required falsification/confirmation |
|---|---|---|---|
| H1 | Current exchange-fill totals and `.agent-memory.json` close totals differ materially for at least some time windows/books. | F2 proves different event coverage, fee/funding assumptions, caps, and close models; it does not quantify current drift. | On a read-only account snapshot, compare gross, fees, funding, and net by UTC day and full retained window. Report unmatched IDs and amounts. Do not accept aggregate cancellation as reconciliation. |
| H2 | Legacy fuzzy attribution still changes at least one book's sign or ranking. | F3 proves fuzzy/default paths remain for old history and records a prior material error. | Run `pnl_by_book.py` on a copied session/loop log and export exact/legacy/default counts plus a sensitivity table that excludes legacy rows. This command contacts Hyperliquid and should be run only in an approved read-only audit environment. |
| H3 | Two loop writers can lose or roll back state despite atomic rename. | F5 proves no cross-process writer lock; a race is plausible, not observed in this audit. | Spawn two isolated writers against a temporary `HERMES_AGENT_MEMORY_FILE`; assert the future singleton lock rejects the second writer and that concurrent reconciliation cannot lose rows. Never test on live state. |
| H4 | Realized-only backtest drawdown understates portfolio drawdown. | F4 shows max drawdown is updated from realized equity without marking open positions. | Use a fixture where open positions suffer a large interim loss and later recover. The current metric should miss it; the new mark-to-market metric must capture it. |
| H5 | The proposed promotion thresholds reduce false positives but cannot establish future profitability. | They enforce sample size, chronological stability, high-cost survival, and bounded simulated drawdown; none removes regime or model risk. | Track shadow/live forward results separately after eligibility. Never rewrite the backtest gate using future live outcomes without a versioned research decision. |

## 5. Canonical immutable execution ledger

This is the critical path. Do not first “fix” the reports independently; that would create a third accounting model.

### 5.1 Contract

Implement an append-only execution-event ledger with immutable source facts and deterministic derived views:

- `fill` events keyed by `(venue, account, fill_id/tid)`, retaining order ID, raw payload hash, exchange timestamp, ingest timestamp, coin/dex, buy/sell, price, quantity, notional, exchange `closedPnl`, fee amount/token, and liquidation/crossing metadata.
- `funding` events keyed by the exchange's stable funding identifier where available, otherwise a deterministic composite `(venue, account, coin, funding_time, amount, rate)`. Never fabricate a fill ID for funding.
- `fill_leg` derivations split a reversal fill into deterministic `:close` and `:open` legs. Partial fills and scale-outs retain their original fill IDs and quantities.
- Every opening leg carries an exact `strategy_book`, `book_open_event_id`, signal/analysis ID where available, side, regime snapshot ID, and position/episode ID. Attribution is fixed when the opening leg is accepted; reports must not redo a timestamp guess.
- Derived amounts use decimal arithmetic from exchange strings: gross realized PnL, fees, funding allocated over the actual exposure interval, entry/exit slippage against a versioned reference price, and `net_pnl = gross - fees + funding - slippage_cost`. Preserve source values and calculation version.
- Never update a historical source row in place. Corrections are new versioned adjustment events with reason, actor, time, and superseded calculation version.

If exact strategy attribution is unavailable for legacy history, store `strategy_book = unknown_legacy` plus the candidate attribution and confidence/source in separate fields. Do not silently default it into `main-engine` for promotion decisions.

### 5.2 Idempotent reconciliation

Reconciliation must:

1. Backfill with an overlap window rather than trusting only a watermark.
2. Insert by stable event key; an identical duplicate is a no-op.
3. Quarantine a duplicate key whose raw payload hash differs.
4. Rebuild derived legs/episodes deterministically from immutable events.
5. Commit events and the new watermark atomically.
6. Produce a reconciliation manifest: requested range, exchange row count, inserted/no-op/conflict counts, ledger totals, exchange totals, drift by component, and code/schema versions.
7. Return byte-identical canonical totals and manifests, except run timestamp, on repeated reconciliation of the same snapshot.

### 5.3 Reporting

Retire direct PnL calculation from `pnl_attribution.py` and `pnl_by_book.py`; keep them as ledger-query front ends or compatibility wrappers. Every report must expose, at minimum:

- strategy book, long/short side, asset/coin and asset class, entry regime, and explicit time window;
- trade/episode count and fill count;
- gross PnL, fees, funding, slippage cost, and net PnL;
- net expectancy per independent trade as the primary ranking metric;
- win rate, average win/loss, payoff ratio, turnover, fee/PnL ratio, maximum drawdown, and attribution quality (`exact`, `unknown_legacy`, `adjusted`);
- sample-size warnings and reconciliation freshness/drift.

Ranks must default to **net expectancy after all costs**, not gross PnL, win rate, or total PnL.

## 6. Standard portfolio backtest and promotion gate

### 6.1 One deterministic contract

Replace the fragmented promotion path with one library API and one thin CLI. The contract must specify:

- Signals may use only fully closed data available at decision time *t*.
- An accepted signal executes no earlier than the next tradable bar's open. Missing bars, delistings, and price gaps have explicit deterministic rules.
- Same-bar stop/target ambiguity uses a documented conservative ordering unless higher-resolution data resolves the sequence.
- All strategies share one event clock, equity pool, margin pool, positions, claims, concurrency limit, total-notional limit, per-book limit, cooldowns, and the same risk gates used by live execution through pure shared functions.
- Equity is marked to market every event/bar. Drawdown is peak-to-trough portfolio equity including open PnL, realized PnL, fees, funding, and modeled slippage.
- Funding uses timestamped historical rates over actual exposure; missing funding is an explicit test failure or artifact warning, never silently zero for promotion.
- Decimal/rounding rules, tie ordering, random seeds, fixture versions, config hash, dataset hash, commit, and Python version are embedded in the artifact.
- Run total all-in trading-cost stress tiers of `0, 6, 12, 25, 50` bps per completed round trip, excluding separately reported funding. The artifact must state this convention; no per-side/round-trip ambiguity is allowed.

The existing next-bar logic, shared portfolio clock, and shadow funding/cost code should be refactored into this contract rather than copied again.

### 6.2 Eligibility thresholds

A strategy/configuration is only **eligible for operator review** when the same deterministic portfolio run satisfies all of the following:

1. At least 100 independent completed trades overall.
2. At least 30 independent completed trades in each chronological half.
3. Positive net expectancy in both chronological halves at the 25 bps cost tier.
4. Positive full-sample net expectancy at the 50 bps cost tier.
5. Simulated mark-to-market maximum drawdown no greater than 20% of peak portfolio equity.
6. No reconciliation, missing-data, nondeterminism, lookahead, or fixture-integrity failure.

The 20% ceiling is a strategy-promotion ceiling, not permission to weaken account-level emergency controls. A passing backtest does not toggle config or place orders. Forward shadow performance is reported beside the artifact but is not a mandatory gate under this brief. Live activation always requires an explicit operator action tied to the approved artifact/config hashes.

## 7. CI implementation roadmap

Use least-privilege, offline-by-default jobs. CI must receive no trading private key, wallet authority, operator token, or live `.env.local`/state files.

### Phase A — dedicated Ruff baseline cleanup

Create a focused cleanup change that makes the intended whole tree pass. Review semantic edits separately from mechanical formatting. Do not make the 1,592 existing findings disappear through blanket `ignore`, broad `per-file-ignores`, mass `# noqa`, or exclusion of production/research directories.

Before cleanup lands, a non-blocking Ruff report is acceptable for visibility. After it lands, make the exact whole-tree command blocking:

```bash
ruff check .
ruff format --check .
```

### Phase B — blocking CI stages

1. **Safety bootstrap:** assert no credential variables are present; force all state paths to temporary directories; force mode `OFF`; disable network/order adapters; fail if an order-placement function is reached.
2. **Lint:** whole-tree Ruff, after Phase A only.
3. **Offline tests:** retain the existing Python 3.11/3.12 matrix and run `pytest -m 'not online and not live'` explicitly. Treat unexpected deselection-count changes and warnings deliberately rather than hiding them.
4. **Type check:** add a chosen checker with a reviewed baseline and ratchet. Do not claim a whole-tree type gate until the configured scope actually passes.
5. **Package:** build wheel and sdist, install the wheel into a clean environment, import `hermes_trader`, run the console command help/smoke path, and confirm package data/static assets exist.
6. **Container:** build from `Dockerfile`, start with temporary mounted state, no credentials, and `OFF`; poll `/api/health`; verify no loop/order process starts unexpectedly; then stop cleanly.
7. **Quant regression:** run golden ledger reconciliation and deterministic portfolio fixtures at every cost tier. Run twice and compare canonical JSON/hashes.
8. **Artifact:** upload the wheel/sdist, image digest or build manifest, SBOM/dependency lock output, test/lint/type summaries, reconciliation report, quant JSON/Markdown, dataset/config hashes, and checksums. Retention must cover at least the rollback horizon.

Do not add automatic deployment or live promotion to GitHub Actions. CI produces a verified, versioned release candidate for the local operator.

## 8. Manual local-Mac CD protocol

Implement the following as a documented, mostly scripted workflow around the authoritative `scripts/restart.sh`. Commands must accept alternate root/state/config paths so the full protocol can be dry-run in a temporary directory.

### 8.1 Prepare and quiesce

1. Select the exact CI artifact by version, commit, and SHA-256; verify checksum and provenance locally.
2. Record current release ID, code commit, config hash, ledger schema/calculation version, and process PIDs.
3. Create a timestamped backup/manifest of mutable state: agent config/memory, DSL state, strategy/rebalancer state, ledger database plus WAL/sidecars, and session log position. Verify the backup can be read. Never package secrets into the release artifact.
4. Atomically set trading mode to `OFF` and verify a fresh heartbeat reports `OFF`. OFF continues exit monitoring under current behavior; explicitly document that distinction.
5. Stop the existing processes through `scripts/restart.sh stop` only after backup and OFF verification.

### 8.2 Deploy and validate OFF

1. Extract/install into a new immutable versioned release directory. Mutable state must live outside that directory and be selected by explicit environment paths.
2. Atomically switch the `current` release pointer; never overwrite the prior release in place.
3. Run schema compatibility/preflight checks. Migrations must be forward-compatible with the prior code or declare rollback blocked before activation.
4. Start with `scripts/restart.sh` and mode still `OFF`.
5. Require all of: exactly one trading-loop PID, exactly one server PID, HTTP `/api/health` success with expected version, a fresh loop heartbeat within the allowed scan interval, writable/readable state, zero reconciliation drift, and no new order event.
6. Observe at least two healthy heartbeat cycles before considering activation.

### 8.3 Explicit activation

The operator reviews the release manifest, health checks, reconciliation status, and approved strategy artifact/config hashes, then explicitly changes mode to `LIVE`. Record actor/time/release/config hashes. Verify the next heartbeat reports LIVE. This action must never be performed implicitly by install, restart, health success, or rollback.

### 8.4 Rollback

1. Set mode `OFF` and verify it.
2. Stop processes.
3. Switch `current` back to the prior **code artifact**.
4. Restart and rerun all OFF-mode checks.
5. Do **not** restore old mutable state over newer fills, positions, funding, ledger rows, DSL state, or logs. Reconcile current exchange state before any live re-enable.

If the prior code cannot read the current state schema, remain OFF and perform an operator-reviewed forward fix. Never solve code rollback by silently reverting trading state.

## 9. Monitoring requirements

Add strategy/accounting metrics sourced from the reconciled ledger and label them with bounded-cardinality dimensions (book, side, asset class, regime bucket, window—not raw order/fill IDs):

- net realized PnL after fees, funding, and slippage; gross-to-net bridge;
- net expectancy and sample size by book;
- account and per-book mark-to-market drawdown;
- turnover and gross exposure;
- fees-to-absolute-gross-PnL ratio and funding/PnL ratio;
- order/entry rejection rate by bounded reason and book;
- last successful loop heartbeat age and last successful reconciliation age;
- trading-loop process count, with an alert unless it equals one during operation;
- reconciliation drift and unmatched/conflicting event counts;
- exact versus unknown-legacy attribution counts;
- release version, config hash, ledger schema/calculation version, and current mode as info metrics.

Minimum alerts: stale heartbeat, duplicate/missing loop, nonzero reconciliation drift/conflicts, ledger ingestion stalled while exchange activity exists, drawdown threshold breach, anomalous fee/PnL ratio, high rejection rate, and insufficient sample size on any book marked eligible/live.

## 10. Required tests and fixtures

### 10.1 Golden ledger fixtures

Fixtures must contain stable venue-shaped IDs and expected decimal totals for:

- multiple partial fills on one order;
- partial scale-outs across several fills;
- a single reversal fill split into close/open legs;
- long and short funding receipts/payments over actual holding intervals;
- duplicate ingestion within one run and across a simulated restart;
- pagination overlap/watermark restart;
- two books trading the same coin at different times;
- missing exact legacy attribution, which must remain `unknown_legacy`;
- liquidation/external close and late-arriving fill/funding events;
- conflicting payload under an existing fill ID, which must quarantine/fail.

### 10.2 Backtest fixtures

Tests must prove:

- a signal on bar *t* cannot fill on bar *t*;
- mutating data after *t* cannot change the decision at *t*;
- next-bar gaps and same-bar stop/target ambiguity follow the declared rules;
- simultaneous candidates contend for shared slots, margin, notional, and claims deterministically;
- open losing positions affect mark-to-market equity and drawdown before close;
- funding sign and accrual window are correct for longs and shorts;
- all five cost tiers reduce net results monotonically;
- identical input/config/seed produces byte-identical canonical output and hash;
- each promotion gate fails independently: overall sample, each half sample, half expectancy at 25 bps, full expectancy at 50 bps, and drawdown over 20%.

### 10.3 Deployment dry run

Use a temporary release root and temporary state paths, a fake/non-routing exchange client, and mode `OFF`. Exercise install, backup verification, restart, `/api/health`, two fresh heartbeats, exact singleton checks, activation refusal in the test harness, code-only rollback, and post-rollback reconciliation. The dry run must not read `.env.local` and must fail if any real-order path is invoked.

## 11. Acceptance criteria

Fable 5 may call the work complete only when all of the following are demonstrated in artifacts:

1. Replaying the same exchange snapshot twice produces identical ledger event keys, identical derived totals, no new rows on the second pass, and no changed historical source rows.
2. Ledger versus exchange totals agree per account/coin/UTC day and overall within these initial tolerances: gross/fee/funding/net USD difference no more than `$0.01` after final aggregate rounding; position quantity difference no more than the venue's declared size quantum. Any larger difference is a failed reconciliation, not a warning.
3. Every report's totals can be traced to ledger event IDs and agree with the canonical ledger query for the same filters.
4. Quant JSON/Markdown artifacts reproduce byte-for-byte after removing explicitly non-deterministic metadata such as run timestamp; their canonical hash is stable.
5. The promotion-gate fixture suite proves all pass and fail boundaries, including exactly 20% passing and greater than 20% failing.
6. Ruff, explicit offline pytest on 3.11/3.12, type scope, clean-wheel import, container OFF-mode health, ledger regressions, and quant regressions are blocking CI gates.
7. CI and deployment dry runs have no credentials, default to OFF, use temporary state, make no real network/order call, and contain no code path capable of placing a real order in that environment.
8. A local-Mac dry run proves versioned install, backup, singleton/health/heartbeat checks, explicit activation boundary, and code-only rollback without reverting state.
9. Monitoring exposes and alerts on the metrics in section 9, with a test that simulates stale heartbeat, duplicate loop, and reconciliation drift.

## 12. Assumptions and non-goals

- “Fable 5” is the downstream verifier/implementer receiving this brief.
- The authoritative runtime is a local Mac operated through `scripts/restart.sh`; Fly/Kubernetes material is informative but not the CD target here.
- Strategy eligibility is backtest-only under the stated gates plus explicit operator approval. Forward shadow performance is reported but not mandatory.
- The 20% backtest drawdown ceiling does not weaken live account kill switches or other emergency controls.
- Existing untracked research, local state, logs, secrets, and runtime processes are not inputs to implementation unless the operator separately authorizes a read-only migration/reconciliation exercise.
- This handoff does not prescribe a particular ledger database or type checker. The selected technologies must satisfy immutability, decimal correctness, atomicity, deterministic export, local-Mac operability, and the acceptance tests above.
- Historical profit is not an acceptance criterion. Accounting reproducibility, conservative simulation, release identity, and inability of CI/CD to trade are.
