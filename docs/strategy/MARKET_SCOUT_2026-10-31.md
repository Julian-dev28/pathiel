# Market scout: could the oil desk's rules replicate elsewhere? (2026-10-31)

Operator: Brent works -- which OTHER markets could run the same desk-tick
rules (`desk_tick_sim.live_params()`: market entry inside the 24h range to
pay 2R, stop 1.3xATR14 beyond the edge, target 0.5% inside the far edge,
20x capped at the venue max, 15m ticks), long or short? Named candidates:
`xyz:SILVER` (considering a short), `xyz:DIESEL` (thought to track Brent),
`io:ANT`, `io:OPENAI` ("lots of interlinked news"). References:
`xyz:BRENTOIL`, `xyz:CL`, `xyz:GOLD`, `xyz:NATGAS`.

**Answer: silver is the one real candidate, and the data supports shorting
it over this window -- but the window may be a pullback inside a bigger
uptrend, which only a thesis can tell apart.** Nothing else here clears the
bar: WTI is too correlated with Brent to be a second bet, gold/nat gas show
no consistent edge, diesel is too thin and costly to trade at any size, and
both private-company perps (Anthropic, OpenAI) show no edge and carry a
market-structure risk oil doesn't have.

Built by `services/strategies/evals/market_scout.py`
(`python3 -m services.strategies.evals.market_scout`), gate-tested in
`services/strategies/tests/test_market_scout.py`. Live numbers pulled
2026-10-31 06:33 UTC from the venue's public, read-only `/info` endpoint
(no key). No pathiel MCP tool was called; nothing live changed.

## Two of the four named candidates don't exist under that name

Checked against the venue's own `perpDexs` / `metaAndAssetCtxs` for
dex=xyz and dex=io:

