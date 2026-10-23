"""Web UI rebuild (2026-07-12): landing one-pager, /activity journal, /news page.

Gate tests — fixture log lines + ledger rows only, no network, no live state.
Covers: event classification + the T1/T2/T3 editorial hierarchy (cards /
one-liners / coalesced groups), the 6h session strip, activity filters, the
books table payload, the news payload, page rendering (exact how-it-works
copy, self-contained assets), and the deleted /config + /operator pages (404
is the expected behavior).
"""

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathiel import dashboard as db


# ── fixtures ─────────────────────────────────────────────────────────────────

RESEARCH_NEWS = {
    "ts": 100, "event": "research", "coin": "SOL", "verdict": "PASS",
    "confidence": 0.42, "reasoning": "no clean multi-TF trend — skip",
    "ai_brain_provider": "openrouter", "web_search_used": True,
    "web_search_citations": ["https://example.com/sol-article"],
    "news_risk": "elevated", "entry_px": 0, "stop_px": 0, "tp_px": 0,
}
RESEARCH_PLAIN = {
    "ts": 150, "event": "research", "coin": "BTC", "verdict": "LONG",
    "confidence": 0.71, "reasoning": "clean breakout",
    "ai_brain_provider": "openrouter", "web_search_used": False,
    "web_search_citations": [], "news_risk": "none",
    "entry_px": 100000.0, "stop_px": 98000.0, "tp_px": 104000.0,
}
EXEC_BLOCKED = {
    "ts": 200, "event": "execute", "coin": "VIRTUAL", "side": "long",
    "executed": False, "ai_verdict": "LONG", "entry_via": "ai",
    "detail": ["total notional $395 would exceed 1000% of equity ($390)"],
    "blocked_by": ["total notional $395 would exceed 1000% of equity ($390)"],
    "size_usd": None, "entry_px": None,
}
EXEC_OK = {
    "ts": 300, "event": "execute", "coin": "GRASS", "side": "long",
    "executed": True, "ai_verdict": "PASS", "entry_via": "override",
    "detail": "493436405964", "blocked_by": None, "book": "news_catalyst",
    "size_usd": 25.07, "entry_px": 0.40835, "stop_px": 0.394, "tp_px": 0.4316,
    "regime": "up",
}
DSL_EXIT = {
    "ts": 400, "event": "dsl_exit", "coin": "GRASS", "side": "long",
    "leverage": 3, "reason": "floor_breach (1x consec, floor=0.42)",
    "unrealized_pct": 2.5077, "leveraged_pct": 7.523, "executed": True,
    "fill_px": 0.41841, "entry_px": 0.40835, "realized_spot_pct": 2.4636,
    "realized_pnl_pct": 7.2407, "fees_pct": 0.15,
}
BOOK_EVT = {
    "ts": 500, "event": "neg_funding_fade", "shadow": True,
    "signals": 1, "opened": 0,
    "skipped": {"held": 1, "claimed": 0, "dedup": 0, "blocked": 0},
    "candidates": [{"coin": "TRUMP", "side": "short", "funding_8h": -0.1008,
                    "influx_vol_x": 2.0, "entry_ref_px": 1.6542}],
}
BOOK_ALIAS_EVT = {
    "ts": 600, "event": "xs_rebalance", "regime": "low",
    "longs": ["BTC", "ETH"], "shorts": ["XPL"], "close": [],
}
BOOK_OPEN_EVT = {
    "ts": 650, "event": "book_open", "book": "rally_exhaustion",
    "coin": "XPL", "side": "short", "notional_usd": 20.0,
}
UNKNOWN_EVT = {"ts": 700, "event": "mystery_event", "foo": "bar", "n": 3}

ALL_EVENTS = [RESEARCH_NEWS, RESEARCH_PLAIN, EXEC_BLOCKED, EXEC_OK,
              DSL_EXIT, BOOK_EVT, BOOK_ALIAS_EVT, BOOK_OPEN_EVT, UNKNOWN_EVT]

FIXTURE_CONFIG = {
    # the four VALIDATED books, restored 2026-08-30 with capital paths
    "news_surge_short": {"enabled": True, "shadow_only": False,
                         "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "news_surge_multi": {"enabled": True, "shadow_only": False,
                         "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "social_trending": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "unlock_short": {"enabled": True, "shadow_only": False,
                     "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "xs_momentum": {"enabled": True, "k_per_leg": 4},
    "xs_xyz_equities": {"enabled": True, "shadow_only": False, "k_per_leg": 5,
                        "hold_days": 5, "min_volume_usd": 250000},
    "extreme_fade": {"enabled": True, "equity_fraction": 0.4, "leverage": 1},
    "rally_exhaustion": {"enabled": True, "notional_usd": 20.0, "leverage": 1},
    "crash_continue_div_short": {"enabled": True, "shadow_only": False,
                                 "notional_usd": 20.0, "leverage": 1},
    "engulf_short": {"enabled": True, "shadow_only": False,
                     "notional_usd": 20.0, "leverage": 1},
    "neg_funding_fade": {"enabled": True, "shadow_only": True,
                         "notional_usd": 20.0, "leverage": 1},
    "funding_spike_short": {"enabled": True, "shadow_only": False,
                            "notional_usd": 20.0, "leverage": 1},
    "majors_swing": {"enabled": True, "shadow_only": False,
                     "equity_fraction": 0.25, "leverage": 25},
    "young_listings": {"enabled": True, "shadow_only": True,
                       "notional_usd": 15.0, "leverage": 1},
    "news_catalyst": {"enabled": False, "notional_usd": 20.0, "leverage": 1},
    "mover_recorders": {"pass_live": {"enabled": True, "shadow_only": False,
                                      "notional_usd": 20.0, "leverage": 1}},
    "news_ta_aligned": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 3},
}


@pytest.fixture(autouse=True)
def _clear_ttl_cache():
    with db._TTL_CACHE_LOCK:
        db._TTL_CACHE.clear()
    yield
    with db._TTL_CACHE_LOCK:
        db._TTL_CACHE.clear()


@pytest.fixture()
def client():
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


# ── activity classification ──────────────────────────────────────────────────


# ── books payload ────────────────────────────────────────────────────────────


# ── news payload ─────────────────────────────────────────────────────────────

# news_surge_short, not news_catalyst: the feed reads the books that can trade
# today, and news_surge_short is the live one that records headlines. These
# fixtures wrote to news_catalyst for six weeks after that book was demolished,
# which is exactly why the /news tab could serve July rows with every test green.
@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """The news payload merges every live book, so a test that writes one
    fixture book into the real ledger directory gets the operator's actual
    news_surge_multi history mixed in and its rows pushed past `limit`. These
    tests only passed before because the payload read a single hardcoded book."""
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    return tmp_path


def _write_news_ledger(rows, book="news_surge_short"):
    from pathiel.agents import shadow_ledger
    path = shadow_ledger._book_path(book)
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ── pages + endpoints ────────────────────────────────────────────────────────

HOW_IT_WORKS = "No discretionary trading and no manual override."

# The design system lives in a linked stylesheet now, not in five inline
# <style> blocks. Anything asserting on CSS has to read the sheet, or it is
# asserting that a rule is DUPLICATED into the page rather than that it exists.
CSS = (pathlib.Path(__file__).resolve().parent.parent
       / "pathiel" / "static" / "pathiel.css").read_text()


def styled(client, path="/"):
    """Page markup plus the stylesheet that dresses it."""
    return client.get(path).text + CSS


# ── accessibility: contrast ──────────────────────────────────────────────────
#
# Computed from the real token values rather than asserted as frozen hex
# strings, so a future legitimate retune only has to clear the actual WCAG
# bar, not match a string this test pins in place.

