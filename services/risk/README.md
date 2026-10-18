# services/risk

Risk sizing that scales from a $10 account to a $100k one. Five plain
choices (`RiskProfile`); everything else — every book's notional, how many
positions can be open at once, the daily loss limit, whether `$15.19` can
place a trade at all — is DERIVED from those five choices and current
equity by one pure function, `derive_limits`. No LLM anywhere in this
service: every number comes from a deterministic formula you can read in
`derive.py` and re-run yourself.

## The problem this replaces

`docs/overhaul/PROFIT_AUDIT.md` section 1: the live account has $15.19 of
equity and an operator-set `min_tradable_equity_usd: 26.0` in
`.agent-config.json`. `pathiel.agents.executor.min_tradable_equity` returns
$26 against that config; `maybe_execute` checked `equity < min_tradable_equity`
on every single entry attempt and refused all of them, unconditionally,
whatever book or coin was asking. The account could not place one trade.

That one global number was standing in for two different questions —
"is this account funded at all" and "can THIS book afford THIS trade
right now" — and conflating them meant a single hand-tuned override could
block a book that would have fit easily. The fix separates them:
`BookFeasibility` answers the second question per book, per trade, and
`maybe_execute` no longer asks the first one at all (see "Behavior deltas"
below).

## The five choices (`RiskProfile`, `contracts/risk/models.py`)

| field | meaning | default |
|---|---|---|
| `preset` | `"auto"` picks a tier from equity; or name one explicitly, or `"custom"` | `"auto"` |
| `risk_per_trade_pct` | % of equity you're willing to lose if a trade's stop hits | from preset |
| `daily_loss_limit_pct` | % of equity lost today before trading pauses | from preset |
| `max_leverage` | the leverage ceiling, across every book | from preset |
| `max_open_positions` | how many positions at once; `None` = derive it from equity/margin/daily-loss | derived |
| `allow_min_order_bump` | let a too-small order round UP to Hyperliquid's $10 minimum | from preset |
| `max_bumped_risk_pct` | the risk% a bump is allowed to cost, at most | from preset |

Set `preset` and stop there — every other field fills in from the table
below. Override any ONE field and it sticks; everything else still comes
from the preset ("any explicit field overrides the preset's value").

## The four presets (`presets.py`)

| preset | equity band | risk/trade | daily loss | max leverage | bump allowed | bump risk cap | positions |
|---|---|---:|---:|---:|---|---:|---:|
| `starter` | $0 – $1,000 | 3.0% | 8.0% | 10x | yes | **15.0%** | 1 |
| `small` | $1,000 – $10,000 | 2.0% | 6.0% | 8x | yes | 4.0% | up to 3 |
| `standard` | $10,000 – $50,000 | 1.5% | 5.0% | 6x | no | — | up to 6 |
| `pro` | $50,000+ | 1.0% | 4.0% | 5x | no | — | up to 10 |

Every number moves in ONE direction as equity grows: risk% down, daily-loss%
down, leverage down, position count up. `services/risk/tests/test_derive.py`'s
property tests enforce this isn't just a table that LOOKS monotonic — it
holds for the actual derived numbers at every equity in
`[5, 8, 10, 12, 15, 20, 25, 50, 100, 250, 500, 1k, 5k, 10k, 100k]`, for
every preset including `auto`'s mid-sweep preset switches.

**Why `starter`'s bump cap is 15%, not something smaller.** Hyperliquid's
real minimum order is $10.50 (`pathiel.client.exchange.MIN_ORDER_USD`), and
`book_params.py`'s own fallback stop width when a book sets no override is
15% (`FLOOR_STOP_PCT`). Solving `needs_equity_usd` for that worst-realistic
case against a 15% bump cap gives `$10.50 × 0.15 × 100 / 15 = $10.50` — a
brand-new, zero-configuration account can place its first trade the
instant it clears the EXCHANGE's own minimum, no migration and no manual
tuning required. A tighter-stop book (6%, what the currently-live
`news_surge_short`/`news_surge_multi` actually use) stays far under this
ceiling in practice.

## How a book's size is derived (`derive.py`)

Two shapes:

