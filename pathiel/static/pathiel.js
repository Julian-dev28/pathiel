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

/** Re-run `fn` every `ms`, the way a page should poll a server it shares
 *  with every other open tab: the next run is scheduled only after this one
 *  settles, so a slow answer never stacks requests behind itself, and
 *  nothing runs while the tab is hidden (one catch-up run when it shows).
 *
 *  2026-10-19: the dashboard polled with setInterval. With account reads
 *  taking 6-12 s, its 6 s and 10 s polls stacked up and held the browser's
 *  six connections to this site, so clicking Desks in any tab waited. */
function pollEvery(fn, ms) {
  let timer = null, running = false, due = false;
  async function tick() {
    timer = null;
    if (document.hidden) { due = true; return; }
    running = true;
    try { await fn(); } catch (_) { /* each poller reports its own failures */ }
    running = false;
    timer = setTimeout(tick, ms);
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && due && !running && timer === null) { due = false; tick(); }
  });
  timer = setTimeout(tick, ms);
}

/** Signed percentage to two places. */
const fmtPct = n => (Number(n ?? 0) >= 0 ? '+' : '') + Number(n ?? 0).toFixed(2) + '%';

/** Read a design token, so charts and canvases follow the theme rather than
 *  hard-coding a colour the stylesheet cannot reach. */
const tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/** Plain dollars, always two decimals — the onboarding wizard and /account's
 *  risk editor both render live DerivedLimits previews and used to carry
 *  byte-identical copies of this. */
