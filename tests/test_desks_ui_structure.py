"""Gate tests for the Desks UI: offline, no browser, no network.

Two layers:

  1. Pure template-text checks — every id and aria attribute desks.js and the
     e2e suite rely on actually exists in the committed HTML. Catches a typo'd
     id before it ever reaches a browser; same spirit as test_dashboard_pages.
     py's `'id="kpi-spark"' in r` checks, just for these two new templates.
  2. TestClient checks against tests/browser/desks_fixture_app.py — same ASGI
     app the Playwright suite drives, exercised in-process with no sockets, no
     Chromium. Fast and deterministic, so the refresh skip/done branching, the
     422-vs-500 split, and the per-desk state shapes (empty / stale / normal)
     are proven on every commit, not just when someone remembers to run -m
     browser.

Run:  PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest -q tests/test_desks_ui_structure.py
"""
from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "pathiel" / "templates"

# Loaded by file path, deliberately NOT `from tests.browser.desks_fixture_app
# import create_app`: the root tests/ directory has no __init__.py (by this
# repo's own convention — pytest's testpaths config relies on it), so a
# dotted `tests.*` import registers `tests` as a PEP 420 namespace package
# rooted here. services/pathiel_data_api/tests/ is a real package (it DOES
# have __init__.py) with its own conftest.py, and pytest resolves that
# conftest's module name as the same bare `tests.conftest` — colliding with
# the namespace package this file would otherwise create and breaking
# collection for the entire suite with ImportPathMismatchError. Confirmed by
# reproducing it: it disappears the moment this import stops using `tests.`.
_spec = importlib.util.spec_from_file_location(
    "desks_fixture_app_under_test", ROOT / "tests" / "browser" / "desks_fixture_app.py")
_desks_fixture_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_desks_fixture_app)
create_app = _desks_fixture_app.create_app

DESKS_HTML = (TEMPLATES / "desks.html").read_text(encoding="utf-8")
DESK_HTML = (TEMPLATES / "desk.html").read_text(encoding="utf-8")


