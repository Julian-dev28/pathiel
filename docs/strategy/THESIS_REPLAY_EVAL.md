# Thesis replay eval (2026-11-22)

"Own Your Intelligence" for trading: every thesis a live desk has written
is a complete eval case on disk -- the inputs (`analytics_ref`,
`intel_ref`), the model's answer (`call`, `bull`, `bear`, `scenarios`,
`what_changes_my_mind`), and (once resolved) a `grade`. This harness
replays the SAME saved inputs through model A vs model B, grades both
answers the same deterministic way, and prints a leaderboard. It doubles
as a regression gate for the live thesis prompt.

Code: `services/strategies/evals/thesis_replay.py` (CLI + orchestration),
`thesis_replay_scoring.py` (pure scores, no network), `thesis_replay_models.py`
(model adapters). Tests: `services/strategies/tests/test_thesis_replay*.py`.

## How to run

```
export PATHIEL_STATE_DIR="$HOME/Documents/code/pathia/.state"   # read-only archive

# Free: grade the ORIGINAL stored calls for every thesis on a desk (or "all").
# Also attempts prompt reconstruction per case (to count unreplayable), but
# makes no LLM call.
.venv/bin/python -m services.strategies.evals.thesis_replay --desk all --dry-run

# Real replay: reconstruct the prompt for the 5 most recent oil theses and
# ask sonnet for a fresh call. One LLM call per (thesis, model) -- no
# repair round, so this is at most 5 calls here.
.venv/bin/python -m services.strategies.evals.thesis_replay \
    --desk oil --models sonnet --limit 5

# Multiple models, exported as fine-tuning records (input prompt + answer + scores):
.venv/bin/python -m services.strategies.evals.thesis_replay \
    --desk oil --models opus,sonnet --limit 20 --export-jsonl /tmp/thesis-ft-records.jsonl

# Maintenance only, off by default, never scheduled: delete snapshot files
# older than N days, across every desk, then exit.
.venv/bin/python -m services.strategies.evals.thesis_replay --prune-snapshots-older-than 90
```

Every run writes `/tmp/thesis-replay/<run>/results.jsonl` (one row per
thesis x model) and prints a leaderboard to stdout. `--run-name` pins the
run directory name; default is a UTC timestamp.

`--models` is a comma-separated list of either a Claude Code CLI alias
(`opus`, `sonnet`, `haiku`, `fable` -- the only models this eval's CLI
actually drives, per CLAUDE.md "LLM access": every call goes through
`services.llm.complete`, the project's local-Claude-Code gateway, never a
hosted API) or `command:<local CLI that reads a prompt on stdin and prints
a JSON answer>` (e.g. `command:ollama run llama3`), for plugging in an
open-weight model later. A `command:` entry must be the last item in
`--models` since the comma is also the CLI's own list separator.

## What the two scores mean

Profit and loss over ~140 historical calls is too noisy to compare model A
vs model B on, so every call is graded on two sharper, deterministic
scores from Hyperliquid public candles after the thesis's `created_at` --
no LLM, no P&L, no randomness:

- **direction_24h** -- was the call's side right 24h later (the close vs a
  reference price at `created_at`)? `hit` / `miss` / `void` (no side --
  flat or pair calls are not scored this way).
- **target_before_stop** -- from the call's entry (or the reference price,
  if the entry limit never fills), did the first target trade before the
  stop within the thesis's own horizon? `hit` / `stop` / `neither` (the
  horizon elapsed with neither touched) / `void`.

Both reuse `services.strategies.grader`'s own bar primitives (`Bar`,
`_clip`, `_direction_sign`, `_first_fill`) -- same 1h-bar semantics, same
"a bar that touches both the stop and the target the same hour resolves as
the stop" conservatism the live grader already uses. `void` also covers
missing candle coverage: the fetcher asked for 1h bars (never 1m/5m -- that
window rolls from *now*, so an old thesis would silently get a truncated,
wrong window), and `has_coverage` checks the fetched window's first bar
actually opens within 2 hours of `created_at` before trusting it.

