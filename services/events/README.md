# services/events

Scheduled-event calendar for the xyz (HIP-3) universe, and the honest verdict on
whether any of it is tradeable yet.

## The power constraint, stated first

The interesting catalysts are fetchable. They are not testable. That is
arithmetic, not effort:

| event | count in the 72-day panel |
|---|---|
| a named bill passing (CLARITY Act, etc.) | **1**, by definition |
| executive orders on any one topic | 0–3 |
| FOMC decisions | ~2 |
| corporate earnings across 65 mapped names | **~190** |

A strategy fitted to n=1 is a story with a backtest attached. Only earnings is
dense enough to measure, so that is the only thing C9 tested. Everything else in
this module is calendar context for a human, deliberately not wired to a book.

## Sources

| source | key | what |
|---|---|---|
| SEC EDGAR | none | 8-K / 10-Q filing dates — the testable one |
| Federal Reserve | none | FOMC meetings + speaker calendar (feed carries a UTF-8 BOM) |
| Federal Register | none | executive orders, rules, proposed rules |
| Congress.gov | **free key** | [sign up](https://api.congress.gov/sign-up/) — bills, status, votes |
| EIA | **free key** | [register](https://www.eia.gov/opendata/register.php) — weekly petroleum, direct `xyz:CL` / `xyz:BRENTOIL` catalyst |
| FRED | **free key** | [key](https://fred.stlouisfed.org/docs/api/api_key.html) — macro series |

Keyed sources read from the environment and **skip cleanly when absent** rather
than failing or silently returning nothing. A calendar that quietly omits a
source is worse than one that says the key is missing.

```sh
python services/events/fetch_events.py --refresh      # writes .state/events_calendar.json
export EIA_API_KEY=...                                # optional, adds petroleum
```

## What was tested and rejected

**C9 — shorting into earnings.** The hypothesis was that a name which just ran
8% is running into its print, turning a reversal bet into a coin flip on a
binary event. Measured: earnings-spanning shorts returned **+3.921%** against
**+1.552%** for clean ones — the opposite of the hypothesis, and the difference
does not survive the breadth guard (144 rows are only **114 timestamps across 10
coins**; one print catches many entries on the same name, and those are one
event, not many). No filter shipped.

## Why the oil/war intuition is right and still unusable

The operator's reasoning — war and oil are correlated, `xyz:CL` and
`xyz:BRENTOIL` are on the dex — is sound. The problem is that a geopolitical
shock is one observation. With 72 days of panel you cannot separate "oil rises
on conflict" from "oil rose that month". The EIA weekly petroleum report is the
version of this that IS schedulable (~10 releases per window), which is why the
EIA key is the one worth adding first.