def _ids(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


# ── 1. HTML structure ────────────────────────────────────────────────────────

def test_desks_html_has_no_duplicate_ids():
    found = re.findall(r'id="([^"]+)"', DESKS_HTML)
    dupes = {i for i in found if found.count(i) > 1}
    assert not dupes, dupes


def test_desk_html_has_no_duplicate_ids():
    found = re.findall(r'id="([^"]+)"', DESK_HTML)
    dupes = {i for i in found if found.count(i) > 1}
    assert not dupes, dupes


def test_desks_grid_required_ids():
    required = {
        "desks-loading", "desks-empty", "desks-error", "desks-retry", "desks-grid",
        "discreet-toggle", "status-pill", "wallet-connect-root",
    }
    missing = required - _ids(DESKS_HTML)
    assert not missing, missing


def test_desks_grid_links_shared_assets_and_own_css_js():
    # desks.css/js may carry a ?v= cache-buster (see pathiel/server.py
    # _RevalidatedStatic for why one was needed once), so match the prefix.
    assert '"/static/pathiel.css"' in DESKS_HTML
    assert '"/static/desks.css' in DESKS_HTML
    assert '"/static/pathiel.js"' in DESKS_HTML
    assert '"/static/desks.js' in DESKS_HTML


def test_desks_grid_has_desks_nav_link():
    assert re.search(r'<a href="/desks"[^>]*class="nav-link[^"]*">\s*Desks\s*</a>', DESKS_HTML)


def test_desk_page_required_ids_call_card():
    required = {
        "desk-root", "desk-loading", "desk-error", "desk-retry", "desk-content",
        "desk-name", "desk-tagline", "stale-banner",
        "call-card", "call-empty", "call-side-badge", "call-instrument", "call-one-liner",
        "call-leverage", "call-confidence", "call-entry", "call-stop", "call-targets",
        "call-invalidation", "call-age",
    }
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_required_ids_leverage():
    required = {
        "lev-card", "lev-empty", "lev-seg", "lev-liq-px", "lev-liq-dist", "lev-p-liq",
        "lev-p-win", "lev-ev", "lev-p10", "lev-p-double", "lev-at-risk", "lev-verdict", "lev-reason",
    }
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_required_ids_scenarios_catalysts_bullbear():
    required = {
        "scenarios-empty", "scenario-bar", "scenario-list",
        "catalysts-empty", "catalyst-chips",
        "bull-bear-empty", "bull-col", "bear-col",
        "wcmm-empty", "wcmm-list",
        "lessons-list", "lessons-upcoming",
    }
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_required_ids_history_and_track_record():
    required = {"track-record", "history-empty", "history-body"}
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_required_ids_user_thesis_form():
    required = {
        "user-thesis-form", "ut-form-error", "ut-success",
        "ut-side", "ut-instrument", "ut-leverage",
        "ut-entry", "ut-entry-error", "ut-stop", "ut-stop-error",
        "ut-targets", "ut-targets-error", "ut-one-liner", "ut-one-liner-error",
        "ut-confidence", "ut-confidence-display", "ut-confidence-error",
        "ut-invalidation", "ut-invalidation-error",
        "ut-scenarios", "ut-scenarios-error", "ut-add-scenario", "ut-notes", "ut-submit",
    }
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_required_ids_refresh_and_details():
    required = {
        "refresh-btn", "refresh-status", "desk-details",
        "snapshot-table", "pair-table", "forward-table", "odds-table",
        "positioning-table", "sources-list", "sources-failed",
    }
    missing = required - _ids(DESK_HTML)
    assert not missing, missing


def test_desk_page_leverage_switch_is_a_radiogroup():
    """Keyboard-accessible by construction: native radios in a named group get
    arrow-key navigation and immediate activation for free, no custom keydown
    handler needed. This asserts the DOM shape that guarantees that, not the
    behavior itself (that is the e2e suite's job)."""
    assert 'role="radiogroup"' in DESK_HTML
    seg_block = DESK_HTML[DESK_HTML.index('id="lev-seg"') - 80 : DESK_HTML.index('id="lev-seg"') + 80]
    assert 'role="radiogroup"' in seg_block


def test_desk_page_error_and_status_regions_are_announced():
    """Loading/error/success text changes get to a screen reader without the
    user having to go look for them."""
    assert 'aria-live="polite"' in DESK_HTML
    assert DESK_HTML.count('role="alert"') >= 2     # desk-error + ut-form-error at least
    assert DESK_HTML.count('role="status"') >= 1    # stale-banner / ut-success


def test_desk_page_details_section_uses_native_disclosure():
    assert re.search(r'<details class="explainer" id="desk-details">', DESK_HTML)


def test_one_liner_input_caps_at_160_matching_the_contract():
    assert 'id="ut-one-liner" maxlength="160"' in DESK_HTML


def test_neither_template_uses_a_cdn():
    for banned in ("unpkg.com", "fonts.googleapis", "fonts.gstatic", "https://cdn"):
        assert banned not in DESKS_HTML, banned
        assert banned not in DESK_HTML, banned


def test_neither_template_defines_tailwind_shaped_classes(tmp_path):
    """scripts/build_static_css.py hard-fails the build if a template uses a
    utility-shaped class token with no mapping, and two existing gate tests
    (test_dashboard_pages.py::test_v4_compiled_css_replaces_tailwind_runtime,
    test_trend_engine.py::test_css_build_is_current) assert the committed
    app.css is byte-exact with what that scanner emits for ALL templates,
    these two included now that they exist. Reproduce its own scan here so a
    future edit to these templates fails fast, in this file, with a clear
    reason, instead of as a confusing unrelated-looking failure over in
    someone else's test file."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_static_css as gen

    unknown = []
    for html in (DESKS_HTML, DESK_HTML):
        for m in re.findall(r'class="([^"]+)"', html):
            for tok in m.split():
                if gen.rule_for(tok) is None and gen.UTILITY_SHAPE.match(tok):
                    unknown.append(tok)
    assert not unknown, f"utility-shaped class with no mapping: {unknown}"


def test_app_css_is_unaffected_by_the_new_templates():
    """The stronger, end-to-end version of the check above: actually run the
    generator against the real templates directory (desks.html/desk.html
    included) and diff against the committed file byte for byte."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_static_css as gen

    assert gen.OUT.read_text() == gen.build(), "app.css changed by the new templates — not expected"


# ── 2. fixture app — fast, in-process, no browser ────────────────────────────

@pytest.fixture()
def client():
    return TestClient(create_app())


def test_grid_lists_all_four_desks_newest_first_order_is_stable(client):
    r = client.get("/api/desks")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()]
    assert ids == ["oil", "btc", "ai", "gas"]


