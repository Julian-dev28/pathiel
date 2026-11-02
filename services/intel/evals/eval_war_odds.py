#!/usr/bin/env python3
"""Does a desk's thesis prompt carry the US-Iran war-or-peace signal?

2026-11-02: the oil desk wrote a SHORT thesis "on Iran talks" while
Polymarket priced only a 37% chance that the US x Iran ceasefire survives
November ($1.1M traded) and Trump was naming Pickaxe Mountain as a strike
target. Neither reached the prompt: the desk searched Polymarket for
"Iran deal" (which never returns the ceasefire markets) and had no news
query about a strike. The fix added "Iran ceasefire" to the oil and metals
prediction-market queries and "US strike Iran" to the oil news queries.
Metals also added "Fed December": the December meeting market (a 25bp hike
at 0.705 that day) is the rate risk silver trades on, and "Fed rate cut"
never returned it.

This eval builds the real intel packet (live network, no LLM calls),
renders it exactly as the thesis prompt does, and checks that the rendered
text holds at least one ceasefire / war odds row and, for oil, at least one
Iran strike headline. Periodic lane: the markets and headlines change, so
it measures the pipeline on today's world, not a fixture.

    .venv/bin/python -m services.intel.evals.eval_war_odds            # oil + metals
    .venv/bin/python -m services.intel.evals.eval_war_odds --desk oil

Exit 0 when every desk checked passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List

WAR_ODDS = re.compile(r"(ceasefire|declare war|military action|strike)", re.I)
STRIKE_HEADLINE = re.compile(r"\b(strikes?|attacks?|bomb\w*|Pickaxe)\b", re.I)
IRAN = re.compile(r"\bIran", re.I)
NEEDS_HEADLINE = {"oil"}
FED_MEETING = re.compile(r"Fed .*(December|meeting)", re.I)
NEEDS_FED = {"metals"}   # silver trades the December hike-or-hold odds


def check_rendered(desk: str, text: str) -> Dict[str, object]:
    """Pure: which war signals made it into the rendered prompt text."""
    lines = text.splitlines()
    odds_rows: List[str] = []
    in_odds = False
    for ln in lines:
        if ln.strip().upper().startswith("PREDICTION MARKETS") or ln.startswith("## PREDICTION"):
            in_odds = True
            continue
        if in_odds and ln.strip() and ln.strip().upper() == ln.strip() and not ln.startswith("["):
            in_odds = False
        if in_odds and IRAN.search(ln) and WAR_ODDS.search(ln):
            odds_rows.append(ln.strip())
    headlines = [ln.strip() for ln in lines
                 if IRAN.search(ln) and STRIKE_HEADLINE.search(ln) and ln not in odds_rows]
    fed_rows = [ln.strip() for ln in lines if "polymarket" in ln and FED_MEETING.search(ln)]
    ok = (bool(odds_rows) and (desk not in NEEDS_HEADLINE or bool(headlines))
          and (desk not in NEEDS_FED or bool(fed_rows)))
    return {"desk": desk, "ok": ok, "odds_rows": odds_rows[:5], "strike_headlines": headlines[:5],
            "fed_rows": fed_rows[:5]}


def run(desk: str) -> Dict[str, object]:
    from services.intel.api import build_packet
    from services.intel.render import render_compact
    from services.strategies.desks import require_spec
    spec = require_spec(desk)
    packet = build_packet(spec)
    text = render_compact(packet, spec.token_budget.max_input_tokens)
    return check_rendered(desk, text)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desk", action="append", help="desk id (repeatable); default oil and metals")
    a = ap.parse_args(argv)
    ok = True
    for desk in a.desk or ["oil", "metals"]:
        r = run(desk)
        ok = ok and bool(r["ok"])
        print(f"{desk}: {'PASS' if r['ok'] else 'FAIL'}")
        for row in r["odds_rows"]:
            print(f"  odds     {row[:140]}")
        for row in r["strike_headlines"]:
            print(f"  headline {row[:140]}")
        for row in r["fed_rows"]:
            print(f"  fed      {row[:140]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
