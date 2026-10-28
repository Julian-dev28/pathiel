/* oil_desk.js — the Desk page ("/"), the one screen this dashboard is now
 * built around: the active desk's call, wallets, levels, headlines and
 * catalysts, loop log, track record. Also carries the onboarding wizard
 * (ported from the old landing.html, 2026-10-23): a brand-new visitor
 * connects an account and picks a risk preset before ever seeing this
 * page's real content.
 *
 * Generic over desk id (docs/overhaul/DESK_UI_2026-10-23.md): oil is the
 * only ACTIVE desk today, not the identity. GET /api/desks/active says
 * which desk(s) to render (agent config active_desks, default ["oil"]) —
 * one active desk shows no switcher, several show a small tab row. Every
 * fetch below is against `activeDesk`, never a hardcoded "oil".
 *
 * Loaded after pathiel.js (esc, fmtUsd, fmtAddUsd, RISK_PRESETS,
 * matchesPresetDefaults, reportRefreshFailure, pollEvery, PathielDesk,
 * PathielSession already in scope) and after desks.js (pctSigned,
 * pctUnsigned, fmtMoney, fmtPrice, fmtCountdown, kindLabel, setDollarField,
 * wireDiscreetToggle, VERDICT_CLASS, SIDE_CLASS already in scope — desks.js's
 * own page-init calls at its bottom are no-ops here since #desks-grid/
 * #desk-root do not exist on this page).
 *
 * Every Desk section loads and refreshes independently: one source failing
 * shows that section's own error state and never blanks the rest of the
 * page. Refreshes every 30s, per the product spec.
 */

const REFRESH_MS = 60_000;   // 30s starved the venue budget (2026-10-23)
const LEVERAGE_CHOICES = [5, 10, 20];
const DEFAULT_LEVERAGE = 20;
const INTEL_CAP = 6;   // small tables, per the ADHD-first brief

let activeDesk = 'oil';
let activeDesks = ['oil'];

function show(id, on) {
  const el = document.getElementById(id);
  if (el) el.hidden = !on;
}

function setErrorDetail(prefix, err) {
  show(`${prefix}-error`, true);
  const detail = document.getElementById(`${prefix}-error-detail`);
  if (detail) detail.textContent = String((err && err.message) || err);
}

async function fetchJson(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = `http ${r.status}`;
    try {
      const body = await r.json();
      if (body && body.detail) detail = body.detail;
    } catch { /* not JSON, keep the status */ }
    throw new Error(detail);
  }
  return r.json();
}

// ── 1. THE CALL ──────────────────────────────────────────────────────────────

async function loadCall() {
  show('call-loading', true); show('call-error', false); show('call-card', false);
  try {
    const brief = await fetchJson(`/api/desks/${encodeURIComponent(activeDesk)}`);
    show('call-loading', false);
    const deskName = (brief.spec && brief.spec.name) || activeDesk;
    const titleEl = document.getElementById('desk-page-title');
    if (titleEl) titleEl.textContent = deskName;
    renderCallCard(brief, deskName);
    show('call-card', true);
    // The stale note is for the parts of the brief that are older than their
    // TTL; an intel source that failed is noted as such, not as "stale: ...".
    const stale = (brief.stale || []).map(s => s.replace(/^intel: (\d+) source\(s\) failed this refresh.*$/, 'intel: $1 source(s) down'));
    document.getElementById('call-stale-note').textContent = stale.join(' · ');
    renderTrackRecord(brief.track_record);
    renderIntel(brief);
    lastThesisCall = (brief.thesis && brief.thesis.call) || null;
    renderPositionMismatch();
  } catch (e) {
    show('call-loading', false);
    setErrorDetail('call', e);
    reportRefreshFailure('oil desk call', e, document.getElementById('call-error'));
    renderIntel(null, e);
    lastThesisCall = null;
    renderPositionMismatch();
  }
}

// The thesis's own numbers (entry/stop/leverage/side) are written at most
// once an hour; the venue's own position (fetched by loadWallets below) can
// move at any time — a hand trade, a bracket_sweep, a stop getting hit and
// re-placed. Printing the thesis's stop/entry as if it were the live order
// is how a trader gets hurt (2026-10-24: thesis said "long Brent 109.24 at
// 5x, stop 108.05" while the actual position was 111.57 at 10x, stop 110.9
// — same side, same instrument, three numbers off).
//
// lastThesisCall / lastPositionsByCoin are filled by loadCall() and
// loadWallets() respectively (they run in parallel inside loadAll(), so
// either can resolve first); renderPositionMismatch() is called from both
// and only reads the two module-level vars, never fetches anything itself.
let lastThesisCall = null;
let lastPositionsByCoin = {};

// position_line.js, loaded just before this file. A server still serving an
// older cached oil_desk.html loads this file without it: fall back to the
// call's own numbers instead of a ReferenceError that blanks the card.
const PositionLine = window.PathielPositionLine
  || { positionForCall: () => null, positionLine: () => null };

const MISMATCH_PX_TOL = 0.003; // 0.3% — past this, "entry 109.24" is not "entry 111.57"

/** Pure comparison, no DOM: null when the two agree (or there is nothing to
 *  compare), otherwise a plain-language description of what differs. */
