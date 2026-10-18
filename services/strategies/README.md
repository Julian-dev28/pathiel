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
                 (on_stage reports "reading market data" ... "writing the thesis")
candle_store.py  daily candles on disk, shared by server, MCP and scheduler;
                 only the missing tail is fetched, a failed fetch serves disk
facade.py        list_cards / get_brief / refresh / submit_user_thesis /
                 track_record / grade_due — the stable surface other agents import;
                 also the one background analytics worker (revalidate, warm_all)
api.py           FastAPI routes (contracts/strategies/models.py's route list)
__main__.py      CLI: list / brief / refresh / grade
evals/           verdict_backtest.py — walk-forward backtest, runnable as a script
```

Analytics freshness (2026-10-18). Rebuilding live on every `get_brief()` ran
through the dashboard server's Hyperliquid budget (60 burst, 2 weight/s) and
took 22s (btc) to 122s (ai, 11 instruments) per page view. Now:

| caller | analytics |
|---|---|
| `refresh()` | always rebuilt; candle tails re-fetched (`candle_max_age_s=0`) |
| `get_brief()` (web) | cache if < 15 min old; else the cache at once plus a queued background rebuild (`brief.refreshing=True`, the page re-reads until it lands) |
| `get_brief(wait=True)` (MCP, CLI) | rebuilt inline when older than `max_age_s` (MCP: 120 s) |
| `list_cards()` | cache only; desks older than an hour are queued for the worker |
| server startup | desks with no cache at all are queued (`start_warmup`) |

One worker drains the queue one desk at a time (a brief someone is looking at
jumps the line), so six stale desks never stampede the budget. A failed
rebuild serves the old copy with a note in `stale`; no copy at all gives
`analytics=None` with a note, never a 500. Knobs: `PATHIEL_DESK_BRIEF_MAX_AGE_S`,
`PATHIEL_DESK_CARD_MAX_AGE_S`, `PATHIEL_DESK_CANDLE_MAX_AGE_S`,
`PATHIEL_DESK_ACCOUNT_TTL_S`; `PATHIEL_DESK_BACKGROUND_REFRESH=0` and
`PATHIEL_DESK_WARMUP=0` switch the worker and the warmup off (the test session
does both).

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

Verdict rule (`analytics.verdict_for_rung`). Two probabilities, each mapped
to the same buckets, and the verdict is the WORSE of the two:

| probability | verdict | meaning |
|---|---|---|
| < 10% | `hold` | survives the full horizon >90% of the time |
| 10-35% | `dip_only` | size down / add on dips, don't carry the full horizon casually |
| 35-55% | `intraday_only` | only as a same-day, actively managed trade |
| >= 55% | `avoid` | coin-flip or worse on liquidation within the horizon |

1. `p_liq`: empirical, from the ladder simulation over every historical
   horizon window.
2. `p_touch`: parametric, the chance price touches the liquidation level
   within the horizon at today's volatility: driftless Brownian touch
   probability with sigma = max(full-sample, 20-day) daily vol x 1.25.

Fewer than 120 forward windows drops the verdict one more level.

Why two signals (2026-10-17): the walk-forward eval (`evals/verdict_backtest.py`)
showed the p_liq-only rule calling rungs "hold" right before volatility
expansions liquidated them. 14 of 72 desk/side/leverage rows put more than
15% of their "hold" days into liquidation (oil 10x long 57.9%, SOL 10x short
42.2%, XYZ100 20x 21.5%). A grid over the volatility choice and tail
multiplier found exactly one setting with zero failing rows:

| | p_liq only | two signals |
|---|---|---|
| failing rows (hold-day liquidation > 15%) | 14 | 0 |
| worst hold-bucket liquidation rate | 100% | 4.8% |
| hold-days across all rows | 13,204 | 4,036 |

The cost is fewer "hold" calls. Oil 5x is `dip_only` while the sample
includes the March/April war shocks. That is deliberate: liquidations were
92% of this account's 60-day loss (docs/overhaul/PROFIT_AUDIT.md).


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
