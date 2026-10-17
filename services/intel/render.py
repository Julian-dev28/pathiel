"""`render_compact`: the dense text block the thesis LLM actually reads.

Every line keeps the item's id in `[brackets]` so the thesis's `source_ids`
can cite it (see contracts/strategies/models.py: `Point.source_ids`,
`Thesis.sources`). Token budget is enforced by a deterministic greedy
fill — highest cross-section priority first — never by asking a model to
summarize, which would defeat the entire point of a zero-LLM intel layer.

`sources_failed` is reserved budget, not truncatable: a packet that is
honest about what it's missing is more valuable to the thesis than a few
extra headlines, and the intel brief is explicit that a failed source
must never be silently dropped.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Tuple

from contracts.strategies.models import IntelPacket

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _age_str(dt, now: datetime) -> str:
    if dt is None:
        return "age?"
    secs = max(0.0, (now - dt).total_seconds())
    hours = secs / 3600
    if hours < 1:
        return f"{int(secs // 60)}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


def _headline_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    return [
        (h.id,
         f"[{h.id}] {h.stance:<7} rel={h.relevance:.2f} {_age_str(h.published_at, packet.as_of):>4}"
         f" {h.source[:24]:<24} {h.title}",
         h.relevance)
        for h in packet.headlines
    ]


def _policy_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    return [
        (p.id,
         f"[{p.id}] {p.status:<16} {p.stance:<7} {p.body[:20]:<20}"
         f" {(p.date.strftime('%Y-%m-%d') if p.date else 'undated')}: {p.title}",
         # newer first within the section; stays below 0.66 so cross-section
         # priority versus headlines and catalysts is unchanged
         0.65 - min(0.1, ((packet.as_of - p.date).days / 3650.0) if p.date else 0.1))
        for p in packet.policy
    ]


def _catalyst_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    out = []
    for c in packet.catalysts:
        suffix = f" ({c.expected})" if c.expected else ""
        line = (f"[{c.id}] {c.at.strftime('%Y-%m-%d %H:%MZ')} imp{c.importance}"
                f" {c.source:<12} {c.title}{suffix}")
        out.append((c.id, line, 0.80 + c.importance * 0.05))
    return out


_STRIKE_RE = re.compile(r"\babove\s+\$?([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
# A strike ladder is a set of "above X" markets on one underlying and one
# close time. Kalshi lists one market per strike (36 rows for one WTI week on
# 2026-10-17), which spent most of the prompt on near-certain 0.98 rows.
# Collapsed, one line carries the implied distribution: only the strikes
# where the market is genuinely uncertain, plus the two that bracket them.
LADDER_UNCERTAIN = (0.05, 0.95)
LADDER_MAX_STRIKES = 7
ODDS_MAX_PER_EVENT = 2
# A ladder whose odds rise with a higher strike is quoting stale books, not
# a distribution (live 2026-10-17: a WTI Nov-3 ladder read P(>58.99)=0.88,
# P(>59.99)=0.96 with $11K traded). Thin or broken ladders are dropped.
LADDER_MIN_VOLUME_USD = 5_000.0
LADDER_MONOTONE_TOL = 0.05


def _ladder_is_usable(rows) -> bool:
    if sum(o.volume_usd or 0.0 for _k, o in rows) < LADDER_MIN_VOLUME_USD:
        return False
    probs = [o.probability for _k, o in rows]
    return all(b <= a + LADDER_MONOTONE_TOL for a, b in zip(probs, probs[1:]))


def _ladder_key(o) -> str:
    return f"{o.venue}|{o.url}|{o.closes_at.isoformat() if o.closes_at else ''}"


def _ladder_line(rows) -> Tuple[str, str, float]:
    """rows: [(strike, MarketOdds)] sorted by strike. Strikes and
    probabilities are copied verbatim from the packet, so every number in
    the line is grounded for the thesis validator; nothing is derived."""
    lo, hi = LADDER_UNCERTAIN
    idx = [i for i, (_k, o) in enumerate(rows) if lo <= o.probability <= hi]
    if idx:
        first, last = max(0, idx[0] - 1), min(len(rows) - 1, idx[-1] + 1)
    else:  # every strike is near-certain: keep the boundary where it flips
        flip = next((i for i, (_k, o) in enumerate(rows) if o.probability < 0.5), len(rows) - 1)
        first, last = max(0, flip - 1), min(len(rows) - 1, flip)
    window = rows[first:last + 1]
    if len(window) > LADDER_MAX_STRIKES:  # keep the ends, thin the middle evenly
        step = (len(window) - 1) / (LADDER_MAX_STRIKES - 1)
        window = [window[round(i * step)] for i in range(LADDER_MAX_STRIKES)]
    head = rows[0][1]
    vol = sum(o.volume_usd or 0.0 for _k, o in rows)
    closes = head.closes_at.strftime("%Y-%m-%d %H:%MZ") if head.closes_at else "close?"
    stem = _STRIKE_RE.split(head.question)[0].strip().rstrip(",")
    strikes = " ".join(f"P(>{k:g})={o.probability:.2f}" for k, o in window)
    line = (f"[{head.id}] {head.venue:<10} ladder {len(rows)} strikes, ${vol / 1000:.0f}K vol,"
            f" closes {closes}: {stem} ... {strikes}")
    return head.id, line, 0.60


def _odds_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    ladders: Dict[str, list] = OrderedDict()
    singles = []
    for o in packet.odds:
        m = _STRIKE_RE.search(o.question)
        if m:
            ladders.setdefault(_ladder_key(o), []).append((float(m.group(1)), o))
        else:
            singles.append(o)

    out = []
    for rows in ladders.values():
        rows.sort(key=lambda t: t[0])
        if _ladder_is_usable(rows):
            out.append(_ladder_line(rows))

    # Plain markets: at most ODDS_MAX_PER_EVENT per event (same url), the
    # most traded first. 16 near-duplicate "will X be in a US-Iran deal"
    # rows say no more than the two most liquid of them.
    by_event: Dict[str, list] = OrderedDict()
    for o in sorted(singles, key=lambda o: -(o.volume_usd or 0.0)):
        by_event.setdefault(o.url, []).append(o)
    for rows in by_event.values():
        for o in rows[:ODDS_MAX_PER_EVENT]:
            vol = f"${o.volume_usd / 1000:.0f}K vol" if o.volume_usd else "vol?"
            line = f"[{o.id}] {o.venue:<10} p={o.probability:.2f} {vol} {o.question}"
            out.append((o.id, line, 0.55))
    return out


def _positioning_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    out = []
    for p in packet.positioning:
        chg = f"{p.change:+,.0f}" if p.change is not None else "n/a"
        line = (f"[{p.id}] {p.source:<10} {p.label}: {p.value:,.0f} (chg {chg})"
                f" as_of {p.as_of.strftime('%Y-%m-%d')}")
        out.append((p.id, line, 0.55))
    return out


_SECTIONS = (
    ("HEADLINES", _headline_lines),
    ("POLICY", _policy_lines),
    ("CATALYSTS", _catalyst_lines),
    ("PREDICTION MARKETS", _odds_lines),
    ("POSITIONING", _positioning_lines),
)

# Hard per-section caps, applied before the budget fill. Measured on the live
# oil packet of 2026-10-17: 42 headlines, 25 policy items and 130 odds rows
# filled a 6,000-token allowance and pushed one thesis call to ~19k input
# tokens. A thesis needs the best few of each, not all of them.
SECTION_CAPS = {"HEADLINES": 10, "POLICY": 6, "CATALYSTS": 8,
                "PREDICTION MARKETS": 8, "POSITIONING": 6}


def _cap(name: str, lines: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
    cap = SECTION_CAPS.get(name)
    if cap is None or len(lines) <= cap:
        return lines
    keep = {cid for cid, _l, _p in sorted(lines, key=lambda t: -t[2])[:cap]}
    return [t for t in lines if t[0] in keep]   # original (chronological) order


def render_compact(packet: IntelPacket, max_tokens: int) -> str:
    budget_chars = max(200, max_tokens * CHARS_PER_TOKEN)
    header = f"INTEL desk={packet.desk_id} as_of={packet.as_of.isoformat()}"
    failed_line = (f"SOURCES_FAILED: {', '.join(packet.sources_failed)}"
                    if packet.sources_failed else "SOURCES_FAILED: none")

    all_sections = [(name, _cap(name, fn(packet))) for name, fn in _SECTIONS]
    # Reserve for the fixed header/failed lines AND, worst case, every
    # section's own "NAME:" line — unknown in advance which sections end
    # up non-empty, so reserve for all of them rather than risk the actual
    # rendered text creeping over `budget_chars` (and so over the desk's
    # token budget, which is the measurable contract this function has to
    # hit, not just approximate).
    section_header_cost = sum(len(name) + 2 for name, _lines in all_sections)
    reserved = len(header) + 1 + len(failed_line) + 1 + section_header_cost
    budget_remaining = max(0, budget_chars - reserved)

    candidates = [(pr, cid) for _name, lines in all_sections for cid, _line, pr in lines]
    candidates.sort(key=lambda t: t[0], reverse=True)

    line_by_id = {cid: line for _name, lines in all_sections for cid, line, _pr in lines}
    included = set()
    used = 0
    for _pr, cid in candidates:
        cost = len(line_by_id[cid]) + 1
        if used + cost <= budget_remaining:
            included.add(cid)
            used += cost

    out = [header, failed_line]
    for name, lines in all_sections:
        kept = [line for cid, line, _pr in lines if cid in included]
        if not kept:
            continue
        out.append(f"{name}:")
        out.extend(kept)
    text = "\n".join(out)

    # Belt and suspenders: the reservation above makes this a no-op in
    # practice, but the token budget is a hard contract (eval + perf
    # target both assert on it), so guarantee it rather than trust the
    # estimate alone.
    if len(text) > budget_chars:
        text = text[:budget_chars]
    return text
