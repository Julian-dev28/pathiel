# W-WH2: the 0xe282 ladder (buy the drawdown, average down, no stop)

2026-09-13. Backtest of the strategy reverse-engineered in
`W-WH1_wallet_0xe282.md`, as a candidate book for the Pathiel loop.

## PRE-REGISTRATION (frozen 2026-09-13 before the backtest produced a number)

Nothing in this section may be moved after the first run. Every parameter is
read off the wallet's own trades by `W-WH1_reverse_engineer.py` (section 8,
`calibration` in `W-WH1_results.json`), so none of them is tuned on the data
this file scores.

### The rule

| piece | value | where it comes from |
|---|---|---|
| trigger | daily close <= 0.789 x the highest high of the prior 30 daily bars | median first fill vs trailing-30d high, -21.1% (n=18 trips >= $250K) |
| first rung | next day's open | doctrine: signal at close, fill next open |
| rungs | K = 5 | median new-low buy orders per trip, 5 |
| rung spacing | each rung 7.2% under the previous rung | 5 rungs spanning the median buy span, -25.7% |
| take profit | limit at avg entry x 1.0514, whole position | median winning exit vs avg entry, +5.14% |
| stop | **none** | the wallet has none; that is the strategy |
| sizing | rung notional = L x equity at ladder start / (K x N coins) | max gross = L x equity |

Fills inside a bar: an add fills at min(open, rung price) when low <= rung
price. The take profit fills at max(open, target) when high >= target, using
the average entry held **before** the bar. A bar that adds does not also take
profit; the lower average entry is live from the next bar. Adverse first.

### Costs and liquidation

- 12.5 bps on every rung fill and on the exit (25 bps round trip).
- Funding: longs pay 1.25e-5 per hour on notional, charged daily at the close.
- One cross-margined account, as the wallet runs and as Pathiel runs. Every
  bar: equity at the lows = cash + sum of position x (low - avg entry);
  maintenance = sum of position x low / (2 x HL max leverage). Every coin's
  low is taken at once, which overstates coincident stress.
- Equity at the lows <= maintenance is a liquidation: equity goes to zero and
  the cell ends. **No redeposit.** The wallet redeposited $402,826 three
  minutes after its 2025-10-10 liquidation; a backtest that lets the account
  do that is testing the depositor, not the rule.
- Ladders still open at the end of a span are marked at the last close. They
  are not discarded. For a no-stop rule, discarding them is the one move that
  makes it look safe.

### Cells: 2 universes x 2 leverages = 4

- **A, Pathiel's crypto majors:** BTC, ETH, SOL, BNB, XRP (`pathiel/agents/universe.py`
  MAJORS entries with a Hyperliquid perp and multi-year daily history). The
  HIP-3 commodities, indices and mega-caps have under a year of history, so a
  no-stop ladder cannot be tested on them through a bear market. They are out,
  and said so.
- **B, the wallet's own perps:** AAVE, CRV, GMX, HYPE, LINK, NEAR, ONDO,
  RENDER, RUNE, UNI, VVV, WLD, ZEC. Control only: outside the majors allowlist
  (operator decision 2026-08-29) and chosen by a trader looking at 2025-26, so
  it carries selection bias. **B cannot promote.**
- **L = 1** is Pathiel's live sizing. **L = 3** is the wallet's account leverage
  on the day of the screenshots (Hyperdash 3.01x, clearinghouse 2.27M / 755K).

Data: Hyperliquid `candleSnapshot` 1d from `startTime=0`, cached by
`W-WH2_fetch.py`. A coin joins after it has 31 bars.

### Statistics

- **Primary:** terminal equity multiple over the full span, net.
- **Matched null:** the same machine, trigger replaced by a coin-day coin flip
  while flat, p = observed ladders / flat days for that coin, so the ladder
  count matches in expectation. 2000 draws. p = P(null terminal >= observed).
