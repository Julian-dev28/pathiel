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
   kind, coins (oil and/or metals; "silver" is the old name of the metals group and still accepted), impact (1 to 3), verified, source.
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

## Scan (every 30 minutes)

`/event-calendar scan`. The desk-loops cron runs it at 17 and 47 past the
hour (operator, 2026-11-13: "every 30 mins"; it started at every 12
hours on 2026-11-05: "a scan every 12 hours to get new events and wire
them into our trading decision"). It adds to the overlay at
`$PATHIEL_STATE_DIR/intel/scanned_events.json`; it never edits
`services/intel/data/scheduled_events.json`. A curated row wins on a
(date, kind, title) conflict; the brief shows scanned rows with a `(scan)`
tag. Keep it cheap: one script, one agent, no more.

1. **Deterministic half.** `python3 scripts/event_scan.py` (no LLM):
   congress.gov committee meetings and hearings for the next 21 days
   (Foreign Relations, Energy and Natural Resources, Foreign Affairs, Energy
   and Commerce, Armed Services, Appropriations always; any committee whose
   title matches Iran, Hormuz, oil, sanctions, OPEC, war powers, NDAA...),
   new actions on the watched bills (H.R. 1422 and S. 556, the Iran-oil
   sanctions bills, plus bills on a kept markup's agenda), and Federal
   Register documents with an effective date or comment deadline in the
   window. It prints and logs one `EVENT-SCAN ...` line. Without
   `CONGRESS_API_KEY` it says `skipped=congress_...` and still runs the
   Federal Register. `--dry-run` prints without writing.
2. **Research half.** ONE general-purpose research agent with web search,
   for the next 21 days from today: dated Iran/Hormuz talks and deadlines,
   US-Russia-Ukraine talks, OPEC+ meetings not in `calendar_us.py`,
   Congress floor schedules (cloture votes, NDAA, appropriations, sanctions
   bills), sanctions deadlines (snapback, waivers, general licenses expiring),
   summits (G7, G20, NATO, Trump travel), Fed speakers (Powell, Waller,
   Williams), China data (NBS, trade, PBoC). It returns rows only: date,
   at_utc (or null), event, kind, coins, impact, verified, source, note.
   Official page first; otherwise `verified: false`. No row without a source.
3. **Write each row** with `scripts/event_add.py` (the one writer; it
   validates, dedups on date + kind + title, and refuses impact 3):

       python3 scripts/event_add.py --json '{"date": "2026-11-18", "at_utc": null, "event": "...", "kind": "iran_talks", "coins": ["oil", "silver"], "impact": 2, "verified": false, "source": "https://..."}'

   Impact 3 only for FOMC, CPI, an OPEC+ ministerial, a national election,
   or a dated war/sanctions/peace deadline; pass `--allow-impact-3` for
   exactly those and say so in the note. Everything else is 1 or 2. Never
   `--allow-impact-3` for a hearing, a report, a speech or a summit.
   Skip anything `calendar_us.py` generates (FOMC decisions, CPI, OPEC+
   meetings, EIA, API, Baker Hughes, CFTC). `python3 scripts/event_add.py
   --list` shows the overlay; `--prune-before <DATE>` drops old rows.
4. **End with one line** appended to `/tmp/oil-loop/progress.log`:
   `EVENT-SCAN-RESEARCH <utc> added=N updated=M refused=R` (what the agent
   found, what event_add took). Say nothing else in the loops window
   unless a row is impact 3 or a new blackout starts within 24h: then
   one sentence to the operator.
5. **Promote, do not edit.** A scanned row the operator wants permanent
   moves into the curated file in a normal refresh (steps above), with a
   commit; the scan then reports it as `curated` and stops carrying it.

## Rules

- Unscheduled headlines (a deal, a strike) are the thesis's job (news,
  Polymarket), not this file's.
- A blackout never touches an open position, its stop, target or a close.
- Do not change BEFORE_H/AFTER_H or BLACKOUT_IMPACT without the operator's go.
