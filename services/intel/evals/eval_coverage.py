#!/usr/bin/env python3
"""Coverage eval for the HARD DATA block: of the catalyst kinds
services/intel/sources/catalysts.py actually schedules for a desk, how
many have at least one real hard number (a DataPoint or a PositioningItem)
a thesis could cite instead of paraphrasing a headline?

This is the eval half of the deterministic/latent split for the
intel-hard-data brief: build_packet itself is pure code, but whether it
actually delivers "read what the big desks read -- hard numbers before
the headline" is an empirical, measurable claim, same as
eval_precision.py measures scoring.relevance's ranking quality instead
of just trusting the code compiled.

Usage:
    .venv/bin/python -m services.intel.evals.eval_coverage
    .venv/bin/python -m services.intel.evals.eval_coverage --threshold 0.2
    .venv/bin/python -m services.intel.evals.eval_coverage --online   # real network, real keys

Exit 0 if coverage >= threshold, 1 otherwise. Offline by default (every
HTTP call mocked from the real captured fixtures in
services/intel/tests/fixtures/ — see --online to hit the real APIs with
real keys instead, e.g. in a nightly job per CLAUDE.md's "periodic
evals" lane).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.strategies.models import DeskSpec, IntelPacket  # noqa: E402
from services.intel import http_client  # noqa: E402
from services.intel.api import build_packet  # noqa: E402
from services.intel.sources import catalysts  # noqa: E402

DEFAULT_THRESHOLD = 0.20  # see services/intel/README.md's "Coverage eval" section:
                          # this is today's real baseline (2/8 kinds), not an
                          # aspirational target -- raise it as api_weekly,
                          # opec_meeting, baker_hughes and cpi each get a real
                          # hard-number source.


def _has_datapoint(packet: IntelPacket, *, sources: tuple = (), label_has: tuple = ()) -> bool:
    for dp in packet.datapoints:
        if dp.source in sources and (not label_has or any(s in dp.label for s in label_has)):
            return True
    return False


def _has_positioning(packet: IntelPacket) -> bool:
    return bool(packet.positioning)


# catalyst kind -> (covered?(packet) -> bool, note on why/why not).
# Keep in sync with services/intel/sources/catalysts.py's _CALENDAR_GENERATORS
# and _ALWAYS_ON_KINDS/active_kinds -- this maps EVERY kind that function can
# schedule, not just the ones a given desk happens to list.
CATALYST_HARD_DATA_MAP: Dict[str, Callable[[IntelPacket], bool]] = {
    "eia_weekly": lambda p: _has_datapoint(p, sources=("eia",), label_has=("crude stocks",)),
    "eia_steo": lambda p: _has_datapoint(p, sources=("eia",)),
    "api_weekly": lambda p: False,   # API's own weekly number has no free structured feed
                                     # distinct from EIA's (API publishes PDF/press release only)
    "opec_meeting": lambda p: False,  # OPEC+ quota/compliance data has no free structured API
    "cftc_cot": _has_positioning,
    "baker_hughes": lambda p: False,  # Baker Hughes rig count is an XLSX download, no free API
    "fomc": lambda p: _has_datapoint(p, sources=("fred",)),  # oil desk is NOT FRED-gated by
                                                              # design (desk_rules.is_metals_desk/
                                                              # is_indices_desk) -- correctly 0 for oil
    "cpi": lambda p: False,           # BLS CPI release has no hard-number source wired yet
    "hurricane_atlantic": lambda p: False,  # qualitative (storm track/intensity), not a number series
    "token_unlock": lambda p: False,  # the unlock % itself IS the hard number, already in the
                                       # Catalyst's own `expected` field, not duplicated as a DataPoint
    "earnings": lambda p: _has_datapoint(p, sources=("sec_edgar",), label_has=("next earnings",)),
}


def coverage_for_packet(spec: DeskSpec, packet: IntelPacket) -> Dict[str, bool]:
    kinds = sorted(catalysts.active_kinds(spec))
    return {k: CATALYST_HARD_DATA_MAP.get(k, lambda p: False)(packet) for k in kinds}


def _oil_spec() -> DeskSpec:
    import json
    fixture = REPO_ROOT / "contracts" / "strategies" / "fixtures" / "oil_spec.json"
    return DeskSpec.model_validate(json.loads(fixture.read_text()))


def _mock_offline_network() -> None:
    """Routes every HTTP call to the real captured fixtures this service's
    own test suite uses — same fixtures, same router pattern as
    services/intel/tests/test_build_packet_e2e.py, so the offline eval
    measures the exact code path the gate tests already prove works,
    not a second hand-rolled stub that could silently drift from it.

    Also fills EIA_API_KEY with a fake value if the real environment has
    none (never overrides a real key) — this eval scores what the WIRED
    CODE can cover, not whether this particular runner happens to have
    registered for a free key yet; a missing-key skip is its own,
    separately-tested behavior (test_sources_eia.py), not something this
    coverage number should silently zero out.

    Mutates services.intel.http_client's module-level functions directly
    (this script is a one-shot CLI process, not a library) — a test that
    imports and calls this must wrap it in `monkeypatch.setattr(http_client,
    "fetch_json", http_client.fetch_json)` (etc.) BEFORE calling it, so
    pytest restores the real functions afterward; see
    test_eval_coverage.py's `test_eval_coverage_main_runs_offline_...`."""
    import json as _json
    import os as _os
    _os.environ.setdefault("EIA_API_KEY", "offline-eval-fake-key")

    fixdir = HERE.parent / "tests" / "fixtures"

    def fjson(name: str):
        return _json.loads((fixdir / name).read_text())

    def ftext(name: str):
        return (fixdir / name).read_text()

    def router_json(url: str, **kw):
        if "api.eia.gov" in url:
            # No live EIA_API_KEY was available to capture a real response
            # (see sources/eia.py's docstring) — six weekly levels so
            # _rows_to_datapoint has enough history for a surprise number.
            return {"response": {"data": [
                {"period": f"2026-10-{10 - i:02d}", "value": str(420_000 + i * 1_000)}
                for i in range(6)
            ]}}, None
        if "gdeltproject" in url:
            return fjson("gdelt_oil_sample.json"), None
        if "federalregister.gov" in url:
            return fjson("federal_register_ofac_iran.json"), None
        if "gamma-api.polymarket.com" in url:
            return fjson("polymarket_search_oil.json"), None
        if "kalshi.com" in url and "/series" in url:
            return fjson("kalshi_series_commodities.json"), None
        if "kalshi.com" in url and "/events" in url:
            return fjson("kalshi_events_kxwtiw.json"), None
        if "publicreporting.cftc.gov" in url:
            return fjson("cftc_cot_wti.json"), None
        if "nhc.noaa.gov" in url:
            return fjson("nhc_current_storms.json"), None
        if "defillama-datasets.llama.fi" in url:
            return {"data": []}, None
        if "sec.gov/files/company_tickers" in url:
            return fjson("sec_company_tickers_sample.json"), None
        if "data.sec.gov/submissions" in url:
            return fjson("sec_nvda_submissions_sample.json"), None
        return None, "unmocked_url"

    def router_text(url: str, **kw):
        if "news.google.com" in url:
            return ftext("google_news_oil.xml"), None
        if "oilprice.com" in url:
            return ftext("oilprice_main.xml"), None
        if "cointelegraph.com" in url:
            return ftext("cointelegraph.xml"), None
        if "ofac.treasury.gov" in url:
            return ftext("ofac_recent_actions.html"), None
        return None, "unmocked_url"

    def router_post(url: str, json_body, **kw):
        if json_body.get("type") == "metaAndAssetCtxs":
            return fjson("hl_meta_and_ctxs_xyz.json"), None
        if json_body.get("type") == "fundingHistory":
            return fjson("hl_funding_history_brentoil.json"), None
        return None, "unmocked_url"

    http_client.fetch_json = router_json
    http_client.fetch_text = router_text
    http_client.fetch_json_post = router_post


def report(kind_coverage: Dict[str, bool], threshold: float) -> bool:
    covered = sum(1 for v in kind_coverage.values() if v)
    total = len(kind_coverage)
    pct = covered / total if total else 0.0
    passed = pct >= threshold
    status = "PASS" if passed else "FAIL"
    print(f"=== oil desk HARD DATA coverage === {covered}/{total} catalyst kinds "
          f"({pct:.2f}, threshold {threshold:.2f}) [{status}]")
    for kind, ok in sorted(kind_coverage.items()):
        print(f"  [{'OK ' if ok else 'gap'}] {kind}")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--online", action="store_true",
                    help="hit real APIs with real keys instead of the offline fixtures")
    args = ap.parse_args()

    if not args.online:
        _mock_offline_network()

    from datetime import datetime, timezone
    spec = _oil_spec()
    packet = build_packet(spec, now=datetime.now(timezone.utc))
    kind_coverage = coverage_for_packet(spec, packet)
    passed = report(kind_coverage, args.threshold)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
