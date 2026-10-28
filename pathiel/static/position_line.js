// The 20x box from the LIVE position, not the call (2026-10-28, operator:
// "this part of the fe doesnt really keep up with the ticks"). The call card
// showed entry 110.40 at 5x and "Stop too close for 20x" while the account
// was long 13.08 from 109.46 at 20x with the venue's liquidation at 106.66,
// a stop using 18% of the way there. Once a position is open its own entry,
// leverage, venue liquidation price, resting stop and targets are the truth;
// the thesis numbers were the plan before the fill.
//
// Pure: no DOM, no fetch. Loaded before oil_desk.js; also required by
// tests/test_position_line_js.py through node.
(function (root) {
  'use strict';

  const num = v => (v === null || v === undefined || v === '' ? null : Number(v));
  const firstNum = list => {
    for (const v of (Array.isArray(list) ? list : [])) {
      const n = num(v);
      if (n !== null && isFinite(n)) return n;
    }
    return null;
  };
  const uniqueNums = list => {
    const out = [];
    for (const v of (Array.isArray(list) ? list : [])) {
      const n = num(v);
      if (n !== null && isFinite(n) && !out.includes(n)) out.push(n);
    }
    return out;
  };

  /** The open position that answers for `call`: same instrument, same side.
   *  null when flat on that coin or the position is the other way round. */
  function positionForCall(call, positionsByCoin) {
    if (!call || !call.instrument || !positionsByCoin) return null;
    const p = positionsByCoin[call.instrument];
    if (!p || (call.side !== 'long' && call.side !== 'short')) return null;
    return p.side === call.side ? p : null;
  }

  /** The same fields GET /api/desks/{id}/line returns, from a position row
   *  of /api/accounts/{id}/positions. The stop is the tightest resting stop
   *  (all of them share one level when place_bracket topped up); the
   *  targets are the distinct resting take-profits. null when the row lacks
   *  an entry or a venue liquidation price. */
  function positionLine(p) {
    if (!p) return null;
    const entry = num(p.entry_px);
    const liq = num(p.liq_px);
    const lev = num(p.leverage);
    if (!entry || !liq || !isFinite(entry) || !isFinite(liq)) return null;
    const long = p.side === 'long';
    const stops = uniqueNums(p.stop_px);
    const stop = stops.length ? (long ? Math.max(...stops) : Math.min(...stops)) : null;
    const liqDist = Math.abs(entry - liq);
    const stopDist = stop !== null ? Math.abs(entry - stop) : null;
    // A stop on the wrong side of entry (trailed past it into profit) cannot
    // be liquidated through: it uses none of the liquidation distance.
    const stopInProfit = stop !== null && (long ? stop >= entry : stop <= entry);
    const frac = stop === null || !liqDist ? null : (stopInProfit ? 0 : stopDist / liqDist);
    const targets = uniqueNums(p.tp_px);
    const rTargets = stopDist ? targets.map(t => ({
      px: t, r_multiple: Math.round(Math.abs(t - entry) / stopDist * 100) / 100 })) : [];
    return {
      source: 'position', side: p.side, leverage: lev, entry_px: entry, stop_px: stop,
      targets, liq_px: liq, stop_frac_of_liq: frac,
      // the same 50% rule as pathiel/agents/bracket.py's check_leverage; no stop is never ok
      ok: frac !== null && frac < 0.5,
      no_stop: stop === null,
      r_to_targets: rTargets,
    };
  }

  const api = { positionForCall, positionLine };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.PathielPositionLine = api;
})(typeof window !== 'undefined' ? window : globalThis);
