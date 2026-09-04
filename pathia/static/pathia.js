/* Shared front-end helpers.
 *
 * Every page previously declared its own `esc`, `fmtPct` and `tok`, plus
 * byte-identical copies of the nav-marking and keyboard-navigation blocks.
 * The copies had already drifted: landing's `fmtPct` called `.toFixed` on a
 * raw value while the others coerced with `Number()` first, so a string
 * percentage threw on one page and rendered on the rest.
 *
 * Loaded before each page's own script, so these are in scope everywhere.
 */

/** Escape a value for interpolation into HTML. */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/** Signed percentage to two places. */
const fmtPct = n => (Number(n ?? 0) >= 0 ? '+' : '') + Number(n ?? 0).toFixed(2) + '%';

/** Read a design token, so charts and canvases follow the theme rather than
 *  hard-coding a colour the stylesheet cannot reach. */
const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/** Mark the current tab in the masthead. */
(function () {
  const here = window.location.pathname.replace(/\/$/, '') || '/';
  document.querySelectorAll('a[data-nav]').forEach(a => {
    if (a.dataset.nav === here) a.classList.add('nav-active');
  });
})();

/** `g` then a key hops tabs, the way a desk application would. Inert while
 *  typing in a field or holding a modifier. */
(function () {
  const map = { d: '/', a: '/activity', n: '/news', y: '/analytics', t: '/trends' };
  let armed = 0;
  addEventListener('keydown', e => {
    if (e.target.closest('input,textarea,select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'g') { armed = Date.now(); return; }
    if (armed && Date.now() - armed < 900 && map[e.key]) location.href = map[e.key];
    armed = 0;
  });
})();

/** Report a failed background refresh instead of swallowing it.
 *
 *  The pages carried fifteen `catch (e) {}` blocks around their polling. A
 *  panel that stops updating because its fetch started throwing looked
 *  identical to a panel with nothing to say, which is the same failure shape
 *  as a gate that reports an empty result. Nothing here is fatal — one dead
 *  panel must not take the page down — but it says so in the console and
 *  marks the section stale so the operator can see which number is old.
 */
function reportRefreshFailure(what, err, el) {
  console.warn(`[pathia] ${what} refresh failed:`, err);
  const node = typeof el === 'string' ? document.getElementById(el) : el;
  if (node) node.closest('.section, .panel, .card')?.classList.add('is-stale');
}


/** Clamp long prose to two lines, and only offer "more" where there is more
 *  to show. Called after any render that writes `.prose` blocks.
 *
 *  Length, not layout: with `-webkit-line-clamp` applied, scrollHeight equals
 *  clientHeight, so the overflow cannot be measured while the clamp is on, and
 *  lifting it mid-frame does not reliably reflow. Two lines at this column is
 *  roughly 180 characters — an approximation, but a stable one that costs no
 *  layout pass.
 */
const PROSE_TWO_LINES = 180;

function clampProse(root) {
  (root || document).querySelectorAll('.prose:not([data-clamped])').forEach(el => {
    el.dataset.clamped = '1';
    const body = el.querySelector('.prose-clamp');
    const btn = el.querySelector('.prose-more');
    if (!body || !btn) return;
    if (body.textContent.trim().length > PROSE_TWO_LINES) el.classList.add('is-long');
    btn.addEventListener('click', () => {
      const open = el.classList.toggle('is-open');
      btn.textContent = open ? 'Less' : 'More';
    });
  });
}

/* ── Sign in with your wallet ────────────────────────────────────────────────
 *
 * Lives here, not in each page, because all five load this file and all five
 * read the same gated APIs. One copy also means one place where the 401 -> sign
 * in behaviour can drift out of sync, which is the defect that put five
 * near-identical `fmtPct` definitions in this codebase in the first place.
 *
 * The flow is EIP-4361: ask the server for a nonce and the exact text to sign,
 * hand that text to the wallet, post the signature back. The server never
 * accepts a message the client composed — see services/auth/api.py.
 *
 * There is no password anywhere in this product, so there is nothing here to
 * remember, reset, or leak.
 */
const PathiaAuth = (function () {
  let me = null;                      // the signed-in user, or null

  const short = a => a ? a.slice(0, 6) + '…' + a.slice(-4) : '';

  async function refresh() {
    try {
      const r = await fetch('/auth/me');
      me = r.ok ? (await r.json()).user : null;
    } catch { me = null; }
    render();
    return me;
  }

  async function signIn() {
    const eth = window.ethereum;
    if (!eth) {
      note('No wallet found. Install MetaMask or Rabby, then reload.');
      return null;
    }
    try {
      const [address] = await eth.request({ method: 'eth_requestAccounts' });
      const prep = await (await fetch('/auth/nonce?address=' + encodeURIComponent(address))).json();
      if (!prep.message) throw new Error(prep.detail || 'could not start sign-in');
      // personal_sign takes (message, address) in that order. Reversed, the
      // wallet either errors or signs the address as the payload.
      const signature = await eth.request({ method: 'personal_sign', params: [prep.message, address] });
      const r = await fetch('/auth/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prep.message, signature }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || 'signature rejected');
      me = (await r.json()).user;
      render();
      hideGate();
      // Repaint with data the page could not fetch while signed out.
      window.location.reload();
      return me;
    } catch (e) {
      // 4001 is the EIP-1193 code for "user rejected". Not an error worth a
      // scary banner; they simply changed their mind.
      if (e && e.code === 4001) return null;
      note(String(e && e.message || e));
      return null;
    }
  }

  async function signOut() {
    try { await fetch('/auth/logout', { method: 'POST' }); } catch {}
    me = null;
    window.location.reload();
  }

  function note(msg) {
    const el = document.getElementById('auth-note');
    if (el) { el.textContent = msg; el.hidden = !msg; }
  }

  /* The masthead chip: who you are, and the way out. */
  function render() {
    const slot = document.querySelector('.masthead-right');
    if (!slot) return;
    let chip = document.getElementById('auth-chip');
    if (!chip) {
      chip = document.createElement('button');
      chip.id = 'auth-chip';
      chip.type = 'button';
      chip.className = 'icon-btn';
      slot.insertBefore(chip, slot.firstChild);
    }
    if (me) {
      chip.textContent = me.display_name || short(me.address);
      chip.title = me.address + (me.role === 'operator' ? ' · operator' : '') + ' — click to sign out';
      chip.onclick = signOut;
    } else {
      chip.textContent = 'Connect wallet';
      chip.title = 'Sign a message to prove you control your wallet. No transaction is sent.';
      chip.onclick = signIn;
    }
  }

  /* The full-page prompt, shown when a gated fetch comes back 401. */
  function showGate() {
    if (document.getElementById('auth-gate')) return;
    const d = document.createElement('div');
    d.id = 'auth-gate';
    d.innerHTML =
      '<div class="auth-card">' +
        '<div class="sec-label">Sign in</div>' +
        '<p>This account’s positions, balances and history are private. ' +
        'Prove you control your wallet to see them.</p>' +
        '<p class="auth-fine">You will sign a plain-text message. It sends no ' +
        'transaction and approves no trade.</p>' +
        '<button type="button" class="btn go" id="auth-gate-btn">Connect wallet</button>' +
        '<div id="auth-note" class="auth-note" hidden></div>' +
      '</div>';
    document.body.appendChild(d);
    document.getElementById('auth-gate-btn').onclick = signIn;
  }
  function hideGate() {
    const d = document.getElementById('auth-gate');
    if (d) d.remove();
  }

  /* Every page fetches its own data, so the 401 handling belongs at the
   * transport, not in each of the ~20 loaders. A page that forgot to handle it
   * would otherwise show empty panels and no explanation — the exact "looks
   * quiet, is actually broken" failure the rest of this UI works to avoid. */
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await _fetch.apply(this, args);
    if (res.status === 401) {
      try {
        const body = await res.clone().json();
        if (body && body.auth_required) showGate();
      } catch {}
    }
    // 403 on a house-account route is not an error to report. It is the normal
    // answer for a signed-in customer: those routes describe the deployment's
    // own trading, and the customer's account is /api/dashboard/account. Left
    // unhandled, every page painted a rack of red "refresh failed" banners at
    // exactly the people we most want to keep.
    if (res.status === 403) document.body.classList.add('not-operator');
    return res;
  };

  /* The signed-in wallet's own balance. Reads that wallet's address on
   * Hyperliquid, which needs no stored key — so the page can show a customer
   * their account without the product ever being able to trade it. */
  async function loadMyAccount() {
    const slot = document.getElementById('my-account');
    if (!slot) return;
    try {
      const r = await fetch('/api/dashboard/account');
      if (!r.ok) { slot.hidden = true; return; }
      const a = await r.json();
      slot.hidden = false;
      if (a.status === 'unavailable') {
        slot.innerHTML = '<span class="l">Your account</span>' +
          '<span class="v amb">unavailable</span>' +
          '<span class="s">could not reach Hyperliquid just now</span>';
        return;
      }
      if (!a.funded) {
        slot.innerHTML = '<span class="l">Your account</span>' +
          '<span class="v">not funded</span>' +
          '<span class="s">deposit to Hyperliquid with this wallet to see it here</span>';
        return;
      }
      const n = (a.positions || []).length;
      slot.innerHTML = '<span class="l">Your account</span>' +
        '<span class="v">$' + Number(a.equity).toFixed(2) + '</span>' +
        '<span class="s">' + n + ' open position' + (n === 1 ? '' : 's') + '</span>';
    } catch { slot.hidden = true; }
  }

  document.addEventListener('DOMContentLoaded', () => { refresh().then(loadMyAccount); });
  return { refresh, signIn, signOut, loadMyAccount, user: () => me };
})();