function callPositionMismatch(call, position) {
  if (!call || !position) return null;
  if (!['long', 'short'].includes(call.side)) return null;
  if (call.side !== position.side) {
    return `Thesis says ${call.side}; your open position is ${position.side}.`;
  }
  const diffs = [];
  const pxDiff = (a, b) => a != null && b != null && isFinite(a) && isFinite(b)
    && Math.abs(a - b) / Math.abs(b) > MISMATCH_PX_TOL;
  if (pxDiff(call.entry_px, position.entry_px)) {
    diffs.push(`entry ${fmtPrice(call.entry_px)} vs your ${fmtPrice(position.entry_px)}`);
  }
  if (call.leverage != null && position.leverage != null && Number(call.leverage) !== Number(position.leverage)) {
    diffs.push(`${call.leverage}x vs your ${position.leverage}x`);
  }
  const posStop = Array.isArray(position.stop_px) && position.stop_px.length ? Number(position.stop_px[0]) : null;
  if (pxDiff(call.stop_px, posStop)) {
    diffs.push(`stop ${fmtPrice(call.stop_px)} vs your ${fmtPrice(posStop)}`);
  }
  if (!diffs.length) return null;
  return `This call has not caught up with your open position (${diffs.join(', ')}). `
    + `The 20x box below and the Wallets card show the live position; this text is the plan from before the fill.`;
}

function renderPositionMismatch() {
  const el = document.getElementById('call-position-mismatch');
  if (!el) return;
  const coin = lastThesisCall && lastThesisCall.instrument;
  const position = coin ? lastPositionsByCoin[coin] : null;
  const message = callPositionMismatch(lastThesisCall, position);
  el.textContent = message || '';
  el.hidden = !message;
  renderCallLeverage();
  renderLine();
}

/** The call card's Leverage row: the position's own leverage once one is
 *  open on the call's coin (2026-10-28: the row read "5x" while the account
 *  ran 20x), the thesis's text otherwise. */
let lastLeverageText = null;
function renderCallLeverage() {
  const el = document.getElementById('call-leverage');
  if (!el || lastLeverageText === null) return;
  const p = PositionLine.positionForCall(lastThesisCall, lastPositionsByCoin);
  const callLev = lastThesisCall && lastThesisCall.leverage;
  el.textContent = p && p.leverage != null
    ? `${p.leverage}x, your open position${callLev != null && Number(callLev) !== Number(p.leverage) ? ` (the call said ${callLev}x)` : ''}`
    : lastLeverageText;
}

function fmtR(entries) {
  if (!entries || !entries.length) return '—';
  return entries.map(t => `${Number(t.r_multiple).toFixed(2)}R`).join(', ');
}

const DECISION_CLASS = { HOLD: 'b-hold', DIP: 'b-dip-only', 'DIP_ONLY': 'b-dip-only', WAIT: 'b-flat',
                         WATCH: 'b-flat', AVOID: 'b-avoid', 'INTRADAY_ONLY': 'b-intraday-only' };

/** The call as a card: sentence, side and decision badges, a key/value row,
 *  and the invalidation. Falls back to the quick text when a brief carries
 *  no structured fields (an old server, or no thesis yet). */
function renderCallCard(brief, deskName) {
  const f = brief.quick_fields;
  const structured = document.getElementById('call-meta');
  const pre = document.getElementById('call-markdown');
  const set = (id, text) => { document.getElementById(id).textContent = text; };
  if (!f) {
    structured.hidden = true;
    document.getElementById('call-sentence').hidden = true;
    document.querySelector('#call-card .call-top').hidden = true;
    document.querySelector('#call-card .call-invalidation').hidden = true;
    pre.hidden = false;
    pre.textContent = brief.quick_markdown || `No call yet on the ${deskName} desk. Its loop writes one on its next tick.`;
    return;
  }
  pre.hidden = true;
  structured.hidden = false;
  document.getElementById('call-sentence').hidden = false;
  document.querySelector('#call-card .call-top').hidden = false;
  document.querySelector('#call-card .call-invalidation').hidden = false;
  set('call-sentence', f.call || '');
  document.getElementById('call-side-badge').innerHTML =
    f.side ? `<span class="badge ${SIDE_CLASS[f.side] || 'b-flat'}">${esc(f.side)}</span>` : '';
  document.getElementById('call-decision-badge').innerHTML =
    f.decision ? `<span class="badge ${DECISION_CLASS[f.decision] || 'b-flat'}">${esc(f.decision)}</span>` : '';
  set('call-instrument', f.instrument || '');
  lastLeverageText = f.leverage_text || '—';
  set('call-leverage', lastLeverageText);
  renderCallLeverage();
  set('call-pays', f.pays ? `${Number(f.pays.r).toFixed(1)}R to ${f.pays.target}` : '—');
  set('call-risk', f.biggest_risk ? f.biggest_risk.text : 'none named');
  set('call-catalyst', f.next_catalyst ? `${f.next_catalyst.title} · ${fmtCountdown(f.next_catalyst.at)}` : 'none scheduled');
  set('call-out-if', f.out_if || '—');
}

let currentLeverage = DEFAULT_LEVERAGE;

function renderLevSeg() {
  const seg = document.getElementById('line-seg');
  seg.innerHTML = LEVERAGE_CHOICES.map(lev => `
    <span class="lev-radio-wrap">
      <input type="radio" name="line-leverage-choice" class="lev-radio" id="line-opt-${lev}"
             value="${lev}" ${lev === currentLeverage ? 'checked' : ''}>
      <label for="line-opt-${lev}" class="lev-radio-label">${lev}x</label>
    </span>`).join('');
  seg.querySelectorAll('.lev-radio').forEach(radio => {
    radio.addEventListener('change', () => {
      if (radio.checked) { currentLeverage = Number(radio.value); loadLine(); }
    });
  });
}

// The call's line from the server (null until loaded, {error} on a failed
// load). renderLine() paints the box from the LIVE position instead whenever
// one is open on the call's coin (position_line.js), so the box follows the
// ticks: entry, leverage, the venue's liquidation price and the resting
// stop and targets, not the thesis's plan from before the fill.
let lastCallLine = null;

