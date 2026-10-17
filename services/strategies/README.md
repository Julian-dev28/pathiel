# services/strategies — the desks facility

A desk is one siloed trading focus (oil, btc, top5 crypto, AI equities,
metals, indices) with its own instruments, news queries, policy topics,
catalysts, leverage ladder, token budget and thesis journal. Oil is the
flagship — Julien wants to become a master oil trader — and gets the most
depth: three real energy perps (WTI, Brent, Nat Gas), the Brent-WTI spread,
and the widest news/policy/catalyst coverage.

This service owns the `DeskSpec` registry, `DeskAnalytics` (deterministic
math), the thesis journal + grader, and the HTTP/CLI surface. It does NOT
own intel (`services/intel`) or thesis generation (`services/llm` +
`services/thesis`) — those are separate services built in parallel; this
one degrades gracefully when they're missing (see "Pipeline" below).

## The desks

| id | instruments | notes |
|---|---|---|
| `oil` | xyz:CL (WTI, primary), xyz:BRENTOIL (Brent, secondary), xyz:NATGAS (hedge) | Brent-WTI pair. Checked heating oil (xyz:HO, $77k/24h) and gasoline/RBOB (doesn't exist on HL) — both excluded, see `desks/oil.py`. |
| `btc` | BTC | Single largest, most liquid perp on the main dex. |
| `top5` | BTC, ETH, SOL, HYPE, ZEC (all primary) | The 5 largest native crypto perps by 24h volume, verified 2026-10-17. Drifts — see "Adding/updating a desk" below. |
| `ai` | xyz:NVDA, MU, AMD, GOOGL, META, PLTR, TSM (primary); MSFT, AVGO, ORCL, ARM (secondary) | HIP-3 AI-chip/AI-infra equities, >$1M 24h volume. |
| `metals` | xyz:GOLD (primary), xyz:SILVER (secondary) | No PairSpec — see `desks/metals.py` for why a gold/silver spread doesn't fit the contract's difference-based PairSnapshot. |
| `indices` | xyz:XYZ100, xyz:SP500 (primary); xyz:JP225, xyz:KR200 (secondary) | IBOV/NIFTY exist on HL but read $0 volume — excluded. |

Every instrument was checked against Hyperliquid's live `/info`
(`metaAndAssetCtxs`, main dex + every HIP-3 dex) on 2026-10-17 — see each
desk module's docstring for the exact volumes and `desks/_verify.py` for the
tool that did the checking.

## Architecture

```
desks/*.py       DeskSpec registry (config, offline, pure)
analytics.py     DeskAnalytics (deterministic: snapshots, pairs, forward
                 stats, leverage ladder) — the ONLY numbers a Thesis may quote
journal.py       Thesis storage on disk, atomic writes
grader.py        Grade expired theses from hourly candles; TrackRecord roll-up
pipeline.py      refresh(): analytics -> intel -> token gate -> thesis -> journal
facade.py        list_cards / get_brief / refresh / submit_user_thesis /
                 track_record / grade_due — the stable surface other agents import
api.py           FastAPI routes (contracts/strategies/models.py's route list)
__main__.py      CLI: list / brief / refresh / grade
evals/           verdict_backtest.py — walk-forward backtest, runnable as a script
```

Analytics is always rebuilt fresh on `refresh()` and `get_brief()` (live HL
data, parallel fetches, <10s even for an 11-instrument desk); `list_cards()`
reads only whatever the last `refresh()` cached, so an overview page never
triggers a live fetch per desk.

## The leverage ladder and the verdict rule

Each primary instrument gets one `LadderRung` per `(leverage, side)` in
`spec.leverage_ladder`, filtered to leverages the venue actually allows
(`leverage <= max_leverage`). Each rung runs ONE path-dependent simulation
over every historical horizon-day window (daily bars, intraday low/high as
the liquidation/stop proxy), with the suggested stop (60% of the liquidation
distance) ACTIVE throughout — see the long comment on
`analytics.simulate_ladder_rung` for why this deliberately differs from the
reference scratch scripts' numbers (they spliced a no-stop run's P(liq) onto
a with-stop run's P(stop), which don't describe the same probability space).
Here, `p_liq + p_stop + P(rode to the horizon close) == 1.0` exactly.

