/* call_history.js — pure helpers for the Desk page's call history.
 *
 * No DOM, no fetch: desks.js calls these when it renders the "Your calls vs
 * the machine" table, and tests/test_call_history_js.py runs the same file
 * under node (module.exports below) so the folding and the labels are
 * tested deterministically, not eyeballed. Loaded before desks.js.
 *
 * Contract: contracts/strategies/models.py (Thesis, Grade.result).
 */
(function (root) {
  'use strict';

  // Grade.result -> what the Result column says and which class colours it.
  // A grade written before Grade.result existed (2026-11-13) has none: it
  // falls back to the direction read the old column showed.
  const RESULT_LABEL = { win: 'win', loss: 'loss', expired: 'expired', untriggered: 'untriggered' };
  const RESULT_CLASS = { win: 'grade-hit', loss: 'grade-miss', expired: 'grade-expired', untriggered: 'grade-untriggered' };

  function gradeResult(grade) {
    if (!grade) return 'pending';
    if (grade.result && RESULT_LABEL[grade.result]) return RESULT_LABEL[grade.result];
    if (grade.direction_hit == null) return 'graded';
    return grade.direction_hit ? 'hit' : 'miss';
  }

  function gradeClass(grade) {
    if (!grade) return 'grade-pending';
    if (grade.result && RESULT_CLASS[grade.result]) return RESULT_CLASS[grade.result];
    if (grade.direction_hit == null) return '';
    return grade.direction_hit ? 'grade-hit' : 'grade-miss';
  }

  // Whether the Result column shows a return next to the word. An
  // untriggered call made no trade; a pending one has no final number.
  function gradeShowsReturn(grade) {
    return !!grade && grade.result !== 'untriggered';
  }

  // The desk rewrites its thesis every 15 to 60 minutes, so one call shows
  // up as several journal rows ("Still long Brent on a dip to 89.97" four
  // times on 11-10). The journal keeps every row; the TABLE folds a run of
  // consecutive rows that restate the same call -- same author, side,
  // instrument, entry, stop and first target -- into the row where the call
  // was FIRST made (its grade spans the longest window), with how many
  // times it was restated and when last. A change to any of those fields
  // starts a new row. Input and output are newest-first, like
  // DeskBrief.history.
  function callKey(t) {
    const c = (t && t.call) || {};
    const target = Array.isArray(c.targets) && c.targets.length ? c.targets[0] : null;
    return [t.author, c.side, c.instrument, c.entry_px, c.stop_px, target].map(v => String(v)).join('|');
  }

  function collapseCallHistory(history) {
    const runs = [];
    let run = null;
    for (const t of history || []) {
      const key = callKey(t);
      if (run && run.key === key) { run.rows.push(t); continue; }
      run = { key, rows: [t] };
      runs.push(run);
    }
    return runs.map(r => {
      const rows = r.rows;                    // newest first inside the run
      const first = rows[rows.length - 1];    // the call as first made
      return {
        thesis: first,
        repeats: rows.length,
        last_restated_at: rows.length > 1 ? rows[0].created_at : null,
        ids: rows.map(x => x.id),
      };
    });
  }

  const api = { gradeResult, gradeClass, gradeShowsReturn, callKey, collapseCallHistory };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : this);