def test_grid_card_shapes(client):
    cards = {d["id"]: d for d in client.get("/api/desks").json()}
    assert cards["oil"]["one_liner"] and cards["oil"]["call_side"] == "long"
    assert cards["oil"]["mid"] == pytest.approx(99.339)
    assert cards["oil"]["track_record"]["n_graded"] >= 1
    # ai has no thesis yet: one_liner/call_side/thesis_age_min must be null,
    # not a fabricated value, and it must still carry a track record shape.
    assert cards["ai"]["one_liner"] is None
    assert cards["ai"]["call_side"] is None
    assert cards["ai"]["thesis_age_min"] is None
    assert cards["ai"]["track_record"]["n_graded"] == 0


def test_desk_brief_oil_matches_the_real_fixtures(client):
    r = client.get("/api/desks/oil")
    assert r.status_code == 200
    brief = r.json()
    assert brief["spec"]["id"] == "oil"
    assert brief["thesis"]["call"]["one_liner"].startswith("Long WTI at 5x")
    assert brief["analytics"]["ladder"][0]["coin"] == "xyz:CL"
    assert brief["stale"] == []


def test_desk_brief_ai_is_the_empty_state(client):
    brief = client.get("/api/desks/ai").json()
    assert brief["thesis"] is None
    assert brief["history"] == []
    # analytics/intel still populated — the leverage switch and catalysts
    # should still have something to render even with no call yet.
    assert brief["analytics"]["ladder"]
    assert brief["intel"]["catalysts"]