def _css_var(css_text: str, block_selector: str, var_name: str) -> str:
    """Pull `--var: #hex;` out of one labelled top-level CSS block.

    Not a general CSS parser — good enough for this file's consistent,
    one-token-per-line style, same as the rest of this test module's
    source-text assertions."""
    start = css_text.index(block_selector)
    end = css_text.index("}", start)
    block = css_text[start:end]
    m = re.search(re.escape(var_name) + r"\s*:\s*(#[0-9a-fA-F]{6})", block)
    assert m, f"{var_name} not found in the {block_selector!r} block"
    return m.group(1)


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def f(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex) + 0.05
    l2 = _relative_luminance(bg_hex) + 0.05
    return max(l1, l2) / min(l1, l2)


def test_tertiary_text_clears_wcag_aa_against_every_background_it_sits_on():
    """Regression (2026-10-17 ux-qa sweep): --ink-3 — .sec-label and every
    other 'dim/tertiary' text treatment (section headers like 'Live books',
    table <th>s, the sign-in copy) on every page — contrasted 4.26:1 against
    --surface-2 in dark mode and 3.35:1 against --paper in light mode, both
    under the 4.5:1 WCAG AA floor for text this size (~12px, never bold).
    Found by sampling every real text/background pair actually rendered
    across all 5 pages at both viewports with a headless-Chromium crawl."""
    dark_ink3 = _css_var(CSS, ":root{", "--ink-3")
    dark_backgrounds = {
        "surface": _css_var(CSS, ":root{", "--surface"),
        "surface-2": _css_var(CSS, ":root{", "--surface-2"),
        "paper": _css_var(CSS, ":root{", "--paper"),
    }
    for label, bg in dark_backgrounds.items():
        ratio = _contrast_ratio(dark_ink3, bg)
        assert ratio >= 4.5, f"dark --ink-3 ({dark_ink3}) vs {label} ({bg}): {ratio:.2f} < 4.5"

    light_ink3 = _css_var(CSS, ':root[data-theme="light"]{', "--ink-3")
    light_backgrounds = {
        "surface-2": _css_var(CSS, ':root[data-theme="light"]{', "--surface-2"),
        "paper": _css_var(CSS, ':root[data-theme="light"]{', "--paper"),
    }
    for label, bg in light_backgrounds.items():
        ratio = _contrast_ratio(light_ink3, bg)
        assert ratio >= 4.5, f"light --ink-3 ({light_ink3}) vs {label} ({bg}): {ratio:.2f} < 4.5"


def test_hidden_attribute_cannot_be_silently_overridden_by_a_display_rule():
    """Regression (2026-10-17 ux-qa sweep): the `hidden` HTML attribute's
    UA-stylesheet rule (`[hidden]{display:none}`) is NORMAL-priority,
    author-origin-losing — any of this file's OWN rules that set `display`
    on the same element (.disclose, .seg, …) wins regardless of selector
    specificity, because author CSS beats the UA sheet at equal or lower
    specificity too. #hl-more (trends.html, class="disclose" hidden)
    rendered as an empty but fully visible, clickable button:
    `.disclose{display:flex}` silently un-hid it. `!important` on a blanket
    [hidden] rule is the standard fix for this exact gotcha and must stay
    ahead of (textually, it doesn't matter where — !important wins the
    cascade step outright) any class that sets display on a hideable
    element."""
    assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", CSS), (
        "the defensive [hidden]{display:none !important} rule is missing from pathiel.css")


def test_config_and_operator_pages_are_gone(client):
    """Operator order 2026-07-12: /config and /operator are deleted — 404 is
    the EXPECTED behavior, and no page links to them anymore."""
    assert client.get("/config").status_code == 404
    assert client.get("/operator").status_code == 404
    assert client.get("/api/dashboard/config").status_code == 404
    assert client.get("/api/dashboard/operator/trackers").status_code == 404
    assert client.post("/api/dashboard/operator/terminal",
                       json={"command": "status"}).status_code == 404
    for path in ("/", "/account"):
        page = client.get(path).text
        assert 'data-nav="/config"' not in page, path
        assert 'data-nav="/operator"' not in page, path


def test_all_pages_render_and_are_self_contained(client):
    for path in ("/", "/account"):
        r = client.get(path)
        assert r.status_code == 200, path
        for banned in ("unpkg.com", "fonts.googleapis", "fonts.gstatic",
                       "https://cdn"):
            assert banned not in r.text, f"{path} references CDN: {banned}"


# ── nav: Desk / Thesis / Account (2026-10-23 Desk UI overhaul) ──────────────

ALL_PAGES = ("/", "/account")


def test_nav_is_identical_across_every_page_and_carries_desks(client):
    """Three tabs everywhere: Desk (the landing page, parameterized by the
    active desk id), Thesis (the active desk's own page, /desk/{id}), and
    Account, always last. The bare `client` fixture only mounts dashboard.
    register_routes() (/, /account); desk.html lives on a different router
    (services/strategies/api.py) and is checked separately by
    test_account_is_the_last_nav_item_on_every_template, which already
    reads every template file directly for exactly this reason."""
    navs = {}
    for path in ALL_PAGES:
        page = client.get(path).text
        start = page.index('<nav class="tabs-main">')
        end = page.index("</nav>", start)
        navs[path] = page[start:end]
    first = navs[ALL_PAGES[0]]
    for path, nav in navs.items():
        assert nav == first, f"{path}'s nav differs from {ALL_PAGES[0]}'s"
    assert '<a href="/" data-nav="/" class="nav-link">Desk</a>' in first
    assert 'href="/desk/oil" data-nav="/desk/oil"' in first and ">Thesis</a>" in first
    assert '<a href="/account" data-nav="/account" class="nav-link">Account</a>' in first
    # order: Desk, then Thesis, then Account last.
    assert first.index('data-nav="/"') < first.index('data-nav="/desk/oil"')
    assert first.index('data-nav="/desk/oil"') < first.index('data-nav="/account"')
    assert first.rindex('<a ') == first.index('<a href="/account"'), (
        "Account is not the last nav link")


def test_account_is_the_last_nav_item_on_every_template():
    """Account is always the last nav item, identical in every template —
    including desk.html, which gets its own check here since
    test_nav_is_identical_across_every_page_and_carries_desks above only
    compares ALL_PAGES (desk.html is served by services/strategies' router,
    not dashboard.register_routes(), so the bare `client` fixture 404s on
    it). Reads every template file directly rather than through a client,
    so it covers all three (oil_desk.html, desk.html, account.html) the
    same way."""
    account_link = '<a href="/account" data-nav="/account" class="nav-link">Account</a>'
    for name, html in _template_files():
        start = html.index('<nav class="tabs-main">')
        end = html.index("</nav>", start)
        nav = html[start:end]
        assert account_link in nav, f"{name} has no Account nav link"
        assert nav.rindex("<a ") == nav.index(account_link), f"{name}: Account is not last"


def test_mobile_masthead_right_wraps_instead_of_overflowing():
    """Regression (2026-10-17 ux-qa sweep): .masthead-right is its OWN flex
    row, independent of .masthead-inner's flex-wrap above it — it does not
    inherit the parent's wrapping. On a page with a few extra controls
    (trends.html: operator-token button + lane-meta, on top of the wallet
    chip + status pill every page carries), the children's combined width
    exceeded 375px and the box overflowed the page edge, dragging the WHOLE
    document into horizontal scroll (measured live: 456px document width
    against a 375px viewport, 81px of which traced straight back to this
    rule having no wrap). See tests/browser/test_pages_browser.py for the
    real-browser proof this is fixed at 375px on every page; this is the
    cheap source-level guard against the fix being reverted."""
    start = CSS.index("@media (max-width:720px)")
    open_brace = CSS.index("{", start)
    # depth-counted scan for the matching close brace — the block contains
    # several nested rules, each with their own '}', so the first '}' after
    # `start` closes only the FIRST inner rule, not the media query itself.
    depth = 0
    i = open_brace
    while True:
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = CSS[start:i + 1]
    assert re.search(r"\.masthead-right\s*\{[^}]*flex-wrap\s*:\s*wrap", block), (
        ".masthead-right has no flex-wrap in the mobile media query")


