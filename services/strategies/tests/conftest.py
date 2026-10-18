"""Test isolation + shared synthetic-candle helpers for services/strategies.

Every test gets its OWN throwaway PATHIEL_STATE_DIR (via the `state_dir`
fixture, autouse) — never the live one, never shared between tests. This
mirrors the discipline in services/pathiel_data_api/tests/conftest.py and
the root tests/conftest.py, scoped to this service: the global tests/
conftest.py does not apply here (different pytest rootdir branch —
testpaths = ["tests", "services", "contracts"] are separate trees).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

import pytest

from contracts.strategies.models import (
    Author, Call, CallSide, DeskAnalytics, InstrumentSnapshot, Scenario, Thesis,
)
from pathiel.models.types import Candle

DAY_MS = 86_400_000


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Every test's state lives under its own tmp_path. Resolved at call
    time by journal.py/pipeline.py (os.environ.get(...)), so monkeypatch's
    setenv is all that's needed — no module reload dance."""
    d = tmp_path / "pathiel_state"
    d.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(d))
    return str(d)


def make_candles(closes: Sequence[float], *, start: Optional[datetime] = None,
                 wiggle: float = 0.0, volume: float = 1_000_000.0,
                 step: timedelta = timedelta(days=1)) -> List[Candle]:
    """One candle per close, `step` apart (default daily; pass
    step=timedelta(hours=1) for grader tests). `wiggle` fraction above/below
    close for high/low (0.0 -> h=l=c, a flat bar with no intrabar excursion
    — fine for return/vol/ATR tests; pass wiggle>0 for ladder/forward-stats
    tests that need a believable high/low range)."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    prev_c = closes[0]
    for i, c in enumerate(closes):
        o = prev_c
        h = max(o, c) * (1.0 + wiggle)
        lo = min(o, c) * (1.0 - wiggle)
        t = int((start + step * i).timestamp() * 1000)
        out.append(Candle(t=t, o=o, h=h, l=lo, c=c, v=volume))
        prev_c = c
    return out


def make_candles_ohlc(rows: Sequence[dict], *, start: Optional[datetime] = None,
                      step: timedelta = timedelta(days=1)) -> List[Candle]:
    """Full control per bar: rows of {"o","h","l","c"} (and optional "v").
    Used where a test needs an exact intrabar low/high, not just a close."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for i, r in enumerate(rows):
        t = int((start + step * i).timestamp() * 1000)
        out.append(Candle(t=t, o=r["o"], h=r["h"], l=r["l"], c=r["c"], v=r.get("v", 1_000_000.0)))
    return out


def flat_meta(max_leverage: float = 20.0, funding: float = 0.0, oi: float = 1_000_000.0,
              volume_24h: float = 50_000_000.0) -> dict:
    return {"maxLeverage": max_leverage, "funding": funding, "openInterest": oi, "dayNtlVlm": volume_24h}


def make_thesis(*, desk_id: str = "oil", thesis_id: Optional[str] = None,
                author: Author = "machine", created_at: Optional[datetime] = None,
                horizon_days: int = 7, side: CallSide = "long", instrument: str = "xyz:BRENTOIL",
                entry_px: Optional[float] = 100.0, stop_px: Optional[float] = 90.0,
                targets: Sequence[float] = (110.0,), leverage: Optional[float] = 5.0,
                confidence: float = 0.6, scenarios: Optional[List[Scenario]] = None,
                grade=None) -> Thesis:
    """A minimal, always-valid Thesis for journal/grader/pipeline/facade/api
    tests, with every field a reasonable test can override."""
    created_at = created_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    thesis_id = thesis_id or f"{desk_id}-test-{uuid.uuid4().hex[:8]}"
    if scenarios is None:
        scenarios = [
            Scenario(name="up", probability=0.6, move_lo_pct=0.0, move_hi_pct=1.0, description="up"),
            Scenario(name="down", probability=0.4, move_lo_pct=-1.0, move_hi_pct=0.0, description="down"),
        ]
    return Thesis(
        id=thesis_id, desk_id=desk_id, author=author, created_at=created_at,
        expires_at=created_at + timedelta(days=horizon_days), horizon_days=horizon_days,
        call=Call(side=side, instrument=instrument, one_liner="test thesis", entry_px=entry_px,
                 stop_px=stop_px, targets=list(targets), leverage=leverage, confidence=confidence,
                 invalidation="test invalidation"),
        scenarios=scenarios, grade=grade,
    )


def minimal_analytics(desk_id: str, *, coin: str = "XYZ", label: str = "XYZ",
                      mid: float = 100.0, as_of: Optional[datetime] = None) -> DeskAnalytics:
    """The smallest valid DeskAnalytics — for pipeline/facade/api tests that
    exercise ORCHESTRATION around analytics, not analytics math itself
    (that's test_analytics.py's job)."""
    return DeskAnalytics(
        desk_id=desk_id, as_of=as_of or datetime(2026, 1, 1, tzinfo=timezone.utc),
        instruments=[InstrumentSnapshot(coin=coin, label=label, mid=mid, ret_1d=0.0, ret_5d=0.0,
                                        ret_20d=0.0, high_20d=mid, low_20d=mid, atr14_pct=0.01,
                                        vol_daily_10d=0.01, vol_daily_20d=0.01)],
    )


@pytest.fixture
def stub_live_analytics(monkeypatch):
    """Replace the live analytics build with a fast, deterministic fake.
    Call the returned setter with either a DeskAnalytics (used for every
    desk) or a callable(spec) -> DeskAnalytics."""
    import services.strategies.pipeline as pipeline_mod

    def _set(result):
        def fake_build(spec, **kwargs):
            return result(spec) if callable(result) else result
        monkeypatch.setattr(pipeline_mod.analytics_mod, "build_analytics", fake_build)

    return _set


@pytest.fixture(autouse=True)
def _gate_safe_providers(monkeypatch):
    """Default every strategies test to offline intel/thesis providers. With
    services/intel and services/thesis merged, the real defaults would make
    network reads and billable LLM calls from the gate suite."""
    from services.strategies import pipeline as _pipeline
    monkeypatch.setattr(_pipeline, "INTEL_PROVIDER_PATH",
                        "services.strategies.tests._gate_providers:build_packet")
    monkeypatch.setattr(_pipeline, "THESIS_PROVIDER_PATH",
                        "services.strategies.tests._gate_providers:write_thesis")
