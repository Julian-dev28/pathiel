# Event calendar audit, 2026-11-05

Operator, after the 11-03 midterms dropped Brent 9% on 11-04: "I NEED YOU TO
AUDIT THE EVENT CALENDAR AND TAKE THEM INTO ACCOUNT WHEN YOU MAKE DECISIONS
SEE STUFF LIKE THAT, ANY OTHER CALLS, ELECTIONS, BILLS ALL THAT".

## What went wrong on 11-03/11-04

| UTC | What happened | P&L |
|---|---|---|
| 11-03 13:53 | Brent long 29.28 @ 101.79, about 10h before the polls closed. The calendar had no election | |
| 11-04 overnight | Democratic sweep; markets price a faster Iran-war exit | |
| 11-04 13:37 | Stop at 101.00 | -$23.40 |
| 11-04 16:00 to 17:30 | Brent to 93.4 (-9% on the day); the old position would have been near liquidation (99.20) | |
| 11-04 17:32 | Dip buy 25.87 @ 93.74 | |
| 11-05 10:43 | Closed by the operator @ 96.01 | +$58.80 |

Net through the event: about +$33. The stop saved the account; the
calendar did not. The 11-04 thesis named the EIA report as its next
catalyst, not the election.

## What the calendar had (before)

`services/intel/calendar_us.py` generators:
- EIA weekly, EIA gas storage, API weekly, Baker Hughes, CFTC COT, EIA STEO.
- FOMC: 2026 only.
- CPI: through Mar 2027.
- OPEC+: one meeting, 11-01, then nothing.

Live hurricanes, token unlocks and earnings came from feeds. Only the **EIA
report** had a hard trading rule (the 2h blackout). Everything else reached
the thesis only as text.

**Missing:**
- elections
- jobs, PCE, GDP, PPI, retail sales
- the OPEC and IEA monthly reports
- OPEC+ after 11-01, FOMC in 2027
- funding deadlines and the new Congress
- holidays and thin-liquidity days
- contract expiries
- China data (silver)
- any check that the calendar is about to run out

## What changed

| Piece | File |
|---|---|
| 38 dated, sourced rows through 2027-01-31 (`verified` flag per row; the midterms kept as a past row) | `services/intel/data/scheduled_events.json` |
| Reader: windows, blackout, the 72h list, a warning 14 days before the file runs out | `services/intel/scheduled_events.py` |
| FOMC 2027-01-27, OPEC+ 2026-11-29 added to the generators | `services/intel/calendar_us.py` |
| Every row becomes a thesis catalyst for the desk's coins | `services/intel/sources/catalysts.py::scheduled_catalysts` |
| **Event blackout**: impact-3 events block new entries. FOMC, CPI: 2h either side. OPEC+: its whole UTC day. Elections: a custom window (midterms: 24h before the polls closed to 18h after). The EIA blackout is unchanged | `services/strategies/event_blackout.py::event_blackout`, `place_bracket`, `trigger_watch.py` |
| "Events next 72h" line in every desk brief, `[blackout]` and `(unverified)` tags, plus the calendar warning | `scripts/pathiel-mcp-server.py::_live_rule` |
| Tick agent: reads the 72h line, names an impact-3 event before its next tick when holding a position, never closes for it | `.claude/agents/desk-tick.md` |
| Refresh procedure | `.claude/skills/event-calendar/SKILL.md` |

The 11-03 13:53Z entry falls inside the new midterm window, so it would
have been refused (`tests/test_bracket.py::test_handler_refuses_new_entries_on_both_desks_inside_an_event_blackout`).

## The what-if the operator asked for

"what if we shorted 10% until bullish again ... just to see what the results
are". `services/strategies/evals/event_direction_test.py`, 120 days of 1h
candles. A short sized at 10% of equity at 20x opens at each blackout's
start and closes 24h after the event:

| | n | Short wins | Short total | Long total |
|---|---|---|---|---|
| Brent, FOMC x3 + OPEC+ + midterms | 5 | 5 | +25.6% | -25.6% |
| (the midterms alone) | 1 | 1 | +18.5% | |
| Brent, EIA weekly | 17 | 5 | -37.2% | +37.2% |
| Silver, FOMC x3 + midterms | 4 | 1 | -9.7% | +9.7% |

Events carry no direction of their own: they amplify the trend that is
already running. No direction rule was adopted; the blackout stays flat.

## Not covered by any file

Unscheduled headlines: a US-Iran deal or strike, Hormuz, sanctions. They
are the biggest oil risk right now and stay the thesis's job (news,
Polymarket). Talks run through Qatar with no announced date.
