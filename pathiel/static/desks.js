/* desks.js — Strategies ("Desks") pages: /desks (grid) and /desks/{id}.
 *
 * Loaded after pathiel.js (esc, fmtPct, tok, reportRefreshFailure,
 * clampProse, PathielMode, PathielAuth already in scope) and after the page
 * body, same convention as landing.html's own inline script: this file
 * queries DOM that is already parsed, no DOMContentLoaded wrapper needed.
 *
 * Contract: contracts/strategies/models.py. Every number this file formats
 * for display is already computed server-side (DeskAnalytics / IntelPacket /
 * Thesis) — this file never computes a probability, an EV, or a liquidation
 * price. It only picks which already-computed rung to show, which is why
 * the leverage switch is a pure, synchronous DOM write with zero network
 * calls and comfortably under the 50ms budget: BRIEF.analytics.ladder is
 * fetched once with the page, and flipping the segmented control is a single
 * array lookup against data already in memory.
 */

// ── formatting ───────────────────────────────────────────────────────────────
// This contract's *_pct / p_* fields are fractions (ret_1d: 0.05 = +5%, per
// models.py), NOT pre-scaled percentages like the dashboard's daily_pnl_pct.
// The shared fmtPct() in pathiel.js assumes the latter and must not be reused
// here — it would render 0.5% as "+0.01%". These two formatters are the only
// percentage path in this file, signed for returns/distances where the sign
// carries meaning, unsigned for probabilities where a "+" would be noise.
const pctSigned = (v, d = 1) => (v == null || !isFinite(v)) ? '—'
  : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
const pctUnsigned = (v, d = 1) => (v == null || !isFinite(v)) ? '—'
  : (v * 100).toFixed(d) + '%';

const fmtMoney = v => (v == null || !isFinite(v)) ? '—'
  : (v < 0 ? '-$' : '$') + Math.abs(v).toFixed(2);

// Tiered precision so a sub-$1 altcoin and a $100k index both read sanely —
// same rule the rest of the app uses for price cells.
const fmtPrice = v => {
  if (v == null || !isFinite(v)) return '—';
  const n = Math.abs(v);
  const s = n < 1 ? n.toFixed(5) : n < 100 ? n.toFixed(3) : n.toFixed(2);
  return (v < 0 ? '-' : '') + s;
};

const fmtAgeMinutes = mins => {
  if (mins == null || !isFinite(mins)) return '—';
  if (mins < 1) return '<1m';
  if (mins < 60) return Math.round(mins) + 'm';
  if (mins < 1440) return Math.round(mins / 60) + 'h';
  return (mins / 1440).toFixed(1).replace(/\.0$/, '') + 'd';
};

// Forward countdown to a catalyst ("EIA in 2d 4h"). Two units, matching the
// product-brief example exactly, so a reader never has to do the day/hour
// carry in their head.
function fmtCountdown(atIso) {
  const ms = new Date(atIso).getTime() - Date.now();
  if (!isFinite(ms)) return '—';
  if (ms <= 0) return 'now';
  const mins = Math.round(ms / 60000);
  const days = Math.floor(mins / 1440);
  const hours = Math.floor((mins % 1440) / 60);
  const minutes = mins % 60;
  if (days >= 1) return `${days}d ${hours}h`;
  if (hours >= 1) return `${hours}h ${minutes}m`;
  return Math.max(1, minutes) + 'm';
}

const KIND_LABELS = {
  eia_weekly: 'EIA', api_weekly: 'API', opec_meeting: 'OPEC+', fomc: 'FOMC',
  cftc_cot: 'COT', baker_hughes: 'Rigs',
};
const kindLabel = k => KIND_LABELS[k] || String(k || '').replace(/_/g, ' ');

const SIDE_CLASS = { long: 'b-long', short: 'b-short', flat: 'b-flat', pair: 'b-pair' };
const VERDICT_CLASS = { hold: 'b-hold', dip_only: 'b-dip-only', intraday_only: 'b-intraday-only', avoid: 'b-avoid' };
const VERDICT_LABEL = { hold: 'Hold', dip_only: 'Dip only', intraday_only: 'Intraday only', avoid: 'Avoid' };

/** Call.instrument is a coin ("xyz:CL") or, when side == "pair", a pair
 *  label ("Brent-WTI spread") — models.py's own comment on the field. Resolve
 *  either to the human label; never show the raw venue ticker if a desk spec
 *  has a nicer name for it. */
function resolveInstrumentLabel(spec, instrumentStr) {
  if (!spec || !instrumentStr) return instrumentStr || '—';
  const pair = (spec.pairs || []).find(p => p.label === instrumentStr);
  if (pair) return pair.label;
  const inst = (spec.instruments || []).find(i => i.coin === instrumentStr);
  return inst ? inst.label : instrumentStr;
}

/** bull[]/bear[] source_ids point at Headline/PolicyItem ids that live in
 *  intel, not necessarily in thesis.sources — the oil fixture's bear point
 *  cites h3 and p1, neither of which is in thesis.sources. Build the lookup
 *  from intel first, thesis.sources last (most specific wins), so every id a
 *  thesis cites resolves to a title+url whenever the data exists at all. */
function buildSourceMap(brief) {
  const map = new Map();
  const intel = brief.intel;
  if (intel) {
    for (const p of intel.policy || []) map.set(p.id, { title: p.title, url: p.url });
    for (const h of intel.headlines || []) map.set(h.id, { title: h.title, url: h.url });
  }
  if (brief.thesis) for (const s of brief.thesis.sources || []) map.set(s.id, { title: s.title, url: s.url });
  return map;
}

