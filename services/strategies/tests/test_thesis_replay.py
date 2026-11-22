"""Gate tests for services.strategies.evals.thesis_replay: ref resolution
(unreplayable-by-hash-mismatch), the HARD RULE (never prev/scorecard into a
replayed prompt), batched candle-window math, the leaderboard, and the CLI
end to end against a tmp PATHIEL_STATE_DIR with stubbed candles and a
stubbed model adapter -- no network, no real LLM call (conftest.py's
socket block would fail this test otherwise; the autouse `state_dir`
fixture from services/strategies/tests/conftest.py isolates every test's
PATHIEL_STATE_DIR already)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List


from contracts.strategies.models import PayloadRef
from pathiel.models.types import Candle
from services.strategies import journal
from services.strategies import pipeline as pipeline_mod
from services.strategies.evals import thesis_replay as R
from services.strategies.evals import thesis_replay_scoring as scoring
from services.strategies.evals.thesis_replay_models import AdapterResult
from services.strategies.tests.conftest import make_candles_ohlc, make_thesis, minimal_analytics
from services.thesis.hashing import hash_payload

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _stub_fetch_page(all_candles: List[Candle]):
    def fetch(coin: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]:
        return [c for c in all_candles if start_ms <= c.t < end_ms]
    return fetch


def _flat_candles(closes, *, start=T0, step=HOUR):
    rows = []
    prev = closes[0]
    for c in closes:
        rows.append({"o": prev, "h": max(prev, c), "l": min(prev, c), "c": c})
        prev = c
    return make_candles_ohlc(rows, start=start, step=step)


# ── resolve_replay_inputs ────────────────────────────────────────────────

def test_resolve_replay_inputs_replayable_on_hash_match():
    analytics = minimal_analytics("oil", as_of=T0)
    pipeline_mod.save_analytics("oil", analytics)
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=hash_payload(analytics))})

    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is True
    assert inputs.analytics == analytics
    assert inputs.intel is None
    assert inputs.reason is None


def test_resolve_replay_inputs_unreplayable_when_cache_moved_on():
    analytics_then = minimal_analytics("oil", mid=100.0, as_of=T0)
    analytics_now = minimal_analytics("oil", mid=105.0, as_of=T0 + timedelta(days=1))
    pipeline_mod.save_analytics("oil", analytics_now)   # the cache has moved on since
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=hash_payload(analytics_then))})

    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "no longer matches" in inputs.reason


def test_resolve_replay_inputs_unreplayable_when_no_cache_at_all():
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256="deadbeef")})
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "no cached analytics" in inputs.reason


def test_resolve_replay_inputs_unreplayable_without_a_ref_at_all():
    thesis = make_thesis(desk_id="oil", created_at=T0)   # analytics_ref defaults to None
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "no analytics_ref" in inputs.reason


def test_resolve_replay_inputs_intel_ref_also_checked():
    from contracts.strategies.models import IntelPacket

    analytics = minimal_analytics("oil", as_of=T0)
    intel = IntelPacket(desk_id="oil", as_of=T0)
    pipeline_mod.save_analytics("oil", analytics)
    pipeline_mod.save_intel("oil", intel)
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=hash_payload(analytics)),
        "intel_ref": PayloadRef(as_of=T0, sha256="wrong-hash"),
    })
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "intel_ref" in inputs.reason


# ── resolve_replay_inputs: content-addressed snapshots (2026-11-22) ──────

def test_resolve_replay_inputs_resolves_from_snapshot_with_no_live_cache_at_all():
    """A snapshotted thesis is replayable even when the desk's "latest"
    cache has moved on to something completely different (or was never
    written this test run) -- the whole point of the snapshot."""
    from services.strategies import journal

    analytics = minimal_analytics("oil", mid=77.0, as_of=T0)
    the_hash = hash_payload(analytics)
    journal.save_snapshot("oil", the_hash, analytics.model_dump(mode="json"))
    # the live cache is something else entirely (simulating ticks since)
    pipeline_mod.save_analytics("oil", minimal_analytics("oil", mid=999.0, as_of=T0 + timedelta(days=5)))

    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=the_hash)})

    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is True
    assert inputs.analytics == analytics
    assert inputs.reason is None


def test_resolve_replay_inputs_old_thesis_without_a_snapshot_is_still_unreplayable():
    """Old behavior is preserved for a thesis that predates snapshotting:
    no snapshot file, and the live cache has moved on -> unreplayable,
    exactly as before this feature existed."""
    from services.strategies import journal

    analytics_then = minimal_analytics("oil", mid=100.0, as_of=T0)
    pipeline_mod.save_analytics("oil", minimal_analytics("oil", mid=105.0, as_of=T0 + timedelta(days=1)))
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=hash_payload(analytics_then))})

    assert journal.load_snapshot("oil", hash_payload(analytics_then)) is None   # confirm: no snapshot
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "no longer matches" in inputs.reason


def test_resolve_replay_inputs_rejects_a_snapshot_whose_hash_does_not_match_its_content():
    """Hash-verified on read: a snapshot file saved under hash H, whose
    content does NOT actually hash to H (corruption, a hand-edit, a bug
    upstream), is never trusted -- it falls back to the live-cache check
    exactly as if the snapshot did not exist."""
    from services.strategies import journal

    analytics = minimal_analytics("oil", mid=50.0, as_of=T0)
    real_hash = hash_payload(analytics)
    # saved under real_hash, but with DIFFERENT content than what actually hashes to it
    tampered = minimal_analytics("oil", mid=999.0, as_of=T0)
    journal.save_snapshot("oil", real_hash, tampered.model_dump(mode="json"))
    assert hash_payload(tampered) != real_hash   # sanity: the fixture really is mismatched

    # no live cache either, so the fallback also fails -> unreplayable, not silently
    # served the tampered snapshot
    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=real_hash)})
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is False
    assert "no cached analytics" in inputs.reason   # fell through to the live-cache branch


def test_resolve_replay_inputs_rejects_hash_mismatch_but_accepts_the_live_cache_underneath():
    """Same tampered snapshot as above, but THIS time the live cache
    happens to be the real, correct payload -- resolve_replay_inputs must
    still fall through past the untrusted snapshot and succeed via the
    live-cache path, not get stuck on the rejected snapshot."""
    from services.strategies import journal

    analytics = minimal_analytics("oil", mid=50.0, as_of=T0)
    real_hash = hash_payload(analytics)
    tampered = minimal_analytics("oil", mid=999.0, as_of=T0)
    journal.save_snapshot("oil", real_hash, tampered.model_dump(mode="json"))
    pipeline_mod.save_analytics("oil", analytics)   # the live cache still has the real payload

    thesis = make_thesis(desk_id="oil", created_at=T0).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=real_hash)})
    inputs = R.resolve_replay_inputs("oil", thesis)
    assert inputs.replayable is True
    assert inputs.analytics == analytics


# ── HARD RULE: never prev / scorecard into a replayed prompt ────────────

def test_build_replay_prompt_never_passes_prev_or_scorecard(monkeypatch):
    import services.thesis.prompt as prompt_mod

    captured = {}
    real_build_user_prompt = prompt_mod.build_user_prompt

    def spy(spec, analytics, intel, prev, *, incremental, scorecard_text=None):
        captured["prev"] = prev
        captured["scorecard_text"] = scorecard_text
        captured["incremental"] = incremental
        return real_build_user_prompt(spec, analytics, intel, prev, incremental=incremental,
                                      scorecard_text=scorecard_text)

    monkeypatch.setattr(R.prompt_mod, "build_user_prompt", spy)
    from services.strategies.desks import get_spec
    spec = get_spec("oil")
    analytics = minimal_analytics("oil", coin="xyz:BRENTOIL", label="Brent", mid=90.0, as_of=T0)

    system_prompt, user_prompt = R.build_replay_prompt(spec, analytics, None)

    assert captured["prev"] is None
    assert captured["scorecard_text"] is None
    assert captured["incremental"] is False
    assert isinstance(system_prompt, str) and isinstance(user_prompt, str)
    assert system_prompt and user_prompt


# ── candle window math ───────────────────────────────────────────────────

def test_needed_window_covers_direction_horizon_and_target_stop_horizon():
    now = T0 + timedelta(days=10)
    spans = [(T0, T0 + timedelta(days=7))]
    start_ms, end_ms = R._needed_window(spans, now)
    start = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    assert start <= T0
    assert end >= T0 + timedelta(days=7)   # expires_at (window has long since elapsed)


def test_needed_window_bounds_end_by_now_not_expiry():
    now = T0 + timedelta(hours=5)   # thesis's 7-day horizon has not elapsed yet
    spans = [(T0, T0 + timedelta(days=7))]
    _, end_ms = R._needed_window(spans, now)
    end = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    # still reaches the fixed 24h direction window even though "now" is only +5h
    assert end >= T0 + timedelta(hours=24)
    assert end < T0 + timedelta(days=7)


# ── score_original_calls: grouped fetch, one read per instrument ───────

def test_score_original_calls_groups_by_instrument_one_fetch_per_coin(tmp_path):
    calls_made = []

    oil_candles = _flat_candles([100.0] * 24 + [110.0] * 10, start=T0)
    hype_candles = _flat_candles([10.0] * 24 + [9.0] * 10, start=T0)

    def fetch_page(coin, interval, start_ms, end_ms):
        candles = oil_candles if coin == "xyz:BRENTOIL" else hype_candles
        calls_made.append(coin)
        return [c for c in candles if start_ms <= c.t < end_ms]

    theses = [
        make_thesis(desk_id="oil", thesis_id="oil-1", created_at=T0, horizon_days=7,
                   instrument="xyz:BRENTOIL", side="long", entry_px=None, stop_px=90.0, targets=[120.0]),
        make_thesis(desk_id="oil", thesis_id="oil-2", created_at=T0 + timedelta(hours=1), horizon_days=7,
                   instrument="xyz:BRENTOIL", side="long", entry_px=None, stop_px=90.0, targets=[120.0]),
        make_thesis(desk_id="hype", thesis_id="hype-1", created_at=T0, horizon_days=7,
                   instrument="HYPE", side="short", entry_px=None, stop_px=11.0, targets=[8.0]),
    ]
    scores = R.score_original_calls(theses, cache_dir=str(tmp_path / "cache"),
                                    now=T0 + timedelta(days=8), fetch_page=fetch_page)

    assert set(scores) == {"oil-1", "oil-2", "hype-1"}
    assert scores["oil-1"][0].outcome == "hit"     # long, price rose
    assert scores["hype-1"][0].outcome == "hit"    # short, price fell
    assert set(calls_made) == {"xyz:BRENTOIL", "HYPE"}   # grouped: not one fetch per thesis


def test_score_original_calls_flat_side_is_void_without_fetching(tmp_path):
    fetch_calls = []

    def fetch_page(coin, interval, start_ms, end_ms):
        fetch_calls.append(coin)
        return []

    theses = [make_thesis(desk_id="oil", thesis_id="flat-1", created_at=T0, side="flat",
                          entry_px=None, stop_px=None, targets=[])]
    scores = R.score_original_calls(theses, cache_dir=str(tmp_path / "cache"),
                                    now=T0 + timedelta(days=8), fetch_page=fetch_page)
    assert scores["flat-1"][0].outcome == "void"
    assert fetch_calls == []   # no candle fetch for an unscoreable side


# ── leaderboard ──────────────────────────────────────────────────────────

def test_leaderboard_aggregates_per_model_and_formats():
    rows = [
        R._case_row(make_thesis(desk_id="oil", thesis_id="a", created_at=T0),
                   "original", scoring.DirectionScore("hit"), scoring.TargetStopScore("hit"),
                   unreplayable=False, unreplayable_reason=None, call=None),
        R._case_row(make_thesis(desk_id="oil", thesis_id="b", created_at=T0),
                   "original", scoring.DirectionScore("miss"), scoring.TargetStopScore("stop"),
                   unreplayable=True, unreplayable_reason="no cache", call=None),
        R._case_row(make_thesis(desk_id="oil", thesis_id="a", created_at=T0),
                   "sonnet", scoring.DirectionScore("void", reason="unreplayable"),
                   scoring.TargetStopScore("void", reason="unreplayable"),
                   unreplayable=True, unreplayable_reason="no cache", call=None),
    ]
    board = R.build_leaderboard(rows)
    assert board["original"]["n_cases"] == 2
    assert board["original"]["unreplayable"] == 1
    assert board["original"]["direction_24h"].k == 1 and board["original"]["direction_24h"].n == 2
    assert board["sonnet"]["unreplayable"] == 1
    text = R.format_leaderboard(board)
    assert "original" in text and "sonnet" in text


# ── CLI: --dry-run end to end ────────────────────────────────────────────

def test_main_dry_run_scores_original_calls_and_writes_results(tmp_path):
    candles = _flat_candles([100.0] * 24 + [110.0] * 40, start=T0)
    thesis = make_thesis(desk_id="oil", thesis_id="oil-dry-1", created_at=T0, horizon_days=7,
                         instrument="xyz:BRENTOIL", side="long", entry_px=None, stop_px=90.0,
                         targets=[108.0])
    journal.save_thesis(thesis)

    out_dir = str(tmp_path / "run")
    rc = R.main(["--desk", "oil", "--dry-run", "--out-dir", out_dir, "--cache-dir", str(tmp_path / "cache")],
               fetch_page=_stub_fetch_page(candles), now=T0 + timedelta(days=8))

    assert rc == 0
    results_path = tmp_path / "run" / "results.jsonl"
    assert results_path.exists()
    lines = results_path.read_text().strip().splitlines()
    assert len(lines) == 1
    import json
    row = json.loads(lines[0])
    assert row["model"] == "original"
    assert row["unreplayable"] is True   # no analytics cache written in this test -> unreplayable
    assert row["target_before_stop"]["outcome"] == "hit"


def test_main_dry_run_no_theses_returns_1(tmp_path):
    rc = R.main(["--desk", "oil", "--dry-run", "--out-dir", str(tmp_path / "run")],
               fetch_page=_stub_fetch_page([]), now=T0)
    assert rc == 1


def test_main_requires_models_unless_dry_run(tmp_path):
    rc = R.main(["--desk", "oil", "--out-dir", str(tmp_path / "run")])
    assert rc == 2


# ── CLI: real replay path, stubbed adapter, unreplayable never calls it ──

class _FakeAdapter:
    def __init__(self, text: str, model: str = "fake-model"):
        self.text = text
        self.model = model
        self.calls = 0

    def complete(self, system_prompt, user_prompt, *, max_output_tokens, json_schema, tag):
        self.calls += 1
        return AdapterResult(text=self.text, model=self.model, input_tokens=1, output_tokens=1,
                             cost_usd=0.0, duration_s=0.01)


def test_main_replay_never_calls_adapter_for_unreplayable_case(tmp_path):
    thesis = make_thesis(desk_id="oil", thesis_id="oil-unreplay", created_at=T0, horizon_days=7,
                         instrument="xyz:BRENTOIL", side="long", entry_px=None, stop_px=90.0,
                         targets=[108.0])
    # no analytics_ref at all on this thesis -> always unreplayable
    journal.save_thesis(thesis)

    fake = _FakeAdapter('{"not": "valid"}')
    rc = R.main(["--desk", "oil", "--models", "sonnet", "--out-dir", str(tmp_path / "run"),
                "--cache-dir", str(tmp_path / "cache")],
               fetch_page=_stub_fetch_page(_flat_candles([100.0] * 60, start=T0)),
               adapter_factory=lambda spec: fake, now=T0 + timedelta(days=8))

    assert rc == 0
    assert fake.calls == 0   # never invented inputs, never spent a call on an unreplayable case

    import json
    results_path = tmp_path / "run" / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text().strip().splitlines()]
    sonnet_rows = [r for r in rows if r["model"] == "sonnet"]
    assert len(sonnet_rows) == 1
    assert sonnet_rows[0]["unreplayable"] is True


def test_main_replay_records_invalid_draft_without_crashing(tmp_path):
    analytics = minimal_analytics("oil", coin="xyz:BRENTOIL", label="Brent", mid=90.0, as_of=T0)
    pipeline_mod.save_analytics("oil", analytics)
    thesis = make_thesis(desk_id="oil", thesis_id="oil-replay-1", created_at=T0, horizon_days=7,
                         instrument="xyz:BRENTOIL", side="long", entry_px=None, stop_px=90.0,
                         targets=[108.0]).model_copy(update={
        "analytics_ref": PayloadRef(as_of=T0, sha256=hash_payload(analytics))})
    journal.save_thesis(thesis)

    fake = _FakeAdapter("not even json")
    rc = R.main(["--desk", "oil", "--models", "sonnet", "--out-dir", str(tmp_path / "run"),
                "--cache-dir", str(tmp_path / "cache")],
               fetch_page=_stub_fetch_page(_flat_candles([100.0] * 60, start=T0)),
               adapter_factory=lambda spec: fake, now=T0 + timedelta(days=8))

    assert rc == 0
    assert fake.calls == 1   # replayable -> the model WAS asked, exactly once

    import json
    results_path = tmp_path / "run" / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text().strip().splitlines()]
    sonnet_rows = [r for r in rows if r["model"] == "sonnet"]
    assert len(sonnet_rows) == 1
    assert sonnet_rows[0]["unreplayable"] is False
    assert sonnet_rows[0]["parse_ok"] is False
    assert sonnet_rows[0]["direction_24h"]["outcome"] == "void"


# ── CLI: --prune-snapshots-older-than ────────────────────────────────────

def test_main_prune_snapshots_deletes_and_exits_without_requiring_models():
    from services.strategies import journal

    journal.save_snapshot("oil", "old", {"v": 1})
    old_path = journal.snapshot_path("oil", "old")
    old_time = (T0 - timedelta(days=200)).timestamp()
    import os
    os.utime(old_path, (old_time, old_time))
    journal.save_snapshot("oil", "new", {"v": 2})

    rc = R.main(["--prune-snapshots-older-than", "90"], now=T0)
    assert rc == 0
    assert journal.load_snapshot("oil", "old") is None
    assert journal.load_snapshot("oil", "new") == {"v": 2}