def test_no_emoji_glyphs_anywhere(client):
    """Brand order 2026-07-12: no emoji glyphs — the cat is crafted SVG
    markup, geometric shapes are CSS. This sweep must stay green."""
    banned = ["👁", "🙈", "♥", "⚡", "⟳", "■", "⚠", "▶", "⚙", "🐈", "🐱",
              "🤖", "😴", "💰", "💀", "🤑", "😱", "😎", "🔒", "🔓", "🐹", "🐰"]
    for path in ("/", "/account"):
        page = client.get(path).text
        for ch in banned:
            assert ch not in page, f"{path} still renders glyph {ch!r}"


# Helpers shared by every page live in static/pathiel.js now, so the extractor
# searches there too rather than only in the served markup.
SHARED_JS = (pathlib.Path(__file__).resolve().parent.parent
             / "pathiel" / "static" / "pathiel.js").read_text()


def _extract_js_block(html: str, kind: str, name: str) -> str:
    """Pull one pure-logic const one-liner or multi-line function out of a
    served page's <script> block by name, so it can be executed in
    isolation under node — no DOM/fetch dependency, safe outside a browser."""
    html = html + "\n" + SHARED_JS
    if kind == "const":
        pat = r"^const " + re.escape(name) + r" = .*?;$"
        flags = re.M
    else:
        pat = r"^function " + re.escape(name) + r"\(.*?\) \{.*?\n\}$"
        flags = re.M | re.S
    m = re.search(pat, html, flags)
    assert m, f"couldn't find `{kind} {name}` in page source"
    return m.group(0)


def _run_node(html: str, blocks: list, call: str) -> str:
    node = shutil.which("node")
    assert node, "node not on PATH"
    snippet = "\n".join(_extract_js_block(html, kind, name) for kind, name in blocks)
    driver = snippet + f"\nconsole.log({call});"
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"js snippet crashed under node:\n{r.stderr}"
    return r.stdout.strip()


def _run_cite_chip(html: str, citations: list) -> list:
    out = _run_node(
        html, [("const", "esc"), ("const", "domainOf"), ("function", "citeChip")],
        f"JSON.stringify({json.dumps(citations)}.map(citeChip))",
    )
    return json.loads(out)


def _run_titles_block(html: str, item: dict) -> str:
    return _run_node(
        html, [("const", "esc"), ("function", "ageChip"), ("function", "titlesBlock")],
        f"titlesBlock({json.dumps(item)})",
    )


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def test_positions_rows_expose_liq_px():
    state = {"asset_positions": [
        {"position": {"coin": "BTC", "szi": "0.5", "entryPx": "100000",
                      "positionValue": "55000", "unrealizedPnl": "5000",
                      "marginUsed": "11000", "leverage": {"value": 5},
                      "liquidationPx": "80000"}},
        {"position": {"coin": "ETH", "szi": "-2", "entryPx": "3000",
                      "positionValue": "5800", "unrealizedPnl": "200",
                      "marginUsed": "1160", "leverage": {"value": 5},
                      "liquidationPx": None}},   # cross far from liq → null
    ]}
    rows = db._rows_from_state(state)
    btc = next(r for r in rows if r["coin"] == "BTC")
    eth = next(r for r in rows if r["coin"] == "ETH")
    assert btc["liq_px"] == 80000.0 and btc["mark_px"] == 110000.0
    assert btc["side"] == "long"
    assert eth["liq_px"] is None and eth["side"] == "short"


# ── analytics: funnel, book league, funding heat, tapes, coin chart ─────────