function setDollarField(el, v) {
  if (!el) return;
  el.innerHTML = `<span class="dollar-value">${esc(fmtMoney(v))}</span><span class="dollar-mask">•••</span>`;
}

/** Same localStorage key and class as landing.html's toggle — pathiel.css's
 *  body.discreet rule is global, but the click handler is page-local there
 *  too (not deduplicated into pathiel.js), so every page wires its own. */
function wireDiscreetToggle() {
  const btn = document.getElementById('discreet-toggle');
  if (!btn) return;
  const sync = () => { btn.textContent = document.body.classList.contains('discreet') ? 'Show amounts' : 'Hide amounts'; };
  if (localStorage.getItem('pathiel-discreet') === '1') document.body.classList.add('discreet');
  sync();
  btn.addEventListener('click', () => {
    const on = document.body.classList.toggle('discreet');
    localStorage.setItem('pathiel-discreet', on ? '1' : '0');
    sync();
  });
}

// ── grid page (/desks) ───────────────────────────────────────────────────────

function deskCardHtml(d) {
  const moveClass = (d.ret_1d || 0) >= 0 ? 'pos' : 'neg';
  const sideClass = SIDE_CLASS[d.call_side] || '';
  const tr = d.track_record || {};
  return `<a class="desk-card" data-desk-id="${esc(d.id)}" href="/desks/${esc(d.id)}">
    <div class="dc-head">
      <span class="dc-name">${esc(d.name)}</span>
      <span class="dc-mid">${d.mid != null ? fmtPrice(d.mid) : '—'}</span>
    </div>
    <div class="dc-move ${moveClass}">${d.ret_1d != null ? pctSigned(d.ret_1d) + ' today' : ' '}</div>
    <div class="dc-call">${d.one_liner ? esc(d.one_liner)
        : '<span class="mut">No call yet &mdash; open the desk to make one.</span>'}</div>
    <div class="dc-foot">
      ${d.call_side ? `<span class="badge ${sideClass}">${esc(d.call_side.toUpperCase())}</span>` : ''}
      ${d.next_catalyst ? `<span>${esc(kindLabel(d.next_catalyst.kind))} in ${fmtCountdown(d.next_catalyst.at)}</span>` : ''}
      ${d.thesis_age_min != null ? `<span>${fmtAgeMinutes(d.thesis_age_min)} old</span>` : ''}
      <span class="dc-track">${tr.n_graded
          ? pctUnsigned(tr.direction_hit_rate, 0) + ' hit (' + tr.n_graded + ')'
          : 'ungraded'}</span>
    </div>
  </a>`;
}

async function loadGrid() {
  const loading = document.getElementById('desks-loading');
  const empty = document.getElementById('desks-empty');
  const error = document.getElementById('desks-error');
  const grid = document.getElementById('desks-grid');
  loading.hidden = false; empty.hidden = true; error.hidden = true; grid.hidden = true;
  try {
    const r = await fetch('/api/desks');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const cards = await r.json();
    loading.hidden = true;
    if (!Array.isArray(cards) || !cards.length) { empty.hidden = false; return; }
    grid.hidden = false;
    grid.innerHTML = cards.map(deskCardHtml).join('');
    if (cards.some(c => c.mid == null)) followWarmup();
  } catch (err) {
    loading.hidden = true; error.hidden = false;
    reportRefreshFailure('loadGrid', err);
  }
}

// The server warms every desk's market data in the background after a
// restart. Until a desk's first build lands its card has no price; re-read
// the (cache-only, instant) card list a few times so prices fill in without
// a reload. Gives up after ~90s; a card still empty then stays honest ("—").
let WARMUP_FOLLOWING = false;
async function followWarmup() {
  if (WARMUP_FOLLOWING) return;
  WARMUP_FOLLOWING = true;
  const grid = document.getElementById('desks-grid');
  try {
    for (let i = 0; i < 30; i++) {
      await new Promise(res => setTimeout(res, 3000));
      let cards;
      try {
        const r = await fetch('/api/desks');
        if (!r.ok) continue;
        cards = await r.json();
      } catch (_) { continue; }
      if (!Array.isArray(cards) || !cards.length) continue;
      grid.innerHTML = cards.map(deskCardHtml).join('');
      if (!cards.some(c => c.mid == null)) return;
    }
  } finally {
    WARMUP_FOLLOWING = false;
  }
}

function initGridPage() {
  document.getElementById('desks-retry').addEventListener('click', loadGrid);
  loadGrid();
}

// ── desk page (/desks/{id}) ──────────────────────────────────────────────────

let BRIEF = null;

function setDeskState(state) {   // 'loading' | 'ready' | 'error'
  document.getElementById('desk-loading').hidden = state !== 'loading';
  document.getElementById('desk-error').hidden = state !== 'error';
  document.getElementById('desk-content').hidden = state !== 'ready';
}

function renderStaleBanner(brief) {
  const el = document.getElementById('stale-banner');
  const stale = new Set(brief.stale || []);
  const expired = !!(brief.thesis && Date.now() >= new Date(brief.thesis.expires_at).getTime());
  if (expired) stale.add('thesis');
  if (!stale.size) { el.hidden = true; return; }
  el.hidden = false;
  const bits = [...stale];
  el.innerHTML = `<span class="b-strong">Stale:</span> ${esc(bits.join(', '))} &mdash; ` +
    (stale.has('thesis')
      ? 'this call is older than its own horizon. Price has likely moved — hit Refresh before sizing anything off it.'
      : 'this data is older than its refresh window.');
}

