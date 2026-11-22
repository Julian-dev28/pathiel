#!/usr/bin/env python3
"""Replay eval for pathiel's desk theses: "Own Your Intelligence" for
trading. Every thesis the live desks have ever written is a complete eval
case on disk -- the inputs (``analytics_ref``, ``intel_ref``), the model's
answer (``call``, ``bull``, ``bear``, ``scenarios``,
``what_changes_my_mind``), and (once resolved) a ``grade``. This harness
replays the SAME saved inputs through model A vs model B, grades both
answers the same deterministic way (services.strategies.evals.
thesis_replay_scoring -- no LLM, no P&L), and prints a leaderboard. It
doubles as a regression gate for the live thesis prompt: a prompt change
that quietly makes the desk's calls worse shows up here before it ships.

    .venv/bin/python -m services.strategies.evals.thesis_replay \\
        --desk oil --dry-run                       # free: grades the ORIGINAL stored calls
    .venv/bin/python -m services.strategies.evals.thesis_replay \\
        --desk oil --models sonnet --limit 5        # real replay, 5 theses, one model

Read-only on $PATHIEL_STATE_DIR -- never writes a thesis, a grade, or
anything else under the live desks' journal. Every run writes its own
results.jsonl + a printed leaderboard under ``--out-dir`` (default
/tmp/thesis-replay/<run>/).

## Replay, and why most history is unreplayable

Reconstructing "the exact prompt the thesis writer used" means resolving
``thesis.analytics_ref``/``intel_ref`` (an ``as_of`` + a sha256 of the
payload, contracts.strategies.models.PayloadRef) back to the actual
DeskAnalytics/IntelPacket objects the live pipeline fed the model that day.
But services.strategies.pipeline only ever caches ONE analytics snapshot
and ONE intel snapshot per desk --
``$PATHIEL_STATE_DIR/strategies/<desk>/{analytics,intel}/latest.json`` --
overwritten on every refresh tick (every 15 minutes, whether or not a new
thesis was written that tick). By the time this harness runs, the cache
has moved on past every historical thesis's ref, including the desk's own
most recent one. ``resolve_replay_inputs`` below proves this per case by
literally re-hashing the live cache and comparing it to the ref (same
``services.thesis.hashing.hash_payload`` the pipeline itself used to build
the ref) -- a case is marked ``unreplayable`` the moment the hash does not
match, counted, and NEVER fed a reconstructed-from-elsewhere substitute:
the brief is explicit that inventing inputs is worse than admitting the
data is gone. This is expected to mark most (likely all) of today's
archived theses unreplayable; it is a real property of how the live
pipeline caches its inputs, not a bug in this harness, and the leaderboard
reports it plainly rather than silently.

This is orthogonal to the two deterministic scores: grading the ORIGINAL
stored call needs only the call itself (already fully persisted in the
thesis's own journal file) plus public candles after created_at -- never
the analytics/intel snapshot that produced it. So ``--dry-run`` (score the
original calls) still works, and is expected to score nearly every case,
even when every single case is unreplayable for model-vs-model replay.

## Hard rule

Per docs/strategy/SCORECARD_TEST_2026-10-31.md, a replayed prompt NEVER
carries the desk's own trade record or past grades -- ``build_replay_
prompt`` always calls ``services.thesis.prompt.build_user_prompt`` with
``prev=None`` and ``scorecard_text=None``, unconditionally, regardless of
whether the original thesis was written incrementally against a prior one.

## LLM access

Every model call goes through ``services.llm.complete`` (the project's one
sanctioned local-Claude-Code gateway, CLAUDE.md "LLM access") via
``ClaudeCodeAdapter``, or through the generic ``CommandAdapter`` for a
local CLI like ``ollama`` -- see thesis_replay_models.py. No hosted LLM
API is ever called. Unlike services.thesis.api.write_thesis, a failed
validation here is NOT retried with a repair round: the budget for this
eval is a fixed number of calls (``--limit`` theses x ``--models``
aliases), and a silent 2x-cost repair round on every miss would blow that
budget without the caller ever asking for it.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contracts.strategies.models import Call, DeskAnalytics, DeskSpec, IntelPacket, Thesis
from pathiel.agents.atomic_io import append_json_line
from pathiel.models.types import Candle

from services.strategies import journal
from services.strategies import pipeline as pipeline_mod
from services.strategies.backtest.data import CandlePager, fetch_candles_paged_cached
from services.strategies.desks import get_spec
from services.strategies.evals import thesis_replay_scoring as scoring
from services.strategies.evals.thesis_replay_models import (
    AdapterResult, ModelAdapter, ollama_installed, resolve_adapter,
)
from services.thesis import prompt as prompt_mod
from services.thesis import validator
from services.thesis.api import _normalize_instrument
from services.thesis.desk_lookup import ladder_leverages
from services.thesis.hashing import hash_payload
from services.thesis.llm_schema import ThesisDraft, thesis_draft_json_schema

DEFAULT_OUT_ROOT = "/tmp/thesis-replay"
BASELINE_MODEL_LABEL = "original"


# ── replay input resolution (the ref -> cached-payload hash check) ─────────

@dataclass(frozen=True)
class ReplayInputs:
    replayable: bool
    analytics: Optional[DeskAnalytics] = None
    intel: Optional[IntelPacket] = None
    reason: Optional[str] = None


def _load_verified_snapshot(desk_id: str, sha256: str, model_cls):
    """journal.load_snapshot's raw dict, parsed as `model_cls` and
    re-hashed -- "hash-verified on read": a snapshot file is only ever
    trusted when re-hashing the object it parses to reproduces the exact
    sha256 named in its own filename/ref. None for a missing snapshot, a
    file that fails to parse as `model_cls`, or (corruption, a hand-edited
    file, a filename that does not match its own content) a hash that does
    not match -- every one of those falls back to the live-cache check in
    resolve_replay_inputs exactly as if no snapshot existed."""
    raw = journal.load_snapshot(desk_id, sha256)
    if raw is None:
        return None
    try:
        obj = model_cls.model_validate(raw)
    except Exception:  # noqa: BLE001 - an unparseable snapshot is a missing one
        return None
    if hash_payload(obj) != sha256:
        return None
    return obj


def resolve_replay_inputs(desk_id: str, thesis: Thesis) -> ReplayInputs:
    """Resolve `thesis.analytics_ref`/`intel_ref` back to the real payload.

    Checked in order, per ref: (1) the content-addressed snapshot
    services.strategies.pipeline.save_replay_snapshots wrote when this
    thesis was saved (journal.load_snapshot, hash-verified by
    `_load_verified_snapshot`) -- present for every thesis written since
    2026-11-22; (2) services.strategies.pipeline.load_cached_analytics/
    intel, the desk's single mutable "latest" cache -- reused unchanged,
    including its own contract-mismatch and wrong-primaries guards, and
    only ever a hit for the single most recent refresh on a desk that
    has not ticked again since. `replayable` is True only when one of
    those two actually reproduces the ref's own sha256
    (services.thesis.hashing.hash_payload, the same function the pipeline
    used to stamp the ref in the first place). No third fallback, no
    reconstruction from candles or anything else: an unreplayable case
    stays unreplayable rather than being fed invented inputs."""
    if thesis.analytics_ref is None:
        return ReplayInputs(False, reason="thesis has no analytics_ref")

    analytics = _load_verified_snapshot(desk_id, thesis.analytics_ref.sha256, DeskAnalytics)
    if analytics is None:
        analytics = pipeline_mod.load_cached_analytics(desk_id)
        if analytics is None:
            return ReplayInputs(False, reason="no replay snapshot and no cached analytics on disk "
                                              "for this desk (analytics/latest.json missing, "
                                              "unreadable, or built for other primaries)")
        if hash_payload(analytics) != thesis.analytics_ref.sha256:
            return ReplayInputs(False, reason="no replay snapshot, and analytics_ref no longer "
                                              "matches the cached analytics on disk -- the cache is "
                                              "a single mutable 'latest' snapshot, overwritten by "
                                              "every refresh tick since this thesis was written")

    intel: Optional[IntelPacket] = None
    if thesis.intel_ref is not None:
        intel = _load_verified_snapshot(desk_id, thesis.intel_ref.sha256, IntelPacket)
        if intel is None:
            intel = pipeline_mod.load_cached_intel(desk_id)
            if intel is None:
                return ReplayInputs(False, reason="no replay snapshot and no cached intel on disk "
                                                  "for this desk (intel/latest.json missing or "
                                                  "unreadable)")
            if hash_payload(intel) != thesis.intel_ref.sha256:
                return ReplayInputs(False, reason="no replay snapshot, and intel_ref no longer "
                                                  "matches the cached intel on disk -- same "
                                                  "single-'latest'-snapshot limit as analytics")
    return ReplayInputs(True, analytics=analytics, intel=intel)


# ── prompt reconstruction (HARD RULE: prev=None, scorecard_text=None, always) ──

def build_replay_prompt(spec: DeskSpec, analytics: DeskAnalytics,
                        intel: Optional[IntelPacket]) -> Tuple[str, str]:
    """The exact prompt-building call services.thesis.api.write_thesis
    makes, from the stored intel/analytics snapshots alone. HARD RULE
    (docs/strategy/SCORECARD_TEST_2026-10-31.md): never feed the desk's
    trade record or past grades into a replayed prompt -- prev and
    scorecard_text are hardcoded here, not threaded from any caller, so
    there is no argument that could leak either one in."""
    system_prompt = prompt_mod.build_system_prompt(spec)
    user_prompt, _ = prompt_mod.build_user_prompt(spec, analytics, intel, None, incremental=False,
                                                   scorecard_text=None)
    return system_prompt, user_prompt


# ── one model call, parsed + validated, never repaired (budget discipline) ──

@dataclass(frozen=True)
class ModelCallResult:
    ok: bool
    draft: Optional[ThesisDraft] = None
    validator_errors: Sequence[str] = ()
    parse_error: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


def run_model_once(adapter: ModelAdapter, spec: DeskSpec, analytics: DeskAnalytics,
                   intel: Optional[IntelPacket], system_prompt: str, user_prompt: str,
                   *, tag: str) -> ModelCallResult:
    """One completion, one parse, one validate. No repair round (see
    module docstring) -- a validator miss or a bad/absent JSON answer is
    recorded as a failed case, never retried."""
    schema = thesis_draft_json_schema(len(ladder_leverages(analytics)))
    try:
        result: AdapterResult = adapter.complete(system_prompt, user_prompt,
                                                  max_output_tokens=spec.token_budget.max_output_tokens,
                                                  json_schema=schema, tag=tag)
    except Exception as exc:  # noqa: BLE001 - a model/CLI failure degrades this one case, never the run
        return ModelCallResult(False, parse_error=f"adapter call failed: {exc}")

    usage = {"model": result.model, "input_tokens": result.input_tokens,
             "output_tokens": result.output_tokens, "cost_usd": result.cost_usd,
             "duration_s": result.duration_s}
    try:
        draft = ThesisDraft.model_validate_json(result.text)
    except Exception as exc:  # noqa: BLE001 - bad JSON is a failed case, not a crash
        return ModelCallResult(False, parse_error=str(exc), usage=usage)

    draft = _normalize_instrument(draft, analytics)
    errors = validator.validate_draft(draft, spec, analytics, intel)
    return ModelCallResult(not errors, draft=draft, validator_errors=errors, usage=usage)


# ── candle fetch windows (grouped per instrument -- one venue read, not one per thesis) ──

def _needed_window(spans: Sequence[Tuple[datetime, datetime]], now: datetime,
                   *, direction_horizon_h: float = scoring.DEFAULT_DIRECTION_HORIZON_H) -> Tuple[int, int]:
    """[start_ms, end_ms) that covers every (created_at, expires_at) span's
    own target_before_stop window (up to min(expires_at, now)) AND its
    direction_24h window (created_at + direction_horizon_h), with a couple
    hours of slack on each side for has_coverage's own tolerance."""
    starts = [s for s, _ in spans]
    ends = [min(e, now) for _, e in spans]
    start = min(starts) - timedelta(hours=1)
    end = max(max(ends), min(starts) + timedelta(hours=direction_horizon_h)) + timedelta(hours=2)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def fetch_candles_for_spans(coin: str, spans: Sequence[Tuple[datetime, datetime]], *, cache_dir: str,
                            now: datetime, fetch_page: Optional[CandlePager] = None) -> List[Candle]:
    start_ms, end_ms = _needed_window(spans, now)
    return fetch_candles_paged_cached(coin, "1h", start_ms, end_ms, cache_dir, fetch_page=fetch_page)


