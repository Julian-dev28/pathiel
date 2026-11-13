/* scoreboard.js: pathiel research pool public scoreboard.
 *
 * Static page, no build step. Fetches scoreboard.json (produced offline by
 * services/pool, schema in contracts/pool) and renders it with createElement
 * and textContent only: no innerHTML, no template strings inserted as markup.
 *
 * Every function below is pure except boot() and showError(), which touch
 * the real fetch() and the real document. tests/test_pool_page.py runs this
 * same file under node (module.exports below), the way
 * tests/test_call_history_js.py runs pathiel/static/call_history.js, so the
 * formatting and the rendering are tested deterministically, not eyeballed.
 *
 * Contract: contracts/pool/__init__.py (Scoreboard, Counts, SliceRow,
 * MemberRow, CLUSTER_SPAN_MS, validate_scoreboard, EXPLORER_URL).
 */
(function (root) {
  'use strict';

  var ADDRESS_RE = /^0x[0-9a-f]{40}$/;
  var EXPLORER_PREFIX = 'https://app.hyperliquid.xyz/explorer/address/';

  // ── formatting: every one returns 'n/a' for a null or missing stat ───────

  function isNum(x) {
    return typeof x === 'number' && isFinite(x);
  }

  function fmtPct(x) {
    if (!isNum(x)) return 'n/a';
    return (x * 100).toFixed(1) + '%';
  }

  function fmtR(x) {
    if (!isNum(x)) return 'n/a';
    return x.toFixed(2) + ' R';
  }

  function fmtRange(lo, hi, kind) {
    if (!isNum(lo) || !isNum(hi)) return 'n/a';
    var fmt = kind === 'r' ? fmtR : fmtPct;
    return '(' + fmt(lo) + ' to ' + fmt(hi) + ')';
  }

  function fmtP(p) {
    if (!isNum(p)) return 'n/a';
    return p.toFixed(3);
  }

  function fmtLuck(median, n) {
    if (!isNum(median) || !n) return 'n/a';
    return median.toFixed(1) + ' (n=' + n + ')';
  }

  // row.explorer is a string the JSON carries, but it is never trusted
  // blindly: the href this returns is always rebuilt from row.address, and
  // is returned only when that rebuilt value is exactly what row.explorer
  // says. A row whose address is not 0x + 40 lowercase hex, or whose
  // explorer field does not match the address (a bad write, a tampered
  // file, a 'javascript:' value, an uppercase or wrong-length address),
  // gets no link at all.
  function safeExplorer(row) {
    if (!row || typeof row.address !== 'string' || !ADDRESS_RE.test(row.address)) return null;
    var want = EXPLORER_PREFIX + row.address;
    if (row.explorer !== want) return null;
    return want;
  }

  // One cell set per pool.MemberRow, in the order the member table shows:
  // address (link + house badge), since, closed, graded n, win rate and its
  // range, mean R and its range, p, label, luck.
  function memberCells(row) {
    return {
      short: row.short,
      href: safeExplorer(row),
      house: row.kind === 'house',
      since: row.since,
      closed: String(row.closed_trades),
      n: String(row.n),
      winRate: fmtPct(row.win_rate),
      winRange: fmtRange(row.win_lo, row.win_hi, 'pct'),
      meanR: fmtR(row.mean_r),
      rRange: fmtRange(row.r_lo, row.r_hi, 'r'),
      p: fmtP(row.p),
      label: row.label,
      luck: fmtLuck(row.luck_median, row.luck_n),
    };
  }

  // One cell set per pool.SliceRow, in the order the pool table shows: the
  // slice key, independent trades (n), member trades (n_trades), members,
  // win rate and its range, mean R and its range, p, label.
  function poolCells(row) {
    return {
      key: row.key,
      n: String(row.n),
      nTrades: String(row.n_trades),
      members: String(row.members),
      winRate: fmtPct(row.win_rate),
      winRange: fmtRange(row.win_lo, row.win_hi, 'pct'),
      meanR: fmtR(row.mean_r),
      rRange: fmtRange(row.r_lo, row.r_hi, 'r'),
      p: fmtP(row.p),
      label: row.label,
    };
  }

  // The class for a label chip: one per contract label (contracts/pool
  // LABELS), and a plain chip for anything else so a new label still shows
  // its text. The colour is a reading aid; the word is the label.
  var LABEL_CLASSES = {
    'EDGE': 'chip chip-edge',
    'LEAK': 'chip chip-leak',
    'LEANING+': 'chip chip-lean-up',
    'LEANING-': 'chip chip-lean-down',
    'FLUKE': 'chip chip-fluke',
    'NOISE': 'chip chip-noise',
  };

  function labelClass(label) {
    return Object.prototype.hasOwnProperty.call(LABEL_CLASSES, label)
      ? LABEL_CLASSES[label] : 'chip';
  }

  function labelTd(document, label) {
    var td = document.createElement('td');
    var chip = textEl(document, 'span', label);
    chip.setAttribute('class', labelClass(label));
    td.appendChild(chip);
    return td;
  }

  // The count tiles: graded trades, independent trades, members, house,
  // left, and pool slices with 10 or more trades as "K of M".
  function countTiles(doc) {
    var c = doc.counts;
    return [
      {label: 'graded trades', value: String(c.graded_trades)},
      {label: 'independent trades', value: String(c.independent_trades)},
      {label: 'members', value: String(c.members)},
      {label: 'house', value: String(c.house)},
      {label: 'left', value: String(c.left)},
      {label: 'pool slices with 10 or more trades',
       value: String(c.slices_at_min_n) + ' of ' + String(c.slices)},
    ];
  }

  // ── DOM building: createElement and textContent only ─────────────────────

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function textEl(document, tag, text) {
    var el = document.createElement(tag);
    el.textContent = text;
    return el;
  }

  function buildTiles(document, doc) {
    var wrap = document.createElement('div');
    wrap.setAttribute('class', 'tiles');
    countTiles(doc).forEach(function (t) {
      var tile = document.createElement('div');
      tile.setAttribute('class', 'tile');
      tile.appendChild(textEl(document, 'p', t.value));
      tile.appendChild(textEl(document, 'p', t.label));
      wrap.appendChild(tile);
    });
    return wrap;
  }

  var POOL_HEADERS = ['slice', 'independent trades', 'member trades', 'members',
    'win rate', 'win rate range', 'mean R', 'mean R range', 'p', 'label'];

  function buildPoolTable(document, pool) {
    var wrap = document.createElement('div');
    wrap.setAttribute('class', 'table-wrap');
    var table = document.createElement('table');
    table.appendChild(textEl(document, 'caption', 'Pool slices'));
    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    POOL_HEADERS.forEach(function (h) {
      var th = textEl(document, 'th', h);
      th.setAttribute('scope', 'col');
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    var tbody = document.createElement('tbody');
    pool.forEach(function (row) {
      var cells = poolCells(row);
      var tr = document.createElement('tr');
      [cells.key, cells.n, cells.nTrades, cells.members, cells.winRate,
        cells.winRange, cells.meanR, cells.rRange, cells.p]
        .forEach(function (v) { tr.appendChild(textEl(document, 'td', v)); });
      tr.appendChild(labelTd(document, cells.label));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  var MEMBER_HEADERS = ['address', 'since', 'closed trades', 'graded trades',
    'win rate', 'win rate range', 'mean R', 'mean R range', 'p', 'label', 'luck'];

  function buildMembersTable(document, members) {
    var wrap = document.createElement('div');
    wrap.setAttribute('class', 'table-wrap');
    var table = document.createElement('table');
    table.appendChild(textEl(document, 'caption', 'Members'));
    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    MEMBER_HEADERS.forEach(function (h) {
      var th = textEl(document, 'th', h);
      th.setAttribute('scope', 'col');
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    var tbody = document.createElement('tbody');
    members.forEach(function (row) {
      var cells = memberCells(row);
      var tr = document.createElement('tr');
      var addrTd = document.createElement('td');
      if (cells.href) {
        var a = document.createElement('a');
        a.textContent = cells.short;
        a.setAttribute('href', cells.href);
        a.setAttribute('rel', 'noopener noreferrer');
        a.setAttribute('target', '_blank');
        addrTd.appendChild(a);
      } else {
        addrTd.appendChild(textEl(document, 'span', cells.short));
      }
      if (cells.house) {
        var badge = textEl(document, 'span', 'house');
        badge.setAttribute('class', 'badge');
        addrTd.appendChild(badge);
      }
      tr.appendChild(addrTd);
      [cells.since, cells.closed, cells.n, cells.winRate, cells.winRange,
        cells.meanR, cells.rRange, cells.p]
        .forEach(function (v) { tr.appendChild(textEl(document, 'td', v)); });
      tr.appendChild(labelTd(document, cells.label));
      tr.appendChild(textEl(document, 'td', cells.luck));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  // The one entry point after the JSON is in hand: doc.schema !== 1 renders
  // the stale message and nothing else. Only createElement, textContent,
  // setAttribute and appendChild touch the page; no innerHTML anywhere.
  function render(doc, document) {
    var app = document.getElementById('app');
    if (!app) return;
    clearChildren(app);
    if (!doc || doc.schema !== 1) {
      app.appendChild(textEl(document, 'p', 'This page is older than the data. Reload.'));
      return;
    }
    app.appendChild(textEl(document, 'h2', 'Pool overview'));
    app.appendChild(textEl(document, 'p',
      'Build day ' + doc.day + '. Counts trades closed before ' + doc.cutoff + ' UTC.'));
    var notesList = document.createElement('ul');
    (doc.notes || []).forEach(function (n) { notesList.appendChild(textEl(document, 'li', n)); });
    app.appendChild(notesList);
    app.appendChild(buildTiles(document, doc));
    app.appendChild(buildPoolTable(document, doc.pool || []));
    if (!doc.members || doc.members.length === 0) {
      app.appendChild(textEl(document, 'p', 'No members yet.'));
    } else {
      app.appendChild(buildMembersTable(document, doc.members));
    }
  }

  function showError(document, text) {
    var app = document.getElementById('app');
    if (!app) return;
    clearChildren(app);
    app.appendChild(textEl(document, 'p', text));
  }

  // ── wiring: only runs in a real browser ───────────────────────────────────

  function boot() {
    fetch('scoreboard.json', {cache: 'no-store'}).then(function (resp) {
      if (!resp.ok) throw new Error('scoreboard.json: HTTP ' + resp.status);
      return resp.json();
    }).then(function (doc) {
      render(doc, document);
    }).catch(function () {
      showError(document, 'The scoreboard could not be loaded.');
    });
  }

  var api = {
    fmtPct: fmtPct, fmtR: fmtR, fmtRange: fmtRange, fmtP: fmtP, fmtLuck: fmtLuck,
    safeExplorer: safeExplorer, memberCells: memberCells, poolCells: poolCells,
    countTiles: countTiles, labelClass: labelClass, render: render,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);

  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    boot();
  }
})(typeof window !== 'undefined' ? window : this);
