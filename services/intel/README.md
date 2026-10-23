# services/intel

Deterministic market intelligence for the desks (`services/strategies`). No
LLM call anywhere in this service — every source is fetch + parse +
normalize, with an injectable HTTP fetcher (`http_client.py`) and a fixture
captured live from the real API, per CLAUDE.md's latent/deterministic split.

```
build_packet(spec: DeskSpec, now: datetime | None = None) -> IntelPacket
```

`services/intel/api.py` fans every source out to its own thread
(`ThreadPoolExecutor`), bounded by a 12s wall-clock deadline
(`FETCH_DEADLINE_S`). A source still running past the deadline is recorded
in `IntelPacket.sources_failed` as `<key>:timeout_budget_exceeded` and
simply not waited on — the packet always returns on time. Every source is
also wrapped in a TTL file cache (`cache.py`) under
`$PATHIEL_STATE_DIR/intel/<desk>/`, so a desk refreshed twice in a row
answers the second time from disk.

## Sources

Each source lives in `sources/<name>.py`, has its own `ATOMIC_FETCHERS`
tuple (one callable per network source), and is desk-gated through
`desk_rules.py` (`is_oil_desk` / `is_metals_desk` / `is_indices_desk` /
`is_crypto_desk` / `is_equity_desk`) exactly the way `news.py` already
gated oilprice.com and the crypto RSS feed before this branch.

| source | desk(s) | key | sample (from a real fixture) |
|---|---|---|---|
| `sources/news.py` | all | none | GDELT / Google News / oilprice.com / Cointelegraph RSS |
| `sources/policy.py` | all | `CONGRESS_API_KEY` (bills only; Federal Register + OFAC need none) | Federal Register, OFAC recent actions |
| `sources/predictions.py` | all | none | Polymarket / Kalshi odds |
| `sources/positioning.py` | oil, metals, btc, indices | none | `WTI non-commercial net long: 100,343 (chg +1,283) pct1y=0.67 as_of 2026-10-13` |
| `sources/catalysts.py` | all | none | EIA/API/FOMC/CPI calendar, NHC storms, token unlocks, earnings estimate |
| `sources/venue.py` | **all** | none | `Brent funding rate (hourly): -1.037e-05 rate_per_hr (prior n/a, chg n/a) \| insufficient 30d funding history (20 samples)` |
| `sources/eia.py` | **oil** | `EIA_API_KEY` (free) | `EIA crude stocks weekly change: -10 mm_bbl (prior -1, chg -8.625) \| surprise -8.62 vs 4wk avg chg -1.38` |
| `sources/fred.py` | **metals, indices** | `FRED_API_KEY` (free) | `10y Treasury yield: 4.1 pct (prior 4, chg +0.1) \| 5d change +0.100` |
| `sources/cboe.py` | **indices, ai (equity)** | none | `VIX: 19.03 index_pts (prior 16.68, chg +2.35) \| day chg +2.35` plus `VIX term structure slope (9D/3M): 0.9374 ratio \| contango (calm)` |
| `sources/edgar.py` | **ai (equity)** | none (contact User-Agent only, SEC's own rule) | `NVDA insider Form 4 net (buys-sells): 0 count \| 0 buys, 0 sells across 1/1 Form 4 filings in the last 14d (open-market P/S only)` and `NVDA next earnings (estimated): 33.5 days \| estimated 2026-11-25 from last 10-Q/10-K (2026-08-26) + ~91d cadence, not confirmed` |

Every sample line above is copied from this service's own test fixtures
under `tests/fixtures/`, captured live (`curl`) on 2026-10-17/2026-10-23 —
see each source's module docstring for the exact capture notes (which
endpoints 200'd, which 403'd without a key, which URLs 404'd and were
dropped).

### Keys needed, and where to get each

Run `scripts/intel_keys.py` to see which of these are set right now:

```
.venv/bin/python scripts/intel_keys.py           # human-readable report
.venv/bin/python scripts/intel_keys.py --check   # exit 1 if any is missing
```

| env var | free? | register at |
|---|---|---|
| `EIA_API_KEY` | yes | https://www.eia.gov/opendata/register.php |
| `FRED_API_KEY` | yes | https://fred.stlouisfed.org/docs/api/api_key.html |
| `CONGRESS_API_KEY` | yes | https://api.congress.gov/sign-up/ |

Every other source (`venue.py`, `cboe.py`, `edgar.py`, `positioning.py`,
`news.py`'s RSS feeds, Federal Register, OFAC, NHC, DeFiLlama, Polymarket,
Kalshi) is a public endpoint — no key, ever. A missing key skips its
source cleanly (never raises, never blocks the rest of the packet) and is
named in `IntelPacket.sources_failed` as `<source>:<fn>:<KEY>_missing`;
`services/strategies/facade.get_brief` surfaces that into `DeskBrief.stale`
too, so it is visible wherever a human reads the brief, not just in the
packet's own JSON.

## Hard numbers, and the crowding flags

`IntelPacket.datapoints: List[DataPoint]` (contract 1.2.0, additive) is
the home for every number above: `id`, `source`, a desk-relevant `label`,
`value`/`prior`/`change`/`unit`, `as_of`, and a free-text `note` that
carries anything that doesn't deserve its own row — a crowding flag, a
surprise number, or why a figure has no `prior` yet.

- **Funding crowding**: a coin's current funding rate is at or above the
  90th percentile of its own trailing 30 days (`venue.py`,
  `FUNDING_PCTL_CROWDED = 0.90`), computed from Hyperliquid's real
  `fundingHistory` — no local approximation needed, HL has the history.
