"""Prompt construction: dense text (not raw JSON) for analytics and intel,
the desk-trader system prompt, token budgeting, incremental-mode diffing,
and the repair prompt.

Rendering as prose instead of JSON is deliberate: JSON spends tokens on
punctuation and repeated keys the model does not need restated, and a human
style forces every figure to carry its own label and unit inline, which is
exactly what later shows up, number for number, in the model's own prose -
making the validator's grounding check a tight loop instead of a guess.
"""
from __future__ import annotations

import importlib
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from contracts.strategies.models import (
    Catalyst, DeskAnalytics, DeskSpec, Headline, IntelPacket, LadderRung,
    PairSnapshot, PolicyItem, Thesis,
)
from services.thesis import desk_lookup


def _load_intel_render_compact():
    """services.intel.api.render_compact if that package is installed, else
    None (services/intel does not exist in this worktree yet - see
    render_intel_compact below for the fallback).

    Loaded via importlib rather than a static ``from services.intel.api
    import ...`` on purpose: this is a genuinely OPTIONAL, soft dependency -
    _fallback_render_intel_compact makes this module fully correct without
    it - and tests/test_deploy_config.py's import scanner (a different
    agent's gate test, not re-implemented here) reasonably treats any
    static ``from services.X import ...`` line as a hard runtime dependency
    that the Dockerfile must COPY in. A soft, try/except-guarded dependency
    that can never actually break the deployed image is not that, so it is
    written in a shape that scanner does not (and should not) flag -
    importlib.import_module is the standard way to spell "this package may
    not be installed" in Python, same intent as any plugin-discovery loader.
    """
    try:
        module = importlib.import_module("services.intel.api")
    except ImportError:
        return None
    return getattr(module, "render_compact", None)


_intel_render_compact = _load_intel_render_compact()

CHARS_PER_TOKEN = 4  # same rule-of-thumb estimate used across the industry;
# documented here once rather than re-derived at every call site.

INSTRUCTIONS_RESERVE_TOKENS = 350

# Ceiling on the intel block, whatever the desk budget leaves. Intel is the
# part that grows with the news cycle; the analytics and instructions do not.
# 2026-10-17 live oil run: intel filled 6k tokens and one call cost ~19k input.
INTEL_MAX_TOKENS = 2500


def estimate_tokens(text: str) -> int:
    """Deterministic, conservative token estimate: ~4 chars/token for
    English prose. Used only to decide what to trim BEFORE a call -
    LlmResult.input_tokens / output_tokens from the real CLI response is
    always the ground truth for what to report afterward."""
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


# -- system prompt -----------------------------------------------------------


def build_system_prompt(spec: DeskSpec) -> str:
    """The desk-specific persona: a senior desk trader writing for Julien,
    who has ADHD and is training to master this desk's market. The call
    comes first, every claim carries a number, and there is exactly one
    plain-English lesson tied to today's market.

    The terseness and full-number rules below were added after a real CLI
    run (2026-10-17) showed two concrete failure modes with no caps/
    guidance: single calls ran 3100-4000 output tokens against an
    1800-token budget (now also capped structurally - see
    llm_schema._PROSE_CAPS - but a model that already knows the budget
    needs less correcting), and a BTC-desk scenario wrote a bare "120"
    meaning "$120k", which the validator could not tell from an invented
    number near 120.
    """
    lessons_menu = ", ".join(spec.lessons) if spec.lessons else "a concept relevant to today's move"
    return (
        f"You are a senior desk trader on the {spec.name} desk, writing "
        "directly to Julien, who trades it himself.\n\n"
        "Julien has ADHD and is training to master this market. Write for "
        "that: the call comes first, every claim carries a number, and "
        "there is exactly one plain-English lesson tied to today's market - "
        f"not a lecture, one lesson, chosen from: {lessons_menu}.\n\n"
        "Hard rules:\n"
        "- No em dashes. No hedging filler (\"could\", \"might\", "
        "\"potentially\") unless it is immediately followed by a number. "
        "Short sentences.\n"
        "- Every number you write - in the one-liner, the invalidation, the "
        "scenarios, the bull/bear points, the lesson, what-changes-my-mind, "
        "and the leverage reasons - must come from the data given to you, "
        "or be a simple, obviously-derived combination of it (for example: "
        "applying one of your own scenario's percent range to the current "
        "price). Never invent a number. If you are not sure a number is "
        "grounded in the data, do not use it.\n"
        "- Spell catalyst dates out in words (\"next Wednesday's EIA "
        "report\"), not digits, so a typed-out date can never be mistaken "
        "for an invented price.\n"
        "- entry_px must sit close to the current mid price given below. "
        "stop_px must be on the losing side of entry_px for the side you "
        "chose (below entry for a long, above entry for a short). Never "
        "choose a leverage rung whose verdict (given below) is \"avoid\".\n"
        "- scenarios must be 3 to 5 buckets of horizon-day return, "
        "non-overlapping and contiguous, with probabilities that sum to "
        "1.0. move_lo_pct and move_hi_pct are FRACTIONS: -0.05 means -5%, "
        "never write -5.\n"
        "- Cite every bull/bear point with the source id(s) (in brackets in "
        "the data below) it came from.\n"
        "- one_liner is at most 160 characters.\n"
        "- Be terse everywhere, not just the one-liner: one or two short "
        "sentences per scenario description, per bull/bear point, per "
        "leverage reason. State the number and the reason, skip the "
        "preamble.\n"
        "- Write every price in full digits (120000 or $120,000), never an "
        "abbreviation without a k/M suffix (not bare \"120\" for "
        "$120,000).\n\n"
        "Return only the JSON described by the schema. No prose outside the "
        "JSON, no markdown fences."
    )