async function loadLine() {
  try {
    lastCallLine = await fetchJson(`/api/desks/${encodeURIComponent(activeDesk)}/line?leverage=${encodeURIComponent(currentLeverage)}`);
  } catch (e) {
    lastCallLine = { error: e };
  }
  renderLine();
}

function renderLine() {
  const position = PositionLine.positionForCall(lastThesisCall, lastPositionsByCoin);
  const live = PositionLine.positionLine(position);
  const seg = document.getElementById('line-seg');
  if (seg) seg.hidden = !!live;            // the leverage choice prices a call, not a filled position
  if (live) { paintLine(live, true); return; }
  const line = lastCallLine;
  if (!line) return;
  if (line.error) {
    show('line-card', false); show('line-empty', true);
    document.getElementById('line-empty-detail').textContent = 'Could not load the leverage line: ' + line.error.message;
    reportRefreshFailure('oil desk line', line.error, document.getElementById('line-empty'));
    return;
  }
  if (!line.ok && !line.verdict) {
    // No line to show at all (no call yet, no entry/stop, no venue data) —
    // the reason is the whole story here.
    show('line-card', false); show('line-empty', true);
    document.getElementById('line-empty-detail').textContent = line.reason || 'No 20x line to show.';
    return;
  }
  paintLine(line, false);
}

function paintLine(line, isPosition) {
  show('line-empty', false); show('line-card', true);
  const lev = Number(line.leverage || currentLeverage);
  document.getElementById('line-liq-label').textContent =
    isPosition ? `Your position: liquidation at ${lev}x` : `Liquidation at ${lev}x`;
  document.getElementById('line-liq-px').textContent = fmtPrice(line.liq_px);
  document.getElementById('line-stop-frac').textContent = pctUnsigned(line.stop_frac_of_liq);
  document.getElementById('line-entry').textContent = fmtPrice(line.entry_px);
  document.getElementById('line-stop').textContent = fmtPrice(line.stop_px);
  document.getElementById('line-r-targets').textContent = fmtR(line.r_to_targets);
  // The verdict in words a trader reads at a glance: "20x OK" from the
  // server means the stop fires well before the exchange would liquidate.
  const badgeClass = line.ok ? 'b-hold' : 'b-avoid';
  const badgeText = line.no_stop ? 'NO STOP on the open position'
    : line.ok ? `Stop clears ${lev}x liquidation` : `Stop too close for ${lev}x`;
  document.getElementById('line-verdict-badge').innerHTML = `<span class="badge ${badgeClass}">${esc(badgeText)}</span>`;
  const gate = line.r_gate;
  const what = isPosition ? 'your open position' : 'it';
  const explain = (line.liq_px != null && line.stop_frac_of_liq != null)
    ? `At ${lev}x the exchange liquidates ${what} at ${fmtPrice(line.liq_px)}; the stop at ${fmtPrice(line.stop_px)} uses ${pctUnsigned(line.stop_frac_of_liq)} of that distance (limit 50%).`
    : '';
  const source = isPosition ? 'Live from the venue, not the call.' : '';
  document.getElementById('line-reason').textContent =
    [explain, gate && !gate.ok ? (gate.reason || '') : '', source].filter(Boolean).join(' ');
}

// ── 2. WALLETS ───────────────────────────────────────────────────────────────

// Unfilled entry orders, with the stop and target place_bracket attached.
// 2026-10-25: a resting NVDA bracket held the account's one slot for hours
// while this page said "No open positions"; the operator had nowhere to see
// it. A resting entry is not a position, but it is money reserved and a slot
// taken, so it gets its own table.
function fmtUtcTime(ms) {
  if (!ms) return '—';
  const d = new Date(Number(ms));
  return isNaN(d.getTime()) ? '—' : d.toISOString().slice(11, 16) + 'Z';
}

