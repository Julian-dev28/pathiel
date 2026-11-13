"""The trade ledger: every closed desk trade, once, with what was true when
it was opened.

One record per closed trade, built by the loss audit's own code
(services/strategies/evals/loss_audit.py: trades from fills, exit kind,
opening stop and target, equity, R, excursion, cause tags), joined with:

  thesis   the thesis in force at entry: id, side, entry, stop, age in
           minutes, and why it was rewritten (`rewrite_reason`: first,
           flip, same_side, after_flat; derived from the journal order,
           because the thesis file does not carry a reason)
  context  weekend (the sim's own `weekend_closed`), UTC hour, the entry's
           place in the 24h range (side-adjusted: 0 = the side's own edge,
           a long's low; below 0 = it bought a break), planned R (target
           distance over stop distance), leverage, % of equity a stop costs
  luck     the random-timing percentile (luck.py)

Stored append-only at `$PATHIEL_STATE_DIR/review/ledger.jsonl`, one JSON
object per line, idempotent by trade id:

  - a new trade id is appended
  - a trade still inside its settle window (12h after the exit: the audit's
    wrong-direction and stop-too-tight tags look that far ahead) is
    appended again only when its record changed; the last line wins
  - once a line with `settled: true` exists, that trade is frozen: the
    venue's equity samples roll forward, so a later rebuild could drift

Running twice on the same inputs appends nothing.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pathiel.models.types import Candle

from services.review import luck as L
from services.strategies.backtest.desk_tick_sim import weekend_closed
from services.strategies.evals import loss_audit as LA

# theses_by_desk/build_records moved to services/review/thesis_match.py
# (2026-11-13): that is the one place in services/review that imports
# services.strategies.evals.stop_cap_test (for same_market), which itself
# reaches the rest of the backtest/eval chain -- desk_tick_backtest,
# live_params, profit_lock_test, market_scout, strategy_sweep,
# scenario_sweep, cross_market_backtest. services/pool/grade.py imports this
# module for to_record/annotate_pairs/update_ledger/ledger_records, none of
# which need any of that; keeping the split means grade.py's import never
# drags that chain into the plugin bundle (scripts/build_plugin_bundle.py
# walks every import reachable from the MCP server).

SCHEMA = 2   # 2: trend_20d_pct and other_desk_side (2026-11-02)
HOUR_MS = LA.HOUR_MS
SETTLE_MS = int(max(LA.BACK_HOURS, LA.FLIP_HOURS) * HOUR_MS)
RANGE_BARS = 24                     # closed 1h bars behind the entry: the 24h range
VOLATILE = ("recorded_at", "as_of")  # not part of a record's identity


def iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def trade_id(tr: Dict[str, Any]) -> str:
    return f"{tr['coin']}|{int(tr['entry_time'])}|{tr.get('first_oid')}"


def _r(x: Any, nd: int = 6) -> Any:
    return round(x, nd) if isinstance(x, float) else x


# ── context (pure) ─────────────────────────────────────────────────────────

def rewrite_reason(th: Any, theses: Sequence[Any]) -> Optional[str]:
    """Why the thesis in force was written, from the journal order alone:
    "first" (no earlier thesis), "flip" (the previous one had the other
    side), "after_flat" (the previous one was flat), "same_side"."""
    if th is None:
        return None
    prev = None
    for x in sorted(theses, key=lambda t: t.created_at_ms):
        if x.created_at_ms < th.created_at_ms:
            prev = x
    if prev is None:
        return "first"
    if prev.side == "flat":
        return "after_flat"
    if th.side in ("long", "short") and prev.side != th.side:
        return "flip"
    return "same_side"


def range_position(entry_ms: int, entry_px: float, side: str, bars1h: Sequence[Candle]) -> Dict[str, Any]:
    """The entry's place in the 24 closed 1h bars before it, side-adjusted:
    0 at the side's own edge (a long's low, a short's high), 1 at the far
    edge; under 0 = it entered past its own edge (a long under the 24h low:
    the 11-02 long), over 1 = past the far edge."""
    closed = [b for b in bars1h if b.t + HOUR_MS <= entry_ms]
    window = sorted(closed, key=lambda b: b.t)[-RANGE_BARS:]
    if len(window) < RANGE_BARS // 2:
        return {"range_lo": None, "range_hi": None, "range_pos": None}
    lo, hi = min(b.l for b in window), max(b.h for b in window)
    if hi <= lo:
        return {"range_lo": lo, "range_hi": hi, "range_pos": None}
    pos = (entry_px - lo) / (hi - lo)
    if side == "short":
        pos = 1.0 - pos
    return {"range_lo": lo, "range_hi": hi, "range_pos": round(pos, 3) + 0.0}   # + 0.0: never "-0.0"


def planned_r(entry: Optional[float], stop: Optional[float], target: Optional[float]) -> Optional[float]:
    if not entry or not stop or not target or entry == stop:
        return None
    return round(abs(target - entry) / abs(entry - stop), 2)


TREND_BARS = 480   # 20 days of 1h bars


def trend_20d(entry_ms: int, bars1h: Sequence[Candle]) -> Dict[str, Any]:
    """The coin's 20-day return at entry, from closed 1h bars only (causal).
    OIL_REGIME_TEST_2026-11-02: shorts in a 20-day uptrend lost; tracked so
    the review can say whether that is a pattern or a fluke."""
    closed = [b for b in bars1h if b.t + HOUR_MS <= entry_ms]
    if len(closed) <= TREND_BARS:
        return {"trend_20d_pct": None}
    base = closed[-1 - TREND_BARS].c
    return {"trend_20d_pct": round((closed[-1].c / base - 1.0) * 100.0, 2) if base else None}


def annotate_pairs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mark what the OTHER desk held when each trade opened ("long", "short"
    or "flat"), from the closed trades in the same list. Oil and silver move
    opposite (1h correlation -0.36, -0.67 on 1% Brent hours,
    PULLBACK_AND_CORRELATION_TEST_2026-11-02), so opposite sides across the
    two desks are one bet doubled and the same side is a hedge."""
    for rec in records:
        side = "flat"
        for o in records:
            if o is rec or o.get("desk") == rec.get("desk"):
                continue
            if o.get("entry_ms") is not None and o.get("exit_ms") is not None \
                    and o["entry_ms"] <= rec["entry_ms"] < o["exit_ms"]:
                side = o["side"]
                break
        rec["other_desk_side"] = side
    return records


