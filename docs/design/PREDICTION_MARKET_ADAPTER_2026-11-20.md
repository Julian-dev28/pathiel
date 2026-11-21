# Prediction-market execution: design doc

Status: design only. No adapter code, no live orders. Written 2026-11-20 off
`overhaul-2026-10-17`.

pathiel already reads Polymarket and Kalshi odds, read-only, no key, in
`services/intel/sources/predictions.py`. This doc covers adding EXECUTION:
placing and managing real orders on a prediction-market venue from a desk's
thesis. It does not ship a line of adapter code. It does not place an order.
It is the plan Julien reviews before anyone writes `services/predictions_exec/`.

## Why this is a different animal from the perp desks

Every gate pathiel has today, `place_bracket`'s 2R floor, the
half-liquidation-distance stop rule, `desk_share.py`'s per-desk slice,
`desk_leverage.py`'s per-desk cap, is built for a LEVERAGED, MARGINED
position that can be liquidated before its thesis plays out. A binary
prediction-market contract has none of that:

- It settles at exactly $1 (YES resolves) or $0 (NO resolves). There is no
  partial fill of the outcome and no price between settlement and the book.
- There is no leverage and no liquidation. The entire position is
  pre-funded: buying a YES contract at price `p` costs `p` per contract and
  the maximum loss is `p` (it resolves NO). Buying a NO contract at the same
  market (equivalent to selling YES short at `p`) costs `1 - p` and the max
  loss is `1 - p`. There is no stop-loss in the perp sense because there is
  no mark price to trail; the position either resolves or it is sold back
  into the book before resolution at whatever the book will pay.
- Risk is therefore capped and known at entry, by construction, the moment
  size is chosen, the way a long perp's liquidation price is NOT known in
  advance relative to news risk. That is a real advantage. It also means
  every gate pathiel built to protect against forced liquidation
  (`pathiel/agents/bracket.py:103-157`, the `max_stop_frac` liquidation-distance
  check) is simply inapplicable here, not weaker, inapplicable. Porting it
  over by analogy would gate the wrong failure mode.
- The failure mode that replaces liquidation is being WRONG and ALL IN: the
  premium is fully at risk with no partial exit forced by a venue (unlike a
  stop that fires mechanically), so the equivalent discipline has to be
  sizing (how much premium per market), horizon (don't enter so close to
  resolution that the trade is really a bet on the literal event, not a
  mispricing), and liquidity (can the position actually be unwound before
  resolution if the thesis breaks).

Section 2 proposes five gates that are the contract-native equivalents of
pathiel's existing five (2R floor, liquidation distance, per-desk slice, one
position per desk, event blackout).

## 1. Venue recommendation

**Recommendation: build against Kalshi first. Do not place real orders on
the offshore Polymarket CLOB (`polymarket.com` / `clob.polymarket.com`,
the exact platform `services/intel/sources/predictions.py` already reads for
quotes) from a US-based operator. Treat Polymarket US (the new CFTC-regulated
entity) as a second venue to evaluate once its market catalog is confirmed,
not as a day-one build target.**

This is reasoning, not a legal conclusion. Everything market "flag for
counsel" below needs an actual lawyer before real money moves, not this doc.

### Kalshi (KalshiEX LLC)

