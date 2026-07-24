# Polymarket Scout (shadow)

Point the LLM's information-synthesis edge at markets priced by **judgment, not
speed**. This is the deliberate opposite of the gabagool pair-arb, which is a
latency race we measured as **0/60 tradeable on resting books** (see
`research/alpha_swarm/findings/W-Z1_polymarket.md`). Here, being *right* beats
being *fast*.

**Zero trading, zero capital.** It reads public Polymarket data (keyless), asks
the **hermes AI brain** for a YES probability, records the divergence as a PAPER
trade filled at the **touch** (the ask, never the mid), and grades on the
market's actual resolution. Capital only after the gate below clears.

## Two lanes

| lane | universe | ordering | edge threshold | why |
|---|---|---|---|---|
| **judgment** | all open markets, 3–21d, liquid, mid-priced | most liquid first | 12pp | quiet mid-tail markets priced by retail/partisan flow |
| **trending** | polymarket.com's own front page (`/events` by 24h volume, **sports + esports excluded** server-side via `exclude_tag_id`) | BREAKING first, then 24h volume | 15pp | what the crowd is trading now; a wider/faster book has to pay for itself |
| **sports** | polymarket.com/sports (`tag_id=1`), incl. in-play | LIVE first, then 24h volume | 20pp | operator request. Own `SPORTS_CFG` (no 6h floor — a game line settles in hours). **Opt-in only** (`run --lane sports`), never in the cron: no measured edge, and an LLM read of a live game is stale before it lands |

**BREAKING** has no public tag (checked: `breaking` and `breaking-news` both
return 0 events), so it is reconstructed from intent: `|24h price change| ≥ 5pp`
over a $20k 24h-volume floor. A market that repriced hard in a day is a market
the news just hit.

**CRAZY ODDS** (`trending.longshots`, board tab) is a view, not a lane: every
tradeable market at or under **33c (≥3x gross payout, on the ask)** across both
universes, ranked by our AI's disagreement first. It ships with its own number:
buying the p≤0.20 bucket is the **losing** side of the one backtest cell that
cleared t=2 (≈−1%/$ after fees, n=89). A 3x payout is not a 3x edge.

The judgment and trending lanes drop **ladder/latency families** (`up or down`, `higher or lower`,
`what price will X hit`, `# tweets`) — asset-agnostic, because the first live
board surfaced `S&P 500 (SPX) Opens Up or Down` next to the crypto ones.

## Pipeline

```
open_markets() | open_events()  →  is_judgment_market() | trending.is_tradeable()
   →  BrainForecaster  →  hermes_trader.agents.ai_brain.get_brain()  (claude_cli + WebSearch)
   →  signed_edge vs market price  →  decide_side (|edge| ≥ threshold)
   →  paper-fill at the CLOB touch  →  ledger.record(lane=…)  [.state/polymarket_scout/signals.jsonl]
                                        ⋯ market resolves ⋯
   →  ledger.grade(resolver, lane)  →  paper EV + Brier(LLM) vs Brier(market)
   →  board.refresh()  →  .state/polymarket_scout/board.json  →  GET /predictions
```

- **The brain, not a private client.** `BrainForecaster` calls the same
  `ai_brain` seam the trading engine's research verdicts go through, so the
  provider is whatever `AI_BRAIN_PROVIDER` says (claude_cli today) and there is
  one place to change models. The system prompt asks for a `verdict` key because
  `ai_brain._contains_parseable_verdict_json` drops any CLI reply without one.
  `web_search=True` is lane-specific and deliberate: perp candle research
  measured search EV-neutral, event forecasting is a news-synthesis task.
- **Fill realism**: paper-filled at the best **ask** on the side we take, net of
  a conservative ~1%/fill fee proxy both ways. Never the mid.
- **The verdict that matters**: `brier_llm < brier_mkt`. If the LLM is not better
  calibrated than the market's own price, there is no edge — kill it.
- **Lanes grade separately** (`ledger.grade(..., lane=…)`). Pooling them would
  let a good lane launder a bad one through the gate. v1 rows have no `lane`
  field and read as `judgment`.

## Run

```bash
# funnels, no LLM, no cost
python -m services.polymarket_scout.run --dry --limit 12
python -m services.polymarket_scout.run --lane trending --dry --limit 12
python -m services.polymarket_scout.run --lane sports --dry --limit 12
python -m services.polymarket_scout.run --lane trending --dry --longshots   # the >=3x board

# live LLM (routes through the configured brain), zero capital
python -m services.polymarket_scout.run --limit 10
python -m services.polymarket_scout.run --lane trending --limit 6
python -m services.polymarket_scout.run --lane sports --limit 4     # opt-in, measures only

# cron: both lanes + grade + refresh the dashboard cache
python -m services.polymarket_scout.daily
python -m services.polymarket_scout.daily --board-only     # no LLM, cache only

# the mechanical nulls, free and deterministic
python -m services.polymarket_scout.backtest --pages 12 --min-volume 20000
```