- **Out of sample:** the span split at its calendar midpoint, each half run
  from equity 1 with its open ladders marked at the half's last close.
- **Bonferroni:** 4 cells, alpha 0.0125.
- Context, not a gate: equal-weight buy-and-hold of the same universe at 1x.

### Verdict rule

**VALIDATED** only if the full-span terminal multiple > 1, both halves > 1,
and null p < 0.0125. Anything else is **REFUTED** and the book is not built.

---

## RESULTS (first and only run of the frozen rule)

`hypotheses/W-WH2_ladder_backtest.py` -> `W-WH2_results.json`. Daily bars
2020-08-19 to 2026-09-12. Calendar midpoint 2023-09-01.

| cell | terminal | CAGR | max DD | half 1 | half 2 | null median | null p | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **A majors, 1x** | **2.933x** | +19.4% | -43.5% | 1.516x | 1.429x | 1.867x | **0.0015** | **VALIDATED** |
| A majors, 3x | 0.000x | -100% | -100% | 0.000x | 2.602x | 0.000x | 1.0000 | REFUTED: liquidated 2022-06-13 |
| B wallet, 1x | 1.778x | +10.0% | -34.0% | 1.120x | 1.507x | 1.540x | 0.1555 | REFUTED: null |
| B wallet, 3x | 4.803x | +29.6% | -89.8% | 0.517x | 2.989x | 2.230x | 0.0890 | REFUTED: half 1, null |

Context, not a gate: equal-weight 1x buy-and-hold of the same universe returned
**8.369x** on A (2.168x / 2.013x by half) and 2.376x on B.

**At the wallet's leverage, on the wallet's majors, the rule is dead by
2022-06-13.** 99.9% of the random-entry null draws at 3x were liquidated too.
That is the part a 30-day screenshot cannot show: the same rule this wallet
runs, without $26M of inflows behind it, does not survive 2022.

On the wallet's own coins it cannot beat random entry at either leverage.
Picking AAVE, LINK and ZEC added nothing a coin flip would not.

What survives is the rule at 1x on the five crypto majors: 296 ladders, 293
taken at the target, 3 open at the end and marked. Half 1 (2020-08 to 2023-09)
predates every fill the parameters were read from, so it is out of sample for
the calibration as well as for time.

The closed-ladder win rate is **99%** (293 of 296) because a no-stop ladder only
closes at its target. That is the Hyperdash 100% again, reproduced by a
machine. It says nothing about the rule. The equity curve does.

## ROBUSTNESS (after the verdict; cannot change it)

`hypotheses/W-WH2_robustness.py` -> `W-WH2_robustness.json`.

**1. XRP carries the null test.**

| drop | terminal | null p | | only | terminal | null p |
|---|---:|---:|---|---|---:|---:|
| BTC | 3.500x | 0.003 | | BTC | 1.399x | 0.250 |
| ETH | 3.010x | 0.012 | | ETH | 2.572x | 0.038 |
| SOL | 2.681x | 0.000 | | SOL | 4.235x | 0.337 |
| BNB | 3.353x | 0.002 | | BNB | 1.847x | 0.328 |
| **XRP** | 2.302x | **0.071** | | **XRP** | 8.015x | **0.002** |

Every leave-one-out stays above 2.3x. Only XRP beats its own null alone, and
without XRP the basket fails the 0.0125 bar. That is a concentration
discount. XRP's pump-and-bleed cycles are what a -21% trigger with a +5%
target is built for.

**2. Not a knife edge.** Across 27 neighbouring settings (trigger -15/-21/-30%,
spacing -5/-7.2/-10%, target +3/+5.1/+8%), every one ends between 2.03x and
4.16x, and 20 of 27 clear p < 0.0125. The -21% and -30% trigger rows clear it in
all 18 cells. The -15% trigger is where it weakens, because shallow dips are
closer to random entry.

**3. The leverage cliff sits between 2x and 2.5x.**

