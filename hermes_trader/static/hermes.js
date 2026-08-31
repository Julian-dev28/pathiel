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
  console.warn(`[hermes] ${what} refresh failed:`, err);
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