const fmtUsd = v => (Number(v ?? 0) < 0 ? '-$' : '$') + Math.abs(Number(v ?? 0)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** The exact top-up that clears a venue minimum, rounded UP to the next cent
 *  so the number this prints is never itself one cent short — deterministic
 *  arithmetic done once here, not re-derived per caller. Whole dollars print
 *  without cents ("$5", not "$5.00"); Julien reads numbers, not currency
 *  formatting. */
const fmtAddUsd = (need, have) => fmtUsd(Math.max(0, Math.ceil((Number(need) - Number(have)) * 100) / 100)).replace(/\.00$/, '');

/** The four risk presets, mirrored from services/risk/presets.py — blurbs
 *  copied verbatim from there (that file is the one source of truth for
 *  this copy, including the "starter risks up to 15% on a bumped order"
 *  warning). Shared by landing.html's onboarding wizard and account.html's
 *  risk editor, so the two screens can never show two different
 *  descriptions of the same preset. */
const RISK_PRESETS = [
  { id: 'starter', label: 'Starter', risk_per_trade_pct: 3.0, daily_loss_limit_pct: 8.0, max_leverage: 10.0,
    blurb: 'One position at a time. Risks up to 3% a trade (as much as 15% if the $10 '
          + 'exchange minimum forces it on a wide-stop book), stops for the day after an 8% loss.' },
  { id: 'small', label: 'Small', risk_per_trade_pct: 2.0, daily_loss_limit_pct: 6.0, max_leverage: 8.0,
    blurb: 'Up to 3 positions at once. Risks up to 2% a trade (4% if bumped to the exchange '
          + 'minimum), stops for the day after 6%.' },
  { id: 'standard', label: 'Standard', risk_per_trade_pct: 1.5, daily_loss_limit_pct: 5.0, max_leverage: 6.0,
    blurb: 'Up to 6 positions at once. Risks up to 1.5% a trade, stops for the day after 5%. '
          + 'The $10 minimum never needs a bump at this size.' },
  { id: 'pro', label: 'Pro', risk_per_trade_pct: 1.0, daily_loss_limit_pct: 4.0, max_leverage: 5.0,
    blurb: 'Up to 10 positions at once. Risks up to 1% a trade, stops for the day after 4%. '
          + 'Capital preservation over expression.' },
];

/** Does a resolved profile's OWN numbers actually match preset_applied's
 *  table entry? A migrated legacy config (services/risk/migrate.py) comes
 *  back from GET /api/risk/limits with preset_applied resolved to whatever
 *  tier the equity falls into — but every number overridden away from
 *  that tier's own defaults, so the literal preset name alone would
 *  mislabel it (Julien's real config: preset_applied could read "starter"
 *  while risk_per_trade_pct is ~15.75%, nothing like starter's 3%).
 *  Comparing the numbers that actually vary by preset catches that:
 *  `false` means show this as "Custom (from your old settings)", whatever
 *  preset_applied literally says. */
function matchesPresetDefaults(presetApplied, profile) {
  const def = RISK_PRESETS.find(p => p.id === presetApplied);
  if (!def || !profile) return false;   // "custom", or an unrecognized name
  const close = (a, b) => Math.abs(Number(a) - Number(b)) < 0.01;
  return close(profile.risk_per_trade_pct, def.risk_per_trade_pct)
      && close(profile.daily_loss_limit_pct, def.daily_loss_limit_pct)
      && close(profile.max_leverage, def.max_leverage);
}

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
  console.warn(`[pathiel] ${what} refresh failed:`, err);
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
const PathielMode = (function () {
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

  // Self-registering: this used to be called from the now-deleted sign-in
  // module's own DOMContentLoaded handler, which had nothing to do with
  // the demo/live toggle beyond happening to also fire on page load.
  // Losing that handler silently lost this call with it — regression
  // caught by services/demo/tests/test_router.py's
  // test_the_toggle_renders_on_load, which is the reason this module now
  // owns rendering itself instead of depending on another one to remember
  // to. The callback runs on DOMContentLoaded, well after this IIFE
  // returns and the const below is assigned, so the outer-scope reference
  // resolves fine despite looking circular.
  document.addEventListener('DOMContentLoaded', () => PathielMode.render());

  return { render, isDemo, prefix: () => (isDemo() ? DEMO_PREFIX : '') };
})();

/* ── fetch wrapper: demo-mode rewrite + operator-role signal ────────────────
 *
 * Used to live inside the now-deleted sign-in module, which also used it to
 * pop a sign-in gate on 401. The gate is gone — wallet-connect is the
 * identity now, there is no separate sign-in — but this wrapper's other two
 * jobs are unrelated to that and still needed by every page, so it moved out
 * on its own rather than disappearing with it.
 */
(function () {
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    // Keep a demo page talking to the demo app.
    //
    // Every dashboard call in the templates is written absolute — the pages
    // were the whole site before /demo existed. From /demo/ an absolute
    // `/api/dashboard/summary` resolves to the LIVE app, so the demo rendered
    // the live account's numbers: on this deployment a page of zeros, and on a
    // real one somebody else's book behind a button labelled Demo.
    //
    // Rewritten here rather than at ~30 call sites across five templates,
    // because this wrapper already exists and a call site added tomorrow is
    // covered by it without anyone remembering.
    if (PathielMode.isDemo() && typeof args[0] === 'string'
        && args[0].startsWith('/api/')) {
      args = [PathielMode.prefix() + args[0], ...args.slice(1)];
    }
    const res = await _fetch.apply(this, args);
    // 403 on a house-account route is not an error to report. It is the normal
    // answer for a signed-in customer: those routes describe the deployment's
    // own trading, and the customer's account is /api/dashboard/account. Left
    // unhandled, every page painted a rack of red "refresh failed" banners at
    // exactly the people we most want to keep.
    if (res.status === 403) document.body.classList.add('not-operator');
    return res;
  };
})();

/* ── PathielSession: the account, not a sign-in ──────────────────────────────
 *
 * Replaces the deleted sign-in module. Wallet-connect IS the identity now (Hyperdash-style
 * Terminal Wallet — contracts/accounts/models.py): there is no separate
 * SIWE sign-in step, no chip, no full-page gate. Every page asks ONE question,
 * GET /api/session -> SessionInfo, and local_mode means that question can
 * come back "nothing to do" with no login at all.
 *
 * `get()` never rejects. A missing endpoint (this wave's accounts router is
 * mounted by the integrator once every wave-2 agent's work merges — see
 * SWARM_RULES section 10 — so it 404s until then) or any other network
 * failure falls back to "nothing gates this page": every caller gets a
 * SessionInfo-shaped object either way and never needs its own try/catch.
 *
 * `openWallet(view)` and the `pathiel:account-changed` listener are the two
 * shared hooks wallet-ui's bundle and this file's consumers (pathiel.js
 * itself, landing.html's onboarding wizard, account.html) agree on.
 */