def test_desk_brief_gas_is_the_stale_state(client):
    brief = client.get("/api/desks/gas").json()
    assert set(brief["stale"]) == {"analytics", "intel", "thesis"}
    assert brief["thesis"]["call"]["side"] == "short"
    assert all(r["side"] == "short" for r in brief["analytics"]["ladder"])
    # one catalyst is deliberately dated in the real future despite the rest
    # of the packet being 30 days stale.
    from datetime import datetime, timezone
    future = [c for c in brief["intel"]["catalysts"]
              if datetime.fromisoformat(c["at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)]
    assert future, "gas should have at least one real-future catalyst"


def test_unknown_desk_is_404_not_500(client):
    assert client.get("/api/desks/nope").status_code == 404
    assert client.post("/api/desks/nope/refresh", json={"force": False}).status_code == 404
    assert client.get("/api/desks/nope/theses").status_code == 404


def test_err500_desk_is_a_500_and_never_listed(client):
    ids = [d["id"] for d in client.get("/api/desks").json()]
    assert "err500" not in ids
    raw = TestClient(client.app, raise_server_exceptions=False)
    assert raw.get("/api/desks/err500").status_code == 500


def test_refresh_oil_skips_no_material_move(client):
    r = client.post("/api/desks/oil/refresh", json={"force": False})
    assert r.status_code == 200
    job = r.json()
    assert job["state"] == "skipped"
    assert job["message"] == "skipped: no material move"


def test_refresh_btc_runs_then_done_with_token_count_message(client):
    original_thesis_id = client.get("/api/desks/btc").json()["thesis"]["id"]

    r = client.post("/api/desks/btc/refresh", json={"force": False})
    job = r.json()
    assert job["state"] == "running"
    for _ in range(40):
        job = client.get(f"/api/desks/jobs/{job['id']}").json()
        if job["state"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job["state"] == "done"
    assert re.match(r"new thesis, \d+(\.\d+)?k tokens", job["message"]), job["message"]

    # The brief's current thesis must actually be the NEW one (a different id,
    # a newer created_at), not just "some thesis still present" — and it must
    # be the same object now sitting at the top of history.
    brief = client.get("/api/desks/btc").json()
    assert brief["thesis"]["id"] != original_thesis_id
    assert brief["thesis"]["id"].endswith("-m")
    assert brief["history"][0]["id"] == brief["thesis"]["id"]


def test_unknown_job_id_is_404(client):
    assert client.get("/api/desks/jobs/not-a-real-job").status_code == 404


def test_user_thesis_post_is_validated_client_mirrors_server_one_liner_cap(client):
    bad = {"call": {"side": "long", "instrument": "xyz:CL", "one_liner": "x" * 161,
                     "confidence": 0.5, "invalidation": "x"}}
    r = client.post("/api/desks/oil/theses", json=bad)
    assert r.status_code == 422


def test_user_thesis_post_is_validated_scenario_sum_translates_to_422_not_500(client):
    """The actual bug this suite caught while being built: UserThesisIn has no
    sum-to-1 validator of its own (only Thesis does, by contract design), so a
    bad split sails through request parsing and only blows up when the Thesis
    is actually constructed server-side. Pinned here so it can never regress
    back into an unhandled 500."""
    bad = {
        "call": {"side": "long", "instrument": "xyz:CL", "one_liner": "bad scenarios",
                  "confidence": 0.6, "invalidation": "x"},
        "scenarios": [{"name": "a", "probability": 0.9, "move_lo_pct": 0, "move_hi_pct": 0.1, "description": "d"}],
    }
    r = client.post("/api/desks/oil/theses", json=bad)
    assert r.status_code == 422, r.text
    assert "sum to" in str(r.json()["detail"])


def test_user_thesis_post_success_appears_at_top_of_history(client):
    before = client.get("/api/desks/oil/theses").json()
    payload = {"call": {"side": "short", "instrument": "xyz:CL", "one_liner": "My own short call.",
                          "confidence": 0.4, "invalidation": "close above 103"}}
    r = client.post("/api/desks/oil/theses", json=payload)
    assert r.status_code == 201
    created = r.json()
    assert created["author"] == "user"
    assert created["grade"] is None   # never auto-graded on submission

    after = client.get("/api/desks/oil/theses").json()
    assert len(after) == len(before) + 1
    assert after[0]["id"] == created["id"]


def test_track_record_by_author_has_both_machine_and_user_columns_for_btc(client):
    brief = client.get("/api/desks/btc").json()
    by_author = brief["track_record"]["by_author"]
    assert by_author["machine"]["n_graded"] >= 1
    assert by_author["user"]["n_graded"] >= 1


def test_desk_pages_render_and_are_self_contained(client):
    for path in ("/desks", "/desks/oil", "/desks/btc", "/desks/ai", "/desks/gas", "/desks/err500"):
        r = client.get(path)
        assert r.status_code == 200, path
        for banned in ("unpkg.com", "fonts.googleapis", "fonts.gstatic", "https://cdn"):
            assert banned not in r.text, f"{path} references CDN: {banned}"


def test_static_assets_are_mounted(client):
    assert client.get("/static/desks.css").status_code == 200
    assert client.get("/static/desks.js").status_code == 200
    assert client.get("/static/pathiel.css").status_code == 200
    assert client.get("/static/pathiel.js").status_code == 200