def to_record(tr: Dict[str, Any], theses: Sequence[Any], bars1h: Sequence[Candle], bars5: Sequence[Candle],
              as_of_ms: int) -> Dict[str, Any]:
    """One ledger record from one enriched, closed loss-audit trade."""
    th = LA.thesis_at(tr["entry_time"], theses)
    r_unit = tr.get("r_unit")
    risk_usd = r_unit * tr["max_size"] if r_unit else None
    entry_ms = int(tr["entry_time"])
    rec: Dict[str, Any] = {
        "schema": SCHEMA, "id": trade_id(tr), "coin": tr["coin"], "desk": tr["desk"], "side": tr["side"],
        "entry_ms": entry_ms, "exit_ms": int(tr["exit_time"]), "entry": iso(entry_ms), "exit": iso(tr["exit_time"]),
        "entry_px": tr["entry_px"], "avg_entry_px": tr.get("avg_entry_px"), "exit_px": tr.get("exit_px"),
        "stop_px": tr.get("stop_px"), "target_px": tr.get("target_px"), "max_size": tr["max_size"],
        "adds": tr["adds"], "notional_usd": tr["notional_usd"], "exit_kind": tr.get("exit_kind"),
        "closed_pnl": tr["closed_pnl"], "fees": tr["fees"], "funding_usd": tr.get("funding_usd"),
        "net_usd": tr["net_usd"], "risk_usd": risk_usd,
        "r_mult": (tr["net_usd"] / risk_usd) if risk_usd else None,
        "exit_r": tr.get("exit_r"), "mfe_r": tr.get("mfe_r"), "give_back_usd": tr.get("give_back_usd"),
        "equity_at_size": tr.get("equity_at_size"), "eff_leverage": tr.get("eff_leverage"),
        "risk_pct_equity": tr.get("stop_loss_pct_equity"), "tags": list(tr.get("tags") or []),
        "primary": tr.get("primary"), "flip_after_h": tr.get("flip_after_h"),
        "back_to_entry_h": tr.get("back_to_entry_h"),
        "thesis_id": th.thesis_id if th else None, "thesis_side": th.side if th else None,
        "thesis_entry_px": th.entry_px if th else None, "thesis_stop_px": th.stop_px if th else None,
        "thesis_age_min": tr.get("thesis_age_min"), "rewrite_reason": rewrite_reason(th, theses),
        "side_agrees": (th.side == tr["side"]) if th else None,
        "weekend": weekend_closed(entry_ms), "hour_utc": int((entry_ms // HOUR_MS) % 24),
        "planned_r": planned_r(tr["entry_px"], tr.get("stop_px"), tr.get("target_px")),
    }
    rec.update(range_position(entry_ms, tr["entry_px"], tr["side"], bars1h))
    rec.update(trend_20d(entry_ms, bars1h))
    rec.update(L.luck_check({**tr, "id": rec["id"]}, bars5))
    rec["settled"] = as_of_ms >= int(tr["exit_time"]) + SETTLE_MS
    rec["as_of"] = iso(as_of_ms)
    return {k: _r(v) for k, v in rec.items()}


# ── the file (append only) ─────────────────────────────────────────────────

def read_ledger(path: str) -> List[Dict[str, Any]]:
    """Every line, in file order. A torn last line (a crash mid-write) is
    skipped, not fatal."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                out.append(row)
    return out


def _final(rec: Dict[str, Any]) -> bool:
    """Settled under the current schema. A record from an older SCHEMA is
    rebuilt once more (a fix to how a field is computed reaches old trades)."""
    return bool(rec.get("settled")) and rec.get("schema") == SCHEMA


def latest(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The current record per trade id: the last line, except that the first
    settled line under the current schema is final."""
    cur: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        prev = cur.get(row["id"])
        if prev is not None and _final(prev):
            continue
        cur[row["id"]] = row
    return cur


def _identity(rec: Dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in rec.items() if k not in VOLATILE}, sort_keys=True, default=str)


def update_ledger(path: str, records: Sequence[Dict[str, Any]], now_iso: str) -> Dict[str, List[str]]:
    """Append what is new or changed (module doc). Returns the ids added,
    revised, and left alone."""
    cur = latest(read_ledger(path))
    added, revised, same = [], [], []
    lines = []
    for rec in records:
        prev = cur.get(rec["id"])
        if prev is None:
            added.append(rec["id"])
        elif _final(prev) or _identity(prev) == _identity(rec):
            same.append(rec["id"])
            continue
        else:
            revised.append(rec["id"])
        lines.append(json.dumps({**rec, "recorded_at": now_iso}, sort_keys=True, default=str))
    if lines:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torn = False
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                torn = fh.read(1) != b"\n"      # a crash mid-line: start on a fresh line
        with open(path, "a") as fh:
            fh.write(("\n" if torn else "") + "\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return {"added": added, "revised": revised, "unchanged": same}


def ledger_records(path: str, since_ms: int = 0) -> List[Dict[str, Any]]:
    """The current record of every trade entered at or after `since_ms`,
    oldest first."""
    return sorted((r for r in latest(read_ledger(path)).values() if int(r["entry_ms"]) >= since_ms),
                  key=lambda r: r["entry_ms"])


# ── the loss audit's trades.csv as records (the regression fixture) ────────

def _num(v: str) -> Optional[float]:
    return float(v) if v not in ("", None, "None") else None


def _ms(s: str) -> Optional[int]:
    if not s:
        return None
    return int(datetime.strptime(s, "%Y-%m-%d %H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def records_from_audit_csv(path: str) -> List[Dict[str, Any]]:
    """The closed trades of a loss_audit trades.csv as ledger records (the
    columns the audit writes; thesis id, range and luck are not in it)."""
    out = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("exit"):
                continue
            entry_ms = _ms(row["entry"])
            entry, stop, size = _num(row["entry_px"]), _num(row["stop_px"]), _num(row["max_size"])
            risk_usd = abs(entry - stop) * size if entry and stop and size else None
            net = _num(row["net_usd"])
            out.append({
                "schema": SCHEMA, "id": f"{row['coin']}|{entry_ms}|csv", "coin": row["coin"],
                "desk": LA.DESK_OF.get(row["coin"], row["coin"]), "side": row["side"],
                "entry_ms": entry_ms, "exit_ms": _ms(row["exit"]), "entry": iso(entry_ms), "exit": iso(_ms(row["exit"])),
                "entry_px": entry, "exit_px": _num(row["exit_px"]), "stop_px": stop, "target_px": _num(row["target_px"]),
                "max_size": size, "exit_kind": row["exit_kind"], "net_usd": net, "risk_usd": risk_usd,
                "r_mult": net / risk_usd if risk_usd else None, "exit_r": _num(row["exit_r"]),
                "mfe_r": _num(row["mfe_r"]), "equity_at_size": _num(row["equity_at_size"]),
                "eff_leverage": _num(row["eff_leverage"]), "risk_pct_equity": _num(row["stop_loss_pct_equity"]),
                "tags": [t for t in row["tags"].split("+") if t], "primary": row["primary"],
                "thesis_side": row["thesis_side"] or None, "thesis_entry_px": _num(row["thesis_entry_px"]),
                "thesis_age_min": _num(row["thesis_age_min"]),
                "side_agrees": (row["thesis_side"] == row["side"]) if row["thesis_side"] else None,
                "weekend": weekend_closed(entry_ms), "hour_utc": int((entry_ms // HOUR_MS) % 24),
                "planned_r": planned_r(entry, stop, _num(row["target_px"])), "settled": True,
            })
    return annotate_pairs(out)