function renderCallCard(brief) {
  const card = document.getElementById('call-card');
  const empty = document.getElementById('call-empty');
  const thesis = brief.thesis;
  if (!thesis) { card.hidden = true; empty.hidden = false; return; }
  card.hidden = false; empty.hidden = true;
  const call = thesis.call;
  const sideClass = SIDE_CLASS[call.side] || 'b-flat';

  document.getElementById('call-side-badge').innerHTML =
    `<span class="badge ${sideClass}">${esc((call.side || '').toUpperCase())}</span>`;
  document.getElementById('call-instrument').textContent = resolveInstrumentLabel(brief.spec, call.instrument);
  document.getElementById('call-one-liner').textContent = call.one_liner;
  document.getElementById('call-leverage').textContent = call.leverage != null ? call.leverage + 'x' : '—';
  document.getElementById('call-confidence').textContent = pctUnsigned(call.confidence, 0);
  document.getElementById('call-entry').textContent = call.entry_px != null ? fmtPrice(call.entry_px)
      : (call.entry_zone && call.entry_zone.length === 2
          ? fmtPrice(call.entry_zone[0]) + '–' + fmtPrice(call.entry_zone[1]) : '—');
  document.getElementById('call-stop').textContent = fmtPrice(call.stop_px);
  document.getElementById('call-targets').textContent =
    (call.targets && call.targets.length) ? call.targets.map(fmtPrice).join(' / ') : '—';
  document.getElementById('call-invalidation').textContent = call.invalidation;

  const ageMin = (Date.now() - new Date(thesis.created_at).getTime()) / 60000;
  document.getElementById('call-age').textContent = 'Called ' + fmtAgeMinutes(ageMin) + ' ago';
  renderYourSize(brief);
}

// "Your size": the call sized for the active account by the risk service
// (POST /api/risk/size, the same rules the bot sizes its books with). Money
// uses the value+mask pair so "Hide amounts" covers it. Any failure hides the
// line: the call card stands on its own without it.
let SIZE_SEQ = 0;
async function renderYourSize(brief) {
  const el = document.getElementById('call-size');
  if (!el) return;
  const call = brief.thesis && brief.thesis.call;
  if (!call || !['long', 'short'].includes(call.side) || call.entry_px == null || call.stop_px == null) {
    el.hidden = true;
    return;
  }
  const seq = ++SIZE_SEQ;
  let s;
  try {
    const r = await fetch('/api/risk/size', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ coin: call.instrument, entry_px: call.entry_px, stop_px: call.stop_px,
                             leverage: call.leverage || null, targets: call.targets || [] }),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    s = await r.json();
  } catch (_) {
    if (seq === SIZE_SEQ) el.hidden = true;
    return;
  }
  if (seq !== SIZE_SEQ) return;          // a newer render owns this line
  const money = v => `<span class="dollar-value">${esc(fmtMoney(v))}</span><span class="dollar-mask">•••</span>`;
  if (!s.can_trade) {
    el.innerHTML = `<b>Your size:</b> ${esc(s.reason)}`;
  } else {
    const cap = s.leverage_capped_by ? ` (your ${esc(s.leverage_capped_by)} max)` : '';
    el.innerHTML = `<b>Your size:</b> ${money(s.notional_usd)} position at ${esc(String(s.leverage))}x${cap}, `
      + `${money(s.margin_usd)} margin. Stop-out costs ${money(s.risk_usd)} (${esc(s.risk_pct.toFixed(1))}%).`
      + ((s.warnings && s.warnings.length) ? `<div class="call-size-warn">${esc(s.warnings[0])}</div>` : '');
  }
  el.hidden = false;
}

/** The ladder rungs matching the call's own side for its own instrument —
 *  "5x/10x/20x, long or short matching the call" per the product brief. With
 *  no thesis yet (empty state) there is no side to match, so this defaults
 *  to the primary instrument's long rungs: the switch stays usable as a
 *  practice tool even before a call exists. */
function rungsForBrief(brief) {
  const ladder = (brief.analytics && brief.analytics.ladder) || [];
  const call = brief.thesis && brief.thesis.call;
  const primary = (brief.spec.instruments || []).find(i => i.role === 'primary') || {};
  const isDirectional = call && call.side !== 'pair' && call.side !== 'flat';
  const coin = (isDirectional && call.instrument) || primary.coin;
  const wantSide = (call && call.side === 'short') ? 'short' : 'long';
  let rows = ladder.filter(r => r.coin === coin && r.side === wantSide);
  if (!rows.length) rows = ladder.filter(r => r.coin === coin);
  return rows.slice().sort((a, b) => a.leverage - b.leverage);
}

function reasonForLeverage(brief, leverage) {
  const views = (brief.thesis && brief.thesis.leverage_view) || [];
  const v = views.find(x => x.leverage === leverage);
  return v ? v.reason : '';
}

/** Pure, synchronous, no network — every number here already lives in
 *  BRIEF.analytics.ladder from the one fetch that loaded the page. */
function paintLeverage(brief, rows, leverage) {
  const r = rows.find(x => x.leverage === leverage);
  if (!r) return;
  document.getElementById('lev-liq-px').textContent = fmtPrice(r.liq_px);
  document.getElementById('lev-liq-dist').textContent = pctSigned(r.liq_dist_pct);
  document.getElementById('lev-p-liq').textContent = pctUnsigned(r.p_liq);
  document.getElementById('lev-p-win').textContent = pctUnsigned(r.p_win);
  document.getElementById('lev-ev').textContent = pctSigned(r.ev_pct);
  document.getElementById('lev-p10').textContent = pctSigned(r.equity_ret.p10);
  document.getElementById('lev-p-double').textContent = pctUnsigned(r.p_double);
  setDollarField(document.getElementById('lev-at-risk'), r.equity_usd);
  document.getElementById('lev-verdict').innerHTML =
    `<span class="badge ${VERDICT_CLASS[r.verdict] || ''}">${esc(VERDICT_LABEL[r.verdict] || r.verdict)}</span>`;
  document.getElementById('lev-reason').textContent = reasonForLeverage(brief, leverage);
}