- **Risk-scaled** (most books — `stop_pct` set): `notional = risk_budget_usd
  / stop_distance`, then capped by (1) the profile's `max_leverage`, (2) the
  book's own per-coin leverage cap, and (3) a **margin share** — equity's
  usable margin split across `max_open_positions`, so one book can't be
  sized as if it owned the whole account when others might be open too.
  If the result is under Hyperliquid's minimum, it's bumped UP to exactly
  that minimum only when the profile allows it AND the resulting risk% is
  at or under `max_bumped_risk_pct` — otherwise the book reports exactly
  how much more equity it needs (`needs_equity_usd`) and by how many
  dollars (`reason`).
- **Fixed / structural** (`drawdown_ladder` — five resting rungs at 1x, no
  stop at all): either equity clears its hard floor ($525 — re-derived from
  `drawdown_ladder_live.py`'s own rung formula, `MIN_ORDER_USD × RUNGS ×
  len(UNIVERSE) / (LEVERAGE × DEFAULT_SLEEVE_FRAC)`, and pinned against it
  by a test so the two numbers can't drift apart) or it doesn't, no risk
  math applies.

`copy_trade` is neither shape — it mirrors a leader's own position sizing,
not a stop distance — so it reports "no sizing rule configured" rather than
guessing. It is also `enabled: false` in the live config today.

`max_open_positions`, when not set explicitly, is the smallest of: how many
$-minimum positions equity's usable margin could cover at the leverage cap,
how many position-stop-outs on the SAME day the daily loss limit survives,
and the preset's own simplicity ceiling.

## Worked examples

Generated by actually running `derive_limits` against the DEFAULT book
roster (`pathiel.agents.config_store.DEFAULT_CONFIG`) — not hand-typed.

### $15 (starter)

> You can trade 1 position at a time, risking about $0.45 per trade.

| book | can trade | reason |
|---|---|---|
| news_surge_short | yes | Sized up to Hyperliquid's $10.50 minimum: risks $1.57 (10.5%) if the stop hits. |
| xs_reversal | yes | Sized up to Hyperliquid's $10.50 minimum: risks $1.57 (10.5%) if the stop hits. |
| drawdown_ladder | no | Book is disabled. |

Every DEFAULT_CONFIG book (15% stop, 1x leverage) bumps to the $10.50
minimum and risks 10.5% — under starter's 15% bump ceiling. This is the
exact shape of the $15.19 account's fix: previously `below_min_tradable_equity`
against a $26 floor, unconditionally.

### $500 (starter — still under the $1,000 band)

> You can trade 1 position at a time, risking about $15.00 per trade.

| book | can trade | reason |
|---|---|---|
| news_surge_short | yes | Fits: $100.00 notional at 1.0x, risks $15.00 (3.0%) if the stop hits. |

No bump needed — the organic risk-sized notional ($100) already clears the
exchange minimum comfortably, at the un-bumped 3% target.

### $10,000 (standard)

> You can trade 3 positions at a time, risking about $150.00 per trade.

| book | can trade | reason |
|---|---|---|
| xs_reversal | yes | Fits: $1,000.00 notional at 1.0x, risks $150.00 (1.5%) if the stop hits. |

Three concurrent positions now, 1.5% risk each, no bump mechanics involved
at all — `standard`'s `allow_min_order_bump` is off because at this equity
the $10.50 floor is never binding.

Reproduce any of these yourself, read-only, against your OWN config:

    .venv/bin/python scripts/config_preset.py derive --equity 15.19
    .venv/bin/python scripts/config_preset.py derive --equity 500 --profile starter --json

## Config v2 storage and migration

`RiskProfile` lives under the `"risk_profile"` key in `.agent-config.json`.
Precedence, enforced by `pathiel.agents.config_store.read_risk_profile`:

1. a valid `"risk_profile"` key — used as-is.
2. `services.risk.migrate.migrate_legacy(config)` — a best-effort profile
   built from the old scattered keys (`equity_fraction_per_trade`,
   `leverage`, `max_concurrent`, `max_daily_loss_pct`/`_usd`,
   `min_order_bump_max_mult`, the per-book `stop_pct` overrides), with a
   plain-English report explaining every mapping it made. Runs only when
   (1) is absent or fails to validate.
3. `RiskProfile()` (`preset="auto"`) — migration's own fallback when the
   config carries none of the old keys either.

`"risk_profile"` is deliberately **absent** from `DEFAULT_CONFIG` itself —
if it were a template value there, `merge_agent_config` would inject a
syntactically-valid v2 profile into every config read through
`read_agent_config()`, permanently defeating migration for every real,
pre-v2 config (this was a real bug caught while building this service —
see `tests/test_risk_wiring_config_store.py`'s regression test).

`migrate_legacy` never raises and never writes anything; it is pure. On
Julien's real `.agent-config.json` (0.35 equity-fraction, 3x leverage, a
$26 `min_tradable_equity_usd` override, `max_daily_loss_pct: 0.121`), it
produces a profile with `risk_per_trade_pct≈15.75%`, `max_open_positions=3`
(carried over from `max_concurrent`), and `daily_loss_limit_pct=12.1%` —
reproducing the dollar risk the account's OWN settings were already taking,
not a generic default.

## Routes (`api.py`)

    GET  /api/risk/profile   -> RiskProfile
    PUT  /api/risk/profile   -> RiskProfile        body: RiskProfile (writes .agent-config.json atomically)
    GET  /api/risk/limits    -> DerivedLimits       (active account's equity)
    POST /api/risk/preview   -> DerivedLimits       body: {equity_usd, profile}

`GET /api/risk/limits` reads equity from `services.accounts` when that
service is importable (the accounts agent's own wave-2 service), else
`pathiel.client.hl_client.fetch_account_state` for the env account. Neither
resolving is a 503, not a silent $0.

**Not mounted here.** `pathiel/server.py` is integrator-only this wave —
add:

```python
from services.risk.api import router as risk_router
app.include_router(risk_router)
```

## Wired into the executor (behavior deltas for existing accounts)

`pathiel.agents.executor.maybe_execute`, `risk_gates.py`, `config_store.py`
and `book_params.py` all changed. Every delta:

- **The global equity floor is gone.** `min_tradable_equity(config)` and
  `book_margin_requirement(config)` still exist, unchanged, still exported
  (existing tests assert their standalone behavior directly) — but
  `maybe_execute` no longer calls them to BLOCK a trade. The new gate asks
  "can THIS book afford THIS trade at THIS equity" via `DerivedLimits`
  instead. **Any account that cleared the old floor still clears the new
  gate** — the new per-book thresholds are a fraction of what the old
  global floor demanded (tens of dollars vs. the old $26/$88.89-style
  numbers), so this is strictly more permissive for anything that already
  worked, and newly permissive for the accounts it was blocking.
- **Per-book notional is now a ceiling, never a floor.** A legacy fixed
  `notional_usd` (or the equity-fraction result) that exceeds what the
  resolved `RiskProfile` allows at current equity gets clamped down
  (`book_params.apply_notional_ceiling`). A book already sized BELOW the
  ceiling — including a deliberate under-sizing like `xs_reversal`'s
  documented de-risking while its forward ledger is unvalidated — is left
  exactly as configured. Scoped to the seven registered books only; the
  separately-tested main-engine (bookless) sizing path
  (`equity_fraction_per_trade`, `ai_long_notional_usd`) is untouched.
- **`max_open_positions` replaces `max_concurrent`** as the concurrency
  gate's ceiling, for any entry not covered by the existing book-capital
  carveout (`book_capital_carveout`, on by default for strategy-book
  entries since the 2026-07-10 funnel-audit fix — that carveout is
  untouched by this wave).
- **The daily loss limit's percent can now come from the profile.**
  `effective_daily_loss_limit`'s precedence: an explicit `max_daily_loss_pct`
  in config still wins outright; the resolved profile's
  `daily_loss_limit_pct` is the new SECOND choice; the static
  `max_daily_loss_usd` is the final fallback. The kill-switch MECHANICS
  (start-of-day math, the giveback ladder) are unchanged.
- **The refusal message changed shape.** `below_min_tradable_equity
  ($X < $Y)` is gone; the new reason is `book_not_feasible (<book> @ $X
  equity: <plain sentence naming the dollar shortfall>)`. `pathiel/dashboard.py:1012`'s
  humanizer regex matches the OLD string only — flagged for the dashboard
  owner (onboarding/ux-qa) under "For the integrator" below; nothing
  crashes, the raw reason still displays, just without the pretty regex
  rendering.

## For the integrator

- **Mount the router**: `pathiel/server.py` needs
  `app.include_router(services.risk.api.router)` — see "Routes" above.
  Integrator-only this wave (`docs/overhaul/SWARM_RULES.md` section 10).
- **`pathiel/dashboard.py:1012`** (owned by onboarding/ux-qa): the
  `below_min_tradable_equity (\$X < \$Y)` regex no longer matches anything
  `maybe_execute` produces. Suggested replacement, matching the new
  `book_not_feasible (...)` shape:
  `re.compile(r"book_not_feasible \(([\w_]+) @ \$([\d.]+) equity: (.+)\)")`
  → something like `"{0}: {2}"`. Not applied here — out of this wave's
  file ownership.
- **Shared, repo-wide test files edited** (both additive, both following
  an existing documented mechanism for exactly this situation —
  `services.accounts` doesn't exist in this worktree yet, it's the
  accounts agent's own wave-2 service):
  `tests/test_guarded_imports_resolve.py`'s `EXPECTED_MISSING_UNTIL_SWARM_MERGES`
  gained `("services/risk/api.py", "services.accounts", "api")`;
  `tests/test_deploy_config.py`'s `_NOT_YET_BUILT_BY_ANOTHER_SWARM_AGENT`
  gained `"services.accounts"`. Both self-remove (the test itself starts
  failing) once `services/accounts` merges — see each file's own comment.
- **`Dockerfile`**: added `COPY services/risk/ services/risk/` (genuinely
  needed now — `pathiel.agents.executor` imports it unconditionally).

## Tests

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q services/risk/ \
        tests/test_risk_wiring_executor.py tests/test_risk_wiring_gates.py \
        tests/test_risk_wiring_config_store.py tests/test_book_params.py

- `services/risk/tests/test_presets.py` — preset-table invariants (monotonic
  tiers, every preset contract-valid, auto resolution, overrides, custom).
- `services/risk/tests/test_derive.py` — unit tests for the formulas, plus
  the property suite over `[5, 8, ..., 100k] × {starter, small, standard,
  pro, auto}`: every book row has a reason; risk never exceeds its cap
  except through the documented bump; more equity never shrinks the
  feasible-book set or drops `max_open_positions`; the headline agrees with
  `can_trade`.
- `services/risk/tests/test_migrate.py` — every legacy-key mapping, in
  isolation, plus the real $15.19-shaped config end to end.
- `services/risk/tests/test_books.py` — the book registry mirrors the live
  claims registry exactly (tripwire test); the drawdown_ladder $525
  constant mirrors the live rung formula exactly (tripwire test).
- `services/risk/tests/test_api.py` — every route through `TestClient`,
  including the 503 path and the accounts-service-preferred-over-fallback path.
- `services/risk/tests/test_replay_benchmark.py` — the eval script's pure
  core plus one real subprocess run of the actual CLI.
- `tests/test_risk_wiring_executor.py` — **the e2e**: Julien's real
  `.agent-config.json`, copied, at $15.19 equity, through a fully mocked
  exchange, places a valid order; at $5 it refuses naming the dollar
  shortfall; equities already served by the old $26 floor stay served.
- `tests/test_risk_wiring_gates.py`, `tests/test_risk_wiring_config_store.py`,
  and the `apply_notional_ceiling` tests appended to `tests/test_book_params.py`
  — the risk_gates.py/config_store.py/book_params.py wiring additions, each
  in isolation.

## Eval

    .venv/bin/python services/risk/evals/replay_benchmark.py \
        --state-dir "$PATHIEL_STATE_DIR" --equities 15,100,1000,10000,100000

Copies `shadow_ledger/` out of `--state-dir` into a fresh temp directory
(never touches the source, never signs or places anything), replays every
recent signal from every book's own forward ledger against `derive_limits`
at each equity, and reports risk taken vs. would-execute counts. Pass rule:
**no trade exceeds its limit**. See the main agent report for a real run's
numbers (no live `PATHIEL_STATE_DIR` exists in this isolated worktree, so
that run used a representative synthetic ledger — the script itself takes
a real one exactly the same way).

## Sizing one trade (`size.py`)

`size_trade(equity, profile, books, entry_px=, stop_px=, leverage=, targets=)`
answers "how big is THIS trade for my account" with the same rules the books
use: the profile's risk budget over the stop distance, leverage capped at
min(requested, coin max, profile max), bumped to the venue minimum only when
the profile allows. It returns `TradeSize` (contracts/risk/models.py) with the
coin size rounded to the venue's step (never under the minimum), margin, dollars
at risk, payoff and R multiple per target, and `warnings` when the best target
pays under 1R. Served as `POST /api/risk/size` (the desk page's "Your size"
line) and as the MCP tools `risk_size_trade` and `desk_brief`'s `your_size`.
`active_equity_usd()` reuses one equity read for 60 s
(`PATHIEL_RISK_EQUITY_TTL_S`): each read is about 12 venue requests.

