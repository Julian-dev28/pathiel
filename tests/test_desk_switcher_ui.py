"""The Desk page's multi-desk build (2026-11-20, operator: "the FE ONLY
SHOWS OIL, show s&p and Hype"): the desk switcher's URL (`?desk=`) +
localStorage persistence, the "Last tick" bug fix (a PAPER-A6 line is not
any desk's own), and the Loop Log "show all lines" toggle.

Same convention as tests/test_wallets_card.py / tests/test_desk_page_chrome.py:
pin the exact source the behaviour hangs on, since pathiel/static/oil_desk.js
is DOM-coupled top to bottom (unlike entry_rows.js, which is a pure module
loaded through node, tests/test_entry_rows_js.py) and this repo carries no
jsdom. The one function this bug fix adds that IS pure — `lastDeskTickLine`,
no DOM at all — is extracted from the file and actually executed through
node, the same way test_entry_rows_js.py executes entry_rows.js, rather than
only pinned as text.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
HTML = (ROOT / "pathiel" / "templates" / "oil_desk.html").read_text()

NODE = shutil.which("node")


# ── lastDeskTickLine: pure, actually executed through node ─────────────────

def _run_last_desk_tick_line(lines, desk):
    start = JS.index("function lastDeskTickLine")
    end = JS.index("function renderLog()", start)
    func_src = JS[start:end]
    assert "document." not in func_src and "window." not in func_src, \
        "lastDeskTickLine must stay pure (no DOM) to run through node"
    script = (f"{func_src}\n"
              f"process.stdout.write(JSON.stringify("
              f"lastDeskTickLine({json.dumps(lines)}, {json.dumps(desk)})));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_last_tick_ignores_an_unaddressed_sim_line_even_when_it_is_last():
    """The exact 2026-11-20 bug report: a PAPER-A6 line (the oil-only paper-
    breakout sim) is the literal last entry in the shared log, but it is
    not sp500's or hype's own tick -- and must never be picked."""
    lines = [
        "DESK-TICK sp500 2026-11-20T00:00Z coin=xyz:SP500 mid=6800 action=NONE no setup",
        "PAPER-A6 2026-11-20T00:15Z mid=90.05 pos=long 0.42@88.50 stop=86.10 tp=91.66 action=HOLD",
    ]
    assert _run_last_desk_tick_line(lines, "sp500") == lines[0]
    assert _run_last_desk_tick_line(lines, "hype") is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_last_tick_picks_the_most_recent_of_this_desks_own_tick_lines():
    lines = [
        "DESK-TICK oil 2026-11-20T00:00Z action=NONE no setup",
        "DESK-TICK oil 2026-11-20T00:15Z action=PLACED 2.1R",
        "DESK-TICK hype 2026-11-20T00:16Z action=NONE no setup",
    ]
    assert _run_last_desk_tick_line(lines, "oil") == lines[1]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_last_tick_is_case_insensitive_and_ignores_watch_lines():
    lines = [
        "WATCH hype 2026-11-20T00:00Z coin=HYPE action=WAKE entry rule passed, 2.3R",
        "DESK-TICK HYPE 2026-11-20T00:01Z action=PLACED 2.3R",
    ]
    assert _run_last_desk_tick_line(lines, "hype") == lines[1]
    assert _run_last_desk_tick_line([], "hype") is None


# ── renderLog: strips the DESK-TICK prefix, never trusts the array's tail ──

def test_render_log_builds_the_last_tick_text_from_last_desk_tick_line():
    body = JS[JS.index("function renderLog()"):JS.index("// ── 6. TRACK RECORD")]
    assert "lastDeskTickLine(lines, activeDesk)" in body
    assert "tickLine.replace(/^DESK-TICK \\S+ /, '')" in body
    # the old bug: trusting the raw array's last element regardless of desk
    assert "lines[lines.length - 1]" not in body
    assert "last.hidden = true" in body  # no tick for this desk -> hidden, not blank text


# ── /log fetch asks the server to filter server-side, and the toggle flips
#    on the `all` query param rather than re-filtering client-side ─────────

def test_load_log_requests_this_desks_filtered_lines_by_default():
    func = JS[JS.index("async function loadLog()"):JS.index("/** The last line in `lines`")]
    assert "/api/desks/${encodeURIComponent(activeDesk)}/log?n=30${all}" in func
    assert "showAllLogLines ? '&all=1' : ''" in func


def test_the_show_all_checkbox_flips_showAllLogLines_and_refetches():
    assert "let showAllLogLines = false;" in JS
    wiring = JS[JS.index("document.getElementById('log-show-all')"):]
    assert "showAllLogLines = e.target.checked;" in wiring
    assert "loadLog();" in wiring


def test_the_loop_log_section_has_a_show_all_lines_checkbox():
    assert 'id="log-show-all"' in HTML
    section = HTML[HTML.index('id="log-section"'):HTML.index('id="log-body"')]
    assert 'id="log-show-all"' in section
    assert "Show all lines" in section


# ── desk switcher: ?desk= in the URL, in sync with localStorage ────────────

def test_url_desk_reads_the_desk_query_param():
    func = JS[JS.index("function urlDesk()"):JS.index("function setUrlDesk(")]
    assert "new URLSearchParams(window.location.search).get('desk')" in func


def test_set_url_desk_writes_the_param_without_a_new_history_entry():
    func = JS[JS.index("function setUrlDesk("):JS.index("/** Which desk(s) to render")]
    assert "url.searchParams.set('desk', id)" in func
    assert "history.replaceState(" in func
    assert "history.pushState(" not in func   # must not grow "back" history per switch


def test_load_active_desks_prefers_url_then_localstorage_then_the_first_active_desk():
    func = JS[JS.index("async function loadActiveDesks()"):JS.index("/** \"Oil")]
    assert "const fromUrl = urlDesk();" in func
    assert "const stored = PathielDesk.get();" in func
    # URL wins over localStorage, localStorage wins over the config default
    assert ("activeDesk = (fromUrl && activeDesks.includes(fromUrl)) ? fromUrl\n"
            "    : (stored && activeDesks.includes(stored)) ? stored\n"
            "    : activeDesks[0];") in func
    assert "PathielDesk.set(activeDesk);" in func
    assert "setUrlDesk(activeDesk);" in func


def test_switching_desks_via_the_tab_row_updates_both_the_url_and_localstorage():
    func = JS[JS.index("function renderDeskSwitcher()"):]
    handler = func[func.index("addEventListener('change'"):]
    assert "PathielDesk.set(activeDesk);" in handler
    assert "setUrlDesk(activeDesk);" in handler


def test_oil_stays_the_default_desk():
    """activeDesks[0] from the live config's own active_desks ordering
    (oil first: services/strategies/api.py's get_active_desks already falls
    back to ["oil"] and never reorders the configured list) -- the FE picks
    that element whenever the URL and localStorage both say nothing valid,
    so "oil stays the default" is a config-ordering fact this resolution
    must never override with its own hardcoded id (the separate fallback to
    ['oil'] a few lines above, for when GET /api/desks/active itself fails
    to answer at all, is the one legitimate 'oil' literal in this
    function -- a safety net, not the normal-path default)."""
    func = JS[JS.index("async function loadActiveDesks()"):JS.index("/** \"Oil")]
    resolution = func[func.index("const fromUrl = urlDesk();"):]
    assert "activeDesks[0]" in resolution
    assert '"oil"' not in resolution and "'oil'" not in resolution


# ── copy: console diagnostics generalize to the active desk, not "oil" ─────

def test_refresh_failure_labels_name_the_active_desk_not_a_hardcoded_oil():
    assert "reportRefreshFailure('oil desk call'" not in JS
    assert "reportRefreshFailure('oil desk line'" not in JS
    assert "reportRefreshFailure('oil levels'" not in JS
    assert "reportRefreshFailure(`${activeDesk} desk call`" in JS
    assert "reportRefreshFailure(`${activeDesk} desk line`" in JS
    assert "reportRefreshFailure(`${activeDesk} levels`" in JS
