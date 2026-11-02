# services/review: the learning loop

Operator, 2026-11-02: "i need you to continuously evolve, learn from what
we did right, fix what we did wrong, see if mistakes and successes are just
flukes or a predictions".

Every closed desk trade goes into a ledger with what was true when it was
opened. The review slices the ledger, asks of each slice "edge, leak, or
luck?", and turns the leaks into backtest proposals for levers the
simulator already has. It never changes live behaviour by itself.

**Outcome it moves:** the operator sees, after every close and once a day,
which patterns are real enough to test and which are one trade, with a
number for how many trades a real edge needs before it can show.
Trace: `docs/strategy/learning/REVIEW_<UTC date>.md`, the summary line in
`/tmp/oil-loop/progress.log` when something is new, and the ledger.

## Why it does not feed the thesis prompt

SCORECARD_TEST_2026-10-31.md: putting the desk's own graded record into the
thesis prompt made money worse ($89 vs $139). So findings change behaviour
only one way: a proposal is backtested in
`services/strategies/backtest/desk_tick_sim.py` (via the eval it names),
adopted only if it beats live on most datasets as a plateau, and then only
on the operator's go.

## Run

```
python3 -m services.review run                    # live: public fills, read only
python3 -m services.review run --since 2026-10-31
python3 -m services.review run --offline services/review/tests/fixtures/bundle_2026-11-02
python3 -m services.review run --save-bundle /tmp/review/bundle   # keep this run's inputs
```

Prints one line, for the tick window:

```
REVIEW 2026-11-02T09:40Z trades=10 net=+31.49 meanR=+0.99R edge=0 leak=0 lean+=2 lean-=2 fluke=0 proposals=2 new_proposals=2 new_leaks=none report=docs/strategy/learning/REVIEW_2026-11-02.md
```

Cadence: `.claude/skills/desk-loops` schedules `/review-trades` daily at
00:07 UTC and after any close; `.claude/skills/review-trades` runs the CLI.

## Pieces

| File | Does |
|---|---|
| `inputs.py` | Fetch (public `/info` via `loss_audit.load_inputs`, candles via `backtest.data`) or load a saved bundle |
| `ledger.py` | Trades from fills by the loss audit's own code, joined with the thesis and rule context, append-only JSONL |
| `luck.py` | 500 random-timing entries per trade, same day, side, stop, target, hold |
| `stats.py` | Wilson, bootstrap, permutation, sign flip, binomial, labels. Stdlib, seeded |
| `analysis.py` | Slices, the table, thesis calls vs a coin flip |
| `proposals.py` | Slice to sim lever to eval command; keep notes; prior verdicts |
| `report.py` | Markdown and the summary line |
| `review.py` | One run, no network |

## Ledger

`$PATHIEL_STATE_DIR/review/ledger.jsonl` (resolved like services/intel:
the env var, then `.env.local`, then the repo root). One JSON object per
line, keyed by `id` = `coin|entry ms|first order id`.

- New trade id: appended.
- Inside the settle window (12h after the exit, because the audit's
  wrong-direction and stop-too-tight tags look 12h ahead): appended again
  only if the record changed. Last line wins.
- Settled (`settled: true`): frozen. The venue's equity samples roll
  forward, so a rebuild weeks later could drift; the first settled record
  is the record. Bumping `SCHEMA` rebuilds settled records once.
- Same inputs twice: nothing appended.

Fields: the audit's (entry, exit, stop, target, size, net, fees, funding,
tags, MFE R, equity, leverage, % equity a stop costs) plus `r_mult` (net $
over the $ the opening stop risked at full size), the thesis in force
(`thesis_id`, side, entry, stop, `thesis_age_min`, `rewrite_reason`:
first / flip / same_side / after_flat, from the journal order since the
thesis file carries no reason), `weekend` (the sim's `weekend_closed`),
`hour_utc`, `range_pos` (side-adjusted place in the 24h range: 0 = the
side's own edge, under 0 = entered past it), `planned_r`, and the luck
fields. Thesis grades come later than the trade, so `thesis_hit_7d` and
`thesis_hit_24h` are joined at review time, not stored.

## Labels

Per slice: n, win rate (Wilson 95%), mean R and net $ (seeded bootstrap
95%, 2,000 resamples), p (seeded permutation of mean R against every other
trade, 2,000 shuffles; the "all" row is a sign-flip test against 0).

| Label | Rule |
|---|---|
| EDGE | n >= 10, mean R CI above 0, p < 0.05 |
| LEAK | n >= 10, mean R CI below 0, p < 0.05 |
| LEANING+ / LEANING- | n >= 3 and p < 0.2, or 3 <= n < 10 all one sign |
| FLUKE | would be one of the above, but dropping the biggest trade flips the sign |
| NOISE | everything else, and anything under 3 trades |

Nothing under n = 10 is ever EDGE or LEAK. Dimensions: desk, side, cause
tag, weekend, minutes since the rewrite (0-15 / 15-60 / 60+), thesis hit
(7d grader, 24h price), with or against the thesis, planned R, UTC hour
block, 24h range position, % equity at risk.

The report also prints how many trades a real +0.4R edge needs at the
ledger's own spread of R (normal approximation, 5% test, 80% power). On
2026-11-02 that was about 418: read every LEANING as a lead.

## Proposals

`proposals.LEVERS` maps a negative slice to the `Params` fields that act
on it and the eval that sweeps them (`loss_fix_test`,
`brent_stop_buffer_test`, `silver_stop_floor_test`, `strategy_sweep`), with
the last verdict on that lever (`PRIOR`). A positive slice lists the levers
that would cut it; a proposal on a protected lever says so. FLUKE and
NOISE emit nothing.

## Tests and evals

- Gate (`services/review/tests`, under 2s, offline): stats on known values
  (Wilson, exact binomial, bootstrap, permutation floor, sign flip),
  labels at the boundaries (n=9 never EDGE, n=10 EDGE/LEAK, FLUKE, n<3
  NOISE), ledger idempotency / revise / freeze / schema / torn line, the
  luck walk, proposals mapping to real `Params` fields, the CLI end to end
  on the saved 2026-11-02 bundle, the skill wiring. Regression: the loss
  audit's 11 trades (fixtures/audit_trades_2026-11-02.csv): the 11-02 Brent
  long tags wrong_direction + size, and its n=1 slice is NOISE, not LEAK;
  the bundle rebuilds the same 10 closed trades, nets and tags.
- Periodic eval: `python3 -m services.review.evals.fluke_eval [--power]`.
  200 seeded synthetic streams per scenario. Gates: a planted +0.4R edge
  (n=40, sd 0.75R) found >= 80%, its mirror leak >= 80%, noise called
  EDGE/LEAK <= 5% (normal and bracket-shaped), n=9 never EDGE. Last run:
  88%, 82%, 0.5%, 2.0%, 0%. The power table shows what a ~3R spread
  costs: +0.4R at n=320 is found 42% of the time.

## Failure modes

- Fewer than 5000 5m bars back: trades older than about 17 days get no
  excursion or luck on a fresh ledger. Settled records keep theirs.
- The equity behind `% equity` comes from the venue's portfolio samples;
  frozen at settle for that reason.
- Thesis 24h hits overlap in time (calls hours apart), so their binomial p
  is optimistic. The report says so.
- A network failure stops the run before the ledger is touched.
