# W-Z1 — Polymarket as an alpha frontier (scoping, no build)

Lane Z, 2026-07-20. Scoping + feasibility only. No code touched, no live wiring.
Thesis under test: point the LLM-judgment edge (structural edge #1) plus tiny-size
invisibility (structural edge #2) at markets priced by judgment, not speed.
All external claims cited. Verdict at the bottom: **GO-IF** (shadow phase is GO now,
live phase gated).

## 1. Edge reality (2026): is Polymarket still beatable, and where

**Base rates are brutal.** A working paper covering 1.4M users / $20B volume /
70M trades (2022-2025) found ~71% of users lose money and the top 1% capture
~84% of all gains ([Globe and Mail](https://www.theglobeandmail.com/investing/personal-finance/article-the-investors-profiting-from-prediction-markets-the-top-1-of-course/),
[Studocu summary](https://www.studocu.com/row/document/ankara-universitesi/matematik-i/who-wins-and-who-loses-in-prediction-markets-insights-from-polymarket-analysis/161619696)).
Near-zero-sum, winner concentration like poker. Overall calibration is good:
a contract at p resolves YES ~p% of the time; US-politics mean absolute
calibration error ~1.2pp ([Polyburg](https://polyburg.com/polymarket-prediction-accuracy),
[Prediction News](https://predictionnews.com/news/study-reveals-nuance-behind-polymarket-90-percent-accuracy-rate/)).
Headline markets are efficient. We will not out-forecast the aggregate on
tentpole events.

**The gabagool22 datapoint is NOT a judgment edge.** $788K profit / 99.5% win
rate / 24.5K markets in ~3 months since Oct 2025, built on microstructure:
buying YES and NO legs asymmetrically when one side prints cheap so combined
cost < $1.00 (e.g. YES @ 0.517 + NO @ 0.449 = 0.966), thousands of times, mostly
on high-velocity crypto markets ([0xInsider breakdown](https://0xinsider.com/research/gabagool22-polymarket-trader-analysis),
[AInvest](https://www.ainvest.com/news/algorithmic-arbitrage-crypto-prediction-markets-exploiting-binary-mispricings-polymarket-2512/)).
That lane is latency arbitrage, now bot-saturated ([Yahoo Finance](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html))
and directly targeted by the 2026 fee change (below). An NBA-markets study found
single-market arb episodes persist a median 3.6 seconds ([arXiv 2605.00864](https://arxiv.org/html/2605.00864v1)).
Do not copy him; that is a speed game we lose by construction.

**Where the room is soft (documented, not vibes):**

| Pocket | Evidence | Fit for us |
|---|---|---|
| Political/geopolitical price compression toward 50% at long horizons (reversed longshot bias: favorites underpriced) | Domain calibration study, mean slope 1.31 on Polymarket politics; bilateral partisan flow pulls prices to 50% ([arXiv 2602.19520](https://arxiv.org/pdf/2602.19520)) | GOOD: structural, judgment-priced, fee-free category. But harvesting it means buying favorites at 80-90c (risk 85 to win 15) with days-weeks lockup |
| Niche/culture/one-off markets: worst calibration (celebrity ~72% vs politics 85%) | [Polysyncer accuracy study](https://www.polysyncer.com/blog/polymarket-prediction-accuracy) | GOOD in principle, but spreads 5-10c and books held by 1-3 wallets eat the mispricing ([arXiv microstructure study 2604.24366](https://arxiv.org/html/2604.24366v1), [Medium liquidity trap](https://medium.com/coinmonks/the-hidden-liquidity-trap-in-long-dated-polymarket-bets-95fe05d84a9b)) |
| Mid-tail news/geopolitics: real order flow but marginal price-setter is retail/partisan, not a quant desk. ~600 active geopolitics markets alone ([Laika Labs](https://laikalabs.ai/prediction-markets/best-polymarket-markets-to-trade-in)) | Median quoted half-spread on central-decile prices ~200bps (wide enough that pros do not camp there, tight enough to cross on a 10pp edge) | **THE TARGET.** Resolves in days-weeks, fee-free category, LLM synthesis of filings/news is exactly the pricing input |
| 5/15-min crypto up-down markets | 310 markets, pure latency lane | AVOID: bots + the new 1.75%-peak taker fee exists specifically to tax this |

**LLM-vs-market evidence.** Frontier LLM forecasters now beat the human crowd
but not superforecasters: o3 Brier 0.135 vs crowd 0.149 vs superforecasters 0.121
([arXiv 2507.04562](https://arxiv.org/html/2507.04562v3)); ForecastBench shows
SOTA ~0.136 solo vs public 0.121 / superforecasters 0.096, with LLM-superforecaster
parity extrapolated to late 2026 ([ForecastBench / Metaculus benchmark](https://api.emergentmind.com/topics/metaculus-benchmark)).
Liquid Polymarket prices sit at roughly superforecaster level, so on liquid
markets the LLM is the fish. But PolyBench-style backtests report simulated
profit from trading only large model-vs-market divergences ([arXiv 2604.14199](https://arxiv.org/html/2604.14199v1)),
and Halawi et al. reached crowd-parity with retrieval ([NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/file/5a5acfd0876c940d81619c1dc60e7748-Paper-Conference.pdf)).
The viable shape: trade nothing by default; act only when LLM prob diverges from
mid by more than spread + fees + a margin, on mid-tail judgment markets.

**Where we LOSE (named plainly):**
1. Any liquid headline market: the price already contains superforecasters and
   insiders. A big divergence there means WE are wrong.
2. Anything fast: crypto windows, in-game sports. Latency bots own it.
3. Resolution-spec traps: the LLM forecasts the event, the market resolves the
   words. Ambiguous specs regularly burn semantic traders.
4. Oracle risk: UMA token-voting has been gamed. March 2025 Ukraine-minerals
   market resolved YES on a non-signed deal via a whale with ~25% of active
   voting power; no refunds ([CoinMarketCap](https://coinmarketcap.com/academy/article/polymarket-reports-unprecedented-governance-attack-by-uma-whale-on-bet-resolution)).
   1,150+ disputed markets in 2026 already, past the full-2025 total; a $60M
   MicroStrategy market sat disputed in queue ([The Defiant](https://thedefiant.io/news/markets/usd85m-polymarket-dispute-over-strategy-s-may-bitcoin-sale-puts-uma-s-token-voting-oracle-on),
   [Forbes](https://www.forbes.com/sites/digital-assets/2026/04/30/inmates-taking-the-asylum-polymarkets-16m-clavicular-bet/)).
   Being right on probability and losing on resolution is a real tail.
5. Adverse selection in thin books: resting orders get picked off on news by
   faster flow; crossing costs 2-10c.
6. Longshot buying: the classic retail death. The documented bias points the
   other way (favorites cheap); the temptation to buy 5c lottery tickets must be
   structurally blocked in any prompt/filter.

## 2. API feasibility

Fully programmatic, mature, free to read. Sources: [official docs](https://docs.polymarket.com/api-reference/clients-sdks),
[docs llms.txt surface map](https://docs.polymarket.com/llms.txt),
[py-clob-client](https://github.com/Polymarket/py-clob-client) /
[py-clob-client-v2 on PyPI](https://pypi.org/project/py-clob-client-v2/),
[AgentBets rate-limit guide](https://agentbets.ai/guides/polymarket-rate-limits-guide/),
[dev.to CLOB deep dive](https://dev.to/mateosoul/polymarket-api-authentication-and-order-execution-clob-deep-dive-for-trading-bots-1gjg).

| Capability | How | Auth |
|---|---|---|
| (a) All open markets + odds | Gamma API: markets/events by id/slug/token, keyset pagination, tags, historical prices, live volume | none |
| (a) Order books / BBO / mid / spread / trades / klines | CLOB API market-data endpoints; WebSocket market channel for book/price/trade stream | none for reads |
| (b) Place/cancel orders | CLOB: create up to 15 orders/request, cancel single/batch/all. `py-clob-client` `create_and_post_order` | L1 EIP-712 wallet signature to derive keys, then L2 HMAC API creds |
| (c) Resolutions | Market objects carry closed/resolved state + winning outcome (Gamma); settlement on-chain via conditional token framework + UMA oracle | none |
| Positions/holders/leaderboard/OI | Data API | none for public views |

- **Chain/collateral:** Polygon (chain_id 137); collateral pUSD, an ERC-20 backed
  1:1 by USDC ([PyPI v2 docs](https://pypi.org/project/py-clob-client-v2/)).
- **Fees (changed in 2026):** taker fees rolled out Jan-Mar 2026. Crypto up-down:
  `shares x 0.07 x price x (1-price)`, peak $1.75/100 shares at 50c; makers 0%
  plus ~20% rebate of taker fees; by Mar 30 2026 most categories covered, but
  **geopolitics/world events remain fee-free** ([Market Math](https://marketmath.io/blog/polymarket-fees-explained),
  [Start Polymarket fee table](https://startpolymarket.com/learn/polymarket-fees/)).
  Verify per-market via the CLOB fee-rate endpoint before any live math.
- **Min size / tick:** per-market; typically 1 share (~$1), tick 0.01 or 0.001;
  both retrievable via API ([Parlay dev guide](https://www.parlay.run/polymarket-api)).
  $1 minimums fit our tiny-size edge perfectly.
- **Rate limits:** Cloudflare general ~15,000 req/10s; CLOB ~9,000/10s;
  POST /order 3,500/10s burst, 36,000/10min sustained ([AgentBets](https://agentbets.ai/guides/polymarket-rate-limits-guide/)).
  Orders of magnitude above our needs; a daily Gamma scan of 30K markets is a
  few hundred paginated calls. No HL-style 429 pressure.

## 3. Access / legal reality (the blocker section)

Two separate platforms now exist. Do not conflate them.

**International CLOB (the one py-clob-client talks to): order placement is
BLOCKED from the US.** The geoblock endpoint checks IP on every order; US is on
the close-only/blocked list along with UK, France, Germany, Belgium, Canada
provinces, ~35 jurisdictions total ([official geoblock docs](https://docs.polymarket.com/api-reference/geoblock),
[Datawallet country list](https://www.datawallet.com/crypto/polymarket-restricted-countries)).
Geoblocked since the 2022 CFTC settlement. Evading via VPN is a ToS violation
with AML/sanctions exposure on a KYC-trending platform ([MEXC news](https://www.mexc.com/news/1115421));
not a path we take. **Reads are not the restricted operation**: order placement
is what gets rejected; Gamma/CLOB market data and resolutions are public. The
entire shadow phase is therefore unblocked.

**Polymarket US (QCX LLC, CFTC-licensed DCM): legal and API-enabled.**
Launched Dec 3 2025 after the QCX acquisition and CFTC Amended Order of
Designation (Nov 2025) ([PR Newswire](https://www.prnewswire.com/news-releases/polymarket-receives-cftc-approval-of-amended-order-of-designation-enabling-intermediated-us-market-access-302625833.html));
waitlist removed May 2026, open in 41 states + DC ([Start Polymarket US page](https://startpolymarket.com/countries/united-states/),
[LOCALS Insider](https://localsinsider.com/prediction-markets/polymarket/)).
Requirements: full KYC (government ID, SSN, proof of residency, live selfie)
via the iOS app; USD settlement through FCMs, not crypto. API access for KYC'd
users: 23 REST + 2 WebSocket endpoints, Ed25519 key signing, official Python
SDK; KYC must be complete before API keys can be generated; institutional FIX
gateway via onboarding@qcex.com ([TradingVPS US guide](https://tradingvps.io/polymarket-us-guide/),
[QuantVPS](https://www.quantvps.com/blog/polymarket-us-api-available)). This is
a DIFFERENT integration from py-clob-client (different auth, different
endpoints). Catalog on US includes sports, politics, geopolitics, crypto,
economy, culture.

**Blockers, plainly:**
1. Live trading requires operator action: Polymarket US KYC (SSN + iOS app), an
   out-of-band human step. Or relocate jurisdiction, which we are not doing.
2. A fresh CFTC probe opened June 2026 ([tech-insider status page](https://tech-insider.org/prediction-markets/is-polymarket-legal-in-the-usa/));
   regulatory ground can move under the US platform.
3. US platform docs do not yet confirm feature parity (market breadth, maker
   rebates) with international; must be verified against the live US catalog
   before the live phase.
4. Shadow phase: NO blocker. Public reads, no account, no capital.

Fallback if Polymarket US disappoints: Kalshi is US-legal with a mature public
trading API and overlapping event coverage. Out of scope here, but the shadow
harness below should be venue-agnostic in its schema so the same LLM pipeline
can grade against Kalshi prices too.

## 4. Validation methodology (shadow-ledger discipline, adapted)

The unit of record changes: perps books record entries/exits; here the claim is
**probabilistic**, so the ledger must grade both PnL and calibration.
`scripts/shadow_status.py` grades PnL episodes; probability scoring (Brier) is a
new standalone grader that lives with the research code, not a modification of
live scripts.

**Recorder design (paper only, PIT-honest):**
1. Daily scan via Gamma. Deterministic filters (this is deterministic space:
   scripted, not prompted): category in {geopolitics, politics, world news,
   econ/policy, culture}, resolution date 3-21 days out, volume in a mid-tail
   band (~$10K-$500K), quoted spread <= 6c, exclude crypto up-down and in-game
   sports, exclude any market whose resolution source is subjective/UMA-dispute-prone
   (no clear cited resolution source in the spec).
2. LLM forecast via local claude_cli (per repo rule: no hosted API). Prompt
   MUST mandate web search with cited sources; we already know the model fakes
   headlines unless ordered (news-wave lesson, 2026-07-11) - check envelopes.
   Output envelope: `{prob, confidence, resolution_spec_reading, sources[], rationale}`.
   The prompt must restate the market's resolution criteria verbatim and force
   the model to forecast THE SPEC, not the vibe of the event.
3. Record point-in-time: timestamp, market id/slug, bid/ask/mid, book depth at
   3 levels, LLM envelope, divergence = |LLM prob - mid|.
4. Paper-fill rule (the honesty rule): if divergence > threshold (pre-register
   10pp) AND direction survives the spread, log a paper position filled AT THE
   TOUCH (buy at ask / sell at bid), never at mid, sized $1 notional, fee per
   the market's live fee rate. One position per market. Also log the
   passed-on markets: they are the control set.
5. Grade on actual resolution (Gamma resolved state): net PnL per $1, plus
   Brier(LLM) vs Brier(market mid) on the identical set, plus
   divergence-bucket monotonicity (bigger divergence should mean bigger edge;
   if not, the "edge" is noise or spec-misreading).
6. Null: MC random-direction paper bets on the same markets at the same touch
   prices, 2000 iters, one-sided p on mean net PnL. Same discipline as W-Y.

**The slow-resolution problem, solved by selection not patience:** restrict to
<= 14-21 day resolution. Polymarket lists ~30K active markets; geopolitics
alone runs ~600, plus weekly politics/econ deadlines. A daily scan should
surface 5-15 qualifying divergences; expect **30-60 graded outcomes/month**.
Pre-registered gate needs n >= 150 resolved, so validation runway is ~8-12
weeks. Interim early-warning proxy (never a validation gate): does the market
mid migrate toward the LLM prob at T+24h/T+72h. Directional check only; edges
that only show in migration and never in resolution PnL are microstructure
mirages.

**Failure modes the grader must catch:** UMA-disputed resolutions get tagged
and reported both included and excluded (if the edge only exists including
disputed markets, it is oracle luck, not judgment); markets that resolve
against unambiguous reality get flagged as oracle-risk events, not model error.

## 5. Build plan + expected edge + verdict

**Minimal shadow-first build** (order of days, all under `services/polymarket_scout/`
+ ledger jsonl; NOTHING touches the live loop, no `_ACTIVE_CLAIM_BOOKS` entry
because it never executes):

| Step | What | Effort |
|---|---|---|
| 1 | `fetch_markets.py`: Gamma scan + deterministic candidate filter, cache to jsonl | ~half day |
| 2 | `forecast.py`: claude_cli envelope per candidate (web-search-mandated), 5-15 calls/day | ~half day |
| 3 | `record.py`: PIT snapshot + paper-fill at touch to `shadow_ledger/polymarket_judgment.jsonl` (new book, shadow-only) | ~half day |
| 4 | `grade.py`: resolution poller + net-PnL + Brier-diff + divergence buckets + MC null; standalone, does not modify `shadow_status.py` | ~1 day |
| 5 | Cron the scan+grade into the existing daily 09:15 autonomous cycle | trivial |
| 6 (gated) | Polymarket US integration (Ed25519 auth, new SDK) + live $1-5/market | only after the gate |

**Pre-registered live gate:** n >= 150 resolved paper positions, mean net EV
>= +3%/position at touch prices, MC p < 0.05, Brier(LLM) < Brier(mid) on the
traded subset, divergence-monotonic, edge survives excluding disputed
resolutions, AND operator has completed Polymarket US KYC. All seven or no
live dollar.

**Expected edge, honestly.** On liquid markets: zero or negative; the price is
the superforecaster. On the divergence-selected mid-tail: literature supports
low single digits net. Plausible range **+1% to +4% per resolved position net
of spread and fees**, on 30-60 positions/month, high variance, with an
oracle-risk fat tail that no forecasting skill mitigates. Hold times 3-14 days,
no leverage, capital locked to resolution. On deployed capital that is roughly
perps-comparable per month but cycles slower. If the shadow Brier gap comes
back near zero, the honest conclusion is that 2026 Polymarket mid-tail already
prices at LLM level and we close the lane, same as W-Q/W-R.

**Capital/liquidity ceiling.** At our size ($122 total equity, a $30-60 sleeve
at most) the ceiling is irrelevant: mid-tail books absorb $1-5 orders
invisibly, and tiny size is exactly the niche real capital cannot bother with.
The strategy as designed saturates somewhere in the $10K-100K range where
mid-tail depth runs out. The binding constraint is OUR capital: dollars locked
in 1-3 week binaries are dollars not margining perps books. That contention is
an operator sizing decision at gate time, not now.

## Verdict: GO-IF

- **GO now** on the shadow phase: zero access blocker (public reads), zero
  capital at risk, ~2-3 days of build, 8-12 week runway to a clean
  VALIDATED/REFUTED with our standard discipline. It directly tests structural
  edge #1 against a new venue for the cost of a small research build.
- **IF** for live: the seven-condition gate above, dominated by (a) the shadow
  edge actually surviving touch prices + fees, and (b) the operator KYC step on
  Polymarket US (SSN, iOS app), plus the June 2026 CFTC probe not having moved
  the ground.
- **NO-GO** permanently on: crypto up-down/latency lanes, copying gabagool22
  (microstructure, not judgment), longshot buying, and any VPN path to the
  international book.
