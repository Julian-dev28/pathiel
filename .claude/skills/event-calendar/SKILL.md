---
name: event-calendar
description: Audit and refresh the desks' scheduled-event calendar (services/intel/data/scheduled_events.json) - elections, jobs/CPI/PCE/GDP, FOMC, OPEC+, IEA/OPEC reports, funding deadlines, Congress, holidays, contract expiries, China data. Run when the desk brief warns "event calendar ends", monthly, or before any election or big political date.
---

# event-calendar

Operator, 2026-11-05, after the 11-03 midterms dropped Brent 9% with no
election on the calendar: "I NEED YOU TO AUDIT THE EVENT CALENDAR AND TAKE
THEM INTO ACCOUNT WHEN YOU MAKE DECISIONS... ELECTIONS, BILLS, ALL THAT".

What uses the calendar:
- **Thesis:** every row becomes an intel catalyst
  (`services/intel/sources/catalysts.py::scheduled_catalysts`).
- **Desk brief:** the "Events next 72h" line.
- **Blackout:** impact-3 rows block new entries
  (`services/strategies/event_blackout.py::event_blackout`). They are enforced
  in `place_bracket` and `trigger_watch.py`.
- **Generated, not in the file:** FOMC decisions, CPI, OPEC+ meetings, EIA,
  API, Baker Hughes and CFTC come from `services/intel/calendar_us.py`. Update
  those tables there (FOMC_DECISION_DATES_*, CPI_RELEASE_DATES,
  OPEC_PLUS_MEETINGS), not in the JSON.

## Steps

1. **Read the file** and note `valid_through`.
2. **Research forward** to at least 90 days past today. Use one
   general-purpose agent with web search, and official sources first:
   - US data: bls.gov, bea.gov.
   - Central bank: federalreserve.gov.
   - Oil: opec.org, iea.org, eia.gov, ice.com, cmegroup.com.
   - Politics: congress.gov, state election sites. Also look for scheduled
     Iran/Hormuz talks, sanctions deadlines and IAEA board dates.

   Every row needs: date, at_utc (or null for all day or unknown), event,
   kind, coins (oil and/or silver), impact (1 to 3), verified, source.
   Unverified dates stay `verified: false`. Never guess a time.
3. **Impact 3 is for events that can move price 2% or more within hours.**
   Those block entries. Keep it to:
   - FOMC
   - CPI
   - OPEC+ ministerials
   - national elections or referendums
   - a dated war, sanctions or peace deadline

   Jobs, PCE, IEA and OPEC monthly reports, funding deadlines and holidays
   are 2. Elections get their own window: `before_h` from 24h before the
   polls close, `after_h` until results are in.
4. **Update the file.** Bump `refreshed` and `valid_through`. Keep past rows:
   they are the record the what-if eval replays.
5. **Check:**
   - `.venv/bin/python -m pytest -q services/intel/tests/test_scheduled_events.py`
   - `python3 -m services.strategies.evals.event_direction_test` (what a
     short or long through each past event did; information only)
6. **Commit and push.** Say "reconnect pathiel MCP (/mcp) in the desk loops
   window" so place_bracket and the brief reload the file.

## Rules

- Unscheduled headlines (a deal, a strike) are the thesis's job (news,
  Polymarket), not this file's.
- A blackout never touches an open position, its stop, target or a close.
- Do not change BEFORE_H/AFTER_H or BLACKOUT_IMPACT without the operator's go.