Each rate is reported with a 95% Wilson interval
(`services.review.stats.wilson_ci`, reused unchanged) -- the same
small-n-honest interval used elsewhere in this repo for a win rate.

## Follow-up (2026-11-22): every NEW thesis is replayable

The limitation below is now fixed going forward. `services.strategies.
pipeline.save_replay_snapshots` is called from `refresh()` right before
`journal.save_thesis(thesis)` -- the one place every production writer
(the `refresh_desk`/`desk_refresh` MCP tools, a forced desk-tick rewrite)
goes through. It persists the EXACT `DeskAnalytics`/`IntelPacket`
payloads the thesis's own `analytics_ref`/`intel_ref` point at, content-
addressed by the same sha256 the ref already carries, to
`$PATHIEL_STATE_DIR/strategies/<desk_id>/snapshots/<sha256>.json`
(`journal.save_snapshot`/`load_snapshot`/`prune_snapshots`).

- **Write-once**: an existing file at that hash is left alone (same hash
  implies same content -- nothing to gain from rewriting it, and most
  refreshes that don't materially change the analytics will hit the same
  hash as a recent prior thesis).
- **Never blocks the thesis save**: a snapshot write failure is logged
  and swallowed inside `journal.save_snapshot` itself, with a second
  try/except around the whole call in `pipeline.save_replay_snapshots`
  for good measure -- the thesis record is what matters; the snapshot is
  a best-effort extra.
- **Hash-verified on read**: `resolve_replay_inputs` checks the snapshot
  FIRST, but only trusts it if re-hashing the parsed object reproduces
  the exact sha256 named in the ref (`_load_verified_snapshot`). A
  missing, corrupt, or (somehow) mismatched snapshot falls back to
  today's live-"latest"-cache check, exactly as if the snapshot feature
  did not exist -- so an old thesis written before 2026-11-22 is still
  correctly marked `unreplayable`, never silently served the wrong data.

**Typical payload size** (measured on the live archive): analytics
~5-8KB, intel ~5-150KB (headline-heavy desks like oil/hype run toward the
top of that range). A desk whose analytics/intel genuinely changes every
refresh adds roughly that much new snapshot data per thesis; a desk
ticking without a material change reuses an existing hash and adds
nothing. At ~100 new theses this is a few MB at most, not a storage
concern -- but `--prune-snapshots-older-than DAYS` (default: not run,
nothing schedules it) is there for an operator who wants to reclaim the
space for very old snapshots by hand:

```
.venv/bin/python -m services.strategies.evals.thesis_replay --prune-snapshots-older-than 90
```

**The sample-size caveat gets sharper, not weaker, from here**: the 142
archived theses this harness grades today remain permanently
unreplayable (their refs point at payloads that were already gone before
this fix existed) -- they stay the free, deterministic `--dry-run`
baseline forever. The A/B comparison (model A vs model B on the SAME
saved inputs) only has real cases to run on once new theses accumulate
after 2026-11-22; with 0 replayable cases on day one, expect the
leaderboard's `unreplayable` count to fall roughly 1-for-1 with new
theses written from here. Treat any model-vs-model read as noise until
there are enough of them -- rule of thumb, **~100 new theses** before a
Wilson interval on either score is narrow enough to trust, the same
small-n caveat as the rest of this doc, just starting from a smaller base.

## Replay, and why most history is unreplayable

This section describes the state as first built, and still describes every
one of the 142 archived theses permanently (their refs point at payloads
that were already gone before snapshotting existed) -- the follow-up
above is what changes it for every thesis written from 2026-11-22 on.

Reconstructing "the exact prompt the thesis writer used" means resolving
`thesis.analytics_ref` / `intel_ref` (an `as_of` + a sha256,
`contracts.strategies.models.PayloadRef`) back to the actual
`DeskAnalytics` / `IntelPacket` objects the pipeline fed the model that
day. But `services.strategies.pipeline` only ever caches ONE analytics
snapshot and ONE intel snapshot per desk --
`$PATHIEL_STATE_DIR/strategies/<desk>/{analytics,intel}/latest.json` --
overwritten on every refresh tick (every 15 minutes, whether or not a new
thesis was written that tick). By the time this harness runs, the cache
has moved on past every historical thesis's ref, including each desk's own
most recent one.

