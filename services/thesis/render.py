"""render_markdown(thesis, analytics, mode) - the two user-facing views.

"quick" is about 5 lines: the call, the leverage pick, the biggest risk, the
next catalyst, one decision word - everything else is reachable by tapping
through, per SWARM_RULES.md section 7 (Julien has ADHD, wants one decision
per screen, the call first, details on demand).

"full" mirrors the structure the user liked: market snapshot table,
bull/bear news with sources, base case with probabilities, a leverage
ladder table (liq px, P(liq), EV, verdict), a full-send table at the desk's
leverage rungs (the equity-outcome distribution per rung - a different cut
of analytics.ladder than the verdict table above: "should I do this" there,
"what exactly happens to my money" here), a catalyst calendar in UTC, what
changes my mind, and sources.

No em dashes anywhere in this file's output, by rule - a plain hyphen with
spaces around it instead.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from contracts.strategies.models import Catalyst, DeskAnalytics, LeverageView, Scenario, Thesis

_VERDICT_WORD: Dict[str, str] = {
    "hold": "HOLD",
    "dip_only": "DIP",
    "intraday_only": "INTRADAY",
    "avoid": "AVOID",
}


def render_markdown(thesis: Thesis, analytics: DeskAnalytics, mode: str = "quick") -> str:
    if mode == "quick":
        return _render_quick(thesis)
    if mode == "full":
        return _render_full(thesis, analytics)
    raise ValueError(f"unknown render mode {mode!r}, expected 'quick' or 'full'")


# -- shared, deterministic picks ---------------------------------------------


def _leverage_pick(thesis: Thesis) -> Optional[LeverageView]:
    if thesis.call.leverage is None:
        return None
    for lv in thesis.leverage_view:
        if lv.leverage == thesis.call.leverage:
            return lv
    return None


def _worst_case_scenario(thesis: Thesis) -> Optional[Scenario]:
    if not thesis.scenarios:
        return None
    return min(thesis.scenarios, key=lambda s: s.move_lo_pct)


def _next_catalyst(thesis: Thesis, now: Optional[datetime] = None) -> Optional[Catalyst]:
    if not thesis.catalysts:
        return None
    now = now or datetime.now(timezone.utc)
    upcoming = [c for c in thesis.catalysts if c.at >= now]
    pool = upcoming if upcoming else thesis.catalysts
    return min(pool, key=lambda c: c.at)


def decision_word(thesis: Thesis) -> str:
    """One word: what to actually do. Pure function of the call's side and
    the ladder verdict for the leverage rung the call picked - never a fresh
    judgment call, so the same thesis always renders the same word."""
    if thesis.call.side == "flat":
        return "WAIT"
    lv = _leverage_pick(thesis)
    if lv is None:
        return "WATCH"
    return _VERDICT_WORD.get(lv.verdict, "WATCH")


# -- quick ---------------------------------------------------------------


def _render_quick(thesis: Thesis) -> str:
    lv = _leverage_pick(thesis)
    if lv is not None and thesis.call.leverage:
        lev_line = f"Leverage: {thesis.call.leverage:.0f}x - {lv.verdict.upper()}"
    else:
        lev_line = "Leverage: flat, no position"

    worst = _worst_case_scenario(thesis)
    if worst is not None:
        risk_line = f"Biggest risk: {worst.probability * 100:.0f}% chance of {worst.description}"
    else:
        risk_line = "Biggest risk: n/a"

    cat = _next_catalyst(thesis)
    if cat is not None:
        cat_line = f"Next catalyst: {cat.title} ({cat.at.strftime('%Y-%m-%d %H:%M')} UTC)"
    else:
        cat_line = "Next catalyst: none scheduled"

    return "\n".join([
        thesis.call.one_liner,
        lev_line,
        risk_line,
        cat_line,
        f"Decision: {decision_word(thesis)}",
    ])


# -- full -------------------------------------------------------------------


def _render_full(thesis: Thesis, analytics: DeskAnalytics) -> str:
    sections = [
        f"## {thesis.call.one_liner}",
        _render_market_table(analytics),
        _render_news(thesis),
        _render_base_case(thesis),
        _render_ladder_table(thesis, analytics),
        _render_full_send_table(analytics),
        _render_catalyst_calendar(thesis),
        _render_what_changes_my_mind(thesis),
        _render_sources(thesis),
    ]
    return "\n\n".join(s for s in sections if s)


def _render_market_table(analytics: DeskAnalytics) -> str:
    lines = [
        "### Market snapshot",
        "| Instrument | Mid | 1d | 5d | 20d | ATR14 |",
        "|---|---|---|---|---|---|",
    ]
    for snap in analytics.instruments:
        lines.append(
            f"| {snap.label} | {snap.mid:.3f} | {snap.ret_1d * 100:+.2f}% | "
            f"{snap.ret_5d * 100:+.2f}% | {snap.ret_20d * 100:+.2f}% | "
            f"{snap.atr14_pct * 100:.1f}% |"
        )
    return "\n".join(lines)


def _render_news(thesis: Thesis) -> str:
    if not thesis.bull and not thesis.bear:
        return ""
    by_id = {s.id: s for s in thesis.sources}

    def _links(source_ids: List[str]) -> str:
        links = [f"[{by_id[sid].title}]({by_id[sid].url})" for sid in source_ids if sid in by_id]
        return " (" + ", ".join(links) + ")" if links else ""

    lines = ["### Bull / bear"]
    if thesis.bull:
        lines.append("**Bull**")
        for pt in thesis.bull:
            lines.append(f"- {pt.text}{_links(pt.source_ids)}")
    if thesis.bear:
        lines.append("**Bear**")
        for pt in thesis.bear:
            lines.append(f"- {pt.text}{_links(pt.source_ids)}")
    return "\n".join(lines)


def _render_base_case(thesis: Thesis) -> str:
    if not thesis.scenarios:
        return ""
    lines = ["### Base case"]
    for s in sorted(thesis.scenarios, key=lambda s: s.probability, reverse=True):
        lines.append(
            f"- {s.name} - {s.probability * 100:.0f}% - "
            f"[{s.move_lo_pct * 100:+.0f}%, {s.move_hi_pct * 100:+.0f}%) - {s.description}"
        )
    return "\n".join(lines)


def _render_ladder_table(thesis: Thesis, analytics: DeskAnalytics) -> str:
    if not analytics.ladder:
        return ""
    reason_by_leverage = {lv.leverage: lv.reason for lv in thesis.leverage_view}
    lines = [
        "### Leverage ladder",
        "| Lev | Liq px | P(liq) | EV | Verdict | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for rung in analytics.ladder:
        reason = reason_by_leverage.get(rung.leverage, "")
        lines.append(
            f"| {rung.leverage:.0f}x | {rung.liq_px:.2f} | {rung.p_liq * 100:.1f}% | "
            f"{rung.ev_pct * 100:+.1f}% | {rung.verdict.upper()} | {reason} |"
        )
    return "\n".join(lines)


def _render_full_send_table(analytics: DeskAnalytics) -> str:
    """What exactly happens to your money per rung - distinct from the
    ladder table above (should I do this) by cutting the same ladder data
    on equity-outcome quantiles and tail probabilities instead."""
    if not analytics.ladder:
        return ""
    lines = [
        "### Full send (equity outcome by leverage)",
        "| Lev | P10 | P50 | P90 | P(2x) | P(lose half) |",
        "|---|---|---|---|---|---|",
    ]
    for rung in analytics.ladder:
        q = rung.equity_ret
        lines.append(
            f"| {rung.leverage:.0f}x | {q.p10 * 100:+.0f}% | {q.p50 * 100:+.0f}% | "
            f"{q.p90 * 100:+.0f}% | {rung.p_double * 100:.0f}% | {rung.p_lose_half * 100:.0f}% |"
        )
    return "\n".join(lines)


def _render_catalyst_calendar(thesis: Thesis) -> str:
    if not thesis.catalysts:
        return ""
    lines = ["### Catalyst calendar (UTC)"]
    for c in sorted(thesis.catalysts, key=lambda c: c.at):
        lines.append(f"- {c.at.strftime('%Y-%m-%d %H:%M')} UTC: {c.title} (importance {c.importance})")
    return "\n".join(lines)


def _render_what_changes_my_mind(thesis: Thesis) -> str:
    if not thesis.what_changes_my_mind:
        return ""
    lines = ["### What changes my mind"]
    lines += [f"- {line}" for line in thesis.what_changes_my_mind]
    return "\n".join(lines)


def _render_sources(thesis: Thesis) -> str:
    if not thesis.sources:
        return ""
    lines = ["### Sources"]
    lines += [f"- [{s.title}]({s.url})" for s in thesis.sources]
    return "\n".join(lines)


__all__ = ["render_markdown", "decision_word"]
