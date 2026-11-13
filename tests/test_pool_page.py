"""The public research pool scoreboard page: docs/scoreboard/index.html,
scoreboard.js, scoreboard.css, docs/.nojekyll.

Gate lane: offline, deterministic, under 2 seconds (one node subprocess at
most). This unit never writes docs/scoreboard/scoreboard.json (another unit
does); it builds and tests against the golden example
contracts/pool/fixtures/scoreboard.json.

Read first: docs/overhaul/RESEARCH_POOL_2026-11-12.md ("What the scoreboard
publishes", "Independent trades", "The 24 hour delay", "Privacy", "Design
calls" D3/D4/D7), contracts/pool/__init__.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict

import pytest

from contracts import pool

ROOT = Path(__file__).resolve().parents[1]
SCOREBOARD_DIR = ROOT / "docs" / "scoreboard"
INDEX = SCOREBOARD_DIR / "index.html"
JS = SCOREBOARD_DIR / "scoreboard.js"
CSS = SCOREBOARD_DIR / "scoreboard.css"
NOJEKYLL = ROOT / "docs" / ".nojekyll"
FIXTURE = ROOT / "contracts" / "pool" / "fixtures" / "scoreboard.json"
NODE = shutil.which("node")

MAX_TOTAL_BYTES = 40 * 1024

ALLOWED_URL_PREFIXES = (
    "https://github.com/Julian-dev28/pathiel",
    "https://app.hyperliquid.xyz/explorer/address/",
)

# A dot in front is what tells an actual DOM sink ("el.innerHTML = ...") apart
# from this file's own comments saying the page never uses one.
FORBIDDEN_SINKS = (
    ".innerHTML", ".outerHTML", "insertAdjacentHTML(", "document.write(",
    "eval(", "new Function(",
)

# Every pool.MemberRow / SliceRow / Counts key the page shows, per the brief.
SHOWN_KEYS = (
    "address", "short", "kind", "since", "closed_trades", "n", "win_rate",
    "win_lo", "win_hi", "mean_r", "r_lo", "r_hi", "p", "label", "luck_median",
    "luck_n", "key", "n_trades", "members", "graded_trades",
    "independent_trades", "slices_at_min_n", "slices", "left", "house",
)

# Keys the pool schema deliberately never carries (contracts.pool.
# FORBIDDEN_KEY_PARTS); the page must not read them either.
BANNED_SUBSTRINGS = ("usd", "equity", "px", "position", "leverage")

_URL_RE = re.compile(r"https?://[^\s\"'()<>]+")
_TAG_RE = re.compile(r"<[^>]+>")
_STR_LIT_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _texts() -> Dict[str, str]:
    return {"index.html": _read(INDEX), "scoreboard.js": _read(JS), "scoreboard.css": _read(CSS)}


# ── the files exist and are wired relatively ────────────────────────────────

def test_the_three_files_and_nojekyll_exist():
    assert INDEX.is_file()
    assert JS.is_file()
    assert CSS.is_file()
    assert NOJEKYLL.is_file()
    assert NOJEKYLL.read_text(encoding="utf-8") == "", "docs/.nojekyll must be empty"


def test_index_references_css_and_js_relatively():
    html = _read(INDEX)
    assert 'href="scoreboard.css"' in html
    assert 'src="scoreboard.js"' in html
    # the only .css/.js references in the file are the two relative ones
    assert re.findall(r'href="([^"]*\.css)"', html) == ["scoreboard.css"]
    assert re.findall(r'src="([^"]*\.js)"', html) == ["scoreboard.js"]


def test_js_fetches_the_scoreboard_json_relatively():
    js = _read(JS)
    assert "fetch('scoreboard.json', {cache: 'no-store'})" in js


# ── required static copy (shows even when the JSON fails to load) ──────────

REQUIRED_SENTENCES = (
    "at least 24 hours late",
    "Not live positions, not advice",
    "No edge is demonstrated until a row with 10 or more independent trades says EDGE",
    "Rows are ordered by number of graded trades",
    "/pathiel:share",
    "Hyperliquid",
    "members",
)


def test_required_static_sentences_present():
    html = _read(INDEX)
    for sentence in REQUIRED_SENTENCES:
        assert sentence in html, f"missing from index.html: {sentence!r}"


def test_independent_trade_sentence_is_tied_to_the_cluster_span_constant():
    """The sentence names the span in hours, computed from the contract's
    own constants, not hardcoded independently of them: a change to
    CLUSTER_SPAN_MS (or to HOUR_MS) fails this test until index.html is
    updated to match."""
    hours = pool.CLUSTER_SPAN_MS // pool.HOUR_MS
    assert hours == 24
    sentence = (
        "Pool rows count independent trades: trades on the same coin and "
        f"side entered within {hours} hours of the first one count once."
    )
    assert sentence in _read(INDEX)


def test_join_and_privacy_links_present():
    html = _read(INDEX)
    assert 'href="https://github.com/Julian-dev28/pathiel"' in html
    assert 'href="https://github.com/Julian-dev28/pathiel/blob/main/docs/PRIVACY.md"' in html


def test_no_sort_or_rank_controls():
    """No sorting or ranking controls by result: rows stay in file order."""
    texts = _texts()
    for name, text in texts.items():
        low = text.lower()
        for word in ("sort by", "rank by", "<select", "onchange"):
            assert word not in low, f"{word!r} found in {name}"


# ── size, safety, allowed urls ──────────────────────────────────────────────

def test_total_size_is_under_40kb():
    total = INDEX.stat().st_size + JS.stat().st_size + CSS.stat().st_size
    assert total < MAX_TOTAL_BYTES, f"{total} bytes"


def test_no_forbidden_dom_sinks():
    for name, text in _texts().items():
        for sink in FORBIDDEN_SINKS:
            assert sink not in text, f"forbidden sink {sink!r} in {name}"


def test_every_absolute_url_uses_an_allowed_prefix():
    for name, text in _texts().items():
        for url in _URL_RE.findall(text):
            assert url.startswith(ALLOWED_URL_PREFIXES), f"disallowed url {url!r} in {name}"


_PROTOCOL_RELATIVE_RE = re.compile(r"""(?:["'(]|url\()\s*//[^\s"')]+""")


def test_no_protocol_relative_urls():
    """A "//host/path" reference would dodge the https?:// prefix check above
    while still reaching an outside host. None should exist here."""
    for name, text in _texts().items():
        assert not _PROTOCOL_RELATIVE_RE.search(text), name


def test_app_mount_id_matches_between_html_and_js():
    """render()/showError() look up document.getElementById('app'); index.html
    must carry that exact id, or every render silently no-ops (render()
    guards with `if (!app) return`, so a drifted id would fail open as a
    permanently blank page, not a crash the gate would catch any other way)."""
    assert 'id="app"' in _read(INDEX)
    assert "getElementById('app')" in _read(JS)


def test_no_external_resource_tags():
    html = _read(INDEX)
    for tag in ("<link rel=\"preconnect\"", "cdn.", "googleapis.com", "fonts.",
                "<img", "<iframe", "<video", "<audio", "analytics"):
        assert tag not in html.lower(), tag


def test_every_shown_contract_key_is_named_in_the_js():
    js = _read(JS)
    missing = [k for k in SHOWN_KEYS if k not in js]
    assert not missing, missing


def test_js_reads_no_key_outside_the_schema():
    js = _read(JS).lower()
    hits = [b for b in BANNED_SUBSTRINGS if b in js]
    assert not hits, hits


# ── copy lint: visible HTML text and JS string literals ────────────────────

def _visible_text(html: str) -> str:
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = _TAG_RE.sub(" ", html)
    return (text.replace("&hellip;", "...").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " "))


def _js_string_literals(js: str) -> str:
    # Strip line comments first so a comment's prose (which may contain an
    # apostrophe-free quote-like example) is not read as a literal.
    no_comments = re.sub(r"//.*", "", js)
    no_comments = re.sub(r"/\*.*?\*/", " ", no_comments, flags=re.DOTALL)
    return " ".join(m.group(0) for m in _STR_LIT_RE.finditer(no_comments))


def test_copy_lint_on_visible_html_text(copy_lint):
    text = _visible_text(_read(INDEX))
    assert copy_lint.claim_hits(text) == []
    assert copy_lint.style_hits(text) == []


def test_copy_lint_on_js_string_literals(copy_lint):
    literals = _js_string_literals(_read(JS))
    assert copy_lint.claim_hits(literals) == []
    assert copy_lint.style_hits(literals) == []


# ── the folder ships only what it should (it reaches the Docker context) ───

def test_scoreboard_dir_holds_only_the_allowed_files():
    allowed = {"index.html", "scoreboard.js", "scoreboard.css", "scoreboard.json"}
    present = {p.name for p in SCOREBOARD_DIR.iterdir() if p.is_file()}
    assert present <= allowed, present - allowed


# ── the fixture is valid input (not a stale idea of the schema) ────────────

def test_fixture_is_a_valid_scoreboard():
    fixture = json.loads(_read(FIXTURE))
    assert pool.validate_scoreboard(fixture) == []


# ── node: pure functions and render() against a minimal, hand-written DOM ──
#
# No jsdom, no new dependency: FakeElement/FakeDocument below implement only
# what scoreboard.js's render() calls (createElement, getElementById,
# appendChild, removeChild, firstChild, textContent get/set, setAttribute).
# Skipped, not failed, when node is not installed (tests/test_call_history_js.
# py's own pattern).

_NODE_SCRIPT = r"""
'use strict';
const m = require(JS_PATH);
const fixture = require(FIXTURE_PATH);
const emptyDoc = require(EMPTY_PATH);
const LABELS = LABELS_JSON;
const out = {};

function FakeElement(tag) {
  this.tagName = String(tag).toUpperCase();
  this.children = [];
  this.attributes = {};
  this._text = '';
}
Object.defineProperty(FakeElement.prototype, 'textContent', {
  get: function () {
    if (this.children.length === 0) return this._text;
    return this.children.map(function (c) { return c.textContent; }).join('');
  },
  set: function (v) { this.children = []; this._text = String(v); },
});
Object.defineProperty(FakeElement.prototype, 'firstChild', {
  get: function () { return this.children.length ? this.children[0] : null; },
});
FakeElement.prototype.appendChild = function (c) { this.children.push(c); return c; };
FakeElement.prototype.removeChild = function (c) {
  const i = this.children.indexOf(c);
  if (i >= 0) this.children.splice(i, 1);
  return c;
};
FakeElement.prototype.setAttribute = function (n, v) { this.attributes[n] = String(v); };
FakeElement.prototype.getAttribute = function (n) {
  return Object.prototype.hasOwnProperty.call(this.attributes, n) ? this.attributes[n] : null;
};

function FakeDocument() { this._byId = {}; }
FakeDocument.prototype.createElement = function (tag) { return new FakeElement(tag); };
FakeDocument.prototype.getElementById = function (id) { return this._byId[id] || null; };
FakeDocument.prototype.register = function (id, el) {
  el.attributes.id = id;
  this._byId[id] = el;
};

function findAll(el, tag, found) {
  found = found || [];
  if (el.tagName === tag) found.push(el);
  for (const c of el.children) findAll(c, tag, found);
  return found;
}

function newApp() {
  const doc = new FakeDocument();
  const app = doc.createElement('div');
  doc.register('app', app);
  return {doc: doc, app: app};
}

// 1. memberCells: one row of cells per member, in file order, each showing
//    its short address.
out.memberShorts = fixture.members.map(function (r) { return m.memberCells(r).short; });
out.fixtureShorts = fixture.members.map(function (r) { return r.short; });

// 2. null stats render 'n/a' (the fixture has a member with n 0).
const zeroRow = fixture.members.filter(function (r) { return r.n === 0; })[0];
const zc = m.memberCells(zeroRow);
out.zeroRowCells = [zc.winRate, zc.winRange, zc.meanR, zc.rRange, zc.p, zc.luck];

// 3. safeExplorer: a valid row, a mismatched explorer field, an uppercase
//    address, a wrong-length address, and a 'javascript:' explorer value.
const validRow = fixture.members[0];
out.safeExplorerValid = m.safeExplorer(validRow);
out.safeExplorerWrongExplorer = m.safeExplorer(
  Object.assign({}, validRow, {explorer: 'https://example.com/elsewhere'}));
out.safeExplorerUppercase = m.safeExplorer(Object.assign({}, validRow, {
  address: validRow.address.toUpperCase(),
  explorer: validRow.explorer.toUpperCase(),
}));
out.safeExplorerWrongLength = m.safeExplorer(Object.assign({}, validRow, {
  address: '0xabc',
  explorer: 'https://app.hyperliquid.xyz/explorer/address/0xabc',
}));
out.safeExplorerJavascript = m.safeExplorer(
  Object.assign({}, validRow, {explorer: 'javascript:alert(1)'}));

// 4. the house row carries the badge; nobody else does.
out.memberCellsHouseFlags = fixture.members.map(function (r) { return m.memberCells(r).house; });

// 5. countTiles shows graded_trades and 'K of M' slices.
const tiles = m.countTiles(fixture);
out.hasGradedTile = tiles.some(function (t) {
  return t.value === String(fixture.counts.graded_trades);
});
out.hasKofMTile = tiles.some(function (t) {
  return t.value === fixture.counts.slices_at_min_n + ' of ' + fixture.counts.slices;
});

// 6. render() on the fixture: row counts, column parity and the house
//    badge in the DOM.
{
  const {doc, app} = newApp();
  m.render(fixture, doc);
  const tables = findAll(app, 'TABLE');
  const tbodies = findAll(app, 'TBODY');
  const theads = findAll(app, 'THEAD');
  const poolRows = tbodies[0].children;
  const memberRows = tbodies[1].children;
  out.renderedPoolRowCount = poolRows.length;
  out.renderedMemberRowCount = memberRows.length;
  out.renderedTableCount = tables.length;
  // every row has exactly as many cells as its table has header columns
  out.poolHeaderCount = findAll(theads[0], 'TH').length;
  out.poolRowCellCounts = Array.prototype.map.call(poolRows, function (tr) {
    return findAll(tr, 'TD').length;
  });
  out.memberHeaderCount = findAll(theads[1], 'TH').length;
  out.memberRowCellCounts = Array.prototype.map.call(memberRows, function (tr) {
    return findAll(tr, 'TD').length;
  });
  const firstCellLinks = findAll(memberRows[0], 'A');
  out.renderedFirstMemberShort = firstCellLinks.length
    ? firstCellLinks[0].textContent
    : findAll(memberRows[0], 'SPAN')[0].textContent;
  out.renderedHouseBadgeCount = findAll(app, 'SPAN').filter(function (s) {
    return s.attributes['class'] === 'badge' && s.textContent === 'house';
  }).length;
  out.hasCaptions = findAll(app, 'CAPTION').length;
  out.thScopes = findAll(app, 'TH').every(function (th) { return th.attributes.scope === 'col'; });
}

// 6b. label chips: one class per contract label, a plain chip otherwise,
//     and every label cell in the DOM is a chip holding the label's text.
out.labelClasses = LABELS.map(function (l) { return m.labelClass(l); });
out.unknownLabelClass = m.labelClass('SOMETHING NEW');
{
  const {doc, app} = newApp();
  m.render(fixture, doc);
  const chips = findAll(app, 'SPAN').filter(function (s) {
    return /^chip( |$)/.test(s.attributes['class'] || '');
  });
  out.chipTexts = chips.map(function (c) { return c.textContent; });
  out.fixtureLabels = fixture.pool.map(function (r) { return r.label; })
    .concat(fixture.members.map(function (r) { return r.label; }));
}

// 7. pool.empty_scoreboard(now) renders the empty state without throwing.
{
  const {doc, app} = newApp();
  let threw = false;
  try { m.render(emptyDoc, doc); } catch (e) { threw = true; out.emptyError = String(e); }
  out.emptyThrew = threw;
  out.emptyText = app.textContent;
}

// 8. a doc with schema 2 yields the "older than the data" message.
{
  const {doc, app} = newApp();
  m.render(Object.assign({}, fixture, {schema: 2}), doc);
  out.staleText = app.textContent;
}

process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_node_pure_functions_and_render(tmp_path):
    fixture = json.loads(_read(FIXTURE))
    empty_doc = pool.empty_scoreboard(1_700_000_000_000)
    empty_path = tmp_path / "empty_scoreboard.json"
    empty_path.write_text(json.dumps(empty_doc), encoding="utf-8")

    script = (
        _NODE_SCRIPT
        .replace("JS_PATH", json.dumps(str(JS)))
        .replace("FIXTURE_PATH", json.dumps(str(FIXTURE)))
        .replace("EMPTY_PATH", json.dumps(str(empty_path)))
        .replace("LABELS_JSON", json.dumps(list(pool.LABELS)))
    )
    script_path = tmp_path / "run_pool_page_test.js"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run([NODE, str(script_path)], capture_output=True, text=True,
                           timeout=20, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    # 1. file order, short address shown for every member.
    assert out["memberShorts"] == out["fixtureShorts"]

    # 2. null stats render 'n/a'.
    assert out["zeroRowCells"] == ["n/a"] * 6

    # 3. safeExplorer.
    assert out["safeExplorerValid"] == fixture["members"][0]["explorer"]
    assert out["safeExplorerWrongExplorer"] is None
    assert out["safeExplorerUppercase"] is None
    assert out["safeExplorerWrongLength"] is None
    assert out["safeExplorerJavascript"] is None

    # 4. exactly one house badge, on the house row.
    house_flags = out["memberCellsHouseFlags"]
    fixture_kinds = [r["kind"] for r in fixture["members"]]
    assert house_flags == [k == "house" for k in fixture_kinds]
    assert house_flags.count(True) == 1

    # 5. count tiles.
    assert out["hasGradedTile"] is True
    assert out["hasKofMTile"] is True

    # 6. render() on the fixture: row counts, column parity, table semantics.
    assert out["renderedPoolRowCount"] == len(fixture["pool"])
    assert out["renderedMemberRowCount"] == len(fixture["members"])
    assert out["renderedTableCount"] == 2
    assert out["poolRowCellCounts"] == [out["poolHeaderCount"]] * len(fixture["pool"])
    assert out["memberRowCellCounts"] == [out["memberHeaderCount"]] * len(fixture["members"])
    assert out["renderedFirstMemberShort"] == fixture["members"][0]["short"]
    assert out["renderedHouseBadgeCount"] == 1
    assert out["hasCaptions"] == 2
    assert out["thScopes"] is True

    # 6b. label chips: distinct per label, all prefixed 'chip', in row order.
    assert len(set(out["labelClasses"])) == len(pool.LABELS)
    assert all(c.startswith("chip chip-") for c in out["labelClasses"])
    assert out["unknownLabelClass"] == "chip"
    assert out["chipTexts"] == out["fixtureLabels"]

    # 7. empty scoreboard: no throw, empty state shown.
    assert out["emptyThrew"] is False, out.get("emptyError")
    assert "No members yet." in out["emptyText"]

    # 8. schema mismatch: stale message, nothing else material rendered.
    assert "older than the data" in out["staleText"]


# ── integration: the page also renders the grade unit's REAL output, not
# just this unit's own golden fixture ────────────────────────────────────────

_REAL_BOARD_NODE_SCRIPT = r"""
'use strict';
const m = require(JS_PATH);
const doc = require(BOARD_PATH);

function FakeElement(tag) {
  this.tagName = String(tag).toUpperCase();
  this.children = [];
  this.attributes = {};
  this._text = '';
}
Object.defineProperty(FakeElement.prototype, 'textContent', {
  get: function () {
    if (this.children.length === 0) return this._text;
    return this.children.map(function (c) { return c.textContent; }).join('');
  },
  set: function (v) { this.children = []; this._text = String(v); },
});
FakeElement.prototype.appendChild = function (c) { this.children.push(c); return c; };
FakeElement.prototype.removeChild = function (c) {
  const i = this.children.indexOf(c);
  if (i >= 0) this.children.splice(i, 1);
  return c;
};
FakeElement.prototype.setAttribute = function (n, v) { this.attributes[n] = String(v); };
FakeElement.prototype.getAttribute = function (n) {
  return Object.prototype.hasOwnProperty.call(this.attributes, n) ? this.attributes[n] : null;
};

function FakeDocument() { this._byId = {}; }
FakeDocument.prototype.createElement = function (tag) { return new FakeElement(tag); };
FakeDocument.prototype.getElementById = function (id) { return this._byId[id] || null; };
FakeDocument.prototype.register = function (id, el) {
  el.attributes.id = id;
  this._byId[id] = el;
};

function findAll(el, tag, found) {
  found = found || [];
  if (el.tagName === tag) found.push(el);
  for (const c of el.children) findAll(c, tag, found);
  return found;
}

const fakeDoc = new FakeDocument();
const app = fakeDoc.createElement('div');
fakeDoc.register('app', app);

const out = {};
let threw = false;
try {
  m.render(doc, fakeDoc);
} catch (e) {
  threw = true;
  out.error = String(e);
}
out.threw = threw;
out.tableCount = findAll(app, 'TABLE').length;
const tbodies = findAll(app, 'TBODY');
out.poolRowCount = tbodies.length ? tbodies[0].children.length : 0;
out.memberRowCount = tbodies.length > 1 ? tbodies[1].children.length : 0;

process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_renders_the_real_committed_scoreboard_not_just_the_fixture(tmp_path):
    """The grade unit writes docs/scoreboard/scoreboard.json independently of
    this unit's own tests; this is the one check that the two actually fit
    together: the committed file validates against the contract and this
    unit's render() draws it with no throw, the right number of tables, and
    one row per member."""
    board_path = SCOREBOARD_DIR / pool.SCOREBOARD_JSON
    assert board_path.is_file(), "docs/scoreboard/scoreboard.json is not committed"
    doc = json.loads(board_path.read_text(encoding="ascii"))
    assert pool.validate_scoreboard(doc) == []

    script = (_REAL_BOARD_NODE_SCRIPT
              .replace("JS_PATH", json.dumps(str(JS)))
              .replace("BOARD_PATH", json.dumps(str(board_path))))
    script_path = tmp_path / "run_real_board_test.js"
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.run([NODE, str(script_path)], capture_output=True, text=True,
                          timeout=20, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    assert out["threw"] is False, out.get("error")
    assert out["tableCount"] == 2
    assert out["poolRowCount"] == len(doc["pool"])
    assert out["memberRowCount"] == len(doc["members"])