def score_original_calls(theses: Sequence[Thesis], *, cache_dir: str, now: Optional[datetime] = None,
                         fetch_page: Optional[CandlePager] = None,
                         ) -> Dict[str, Tuple[scoring.DirectionScore, scoring.TargetStopScore]]:
    """Both deterministic scores for every thesis's own stored call, one
    venue read per distinct instrument (not per thesis) -- the oil desk's
    108 theses are almost all xyz:BRENTOIL, so this is ~9 fetches for 142
    theses across every desk, not 142."""
    now = now or datetime.now(timezone.utc)
    out: Dict[str, Tuple[scoring.DirectionScore, scoring.TargetStopScore]] = {}
    groups: Dict[str, List[Thesis]] = {}
    for t in theses:
        reason = scoring._unscoreable_side(t.call.side)
        if reason:
            out[t.id] = (scoring.DirectionScore("void", reason=reason),
                        scoring.TargetStopScore("void", reason=reason))
            continue
        groups.setdefault(t.call.instrument, []).append(t)
    for coin, group in groups.items():
        spans = [(t.created_at, t.expires_at) for t in group]
        candles = fetch_candles_for_spans(coin, spans, cache_dir=cache_dir, now=now, fetch_page=fetch_page)
        for t in group:
            out[t.id] = scoring.score_call(t.call, t.created_at, t.expires_at, candles, now=now)
    return out


