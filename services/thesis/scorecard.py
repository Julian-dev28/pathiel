"""The desk's own graded track record, rendered as a compact text block for
the thesis prompt: deterministic, pure, no LLM involved.

    build_scorecard(theses, as_of=..., n=8) -> Scorecard
    render_scorecard(card) -> str

WHY THIS EXISTS
The operator, 2026-10-31: "does the thesis consider wins / losses? what we
did right? what could be improved?" It did not -- services/thesis/prompt.py
fed the writer price analytics, news/intel, and (incremental mode) the
previous call, but never a graded outcome. Grades already exist
(services/strategies/grader.py: direction_hit, Brier over the scenario
buckets, ret_pct); this module turns them into something a prompt can read.

NO LOOKAHEAD
Only theses whose grading window has CLOSED at `as_of` may appear: a thesis
counts only when it already carries a Grade (grader.grade_thesis is a
point-in-time read -- `grade is not None` on its own does not prove the
window closed, since a caller could grade early) AND `expires_at <= as_of`.
`eligible_theses` enforces both; the live pipeline (services.strategies.
pipeline.refresh) and the replay harness (services.thesis.evals.replay) both
go through it, so a thesis can never leak into its own future calibration.

SIZE
Bounded by construction, not by trimming: `n` (default 8) caps the number of
rows before any text is rendered, so the block stays a few hundred tokens
regardless of how large the desk's journal grows -- no separate token-budget
logic is needed the way services/thesis/prompt.py's intel renderer needs one
for an unbounded news feed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence

from contracts.strategies.models import Scenario, Thesis

from services.strategies.grader import find_scenario_bucket

DEFAULT_N = 8

SCORECARD_ENV_VAR = "PATHIEL_THESIS_SCORECARD"

# Below this many graded calls, the calibration numbers are stated but
# flagged as weak evidence -- the same "small sample" caution the system
# prompt rule repeats, so the model sees it twice: once as a plain fact in
# the data, once as an instruction.
SMALL_SAMPLE_N = 10


def scorecard_enabled() -> bool:
    """Whether the live pipeline should build and inject a SCORECARD section
    into the thesis prompt. Defaults OFF -- set PATHIEL_THESIS_SCORECARD=1
    (or true/yes/on) to turn it on once the eval says it helps. Read fresh
    every call (never cached), so flipping the env var takes effect on the
    desk's next refresh without a restart."""
    raw = (os.environ.get(SCORECARD_ENV_VAR) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ScorecardRow:
    created_at: datetime
    side: str
    instrument: str
    entry_px: Optional[float]
    direction_hit: Optional[bool]          # None for a flat call (no direction to grade)
    ret_pct: float                         # direction-adjusted, same sign convention as Grade.ret_pct
    brier: Optional[float]
    # Scenario calibration, both None when the thesis wrote no scenarios or
    # the realized return could not be matched to a bucket (no entry_px).
    actual_bucket_name: Optional[str] = None
    actual_bucket_probability: Optional[float] = None   # what the thesis gave the bucket that happened
    top_scenario_name: Optional[str] = None              # the thesis's own highest-probability pick
    top_scenario_probability: Optional[float] = None
    was_top_pick: Optional[bool] = None                  # did the top pick turn out to be what happened


@dataclass(frozen=True)
class Scorecard:
    n: int
    rows: List[ScorecardRow] = field(default_factory=list)
    direction_hit_rate: Optional[float] = None
    mean_brier: Optional[float] = None
    top_scenario_mean_probability: Optional[float] = None
    top_scenario_hit_rate: Optional[float] = None


def eligible_theses(theses: Sequence[Thesis], as_of: datetime) -> List[Thesis]:
    """Graded theses whose grading window has closed by `as_of` -- see the
    module docstring's NO LOOKAHEAD section. Sorted oldest first."""
    rows = [t for t in theses if t.grade is not None and t.expires_at <= as_of]
    return sorted(rows, key=lambda t: t.created_at)


def _raw_final_ret(thesis: Thesis) -> Optional[float]:
    """outcome_px / entry_px - 1, undoing Grade.ret_pct's direction sign so
    it can be matched against a scenario bucket the same way
    grader._brier matches it (buckets are written as raw price-move
    fractions, not direction-adjusted, per Scenario's own doc). None when
    the call has no entry_px to divide by."""
    entry = thesis.call.entry_px
    if not entry or thesis.grade is None:
        return None
    return thesis.grade.outcome_px / entry - 1.0


def _top_scenario(scenarios: Sequence[Scenario]) -> Optional[Scenario]:
    return max(scenarios, key=lambda s: s.probability) if scenarios else None


def _row(thesis: Thesis) -> ScorecardRow:
    grade = thesis.grade
    assert grade is not None  # eligible_theses already filtered this
    call = thesis.call

    actual_name = actual_prob = top_name = top_prob = was_top = None
    raw_ret = _raw_final_ret(thesis)
    if thesis.scenarios and raw_ret is not None:
        actual = find_scenario_bucket(thesis.scenarios, raw_ret)
        top = _top_scenario(thesis.scenarios)
        if actual is not None:
            actual_name, actual_prob = actual.name, actual.probability
        if top is not None:
            top_name, top_prob = top.name, top.probability
        if actual is not None and top is not None:
            was_top = actual is top

    return ScorecardRow(
        created_at=thesis.created_at, side=call.side, instrument=call.instrument,
        entry_px=call.entry_px, direction_hit=grade.direction_hit, ret_pct=grade.ret_pct,
        brier=grade.brier, actual_bucket_name=actual_name, actual_bucket_probability=actual_prob,
        top_scenario_name=top_name, top_scenario_probability=top_prob, was_top_pick=was_top,
    )


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return (sum(present) / len(present)) if present else None


def build_scorecard(theses: Sequence[Thesis], *, as_of: datetime, n: int = DEFAULT_N) -> Scorecard:
    """The desk's last `n` graded calls as of `as_of`, oldest first (the
    order a prompt should read them in: history leading up to today).
    Pure -- same theses + same as_of always produce the same Scorecard."""
    closed = eligible_theses(theses, as_of)
    recent = closed[-n:] if n else closed
    rows = [_row(t) for t in recent]

    directions = [r.direction_hit for r in rows if r.direction_hit is not None]
    briers = [r.brier for r in rows if r.brier is not None]
    top_probs = [r.top_scenario_probability for r in rows if r.top_scenario_probability is not None]
    top_hits = [r.was_top_pick for r in rows if r.was_top_pick is not None]

    return Scorecard(
        n=len(rows), rows=rows,
        direction_hit_rate=_mean([1.0 if d else 0.0 for d in directions]) if directions else None,
        mean_brier=_mean(briers),
        top_scenario_mean_probability=_mean(top_probs),
        top_scenario_hit_rate=_mean([1.0 if h else 0.0 for h in top_hits]) if top_hits else None,
    )


# -- rendering ----------------------------------------------------------------

_SECTION_TITLE = "SCORECARD"


def _render_row(r: ScorecardRow) -> str:
    date = r.created_at.date().isoformat()
    entry_txt = f"{r.entry_px:g}" if r.entry_px is not None else "n/a"
    if r.direction_hit is None:
        dir_txt = "direction n/a (flat)"
    else:
        dir_txt = "direction HIT" if r.direction_hit else "direction MISS"
    ret_txt = f"{r.ret_pct * 100:+.1f}%"
    bits = [f"- {date} {r.side} {r.instrument} entry {entry_txt}: {dir_txt}, return {ret_txt}"]

    if r.actual_bucket_name is not None and r.actual_bucket_probability is not None:
        prob_txt = f"{r.actual_bucket_probability * 100:.0f}%"
        if r.was_top_pick:
            bits.append(f"top pick \"{r.actual_bucket_name}\" ({prob_txt}) is what happened")
        else:
            top_txt = (f"\"{r.top_scenario_name}\" ({r.top_scenario_probability * 100:.0f}%)"
                      if r.top_scenario_name else "n/a")
            bits.append(f"gave {prob_txt} to what happened (\"{r.actual_bucket_name}\"); "
                       f"top pick was {top_txt}")
    if r.brier is not None:
        bits.append(f"Brier {r.brier:.2f}")

    return bits[0] + " (" + "; ".join(bits[1:]) + ")" if len(bits) > 1 else bits[0]


def render_scorecard(card: Scorecard) -> str:
    """Always at least one line, even for n=0 -- "zero graded theses" is
    itself the finding the model needs to see, never a silently empty
    section (same discipline as prompt.py's intel DATA GAPS)."""
    if card.n == 0:
        return (f"{_SECTION_TITLE} (track record): no graded theses yet for this desk. "
               "n=0: there is no track record to calibrate against yet.")

    lines = [f"{_SECTION_TITLE} (track record, last {card.n} graded call"
            f"{'s' if card.n != 1 else ''} -- use this to calibrate confidence and "
            "scenario probabilities, not to flip side on one loss)"]
    lines.extend(_render_row(r) for r in card.rows)

    hit_txt = f"{card.direction_hit_rate * 100:.0f}%" if card.direction_hit_rate is not None else "n/a"
    brier_txt = f"{card.mean_brier:.2f}" if card.mean_brier is not None else "n/a"
    lines.append(f"Summary: n={card.n}, direction hit rate {hit_txt}, mean Brier {brier_txt} "
                "(0 = perfectly calibrated scenarios, 2 = maximally wrong)")

    if card.top_scenario_mean_probability is not None and card.top_scenario_hit_rate is not None:
        n_top = sum(1 for r in card.rows if r.was_top_pick is not None)
        n_hit = sum(1 for r in card.rows if r.was_top_pick)
        lines.append(
            f"Calibration: your top scenario averaged {card.top_scenario_mean_probability * 100:.0f}% "
            f"confidence and was the bucket that actually happened {n_hit} of {n_top} times "
            f"({card.top_scenario_hit_rate * 100:.0f}%)."
        )

    if card.n < SMALL_SAMPLE_N:
        lines.append(f"Sample size is small (n={card.n}): treat this as weak evidence, not a "
                     "verdict. Do not flip side because of one loss.")

    return "\n".join(lines)