function renderLeverageCard(brief) {
  const rows = rungsForBrief(brief);
  const card = document.getElementById('lev-card');
  const empty = document.getElementById('lev-empty');
  const seg = document.getElementById('lev-seg');
  if (!rows.length) { card.hidden = true; empty.hidden = false; return; }
  card.hidden = false; empty.hidden = true;

  seg.innerHTML = rows.map((r, i) => `
    <span class="lev-radio-wrap">
      <input type="radio" name="leverage-choice" class="lev-radio" id="lev-opt-${r.leverage}"
             value="${r.leverage}" ${i === 0 ? 'checked' : ''}>
      <label for="lev-opt-${r.leverage}" class="lev-radio-label">${r.leverage}x</label>
    </span>`).join('');
  seg.querySelectorAll('.lev-radio').forEach(radio => {
    radio.addEventListener('change', () => { if (radio.checked) paintLeverage(brief, rows, Number(radio.value)); });
  });
  paintLeverage(brief, rows, rows[0].leverage);
}

function classifyScenario(s) {
  if (s.move_lo_pct >= 0) return 'seg-up';
  if (s.move_hi_pct <= 0) return 'seg-down';
  return 'seg-neutral';
}

function renderScenarios(thesis) {
  const bar = document.getElementById('scenario-bar');
  const list = document.getElementById('scenario-list');
  const empty = document.getElementById('scenarios-empty');
  const scenarios = (thesis && thesis.scenarios) || [];
  if (!scenarios.length) { bar.innerHTML = ''; list.innerHTML = ''; empty.hidden = false; return; }
  empty.hidden = true;
  bar.innerHTML = scenarios.map(s =>
    `<div class="scenario-seg ${classifyScenario(s)}" style="width:${Math.max(0, s.probability * 100).toFixed(2)}%"
          title="${esc(s.name)} · ${pctUnsigned(s.probability)}"></div>`).join('');
  list.innerHTML = scenarios.map(s => `
    <div class="scenario-row">
      <div class="scenario-name"><span class="scenario-dot ${classifyScenario(s)}"></span>${esc(s.name)}</div>
      <div class="scenario-prob">${pctUnsigned(s.probability)}</div>
      <div class="scenario-bucket">${pctSigned(s.move_lo_pct)} to ${pctSigned(s.move_hi_pct)}</div>
      <div class="scenario-desc">${esc(s.description)}</div>
      ${(s.triggers && s.triggers.length)
          ? `<div class="scenario-triggers">${s.triggers.map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>` : ''}
    </div>`).join('');
}

function renderCatalysts(brief) {
  const wrap = document.getElementById('catalyst-chips');
  const empty = document.getElementById('catalysts-empty');
  const source = (brief.intel && brief.intel.catalysts) || (brief.thesis && brief.thesis.catalysts) || [];
  const upcoming = source.filter(c => new Date(c.at).getTime() > Date.now())
                          .sort((a, b) => new Date(a.at) - new Date(b.at));
  if (!upcoming.length) { wrap.innerHTML = ''; empty.hidden = false; return; }
  empty.hidden = true;
  wrap.innerHTML = upcoming.map(c => {
    const local = new Date(c.at).toLocaleString();
    // toUTCString() labels itself "GMT", not "UTC" — same civil time here,
    // but the brief asks for "UTC" on hover, so that word is spelled out
    // explicitly rather than trusting the engine's own wording.
    const utc = new Date(c.at).toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
    return `<span class="catalyst-chip imp-${c.importance}" title="${esc(local)} local · ${esc(utc)}">
      <span class="catalyst-kind">${esc(kindLabel(c.kind))}</span>
      <span class="catalyst-when">in ${fmtCountdown(c.at)}</span>
    </span>`;
  }).join('');
}

function renderBullBear(brief) {
  const srcMap = buildSourceMap(brief);
  const bullEl = document.getElementById('bull-col');
  const bearEl = document.getElementById('bear-col');
  const empty = document.getElementById('bull-bear-empty');
  const thesis = brief.thesis;
  const bull = (thesis && thesis.bull) || [];
  const bear = (thesis && thesis.bear) || [];
  if (!bull.length && !bear.length) { bullEl.innerHTML = ''; bearEl.innerHTML = ''; empty.hidden = false; return; }
  empty.hidden = true;
  const renderPoints = pts => pts.map(p => `
    <div class="bb-point">${esc(p.text)}
      <div class="bb-sources">${(p.source_ids || []).map(id => {
          const s = srcMap.get(id);
          return (s && s.url)
            ? `<a class="src-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || id)}</a>`
            : `<span class="chip">${esc(id)}</span>`;
        }).join('')}</div>
    </div>`).join('');
  bullEl.innerHTML = bull.length ? renderPoints(bull) : '<div class="mut">No bull points yet.</div>';
  bearEl.innerHTML = bear.length ? renderPoints(bear) : '<div class="mut">No bear points yet.</div>';
}

function renderWcmm(thesis) {
  const el = document.getElementById('wcmm-list');
  const empty = document.getElementById('wcmm-empty');
  const items = (thesis && thesis.what_changes_my_mind) || [];
  if (!items.length) { el.innerHTML = ''; empty.hidden = false; return; }
  empty.hidden = true;
  el.innerHTML = items.map(t => `<li>${esc(t)}</li>`).join('');
}

function renderLessons(spec, thesis) {
  const grid = document.getElementById('lessons-list');
  const upcomingEl = document.getElementById('lessons-upcoming');
  const lessons = (thesis && thesis.lessons) || [];
  grid.innerHTML = lessons.length ? lessons.map(l => `
      <div class="card lesson-card">
        <div class="lesson-term">${esc(l.term)}</div>
        <div class="lesson-exp">${esc(l.explanation)}</div>
        <div class="lesson-tied">${esc(l.tied_to)}</div>
      </div>`).join('')
    : '<div class="mut">No lesson written for this cycle yet.</div>';
  const covered = new Set(lessons.map(l => l.term));
  const rest = (spec.lessons || []).filter(t => !covered.has(t));
  upcomingEl.innerHTML = rest.length
    ? 'More on this desk: ' + rest.map(t => `<span class="chip">${esc(t)}</span>`).join('') : '';
}

function gradeCellHtml(t) {
  const g = t.grade;
  if (!g) return '<span class="grade-pending">pending</span>';
  if (g.direction_hit == null) return `<span>${pctSigned(g.ret_pct)}</span>`;
  return `<span class="${g.direction_hit ? 'grade-hit' : 'grade-miss'}">${pctSigned(g.ret_pct)}</span>`;
}

function renderTrackRecord(tr) {
  const el = document.getElementById('track-record');
  tr = tr || { by_author: {} };
  const byAuthor = tr.by_author || {};
  const col = (label, key) => {
    const a = byAuthor[key];
    if (!a || !a.n_graded) {
      return `<div class="track-col"><div class="sec-label">${esc(label)}</div><div class="mut">no graded calls yet</div></div>`;
    }
    return `<div class="track-col">
      <div class="sec-label">${esc(label)}</div>
      <div class="kv">${a.direction_hit_rate != null ? pctUnsigned(a.direction_hit_rate, 0) : '—'}</div>
      <div class="ksub">${a.n_graded} graded${a.mean_ret_pct != null ? ' · ' + pctSigned(a.mean_ret_pct) + ' avg' : ''}</div>
    </div>`;
  };
  el.innerHTML = col('Machine', 'machine') + col('You', 'user');
}

function renderHistory(brief) {
  const body = document.getElementById('history-body');
  const empty = document.getElementById('history-empty');
  const history = brief.history || [];
  if (!history.length) {
    body.innerHTML = ''; empty.hidden = false;
  } else {
    empty.hidden = true;
    body.innerHTML = history.map(t => `<tr>
        <td>${esc(new Date(t.created_at).toLocaleDateString())}</td>
        <td><span class="badge ${t.author === 'user' ? 'b-user' : 'b-machine'}">${esc(t.author)}</span></td>
        <td><span class="badge ${SIDE_CLASS[t.call.side] || ''}">${esc((t.call.side || '').toUpperCase())}</span>
            ${esc(t.call.one_liner)}</td>
        <td class="num">${pctUnsigned(t.call.confidence, 0)}</td>
        <td class="num">${gradeCellHtml(t)}</td>
      </tr>`).join('');
  }
  renderTrackRecord(brief.track_record);
}

function renderDetails(brief) {
  const a = brief.analytics;
  const intel = brief.intel;

  document.getElementById('snapshot-table').innerHTML = (!a || !a.instruments.length) ? '<div class="mut">No snapshot.</div>' : `
    <table><thead><tr><th>Instrument</th><th class="num">Mid</th><th class="num">1d</th><th class="num">5d</th>
      <th class="num">20d</th><th class="num">ATR14</th><th class="num">Funding/hr</th><th class="num">OI</th><th class="num">24h vol</th></tr></thead>
      <tbody>${a.instruments.map(i => `<tr>
        <td>${esc(i.label)}</td><td class="num">${fmtPrice(i.mid)}</td><td class="num">${pctSigned(i.ret_1d)}</td>
        <td class="num">${pctSigned(i.ret_5d)}</td><td class="num">${pctSigned(i.ret_20d)}</td>
        <td class="num">${pctUnsigned(i.atr14_pct)}</td>
        <td class="num">${i.funding_hourly != null ? pctSigned(i.funding_hourly, 4) : '—'}</td>
        <td class="num">${i.open_interest_usd != null ? fmtMoney(i.open_interest_usd) : '—'}</td>
        <td class="num">${i.volume_24h_usd != null ? fmtMoney(i.volume_24h_usd) : '—'}</td>
      </tr>`).join('')}</tbody></table>`;

  document.getElementById('pair-table').innerHTML = (!a || !a.pairs.length) ? '<div class="mut">No pair tracked on this desk.</div>' : `
    <table><thead><tr><th>Pair</th><th class="num">Spread now</th><th class="num">Mean 20d</th>
      <th class="num">Mean 60d</th><th class="num">Range 60d</th><th class="num">Vol/day</th></tr></thead>
      <tbody>${a.pairs.map(p => `<tr>
        <td>${esc(p.label)}</td><td class="num">${fmtPrice(p.spread_now)}</td><td class="num">${fmtPrice(p.spread_mean_20d)}</td>
        <td class="num">${p.spread_mean_60d != null ? fmtPrice(p.spread_mean_60d) : '—'}</td>
        <td class="num">${p.spread_min_60d != null ? fmtPrice(p.spread_min_60d) + ' – ' + fmtPrice(p.spread_max_60d) : '—'}</td>
        <td class="num">${pctUnsigned(p.pair_vol_daily)}</td>
      </tr>`).join('')}</tbody></table>`;

  document.getElementById('forward-table').innerHTML = (!a || !a.forward.length) ? '<div class="mut">No forward-window sample yet.</div>' :
    a.forward.map(f => `
      <table><thead><tr><th colspan="2">${esc(f.coin)} · ${f.horizon_days}d forward, n=${f.n_windows}</th></tr></thead>
        <tbody>
          <tr><td>Final return (p10 / p50 / p90)</td><td class="num">${pctSigned(f.final_ret.p10)} / ${pctSigned(f.final_ret.p50)} / ${pctSigned(f.final_ret.p90)}</td></tr>
          <tr><td>Mean final return</td><td class="num">${pctSigned(f.final_ret_mean)}</td></tr>
          <tr><td>Long worst case (p50 MAE)</td><td class="num">${pctSigned(f.long_mae.p50)}</td></tr>
          <tr><td>Short worst case (p50 MAE)</td><td class="num">${pctSigned(f.short_mae.p50)}</td></tr>
          ${(f.conditional || []).map(c => `<tr><td>${esc(c.label)} (n=${c.n})</td>
              <td class="num">mean ${pctSigned(c.mean)}, hit-up ${pctUnsigned(c.hit_rate_up)}</td></tr>`).join('')}
        </tbody></table>`).join('');

  document.getElementById('odds-table').innerHTML = (!intel || !intel.odds.length) ? '<div class="mut">No prediction-market odds.</div>' : `
    <table><thead><tr><th>Question</th><th class="num">Probability</th><th class="num">Volume</th></tr></thead>
    <tbody>${intel.odds.map(o => `<tr>
      <td><a class="src-link" href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.question)}</a></td>
      <td class="num">${pctUnsigned(o.probability, 0)}</td>
      <td class="num">${o.volume_usd != null ? fmtMoney(o.volume_usd) : '—'}</td>
    </tr>`).join('')}</tbody></table>`;

  document.getElementById('positioning-table').innerHTML = (!intel || !intel.positioning.length) ? '<div class="mut">No positioning data.</div>' : `
    <table><thead><tr><th>Measure</th><th class="num">Value</th><th class="num">Change</th><th class="num">As of</th></tr></thead>
    <tbody>${intel.positioning.map(p => `<tr>
      <td>${esc(p.label)}</td><td class="num">${Number(p.value).toLocaleString()}</td>
      <td class="num">${p.change != null ? (p.change >= 0 ? '+' : '') + Number(p.change).toLocaleString() : '—'}</td>
      <td class="num">${esc(new Date(p.as_of).toLocaleDateString())}</td>
    </tr>`).join('')}</tbody></table>`;

  const sources = intel ? [...(intel.headlines || []), ...(intel.policy || [])] : [];
  document.getElementById('sources-list').innerHTML = sources.length
    ? sources.map(s => `<div class="cite-row"><a class="src-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>`).join('')
    : '<div class="mut">No sources recorded.</div>';
  const failed = (intel && intel.sources_failed) || [];
  document.getElementById('sources-failed').innerHTML = failed.length
    ? `<div class="sources-failed-note">Feeds that errored this read: ${esc(failed.join(', '))}</div>` : '';
}