| L | 1 | 1.5 | 2 | 2.5 | 3 |
|---|---:|---:|---:|---:|---:|
| terminal | 2.93x | 4.78x | 7.54x | **0** (2022-06-15) | **0** (2022-06-13) |
| max DD | -43.5% | -63.1% | -81.4% | -100% | -100% |

**4. Start date.** Started on 1 January of each year and run to today:
2021 2.80x, 2022 1.49x, 2023 1.55x, 2024 1.41x, 2025 1.14x. It never loses.
It beats 1x buy-and-hold from the 2022, 2024 and 2025 starts (1.49x vs 0.70x,
1.41x vs 1.15x, 1.14x vs 0.59x). It trails badly from the bull starts: 2021 at
2.80x vs 8.91x, 2023 at 1.55x vs 2.89x.

**5. Pathiel's executor would not run the tested rule.** Every Pathiel position
gets a server-side backup stop. At 1x that stop sits 60% under entry
(`book_params.BACKUP_SL_MAX_FRAC_OF_LIQ`). Modelled on the low, after that
bar's adds:

| backup stop | terminal | max DD | ladders stopped |
|---|---:|---:|---:|
| none (tested) | 2.933x | -43.5% | 0 of 296 |
| -60% vs avg entry (the executor at 1x) | **1.874x** | -52.1% | 6 of 354 |
| -40% | 1.468x | -41.7% | 17 of 425 |
| -25% | 1.223x | -38.9% | 35 of 505 |

With the executor's own stop, the result lands on the random-entry median
(1.867x). The stops fire in the capitulation lows (SOL 2022-05-10 and
2022-11-08, ETH 2022-06-18), which are exactly the bars the rule buys. **Run
through today's executor, the edge is gone.**

## VERDICT

**A majors at 1x: VALIDATED** under the doctrine, with three discounts that
go into the book:

1. The null edge is concentrated in XRP (p 0.071 without it).
2. Leverage above 2x is fatal on this path. The cap is a code constant, not
   config.
3. The rule only holds without a stop, and Pathiel places a backup stop on
   every position.

**Everything else: REFUTED.** The wallet's 3x, and the wallet's coins at any
leverage.

This is a defensive long allocation with a timing edge over random entry. It
is not alpha over holding: 2.93x against 8.37x buy-and-hold over six years,
with a -43.5% drawdown and one ladder that stayed open for 805 days.

## STRATEGY: the book for the Pathiel loop

**Built and live 2026-09-14** as `pathiel/agents/drawdown_ladder_live.py`,
with the three operator decisions below. Tests: `tests/test_drawdown_ladder.py`.
It places nothing until the account holds $525, with the ladder's margin on the
main perp dex.

### `drawdown_ladder`, as tested

| | |
|---|---|
| universe | BTC, ETH, SOL, BNB, XRP perps (main dex) |
| cadence | once per UTC daily close, on `fetch_hl_candles(coin, "1d", 31)` |
| trigger | close <= 0.789 x max(high of the prior 30 closed daily bars), coin flat and unclaimed |
| entry | rung 1 at the next open; rungs 2-5 as resting GTC limit buys at 7.17% steps under rung 1, all placed at entry |
| size | rung notional = equity / 25 (L=1, K=5, N=5); whole ladder = equity / 5 |
| exit | one reduce-only GTC limit for the full position at avg entry x 1.0514, re-priced after every rung fill |
| stop | none; see blocker 2 |
| leverage | 1, hard-coded; a test fails if config can raise it |
| costs assumed | 25 bps round trip + 1.25e-5/h funding on longs |

### Where it plugs in

- `pathiel/agents/drawdown_ladder_live.py`: a `maybe_run(config, universe,
  positions, execute_fn)` shaped like `xs_reversal_live.py`, called from the
  books section of `scripts/trading_loop.py`.
