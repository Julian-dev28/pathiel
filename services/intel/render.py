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

from datetime import datetime
from typing import List, Tuple

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
         0.65)
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


def _odds_lines(packet: IntelPacket) -> List[Tuple[str, str, float]]:
    out = []
    for o in packet.odds:
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


def render_compact(packet: IntelPacket, max_tokens: int) -> str:
    budget_chars = max(200, max_tokens * CHARS_PER_TOKEN)
    header = f"INTEL desk={packet.desk_id} as_of={packet.as_of.isoformat()}"
    failed_line = (f"SOURCES_FAILED: {', '.join(packet.sources_failed)}"
                    if packet.sources_failed else "SOURCES_FAILED: none")

    all_sections = [(name, fn(packet)) for name, fn in _SECTIONS]
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