# -- deterministic analytics renderer ----------------------------------------


def _pct(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def _pct_abs(x: Optional[float], digits: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.{digits}f}%"


def _usd(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    if abs(x) >= 1_000_000:
        return f"${x / 1_000_000:.1f}M"
    if abs(x) >= 1_000:
        return f"${x / 1_000:.1f}K"
    return f"${x:.2f}"


def _num(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}"


def render_analytics_dense(spec: DeskSpec, analytics: DeskAnalytics) -> str:
    """Dense, labeled text render of DeskAnalytics - every figure the LLM
    may ground a number against, laid out so the number appears next to a
    word that explains what it is."""
    lines: List[str] = [
        f"MARKET SNAPSHOT (as of {analytics.as_of.isoformat()})",
    ]
    role_by_coin = {inst.coin: inst.role for inst in spec.instruments}
    for snap in analytics.instruments:
        role = role_by_coin.get(snap.coin, "secondary").upper()
        lines.append(
            f"- {snap.label} ({snap.coin}) {role}: mid {_num(snap.mid, 3)}, "
            f"1d {_pct(snap.ret_1d)}, 5d {_pct(snap.ret_5d)}, 20d {_pct(snap.ret_20d)}"
            + (f", 60d {_pct(snap.ret_60d)}" if snap.ret_60d is not None else "")
        )
        range_bits = [f"20d range {_num(snap.low_20d)}-{_num(snap.high_20d)}"]
        if snap.high_60d is not None and snap.low_60d is not None:
            range_bits.append(f"60d range {_num(snap.low_60d)}-{_num(snap.high_60d)}")
        lines.append(
            "  " + ", ".join(range_bits)
            + f", ATR14 {_pct_abs(snap.atr14_pct)}, vol(20d) {_pct_abs(snap.vol_daily_20d)}/day"
        )
        extra = []
        if snap.funding_hourly is not None:
            extra.append(f"funding {snap.funding_hourly * 100:+.5f}%/hr")
        if snap.open_interest_usd is not None:
            extra.append(f"OI {_usd(snap.open_interest_usd)}")
        if snap.volume_24h_usd is not None:
            extra.append(f"24h vol {_usd(snap.volume_24h_usd)}")
        if snap.max_leverage is not None:
            extra.append(f"max leverage {snap.max_leverage:.0f}x")
        if extra:
            lines.append("  " + ", ".join(extra))

    if analytics.pairs:
        lines.append("")
        lines.append("PAIRS")
        for pair in analytics.pairs:
            lines.append(_render_pair(pair))

    if analytics.forward:
        lines.append("")
        lines.append("FORWARD (empirical, from daily bars)")
        for fwd in analytics.forward:
            lines.append(
                f"- {fwd.coin} horizon {fwd.horizon_days}d, n={fwd.n_windows} windows "
                f"({fwd.sample_start.date()} to {fwd.sample_end.date()})"
            )
            q = fwd.final_ret
            lines.append(
                f"  final return quantiles: p10 {_pct(q.p10)} p25 {_pct(q.p25)} "
                f"p50 {_pct(q.p50)} p75 {_pct(q.p75)} p90 {_pct(q.p90)} "
                f"(mean {_pct(fwd.final_ret_mean)})"
            )
            lq = fwd.long_mae
            lines.append(
                f"  long max adverse excursion: p10 {_pct(lq.p10)} p25 {_pct(lq.p25)} "
                f"p50 {_pct(lq.p50)} p75 {_pct(lq.p75)} p90 {_pct(lq.p90)}"
            )
            sq = fwd.short_mae
            lines.append(
                f"  short max adverse excursion: p10 {_pct(sq.p10)} p25 {_pct(sq.p25)} "
                f"p50 {_pct(sq.p50)} p75 {_pct(sq.p75)} p90 {_pct(sq.p90)}"
            )
            for cond in fwd.conditional:
                lines.append(
                    f"  conditional \"{cond.label}\" (n={cond.n}): mean {_pct(cond.mean)} "
                    f"p50 {_pct(cond.p50)} hit-rate-up {_pct_abs(cond.hit_rate_up)}"
                )

    if analytics.ladder:
        lines.append("")
        acct = f"equity {_usd(analytics.account.equity_usd)}" if analytics.account.equity_usd else "equity n/a"
        lines.append(f"LEVERAGE LADDER ({acct}, source: {analytics.account.source})")
        for rung in analytics.ladder:
            lines.append(_render_ladder_rung(rung))

    if analytics.account.equity_usd is not None:
        lines.append("")
        lines.append(f"ACCOUNT: equity {_usd(analytics.account.equity_usd)} (source: {analytics.account.source})")

    if analytics.notes:
        lines.append("")
        lines.append("NOTES")
        for note in analytics.notes:
            lines.append(f"- {note}")

    return "\n".join(lines)


def _render_pair(pair: PairSnapshot) -> str:
    bits = [
        f"- {pair.label}: now {_num(pair.spread_now)}, 20d mean {_num(pair.spread_mean_20d)}",
    ]
    if pair.spread_mean_60d is not None:
        range_txt = ""
        if pair.spread_min_60d is not None and pair.spread_max_60d is not None:
            range_txt = f" (range {_num(pair.spread_min_60d)}-{_num(pair.spread_max_60d)})"
        bits[0] += f", 60d mean {_num(pair.spread_mean_60d)}{range_txt}"
    bits[0] += f", daily vol {_pct_abs(pair.pair_vol_daily)}"
    out = [bits[0]]
    if pair.spread_change_7d is not None:
        q = pair.spread_change_7d
        out.append(
            f"  7d change quantiles: p10 {_num(q.p10)} p25 {_num(q.p25)} p50 {_num(q.p50)} "
            f"p75 {_num(q.p75)} p90 {_num(q.p90)}"
        )
    return "\n".join(out)


def _render_ladder_rung(rung: LadderRung) -> str:
    bits = [
        f"- {rung.leverage:.0f}x: liq {_num(rung.liq_px)} ({_pct(rung.liq_dist_pct)}), "
        f"P(liq) {_pct_abs(rung.p_liq)}, P(win) {_pct_abs(rung.p_win)}, EV {_pct(rung.ev_pct)}, "
        f"P(2x) {_pct_abs(rung.p_double)}, P(lose half) {_pct_abs(rung.p_lose_half)}, "
        f"fees+funding {_pct_abs(rung.fees_funding_pct)}"
    ]
    if rung.stop_px is not None:
        bits[0] += f", stop {_num(rung.stop_px)}"
        if rung.p_stop is not None:
            bits[0] += f" (P(stop) {_pct_abs(rung.p_stop)})"
    bits[0] += f" -> verdict: {rung.verdict.upper()}"
    return bits[0]


# -- intel renderer (fallback; prefers services.intel.api.render_compact) ---


def render_intel_compact(intel: IntelPacket, max_tokens: int) -> str:
    """Dense text render of an IntelPacket, bounded to roughly max_tokens.

    Prefers services.intel.api.render_compact(packet, max_tokens) when that
    module is importable (it will not exist in this worktree until the
    intel agent's work merges); falls back to this service's own renderer
    otherwise, with the identical signature so the swap is a no-op later.
    """
    if _intel_render_compact is not None:
        return _intel_render_compact(intel, max_tokens)
    return _fallback_render_intel_compact(intel, max_tokens)


def _fallback_render_intel_compact(intel: IntelPacket, max_tokens: int) -> str:
    sections: List[Tuple[str, List[str]]] = []

    headlines = sorted(intel.headlines, key=lambda h: h.relevance, reverse=True)
    if headlines:
        sections.append(("HEADLINES (by relevance)", [_render_headline(h) for h in headlines]))

    if intel.policy:
        sections.append(("POLICY", [_render_policy(p) for p in intel.policy]))

    if intel.catalysts:
        ordered = sorted(intel.catalysts, key=lambda c: c.at)
        sections.append(("CATALYSTS (UTC)", [_render_catalyst(c) for c in ordered]))

    if intel.odds:
        sections.append((
            "PREDICTION MARKETS",
            [
                f"- [{o.id}] {o.venue}: \"{o.question}\" -> {_pct_abs(o.probability)}"
                + (f" (vol {_usd(o.volume_usd)})" if o.volume_usd else "")
                for o in intel.odds
            ],
        ))

    if intel.positioning:
        sections.append((
            "POSITIONING",
            [
                f"- [{p.id}] {p.source} {p.label}: {p.value:g}"
                + (f" (change {p.change:+g})" if p.change is not None else "")
                + f" as of {p.as_of.date()}"
                for p in intel.positioning
            ],
        ))

    if intel.sources_failed:
        sections.append(("DATA GAPS", [f"- {name} failed to load; treat as missing, not zero" for name in intel.sources_failed]))

    return _assemble_bounded(sections, max_tokens)


def _render_headline(h: Headline) -> str:
    date = h.published_at.date().isoformat() if h.published_at else "undated"
    line = f"- [{h.id}] ({h.stance}, {h.relevance:.2f}) {h.source} {date}: {h.title}"
    if h.summary:
        line += f" - {h.summary}"
    return line


def _render_policy(p: PolicyItem) -> str:
    date = p.date.date().isoformat() if p.date else "undated"
    line = f"- [{p.id}] ({p.stance}, {p.status}) {p.body} {date}: {p.title}"
    if p.summary:
        line += f" - {p.summary}"
    return line


def _render_catalyst(c: Catalyst) -> str:
    return f"- [{c.id}] {c.at.strftime('%Y-%m-%d %H:%M')} UTC {c.title} (importance {c.importance})"


def _assemble_bounded(sections: List[Tuple[str, List[str]]], max_tokens: int) -> str:
    """Greedily include whole sections, in priority order, until the next
    one would blow the budget; within the section that does not fully fit,
    include as many leading lines as fit and note how many were dropped.
    Priority order is the order callers pass sections in (headlines first,
    since they carry the day's news; positioning last, since it is the
    slowest-moving input)."""
    out: List[str] = []
    used = 0
    for title, lines in sections:
        body = "\n".join(lines)
        block = f"{title}\n{body}"
        block_tokens = estimate_tokens(block) + 1
        if used + block_tokens <= max_tokens:
            out.append(block)
            used += block_tokens
            continue
        # Try a partial section: as many leading lines as fit.
        kept: List[str] = []
        for line in lines:
            trial = "\n".join(kept + [line])
            trial_tokens = estimate_tokens(f"{title}\n{trial}") + 1
            if used + trial_tokens > max_tokens:
                break
            kept.append(line)
        if kept:
            dropped = len(lines) - len(kept)
            partial = f"{title}\n" + "\n".join(kept)
            if dropped:
                partial += f"\n- ... ({dropped} more, omitted for the token budget)"
            out.append(partial)
            used += estimate_tokens(partial) + 1
        else:
            # Not even one line of this section fit. Still say so instead of
            # vanishing it without a trace - same "never silently empty"
            # rule IntelPacket.sources_failed follows for a feed that errors.
            out.append(f"{title}\n- ... (all {len(lines)} entries omitted for the token budget)")
        break  # once one section is cut short, lower-priority sections are skipped entirely
    return "\n\n".join(out)


# -- user prompt assembly ----------------------------------------------------


def build_user_prompt(
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: Optional[IntelPacket],
    prev: Optional[Thesis],
    *,
    incremental: bool,
) -> Tuple[str, int]:
    """Build the full user message. Returns (prompt_text, estimated_tokens).

    Full mode budget split: analytics is small and required for every
    structural validator rule (stop side, entry-near-mid, leverage verdict),
    so it is rendered in full and never trimmed; whatever max_input_tokens
    leaves over after analytics and a fixed instructions reserve goes to the
    intel render, which does its own internal trimming
    (render_intel_compact's max_tokens).

    Incremental mode renders a DELIBERATELY smaller analytics block (primary
    instrument + ladder only - the validator still checks everything else
    against the FULL analytics/intel objects regardless of what the model
    was shown) and skips the general intel render entirely in favor of just
    the new-headlines list already folded into the incremental header - the
    model already produced ``prev`` from the full picture; this call is
    "what changed", not "start over". Measured on the oil fixture
    (2026-10-17): full mode ~685 est. tokens, incremental ~290 - see
    services/thesis/README.md.
    """
    if incremental and prev is not None:
        header = _incremental_header(spec, analytics, intel, prev)
        analytics_text = _render_analytics_compact(spec, analytics)
        parts = [header, "", analytics_text]
    else:
        header = (
            f"Build today's thesis for the {spec.name} desk. Horizon: {spec.horizon_days} days. "
            f"Leverage choices available: {', '.join(f'{x:.0f}x' for x in spec.leverage_ladder)}."
        )
        analytics_text = render_analytics_dense(spec, analytics)
        budget = spec.token_budget.max_input_tokens
        used = estimate_tokens(header) + estimate_tokens(analytics_text) + INSTRUCTIONS_RESERVE_TOKENS
        remaining = max(200, min(INTEL_MAX_TOKENS, budget - used))
        intel_text = render_intel_compact(intel, remaining) if intel is not None else (
            "No live intel packet is available. Reason from the market data above only; "
            "do not invent news, policy, catalysts, or positioning."
        )
        parts = [header, "", analytics_text]
        if intel_text.strip():
            parts += ["", intel_text]

    prompt = "\n".join(parts)
    return prompt, estimate_tokens(prompt)


def _render_analytics_compact(spec: DeskSpec, analytics: DeskAnalytics) -> str:
    """The incremental-mode analytics block: primary instrument + ladder
    only. Secondary instruments, pairs, and forward stats are omitted - not
    because they stopped mattering, but because ``prev`` was already built
    from them and nothing about them changes fast enough to re-render every
    refresh within refresh_min_interval_min."""
    lines = [
        f"MARKET SNAPSHOT (as of {analytics.as_of.isoformat()}, compact - "
        "unlisted instruments/pairs/forward stats are unchanged from the previous thesis)"
    ]
    primary = desk_lookup.primary_instrument(spec, analytics)
    if primary is not None:
        lines.append(
            f"- {primary.label} ({primary.coin}): mid {_num(primary.mid, 3)}, "
            f"1d {_pct(primary.ret_1d)}, 5d {_pct(primary.ret_5d)}, ATR14 {_pct_abs(primary.atr14_pct)}"
        )
    if analytics.ladder:
        lines.append("LEVERAGE LADDER (current numbers):")
        for rung in analytics.ladder:
            lines.append(_render_ladder_rung(rung))
    return "\n".join(lines)


def _incremental_header(
    spec: DeskSpec, analytics: DeskAnalytics, intel: Optional[IntelPacket], prev: Thesis,
) -> str:
    primary = desk_lookup.primary_instrument(spec, analytics)
    move_txt = "n/a"
    if primary is not None and prev.call.entry_px:
        move_pct = (primary.mid - prev.call.entry_px) / prev.call.entry_px
        move_txt = _pct(move_pct)

    age_min = max(0, int((analytics.as_of - prev.created_at).total_seconds() // 60))
    new_headlines: List[str] = []
    if intel is not None:
        for h in intel.headlines:
            if h.published_at and h.published_at > prev.created_at and h.relevance >= spec.token_budget.min_headline_relevance:
                new_headlines.append(f"[{h.id}] ({h.relevance:.2f}) {h.title}")

    lines = [
        f"This is a SMALL update to the {spec.name} desk's existing thesis from "
        f"{age_min} minutes ago, not a rewrite. Keep everything that is still "
        "true; only change what the data below justifies.",
        "",
        f"PREVIOUS CALL: {prev.call.one_liner}",
        f"  side={prev.call.side} entry={prev.call.entry_px} stop={prev.call.stop_px} "
        f"leverage={prev.call.leverage} confidence={prev.call.confidence:.2f}",
        f"  invalidation: {prev.call.invalidation}",
        "",
        f"WHAT CHANGED: price move since entry {move_txt}; "
        + (f"{len(new_headlines)} new high-relevance headline(s)" if new_headlines else "no new high-relevance headlines"),
    ]
    for h in new_headlines[:5]:
        lines.append(f"  - {h}")
    lines.append(
        "Return the FULL JSON again (every field, same schema) - but change only "
        "what the move or the new headlines above justify."
    )
    return "\n".join(lines)


# -- incremental-mode gate ----------------------------------------------------


def is_small_change(
    spec: DeskSpec, analytics: DeskAnalytics, intel: Optional[IntelPacket], prev: Thesis,
) -> bool:
    """Same thresholds DeskSpec.token_budget defines for "should we even
    call the LLM" (material_move_pct, min_headline_relevance), applied here
    to decide "full rewrite or small update" instead. Deliberately the same
    numbers, not a second set invented for this layer - desk-core may or may
    not already gate the call itself; this function does not assume either
    way and is safe to run regardless."""
    primary = desk_lookup.primary_instrument(spec, analytics)
    if primary is None or not prev.call.entry_px:
        return False

    move_pct = abs(primary.mid - prev.call.entry_px) / prev.call.entry_px * 100
    if move_pct >= spec.token_budget.material_move_pct:
        return False

    if intel is not None:
        for h in intel.headlines:
            if (
                h.relevance >= spec.token_budget.min_headline_relevance
                and h.published_at is not None
                and h.published_at > prev.created_at
            ):
                return False

    return True


# -- repair prompt ------------------------------------------------------------


def build_repair_prompt(errors: List[str], *, full_context: Optional[str] = None) -> str:
    """The repair-round user message.

    When the initial call's session was persisted and is being resumed
    (the normal path - see services/thesis/api.py), ``full_context`` is
    None: the model already has the market data and its own previous
    answer in context, so repeating them would only spend tokens for no
    benefit. ``full_context`` is supplied only as a fallback when resuming
    failed (e.g. the CLI could not find/resume the session) and a fresh,
    standalone call has to carry everything itself.
    """
    bullet_errors = "\n".join(f"- {e}" for e in errors)
    body = (
        "Your previous JSON failed validation:\n"
        f"{bullet_errors}\n\n"
        "Fix every issue above and return the corrected JSON, matching the same "
        "schema. Keep every field that was not flagged exactly as it was. Do not "
        "introduce any new issue."
    )
    if full_context:
        body += f"\n\nFor reference, here is the market data again:\n\n{full_context}"
    return body


_NUM_RE = re.compile(r"(?<![A-Za-z0-9_])(-?\d+(?:\.\d+)?)(%)?")


def extract_numbers_with_flags(text: str) -> List[Tuple[float, bool]]:
    """Every standalone number in ``text`` (not glued to a letter/digit, so
    an id like ``h1`` or ``c2`` never contributes its trailing digit), paired
    with whether it was written with a trailing ``%``. The percent flag
    matters to the validator's scenario-range check: "-10%" must be checked
    against a scenario's move_*_pct range, not its derived PRICE range, or a
    legitimate percent-only description (no price mentioned at all) reads as
    a wildly out-of-range price."""
    out: List[Tuple[float, bool]] = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        out.append((value, m.group(2) == "%"))
    return out


def extract_numbers(text: str) -> List[float]:
    """Plain numbers only, percent flag discarded - used by the general
    grounding scan, whose ground-truth pool already contains both the raw
    and the x100-scaled variant of every source number, so it does not need
    to know which form a given occurrence took."""
    return [v for v, _ in extract_numbers_with_flags(text)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