function renderRestingEntries(resting) {
  const rows = resting.map(o => `
    <tr>
      <td>${esc(o.dex)}</td>
      <td>${esc(o.coin)}</td>
      <td><span class="badge ${SIDE_CLASS[o.side] || ''}">${esc(o.side)}</span></td>
      <td class="num">${esc(o.size)}</td>
      <td class="num">${fmtPrice(o.limit_px)}</td>
      <td class="num">${fmtPrice(o.mark_px)}</td>
      <td class="num">${o.to_fill_pct != null ? (o.to_fill_pct > 0 ? '+' : '') + o.to_fill_pct.toFixed(2) + '%' : '—'}</td>
      <td>${(o.stop_px || []).map(fmtPrice).join(', ') || '—'}</td>
      <td>${(o.tp_px || []).map(fmtPrice).join(', ') || '—'}</td>
      <td class="num">${fmtMoney(o.notional_usd)}</td>
      <td>${fmtUtcTime(o.placed_at_ms)}</td>
      <td>${o.covered ? '' : '<span class="badge b-fail">NO STOP</span>'}</td>
    </tr>`).join('');
  return `<div class="sec-label" style="margin-top:12px">Resting ${resting.length === 1 ? 'entry' : 'entries'} (not filled)</div>
    <div class="scroll-x"><table>
      <thead><tr><th>Dex</th><th>Coin</th><th>Side</th><th class="num">Size</th><th class="num">Limit</th><th class="num">Mark</th><th class="num">Limit vs mark</th>
        <th>Stop</th><th>Target</th><th class="num">Notional</th><th>Placed</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function renderWalletAccount(account, snapshot) {
  const equity = (snapshot.accounts || []).reduce((sum, a) => sum + (a.equity_usd || 0), 0);
  const marginUsed = (snapshot.accounts || []).reduce((sum, a) => sum + (a.margin_used_usd || 0), 0);
  const positions = (snapshot.accounts || []).flatMap(a => (a.positions || []).map(p => ({ ...p, dex: a.dex })));
  const resting = (snapshot.accounts || []).flatMap(a => (a.resting_entries || []).map(o => ({ ...o, dex: a.dex })));
  const stranded = (snapshot.accounts || []).reduce((n, a) => n + (a.stranded_orders || 0), 0);
  const naked = snapshot.any_naked;
  // A watch-only wallet earns a card only while it holds something: flat, it
  // is noise next to the trading account (operator, 2026-10-24). It reappears
  // with the NAKED check the moment a hand-placed position shows up.
  if (account.mode === 'watch' && !positions.length && !resting.length && !(snapshot.errors || []).length) return '';
  const restingBlock = resting.length ? renderRestingEntries(resting) : '';
  const strandedNote = stranded
    ? `<div class="note warn">${stranded} reduce-only order${stranded === 1 ? '' : 's'} with no position behind ${stranded === 1 ? 'it' : 'them'}; the next tick's bracket_sweep cancels ${stranded === 1 ? 'it' : 'them'}.</div>` : '';
  // Equity, margin tied up in positions, and what is still free to deploy.
  const marginLine = `<div class="note">Equity ${esc(fmtMoney(equity))} · Margin in use ${esc(fmtMoney(marginUsed))}`
    + (equity > 0 ? ` (${Math.round(marginUsed / equity * 100)}%)` : '')
    + ` · Free ${esc(fmtMoney(Math.max(0, equity - marginUsed)))}</div>`;

  const head = `
    <div class="dc-head">
      <span class="dc-name">${esc(account.label)}</span>
      ${account.is_active ? '<span class="badge b-live">Active</span>' : ''}
      <span class="badge ${account.mode === 'watch' ? 'b-flat' : 'b-user'}">${esc(account.mode)}</span>
      ${naked ? '<span class="badge b-fail">NAKED</span>' : ''}
    </div>
    <div class="dc-call">${esc(account.main_address)}</div>`;

  // A dex the venue did not answer is reported, never shown as flat; a
  // snapshot served from the last good read says how old it is.
  const failed = (snapshot.errors || []).length
    ? `<div class="note warn">Venue read failed on ${esc(snapshot.errors.join(', '))}; positions there are unknown this refresh.</div>` : '';
  const stale = snapshot.stale_s != null
    ? `<div class="note muted">Last good read ${Math.round(snapshot.stale_s)}s ago; the venue has not answered since.</div>` : '';

  if (!positions.length) {
    const flatNote = resting.length
      ? `<div class="note">Equity ${esc(fmtMoney(equity))}. No open position; the resting ${resting.length === 1 ? 'entry' : 'entries'} below ${resting.length === 1 ? 'holds' : 'hold'} the account's one slot until it fills or is cancelled.</div>`
      : `<div class="note">Equity ${esc(fmtMoney(equity))}. No open positions.</div>`;
    const body = failed ? failed : flatNote;
    return `<div class="card" style="margin-bottom:14px">${head}${body}${strandedNote}${restingBlock}${stale}</div>`;
  }

  // uPnL in dollars and ROE % in its own column, the way Hyperliquid
  // prints "+$21.74 (+30.4%)": ROE is the return on the margin, i.e. the
  // price move times leverage, not the 0.3% the price itself moved
  // (operator 2026-10-28). roe_pct comes from the venue's returnOnEquity; a
  // server that does not send it yet gets price move x leverage here.
  const fmtUsd = usd => (usd == null || !isFinite(usd)) ? '—'
    : (usd >= 0 ? '+$' : '-$') + Math.abs(usd).toFixed(2);
  const roeOf = p => p.roe_pct != null ? Number(p.roe_pct)
    : (p.pnl_pct != null && p.leverage != null ? Number(p.pnl_pct) * Number(p.leverage) : null);
  const fmtRoe = roe => (roe == null || !isFinite(roe)) ? '—' : `${roe >= 0 ? '+' : ''}${roe.toFixed(1)}%`;
  const tone = v => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  const rows = positions.map(p => `
    <tr>
      <td>${esc(p.dex)}</td>
      <td>${esc(p.coin)}</td>
      <td><span class="badge ${SIDE_CLASS[p.side] || ''}">${esc(p.side)}</span></td>
      <td class="num">${esc(p.size)}</td>
      <td class="num">${fmtPrice(p.entry_px)}</td>
      <td class="num">${fmtPrice(p.mark_px)}</td>
      <td class="num ${tone(p.upnl_usd)}">${fmtUsd(p.upnl_usd)}</td>
      <td class="num ${tone(roeOf(p))}">${fmtRoe(roeOf(p))}</td>
      <td class="num">${p.r_now != null ? (p.r_now > 0 ? '+' : '') + p.r_now.toFixed(2) + 'R' : '—'}</td>
      <td class="num">${p.leverage != null ? p.leverage + 'x' : '—'}</td>
      <td class="num">${p.margin_usd != null ? fmtMoney(p.margin_usd) : '—'}</td>
      <td class="num">${fmtPrice(p.liq_px)}</td>
      <td class="num">${pctUnsigned(p.liq_dist_pct != null ? p.liq_dist_pct / 100 : null)}</td>
      <td>${(p.stop_px || []).map(fmtPrice).join(', ') || '—'}${p.to_stop_pct != null ? ` <span class="muted">(${p.to_stop_pct.toFixed(2)}% away)</span>` : ''}</td>
      <td>${(p.tp_px || []).map(fmtPrice).join(', ') || '—'}${p.to_tp_pct != null ? ` <span class="muted">(${p.to_tp_pct.toFixed(2)}% away)</span>` : ''}</td>
      <td>${p.naked ? '<span class="badge b-fail">NAKED</span>' : ''}</td>
    </tr>`).join('');

  return `<div class="card" style="margin-bottom:14px">${head}${marginLine}${failed}${stale}
    <div class="scroll-x"><table>
      <thead><tr><th>Dex</th><th>Coin</th><th>Side</th><th class="num">Size</th><th class="num">Entry</th><th class="num">Mark</th><th class="num">P&amp;L</th><th class="num">ROE %</th><th class="num">R now</th>
        <th class="num">Lev</th><th class="num">Margin</th><th class="num">Liq</th><th class="num">Dist</th>
        <th>Stop</th><th>Target</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>${strandedNote}${restingBlock}</div>`;
}