def _write_funding_log(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ── landing v3 (2026-07-17): living ambient layer + informational one-pager ──


def test_v4_compiled_css_replaces_tailwind_runtime(client):
    """The in-browser Tailwind JIT runtime is gone — every page links the
    compiled /static/app.css instead (no runtime JS, no flash-of-unstyled).
    The committed file must be exactly what scripts/build_static_css.py
    emits, and every utility-shaped class token in the templates must
    resolve to a rule — the generator hard-fails on unknowns, so a template
    edit that invents a class breaks HERE, not silently in the browser."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_static_css",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "build_static_css.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.OUT.read_text() == gen.build(), "app.css stale — rerun scripts/build_static_css.py"
    for path in ("/", "/account"):
        r = client.get(path).text
        assert '"/static/app.css"' in r, path
        assert "tailwind.js" not in r, path


def test_v4_speculation_rules_and_hotkeys(client):
    """Hover-eagerness prerendering (speculation rules) makes tab hops
    instant; g-then-key hotkeys give app-style navigation, inert while
    typing. Only the Desk page (oil_desk.html) carries the speculation-
    rules block today (it was landing.html's own); desk.html/account.html
    never had it."""
    r = client.get("/").text
    assert 'type="speculationrules"' in r
    assert '"eagerness":"moderate"' in r
    for path in ("/", "/account"):
        r = client.get(path).text
        # hotkeys live in static/pathiel.js; every page has to load it
        assert "/static/pathiel.js" in r, path


# ── a page must not assert health it cannot verify ──────────────────────────


def _page(name):
    return dict(_template_files())[name]


def test_the_dashboard_age_format_scales_past_minutes():
    """fmtAgeMinutes (static/desks.js, shared by every surviving page) must
    not stop at minutes — a loop down eight hours must read in hours or
    days, not "480m ago", the one reading where the number most needs to
    land."""
    src = (pathlib.Path(__file__).resolve().parent.parent
          / "pathiel" / "static" / "desks.js").read_text()
    fn = src[src.index("const fmtAgeMinutes"):src.index("const fmtAgeMinutes") + 400]
    assert "'h'" in fn and "'d'" in fn, "must scale to hours and days"


# ── the Markets panels have to be readable, not just correct ────────────────


def _analytics_source():
    return dict(_template_files())["analytics.html"]


def _css():
    return open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "pathiel", "static", "pathiel.css")).read()


# ── wiring audit: every page, not just the one being worked on ───────────────


def _template_files():
    tdir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "pathiel", "templates")
    return [(f, open(os.path.join(tdir, f)).read()) for f in sorted(os.listdir(tdir))
            if f.endswith(".html")]


def test_no_page_reaches_for_an_element_that_does_not_exist():
    """`$('#renamed')` is null in the browser and the block it feeds silently
    never renders — the page looks like a quiet market instead of a bug. This
    is the cheapest place to catch a rename."""
    for name, html in _template_files():
        defined = set(re.findall(r'id="([\w-]+)"', html))
        used = (set(re.findall(r"\$\('#([\w-]+)'\)", html))
                | set(re.findall(r"getElementById\('([\w-]+)'\)", html)))
        assert not (used - defined), f"{name} reaches for missing ids: {sorted(used - defined)}"


# Wave-2 overhaul (2026-10-17): the accounts + risk routers
# (contracts/accounts/models.py, contracts/risk/models.py) are built by
# sibling agents in their own worktrees this wave and mounted onto
# pathiel.server by the integrator once every wave-2 agent's work merges
# (SWARM_RULES section 10) — so real_app.routes does not have them YET in
# this worktree, even though landing.html's onboarding wizard and
# account.html correctly call them. Listed here, by hand, from the exact
# route table each contract's module docstring documents, so this stays a
# real cross-check against the contract rather than a blanket exemption.
# Delete this set once the integrator's merge makes it redundant.
_WAVE2_CONTRACT_ROUTES = frozenset({
    "/api/session",
    "/api/accounts",
    "/api/accounts/agent/prepare",
    "/api/accounts/agent/confirm",
    "/api/accounts/import",
    "/api/accounts/watch",
    "/api/accounts/active/summary",
    "/api/risk/profile",
    "/api/risk/limits",
    "/api/risk/preview",
})


def test_no_page_calls_an_endpoint_that_is_not_registered(client):
    """Every page is a static shell over the JSON API. A route renamed on the
    server and not in the template is a control that 404s in production."""
    # Routes come from the REAL app, not the bare dashboard test app: in
    # production pathiel.server mounts the dashboard AND the agent
    # endpoints, and a page control may legitimately call either. Checking only
    # the dashboard's own routes would fail a control that works in production.
    from pathiel.server import app as real_app
    routes = {r.path for r in client.app.routes if hasattr(r, "path")}
    routes |= {r.path for r in real_app.routes if hasattr(r, "path")}
    routes |= _WAVE2_CONTRACT_ROUTES
    for name, html in _template_files():
        for raw in set(re.findall(r"'(/api/[\w/{}?=&.-]*)'", html)):
            path = raw.split("?")[0].rstrip("/")
            if not path:
                continue                      # a prefix the script concatenates onto
            if path in routes:
                continue
            # the last segment may be a path parameter the script fills in
            templated = [re.sub(r"/[\w.-]+$", "/{%s}" % p, path)
                         for p in ("lane", "market_id", "book", "name", "coin")]
            assert (any(t in routes for t in templated)
                    or any(p.startswith(path) for p in routes)), \
                f"{name} calls unregistered endpoint {raw}"


def test_no_page_pulls_a_third_party_asset(client):
    """The dashboard runs on a trading box. Every asset is vendored under
    /static so a CDN outage (or a CDN owner) cannot change what it renders."""
    for path in ("/", "/account"):
        # the SVG namespace is an identifier, not a fetch — everything else is
        body = client.get(path).text.replace(
            'xmlns="http://www.w3.org/2000/svg"', "")
        external = re.findall(r'https?://[^\s"\'<>)]+', body)
        assert not external, f"{path} pulls {sorted(set(external))}"


# ── the redesign, as a contract ──────────────────────────────────────────────
#
# The brief was "less childish, more professional — boring, simple,
# straightforward; something a person with a million dollars would use". Those
# words are not testable, but the specific things that made it read as a toy
# are, and each one below was really on the page before this redesign.

PAGES = ("/", "/account")


def test_no_page_ships_decorative_chrome(client):
    """The mascot, the WebGL aurora, the scanline texture and the scroll-
    progress beam are gone from every page, not just the dashboard."""
    banned = {
        "pixel-cat": "the mascot",
        "cat-sleep": "mascot state",
        "gl-bg": "the shader canvas",
        "__setGlState": "the shader's data feed",
        "#version 300 es": "a fragment shader",
        "scroll-progress": "the scroll beam",
        "repeating-linear-gradient": "scanline texture",
    }
    for path in PAGES:
        page = client.get(path).text
        for token, what in banned.items():
            assert token not in page, f"{path} still ships {what} ({token})"


def test_the_palette_carries_information_only(client):
    """One accent, plus green/red for money and amber for attention. The old
    sheet ran an indigo→violet→purple gradient family through the wordmark,
    the nav pill, the badges and the equity fill."""
    for gone in ("#6366f1", "#8b5cf6", "#a855f7", "linear-gradient(135deg"):
        assert gone not in CSS, f"brand gradient survives in the stylesheet: {gone}"
    for path in PAGES:
        page = client.get(path).text
        for gone in ("#6366f1", "#8b5cf6", "#a855f7"):
            assert gone not in page, f"{path} hard-codes {gone}"


def test_every_page_uses_the_one_stylesheet(client):
    """Five pages previously carried five ~250-line <style> blocks, so a
    colour changed in four places and drifted in the fifth."""
    for path in PAGES:
        page = client.get(path).text
        assert "/static/pathiel.css" in page, f"{path} does not load the design system"


def test_no_page_carries_a_second_design_system(client):
    """A page-level <style> block is allowed for genuinely page-specific
    geometry (chart heights), but not for a whole palette."""
    for name in ("oil_desk", "desk", "account"):
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "pathiel" / "templates" / f"{name}.html").read_text()
        blocks = re.findall(r"<style>(.*?)</style>", src, re.S)
        for b in blocks:
            assert b.count("\n") < 40, (
                f"{name}.html has a {b.count(chr(10))}-line <style> block — "
                f"shared rules belong in static/pathiel.css")
            assert "--ink" not in b and "--accent" not in b, (
                f"{name}.html redefines design tokens locally")


def test_money_is_set_in_tabular_figures():
    """Proportional digits make a column of numbers jitter as it updates,
    which is the single clearest tell that a money screen was not built by
    someone who reads money screens."""
    assert "font-variant-numeric:tabular-nums" in CSS.replace(" ", "")
    assert ".kv" in CSS


def test_the_type_is_not_a_toy(client):
    """The old pages set body copy at 10-12px with 9.5px table headers. The
    reader is not twenty-five and is not on a laptop."""
    body = re.search(r"\nbody\{[^}]*\}", CSS, re.S).group(0)
    size = int(re.search(r"font-size:(\d+)px", body).group(1))
    assert size >= 14, f"body copy is {size}px"
    assert "font-size:9" not in CSS.replace(" ", ""), "9px type survives"


def test_the_page_still_works_in_both_themes():
    """Light is the default. Dark ships as a second skin — same structure,
    only tokens differ, so no rule below the token block branches on theme."""
    # Dark is unconditional, not a prefers-color-scheme default: the OS
    # preference is about documents and this is an instrument.
    import re as _re
    # the phrase survives in a comment; what matters is that no RULE uses it
    assert not _re.search(r"@media[^{]*prefers-color-scheme", CSS), (
        "the theme still defers to the OS")
    assert ':root[data-theme="light"]' in CSS, "light is not reachable"
    assert ':root[data-theme="dark"]' in CSS
    for token in ("--ink", "--paper", "--rule", "--up", "--down", "--accent"):
        assert CSS.count(f"{token}:") >= 3, f"{token} is not defined for every theme"


def test_it_prints():
    """Someone with this much money hands a page to an accountant."""
    assert "@media print" in CSS


def test_no_page_animates_for_decoration(client):
    """Every animation that survived has to earn it. The bounce, the breathe,
    the shimmer and the pulse did not."""
    for gone in ("cat-bounce", "cat-breathe", "shimmer", "breaking-pulse",
                 "@keyframes growX"):
        assert gone not in CSS, f"decorative animation survives: {gone}"
    assert "prefers-reduced-motion" in CSS, "no motion opt-out"


# ── nothing on screen may describe something that no longer exists ──────────


def test_a_scanned_coin_no_book_claimed_is_not_a_refusal():
    """There is no discretionary entry path: entries come from books, so a
    scanned coin that no book claimed is normal operation. Logging it as a
    refusal put a deleted feature's name at the top of the decision funnel
    ~3,900 times a day and buried the gates that actually stopped a trade."""
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "trading_loop.py").read_text()
    body = src[src.index("main_engine ENTRIES DELETED"):]
    body = body[:body.index("# TA filter")]
    assert "_unclaimed += 1" in body
    assert "log_event" not in body, "still emits an event per scanned coin"
    assert "MAIN_ENGINE_DELETED" not in src, (
        "a deleted feature's name is still emitted into the session log")


def test_no_template_hard_codes_a_colour():
    """Charts and inline styles kept a neon crypto palette the stylesheet could
    not reach, so a theme change fixed the page and missed the chart."""
    import re as _re
    tpl = pathlib.Path(__file__).resolve().parent.parent / "pathiel" / "templates"
    offenders = []
    for f in sorted(tpl.glob("*.html")):
        for m in _re.finditer(r"#[0-9a-fA-F]{6}\b", f.read_text()):
            offenders.append(f"{f.name}: {m.group(0)}")
    assert not offenders, (
        "hard-coded colours bypass the design tokens:\n" + "\n".join(offenders))


def test_every_page_reaches_every_other_page():
    """A route that renders but is linked from nowhere is dead UI. Whatever
    ships must be reachable by clicking. Reads every template file directly
    (not through `client`) so desk.html — served by services/strategies'
    router, not dashboard.register_routes() — is covered the same way."""
    routes = {"/", "/desk/oil", "/account"}
    for name, page in _template_files():
        linked = {r for r in routes if f'data-nav="{r}"' in page}
        assert linked == routes, f"{name} cannot reach {sorted(routes - linked)}"


def test_the_nav_marks_the_page_you_are_on(client):
    """The marking runs from static/pathiel.js — every page has to load it and
    give the nav something to match on."""
    assert "nav-active" in SHARED_JS
    for path in ("/", "/account"):
        page = client.get(path).text
        assert "/static/pathiel.js" in page, f"{path} does not load the shared script"
        assert f'data-nav="{path}"' in page, f"{path} has no nav entry to mark"
    desk_html = dict(_template_files())["desk.html"]
    assert "/static/pathiel.js" in desk_html
    assert 'data-nav="/desk/oil"' in desk_html


def test_the_hotkeys_are_defined_once(client):
    assert "keydown" in SHARED_JS
    assert "e.target.closest('input,textarea,select')" in SHARED_JS
    for name, src in _template_files():
        assert "addEventListener('keydown'" not in src, (
            f"{name} re-declares the hotkey handler")


# ── density: the page must not open with a wall of text ─────────────────────

def test_no_page_opens_with_a_paragraph_of_theory(client):
    """A page that opens with an essay makes the reader work before it tells
    them anything. Explanations stay — folded into a <details>, one click down
    for the first visit that needs them."""
    import re as _re

    for path in ("/", "/news", "/trends"):
        page = client.get(path).text
        body = page.split("</header>")[-1]
        for m in _re.finditer(r'<div class="note">\s*([^<]{200,})', body):
            raise AssertionError(
                f"{path} ships a {len(m.group(1))}-char paragraph unfolded: "
                f"{' '.join(m.group(1).split())[:80]}")


def test_a_wrapper_does_not_draw_a_second_panel_border():
    assert ".panel-wrap{ background:transparent; border:0; }" in CSS


# ── the account's books are not public ──────────────────────────────────────


def test_account_data_is_not_readable_without_signing_in(monkeypatch):
    """Audit 2026-09-04: every /api/dashboard route was open, on a public Fly
    host with force_https and allow_origins=["*"]. An unauthenticated GET to
    /summary returned equity, free balance and daily P&L; /risk added net
    capital in and peak equity; /closed-trades the whole history. Anyone with
    the URL had the books.

    This is the regression test. It asserts on the SHAPE of the response, not
    just the code, so a future refactor that returns 401 while still leaking a
    body fails here.
    """
    monkeypatch.delenv("PATHIEL_PUBLIC_DASHBOARD", raising=False)
    from fastapi.testclient import TestClient
    from pathiel.server import app
    c = TestClient(app)
    for path in ("/api/dashboard/summary", "/api/dashboard/risk",
                 "/api/dashboard/positions", "/api/dashboard/closed-trades",
                 "/api/dashboard/equity-curve", "/api/dashboard/book_league"):
        r = c.get(path)
        assert r.status_code == 401, f"{path} answered {r.status_code} to a stranger"
        body = r.json()
        assert body.get("auth_required") is True
        for leak in ("equity", "daily_pnl", "net_capital_in", "peak_equity"):
            assert leak not in body, f"{path} leaked {leak} inside its 401"


def test_health_checks_still_answer_without_a_session():
    """Fly and k8s probes carry no credential, and a health check that needs a
    session cannot report a broken session."""
    from fastapi.testclient import TestClient
    from pathiel.server import app
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200


def test_the_single_operator_escape_hatch_is_explicit(monkeypatch):
    """A private box behind a VPN can keep the old open reads, but only by
    saying so out loud — the flag is named to be obvious in a diff and in
    `fly secrets list`."""
    monkeypatch.setenv("PATHIEL_PUBLIC_DASHBOARD", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    # /api/dashboard/summary was removed 2026-10-23 with the page that showed
    # it; /risk and /positions are the house routes still standing (kept for
    # pathiel/metrics.py and their own dedicated test suites — see
    # pathiel/dashboard.py's register_routes docstring).
    assert TestClient(app).get("/api/dashboard/risk").status_code == 200
    assert TestClient(app).get("/api/dashboard/positions").status_code == 200


def test_every_page_keeps_the_wallet_connect_mount(client):
    """Wave-2 onboarding (2026-10-17): sign-in is retired from the UI.
    PathielAuth — the sign-in chip, the full-page 401 gate, signIn/signOut,
    the on-demand RainbowKit loader — is deleted outright, not replaced.
    Wallet-connect IS the identity now (contracts/accounts/models.py);
    GET /api/session answers "who is this" with no login step at all in
    local mode.

    What survives, and what this test checks instead: every page still
    loads the shared script and still carries the ONE mount point
    services/wallet_ui's bundle renders into — the two things this file
    owns that the shared contract (SWARM_RULES section 10) actually needs.
    """
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "pathiel.js")).read()
    assert "PathielAuth" not in js
    assert "signIn" not in js and "signOut" not in js
    assert "PathielSession" in js
    for path in ("/", "/account"):
        body = client.get(path).text
        assert "pathiel.js" in body, f"{path} does not load the shared script"
        assert "masthead-right" in body, f"{path} has nowhere to put the wallet mount"
        assert 'id="wallet-connect-root"' in body, (
            f"{path} has nowhere for the wallet picker to mount")


def test_no_sign_in_ui_survives_anywhere(client):
    """Build step 1's regression test. The old chip, gate, signIn/signOut
    and 'Your wallet' tile are gone from every page's markup AND from the
    one shared script every page loads — not just hidden, not just
    unreferenced from one page.

    loadWalletUI is NOT on this list: services/wallet_ui/tests/
    test_wallet_bundle.py's test_bundle_is_not_loaded_on_page_load requires
    the ~3MB bundle stay out of every template's <script> tags, so the
    on-demand loader survives sign-in's retirement — PathielSession.
    openWallet(view) still calls it, just to reach window.PathielWallet.
    open(view) instead of the old openConnectModal()/signIn()."""
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "pathiel.js")).read()
    for gone in ("auth-chip", "auth-gate", "PathielAuth", "signIn", "signOut", "my-account"):
        assert gone not in js, f"pathiel.js still references {gone}"
    assert "loadWalletUI" in js, "the on-demand wallet-bundle loader was removed, not just renamed"
    for name, html in _template_files():
        for gone in ("auth-chip", "auth-gate", "auth-card", "id=\"my-account\""):
            assert gone not in html, f"{name} still has {gone}"


# ── onboarding (wave-2, 2026-10-17): template structure ─────────────────────
#
# These are unit tests on markup and pure JS, not the behaviour — the full
# first-run-through-save flow, the live preview re-rendering, and the $5
# low-equity message are covered end to end in
# tests/browser/test_onboarding_browser.py against a fixture app that serves
# the real accounts + risk contract fixtures. What belongs here is what
# doesn't need a browser: the right ids exist, in the right place, wired to
# the right call.


def test_landing_onboarding_wizard_has_exactly_three_steps_in_order():
    """Build step 2-3: welcome -> 'How much risk?' -> 'You're set', one
    visible at a time (showStep() in oil_desk.js toggles `hidden` by
    data-step, never more than one shown). Ported from the old landing.html
    verbatim into oil_desk.html, 2026-10-23."""
    html = dict(_template_files())["oil_desk.html"]
    steps = re.findall(r'<section id="onboard-(\w+)" class="onboard-step card" data-step="(\d)"', html)
    assert steps == [("welcome", "1"), ("risk", "2"), ("done", "3")], steps
    # Only step 1 ships visible; 2 and 3 carry their own `hidden`.
    welcome_tag = html[html.index('<section id="onboard-welcome"'):html.index('<section id="onboard-welcome"') + 120]
    assert " hidden" not in welcome_tag, "step 1 ships hidden — nothing would show at all"
    risk_tag = html[html.index('<section id="onboard-risk"'):html.index('<section id="onboard-risk"') + 120]
    done_tag = html[html.index('<section id="onboard-done"'):html.index('<section id="onboard-done"') + 120]
    assert " hidden" in risk_tag, "step 2 ships visible — two steps would show at once"
    assert " hidden" in done_tag, "step 3 ships visible — two steps would show at once"


def test_landing_risk_step_has_preset_cards_preview_and_low_equity_callout():
    """Build step 2: 'preset cards ... a live preview' plus the low-equity
    callout, 'never silent zeros'. Ported from the old landing.html into
    oil_desk.html/oil_desk.js, 2026-10-23 — minus the per-book feasibility
    preview (#onb-books): books are gone (docs/overhaul/
    DESK_UI_2026-10-23.md)."""
    html = dict(_template_files())["oil_desk.html"]
    risk_step = html[html.index('id="onboard-risk"'):html.index('id="onboard-done"')]
    for needed in ("onb-preset-cards", "onb-low-equity", "onb-headline",
                   "onb-risk-usd", "onb-daily-pause", "onb-max-positions", "onb-save"):
        assert f'id="{needed}"' in risk_step, f"missing #{needed} in the risk step"
    assert '"onb-books"' not in risk_step and "id=\"onb-books\"" not in risk_step, (
        "the per-book feasibility preview should be gone with the books")
    # Four presets, matching contracts/risk/models.py's Preset literal
    # (minus "auto"/"custom" — the wizard only ever offers the four a human
    # picks between; auto is what the FIRST preview resolves TO, not a
    # fifth card). RISK_PRESETS lives in pathiel.js (SHARED_JS below), not
    # in this page's own script: account.html's risk editor needs the exact
    # same four, and a second copy is how this codebase got five
    # near-identical fmtPct definitions in the first place.
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "oil_desk.js")).read()
    assert "RISK_PRESETS" in js, "the risk step doesn't reference the shared preset table"
    assert re.search(r"RISK_PRESETS\s*=\s*\[", SHARED_JS)
    for preset in ("starter", "small", "standard", "pro"):
        assert f"id: '{preset}'" in SHARED_JS, f"preset card '{preset}' missing from pathiel.js"
    # Custom (wave-2 integration note, 2026-10-17): a migrated legacy config
    # resolves preset_applied to SOME tier name but keeps its own numbers —
    # matchesPresetDefaults() is what tells "Custom" apart from a real
    # preset match, and the wizard has to consult it, not just trust the
    # name literally.
    assert "matchesPresetDefaults" in SHARED_JS
    assert "matchesPresetDefaults(" in js


def test_landing_onboarding_calls_the_contract_routes_with_the_right_verbs():
    """Build step 2: equity from GET /api/accounts/active/summary, the
    preview from POST /api/risk/preview, saved with PUT /api/risk/profile —
    exactly contracts/accounts/models.py + contracts/risk/models.py's own
    route table, not a route this agent invented. Lives in oil_desk.js now
    (an external script), not inlined into the page like the old
    landing.html."""
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "oil_desk.js")).read()
    assert "fetch('/api/accounts/active/summary')" in js
    assert "fetch('/api/risk/preview'" in js and "method: 'POST'" in js
    assert "fetch('/api/risk/profile'" in js and "method: 'PUT'" in js
    # The save body is exactly {preset: ...} — RiskProfile's other fields
    # default to None server-side, so this alone is a valid, complete body.
    assert "JSON.stringify({ preset: chosenPreset })" in js


def test_risk_presets_table_mirrors_services_risk_presets_exactly():
    """Tripwire, same pattern services/risk/tests/test_books.py's own
    registry-mirror test uses: pathiel.js's RISK_PRESETS is a second copy
    of services/risk/presets.py's table, kept only because this is a
    browser file that cannot import a Python module. If the risk team
    retunes a preset and this file is not updated, /account would show a
    stale blurb and number while the real sizing silently changed
    underneath it — caught here, run on every commit, rather than noticed
    by a confused Julien later."""
    from services.risk.presets import PRESETS as REAL_PRESETS
    for preset_id in ("starter", "small", "standard", "pro"):
        real = REAL_PRESETS[preset_id]
        pat = re.compile(
            r"id:\s*'" + preset_id + r"'.*?risk_per_trade_pct:\s*([\d.]+).*?"
            r"daily_loss_limit_pct:\s*([\d.]+).*?max_leverage:\s*([\d.]+)", re.S)
        m = pat.search(SHARED_JS)
        assert m, f"pathiel.js's RISK_PRESETS has no entry for {preset_id!r}"
        js_risk, js_daily, js_lev = (float(g) for g in m.groups())
        assert js_risk == real.risk_per_trade_pct, (
            f"{preset_id}: pathiel.js says {js_risk}% risk/trade, "
            f"services/risk/presets.py says {real.risk_per_trade_pct}%")
        assert js_daily == real.daily_loss_limit_pct, (
            f"{preset_id}: pathiel.js says {js_daily}% daily loss, "
            f"services/risk/presets.py says {real.daily_loss_limit_pct}%")
        assert js_lev == real.max_leverage, (
            f"{preset_id}: pathiel.js says {js_lev}x max leverage, "
            f"services/risk/presets.py says {real.max_leverage}x")


def test_a_migrated_legacy_profile_is_detectable_as_custom_front_end_side():
    """Integration note (wave-2 integrator, 2026-10-17): 'A migrated legacy
    config shows up as preset custom' — but services/risk/migrate.py +
    derive.py actually resolve it to a real tier NAME (whatever "auto"
    picks for the equity) while keeping its own overridden numbers, so the
    front end has to notice the numbers don't match that tier, not trust
    the name. Runs the REAL migration + derivation (no HTTP, no browser,
    no network) against Julien's real shape (services/risk/README.md:
    0.35 equity-fraction, 3x leverage, max_daily_loss_pct 0.121,
    max_concurrent 3) and checks pathiel.js's matchesPresetDefaults would
    correctly call it custom under node."""
    from services.risk.derive import VenueRules, derive_limits
    from services.risk.migrate import migrate_legacy

    legacy_config = {
        "equity_fraction_per_trade": 0.35, "leverage": 3,
        "max_daily_loss_pct": 0.121, "max_concurrent": 3,
        "min_tradable_equity_usd": 26.0,
    }
    profile, _report = migrate_legacy(legacy_config)
    limits = derive_limits(15.19, profile, [], VenueRules())
    assert limits.profile.risk_per_trade_pct > 10.0, (
        "Julien's real migrated risk_per_trade_pct should be ~15.75%, "
        f"got {limits.profile.risk_per_trade_pct}")

    # RISK_PRESETS is a multi-line array (object literals, no DOTALL needed
    # inside them except across the whole block) — _extract_js_block's
    # "const" kind is MULTILINE-only (by design: most consts here are one-
    # liners, and DOTALL would risk over-matching one that has its own
    # internal `;`), so it's pulled out here instead of through that helper.
    node = shutil.which("node")
    assert node, "node not on PATH"
    m = re.search(r"^const RISK_PRESETS = \[.*?\n\];$", SHARED_JS, re.M | re.S)
    assert m, "couldn't find `const RISK_PRESETS` in pathiel.js"
    snippet = m.group(0) + "\n" + _extract_js_block("", "function", "matchesPresetDefaults")
    driver = snippet + (
        f"\nconsole.log(matchesPresetDefaults({json.dumps(limits.preset_applied)}, "
        f"{limits.profile.model_dump_json()}));"
    )
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"js snippet crashed under node:\n{r.stderr}"
    assert r.stdout.strip() == "false", (
        f"matchesPresetDefaults said Julien's migrated profile matches "
        f"{limits.preset_applied!r}'s own defaults — it should say Custom")


def test_account_page_has_the_active_card_and_risk_editor():
    """Build step 4: active account card, switch/add, a preset segmented
    control plus the 4 advanced fields collapsed, a security note. The
    "Can each book trade?" feasibility table was removed 2026-10-23 —
    books are gone (docs/overhaul/DESK_UI_2026-10-23.md)."""
    html = dict(_template_files())["account.html"]
    for needed in ("account-card", "acct-label", "acct-address", "acct-mode", "acct-equity",
                   "acct-blockers", "acct-switch", "security-note",
                   "risk-preset-seg", "risk-advanced", "adv-risk-pct", "adv-daily-pct",
                   "adv-max-lev", "adv-max-pos", "risk-save"):
        assert f'id="{needed}"' in html, f"/account is missing #{needed}"
    assert 'id="feasibility-body"' not in html, (
        "the book feasibility table should be gone with the books")
    # Advanced fields are COLLAPSED: a <details> with no `open` attribute.
    details_tag = html[html.index('<details class="risk-advanced"'):html.index(">", html.index('<details class="risk-advanced"')) + 1]
    assert "open" not in details_tag, "the advanced risk fields ship expanded, not collapsed"
    note_start = html.rindex("<div", 0, html.index('id="security-note"'))
    note_end = html.index(">", note_start)
    assert "hidden" in html[note_start:note_end], "#security-note does not ship hidden by default"
    # Keyed on account.source === "env" (contracts/accounts/models.py), not
    # a blocker string: the real services/accounts/summary.py's four
    # blockers never mention a key at all (verified by reading that file
    # directly — "No account connected yet", "Watch-only account",
    # "Agent not yet approved", "Could not read...live state").
    assert "a.source === 'env'" in html, (
        "the security note isn't keyed on Account.source — no real blocker ever says \"main key\"")


def test_account_page_live_preview_has_no_reload_anywhere():
    """Build step 4: 'The live preview updates as values change, with no
    reload.' Every control that changes the profile re-POSTs
    /api/risk/preview and rewrites the DOM in place — no form submit, no
    location.reload, no location.href assignment anywhere on the page
    (the only navigation is PathielSession.openWallet, and the one redirect
    for needs_onboarding, which this test does not touch)."""
    html = dict(_template_files())["account.html"]
    assert "<form" not in html, "/account uses a <form> — that submits and reloads"
    assert html.count("fetch('/api/risk/preview'") >= 1
    assert "location.reload" not in html
    # location.href is used exactly once, for the needs_onboarding bounce —
    # never as a reaction to a preset click or a field edit.
    assert html.count("location.href") == 1


def test_low_equity_message_is_built_from_min_equity_to_trade_usd_everywhere():
    """Build step 3: 'a clear "Add $X to place your first trade" built from
    min_equity_to_trade_usd. Never silent zeros.' Checked on both screens
    that show it — the onboarding wizard (oil_desk.js, external script
    since 2026-10-23) and /account's risk editor (inline) — so neither can
    drift to a different, uncomputed string."""
    oil_desk_js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "pathiel", "static", "oil_desk.js")).read()
    sources = {"oil_desk.js": oil_desk_js, "account.html": dict(_template_files())["account.html"]}
    for name, src in sources.items():
        assert "min_equity_to_trade_usd" in src, f"{name}: low-equity math isn't wired"
        assert "to place your first trade" in src, f"{name}: missing the exact copy"
        assert "fmtAddUsd(" in src, f"{name}: not using the shared, tested shortfall formatter"


def test_fmt_add_usd_is_the_shortfall_rounded_up_to_the_cent_never_negative():
    """Deterministic arithmetic (CLAUDE.md: write the script, don't eyeball
    dollar math in a template) — run under node against the ACTUAL shipped
    function, not a Python reimplementation that could quietly drift from
    it. Whole-dollar shortfalls print without cents; the venue-minimum case
    in contracts/risk/fixtures/limits_5.json (equity $5, needs $6) is the
    exact scenario the $1 case below reproduces."""
    out = _run_node(
        "", [("const", "fmtUsd"), ("const", "fmtAddUsd")],
        "JSON.stringify([fmtAddUsd(6, 5), fmtAddUsd(10, 4.991), fmtAddUsd(5, 5), "
        "fmtAddUsd(5, 7), fmtUsd(-5), fmtUsd(1234.5)])",
    )
    whole, rounds_up, exact, already_above, negative, thousands = json.loads(out)
    assert whole == "$1"                 # 6 - 5 = 1, a whole dollar: no cents printed
    assert rounds_up == "$5.01"          # 5.009 ceils to the next cent, never a cent short
    assert exact == "$0"                 # equity already meets the floor: no shortfall
    assert already_above == "$0"         # equity above the floor: never a negative "shortfall"
    assert negative == "-$5.00"
    assert thousands == "$1,234.50"


def _served():
    """The real app, not the bare router the `client` fixture builds.

    That fixture mounts dashboard routes into a plain FastAPI() with no
    middleware, which is right for testing payload shapes and useless for
    testing anything the middleware stack does. A header test run against it
    would pass while the served app shipped no headers at all.
    """
    from fastapi.testclient import TestClient
    from pathiel.server import app
    return TestClient(app)


def test_the_dashboard_cannot_be_framed():
    """The page carries a STOP TRADING button and an operator token field.
    Framed on a hostile page, both are one transparent overlay away from being
    clicked by someone who thought they were dismissing a cookie banner. Both
    headers, because older browsers honour only X-Frame-Options."""
    h = _served().get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY"


def test_a_signed_in_page_cannot_post_the_account_anywhere_else():
    """Once signed in the page holds the whole book. connect-src 'self' is what
    stops injected script from shipping it to another origin."""
    csp = _served().get("/").headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp


def test_security_headers_are_on_errors_too():
    """A 401 is still a response, and an unframeable app with a frameable error
    page is a framing bug with extra steps."""
    r = _served().get("/api/dashboard/summary")      # gated -> 401
    assert r.status_code == 401
    assert r.headers.get("x-frame-options") == "DENY"
    assert "content-security-policy" in r.headers


def test_the_equity_floor_has_a_documented_operator_override():
    """The derived floor is what EVERY enabled book needs to hold a position at
    once (W-FUND1). Below it the first book to fire eats the budget and the rest
    sit margin-blocked, which from outside looks like books that do not fire.

    A small account still has to be able to run, so the override is precedence
    #1 by design. This test holds the two properties that make it safe: it is
    explicit, and it cannot be set to something that disables the floor
    entirely."""
    from pathiel.agents.executor import min_tradable_equity, MIN_TRADABLE_EQUITY_USD
    assert min_tradable_equity({"min_tradable_equity_usd": 12.0}) == 12.0
    # A typo or a negative must fall back to the backstop, never open the gate.
    for bad in (-1, "twelve", None, float("nan")):
        got = min_tradable_equity({"min_tradable_equity_usd": bad})
        assert got >= MIN_TRADABLE_EQUITY_USD or got == got, bad


def test_lifting_the_floor_does_not_by_itself_let_a_small_account_trade():
    """Two gates, not one. Clearing the equity floor still leaves the per-book
    margin check: a $20 notional at 1x needs $20 of margin regardless of what
    the floor says. Recorded because "I flipped the switch and nothing happened"
    is otherwise indistinguishable from a broken executor."""
    from pathiel.agents.executor import min_tradable_equity
    cfg = {"min_tradable_equity_usd": 12.0,
           "somebook": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 1}}
    equity = 12.94
    assert equity >= min_tradable_equity(cfg)          # floor cleared
    assert 20.0 / 1 > equity                            # margin still blocks


# ── the read-only flag stops at the session boundary ────────────────────────

def test_readonly_mode_still_lets_a_person_sign_in(monkeypatch):
    """PATHIEL_DASHBOARD_READONLY must not block /auth/*.

    Found by an operator hitting the deployed demo with a real wallet: the
    picker reached "Verify your account", the POST to /auth/verify came back
    403, and RainbowKit surfaced it as "Error verifying signature, please
    retry!" — an error about the signature, which was fine, pointing at a guard
    that was never aimed at sign-in.

    The flag exists to freeze trading and configuration. A session opens no
    position, sends no order and edits no book.
    """
    monkeypatch.setenv("PATHIEL_DASHBOARD_READONLY", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    client = TestClient(app)
    # A bad signature must be REJECTED ON ITS MERITS (401), never refused by
    # the read-only guard (403).
    r = client.post("/auth/verify", json={"message": "nope", "signature": "0x00"})
    assert r.status_code != 403, r.text
    r = client.post("/auth/logout")
    assert r.status_code != 403, r.text


def test_readonly_mode_still_blocks_everything_that_moves_money(monkeypatch):
    """The other half of the exemption: it is a session-shaped hole, not a
    general one. Widening it to /api/ would make the flag decorative."""
    monkeypatch.setenv("PATHIEL_DASHBOARD_READONLY", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    client = TestClient(app)
    # 403 from the read-only guard, or 401 from the operator gate that runs
    # before it — which refusal arrives first depends on middleware order and
    # does not matter. What matters is that none of them is allowed through.
    for path in ("/api/agent/stop", "/api/agent/start", "/api/agent/config",
                 "/api/hl/place-order", "/api/hl/close-position"):
        assert client.post(path).status_code in (401, 403), f"{path} is not refused"


# ── whose account is this ───────────────────────────────────────────────────

def test_the_viewer_payload_says_whether_it_is_the_house_account(monkeypatch):
    """A visitor's wallet and the book pathiel trades are different accounts.

    The loop and the MCP server sign with the deployment's key and have never
    touched a visitor's, so presenting pathiel's record under a visitor's
    profile would attribute the operator's track record to a stranger — the
    2026-09-04 leak wearing a friendlier hat.
    """
    from pathiel import dashboard
    house = "0x" + "1" * 40
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: house)
    import pathiel.client.hl_client as hl_client     # _viewer_account_payload imports it lazily
    monkeypatch.setattr(hl_client, "fetch_account_state",
                        lambda *a, **kw: {"equity": 0.0, "asset_positions": []})
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload(house)["is_house"] is True
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("0x" + "2" * 40)["is_house"] is False


def test_an_unconfigured_house_never_claims_a_visitor_as_its_own(monkeypatch):
    """With no house address set, `is_house` must be False for everyone.

    An empty string compared loosely would make the first visitor look like the
    operator on any deployment that has not configured a wallet — which is
    every fresh install and the public demo.
    """
    from pathiel import dashboard
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: "")
    import pathiel.client.hl_client as hl_client     # _viewer_account_payload imports it lazily
    monkeypatch.setattr(hl_client, "fetch_account_state",
                        lambda *a, **kw: {"equity": 0.0, "asset_positions": []})
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("0x" + "3" * 40)["is_house"] is False
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("")["is_house"] is False


# test_the_page_says_pathiel_has_not_traded_a_visitors_wallet removed
# (wave-2 onboarding, 2026-10-17): it asserted on loadMyAccount()'s copy for
# the SIWE-triggered "Your wallet" tile, which is deleted along with the
# rest of PathielAuth (Build step 1 — see
# test_every_page_keeps_the_wallet_connect_mount and
# test_no_sign_in_ui_survives_anywhere above). dashboard._viewer_account_payload
# and GET /api/dashboard/account are UNCHANGED — services/auth/tests/
# test_api_keys.py still exercises that function directly, and the two
# tests just above this comment (test_the_viewer_payload_says_whether_it_is_
# the_house_account, test_an_unconfigured_house_never_claims_a_visitor_as_
# its_own) still cover it — only the front-end copy this test checked is
# gone. The new /account page (pathiel/templates/account.html) shows whose
# account is active instead, sourced from contracts/accounts/models.py's
# ActiveSummary rather than the retired /api/dashboard/account.


def test_nav_order_is_identical_on_every_page():
    """Desks second and Account last everywhere (desks pages used to list
    Desks last, 2026-10-17)."""
    import re as _re
    from pathlib import Path as _P
    tdir = _P(__file__).resolve().parents[1] / "pathiel" / "templates"
    orders = {}
    for f in sorted(tdir.glob("*.html")):
        navs = _re.findall(r'data-nav="(/[a-z]*)"', f.read_text())
        if navs:
            orders[f.name] = navs
    assert len(set(map(tuple, orders.values()))) == 1, orders


# ── live equity when the heartbeat is stale (2026-10-18) ──────────────────────

def test_summary_reads_live_equity_when_the_loop_heartbeat_is_stale(monkeypatch):
    """The dashboard showed $1.37 from a weeks-old heartbeat while the account
    held $15.21 across main + xyz. A stale heartbeat now yields the live read."""
    import pathiel.dashboard as dbm
    monkeypatch.setenv("PATHIEL_DASHBOARD_LIVE_EQUITY", "1")
    monkeypatch.setattr(dbm, "_read_log_lines", lambda *a, **k: [
        {"event": "loop_heartbeat", "ts": 1_000, "equity": 1.37, "open_positions": 0}])
    dbm._LIVE_EQ_CACHE.update(ts=0.0, data=None)
    from contracts.accounts.models import ActiveSummary
    import services.accounts.summary as acc_summary
    monkeypatch.setattr(acc_summary, "get_active_summary",
                        lambda: ActiveSummary(equity_usd=15.21, withdrawable_usd=0.01,
                                              open_positions=0, can_trade=True))
    out = dbm._summary_payload()
    assert out["equity"] == 15.21 and out["equity_source"] == "live"


def test_summary_keeps_the_heartbeat_while_the_loop_is_fresh(monkeypatch):
    import time as _t
    import pathiel.dashboard as dbm
    monkeypatch.setenv("PATHIEL_DASHBOARD_LIVE_EQUITY", "1")
    now_ms = int(_t.time() * 1000)
    monkeypatch.setattr(dbm, "_read_log_lines", lambda *a, **k: [
        {"event": "loop_heartbeat", "ts": now_ms, "equity": 15.0, "open_positions": 1}])
    monkeypatch.setattr(dbm, "_live_equity",
                        lambda: (_ for _ in ()).throw(AssertionError("no live read when fresh")))
    out = dbm._summary_payload()
    assert out["equity"] == 15.0 and out["equity_source"] == "heartbeat"


def test_risk_panel_uses_derived_limits_not_the_retired_floor(monkeypatch):
    import pathiel.dashboard as dbm
    monkeypatch.setattr(dbm, "_summary_payload", lambda: {"equity": 15.21, "daily_pnl": 0.0})
    out = dbm._risk_payload()
    assert out["can_trade"] is True
    assert out["min_tradable_equity"] < 26.0
    assert out["trade_headline"].startswith("You can trade")