// ── "Make your own call" form ────────────────────────────────────────────────

let scenarioRowId = 0;

function addScenarioRow() {
  const wrap = document.getElementById('ut-scenarios');
  const row = document.createElement('div');
  row.className = 'scenario-edit-row';
  row.dataset.rowId = String(++scenarioRowId);
  row.innerHTML = `
    <input type="text" placeholder="Scenario name" class="se-name" aria-label="Scenario name">
    <input type="number" step="0.01" min="0" max="1" placeholder="Prob 0-1" class="se-prob" aria-label="Scenario probability">
    <input type="number" step="0.01" placeholder="Move lo" class="se-lo" aria-label="Scenario move low">
    <input type="number" step="0.01" placeholder="Move hi" class="se-hi" aria-label="Scenario move high">
    <input type="text" placeholder="Description" class="se-desc" aria-label="Scenario description">
    <button type="button" class="scenario-remove">Remove</button>`;
  row.querySelector('.scenario-remove').addEventListener('click', () => row.remove());
  wrap.appendChild(row);
}

function readScenarioRows() {
  return [...document.querySelectorAll('#ut-scenarios .scenario-edit-row')].map(row => ({
    name: row.querySelector('.se-name').value.trim(),
    probability: parseFloat(row.querySelector('.se-prob').value),
    move_lo_pct: parseFloat(row.querySelector('.se-lo').value),
    move_hi_pct: parseFloat(row.querySelector('.se-hi').value),
    description: row.querySelector('.se-desc').value.trim(),
    triggers: [],
  }));
}