async function loadWallets() {
  show('wallets-loading', true); show('wallets-error', false); show('wallets-empty', false);
  const list = document.getElementById('wallets-list');
  try {
    const accounts = await fetchJson('/api/accounts');
    show('wallets-loading', false);
    if (!accounts.length) {
      list.innerHTML = ''; show('wallets-empty', true);
      lastPositionsByCoin = {}; renderPositionMismatch();
      return;
    }
    const byCoin = {};
    const blocks = await Promise.all(accounts.map(async account => {
      try {
        const snapshot = await fetchJson(`/api/accounts/${encodeURIComponent(account.id)}/positions`);
        // The active trade account's own position is the one the call
        // should match; a watch account's position (if any) only fills in
        // where the active account has none, never overrides it.
        for (const a of (snapshot.accounts || [])) {
          for (const p of (a.positions || [])) {
            if (!byCoin[p.coin] || account.is_active) byCoin[p.coin] = p;
          }
        }
        return renderWalletAccount(account, snapshot);
      } catch (e) {
        return `<div class="card" style="margin-bottom:14px">
          <div class="dc-head"><span class="dc-name">${esc(account.label)}</span></div>
          <div class="note">Could not read this account's positions: ${esc(e.message)}</div></div>`;
      }
    }));
    list.innerHTML = blocks.join('');
    lastPositionsByCoin = byCoin;
    renderPositionMismatch();
  } catch (e) {
    show('wallets-loading', false);
    list.innerHTML = '';
    setErrorDetail('wallets', e);
    reportRefreshFailure('wallets', e, document.getElementById('wallets-error'));
    lastPositionsByCoin = {};
    renderPositionMismatch();
  }
}

// ── 3. LEVELS ────────────────────────────────────────────────────────────────

async function loadLevels() {
  show('levels-loading', true); show('levels-error', false);
  try {
    const l = await fetchJson(`/api/desks/${encodeURIComponent(activeDesk)}/levels`);
    show('levels-loading', false);
    const stats = document.getElementById('levels-stats');
    const last = l.last_closed_1h;
    stats.innerHTML = `
      <div><div class="sec-label">Mid</div><div class="kv">${fmtPrice(l.mid)}</div></div>
      <div><div class="sec-label">Last closed 1h</div><div class="kv">${last ? fmtPrice(last.close) : '—'}</div></div>
      <div><div class="sec-label">8h range</div><div class="kv">${l.range_8h ? fmtPrice(l.range_8h.low) + ' – ' + fmtPrice(l.range_8h.high) : '—'}</div></div>
      <div><div class="sec-label">24h range</div><div class="kv">${l.range_24h ? fmtPrice(l.range_24h.low) + ' – ' + fmtPrice(l.range_24h.high) : '—'}</div></div>
      <div><div class="sec-label">48h range</div><div class="kv">${l.range_48h ? fmtPrice(l.range_48h.low) + ' – ' + fmtPrice(l.range_48h.high) : '—'}</div></div>
      <div><div class="sec-label">ATR14 (1h)</div><div class="kv">${l.atr14_1h != null ? fmtPrice(l.atr14_1h) + ' (' + pctUnsigned(l.atr14_pct != null ? l.atr14_pct / 100 : null) + ')' : '—'}</div></div>
      <div><div class="sec-label">Mid position in 24h range</div><div class="kv">${l.mid_pos_in_24h != null ? pctUnsigned(l.mid_pos_in_24h) : '—'}</div></div>`;
    show('levels-stats', true);
  } catch (e) {
    show('levels-loading', false);
    show('levels-stats', false);
    setErrorDetail('levels', e);
    reportRefreshFailure('oil levels', e, document.getElementById('levels-error'));
  }
}

// ── 4. HEADLINES AND CATALYSTS ───────────────────────────────────────────────
// Driven by the same DeskBrief loadCall() already fetched — no second
// network call. The intel the thesis already carries (contracts/strategies
// /models.py: IntelPacket), capped to a small table per the ADHD-first brief.

const STANCE_CHIP = { bull: 'chip-green', bear: 'chip-red', neutral: 'chip-amber' };