Dashboard: **`/predictions`** (page) and `GET /api/dashboard/predictions` (JSON).
The web route only ever reads `board.json` — no network and no LLM call happens
inside a request, so a slow Gamma API or a hung CLI cannot hang a poll. A cache
older than 3h renders with a STALE badge.

Paid eval (opt-in, format + calibration floor):
```bash
HERMES_RUN_PAID_POLYMARKET_EVAL=1 python scripts/eval_polymarket_forecaster.py
```

## What the backtest says (2026-07-24, n=263 resolved markets / 164 non-sports)

`backtest.py` grades only what can be graded honestly. **The LLM's forecast is
not backtestable** — asking a model about a resolved market leaks the outcome.
What it measures is the mechanical null the forward LLM ledger has to beat.
Full write-up: `research/alpha_swarm/findings/W-Z3_polymarket_backtest.md`.

| test | result (non-sports) | read |
|---|---|---|
| market Brier @T-24h | 0.088 (0.146 incl. sports) | the price is **well calibrated**; we will not out-forecast it on average |
| buy favorites p≥0.80 | −0.037/$ (n=10, t=−0.39); −0.114 all (n=20, t=−1.3) | the reversed-longshot claim does **not** replicate |
| fade longshots p≤0.20 @T-24h | **+0.0106/$ (n=89, t=2.28)** | best cell on the board; dies under Bonferroni (12 cells), flips sign at T-168h and when sports are included → **CANDIDATE, not validated** |
| BREAKING momentum → resolution | −0.060/$ (n=46, t=−0.93) | buying the direction of a hard 24h move **loses** |
| BREAKING fade → resolution | +0.011/$ (n=46, t=0.17) | statistically **zero** |
| forward 24h move after a break | −0.016 (t=−0.70) | no continuation, no reversal — **efficiently priced** |

**Conclusion: there is no mechanical edge on this venue.** Every rule-based
translation lands inside noise after fees, and the one cell that clears t=2 is a
best-of-12 pick that does not survive correction or a sample change. That is the
point of running it: the BREAKING tab is a *news surface*, not a signal, and the
only live hypothesis left is the LLM's judgment, graded forward against these
numbers.

## Go-live gate (pre-registered — W-Z1)

No capital until ALL hold on the shadow ledger, **per lane**:
1. **n ≥ 150** resolved paper trades.
2. **mean paper PnL ≥ +3%/position** at the touch (not the mid).
3. **p < 0.05** vs a matched-null (random side / random divergence-selected).
4. **brier_llm < brier_mkt** — the LLM beats the market's price.
5. **Monotonic**: bigger divergence → bigger realized edge.
6. **Dispute-robust**: edge survives dropping UMA-disputed resolutions.
7. **Access solved**: US order placement is geoblocked on the international CLOB
   — live needs Polymarket US (QCX, CFTC DCM) + operator KYC + an Ed25519 key.

## Known refinements (not final)

- **Novelty/perpetual markets** ("… before GTA VI?") pass the date gate but
  resolve on a moving target — add a category/keyword exclusion.
- **Category weighting**: W-Z1 found the soft money in geopolitics/world/culture.
  The trending lane already drops sports/esports at the API; it does not yet
  up-weight the soft categories.
- **Book-mid vs Gamma price**: the filter uses the Gamma outcomePrice; the paper
  fill uses the live CLOB ask. Consider mid-from-book for the edge calc too.
- **Correlated duplicates**: one skip set covers both lanes, but a multi-market
  event (three "Claude Opus released by <date>" markets) can still produce
  near-duplicate reads. Grade with that in mind before trusting n.

## Gotchas found the hard way

- **Gamma caps a page at 100 rows** regardless of `limit`. The old pager asked
  for 500 and stepped the offset by 500, silently striding over 80% of the
  universe. Every pager here steps by `PAGE_MAX`.
- **`order=volume` sorts the string column** and returns junk. Use `volumeNum`.
- **`endDate` is not a clock**: plenty of settled markets carry placeholder end
  dates years out. The backtest ages markets off the last price bar instead.
- **Only the event payload carries `tags`** — the `events` stub embedded in a
  `/markets` response has none. That is why the trending lane reads `/events`.