function setFieldError(fieldId, msg) {
  const errEl = document.getElementById(fieldId + '-error');
  const input = document.getElementById(fieldId);
  if (errEl) errEl.textContent = msg || '';
  const wrap = input && input.closest('.field');
  if (wrap) wrap.classList.toggle('has-error', !!msg);
  return !msg;
}

/** Mirrors the server-side validators in contracts/strategies/models.py
 *  exactly (160-char one_liner cap, 0..1 confidence/probability, scenario
 *  probabilities summing to 1 within 0.02) so a mistake is caught here
 *  rather than round-tripping a 422. Returns a UserThesisIn-shaped object, or
 *  null when any field fails. */
function validateUserThesis() {
  let ok = true;
  const oneLiner = document.getElementById('ut-one-liner').value.trim();
  ok = setFieldError('ut-one-liner', !oneLiner ? 'Required.' : oneLiner.length > 160 ? 'Max 160 characters.' : '') && ok;

  const invalidation = document.getElementById('ut-invalidation').value.trim();
  ok = setFieldError('ut-invalidation', !invalidation ? 'Required — what proves this wrong?' : '') && ok;

  const confidence = Number(document.getElementById('ut-confidence').value);
  ok = setFieldError('ut-confidence', (!isFinite(confidence) || confidence < 0 || confidence > 1) ? 'Must be 0 to 1.' : '') && ok;

  const targetsRaw = document.getElementById('ut-targets').value.trim();
  let targets = [];
  if (targetsRaw) {
    targets = targetsRaw.split(',').map(s => parseFloat(s.trim()));
    ok = setFieldError('ut-targets', targets.some(n => !isFinite(n)) ? 'Numbers, comma-separated.' : '') && ok;
  } else setFieldError('ut-targets', '');

  const entryRaw = document.getElementById('ut-entry').value.trim();
  const entry = entryRaw ? parseFloat(entryRaw) : null;
  ok = setFieldError('ut-entry', (entryRaw && !isFinite(entry)) ? 'Must be a number.' : '') && ok;

  const stopRaw = document.getElementById('ut-stop').value.trim();
  const stop = stopRaw ? parseFloat(stopRaw) : null;
  ok = setFieldError('ut-stop', (stopRaw && !isFinite(stop)) ? 'Must be a number.' : '') && ok;

  const scenarios = readScenarioRows();
  let scenarioError = '';
  for (const s of scenarios) {
    if (!s.name) scenarioError = 'Every scenario needs a name.';
    else if (!(s.probability >= 0 && s.probability <= 1)) scenarioError = 'Scenario probability must be 0 to 1.';
    else if (!isFinite(s.move_lo_pct) || !isFinite(s.move_hi_pct)) scenarioError = 'Scenario move range must be numbers.';
    else if (s.move_lo_pct > s.move_hi_pct) scenarioError = 'Scenario move range: low must be ≤ high.';
    else if (!s.description) scenarioError = 'Every scenario needs a short description.';
    if (scenarioError) break;
  }
  if (!scenarioError && scenarios.length) {
    const sum = scenarios.reduce((acc, s) => acc + s.probability, 0);
    if (Math.abs(sum - 1) > 0.02) scenarioError = `Scenario probabilities sum to ${sum.toFixed(2)}, not 1.`;
  }
  document.getElementById('ut-scenarios-error').textContent = scenarioError;
  if (scenarioError) ok = false;

  if (!ok) return null;
  const leverageRaw = document.getElementById('ut-leverage').value;
  return {
    call: {
      side: document.getElementById('ut-side').value,
      instrument: document.getElementById('ut-instrument').value,
      one_liner: oneLiner,
      entry_px: entry,
      stop_px: stop,
      targets,
      leverage: leverageRaw ? Number(leverageRaw) : null,
      confidence,
      invalidation,
    },
    scenarios,
    notes: document.getElementById('ut-notes').value.trim() || null,
  };
}