def score_one_call(call: Call, created_at: datetime, expires_at: datetime, *, cache_dir: str,
                   now: Optional[datetime] = None, fetch_page: Optional[CandlePager] = None,
                   ) -> Tuple[scoring.DirectionScore, scoring.TargetStopScore]:
    """Same scoring as score_original_calls, for one ad-hoc call (a
    replayed model answer) instead of a batch of stored theses."""
    now = now or datetime.now(timezone.utc)
    reason = scoring._unscoreable_side(call.side)
    if reason:
        return scoring.DirectionScore("void", reason=reason), scoring.TargetStopScore("void", reason=reason)
    candles = fetch_candles_for_spans(call.instrument, [(created_at, expires_at)], cache_dir=cache_dir,
                                      now=now, fetch_page=fetch_page)
    return scoring.score_call(call, created_at, expires_at, candles, now=now)


# ── theses to consider ───────────────────────────────────────────────────

def load_candidate_theses(desk_arg: str) -> List[Thesis]:
    """Every thesis for `desk_arg` ("all" -> every desk with a theses/ dir
    on disk, journal.all_desk_ids -- not the code registry, so a retired
    desk's history is still included), newest first across desks."""
    desk_ids = journal.all_desk_ids() if desk_arg == "all" else [desk_arg]
    theses: List[Thesis] = []
    for d in desk_ids:
        theses.extend(journal.list_theses(d))
    theses.sort(key=lambda t: t.created_at, reverse=True)
    return theses


