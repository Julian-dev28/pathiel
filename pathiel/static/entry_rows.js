// entry_rows.js — pure string/class builder for the Levels card's entry
// rows (Desk page, "/"), 2026-11-19. Operator, looking at that card (mid,
// last closed 1h, 8h/24h/48h range, ATR14, mid position in the 24h range):
// "should add entry to FE right?" — he wants the desk's entry on the same
// card: the thesis's own plan, where the live rule will actually act, and
// how far away that is.
//
// Every number this file formats comes straight off GET
// /api/desks/{id}/levels's `entry` field (services/strategies/
// entry_card.py), which itself only reads services/strategies/
// next_trade.next_trade() and services/strategies/event_blackout.
// event_blackout() — the exact functions the desk tick and GET /next use.
// Nothing in this file computes a stop, a target, an R multiple or a
// blackout window; it only picks words and a badge class for numbers it is
// handed. `position`, when given, is PathielPositionLine.positionLine()'s
// own answer (pathiel/static/position_line.js) — again never recomputed
// here, just relabeled so the Levels card does not duplicate the Wallets
// card / "Next trade" box.
//
// Pure: no DOM, no fetch. Loaded before oil_desk.js, right after
// position_line.js; also required by tests/test_entry_rows_js.py through
// node.
(function (root) {
  'use strict';

  // Keys match services/strategies/entry_card.py's STATUS_* constants
  // exactly (a test pins the two lists together) plus "in_position", the
  // one status this file adds on top (entry_card.py has no opinion on an
  // open position — that comes from the page's own position data).
  const BADGE_CLASS = {
    ready: 'b-hold', waiting: 'b-flat', blackout: 'b-avoid',
    chase: 'b-avoid', break: 'b-avoid', no_thesis: 'b-flat', in_position: 'b-hold',
  };

  const num = v => (v === null || v === undefined || !isFinite(Number(v))) ? null : Number(v);

  /** {side, price} off the thesis's own call; null with no thesis. */
  function thesisEntryText(entry) {
    if (!entry || !entry.has_thesis) return null;
    return { side: entry.side === 'short' ? 'Short' : 'Long', price: num(entry.thesis_entry_px) };
  }

  /** "Buy at 87.98 or lower" / "Buys now at 87.98" / "Sell at 91.40 or
   *  higher" — presentation only; `entryPx` is next_trade()'s own
   *  entry_px (the mid when the rule trades now, else its trigger price). */
  function liveEntrySentence(side, status, entryPx) {
    const long = side !== 'short';
    const verb = long ? 'Buy' : 'Sell';
    if (entryPx === null || entryPx === undefined) return `${verb}: price unavailable`;
    return status === 'ready' ? `${verb}s now at ${entryPx}` : `${verb} at ${entryPx} ${long ? 'or lower' : 'or higher'}`;
  }

  /** The Levels card's three entry rows as plain data — no HTML, no DOM:
   *  { thesis: {side, price} | null,
   *    live: {sentence, stop, tp, r, status, statusText, badgeClass},
   *    distance: {price, pct, atr} | null,
   *    inPosition: bool }.
   *  `entry` is GET /api/desks/{id}/levels's own `entry` field (or null/
   *  undefined before it has loaded). `position` is
   *  PathielPositionLine.positionLine(PathielPositionLine.positionForCall(
   *  call, positionsByCoin)) — pass null when flat on this call's coin. */
  function entryRows(entry, position) {
    entry = entry || {};
    const thesis = thesisEntryText(entry);

    if (position) {
      const tp = Array.isArray(position.targets) && position.targets.length ? num(position.targets[0]) : null;
      const r = Array.isArray(position.r_to_targets) && position.r_to_targets.length
        ? num(position.r_to_targets[0].r_multiple) : null;
      return {
        thesis,
        live: {
          sentence: `In position at ${num(position.entry_px)}`, stop: num(position.stop_px), tp, r,
          status: 'in_position', statusText: 'in position', badgeClass: BADGE_CLASS.in_position,
        },
        distance: null, inPosition: true,
      };
    }

    if (!entry.has_thesis) {
      return {
        thesis: null,
        live: { sentence: null, stop: null, tp: null, r: null,
               status: 'no_thesis', statusText: 'no thesis', badgeClass: BADGE_CLASS.no_thesis },
        distance: null, inPosition: false,
      };
    }

    const live = entry.live || {};
    const status = live.status || 'waiting';
    const d = entry.distance || {};
    return {
      thesis,
      live: {
        sentence: liveEntrySentence(entry.side, status, num(live.entry_px)),
        stop: num(live.stop_px), tp: num(live.tp_px), r: num(live.r_multiple),
        status, statusText: live.status_text || status, badgeClass: BADGE_CLASS[status] || 'b-flat',
      },
      distance: (d.price === null || d.price === undefined) ? null
        : { price: num(d.price), pct: num(d.pct), atr: num(d.atr) },
      inPosition: false,
    };
  }

  const api = { entryRows, liveEntrySentence, thesisEntryText, BADGE_CLASS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.PathielEntryRows = api;
})(typeof window !== 'undefined' ? window : globalThis);