function prefillUserForm(spec) {
  const instSel = document.getElementById('ut-instrument');
  const levSel = document.getElementById('ut-leverage');
  instSel.innerHTML = spec.instruments.map(i => `<option value="${esc(i.coin)}">${esc(i.label)}</option>`).join('');
  const primary = spec.instruments.find(i => i.role === 'primary');
  if (primary) instSel.value = primary.coin;
  levSel.innerHTML = '<option value="">not set</option>' +
    spec.leverage_ladder.map(l => `<option value="${l}">${l}x</option>`).join('');
  document.getElementById('ut-scenarios').innerHTML = '';
}

function wireConfidenceReadout() {
  const input = document.getElementById('ut-confidence');
  const out = document.getElementById('ut-confidence-display');
  if (!input || !out) return;
  const sync = () => { out.textContent = Number(input.value).toFixed(2); };
  input.addEventListener('input', sync);
  sync();
}

function wireUserThesisForm(deskId) {
  wireConfidenceReadout();
  document.getElementById('ut-add-scenario').addEventListener('click', () => addScenarioRow());
  document.getElementById('user-thesis-form').addEventListener('submit', async e => {
    e.preventDefault();
    const errBanner = document.getElementById('ut-form-error');
    const okBanner = document.getElementById('ut-success');
    errBanner.hidden = true; okBanner.hidden = true;
    const payload = validateUserThesis();
    if (!payload) return;

    const btn = document.getElementById('ut-submit');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const r = await fetch(`/api/desks/${encodeURIComponent(deskId)}/theses`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        errBanner.hidden = false;
        errBanner.textContent = body && body.detail ? `save failed: ${JSON.stringify(body.detail)}` : `save failed: HTTP ${r.status}`;
        return;
      }
      const thesis = await r.json();
      BRIEF.history = [thesis, ...(BRIEF.history || [])];
      renderHistory(BRIEF);
      okBanner.hidden = false;
      okBanner.textContent = 'Saved — see it in "Your calls vs the machine" below.';
      document.getElementById('user-thesis-form').reset();
      document.getElementById('ut-scenarios').innerHTML = '';
      ['ut-one-liner', 'ut-invalidation', 'ut-confidence', 'ut-targets', 'ut-entry', 'ut-stop']
        .forEach(f => setFieldError(f, ''));
      document.getElementById('ut-scenarios-error').textContent = '';
    } catch (err) {
      errBanner.hidden = false;
      errBanner.textContent = 'save failed: ' + String(err);
      reportRefreshFailure('userThesisSubmit', err);
    } finally {
      btn.disabled = false; btn.textContent = 'Save my call';
    }
  });
}