- **OI crowding**: open interest is up 20%+ in a day
  (`OI_JUMP_CROWDED_PCT = 0.20`). Hyperliquid's public `/info` API has **no
  historical open-interest endpoint** (verified live 2026-10-23:
  `openInterestHistory`/`assetHistory`/`candleSnapshot` all reject a bare
  `{"type", "coin"}` body) — so `venue.py` grows its own short rolling
  history via `cache.append_history`/`read_history`, one sample per real
  (non-cached) fetch, and reports the 24h change as `None` with a note
  until roughly 20 hours of samples exist. Never a guess.
- **Long/short skew of top traders on a coin**: **not built.** The brief's
  own hedge — "if the public info API exposes it" — resolves to false:
  `metaAndAssetCtxs`'s per-asset ctx has no such field, and the one place
  it could come from (the public leaderboard, `pathiel/agents/hyperfeed.py`)
  only gives per-trader positions, which would mean one
  `clearinghouseState` lookup per top trader per instrument — tens of
  extra HTTP calls per desk refresh for a number this service could not
  even verify is representative. Documented instead of faked; see
  `sources/venue.py`'s module docstring.

`render.py`'s `render_compact` renders these in a **HARD DATA** block,
placed *first* (ahead of HEADLINES) and boosted into a 0.75-0.95
cross-section priority band, so a hard number survives a tight token
budget ahead of a middling headline — "read what the big desks read: hard
numbers before the headline."

## Coverage eval

```
.venv/bin/python -m services.intel.evals.eval_coverage
```

Scores the oil desk's packet for *"how many of its catalyst kinds have a
hard number attached"* — today that's **2 of 8** active kinds
(`eia_weekly`, `cftc_cot`); `api_weekly`, `opec_meeting`, `baker_hughes`,
`cpi`, `hurricane_atlantic`, and `fomc` (for the oil desk specifically —
it is deliberately not FRED-gated, see the table above) have no free
structured hard-number source yet and are marked uncovered rather than
faked.

**Threshold: `DEFAULT_THRESHOLD = 0.20`.** This is today's real baseline
(0.25), not an aspirational target — it is a floor meant to catch a
*regression* (a source silently breaking) and should be raised as
`api_weekly`/`opec_meeting`/`baker_hughes`/`cpi` each get a real
hard-number source in a later pass. `eval_precision.py`'s
precision@10 threshold (0.8) is the other eval in this service, scoring
`scoring.relevance`'s headline ranking against hand-labeled real
headlines — same two-eval shape CLAUDE.md asks for: a gate-test proof the
code runs, and a periodic eval proving the ranking/coverage quality it
produces is actually good.

Offline by default — every HTTP call routed to the same real fixtures the
gate tests use, so this measures the exact code path those tests already
prove works. `--online` hits the real APIs with real keys, for a nightly
run.

## Search before building (what this branch chose, and why)

Every PyPI candidate below was checked against its own project page
2026-10-23 before writing a line of this branch's fetch/parse code:

- **FRED**: rejected `fredapi` (last release 2024-05-05, 0.5.2 — ~2.5
  years stale, and FRED's `/series/observations` is a single GET with a
  documented JSON shape; a dependency buys nothing over the ~15 lines
  `sources/fred.py` needed).
- **EIA**: rejected `eia-python` (last release 2018-07-23 — eight years
  stale, effectively abandoned).
- **SEC EDGAR**: rejected `edgartools` (actively maintained — a release
  literally the same day this branch was written — but a heavy library
  pulling pandas/numpy for full XBRL financial-statement analysis, when
  this service needs exactly three thin things: a ticker->CIK map, a
  Form 4 transaction-code count, and a 10-Q/10-K-cadence date estimate,
  all already ~250 lines of plain `requests`/`httpx`). Also rejected
  `sec-edgar-api` (last release 2023-09-30, thin unofficial wrapper, no
  benefit over `http_client.py` for three calls).
- **CFTC COT**: rejected `cot_reports`/`cot-reports` (last release
  2023-12-29, and built around downloading the full historical bulk CSV —
  a different shape than the targeted, real-time Socrata `$where=...`
  lookup `sources/positioning.py` already used before this branch).
- **Hyperliquid**: no third-party SDK considered — the public `/info`
  endpoint is one documented POST call, and `pathiel/client/hl_client.py`
  (this repo's own HL client) is owned by a different service; reaching
  into it would violate the one-concern-one-directory rule
  `sources/positioning.py`'s docstring already called out for the exact
  same reason (HL funding/OI already live in `DeskAnalytics` — this
  service reads the venue directly instead of duplicating that path).
- **CBOE**: no package exists for this at all; `cdn.cboe.com`'s daily CSV
  is about as simple an API as there is.

Every source in this service — the five new ones on this branch and the
five that existed before it — goes through the same `http_client.py`
(`fetch_json` / `fetch_text` / `fetch_json_post`), on purpose: one
retry/timeout/never-raise contract, one place to fix a timeout budget
(see `news.fetch_gdelt` and `policy.fetch_ofac`'s fixes), and zero new
runtime dependencies for this whole service.

## Tests

```
PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest services/intel/
```

Every source has: a parse/normalize test against a *real* captured
fixture, a surprise/percentile/crowding-math test, a missing-key test
(where applicable), and a timeout/failure-isolation test (one source
failing never blocks another). `tests/test_build_packet_e2e.py` wires
every source's real fixture through `build_packet` itself end to end,
fully offline — if a source's HTTP call isn't mocked there, the test
fails loudly (`unmocked_url`) rather than quietly hitting the real
network (the gate guard in `tests/conftest.py` blocks that anyway, but
unmocked != intentionally-exercising-the-failure-path, and an unbounded
retry against a blocked connection cost ~9s across this one file before
it was fixed — see that file's own fix commit).