- CFTC-designated contract market (DCM) since November 3, 2020
  ([CFTC Release 8302-20](https://cftc.gov/node/234616),
  [designation order PDF](https://cftc.gov/filings/documents/2020/orgkexkalshidesignation201103.pdf)).
  CFTC granted an amendment January 17, 2025 permitting intermediated
  futures trading
  ([order PDF](https://cftc.gov/filings/documents/2025/orgdcmkexkalshiamord250117.pdf)).
  Still listed "Designated" in the CFTC's own registry
  ([TradingOrganizations/42993](https://cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/42993)).
- The exact commodity/macro contract types pathiel's `fetch_kalshi`
  (`services/intel/sources/predictions.py:150-197`) already pulls
  (`KXWTIW`, `KXWTIMAX`, `KXBRENTMON` price-range ladders, plus CPI/jobs/FOMC
  series under its `Commodities`/`Financials` category scan) are the exact
  contract categories this doc proposes trading. Nothing new to source;
  the read path already exists and is unchanged by this doc.
- A documented, stable, key-pair-signed REST API (Ed25519 or RSA-2048 PSS,
  no wallet, no on-chain custody): `docs.kalshi.com/getting_started/api_keys`,
  `docs.kalshi.com/getting_started/quick_start_authenticated_requests`. No
  OAuth path exists; this is the only auth model.
- A **primary-sourced, stable fee formula**: Kalshi's own "Kalshi Fee
  Schedule, 2nd Iteration" PDF (dated September 22, 2022, fetched directly
  at `kalshi.com/docs/kalshi-fee-schedule.pdf`) gives
  `fee = round_up(0.07 x contracts x price x (1 - price))`, half that rate
  (`0.035`) on S&P 500 / Nasdaq-100 index markets. This is a known, bounded,
  computable number the edge-floor gate in section 2 can subtract before
  any order goes out.
- **Flag for counsel, not resolved here: active circuit-split litigation
  over Kalshi's SPORTS-wagering contracts specifically**, not the
  commodity/macro categories pathiel would trade: the Third Circuit ruled
  for Kalshi on New Jersey (April 6, 2026), the Ninth Circuit ruled against
  Kalshi on Nevada (August 28, 2026), the Sixth Circuit let Ohio/Tennessee
  proceed (~September 2026), and the Fourth Circuit ruled against Kalshi on
  Maryland (November 17, 2026). New Jersey petitioned the Supreme Court for
  cert September 2, 2026; as of November 18, 2026 no cert has been granted.
  Separately, a CFTC directive issued November 5, 2026 told every DCM
  (Kalshi included) to remove state-level geofencing within 30 days, which
  conflicts with existing state-court injunctions in at least Michigan,
  Nevada and Washington. None of this litigation is about the WTI
  price-ladder or macro-data contracts this doc targets, but an operator's
  account-level access, and whether Kalshi geofences a given state at all
  by December 2026, is in active flux. **Get a lawyer's read on whether the
  sports-contract litigation creates any exposure for an account that only
  ever trades commodity/macro series, and on the operator's own state of
  residence against the live geofencing picture, before this ships.**
- Election contracts: Kalshi already won this fight (the CFTC dropped its
  appeal after the D.C. Circuit sided with Kalshi in the 2024/2025 fight).
  Not a live risk as of this writing.

### Offshore Polymarket (`polymarket.com`, the platform predictions.py reads today)

- Polymarket's own Trust & Safety page states plainly: "US traders and
  others in restricted jurisdictions are prohibited from trading," and
  describes active wallet screening for "restricted jurisdictions"
  (`polymarket.com/trust`). This is Polymarket's own ToS, not a third
  party's claim.
- **Flag for counsel: that geo-block is under live, unresolved CFTC
  investigation as of this writing.** Reuters reported exclusively on
  October 27, 2026 that the CFTC has reopened an investigation into whether
  Polymarket's offshore platform let US persons trade in breach of its 2022
  settlement
  ([syndicated copy](https://www.thestar.com.my/tech/tech-news/2026/10/28/exclusive-us-agency-is-again-probing-if-polymarket-broke-2022-settlement-sources-says)).
  Both the CFTC and Polymarket declined comment. No charges as of that
  reporting. This is exactly the question a US-based operator placing real
  orders there would be stepping into: do not treat the ToS block as either
  "safe because they'll stop US traffic" or "ignorable because an earlier
  probe closed without charges." It is an open question, not a ruling.
- **Recommendation: do not execute against this venue from a US-based
  operator.** This is the lowest-confidence, highest-exposure option of the
  three and the one furthest from a clean legal answer.

### Polymarket US (QCX LLC, d/b/a "Polymarket US")

- A real, separate, CFTC-regulated entity structure, launched and
  operating, not a rumor:
  - **QCX LLC**, doing business as "Polymarket US", is a CFTC-designated
    contract market (DCM), designated July 9, 2025 (CFTC trading
    organizations registry). Polymarket acquired this entity (then "QCEX")
    in 2025.
  - **QC Clearing LLC** is the clearinghouse, a CFTC-registered derivatives
    clearing organization (DCO), registered December 17, 2024
    ([CFTC Release 9023-24](https://cftc.gov/PressRoom/PressReleases/9023-24)).
  - **PM Derivatives, LLC** is the broker, NFA-approved as a futures
    commission merchant (FCM), operating fully collateralized (no margin:
    "requiring customers to fund their positions in full at the time of
    each trade"), reported approved by Finance Magnates November 3, 2026.
  - A November 24, 2025 CFTC amended order removed an earlier clause that
    barred FCM-intermediated access, which is why PM Derivatives exists at
    all.
  - Live to the public since December 3, 2025 (sports contracts first),
    expanding through 2026 via CFTC self-certifications: Token Launch
    Contracts (July 2026), Presidential Announcement Contracts / "PACUS"
    (October 20, 2026).
- **Open question, not resolved by this research: does Polymarket US's
  order book actually carry the geopolitical/macro markets
  `fetch_polymarket` (`services/intel/sources/predictions.py:58-117`) reads
  today** (Iran ceasefire, Strait of Hormuz, the oil desk's
  `prediction_market_queries` in `services/strategies/desks/oil.py:71`)?
  Those odds come from `gamma-api.polymarket.com`, the OFFSHORE platform's
  public data API. Polymarket US is a legally and operationally separate
  exchange (separate DCM, separate order book, separate entity) that
  started with sports contracts. Nobody has confirmed in this research
  that the same markets pathiel's thesis prompt already shows the desk
  (`services/thesis/prompt.py:502-509`) are even listed on Polymarket US.
  **This must be checked against Polymarket US's own market list before any
  code is written against it** (see section 6, open question 1).
- State-by-state availability for Polymarket US itself is unconfirmed and
  sources conflict (one claims 49 states + DC, excluding only Nevada;
  others list more exclusions). Check `polymarket.us` directly at build
  time, not a number in this doc.
- **Recommendation: track this venue, do not build against it in milestone
  one.** It is young (under a year live as of this writing), fully
  collateralized (which actually fits the contract-native risk model in
  section 2 well: there is no margin to misuse because there is none), and
  worth a second look once its catalog is confirmed to cover the same event
  categories the oil/metals desks already care about.

### Net

Kalshi is the only one of the three with (a) a stable, already-relevant
market catalog pathiel already reads, (b) a documented non-custodial
key-pair auth model with no wallet risk, (c) a primary-sourced fee formula,
and (d) litigation risk that is concentrated in a contract category
(sports) this doc is not proposing to trade. It is still a flag-for-counsel
situation on the geofencing question, not a clean green light. Build M1
(section 5) against Kalshi.

## 2. Contract-native risk model

Five gates, each a deterministic function pathiel's existing gate code
pattern already proves out (pure, no I/O, unit-testable without a venue
call, exactly like `pathiel/agents/bracket.py`'s own module docstring:
"Everything here is deterministic... The judgment... comes in as arguments,
never from in here"). None of these touch leverage or liquidation; none of
pathiel's existing leverage/liquidation code is reused, because none of it
applies.

### 2.1 Max premium at risk per market, as a % of the desk slice

The direct analog of `size_trade` sizing off `desk_share.py`'s
`slice_usd`. Every active desk already has an equity share
(`desk_equity_share` in the agent config, enforced by
`services/strategies/desk_share.py:38-60`). Propose the same mechanic one
level down:

- Each desk that trades prediction markets gets its own
  `prediction_market_share` of ITS OWN desk slice (not of total account
  equity), e.g. the oil desk could carry `prediction_market_share: 0.10`,
  meaning at most 10% of the oil desk's equity slice is ever at risk in
  prediction-market premium at any one time, on top of (not instead of) its
  perp position.
- Within that budget, a single market cap, e.g. no one market ever costs
  more than 25% of the prediction-market sub-slice, so one wrong contract
  does not burn the whole budget. This is a straight re-use of the
  "per-desk slice, one slot" shape `desk_share.py` already enforces for
  perps, applied to a line-item budget instead of a single position.
- The sub-slice is marked-to-market continuously (unlike a perp, a
  resolved-at-$1 contract and an about-to-expire-worthless contract both
  show up honestly in the running total, since there is no unrealized PnL
  ambiguity from leverage to correct for the way
  `_stable_equity_for_sizing` has to for perps,
  `scripts/pathiel-mcp-server.py:4639-4650`).

### 2.2 Edge floor: model probability vs. market price, net of fees

The contract-native replacement for the 2R floor
(`pathiel/agents/bracket.py:103`, `DEFAULT_MIN_R = 2.0`). A 2R floor asks
"is the reward worth the risk." The prediction-market question is
different: "does my model disagree with the market's price by more than it
costs to bet on that disagreement."

```
edge          = p_model - p_market          (signed; trade YES if positive, NO if negative)
fee_cost      = venue fee at this price and size
                  Kalshi:     round_up(0.07 * C * P * (1-P))   [0.035 on S&P/Nasdaq index markets]
                  Polymarket: C * taker_rate(category) * P * (1-P)  [0% if resting/maker]
net_edge      = |edge| - (fee_cost / (C * P))     # fee expressed as probability points
```

Refuse the order unless `net_edge >= MIN_NET_EDGE` (propose starting at
0.03-0.05, i.e. 3-5 probability points net of fees; this is Julien's risk
call, the same way `DEFAULT_MIN_R` was, see section 6).

Where does `p_model` come from? Not a one-off guess. The thesis writer
already produces calibrated scenario probabilities on every desk tick:
`contracts/strategies/models.py:403-414`'s `Scenario` model (`name`,
`probability`, `move_lo_pct`/`move_hi_pct`, `description`, `triggers`),
validated to sum to 1 (`contracts/strategies/models.py:508-512`). Section 4
below is the deterministic bridge from a thesis's scenario probabilities to
a `p_model` for a specific prediction-market question, and the eval that
has to clear before this gate is trusted with real money.

### 2.3 Resolution-date blackout

The contract-native replacement for `event_blackout.py`'s EIA/scheduled-event
windows. Two distinct reasons to block a fresh entry close to a contract's
own `closes_at` (Polymarket, `MarketOdds.closes_at`) or `close_time`
(Kalshi):

1. **Liquidity dries up near expiry.** The book thins and spreads blow out
   as a market nears resolution; entering there is a bad fill, not a
   trade.
2. **The trade stops being a mispricing bet and starts being a coin flip on
   the literal outcome.** The whole premise of the edge-floor gate (2.2) is
   that `p_model` has time to be right while the market is still pricing
   uncertainty. A contract hours from resolving has almost no time for that
   gap to close before the outcome just happens; buying it is a bet on the
   event, not a trade on the model being better calibrated than the
   market.

Propose: refuse a new entry inside 24 hours of `closes_at`/`close_time`
(wider than oil's 2h EIA window, because most of these contracts resolve on
a days-to-weeks cadence, not an hourly one; tune per market cadence, not a
single global constant). When a contract settles ON an event pathiel's
desks already blackout around (CPI, FOMC, jobs, OPEC+, the scheduled-event
calendar in `services/intel/data/scheduled_events.json`), reuse that
EXACT calendar and `event_blackout()`
(`services/strategies/event_blackout.py`) rather than building a second,
parallel one that can drift out of sync with the first.

### 2.4 Liquidity / spread check

pathiel already has the shape of this check, just tuned for a different
purpose. `fetch_kalshi` (`services/intel/sources/predictions.py:179`) drops
a market from the READ path entirely once `ask - bid > 0.9`, "near full
[0,1] spread == no real liquidity," and `fetch_polymarket`'s
`MIN_VOLUME_USD = 1.0` (line 45) is a "is this worth even showing the
thesis" floor. Both thresholds are deliberately loose because the read path
only has to decide whether a market is worth mentioning as context.
Execution needs a much tighter version of the same two checks, because
placing real size into a market is a different bar than reading its quote:

- **Spread cap**: refuse an entry if the live bid-ask spread (in
  probability points) exceeds a floor meaningfully tighter than
  `predictions.py`'s 0.9 read-path cutoff, propose 0.03-0.05 (3-5 cents),
  configurable per market cadence (daily-resolving markets tolerate a
  wider spread than hourly ones).
- **Depth check**: before sizing, read the live book (Polymarket's public
  `/book`, Kalshi's order-book endpoint) and refuse or size down if the
  order would walk through more than some fraction (propose 20%) of the
  resting size at the touch, the same shape as pathiel's own
  `get_order_book` price-impact walk for perps.
- **Volume floor**: raise `predictions.py`'s `MIN_VOLUME_USD = 1.0`
  read-path floor by two to three orders of magnitude for the execution
  gate (propose $500-$1,000 in recent volume), since $1 of volume tells you
  a market exists, not that it is tradable in real size.

### 2.5 Correlated-market cap

The contract-native replacement for "one position per desk"
(`scripts/pathiel-mcp-server.py:4656-4661`, `bracket.desk_coin_held`). A
desk buying YES across five adjacent strikes of the same Kalshi WTI
price-range ladder (`KXWTIW`), or across three dated variants of the same
Polymarket event (the Iran-ceasefire event already carried multiple
dated/strike markets under one `event.slug`,
`services/intel/sources/predictions.py:65-67`'s own comment about that
exact event), is not five or three diversified bets. It is one
concentrated bet on the same underlying move, sized five or three times
over.

Gate: group markets by their venue's own event grouping (Polymarket
`event.slug`, Kalshi `series_ticker`) and apply the SAME ceiling from 2.1
to the SUM of premium at risk across every market in that group, not per
market. This is a direct structural port of the "one slot, one desk"
pattern pathiel already enforces for perps (`desk_share.py:95-99`'s
`slots()`), just keyed on event/series instead of coin.

## 3. Service layout

New top-level service, `services/predictions_exec/`, one concern (placing
and managing prediction-market orders), independent of the read-only
`services/intel/sources/predictions.py` it consumes and independent of
every perp desk. Matches CLAUDE.md's services-first rule: a separate
Claude session could build this without touching `pathiel/agents/bracket.py`
or any perp desk file.

```
contracts/predictions/
  models.py        # PmMarketSnapshot, PmOrderRequest, PmOrderResult, PmPosition, PmFill
  README.md        # the contract both sides import; versioned like contracts/risk, contracts/strategies
  fixtures/        # golden JSON, _build.py, same pattern as contracts/strategies/fixtures
  tests/test_contract.py

services/predictions_exec/
  __init__.py
  README.md
  kalshi_client.py      # Ed25519/RSA request signing, order create/cancel/read -- M1
  polymarket_client.py  # L1 EIP-712 derive -> L2 HMAC order submission -- M2
  risk_gates.py         # the five gates from section 2, pure functions, no I/O
  order.py              # lifecycle: dry_run sizing -> gates -> placement -> journal write
  ownership.py          # client_order_id-tagged journal, same shape as pathiel/agents/ownership.py
  edge.py                # p_model vs p_market, net of fees -- section 4
  tests/                 # gate tests: fee math, edge math, blackout windows, event grouping. <2s, no network.
  evals/                 # paid: paper-harness-vs-known-resolution checks. Section 4/5.
```

`contracts/predictions/models.py` reuses `MarketOdds`'s venue identity
(`contracts/strategies/models.py:331-338`,
`venue: Literal["polymarket", "kalshi", "other"]`) rather than inventing a
second ID scheme, so a market the read path already surfaced and a market
the execution path is about to trade are provably the same row, not two
coincidentally-similar ones.

### MCP tools

Named in the existing dense, one-tool-one-job style
(`docs/overhaul/MCP.md`'s table is the reference for density and the
"Refused:... / Returns:..." shape). Every one of these that can move money
carries `dry_run` (default `true`, matching the hard rule that this doc
ships no live-order code and the live-money convention `place_bracket`
already sets with its own `dry_run` flag):

| tool | safety | description (style match to `docs/overhaul/MCP.md`) |
|---|---|---|
| `pm_market` | read-only | One market's live book, spread, recent volume and `closes_at`/`close_time`, the exact inputs gates 2.3 and 2.4 need. No key. |
| `pm_place_order` | LIVE MONEY (dry_run defaults true) | One order on one venue/market: side (yes/no), price, size. Runs every gate in section 2 before sizing; `dry_run=true` (default) returns the sizing and every gate's verdict without sending anything. Refused, with nothing sent: net edge under the floor (2.2), premium cap or correlated-market cap exceeded (2.1/2.5), inside the resolution blackout (2.3), spread or depth fails the liquidity check (2.4), or the event is not EDGE-graded yet (section 4). Returns `{ok, placed, gates: {...}, reason}` on refusal, matching `place_bracket`'s own refusal shape. |
| `pm_positions` | read-only | Open prediction-market positions across venues: entry price, current price, unrealized P/L (just `current - entry` per contract, no leverage math), and which desk/budget owns each. |
| `pm_cancel` | LIVE MONEY | Cancel one resting order by id. Same ownership guard as pathiel's `cancel_order`: refuses to touch an order this desk did not place. |
| `pm_sweep` | LIVE MONEY | Housekeeping analog of `sweep_brackets`: cancel stranded resting orders left behind after a resolution or a manual exit. |

### Key handling

Follows the exact pattern `docs/SECRETS.md` already documents for
`HYPERLIQUID_PRIVATE_KEY`: new rows in the SAME `.env.local` table, same
file, same gitignore, same "never read it in code outside the client
module" discipline. This doc does not read, print or reference the
contents of `.env.local` or `~/.pathiel` anywhere, and proposes NAMES only:

- Kalshi: `KALSHI_API_KEY_ID` (the key id from account provisioning) and
  `KALSHI_PRIVATE_KEY_PEM` or `KALSHI_PRIVATE_KEY_PATH` (the Ed25519 or RSA
  private key Kalshi shows once at creation), consistent with how the
  Hyperliquid key is already a PEM/hex string in that same file.
- Polymarket (if/when M2 happens): `POLYMARKET_PRIVATE_KEY` (the L1 Polygon
  wallet key, used locally only to derive or sign, exactly as
  `HYPERLIQUID_PRIVATE_KEY` is used locally only to sign) and
  `POLYMARKET_API_KEY` / `POLYMARKET_API_SECRET` / `POLYMARKET_API_PASSPHRASE`
  (the derived L2 credentials, cached so the L1 signature is not re-derived
  on every call).

Credential creation (deriving the L2 key, registering a Kalshi key pair) is
a setup-skill concern, the same role `pathiel:setup` already plays for the
Hyperliquid key, not something this doc or `predictions_exec` code does
inline.

## 4. From Polymarket odds to a trading signal, and the eval that must clear first

### What exists today

`services/intel/sources/predictions.py` fetches `MarketOdds` rows
(question, probability, volume, `closes_at`), read-only, no key, already
live. `services/intel/api.py:125` bundles them into `IntelPacket.odds`.
`services/thesis/prompt.py:502-509` renders them into the thesis prompt as
a flat bullet list:

```
PREDICTION MARKETS
- [id] polymarket: "Iran ceasefire by Nov 8?" -> 32%
```

That is the entire signal today: the LLM sees a market's implied
probability next to everything else (news, policy, positioning) when it
writes its own thesis. There is no comparison step. It is pure latent-space
judgment, which is fine for a human-readable thesis but is NOT, by
CLAUDE.md's own rule, something `pm_place_order` should ever act on
directly: "if the same question asked twice would produce the same correct
answer by definition, it's deterministic work... write the script."
Whether `p_model` beats `p_market` net of fees is exactly that kind of
question. It must be computed, not eyeballed.

### The bridge: edge.py

A new deterministic module, `services/predictions_exec/edge.py`, with one
job: for every `MarketOdds` row a desk's existing
`prediction_market_queries` already surface (no new fetch, no new query,
the read path in section 3 is unchanged), look up the thesis's own
`Scenario.probability` for whatever real-world event that market also
resolves on, and compute `edge`/`net_edge` exactly as in section 2.2. The
ONE judgment call in this whole pipeline, matching a given market's
question text to a given scenario's `description`/`triggers`, is the one
place this doc allows latent-space help, and even then it must route
through `services/llm` (shells out to local Claude Code per CLAUDE.md's
"LLM access" rule), never a hosted API, and its OUTPUT (which market maps
to which scenario) should itself be cached and reviewed, not re-derived
fresh on every tick for the same pair.

### The eval that has to clear before a live order

CLAUDE.md's rule: "every feature ships with a test suite AND an eval suite"
and this task's own instruction: paper-trade first, same discipline as
`scripts/paper_breakout.py`. Concretely:

1. `scripts/paper_predictions.py`, built the same way
   `scripts/paper_breakout.py` is built: log-only, no orders, no money,
   reads the live thesis journal and live `MarketOdds` (both already public
   reads, nothing new), replays the exact same `edge.py`/`risk_gates.py`
   functions the live path would call, and on a net-edge pass, opens a
   PAPER position, marks it every tick, and resolves it to $1/$0 when the
   venue settles (polled via the same public read). One JSON line per
   OPEN/RESOLVE, same convention as `breakout_a6.jsonl`, to
   `$PATHIEL_STATE_DIR/paper/predictions.jsonl`.
2. The resulting paper ledger is graded with the SAME labelling scheme
   `services/review/stats.py` already defines for real trade outcomes:
   `MIN_N_EDGE = 10`, `P_EDGE = 0.05`
   (`services/review/stats.py:35-37`), EDGE / LEAK / LEANING+ / LEANING- /
   FLUKE / NOISE
   (`services/review/stats.py:43-44`). Nothing new invented; this doc reuses
   the exact bar the rest of the repo already holds every other edge claim
   to, "n is small: an EDGE or LEAK needs at least 10 trades in one slice.
   Say LEANING, FLUKE or NOISE as the report does; never round them up"
   (`.claude/skills/review-trades/SKILL.md`).
3. **The hard gate**: `pm_place_order` refuses `dry_run=false` for any
   market/event group whose paper-ledger verdict is not EDGE (not LEANING,
   not FLUKE) with n >= 10, in code, not by operator discipline, the same
   way `place_bracket` refuses under 2R in code rather than trusting the
   caller to check first. A LEAK verdict (the model is reliably WRONG
   relative to the market) is logged and reported the same way
   `review-trades`' skill already treats a perp LEAK: a yes/no to Julien,
   never silently flipped into a NO-side trade, because "never feed the
   record to the thesis" (`feedback-continuous-learning` memory) applies
   here exactly as it does to perp review: the paper ledger feeds the gate,
   never the prompt.

This is the measurable outcome CLAUDE.md requires before anything ships:
the metric is "n resolved paper positions in a slice, net R, EDGE/LEAK/
LEANING/FLUKE/NOISE label," the trace is `predictions.jsonl` plus the
review-pipeline report, and nothing moves real money until that trace says
EDGE.

## 5. Build plan

Milestone 0 is serial and blocks everything else. M1-M3 are independent
once M0 lands and should run as separate worktrees in parallel, per
CLAUDE.md's "fan out by default." M4 is serial again (it wires M1 and M2
together). M5 is not a build step, it is a decision gate.

| Milestone | What | Effort | Depends on | Gate tests | Evals | Parallel? |
|---|---|---|---|---|---|---|
| **M0** | `contracts/predictions/` models, README, fixtures, contract test | 0.5 day | nothing | schema round-trip (`contracts/risk/tests/test_contract.py` pattern) | none | no, blocks M1-M4 |
| **M1** | `kalshi_client.py` (Ed25519 signing, order create/cancel/read) + `risk_gates.py` (all 5 gates) | 2-3 days | M0 | fee math vs. the 0.07/0.035 formula, edge math, blackout windows, correlated-market grouping, all offline, <2s | none yet (venue calls mocked) | yes, with M2 and M3 |
| **M2** | `polymarket_client.py` (L1 derive, L2 HMAC, order submit) | 2-3 days | M0 | same `risk_gates.py` suite, reused venue-agnostically once a `PmOrderRequest` exists | none yet | yes, with M1 and M3 |
| **M3** | `scripts/paper_predictions.py`, `edge.py`, review-pipeline reuse for the paper ledger | 2-3 days | M0 only (needs no venue credentials, public reads only) | paper-tick state-machine tests, no network | one paid eval: replay a known, already-resolved market and confirm the paper harness would have scored it right | yes, with M1 and M2, and can start FIRST since it needs no venue account |
| **M4** | MCP tools (`pm_place_order`, `pm_positions`, `pm_cancel`, `pm_market`, `pm_sweep`), `ownership.py` journal | 1-2 days | M1 AND M2 | handler tests against mocked clients, `dry_run` default-true pinned by a test | none | no, serial after M1+M2 |
| **M5** | Flip `dry_run` default to allow `false` for a given market/event group | n/a, a decision | M3's paper ledger showing EDGE (n>=10, p<0.05) for that group | n/a | n/a, this IS the eval result | no, this is Julien's call, Confusion Protocol |

Total build effort before any real order is possible: roughly 8-11 engineer-days
across M0-M4, most of it parallelizable to 3-4 calendar days with three
worktrees running M1/M2/M3 at once. M5 has no calendar estimate; it happens
when the eval says so.

Recommend starting M3 first and alone if a one-worktree start is preferred:
it needs no venue account, no key, no legal sign-off, and it is the one
piece that produces the actual go/no-go evidence for everything else.
Kalshi account opening and key provisioning (needed for M1) can run in
parallel with M3's development since they are an operator action, not a
coding task.

## 6. Open questions for the operator

1. **Does Polymarket US's own order book actually list the geopolitical/
   macro markets the oil desk already tracks** (Iran ceasefire, Strait of
   Hormuz) **, or only the sports/politics contracts it launched with?**
   Not resolved by this research. Needed before M2 is worth building against
   Polymarket US instead of staying Kalshi-only.
2. **Does Julien already have, or intend to open, a funded Kalshi account
   (and/or Polymarket US account)?** Unlike the Hyperliquid wallet-only
   flow, both venues require real identity verification (KYC) on a named
   account, individual or entity. Whose name goes on it is a question for
   counsel, not this doc, and it gates M1's start.
3. **Is there a Kalshi (or Polymarket) demo/sandbox environment** to test
   real order flow against before using funded money, the way Hyperliquid's
   testnet lets `PATHIEL_NETWORK=testnet` exercise the whole live code path
   with no real funds? Not confirmed in this research. If none exists, M1's
   gate tests stay fully mocked until M5, and the first real order IS the
   first live-venue test, which changes how conservative M5's rollout
   should be.
4. **What net-edge floor should gate 2.2 actually use to start?** This doc
   proposes 3-5 probability points, net of fees, as a starting point, the
   same way `DEFAULT_MIN_R = 2.0` was a starting point someone had to pick.
   Julien's call.
5. **Does prediction-market execution get its own line in
   `desk_equity_share`** (e.g. a standalone `"predictions": 0.02` slice, its
   own desk-like entity for sizing purposes), **or is it a sub-allocation
   inside an existing desk's own slice** (e.g. the oil desk spends part of
   ITS slice on an Iran-ceasefire hedge)? This decides whether
   `desk_share.py` and `desk_leverage.py` need a new desk id registered or
   whether `risk_gates.py`'s 2.1 reads an existing desk's slice directly.
6. **Should the correlated-market cap (2.5) span both venues**, grouping a
   Kalshi WTI range contract and a Polymarket Iran-ceasefire contract
   together as "oil geopolitical risk" even though they are two different
   question texts on two different exchanges, **or should each venue's own
   event grouping stay independent for now**? Cross-venue grouping is
   harder to do deterministically (no shared event id) and may need the one
   latent-space matching step from section 4 extended further than this
   doc currently proposes.
7. **When a prediction-market position resolves to $1 or $0, should it feed
   the SAME `services/review` ledger and EDGE/LEAK reporting the perp desks
   already get**, with the same "a LEAK goes to Julien as a yes/no, same
   day" protocol (`feedback-leaks-act-fast` memory), **or does it need its
   own, separate reporting loop**? Affects whether `review-trades`'s
   existing skill just starts seeing these trades or whether a sibling
   skill is needed.

---

Sources used in this doc (fetched directly during research, dated where the
source states a date; see the two research reports this doc was built from
for the full citation list and every flagged conflict/unconfirmed item):

- Polymarket CLOB API, auth, fees, rate limits: `docs.polymarket.com`
  (trading/orders/overview, trading/orders/create, api-reference/authentication,
  trading/fees, api-reference/rate-limits, api-reference/trading-rate-limits,
  api-reference/clients-sdks), `programs/fee-and-rebate-changes-november-2026`.
- Polymarket geo/regulatory: `polymarket.com/trust`; Reuters via
  thestar.com.my (Oct 28, 2026); CFTC trading-organizations registry;
  CFTC Release 9023-24 (Dec 17, 2024); theblock.co (Jul 10 / Oct 19, 2026);
  Finance Magnates (Nov 3, 2026); frontofficesports.com (Dec 3, 2025).
- Kalshi trade API, auth, fees, rate limits: `docs.kalshi.com`
  (api-reference/orders/create-order-v2, getting_started/api_keys,
  getting_started/quick_start_authenticated_requests, fix/authentication,
  getting_started/fee_rounding, getting_started/rate_limits);
  `kalshi.com/docs/kalshi-fee-schedule.pdf` (Sept 22, 2022, primary);
  `help.kalshi.com` (fees, market-specific-fees, order-types).
- Kalshi regulatory/litigation: CFTC Release 8302-20 and amended order
  (Jan 17, 2025); theblock.co (Apr 6 / Aug 28 / Jul 14, 2026);
  news.bloomberglaw.com (Ohio/Tennessee ruling; NJ cert petition);
  covers.com (Nov 18, 2026); gambling.com (NJ SCOTUS petition);
  sccgmanagement.com (Nov 7, 2026, CFTC geofencing directive).

This doc makes no legal conclusions from any of the above. Every "flag for
counsel" item above is exactly that: a flag, not an answer.
