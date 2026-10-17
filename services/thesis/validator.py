"""The anti-hallucination gate.

Every rule here is deterministic - same draft, same analytics, same intel,
same verdict, every time, which is why this is a validator and not another
LLM call. Two public entry points share one rule set:

    validate_draft(draft, spec, analytics, intel) -> list[str]   # pre-assembly
    validate_thesis(thesis, spec, analytics, intel) -> list[str]  # post-assembly

An empty list means "passed". A non-empty list is both the repair-round
instructions (fed back to the model verbatim) and the eval's per-check
signal.

Rule inventory, and why each one is the shape it is:

- scenario count (3-5), sum-to-one, non-overlapping buckets: contract-level
  sanity on the LLM's own return-bucket construction. Sum-to-one duplicates
  contracts.strategies.models.Thesis's own model_validator deliberately -
  catching it here, on the raw draft, produces a specific, repairable error
  message instead of a generic pydantic ValidationError after assembly.
- scenario numbers derived: a scenario's own description/triggers often
  states a price level implied by its move_lo_pct/move_hi_pct (e.g. "WTI
  103-106" for a +2%/+50% bucket off a $99 mid) - that price is NOT a literal
  analytics/intel number, it is a derived one, so it is checked against the
  bucket's own implied range (with slack) rather than the flat grounding
  pool.
- stop side, entry-near-mid, targets-consistent, leverage-not-avoid,
  instrument-known: structural sanity on the Call the LLM is authoring as
  the actual trade idea.
- cited ids exist: every bull/bear source id must resolve inside the given
  IntelPacket.
- lessons count (exactly 1), leverage_reasons count (exactly
  len(analytics.ladder)): shape checks the JSON schema already biases
  toward, re-checked here with a specific message in case a model drifts
  off-schema anyway.
- numbers grounded: the general anti-hallucination scan, applied to the
  genuinely free-text fields (one_liner, invalidation, bull/bear text,
  lesson text, what-changes-my-mind, leverage reasons). See
  ``build_ground_truth_pool`` for exactly what counts as grounded and why -
  and its docstring for the known false-positive/false-negative edges.

Where this would break (read before trusting it blindly): a number typed
into prose using words ("ninety-nine") is invisible to the regex scanner,
so a hallucination phrased in words slips through - the system prompt asks
for numerals specifically, which is what the rest of the codebase does too,
but it is a prompt-level mitigation, not a structural guarantee. A raw ISO
date typed into prose (rather than spelled in words, as the system prompt
asks) can spuriously fail grounding on its day/hour components - a false
positive, which costs one repair round but does not let a real
hallucination through. The scenario "price-like" magnitude test
(0.3x-3x of the primary instrument's mid) can misclassify a number for an
instrument whose mid swings more than 3x within one desk's recent history;
none of this project's current desks do.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Tuple

from pydantic import BaseModel

from contracts.strategies.models import (
    Call, DeskAnalytics, DeskSpec, IntelPacket, Lesson, Point, Scenario, Thesis,
)
from services.thesis import desk_lookup
from services.thesis.llm_schema import ThesisDraft
from services.thesis.prompt import extract_numbers, extract_numbers_with_flags

# -- ground-truth pool --------------------------------------------------------

_TINY_INT_ALLOWANCE = {0.0, 1.0, 2.0, 3.0}
# Day-window conventions baked into this project's own field names
# (ret_1d/5d/20d/60d, atr14, vol_daily_10d/20d/60d, high/low_20d/60d,
# horizon_days) - these appear in prose as "20d low" or "14-day ATR" without
# ever being a literal VALUE anywhere in the payload (the "20" lives in the
# field NAME, not a leaf), so they are allowed outright rather than chased
# through field-name parsing.
_WINDOW_SIZE_ALLOWANCE = {1.0, 5.0, 7.0, 10.0, 14.0, 20.0, 60.0, 90.0}


def _is_calendar_year(v: float) -> bool:
    return v == int(v) and 1900 <= v <= 2100


def _walk_numbers(obj: object) -> Iterator[float]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_numbers(v)


def _numeric_leaves(model: Optional[BaseModel]) -> List[float]:
    if model is None:
        return []
    return list(_walk_numbers(model.model_dump(mode="python")))


def _analytics_text_blob(analytics: DeskAnalytics) -> str:
    parts = list(analytics.notes)
    for fwd in analytics.forward:
        for cond in fwd.conditional:
            parts.append(cond.label)
    return "\n".join(parts)


def _intel_text_blob(intel: IntelPacket) -> str:
    parts: List[str] = []
    for h in intel.headlines:
        parts.append(h.title)
        if h.summary:
            parts.append(h.summary)
    for p in intel.policy:
        parts.append(p.title)
        if p.summary:
            parts.append(p.summary)
    for c in intel.catalysts:
        parts.append(c.title)
    for o in intel.odds:
        parts.append(o.question)
    for pos in intel.positioning:
        parts.append(pos.label)
    return "\n".join(parts)


def _scaled_variants(v: float) -> Iterator[float]:
    """v itself, plus the alternate scale prose commonly uses for it.

    The x100 (fraction -> percent) variant is gated to |v| <= 1.5 on
    purpose: that covers every fraction-shaped field this contract has
    (returns, rates, probabilities, EV, fees - all naturally within
    roughly -1..1) without also firing on a PRICE that happens to be
    numerically small, which would be wrong. The gate matters: ungated, a
    WTI mid of 99.339 produces a ground-truth entry of 9933.9, whose own
    1.5%-relative tolerance band (+-149) is wide enough to "ground" a
    clearly invented price like 9999 - caught by this project's own
    adversarial test for exactly that number.
    """
    yield v
    if v < 0:
        # Prose drops the sign when the verb already carries direction
        # ("fell by 10403 contracts" for a change of -10403) - caught live
        # 2026-10-17 on the real oil eval: positioning.change=-10403,
        # repair-round prose wrote "10403" with no minus sign.
        yield -v
    if abs(v) <= 1.5:
        yield v * 100.0
    if abs(v) >= 1000:
        yield v / 1_000.0
        yield v / 1_000_000.0
        yield v / 1_000_000_000.0


def build_ground_truth_pool(analytics: DeskAnalytics, intel: Optional[IntelPacket]) -> set:
    """Every number the LLM is allowed to cite in free text: numeric leaves
    of analytics/intel (plus their percent/K/M/B-scaled variants, since
    prose rounds and rescales), plus numbers that appear inside intel's own
    string fields (a headline title like "...drop 8.6 million barrels" is a
    legitimate source for the LLM writing "8.6M bbl draw" even though 8.6 is
    not a numeric *leaf* anywhere - it is text intel actually said)."""
    pool: set = set()
    for v in _numeric_leaves(analytics):
        pool.update(_scaled_variants(v))
    for n in extract_numbers(_analytics_text_blob(analytics)):
        pool.add(n)
    if intel is not None:
        for v in _numeric_leaves(intel):
            pool.update(_scaled_variants(v))
        for n in extract_numbers(_intel_text_blob(intel)):
            pool.add(n)
    return pool


def _self_reference_pool(call: Call, spec: DeskSpec) -> set:
    """Numbers the LLM just authored itself in ``call`` (checked by their
    own dedicated rules) and the desk's own static config - citing these
    elsewhere in prose (e.g. the one-liner repeating the stop price) is
    expected, not a hallucination."""
    pool: set = set()
    for v in (call.entry_px, call.stop_px, call.leverage, call.confidence):
        if v is not None:
            pool.update(_scaled_variants(float(v)))
    for t in call.targets:
        pool.add(float(t))
    if call.entry_zone:
        pool.update(float(x) for x in call.entry_zone)
    pool.update(float(x) for x in spec.leverage_ladder)
    pool.add(float(spec.horizon_days))
    return pool


def _is_grounded(value: float, pool: set, *, rel: float = 0.015, abs_tol: float = 0.05) -> bool:
    if value in _TINY_INT_ALLOWANCE or value in _WINDOW_SIZE_ALLOWANCE:
        return True
    if _is_calendar_year(value):
        return True
    for g in pool:
        if abs(value - g) <= max(rel * abs(g), abs_tol):
            return True
    return False


# -- individual rules ---------------------------------------------------------


def check_scenario_count(scenarios: List[Scenario]) -> List[str]:
    n = len(scenarios)
    if not (3 <= n <= 5):
        return [f"scenarios must have 3 to 5 entries, got {n}"]
    return []


def check_scenarios_sum_to_one(scenarios: List[Scenario]) -> List[str]:
    if not scenarios:
        return []
    total = sum(s.probability for s in scenarios)
    if abs(total - 1.0) > 0.02:
        return [f"scenario probabilities sum to {total:.3f}, must sum to 1.0 (tolerance 0.02)"]
    return []


def check_scenarios_non_overlapping(scenarios: List[Scenario]) -> List[str]:
    errors: List[str] = []
    for s in scenarios:
        if s.move_lo_pct >= s.move_hi_pct:
            errors.append(
                f"scenario {s.name!r} has move_lo_pct >= move_hi_pct "
                f"({s.move_lo_pct} >= {s.move_hi_pct})"
            )
    ordered = sorted(scenarios, key=lambda s: s.move_lo_pct)
    for a, b in zip(ordered, ordered[1:]):
        if b.move_lo_pct < a.move_hi_pct - 1e-9:
            errors.append(
                f"scenarios {a.name!r} [{a.move_lo_pct},{a.move_hi_pct}) and "
                f"{b.name!r} [{b.move_lo_pct},{b.move_hi_pct}) overlap"
            )
    return errors


def check_scenario_numbers(
    scenarios: List[Scenario], spec: DeskSpec, analytics: DeskAnalytics,
    pool: Optional[set] = None,
) -> List[str]:
    """Every number in a scenario's description/triggers must be consistent
    with that scenario's OWN move_lo_pct/move_hi_pct bucket - a derived
    check, not a grounding-pool lookup, because "WTI 103-106" for a
    +2%/+50% bucket off a $99 mid is a real, correct number nobody's data
    payload states literally. A number written with a trailing ``%`` is
    checked against the bucket's own percent range; a bare number is
    checked against the bucket's derived PRICE range (percent and price
    ranges are not interchangeable - "-10% to -15%" must not be read as a
    price, or a scenario with no price mention at all reads as nonsense).
    A bare number that misses the price range still passes if it is a tiny
    integer, a day-window size, or a calendar year (same universal
    allowances as the general grounding check) - a day-of-month inside a
    spelled-out trigger should not force a repair round.

    A number quoted near-exactly from the data (``pool``, the ground-truth
    pool the general grounding rule uses, at a much tighter tolerance) passes:
    it is a fact from the payload, not a scenario target. Live 2026-10-17: Polymarket odds (0.97),
    Brent's price (107.06) and a 20-day distance (7.01%) inside scenario text
    tripped this rule on every oil draft and forced a paid repair round each
    time. The rule exists to catch INVENTED scenario prices, which by
    definition are not in the pool.
    """
    primary = desk_lookup.primary_instrument(spec, analytics)
    mid = primary.mid if primary is not None and primary.mid else None
    errors: List[str] = []
    for s in scenarios:
        pct_lo, pct_hi = s.move_lo_pct * 100.0, s.move_hi_pct * 100.0
        pct_low, pct_high = min(pct_lo, pct_hi), max(pct_lo, pct_hi)
        pct_pad = max(2.0, 0.05 * (pct_high - pct_low))
        pct_low, pct_high = pct_low - pct_pad, pct_high + pct_pad

        price_low = price_high = None
        if mid is not None:
            raw_lo, raw_hi = mid * (1 + s.move_lo_pct), mid * (1 + s.move_hi_pct)
            price_low, price_high = min(raw_lo, raw_hi), max(raw_lo, raw_hi)
            price_pad = max(0.05 * mid, 0.05 * (price_high - price_low))
            price_low, price_high = price_low - price_pad, price_high + price_pad

        text = " ".join([s.description, *s.triggers])
        for value, is_pct in extract_numbers_with_flags(text):
            # Near-exact quotes only (0.2% / 0.005): the general rule's 1.5%
            # tolerance over a pool of hundreds of values would wave through
            # almost any number, which is exactly what this rule must catch.
            if pool is not None and _is_grounded(value, pool, rel=0.002, abs_tol=0.005):
                continue
            if is_pct:
                if not (pct_low <= value <= pct_high):
                    errors.append(
                        f"scenario {s.name!r} mentions {value:g}%, outside its own "
                        f"implied range [{pct_low:.1f}%, {pct_high:.1f}%]"
                    )
                continue
            if price_low is not None and price_low <= value <= price_high:
                continue
            # Crypto-scale prose commonly abbreviates ("120" meaning
            # "$120k") without a k/M suffix the regex can see. Only accept
            # the abbreviation if the SCALED value lands inside this
            # scenario's own narrow derived range - a tight, scenario-
            # specific window, not a blanket "numbers under 1000 are OK"
            # rule, so a genuinely wrong small number still fails. Caught
            # live 2026-10-17 on the synthetic BTC eval: "120" for a
            # scenario whose derived range was [111703, 131225].
            if price_low is not None and abs(value) < 1000 and (
                price_low <= value * 1_000 <= price_high or price_low <= value * 1_000_000 <= price_high
            ):
                continue
            if value in _TINY_INT_ALLOWANCE or value in _WINDOW_SIZE_ALLOWANCE or _is_calendar_year(value):
                continue
            range_txt = f" [{price_low:.2f}, {price_high:.2f}]" if price_low is not None else ""
            errors.append(f"scenario {s.name!r} mentions {value:g}, outside its own implied price range{range_txt}")
    return errors


def check_one_liner_length(call: Call) -> List[str]:
    if len(call.one_liner) > 160:
        return [f"call.one_liner is {len(call.one_liner)} chars, max 160"]
    return []


def check_stop_side(call: Call) -> List[str]:
    if call.side not in ("long", "short") or call.entry_px is None or call.stop_px is None:
        return []
    if call.side == "long" and not (call.stop_px < call.entry_px):
        return [f"long stop_px ({call.stop_px}) must be below entry_px ({call.entry_px})"]
    if call.side == "short" and not (call.stop_px > call.entry_px):
        return [f"short stop_px ({call.stop_px}) must be above entry_px ({call.entry_px})"]
    return []


def _resolve_price_reference(
    call: Call, analytics: DeskAnalytics,
) -> Optional[Tuple[float, float]]:
    """(reference_price, relative_vol) for call.instrument - spread_now /
    pair_vol_daily for a pair call, mid / atr14_pct for a single coin.
    None if call.instrument does not resolve to anything in analytics."""
    if call.side == "pair":
        pair = desk_lookup.find_pair(call.instrument, analytics)
        return (pair.spread_now, pair.pair_vol_daily) if pair is not None else None
    snap = desk_lookup.find_instrument(call.instrument, analytics)
    return (snap.mid, snap.atr14_pct) if snap is not None else None


def check_instrument_known(call: Call, analytics: DeskAnalytics) -> List[str]:
    if call.side == "flat":
        return []
    if _resolve_price_reference(call, analytics) is None:
        kind = "pair label" if call.side == "pair" else "coin"
        return [f"call.instrument {call.instrument!r} is not a known {kind} for this desk"]
    return []


def check_entry_near_mid(call: Call, analytics: DeskAnalytics) -> List[str]:
    if call.entry_px is None:
        return []
    ref = _resolve_price_reference(call, analytics)
    if ref is None:
        return []  # check_instrument_known already reports this
    mid, vol = ref
    if not mid:
        return []
    threshold = max(0.05, 3 * vol)
    diff = abs(call.entry_px - mid) / abs(mid)
    if diff > threshold:
        return [
            f"call.entry_px ({call.entry_px}) is {diff * 100:.1f}% from the reference "
            f"price ({mid:g}), beyond the {threshold * 100:.1f}% sanity band"
        ]
    return []


def check_targets_consistent(call: Call, analytics: DeskAnalytics) -> List[str]:
    if not call.targets or call.entry_px is None or call.side not in ("long", "short"):
        return []
    ref = _resolve_price_reference(call, analytics)
    mid, vol = ref if ref is not None else (call.entry_px, 0.05)
    cap = max(0.6, 10 * vol)
    errors: List[str] = []
    for t in call.targets:
        if call.side == "long" and t <= call.entry_px:
            errors.append(f"long target {t} must be above entry_px {call.entry_px}")
        if call.side == "short" and t >= call.entry_px:
            errors.append(f"short target {t} must be below entry_px {call.entry_px}")
        if mid and abs(t - mid) / abs(mid) > cap:
            errors.append(
                f"target {t} is implausibly far from the reference price {mid:g} "
                f"(more than {cap * 100:.0f}%)"
            )
    return errors


def check_leverage_not_avoid(call: Call, analytics: DeskAnalytics) -> List[str]:
    if call.side not in ("long", "short") or call.leverage is None:
        return []
    rung = desk_lookup.find_ladder_rung(call.leverage, analytics, coin=call.instrument,
                                        side=call.side)
    if rung is None:
        rung = desk_lookup.find_ladder_rung(call.leverage, analytics, side=call.side)
    if rung is None:
        return [f"call.leverage {call.leverage} is not one of the ladder's rungs"]
    if rung.verdict == "avoid":
        return [f"call.leverage {call.leverage:g}x has verdict 'avoid' and cannot be the call"]
    return []


def check_cited_ids_exist(bull: List[Point], bear: List[Point], intel: Optional[IntelPacket]) -> List[str]:
    universe = _citeable_id_universe(intel)
    errors: List[str] = []
    for label, points in (("bull", bull), ("bear", bear)):
        for i, pt in enumerate(points):
            for sid in pt.source_ids:
                if sid not in universe:
                    errors.append(f"{label}[{i}] cites source id {sid!r} which does not exist in intel")
    return errors


def _citeable_id_universe(intel: Optional[IntelPacket]) -> set:
    if intel is None:
        return set()
    ids = {h.id for h in intel.headlines}
    ids |= {p.id for p in intel.policy}
    ids |= {c.id for c in intel.catalysts}
    ids |= {o.id for o in intel.odds}
    ids |= {p.id for p in intel.positioning}
    return ids


def check_lessons_count(lessons: List[Lesson]) -> List[str]:
    if len(lessons) != 1:
        return [f"lessons must contain exactly 1 entry, got {len(lessons)}"]
    return []


def _lr_field(lr, name: str):
    """LeverageReason objects in production; dicts in hand-built test drafts."""
    return lr.get(name) if isinstance(lr, dict) else getattr(lr, name, None)


def check_leverage_reasons_count(leverage_reasons, analytics: DeskAnalytics) -> List[str]:
    """Exactly one reason per ladder leverage, each leverage named once."""
    expected = desk_lookup.ladder_leverages(analytics)
    try:
        got = [float(_lr_field(lr, "leverage")) for lr in leverage_reasons]
    except (TypeError, ValueError):
        return ["leverage_reasons entries must each be {leverage, reason}"]
    if sorted(got) != sorted(float(x) for x in expected):
        return [
            f"leverage_reasons must name each ladder leverage exactly once "
            f"({', '.join(f'{x:g}' for x in expected)}), got {', '.join(f'{x:g}' for x in got) or 'none'}"
        ]
    return []


def check_numbers_grounded(
    *,
    call: Call,
    bull: List[Point],
    bear: List[Point],
    lessons: List[Lesson],
    what_changes_my_mind: List[str],
    leverage_reasons: List[str],
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: Optional[IntelPacket],
) -> List[str]:
    pool = build_ground_truth_pool(analytics, intel)
    pool |= _self_reference_pool(call, spec)
    errors: List[str] = []

    def _scan(label: str, text: str) -> None:
        for n in extract_numbers(text):
            if not _is_grounded(n, pool):
                errors.append(f"{label} mentions {n:g}, which is not grounded in analytics or intel")

    _scan("call.one_liner", call.one_liner)
    _scan("call.invalidation", call.invalidation)
    for i, pt in enumerate(bull):
        _scan(f"bull[{i}].text", pt.text)
    for i, pt in enumerate(bear):
        _scan(f"bear[{i}].text", pt.text)
    for i, lesson in enumerate(lessons):
        _scan(f"lessons[{i}].explanation", lesson.explanation)
        _scan(f"lessons[{i}].tied_to", lesson.tied_to)
    for i, line in enumerate(what_changes_my_mind):
        _scan(f"what_changes_my_mind[{i}]", line)
    for i, lr in enumerate(leverage_reasons):
        _scan(f"leverage_reasons[{i}]", str(_lr_field(lr, "reason") or ""))
    return errors


# -- public entry points ------------------------------------------------------


def _run_all_rules(
    *,
    call: Call,
    scenarios: List[Scenario],
    bull: List[Point],
    bear: List[Point],
    lessons: List[Lesson],
    what_changes_my_mind: List[str],
    leverage_reasons: List[str],
    spec: DeskSpec,
    analytics: DeskAnalytics,
    intel: Optional[IntelPacket],
) -> List[str]:
    errors: List[str] = []
    errors += check_scenario_count(scenarios)
    errors += check_scenarios_sum_to_one(scenarios)
    errors += check_scenarios_non_overlapping(scenarios)
    errors += check_scenario_numbers(scenarios, spec, analytics,
                                     pool=build_ground_truth_pool(analytics, intel))
    errors += check_one_liner_length(call)
    errors += check_stop_side(call)
    errors += check_instrument_known(call, analytics)
    errors += check_entry_near_mid(call, analytics)
    errors += check_targets_consistent(call, analytics)
    errors += check_leverage_not_avoid(call, analytics)
    errors += check_cited_ids_exist(bull, bear, intel)
    errors += check_lessons_count(lessons)
    errors += check_leverage_reasons_count(leverage_reasons, analytics)
    errors += check_numbers_grounded(
        call=call, bull=bull, bear=bear, lessons=lessons,
        what_changes_my_mind=what_changes_my_mind, leverage_reasons=leverage_reasons,
        spec=spec, analytics=analytics, intel=intel,
    )
    return errors


def validate_draft(
    draft: ThesisDraft, spec: DeskSpec, analytics: DeskAnalytics, intel: Optional[IntelPacket],
) -> List[str]:
    return _run_all_rules(
        call=draft.call, scenarios=draft.scenarios, bull=draft.bull, bear=draft.bear,
        lessons=draft.lessons, what_changes_my_mind=draft.what_changes_my_mind,
        leverage_reasons=draft.leverage_reasons, spec=spec, analytics=analytics, intel=intel,
    )


def validate_thesis(
    thesis: Thesis, spec: DeskSpec, analytics: DeskAnalytics, intel: Optional[IntelPacket],
) -> List[str]:
    """Same rules, run against an already-assembled Thesis (used by the
    eval harness to score a finished write_thesis() result end to end)."""
    leverage_reasons = [{"leverage": lv.leverage, "reason": lv.reason} for lv in thesis.leverage_view]
    return _run_all_rules(
        call=thesis.call, scenarios=thesis.scenarios, bull=thesis.bull, bear=thesis.bear,
        lessons=thesis.lessons, what_changes_my_mind=thesis.what_changes_my_mind,
        leverage_reasons=leverage_reasons, spec=spec, analytics=analytics, intel=intel,
    )
