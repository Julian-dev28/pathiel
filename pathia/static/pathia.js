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

/* ── Live / Demo ─────────────────────────────────────────────────────────────
 *
 * The demo is a separate sub-application mounted at /demo, with its own
 * dashboard instance reading its own generated files (services/demo/router.py).
 * So switching is NAVIGATION, not client state: no fetch rewriting, no mode
 * flag to leak, and no way for a live page to end up rendering invented
 * numbers — the code that serves them is not mounted under the live prefix.
 *
 * It also means the toggle is bookmarkable and survives a reload, and "which am
 * I looking at" is answered by the address bar rather than by a badge someone
 * has to notice.
 */
const PathiaMode = (function () {
  const DEMO_PREFIX = '/demo';

  const isDemo = () => location.pathname === DEMO_PREFIX
    || location.pathname.startsWith(DEMO_PREFIX + '/');

  /* The same page on the other side. /activity <-> /demo/activity. */
  function counterpart() {
    const path = location.pathname;
    if (isDemo()) return (path.slice(DEMO_PREFIX.length) || '/') + location.search;
    return DEMO_PREFIX + (path === '/' ? '/' : path) + location.search;
  }

  function render() {
    const slot = document.querySelector('.masthead-right');
    if (!slot || document.getElementById('mode-toggle')) return;
    const demo = isDemo();

    const wrap = document.createElement('div');
    wrap.id = 'mode-toggle';
    wrap.className = 'mode-toggle';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Data source');

    const here = document.createElement('span');
    here.className = 'mode-opt on';
    here.textContent = demo ? 'Demo' : 'Live';
    here.setAttribute('aria-current', 'true');

    const there = document.createElement('a');
    there.className = 'mode-opt';
    there.href = counterpart();
    there.textContent = demo ? 'Live' : 'Demo';
    there.title = demo
      ? 'Back to this deployment\u2019s own trading. Empty until the loop has run.'
      : 'A worked example: seven days of generated trading history. No account, no exchange connection.';

    wrap.append(demo ? there : here, demo ? here : there);
    slot.insertBefore(wrap, slot.firstChild);
    if (demo) document.body.classList.add('is-demo');
  }

  return { render, isDemo };
})();

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

  /* ── Loading the wallet picker ──────────────────────────────────────────
   *
   * The picker is RainbowKit, bundled at services/wallet_ui into
   * /static/wallet.js. It is ~380 KB over the wire against ~30 KB for this
   * whole file, so it is fetched on demand rather than on page load: a
   * signed-in operator reloading the dashboard never pays for it.
   *
   * This replaced a direct `window.ethereum` call. With two extensions
   * installed that is whichever one won a race at page load, with no way to
   * choose — and it told people who already had a wallet to "install MetaMask
   * or Rabby". RainbowKit resolves wallets over EIP-6963, so each announces
   * itself by name and the user picks the one they meant.
   */
  let walletLoading = null;

  function loadWalletUI() {
    if (walletLoading) return walletLoading;
    walletLoading = new Promise((resolve, reject) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = '/static/wallet.css';
      document.head.appendChild(css);

      const js = document.createElement('script');
      js.src = '/static/wallet.js';
      js.defer = true;
      js.onload = () => resolve();
      // Reset the latch so a second click retries rather than hanging forever
      // on a request that failed once.
      js.onerror = () => { walletLoading = null; reject(new Error('could not load the wallet picker')); };
      document.head.appendChild(js);
    });
    return walletLoading;
  }

  async function signIn() {
    // The island renders its own button into #wallet-connect-root, so the
    // placeholder chip has to go before it mounts or the masthead shows two.
    const chip = document.getElementById('auth-chip');
    if (chip) chip.hidden = true;
    // Read by index.tsx once React commits: the user already clicked, and
    // nobody clicks a second time to open the modal they asked for.
    window.__pathiaWalletAutoOpen = true;
    try {
      await loadWalletUI();
      if (window.PathiaWallet) window.PathiaWallet.openConnectModal();
    } catch (e) {
      if (chip) chip.hidden = false;
      note(String((e && e.message) || e));
    }
    return null;
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
      // Deliberately still vanilla. A signed-in operator needs an identity and
      // a way out, not a wallet picker, so this path never loads the bundle.
      chip.hidden = false;
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

  /* The connected wallet, reflected.
   *
   * Reads that wallet's own Hyperliquid state through /api/dashboard/account,
   * which needs no stored key: /info clearinghouseState takes a plain address
   * and SIWE already proved the caller controls it. So the page can show
   * somebody their account while the product remains unable to trade it.
   *
   * This matters most on a fresh deployment, where the house book is empty and
   * this tile is the only thing on the page with a number in it.
   *
   * Classes match the KPI tiles around it (`sec-label` / `kv` / `ksub`). They
   * used to be `l` / `v` / `s`, which this stylesheet does not define, so the
   * tile rendered as three unstyled lines wedged between two proper cards.
   */
  function accountTile(inner) {
    return '<div class="sec-label">Your wallet</div>' + inner;
  }

  async function loadMyAccount() {
    const slot = document.getElementById('my-account');
    if (!slot) return;
    try {
      const r = await fetch('/api/dashboard/account');
      if (!r.ok) { slot.hidden = true; return; }
      const a = await r.json();
      slot.hidden = false;
      const addr = '<div class="ksub mono">' + esc(short(a.address || (me && me.address) || '')) + '</div>';

      if (a.status === 'unavailable') {
        slot.innerHTML = accountTile(
          '<div class="kv amb">unavailable</div>' +
          '<div class="ksub">could not reach Hyperliquid just now</div>' + addr);
        return;
      }
      if (!a.funded) {
        slot.innerHTML = accountTile(
          '<div class="kv">not funded</div>' +
          '<div class="ksub">deposit to Hyperliquid with this wallet to see it here</div>' + addr);
        return;
      }
      const n = (a.positions || []).length;
      // Whose work this account reflects. pathia trades one account: the
      // deployment's own. A visitor signing in is looking at their Hyperliquid
      // balance, which this system has never traded and cannot — saying so is
      // the difference between a profile and an implied track record.
      const whose = a.is_house
        ? '<div class="ksub">traded by this deployment — the history below is its work</div>'
        : '<div class="ksub">your own account. pathia has never traded it, and holds no key for it.</div>';
      slot.innerHTML = accountTile(
        '<div class="kv">$' + Number(a.equity).toFixed(2) + '</div>' +
        '<div class="ksub">' + n + ' open position' + (n === 1 ? '' : 's') + '</div>' +
        whose + addr);
    } catch { slot.hidden = true; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    PathiaMode.render();
    refresh().then(loadMyAccount);
  });
  return { refresh, signIn, signOut, loadMyAccount, user: () => me };
})();