# ── per-case row + leaderboard aggregation ──────────────────────────────────

def _score_dict(s: Any) -> Dict[str, Any]:
    return asdict(s)


def _case_row(t: Thesis, model: str, d_score: scoring.DirectionScore, ts_score: scoring.TargetStopScore,
             *, unreplayable: bool, unreplayable_reason: Optional[str], call: Optional[Call],
             usage: Optional[Dict[str, Any]] = None, validator_errors: Sequence[str] = (),
             parse_ok: Optional[bool] = None) -> Dict[str, Any]:
    return {
        "desk_id": t.desk_id,
        "thesis_id": t.id,
        "model": model,
        "created_at": t.created_at.isoformat(),
        "expires_at": t.expires_at.isoformat(),
        "unreplayable": unreplayable,
        "unreplayable_reason": unreplayable_reason,
        "call": call.model_dump(mode="json") if call is not None else None,
        "parse_ok": parse_ok,
        "validator_errors": list(validator_errors),
        "direction_24h": _score_dict(d_score),
        "target_before_stop": _score_dict(ts_score),
        "usage": usage,
    }


def build_leaderboard(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    board: Dict[str, Dict[str, Any]] = {}
    for model, model_rows in by_model.items():
        board[model] = {
            "n_cases": len(model_rows),
            "unreplayable": sum(1 for r in model_rows if r["unreplayable"]),
            "direction_24h": scoring.summarize_outcomes([r["direction_24h"]["outcome"] for r in model_rows]),
            "target_before_stop": scoring.summarize_outcomes(
                [r["target_before_stop"]["outcome"] for r in model_rows]),
        }
    return board


def _fmt_rate(s: scoring.RateSummary) -> str:
    if s.n == 0 or s.rate is None:
        return f"{s.n:>4} n/a             "
    ci = f"[{s.ci_lo * 100:4.1f},{s.ci_hi * 100:4.1f}]"
    return f"{s.n:>4} {s.rate * 100:5.1f}% {ci:>13}"


def format_leaderboard(board: Dict[str, Dict[str, Any]]) -> str:
    order = [BASELINE_MODEL_LABEL] + sorted(m for m in board if m != BASELINE_MODEL_LABEL)
    header = (f"{'model':<10} {'n':>4} {'unreplay':>8} | {'dir_24h':>23} | {'target<stop':>23}")
    lines = [header, "-" * len(header)]
    for model in order:
        if model not in board:
            continue
        b = board[model]
        lines.append(f"{model:<10} {b['n_cases']:>4} {b['unreplayable']:>8} | "
                     f"{_fmt_rate(b['direction_24h'])} | {_fmt_rate(b['target_before_stop'])}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None, *, fetch_page: Optional[CandlePager] = None,
        adapter_factory: Optional[Callable[[str], ModelAdapter]] = None,
        now: Optional[datetime] = None) -> int:
    """``fetch_page``/``adapter_factory``/``now`` are test-only seams
    (default None -> real network, real services.llm/CommandAdapter, real
    wall clock); every real invocation leaves them unset."""
    ap = argparse.ArgumentParser(description="Replay pathiel's desk theses through model A/B, "
                                             "deterministically graded, no P&L.")
    ap.add_argument("--desk", default="all", help='desk id, or "all" (default)')
    ap.add_argument("--models", default="", help="comma-separated: opus,sonnet,haiku,fable, or "
                                                 "command:<local CLI reading a prompt on stdin>. "
                                                 "Required unless --dry-run.")
    ap.add_argument("--limit", type=int, default=None, help="consider at most this many theses "
                                                             "(newest first)")
    ap.add_argument("--dry-run", action="store_true", help="grade the ORIGINAL stored calls only; "
                                                            "still attempts prompt reconstruction "
                                                            "to count unreplayable cases, but makes "
                                                            "no LLM call")
    ap.add_argument("--run-name", default=None, help="default: a UTC timestamp")
    ap.add_argument("--out-dir", default=None, help="default: /tmp/thesis-replay/<run-name>")
    ap.add_argument("--cache-dir", default=None, help="default: <out-root>/candle-cache")
    ap.add_argument("--export-jsonl", default=None, help="write graded, replayed cases as "
                                                          "fine-tuning records to this path")
    ap.add_argument("--prune-snapshots-older-than", type=float, default=None, metavar="DAYS",
                    help="maintenance only, off by default, never scheduled by this tool: delete "
                        "replay snapshot files (journal.snapshots_dir) older than DAYS, across "
                        "every desk, then exit without running a replay")
    args = ap.parse_args(argv)

    if args.prune_snapshots_older_than is not None:
        deleted = journal.prune_snapshots(args.prune_snapshots_older_than, now=now)
        total = sum(deleted.values())
        print(f"pruned {total} snapshot file(s) older than {args.prune_snapshots_older_than} day(s): "
             f"{deleted or '{}'}")
        return 0

    model_specs = [m.strip() for m in args.models.split(",") if m.strip()]
    if not args.dry_run and not model_specs:
        print("FATAL: --models is required unless --dry-run")
        return 2

    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir or os.path.join(DEFAULT_OUT_ROOT, run_name)
    cache_dir = args.cache_dir or os.path.join(DEFAULT_OUT_ROOT, "candle-cache")
    os.makedirs(out_dir, exist_ok=True)
    results_path = os.path.join(out_dir, "results.jsonl")
    if os.path.exists(results_path):
        os.remove(results_path)
    if args.export_jsonl and os.path.exists(args.export_jsonl):
        os.remove(args.export_jsonl)

    theses = load_candidate_theses(args.desk)
    if args.limit is not None:
        theses = theses[:args.limit]
    if not theses:
        print(f"no theses found for desk={args.desk!r} under "
             f"PATHIEL_STATE_DIR={os.environ.get('PATHIEL_STATE_DIR') or '(default)'}")
        return 1

    now = now or datetime.now(timezone.utc)
    adapter_factory = adapter_factory or resolve_adapter
    baseline_scores = score_original_calls(theses, cache_dir=cache_dir, now=now, fetch_page=fetch_page)

    all_rows: List[Dict[str, Any]] = []
    n_unreplayable = 0

    for t in theses:
        spec = get_spec(t.desk_id)
        inputs = resolve_replay_inputs(t.desk_id, t)
        if not inputs.replayable:
            n_unreplayable += 1
        d0, ts0 = baseline_scores[t.id]
        baseline_row = _case_row(t, BASELINE_MODEL_LABEL, d0, ts0, unreplayable=not inputs.replayable,
                                 unreplayable_reason=inputs.reason, call=t.call)
        all_rows.append(baseline_row)
        append_json_line(results_path, baseline_row)

        if args.dry_run or not model_specs or spec is None:
            continue

        if not inputs.replayable:
            for spec_name in model_specs:
                row = _case_row(t, spec_name, scoring.DirectionScore("void", reason="unreplayable"),
                                scoring.TargetStopScore("void", reason="unreplayable"),
                                unreplayable=True, unreplayable_reason=inputs.reason, call=None)
                all_rows.append(row)
                append_json_line(results_path, row)
            continue

        system_prompt, user_prompt = build_replay_prompt(spec, inputs.analytics, inputs.intel)
        for spec_name in model_specs:
            adapter = adapter_factory(spec_name)
            tag = f"thesis-replay:{t.desk_id}:{t.id}:{spec_name}"
            call_result = run_model_once(adapter, spec, inputs.analytics, inputs.intel, system_prompt,
                                         user_prompt, tag=tag)
            if call_result.ok and call_result.draft is not None:
                d1, ts1 = score_one_call(call_result.draft.call, t.created_at, t.expires_at,
                                         cache_dir=cache_dir, now=now, fetch_page=fetch_page)
                row = _case_row(t, spec_name, d1, ts1, unreplayable=False, unreplayable_reason=None,
                                call=call_result.draft.call, usage=call_result.usage,
                                validator_errors=call_result.validator_errors, parse_ok=True)
                if args.export_jsonl:
                    append_json_line(args.export_jsonl, {
                        "desk_id": t.desk_id, "thesis_id": t.id, "model": spec_name,
                        "input": {"system": system_prompt, "user": user_prompt},
                        "answer": call_result.draft.model_dump(mode="json"),
                        "scores": {"direction_24h": _score_dict(d1), "target_before_stop": _score_dict(ts1)},
                    })
            else:
                reason = call_result.parse_error or "validator rejected the draft"
                row = _case_row(t, spec_name, scoring.DirectionScore("void", reason="model_call_failed"),
                                scoring.TargetStopScore("void", reason="model_call_failed"),
                                unreplayable=False, unreplayable_reason=None, call=None,
                                usage=call_result.usage, validator_errors=call_result.validator_errors,
                                parse_ok=False)
                row["model_call_error"] = reason
            all_rows.append(row)
            append_json_line(results_path, row)

    board = build_leaderboard(all_rows)
    n_considered = len(theses)
    print(f"thesis replay: desk={args.desk!r} n_considered={n_considered} "
         f"unreplayable={n_unreplayable} models={model_specs or '(dry-run only)'}")
    if model_specs and "command:" not in args.models:
        print(f"ollama on PATH: {ollama_installed()} (informational only; not installed or used "
             f"unless a --models entry names it)")
    print(format_leaderboard(board))
    print(f"results: {results_path}")
    if args.export_jsonl:
        print(f"export: {args.export_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