| Operator named | Exists? | Used instead | Why |
|---|---|---|---|
| `xyz:SILVER` | yes | -- | -- |
| `xyz:DIESEL` | **no** | `xyz:HO` | NY Harbor ULSD / heating oil -- the standard diesel-proxy futures ticker (CME's own crack-spread convention: CL/HO) |
| `io:ANT` | **no** (on `io`) | `io:ANTH` | `io` has `ANTH` (Anthropic's pre-IPO valuation perp); `ANT` exists but on `xyz`, and is Ant Group (Alibaba-affiliate fintech) -- a different company entirely |
| `io:OPENAI` | **no** | `io:OAI` | `io:OAI` is OpenAI's pre-IPO valuation perp |

Aside: `xyz:ANT` (Ant Group) is real and tradeable (10x max, $21M/day volume,
$41M OI) but listed 2026-10-26 -- 5 days of history, nowhere near enough for
a backtest. Not scouted further here.

## Ranked table (best candidate first)

Tradeability and behaviour are live snapshots (2026-10-31 06:33 UTC).
Backtest is the live rules with the stop rescaled to each coin's own mean
1h ATR14 x 1.3 (`next_trade.stop_buffer_for`'s non-Brent rule) and leverage
capped at the venue max; no thesis exists for any of these markets, so
"ctrl" columns are the always-long / always-short / 20-day-trend-sign
controls -- the same mechanics-only question Brent's own "no thesis" rows
in `OIL_STRATEGY.md` answer for Brent. $64 start, 50-day window (30-day for
`xyz:HO`, only market with under 50 days of history).

| Market | Venue max lev | 24h vol | OI | Funding (ann.) | ATR14% | Slip $2.5k/$25k (buy, bps) | Lev-gate pass | Corr. vs Brent | Best ctrl, 50d (series) | Both halves positive? | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **xyz:BRENTOIL** (Brent) | 20x | $92M | $213M | 5.5%* | 0.59% | 0.4 / 0.4 | 92% | 1.00 | +4.63%/d (long) | no (-0.48/+10.10) | *(live desk -- the baseline)* |
| **xyz:SILVER** | 25x | $151M | $208M | 5.5%* | 0.51% | 0.4-0.8 / 1.8-2.0 | 99% | -0.31 | **+3.63%/d (short)** | **yes** (+2.86 / +4.40) | **needs a thesis replay first** |
| xyz:CL (WTI, ref) | 20x | $160M | $199M | 5.5%* | 0.61% | 0.5 / 0.5-1.4 | 87% | 0.94 | +0.72%/d (long) | no (-6.05/+8.09) | no (same bet as Brent) |
| xyz:GOLD (ref) | 25x | $70M | $308M | 5.5%* | 0.27% | 0.1 / 1.4 | 100% | -0.27 | +0.09%/d (short) | no (+2.37/-2.13) | maybe |
| xyz:NATGAS (ref) | 10x | $7M | $11M | 5.5%* | 0.52% | 1.4 / 4.7 | 100% | -0.06 | -3.50%/d (short) | no | no |
| xyz:HO (Diesel/ULSD) | 10x | $1.5M | $2.8M | 153-179% | 1.09% | 52.7 / 139.5 | 84% | 0.52 | -0.15%/d (long) | n/a (30d only) | no |
| io:ANTH (Anthropic) | 15x | $25M | $60M | 15.6-15.8% | 0.54% | -1.0 to -5.4 / 3.0-6.8 | 95% | -0.10 | -0.17%/d (short) | no | no |
| io:OAI (OpenAI) | 6x | $4.4M | $7.0M | 3.3-3.8% | 0.77% | 2.0-4.5 / 8.7-10.0 (often unfilled) | 100% | -0.37 | -1.12%/d (short) | no (-1.50/-0.75) | no |

\* Five of the eight markets show the exact same 0.00000625/hr funding
(5.5% annualized) -- this is Hyperliquid's platform-wide floor rate on a
quiet market, not an organic signal; it showed up identically on dozens of
unrelated HIP-3 tickers checked while finding the real names above. Diesel,
Anthropic and OpenAI's funding IS differentiated (and diesel's is punishing).

Full numbers (tradeability, behaviour, every window/series/half) in
`/tmp/market-scout/report.json` from a live run; re-run
`python3 -m services.strategies.evals.market_scout` to refresh.

## Behaviour detail

| Market | Daily range (median) | Trend persistence (5d) | London-NY session share |
|---|---|---|---|
| xyz:BRENTOIL | 3.10% | 55% | 66% |
| xyz:SILVER | 2.62% | 44% | 61% |
| xyz:CL | 3.33% | 58% | 68% |
| xyz:GOLD | 1.45% | 45% | 61% |
| xyz:NATGAS | 2.95% | 58% | 73% |
| xyz:HO | 5.17% | 33% | 62% |
| io:ANTH | 2.66% | 52% | 63% |
| io:OAI | 3.62% | 44% | 65% |

Nothing here clears 60% trend persistence (Brent itself is only 55%) --
none of these markets hands the daily-sign control a free lunch; the
desk's edge on Brent has always been the thesis, not the coin's own
momentum, and that will be true everywhere else too.

## The three questions, answered directly

**Is shorting silver supported by the data?** Mechanically, yes, and more
cleanly than anything else scouted: `always_short` made +3.59%/day over the
last 30 days and +3.63%/day over 50, positive in BOTH halves of the 50-day
window (+2.86% then +4.40%/day) -- `always_long` over the same 50 days lost
-11.50%/day and hit a 99.9% max drawdown, effectively wiping the account.
The 20-day trend-sign control agrees (+2.42%/day, also positive both
halves) -- more consistent across the two halves than Brent's OWN 50-day
no-thesis control ever was (-0.48%/day then +10.10%/day: it needed the
second half's rally to look good at all). Tradeability is Brent-grade:
25x venue max, $151M/day volume,
0.8bps to fill $2.5k, 2.0bps for $25k, a 1.3-ATR stop clears the 20x-style
leverage gate 99% of days. Silver is also NOT just a lever on the oil desk
(-0.31 correlation with Brent) -- it would be a genuinely independent bet,
which is what the operator asked for.

**The catch**, and it is a real one: a web check on silver's 2025-2026
drivers (Fed real yields, the gold-silver ratio, industrial demand, a
reported fifth straight year of supply deficit) describes a BULLISH
multi-month story -- record prices, ETF inflows, UBS and others calling for
further upside. The 50-day window this backtest ran on is far more likely
a pullback/correction inside that bigger uptrend than a trend reversal. A
blind always-short bet is betting the correction continues; the operator's
own silver thesis needs to say explicitly which of those two it believes,
because the mechanics will happily compound whichever side is fed to them,
right or wrong -- exactly the lesson from Brent's own history
(`OIL_STRATEGY.md`: "the edge is the thesis, not the market").

**Does diesel actually track Brent, and would trading it double the same
bet?** No -- correlation is 0.52, real but moderate, nowhere near WTI's
0.94 (WTI really is close to the same bet as Brent; diesel is not). The
research backs this up: diesel/ULSD cracks move on their own refining-
margin dynamics (refinery outages, East Coast distillate inventories,
winter heating-season seasonality October-February) that can run against
crude itself. So diesel would be real diversification, not a double-bet --
**but it isn't tradeable at a sane cost right now**: $1.5M/day volume (60x
thinner than Brent), 52.7bps to fill just $2.5k and 139bps for $25k (a
round trip at that size alone would burn well past the 2R the desk requires
to even take a trade), and 150-180% annualized funding. The 30-day backtest
(the only window its listing history allows) found no edge either way
(-0.15%/day long, -2.91%/day short) on top of that cost. Revisit if its
volume and depth grow; not actionable today.

**Are `io:ANT` / `io:OPENAI` tradeable at our size?** At today's ~$2,500
trade size, yes for both (`io:ANTH` -1 to -5bps -- a thin, sometimes-crossed
top of book, not a real negative cost -- `io:OAI` 2-4.5bps to fill
$2,500) -- but neither scales: at $25,000 `io:OAI`'s book doesn't even
fully fill on either side, and `io:ANTH`'s cost roughly triples. Research
on what these actually ARE matters more than the numbers: both are
synthetic contracts tracking a private company's IMPLIED valuation (per-
share price = an implied valuation, no equity, no vote, no IPO allocation,
HIP-3 dexes like Trade.xyz/Ventuals as the builders), priced off funding-
round news and secondary-market chatter rather than any scheduled calendar
-- a different, thinner kind of "news-driven" than oil's EIA/API/OPEC
cadence. These exact two names were shut down and delisted once already
(Ventuals pulled its OpenAI/Anthropic markets in June 2026; the `io:OAI` /
`io:ANTH` tested here are a later listing under a different builder) -- the
market itself is less durable than a commodity perp, on top of showing no
backtest edge in either direction (`io:ANTH` -0.17%/day short best case,
`io:OAI` -1.12%/day short best case, both at high drawdown on the wrong
side). Tradeable, not worth building on.

## Verdict per market

| Market | Verdict | Why (one line) |
|---|---|---|
| xyz:SILVER | **needs a thesis replay first** | Best mechanics of the eight (+3.6%/day short, robust both halves) and Brent-grade liquidity, but the backtest only proves the rules reward the right call -- the right call itself needs a thesis given silver's bullish multi-month backdrop |
| xyz:CL (WTI) | no | 0.94 correlated with the live Brent desk -- a second position here is leverage on the same bet, not diversification |
| xyz:GOLD | maybe | Deepest OI and tightest spread of the eight, genuinely independent of Brent (-0.27 corr), but no consistent directional edge this window -- same position Brent itself was in before a thesis existed |
| xyz:NATGAS | no | No edge either direction over 30 or 50 days; weather-driven whipsaw doesn't suit a trend-following stop/target |
| xyz:HO (Diesel/ULSD) | no | Real, moderate independence from Brent (0.52 corr) but too thin and costly to clear 2R at any size right now (52-139bps slippage, 150%+ annualized funding); no edge found either way on its only 30 days of history |
| io:ANTH (Anthropic) | no | No backtest edge, immature (2.5 months), priced off funding-round news rather than a schedulable catalyst, and this exact contract has been delisted once already under a different builder |
| io:OAI (OpenAI) | no | Weakest liquidity of the eight (6x max leverage, can't fill $25k), no backtest edge, same market-structure risk as Anthropic |

## Recommended next step

**30-day thesis replay on silver first** (`services/thesis/evals/replay.py`),
the top candidate by a clear margin. Gold is a distant, tradeability-driven
second (not an edge-driven one) worth a look only if silver's replay
doesn't justify a desk on its own. **No replay was run here** -- it needs
an LLM call (routed through local Claude Code per policy) and this task was
scoped to the free, deterministic scout only.

What a `DeskSpec` (`contracts/strategies/models.py`) for each would need
beyond Brent's shape:

- **Silver**: `instruments=[Instrument(coin="xyz:SILVER", label="Silver", role="primary")]`,
  `news_queries` covering silver ETF flows, industrial demand (solar/EV),
  Fed policy and the gold-silver ratio, `policy_topics` for Fed/tariff
  headlines, `catalyst_kinds` for `fomc`, `cpi_release`, and a silver-supply
  catalyst (Silver Institute reports play the role EIA/API/OPEC play for
  oil), `leverage_ladder` up to its 25x venue max (vs. Brent's 20x).
- **Gold** (if pursued as the distant second): the same shape with
  `xyz:GOLD`, news/catalysts centered on real yields, Fed policy and
  central-bank buying instead of industrial demand.

Neither was built (desk configs and `services/thesis/` were explicitly out
of scope for this task) -- this is a spec for whoever runs that replay
next, not a change made here.

## Sources (web research, qualitative section only)

- Silver drivers: [FXStreet -- silver's dual role as monetary and industrial metal in 2026](https://www.fxstreet.com/analysis/five-reasons-silvers-dual-role-as-a-monetary-and-industrial-metal-will-drive-prices-in-2026-202610211945), [Capital.com -- silver's dual engine: safe-haven flows meet industrial demand](https://capital.com/en-gb/analysis/silver-s-dual-engine-safe-haven-flows-meet-industrial-demand), [Business Today -- silver's 2025 price surge](https://www.businesstoday.in/amp/personal-finance/investment/story/silver-shines-in-2025-60-price-surge-amid-industrial-demand-fed-cuts-rupee-weakness-496797-2025-10-04).
- Diesel / crack spreads: [EIA -- Today in Energy, distillate crack spread seasonality](https://www.eia.gov/todayinenergy/detail.php?id=66764), [CME Group -- Understanding Crack Spreads](https://www.cmegroup.com/education/whitepapers/understanding-crack-spreads.html), [Enverus -- diesel's hundred-dollar crack, refinery outages and crude supply](https://www.enverus.com/blog/diesels-hundred-dollar-crack-refinery-outages-crude-supply-driving-pain-pump/), [Transport Topics -- diesel crack spread surges to record $106/bbl](https://www.ttnews.com/articles/diesel-crack-spread-record).
- Pre-IPO perps (`io:OAI` / `io:ANTH`): [KuCoin News -- Hyperliquid's HYPE rises amid pre-IPO perpetuals for SpaceX, Anthropic, OpenAI](https://www.kucoin.com/news/flash/hyperliquid-s-hype-token-rises-amid-pre-ipo-perpetuals-for-spacex-anthropic-and-openai), [KuCoin News -- Ventuals shuts down OpenAI and Anthropic markets on Hyperliquid](https://www.kucoin.com/news/flash/ventuals-shuts-down-openai-and-anthropic-markets-on-hyperliquid), [KuCoin News -- Anthropic's valuation surpasses OpenAI on Hyperliquid, reaching $934B](https://www.kucoin.com/news/flash/anthropic-valuation-surpasses-openai-on-hyperliquid-reaches-934b), [CryptoBriefing -- EntropyIO lists pre-IPO perps on Hyperliquid](https://cryptobriefing.com/entropyio-pre-ipo-perps-hyperliquid/).