// ── refresh ──────────────────────────────────────────────────────────────────

// A refresh that writes a thesis takes about a minute (news + one model
// call); one that finds nothing new takes seconds. Poll fast at first, then
// once a second, for up to 4 minutes, showing the server's stage label and
// the elapsed time so a long write never looks hung.
const JOB_POLL_LIMIT_MS = 240000;
async function pollJob(jobId, deskId, status) {
  const t0 = Date.now();
  for (let i = 0; Date.now() - t0 < JOB_POLL_LIMIT_MS; i++) {
    const r = await fetch(`/api/desks/jobs/${encodeURIComponent(jobId)}`);
    if (!r.ok) throw new Error('job poll HTTP ' + r.status);
    const job = await r.json();
    if (job.state === 'running' || job.state === 'queued') {
      const secs = Math.round((Date.now() - t0) / 1000);
      status.className = 'refresh-status';
      status.textContent = (job.message || 'checking for a material move') + '… ' + secs + 's';
      await new Promise(res => setTimeout(res, i < 8 ? 250 : 1000));
      continue;
    }
    status.className = 'refresh-status is-' + job.state;
    status.textContent = job.message || job.state;
    if (job.state === 'done') {
      const brief = await (await fetch(`/api/desks/${encodeURIComponent(deskId)}`)).json();
      renderDesk(brief, { quiet: true });
    }
    return job;
  }
  status.className = 'refresh-status is-error';
  status.textContent = 'still running after 4 minutes. It will land on its own; reload in a bit.';
  return null;
}

function wireRefresh(deskId) {
  const btn = document.getElementById('refresh-btn');
  const status = document.getElementById('refresh-status');
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Checking…';
    status.className = 'refresh-status'; status.textContent = 'checking for a material move…';
    try {
      const r = await fetch(`/api/desks/${encodeURIComponent(deskId)}/refresh`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: false }),
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const job = await r.json();
      await pollJob(job.id, deskId, status);
    } catch (err) {
      status.className = 'refresh-status is-error';
      status.textContent = 'refresh failed: ' + String(err);
      reportRefreshFailure('refresh', err);
    } finally {
      btn.disabled = false; btn.textContent = 'Refresh';
    }
  });
}

// ── page assembly ────────────────────────────────────────────────────────────

// quiet: a background update of a page the user may be typing on. Data
// sections re-render; the "Make your own call" form is left alone.
function renderDesk(brief, opts = {}) {
  BRIEF = brief;
  document.getElementById('desk-root').dataset.deskId = brief.spec.id;
  document.title = brief.spec.name + ' · pathiel desks';
  document.getElementById('desk-name').textContent = brief.spec.name;
  document.getElementById('desk-tagline').textContent = brief.spec.tagline;
  renderStaleBanner(brief);
  renderCallCard(brief);
  renderLeverageCard(brief);
  renderScenarios(brief.thesis);
  renderCatalysts(brief);
  renderBullBear(brief);
  renderWcmm(brief.thesis);
  renderLessons(brief.spec, brief.thesis);
  renderHistory(brief);
  renderDetails(brief);
  if (!opts.quiet) prefillUserForm(brief.spec);
  renderUpdating(brief);
}

function renderUpdating(brief) {
  const el = document.getElementById('desk-updating');
  if (el) el.hidden = !brief.refreshing;
}

// The brief came from cache while the server rebuilds it in the background
// (brief.refreshing). Re-read it every few seconds until the new numbers
// land, then re-render in place. Stops after ~2 minutes either way.
let FOLLOWING = false;
async function followRefresh(deskId) {
  if (FOLLOWING) return;
  FOLLOWING = true;
  const asOf = BRIEF && BRIEF.analytics ? BRIEF.analytics.as_of : null;
  try {
    for (let i = 0; i < 40; i++) {
      await new Promise(res => setTimeout(res, i < 10 ? 2000 : 4000));
      let next;
      try {
        const r = await fetch(`/api/desks/${encodeURIComponent(deskId)}`);
        if (!r.ok) continue;
        next = await r.json();
      } catch (_) { continue; }
      const landed = next.analytics && next.analytics.as_of !== asOf;
      if (landed || !next.refreshing) {
        renderDesk(next, { quiet: true });
        return;
      }
    }
    renderUpdating({ refreshing: false });
  } finally {
    FOLLOWING = false;
  }
}

async function loadDesk(deskId) {
  setDeskState('loading');
  try {
    const r = await fetch(`/api/desks/${encodeURIComponent(deskId)}`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const brief = await r.json();
    renderDesk(brief);
    setDeskState('ready');
    if (brief.refreshing) followRefresh(deskId);
  } catch (err) {
    setDeskState('error');
    reportRefreshFailure('loadDesk', err);
  }
}

function currentDeskIdFromPath() {
  const parts = location.pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  return parts[parts.length - 1] || '';
}

function initDeskPage() {
  const deskId = currentDeskIdFromPath();
  document.getElementById('desk-root').dataset.deskId = deskId;
  document.getElementById('desk-retry').addEventListener('click', () => loadDesk(deskId));
  wireUserThesisForm(deskId);
  wireRefresh(deskId);
  loadDesk(deskId);
}

wireDiscreetToggle();
if (document.getElementById('desks-grid')) initGridPage();
if (document.getElementById('desk-root')) initDeskPage();