- **Adds are pre-committed, not re-decided.** All five rungs are gated once,
  at entry, on the whole ladder's notional (equity / 5). Resting rungs then
  fill on the exchange without passing through the gates again. That matches
  the backtest, which assumed resting limits. It also keeps the daily-loss kill
  switch from cancelling rungs on the down days they exist for. The kill switch
  still blocks **new** ladders.
- **Exits bypass DSL.** No trailing floor, no hold timer. The DSL add-refresh
  (`test_dsl_add_refresh.py`) already moves entry to the exchange average on a
  size increase. The book re-prices its TP from that same average.
- **Claims.** The book holds its coin claim for the life of the ladder, which
  can run past two years. `xs_reversal` cannot short that coin for that time. Say
  so in the claims registry rather than find out.
- **`max_concurrent`.** The live config holds 3. In 2022 this rule held all 5
  ladders at once, so it needs the book carve-out `risk_gates` already has.
- **Grading.** `shadow_ledger` grades per trade at a horizon. This book's
  closed trades are 99% wins by construction, so a closed-trade grade would
  promote it forever. `autonomous_cycle.py` must grade it on **daily
  mark-to-market book equity**, and demote on either:
  - book drawdown worse than **-43.5%**, the six-year backtest maximum, or
  - trailing-365d book return below the median of a seeded random-entry twin,
    run by `simulate(..., entry_prob=...)` on the same live candles.
- **Trace.** One nightly log line: book MTM equity, open rungs per coin, the
  random-entry twin median, and 1x buy-and-hold of the five coins. Those three
  numbers are the measurable outcome the book is accountable to.

### Operator decisions, 2026-09-14

1. **Capital: a 50% sleeve.** Rung = 50% of equity / 25, so a $10.50 rung
   needs **$525** of equity. xs_reversal keeps its 3 x 13.462% (40.4%);
   50% + 40.4% + the 2% free-margin floor = 92.4%. Below $525 the book logs
   the shortfall and places nothing.
2. **No backup stop, and no hard flatten.** The book never goes through the
   executor. The hard daily-loss flatten, the DSL tracker and the slot count
   all read `book_positions()`, which leaves this book's coins out. The
   daily-loss *gate* still blocks new ladders. At a 50% sleeve the backtest
   crosses the -12.1% daily kill on zero days (one day, 2021-06-21 at -16.5%,
   at a 100% sleeve).
3. **Crypto exception.** The account stays HIP-3 only (`enable_crypto: false`)
   for every other book. This book trades its five main-dex majors itself.

XRP concentration (p 0.071 without it) is accepted as a known discount rather
than a blocker.

### What the build does that the spec did not say

- **The rule is code constants, not config.** Only `enabled`, `shadow_only` and
  `sleeve_frac` are config. A test fails if they drift from `W-WH2 FROZEN` or
  if leverage is anything but 1.
- **It opens within 6 hours of the UTC daily close or skips the day.** Late
  entries are not the rule that was tested.
- **Rung 1 goes to disk before any resting order is sent**, so a crash mid-entry
  cannot leave an unrecorded position.
- **A closed ladder cancels its rungs first.** If a rung fills after the target
  while the loop is down, the leftover is flattened on the next clean read.
- **A degraded account read (equity 0) touches nothing**, and an unreadable
  state file is quarantined and blocks new ladders.
- **Grading is mark-to-market only.** One row a day in
  `.state/.drawdown_ladder_equity.jsonl`. `autonomous_cycle.py` demotes the book
  when drawdown passes -43.5% of sleeve. The random-entry-twin rule needs 365
  days of live rows and is **not built yet**.

## What kills this book

The rule assumes large caps that fall 21% get back to average entry +5% before
all capital is spent. A market that bleeds for years without that bounce (BTC
2018: -84% over 12 months) fills all five rungs early and marks every coin's
ladder at its full loss. At 1x that is a drawdown, not a liquidation. The
demotion rule is sized to catch it.