/** "3h ago" / "2d ago" for a headline's published_at; '' when unparseable. */
function fmtAge(iso) {
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 60) return `${m}m ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
}

/** A failed-source key like "policy:ofac:timeout" or
 *  "news.fetch_gdelt:timeout_budget_exceeded" -> "ofac timeout". */
function failedSourceLabel(key) {
  const parts = String(key).split(':');
  const src = (parts.length > 1 ? parts[1] : parts[0]).replace(/^fetch_/, '').replace(/_/g, ' ');
  const why = parts.length > 2 ? parts.slice(2).join(' ').replace(/_/g, ' ') : (parts.length > 1 ? '' : '');
  return [src, why].filter(Boolean).join(' ');
}

/** One line under the headlines: packet age, headline count, the feeds it
 *  came from (top six by count), and every feed that failed this build in
 *  red. The six headlines above are the top of a list this line sizes. */
function renderIntelSources(intel, heads) {
  if (!intel) return '';
  const counts = {};
  for (const h of heads) {
    const s = h.source || 'no source';   // never the word "unknown" on the page
    counts[s] = (counts[s] || 0) + 1;
  }
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const top = ranked.slice(0, 6).map(([s, n]) => `<span class="src-chip" title="${esc(s)}">${esc(s)} <b>${n}</b></span>`).join('');
  const more = ranked.length > 6 ? `<span class="src-more">+${ranked.length - 6} more</span>` : '';
  const failed = (intel.sources_failed || []).map(f => `<span class="chip chip-red" title="${esc(f)}">${esc(failedSourceLabel(f))}</span>`).join('');
  const age = intel.as_of ? fmtAge(intel.as_of) : '';
  const lead = `${heads.length} headline${heads.length === 1 ? '' : 's'} from ${ranked.length} source${ranked.length === 1 ? '' : 's'}${age ? ` · packet ${age}` : ''}`;
  const failLead = failed ? `<span class="src-failed-label">failed:</span>${failed}` : `<span class="src-ok">all feeds answered</span>`;
  return `<div class="src-line"><span class="src-lead">${esc(lead)}</span>${top}${more}</div><div class="src-line">${failLead}</div>`;
}

function renderIntel(brief, loadError) {
  const chips = document.getElementById('catalyst-chips');
  const headlines = document.getElementById('headlines-list');
  const odds = document.getElementById('odds-list');
  const sources = document.getElementById('intel-sources');
  show('intel-loading', false); show('intel-error', false); show('intel-empty', false);

  if (loadError) {
    chips.innerHTML = ''; headlines.innerHTML = ''; odds.innerHTML = ''; sources.innerHTML = '';
    setErrorDetail('intel', loadError);
    return;
  }

  const intel = brief && brief.intel;
  const catalysts = (brief && brief.thesis && brief.thesis.catalysts) || (intel && intel.catalysts) || [];
  const heads = (intel && intel.headlines) || [];
  const marketOdds = (intel && intel.odds) || [];

  if (!catalysts.length && !heads.length && !marketOdds.length) {
    chips.innerHTML = ''; headlines.innerHTML = ''; odds.innerHTML = ''; sources.innerHTML = '';
    show('intel-empty', true);
    return;
  }

  chips.innerHTML = [...catalysts].sort((a, b) => new Date(a.at) - new Date(b.at)).slice(0, INTEL_CAP).map(c => `
    <span class="catalyst-chip${c.importance >= 3 ? ' imp-3' : ''}" title="${esc(c.source || '')}">
      <span class="catalyst-kind">${esc(kindLabel(c.kind))}</span>
      <span class="catalyst-when">${esc(fmtCountdown(c.at))}</span>
    </span>`).join('');

  // A headline carries a stance chip only when the intel layer tagged one;
  // an untagged headline shows its source and age, never the word "unknown"
  // (2026-10-24: every row read "unknown" because stance is not tagged yet).
  headlines.innerHTML = [...heads].sort((a, b) => (b.relevance || 0) - (a.relevance || 0)).slice(0, INTEL_CAP).map(h => `
    <div class="headline-row">
      ${h.stance && h.stance !== 'unknown' ? `<span class="chip ${STANCE_CHIP[h.stance] || ''}">${esc(h.stance)}</span>` : ''}
      <a class="headline-title" href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title)}</a>
      <span class="headline-meta">${esc([h.source, h.published_at ? fmtAge(h.published_at) : ''].filter(Boolean).join(' · '))}</span>
    </div>`).join('');

  sources.innerHTML = renderIntelSources(intel, heads);

  odds.innerHTML = [...marketOdds].sort((a, b) => (b.volume_usd || 0) - (a.volume_usd || 0)).slice(0, INTEL_CAP).map(o => `
    <div class="odds-row">
      <span class="odds-q">${esc(o.question)}</span>
      <span class="odds-p">${pctUnsigned(o.probability)}</span>
    </div>`).join('');
}

// ── 5. LOOP LOG ──────────────────────────────────────────────────────────────

async function loadLog() {
  const body = document.getElementById('log-body');
  try {
    const r = await fetchJson(`/api/desks/${encodeURIComponent(activeDesk)}/log?n=30`);
    body.textContent = (r.lines && r.lines.length) ? r.lines.join('\n') : 'no ticks yet';
  } catch (e) {
    body.textContent = 'could not read the loop log: ' + e.message;
  }
}

// ── 6. TRACK RECORD ──────────────────────────────────────────────────────────

// Two sources, one card. The thesis columns (graded, hit rate, return,
// Brier) grade CALLS at their 7-day horizon. The fill columns count what the
// account actually did: every fill on closed thesis windows plus the open
// window summed live (TrackRecord.open_window), so a stop that fired this
// hour is on the page this hour, not when the thesis expires (2026-10-25).
function fmtUsdSigned(v) {
  if (v == null || !isFinite(Number(v))) return '—';
  const n = Number(v);
  return (n < 0 ? '-' : '+') + '$' + Math.abs(n).toFixed(2);
}

function renderTrackRecord(tr) {
  const cols = document.getElementById('track-cols');
  if (!tr || (!tr.n_graded && !tr.n_trades)) {
    cols.innerHTML = '';
    show('track-empty', true);
    return;
  }
  show('track-empty', false);
  const ow = tr.open_window;
  const liveNote = (ow && ow.n_fills)
    ? `<div class="ksub">${ow.n_fills} fill${ow.n_fills === 1 ? '' : 's'} on the open call</div>` : '';
  const pnl = Number(tr.realized_pnl_usd);
  const pnlClass = (tr.realized_pnl_usd == null || !isFinite(pnl)) ? '' : (pnl < 0 ? 'neg' : 'pos');
  cols.innerHTML = `
    <div class="track-col"><div class="sec-label">Graded</div><div class="kv">${tr.n_graded || 0}</div></div>
    <div class="track-col"><div class="sec-label">Direction hit rate</div><div class="kv">${tr.n_graded ? pctUnsigned(tr.direction_hit_rate) : '—'}</div></div>
    <div class="track-col"><div class="sec-label">Mean return</div><div class="kv">${tr.n_graded ? pctSigned(tr.mean_ret_pct) : '—'}</div></div>
    <div class="track-col"><div class="sec-label">Mean Brier</div><div class="kv">${tr.mean_brier != null ? Number(tr.mean_brier).toFixed(3) : '—'}</div></div>
    <div class="track-col"><div class="sec-label">Fills</div><div class="kv">${tr.n_trades || 0}</div></div>
    <div class="track-col"><div class="sec-label">Realized P&amp;L</div><div class="kv ${pnlClass}">${fmtUsdSigned(tr.realized_pnl_usd)}</div>${liveNote}</div>`;
}

// The old auto-trader kill switch and the operator-token popover left with
// the retired bot (2026-10-24): the page has no token-gated action any more,
// and local reads need no token (services/auth/integration.is_local_mode).

// ── the Desk page itself ─────────────────────────────────────────────────────

let pollStarted = false;

/** Which desk(s) to render (GET /api/desks/active). One entry -> no
 *  switcher; several -> a small tab row, switching re-fetches every
 *  section for the clicked desk. Also repoints the Thesis nav link and
 *  "Full thesis" link at the active desk, and the page's H1 at its name.
 *
 *  Prefers the reader's last-selected desk (localStorage, via PathielDesk)
 *  over activeDesks[0] so a reload keeps the desk they were actually
 *  looking at, falling back to the first active desk only when nothing is
 *  stored yet or the stored id is no longer active. */
async function loadActiveDesks() {
  try {
    const d = await fetchJson('/api/desks/active');
    activeDesks = (d.active_desks && d.active_desks.length) ? d.active_desks : ['oil'];
  } catch (e) {
    activeDesks = ['oil'];
  }
  const stored = PathielDesk.get();
  activeDesk = (stored && activeDesks.includes(stored)) ? stored : activeDesks[0];
  PathielDesk.set(activeDesk);
  renderDeskSwitcher();
  repointDeskLinks();
}

function repointDeskLinks() {
  PathielDesk.repointThesisLink();
  const thesisHref = `/desk/${encodeURIComponent(activeDesk)}`;
  const fullThesisLink = document.getElementById('full-thesis-link');
  if (fullThesisLink) fullThesisLink.href = thesisHref;
}

function renderDeskSwitcher() {
  const seg = document.getElementById('desk-switcher');
  if (!seg) return;
  if (activeDesks.length < 2) { seg.hidden = true; seg.innerHTML = ''; return; }
  seg.hidden = false;
  seg.innerHTML = activeDesks.map(id => `
    <span class="lev-radio-wrap">
      <input type="radio" name="active-desk-choice" class="lev-radio" id="desk-opt-${esc(id)}"
             value="${esc(id)}" ${id === activeDesk ? 'checked' : ''}>
      <label for="desk-opt-${esc(id)}" class="lev-radio-label">${esc(id)}</label>
    </span>`).join('');
  seg.querySelectorAll('.lev-radio').forEach(radio => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      activeDesk = radio.value;
      PathielDesk.set(activeDesk);
      repointDeskLinks();
      loadAll();
    });
  });
}

// Returns the combined promise so pollEvery (pathiel.js) only schedules the
// next run once every section has actually settled — never stacking polls
// behind a slow account read, and pausing while the tab is hidden. Same
// convention every page's own refresh loop uses; see
// tests/test_page_polling.py.
async function loadAll() {
    if (document.hidden) return;   // a hidden tab must not spend the venue budget
  await Promise.all([loadCall(), loadLine(), loadWallets(), loadLevels(), loadLog()]);
}

async function startOilDesk() {
  if (pollStarted) return;
  pollStarted = true;
  await loadActiveDesks();
  loadAll();                     // once immediately — pollEvery's own first
  pollEvery(loadAll, REFRESH_MS); // tick fires only after the first REFRESH_MS
  // This page's own <script type="speculationrules"> prerenders same-origin
  // links at "moderate" eagerness, so a visit here often starts life as a
  // prerendered, hidden document: that first loadAll() call above (and the
  // guard inside loadAll() itself) correctly does nothing rather than spend
  // the venue budget on a page nobody may ever look at. Reproduced live,
  // 2026-10-24: a background tab never fired a single /api request and
  // every section sat on "Loading..." forever. The reason: a prerendered
  // page's activation does not reliably fire 'visibilitychange' (a
  // documented Chrome/Speculation-Rules gotcha — see
  // web.dev/articles/speculation-rules#prerenderingchange) so the listener
  // below, on its own, never ran for that page. 'prerenderingchange' is the
  // event the spec actually guarantees on activation; listening for both
  // covers a normal hidden-tab resume (visibilitychange) and a prerender
  // activation (prerenderingchange) the same way.
  const resumeIfVisible = () => { if (!document.hidden) loadAll(); };
  document.addEventListener('visibilitychange', resumeIfVisible);
  document.addEventListener('prerenderingchange', resumeIfVisible);
}

// ── Onboarding: first run + the "how much risk" wizard ──────────────────────
// Ported from the old landing.html verbatim (2026-10-23), minus the
// per-book feasibility preview (books are gone): one decision per screen,
// the call first. needs_onboarding (GET /api/session) means no Hyperliquid
// account is connected at all; the wizard walks a first connect through the
// risk preset before ever showing the Oil Desk itself. After step 3, THIS
// SAME PAGE becomes the Oil Desk — no navigation.
(function () {
  const root = document.getElementById('onboarding-root');
  const main = document.getElementById('oil-desk-root');
  if (!root || !main) return;

  // Set while mid-wizard past step 1, so a reload before Save resumes the
  // risk step instead of snapping back to the (still-empty) Oil Desk —
  // needs_onboarding itself already flipped false the moment the account
  // connected, so it alone can't tell step 2 apart from "done".
  const RESUME_KEY = 'pathiel-onboarding-step';
  const steps = Array.from(root.querySelectorAll('.onboard-step'));

  function showStep(n) { steps.forEach(el => { el.hidden = el.dataset.step !== String(n); }); }
  function showWizard(n) { main.hidden = true; root.hidden = false; showStep(n); }
  function showOilDesk() { root.hidden = true; main.hidden = false; startOilDesk(); }

  let equity = 0;
  let chosenPreset = null;   // null until the first preview resolves "auto"
  let isCustom = false;      // true when the account's real numbers don't match any card

  function renderPresetCards() {
    const wrap = document.getElementById('onb-preset-cards');
    wrap.innerHTML = RISK_PRESETS.map(p => `
      <button type="button" class="preset-card${!isCustom && p.id === chosenPreset ? ' on' : ''}"
              data-preset="${p.id}" role="radio" aria-checked="${!isCustom && p.id === chosenPreset}">
        <span class="preset-card-name">${esc(p.label)}</span>
        <span class="preset-card-blurb">${esc(p.blurb)}</span>
      </button>`).join('');
    wrap.querySelectorAll('.preset-card').forEach(btn => {
      btn.addEventListener('click', () => {
        chosenPreset = btn.dataset.preset;
        isCustom = false;
        renderPresetCards();
        loadPreview();
      });
    });
    const note = document.getElementById('onb-custom-note');
    note.textContent = 'Custom (from your old settings)';
    note.hidden = !isCustom;
  }

  async function loadPreview() {
    const presetToSend = chosenPreset || 'auto';
    try {
      const r = await fetch('/api/risk/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ equity_usd: equity, profile: { preset: presetToSend } }),
      });
      if (!r.ok) throw new Error('preview ' + r.status);
      const d = await r.json();
      if (!chosenPreset) {
        chosenPreset = d.preset_applied || 'starter';
        isCustom = !matchesPresetDefaults(d.preset_applied, d.profile);
        renderPresetCards();
      }
      document.getElementById('onb-headline').textContent = d.headline || '—';
      // Plainly, right under the headline, never buried in a per-book row:
      // the exchange-minimum bump risk note services/risk/derive.py already
      // computes (notes), so a starter account risking up to 15% on a
      // bumped order is never a surprise.
      document.getElementById('onb-notes').innerHTML = (d.notes || [])
        .map(n => `<div class="preview-note">${esc(n)}</div>`).join('');
      document.getElementById('onb-risk-usd').textContent = fmtUsd(d.risk_per_trade_usd);
      document.getElementById('onb-daily-pause').textContent = fmtUsd(d.daily_loss_limit_usd);
      document.getElementById('onb-max-positions').textContent = d.max_open_positions ?? '—';
      const low = document.getElementById('onb-low-equity');
      // Never silent zeros: a non-tradable account always says the exact
      // dollar amount that would fix it, built from min_equity_to_trade_usd.
      if (!d.can_trade && Number(d.min_equity_to_trade_usd) > equity) {
        low.textContent = 'Add ' + fmtAddUsd(d.min_equity_to_trade_usd, equity) + ' to place your first trade.';
        low.hidden = false;
      } else {
        low.hidden = true;
      }
    } catch (e) { reportRefreshFailure('onboarding risk preview', e, 'onb-preview'); }
  }

  async function enterRiskStep() {
    localStorage.setItem(RESUME_KEY, '2');
    showWizard(2);
    try {
      const r = await fetch('/api/accounts/active/summary');
      const s = r.ok ? await r.json() : {};
      equity = Number(s.equity_usd ?? 0);
    } catch (e) { equity = 0; }
    document.getElementById('onb-risk-equity').textContent = 'Your account has ' + fmtUsd(equity) + '.';
    chosenPreset = null;
    await loadPreview();
  }

  document.getElementById('onb-save').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await fetch('/api/risk/profile', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset: chosenPreset }),
      });
      if (!r.ok) throw new Error('save ' + r.status);
      localStorage.removeItem(RESUME_KEY);
      showStep(3);
    } catch (e) {
      reportRefreshFailure('onboarding risk save', e, 'onb-preview');
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('onb-done').addEventListener('click', () => {
    localStorage.removeItem(RESUME_KEY);
    showOilDesk();
  });

  // 2026-10-25: no wallet picker, so no pathiel:account-changed event. The
  // welcome card tells the user to set .env.local and restart; the next
  // load sees the account and either resumes the risk step or shows the desk.
  (async function initOnboarding() {
    const session = await PathielSession.get();
    if (session.needs_onboarding) { showWizard(1); return; }
    if (localStorage.getItem(RESUME_KEY) === '2') { await enterRiskStep(); return; }
    showOilDesk();
  })();
})();

renderLevSeg();