Verdict rule (`analytics.verdict_for_rung`), on empirical P(liquidated
within the horizon) alone:

| p_liq | verdict | meaning |
|---|---|---|
| < 10% | `hold` | survives the full horizon >90% of the time, historically |
| 10–35% | `dip_only` | size down / add on dips, don't carry the full horizon casually |
| 35–55% | `intraday_only` | only defensible as a same-day, actively managed trade |
| >= 55% | `avoid` | coin-flip or worse on liquidation within the horizon |

Calibrated against the oil fixture (5x -> hold, 10x -> dip_only, 20x ->
avoid). The walk-forward eval (`evals/verdict_backtest.py`) found this rule
well-calibrated for BTC (large sample: 0-11% actual liquidation rate on
"hold" days vs the <10% promise) but UNRELIABLE for oil at 10x specifically
(57.9% long / 100% short actual liquidation rate on the handful of days
classified "hold" — xyz:CL's ~285-day history gives too small/noisy a
trailing sample at that leverage). See the eval's output and this service's
final delivery report for the two follow-up options; not fixed here pending
Julien's call on which approach he wants.

## Pipeline: the token gate

`pipeline.refresh(desk_id, force=False)` always rebuilds analytics, then
tries to load intel and generate a new thesis — but only actually CALLS the
(billable, slow) thesis provider when `needs_rewrite()` says to:

1. `force=True` -> always
2. no prior machine thesis -> always
3. prior thesis expired -> always
4. `refresh_min_interval_min` not yet elapsed since the prior thesis -> never (rate limit; this check runs before 5/6 and wins over them)
5. primary instrument moved >= `material_move_pct` since the prior thesis's entry -> yes
6. a headline >= `min_headline_relevance` arrived after the prior thesis -> yes
7. otherwise -> no

Intel and thesis are resolved by import path (`services.intel.api:build_packet`,
`services.thesis.api:write_thesis`), not a hard dependency — missing or
failing, the refresh degrades (`RefreshJob.state == "done"`, message says
what was skipped) rather than crashing. Every refresh appends one row to
`$PATHIEL_STATE_DIR/strategies/usage.jsonl` (desk, decision, reason, tokens,
duration) regardless of outcome, so cost per thesis is visible.

## Adding a desk

1. `services/strategies/desks/<id>.py` with a `build_spec() -> DeskSpec`.
   Copy `oil.py` — it has the most comments on *why*.
2. Verify every instrument with `desks/_verify.py` (reads Hyperliquid's live
   universe; read-only, no account calls) and record the checked volumes in
   the module's docstring, same as every other desk here.
3. Add it to `_BUILDERS` in `desks/__init__.py`.
4. `python -m services.strategies list` and `pytest services/strategies` pick
   it up automatically (test_desks.py iterates the registry, nothing is
   hardcoded to the 6 existing ids).

## Running it

```
ln -s /Users/julian_dev/Documents/code/pathiel/.venv .venv   # once, at the repo/worktree root
export PATHIEL_STATE_DIR="$(mktemp -d)"                      # never the live one

.venv/bin/python -m pytest -q services/strategies                       # gate tests, offline, <1s
.venv/bin/python -m pytest -q -m online services/strategies             # real HL reads
.venv/bin/python -m services.strategies list
.venv/bin/python -m services.strategies brief oil
.venv/bin/python -m services.strategies refresh oil --force
.venv/bin/python -m services.strategies refresh --all      # every desk, one call — the scheduler hook
.venv/bin/python -m services.strategies grade
.venv/bin/python -m services.strategies.evals.verdict_backtest          # periodic eval, hits live HL
```
