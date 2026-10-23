"""Unit tests for services/intel/evals/eval_coverage.py: the catalyst-kind
hard-number coverage score, and the offline eval end to end (fully
mocked, no network, no real keys)."""
from __future__ import annotations

from datetime import datetime, timezone

from contracts.strategies.models import DataPoint, DeskSpec, Instrument, IntelPacket, PositioningItem
from services.intel import http_client
from services.intel.evals import eval_coverage

T0 = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def _spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")],
        catalyst_kinds=["cftc_cot"])


def _empty_packet() -> IntelPacket:
    return IntelPacket(desk_id="oil", as_of=T0)


# ── per-kind predicates ──────────────────────────────────────────────

def test_eia_weekly_covered_only_by_a_crude_stocks_datapoint():
    covered_packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id="d1", source="eia", label="EIA crude stocks weekly change",
                  value=-8.6, unit="mm_bbl", as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["eia_weekly"](covered_packet) is True
    assert eval_coverage.CATALYST_HARD_DATA_MAP["eia_weekly"](_empty_packet()) is False
    wrong_source = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id="d2", source="fred", label="crude stocks lookalike", value=1.0,
                  unit="x", as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["eia_weekly"](wrong_source) is False


def test_cftc_cot_covered_by_any_positioning_item():
    packet = IntelPacket(desk_id="oil", as_of=T0, positioning=[
        PositioningItem(id="p1", source="cftc_cot", label="WTI non-commercial net long",
                        value=1.0, as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["cftc_cot"](packet) is True
    assert eval_coverage.CATALYST_HARD_DATA_MAP["cftc_cot"](_empty_packet()) is False


def test_kinds_with_no_built_source_are_honestly_uncovered():
    """api_weekly, opec_meeting, baker_hughes, cpi, hurricane_atlantic have
    no hard-number source wired — must stay False even against a packet
    stuffed with unrelated datapoints, never silently "covered" by
    coincidence."""
    stuffed = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id=f"d{i}", source="venue_hl", label=f"thing {i}", value=1.0,
                  unit="x", as_of=T0) for i in range(5)
    ], positioning=[PositioningItem(id="p1", source="cftc_cot", label="x", value=1.0, as_of=T0)])
    for kind in ("api_weekly", "opec_meeting", "baker_hughes", "cpi", "hurricane_atlantic"):
        assert eval_coverage.CATALYST_HARD_DATA_MAP[kind](stuffed) is False, kind


def test_fomc_covered_only_by_fred_not_by_venue():
    fred_packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id="d1", source="fred", label="10y Treasury yield", value=4.0,
                  unit="pct", as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["fomc"](fred_packet) is True
    venue_only = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id="d2", source="venue_hl", label="Brent funding rate (hourly)", value=1.0,
                  unit="rate_per_hr", as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["fomc"](venue_only) is False


def test_earnings_covered_only_by_sec_edgar_next_earnings_label():
    packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=[
        DataPoint(id="d1", source="sec_edgar", label="NVDA next earnings (estimated)",
                  value=30.0, unit="days", as_of=T0)])
    assert eval_coverage.CATALYST_HARD_DATA_MAP["earnings"](packet) is True


# ── coverage_for_packet / report ─────────────────────────────────────

def test_coverage_for_packet_matches_active_kinds(monkeypatch):
    spec = _spec()
    packet = IntelPacket(desk_id="oil", as_of=T0, positioning=[
        PositioningItem(id="p1", source="cftc_cot", label="x", value=1.0, as_of=T0)])
    coverage = eval_coverage.coverage_for_packet(spec, packet)
    from services.intel.sources import catalysts
    assert set(coverage) == catalysts.active_kinds(spec)
    assert coverage["cftc_cot"] is True


def test_report_prints_and_returns_pass_fail(capsys):
    assert eval_coverage.report({"a": True, "b": False}, threshold=0.4) is True   # 0.5 >= 0.4
    out = capsys.readouterr().out
    assert "1/2" in out and "PASS" in out

    assert eval_coverage.report({"a": True, "b": False}, threshold=0.6) is False  # 0.5 < 0.6
    out2 = capsys.readouterr().out
    assert "FAIL" in out2


def test_report_empty_coverage_does_not_divide_by_zero():
    assert eval_coverage.report({}, threshold=0.2) is False


# ── end to end, fully offline ─────────────────────────────────────────

def test_eval_coverage_main_runs_offline_and_matches_documented_baseline(monkeypatch):
    """The documented DEFAULT_THRESHOLD (see this module's docstring and
    services/intel/README.md) is today's real baseline, not aspirational
    — pin the actual number so a silent regression (or an undocumented
    improvement) in build_packet's HARD DATA coverage is caught here
    first, not discovered by Julien reading a desk brief."""
    # eval_coverage._mock_offline_network() mutates http_client's module-level
    # functions directly (it is a one-shot CLI script, not a library) —
    # capture the REAL functions now so pytest restores them at teardown
    # regardless of that direct reassignment. See _mock_offline_network's
    # docstring.
    monkeypatch.setattr(http_client, "fetch_json", http_client.fetch_json)
    monkeypatch.setattr(http_client, "fetch_text", http_client.fetch_text)
    monkeypatch.setattr(http_client, "fetch_json_post", http_client.fetch_json_post)
    # _mock_offline_network() also does a bare os.environ.setdefault for
    # EIA_API_KEY (see its docstring) — set it through monkeypatch first so
    # that call becomes a no-op and pytest restores the real environ
    # afterward instead of leaking a fake key into every later test in this
    # process (caught for real: it broke test_build_packet_e2e.py's
    # "EIA_API_KEY is unset" assumption when both files ran in one session).
    monkeypatch.setenv("EIA_API_KEY", "offline-eval-fake-key")
    monkeypatch.setattr(eval_coverage.sys, "argv", ["eval_coverage.py"])
    exit_code = eval_coverage.main()
    assert exit_code == 0  # 2/8 (0.25) >= DEFAULT_THRESHOLD (0.20)