`resolve_replay_inputs` proves this per case: it re-hashes the live cache
with the same `services.thesis.hashing.hash_payload` the pipeline used to
stamp the ref, and compares. A mismatch (or a missing cache) marks the
case `unreplayable`, with a reason, and it is counted -- never fed a
reconstructed-from-candles or otherwise invented substitute. The brief is
explicit about this: inventing inputs is worse than admitting the data is
gone. **In practice, on the real archived state
(`$HOME/Documents/code/pathia/.state`), this is expected to mark every
historical case unreplayable** -- a real, verified property of how the
live pipeline caches its inputs (a single mutable "latest" snapshot, not a
history), not a bug in this harness.

This is orthogonal to the two deterministic scores above: grading the
ORIGINAL stored call needs only the call itself (fully persisted forever
in the thesis's own journal file, `strategies/<desk>/theses/<id>.json`)
plus public candles after `created_at` -- never the analytics/intel
snapshot that produced it. So `--dry-run` still scores nearly every case
even when every case is unreplayable for model-vs-model replay.

## Hard rule: no trade record in the prompt

Per `docs/strategy/SCORECARD_TEST_2026-10-31.md`, a replayed prompt NEVER
carries the desk's own trade record or past grades.
`thesis_replay.build_replay_prompt` always calls
`services.thesis.prompt.build_user_prompt` with `prev=None` and
`scorecard_text=None`, hardcoded, not threaded from any CLI flag or
caller -- there is no argument that could leak either one in. A gate test
(`test_build_replay_prompt_never_passes_prev_or_scorecard`) proves this by
spying on the real `build_user_prompt` call.

A validator miss is NOT retried with a repair round here (unlike the live
`services.thesis.api.write_thesis`, which spends a second call to repair a
rejected draft): this eval's budget is a fixed number of calls
(`--limit` theses x `--models` aliases), and a silent 2x-cost repair on
every miss would blow that budget without the caller asking for it. A
failed parse or validation is recorded as a `void` case
(`model_call_failed`), not retried.

## Sample-size caveat

~140 historical calls split across 9 desk/instrument pairs, most of it one
desk (oil, 108 of ~142). A leaderboard row's Wilson interval is reported
precisely because small-n hit rates are not reliable on their own --
read the interval width, not just the point estimate, before concluding
model A beats model B. `target_before_stop`'s `neither` outcome (the
7-day horizon elapsed with nothing touched) is common for calls written in
the last week of the window relative to "now"; it is counted in the rate's
denominator (it is a real, known outcome) but is not a `hit`.

## Output rows (`results.jsonl`)

One JSON object per (thesis, model), including the baseline
`model: "original"` row for every considered thesis:

```json
{
  "desk_id": "oil", "thesis_id": "...", "model": "original" | "sonnet" | ...,
  "created_at": "...", "expires_at": "...",
  "unreplayable": true, "unreplayable_reason": "analytics_ref no longer matches ...",
  "call": { ... } | null,
  "parse_ok": true | false | null,
  "validator_errors": [],
  "direction_24h": {"outcome": "hit", "ref_px": ..., "px_after": ...},
  "target_before_stop": {"outcome": "stop", "entry_used": ..., "resolved_at": "..."},
  "usage": {"model": "...", "input_tokens": ..., "cost_usd": ...} | null
}
```

`--export-jsonl PATH` additionally writes one record per successfully
replayed (parsed + validated) case: `{"desk_id", "thesis_id", "model",
"input": {"system", "user"}, "answer": <ThesisDraft JSON>, "scores": {...}}`
-- the "own your training data" shape, ready to use as a fine-tuning
example. Only cases that actually ran a model and parsed cleanly appear
here; unreplayable and invalid-draft cases do not (there is no answer to
export).