const PathielSession = (function () {
  const FALLBACK = Object.freeze({
    local_mode: true, address: null, active_account_id: null,
    accounts_count: 0, can_trade: true, needs_onboarding: false,
  });
  let cached = null;

  async function get() {
    if (cached) return cached;
    try {
      const r = await fetch('/api/session');
      cached = r.ok ? await r.json() : FALLBACK;
    } catch {
      cached = FALLBACK;
    }
    return cached;
  }

  /* Any connect, switch or removal invalidates the cache AND re-renders the
   * "trading is off" banner below — one place reacts, every page's own
   * script just reads e.detail (already the new SessionInfo; no re-fetch
   * needed) for whatever it wants to do next. */
  document.addEventListener('pathiel:account-changed', (e) => {
    cached = e.detail || null;
    renderTradeBanner();
  });

  /* ── loading the wallet picker ──────────────────────────────────────────
   *
   * The bundle (services/wallet_ui) is ~3 MB over the wire against ~30 KB
   * for this whole file (services/wallet_ui/bundle.manifest.json has the
   * exact byte count), so it is fetched on demand rather than on page
   * load: a visitor who never opens the picker never pays for it — see
   * services/wallet_ui/tests/test_wallet_bundle.py's
   * test_bundle_is_not_loaded_on_page_load, which fails the build outright
   * if any template loads it eagerly instead.
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
      // Reset the latch so a second click retries rather than hanging
      // forever on a request that failed once.
      js.onerror = () => { walletLoading = null; reject(new Error('could not load the wallet picker')); };
      document.head.appendChild(js);
    });
    return walletLoading;
  }

  /* window.PathielWallet.open(view) is the bundle's own entry point once
   * loaded (contracts/accounts/models.py's "choose" | "connect" | "apikey"
   * | "watch"). openConnectModal() survives only as a temporary alias on
   * the bundle's side — this file calls .open() directly, never the
   * alias. Checks for an already-defined window.PathielWallet first
   * (tests/browser/test_onboarding_browser.py's stub installs one before
   * any page script runs) so a page that somehow already has it loaded
   * never re-injects the ~3MB bundle a second time. */
  async function openWallet(view) {
    if (!(window.PathielWallet && typeof window.PathielWallet.open === 'function')) {
      try {
        await loadWalletUI();
      } catch (e) {
        console.warn('[pathiel] could not load the wallet UI:', e);
        return;
      }
    }
    if (window.PathielWallet && typeof window.PathielWallet.open === 'function') {
      window.PathielWallet.open(view);
    } else {
      console.warn('[pathiel] wallet UI loaded but did not define open()');
    }
  }

  /* ── "everywhere else": why trading is off, in dollars ──────────────────
   *
   * landing.html's onboarding wizard and account.html own this story in
   * full on their own pages, so this banner stays out of their way (it is a
   * no-op on "/" and "/account") and only speaks up on every OTHER page —
   * activity, news, analytics, trends, desks — where nothing else would.
   * Never a silent "nothing happens when you'd expect a trade": the reason
   * and the dollar shortfall come straight off ActiveSummary / DerivedLimits,
   * never invented here.
   */
  function bannerEl() {
    let el = document.getElementById('trade-status-banner');
    if (el) return el;
    const shell = document.querySelector('.shell');
    if (!shell) return null;
    el = document.createElement('div');
    el.id = 'trade-status-banner';
    el.className = 'trade-banner';
    el.hidden = true;
    shell.insertBefore(el, shell.firstChild);
    return el;
  }

  async function renderTradeBanner() {
    if (location.pathname === '/' || location.pathname === '/account') return;
    const el = bannerEl();
    if (!el) return;
    const session = await get();
    if (session.needs_onboarding) {
      el.innerHTML = '<span>Connect your Hyperliquid account to start trading.</span>' +
        '<button type="button" class="btn go" id="trade-banner-connect">Connect</button>';
      el.hidden = false;
      document.getElementById('trade-banner-connect').onclick = () => openWallet('choose');
      return;
    }
    if (session.can_trade) { el.hidden = true; return; }
    try {
      const r = await fetch('/api/accounts/active/summary');
      const s = r.ok ? await r.json() : null;
      const reason = (s && s.blockers && s.blockers[0]) || 'Trading is off for this account.';
      el.innerHTML = '<span>' + esc(reason) + '</span>';
      el.hidden = false;
    } catch (e) {
      reportRefreshFailure('trade status banner', e, el);
    }
  }

  document.addEventListener('DOMContentLoaded', () => { renderTradeBanner(); });

  return { get, openWallet, renderTradeBanner };
})();
