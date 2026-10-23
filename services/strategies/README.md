# services/strategies — the desks facility

A desk is one siloed trading focus (oil, btc, top5 crypto, AI equities,
metals, indices) with its own instruments, news queries, policy topics,
catalysts, leverage ladder, token budget and thesis journal. Oil is the
flagship — Julien wants to become a master oil trader — and gets the most
depth: three real energy perps (Brent, WTI, Nat Gas), the Brent-WTI spread,
and the widest news/policy/catalyst coverage. Since 2026-10-19 it is priced
off Brent: Brent is the primary, so the call, ladder, forward stats and
"Your size" are Brent's; WTI is the spread's other leg.

This service owns the `DeskSpec` registry, `DeskAnalytics` (deterministic
math), the thesis journal + grader, and the HTTP/CLI surface. It does NOT
own intel (`services/intel`) or thesis generation (`services/llm` +
`services/thesis`) — those are separate services built in parallel; this
one degrades gracefully when they're missing (see "Pipeline" below).

## The desks

| id | instruments | notes |
|---|---|---|
| `oil` | xyz:BRENTOIL (Brent, primary since 2026-10-19), xyz:CL (WTI, secondary), xyz:NATGAS (hedge) | Brent-WTI pair. Checked heating oil (xyz:HO, $43k/24h) and gasoline/RBOB (doesn't exist on HL) — both excluded, see `desks/oil.py`. |
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
trades.py        Grade every registered account's trades against the thesis
                 that was live when they placed them; TrackRecord enrichment
pipeline.py      refresh(): analytics -> intel -> token gate -> thesis -> journal
                 (on_stage reports "reading market data" ... "writing the thesis")
candle_store.py  daily candles on disk, shared by server, MCP and scheduler;
                 only the missing tail is fetched, a failed fetch serves disk
facade.py        list_cards / get_brief / refresh / submit_user_thesis /
                 track_record / grade_due / grade_due_trades — the stable surface
                 other agents import; also the one background analytics worker
                 (revalidate, warm_all)
api.py           FastAPI routes (contracts/strategies/models.py's route list)
__main__.py      CLI: list / brief / refresh / grade
evals/           verdict_backtest.py — walk-forward liquidation backtest;
                 trades_replay.py — replays a real fill history through trades.py
```

## Grading: what, when, where

**Thesis grading** (`grader.py`, unchanged by this build): every expired
thesis gets a point-in-time `Grade` (direction hit, target/stop-first, Brier,
MAE/MFE) from hourly candles, written back onto the thesis itself
(`journal.save_thesis`). Idempotent — a thesis with `.grade` already set is
skipped.

**Trade grading** (`trades.py`, 2026-10-23): every REGISTERED account (every
mode — trade and watch — plus the synthetic `env` account for `.env.local`
keys; `services.accounts.view.list_all_accounts()`) gets graded against every
thesis that is graded-or-expired, on the call's instrument coin(s) (both legs
for a `pair` call), over the window `[thesis.created_at, min(thesis.expires_at,
the next thesis's created_at)]`. Per account, per thesis: `n_fills`,
`n_opens`, `n_closes`, `realized_pnl_usd` (sum(closedPnl) - sum(fee)),
`follow_rate` (fraction of OPENING fills in the call's own direction — a long
call's correct open is a Buy), `first_entry_px` / `entered_in_zone`, and
`max_adverse_fill` (the single worst closedPnl fill). See `trades.py`'s
module docstring for the exact rules.

**Why a sibling file, not a `Thesis.trades` field.** The build brief offered
either: extend the `Thesis` contract (a `CONTRACT_VERSION` bump, since every
thesis already on disk would need to keep validating) or store trades next to
the thesis. This ships the sibling file:

    $PATHIEL_STATE_DIR/strategies/<desk_id>/trades/<thesis_id>.json   # ThesisTrades

A thesis is written once and frozen; its trades keep arriving for as long as
the window is open and get recomputed independently. Keeping them apart means
`trades.py` never has to import-and-widen the `Thesis` model to do its job —
one more service, one more sharp boundary. `AccountTradeGrade`/`ThesisTrades`
(`contracts/strategies/models.py`) are additive: no existing model's shape
changed, so `CONTRACT_VERSION` did not need to bump either.

**Reading it back.** `trade_grading.track_record_for_desk(desk_id)` (what
`facade.list_cards()`, `facade.get_brief()` and `facade.track_record()` all
call) returns a `TrackRecord` with the thesis-grading fields PLUS
`realized_pnl_usd` / `n_trades` / `follow_rate` flattened across every
account's trade grade for every graded thesis on that desk — `None`/`0` when
nothing has been graded yet, never a stand-in zero for "no data." The
`desk_track_record` MCP tool and `/desks` pages read the same combined
number; nothing reads `grader.py`'s thesis-only roll-up directly any more.

**When it runs.**
- **Automatically**: `pathiel/server.py`'s lifespan starts a background task
  ~60s after boot (`PATHIEL_GRADE_INITIAL_DELAY_S`), then every
  `PATHIEL_GRADE_INTERVAL_MIN` minutes (default 60) — thesis grading, THEN
  trade grading, both via `asyncio.to_thread` so the shared event loop is
  never held for a Hyperliquid read. `PATHIEL_GRADE_LOOP=0` turns it off (the
  gate test session does). This replaces `scripts/scheduler.py`'s
  `desks-grade` job as the live grading path — the scheduler process is not
  running on the operator's machine.
- **On demand**: `python -m services.strategies grade` (CLI) or the
  `desk_grade` MCP tool (optional `desk` — narrows TRADE grading only;
  thesis grading always covers every desk, it is cheap and idempotent).

Analytics freshness (2026-10-18). Rebuilding live on every `get_brief()` ran
through the dashboard server's Hyperliquid budget (60 burst, 2 weight/s) and
took 22s (btc) to 122s (ai, 11 instruments) per page view. Now:

| caller | analytics |
|---|---|
| `refresh()` | always rebuilt; candle tails re-fetched (`candle_max_age_s=0`) |
| `get_brief()` (web) | cache if < 15 min old; else the cache at once plus a queued background rebuild (`brief.refreshing=True`, the page re-reads until it lands) |
| `get_brief(wait=True)` (MCP, CLI) | rebuilt inline when older than `max_age_s` (MCP: 120 s) |
| `list_cards()` | cache only; desks older than an hour are queued for the worker |
| server startup | desks with no cache at all, or a cache built for other primaries, are queued (`start_warmup`) |

One worker drains the queue one desk at a time (a brief someone is looking at
jumps the line), so six stale desks never stampede the budget. The worker runs
at background priority (`pathiel.client.rate_limit.background_priority`): its
requests may only spend the bucket above its reserve, so a page view or a
clicked refresh is never queued behind it (2026-10-19: a BTC rebuild took 213 s
and a clicked BTC refresh sat behind it). A failed
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
3. the desk's primary changed since the prior thesis (`primary_changed`) -> always
4. prior thesis expired -> always
5. `refresh_min_interval_min` not yet elapsed since the prior thesis -> never (rate limit; this check runs before 6/7 and wins over them)
6. the call's instrument moved >= `material_move_pct` since the prior thesis's entry -> yes
7. a headline >= `min_headline_relevance` arrived after the prior thesis -> yes
8. otherwise -> no

Changing a desk's primary (oil: WTI to Brent, 2026-10-19) is safe. Every
written thesis is stamped with the desk's primaries (`Thesis.primary_coins`);
older, unstamped theses count as from the old desk when they are long or short
a coin that is not a primary now. A thesis from the old desk triggers rule 3,
and the rewrite starts from scratch: the old call is not handed to the thesis
service as a base, so no incremental update ever measures Brent's price
against a WTI entry. A cached read whose ladder or forward stats are on a coin
that is not a primary now counts as no cache (`load_cached_analytics` returns
None), so the overview, the brief and the startup warmup rebuild it rather
than show the old primary's numbers. Until the rewrite lands, the brief's
`stale` says which coin the shown call was written for.

Refresh jobs (`POST /api/desks/{id}/refresh`). One per desk at a time: a
second click, or a reloaded page clicking again, gets the running job back
instead of a second market read and a second model call. `GET
/api/desks/{id}` carries it as `refresh_job` (queued or running, else null), so
a page opened mid-refresh shows its progress at the top and follows it to the
end. Every refresh logs `"<desk>: refresh <state> in <s>s: <message>"`. A desk
with no call yet shows "Write the first call" in the call section rather than
a "Stale" banner: missing is not stale. A finished job stays readable at
`GET /api/desks/jobs/{id}` for an hour (a page follows one for at most 10
minutes), then the next refresh click drops it; queued and running jobs are
never dropped.

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
.venv/bin/python -m services.strategies grade          # theses, then every account's trades
.venv/bin/python -m services.strategies.evals.verdict_backtest          # periodic eval, hits live HL
.venv/bin/python -m services.strategies.evals.trades_replay             # offline, two fixtures, no HL
```
