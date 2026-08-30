"""Deposits and withdrawals, so the drawdown number stops lying.

The problem this fixes: nothing in this repo recorded capital moving in or out,
so an equity curve could not tell a trading loss from the operator withdrawing
USDC. The risk panel read peak $225.93 against $0.03 today and had to label that
an "equity decline" rather than a loss, because it genuinely did not know which
it was. A number that cannot distinguish those two is not a risk metric.

Two pieces:

  RECORD — `record_flows()` pulls Hyperliquid's non-funding ledger (deposits,
  withdrawals, and transfers crossing the pool boundary) and appends them to a
  JSONL under the state dir. Idempotent by event key, so re-running it over an
  overlapping window never double-counts. The heavy lifting of classifying a
  transfer already lives in client.hl_client.fetch_aggregate_contributions_since;
  this module owns persistence and the time series.

  MEASURE — `nav_series()` converts an equity curve plus those flows into a
  flow-neutral NAV index. This is a time-weighted return: between two equity
  observations the return is (equity_now - net_flow) / equity_before, and the
  index compounds those. Depositing $100 does not move the index, which is
  exactly the property the drawdown needs.

Why time-weighted and not simply subtracting cumulative flows: subtraction gives
a dollar figure that still moves when capital moves, so a withdrawal would still
print as a drawdown. Only an index that normalises each interval by the capital
actually at risk during it is flow-neutral.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hermes_trader.agents.atomic_io import append_json_line

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("HERMES_STATE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state")

FLOWS_FILE = os.path.join(_STATE_DIR, "capital_flows.jsonl")

# Ledger event types that move capital across the account boundary. Anything
# else (funding, fills) is trading, not a flow, and must not be netted out.
_DEPOSIT_TYPES = {"deposit", "vaultWithdraw"}
_WITHDRAW_TYPES = {"withdraw", "vaultDeposit"}


def _flows_path() -> str:
    """Resolved at call time so tests can point HERMES_STATE_DIR at a tmp dir."""
    base = os.environ.get("HERMES_STATE_DIR")
    return os.path.join(base, "capital_flows.jsonl") if base else FLOWS_FILE


def _event_key(e: Dict[str, Any]) -> str:
    """Stable identity for a ledger event, so overlapping fetches are idempotent.

    HL gives a hash on most rows; where it does not, time+type+amount is unique
    enough in practice and still beats double-counting a deposit.
    """
    d = e.get("delta") or {}
    return str(e.get("hash") or f"{e.get('time')}:{d.get('type')}:"
                                f"{d.get('usdcValue', d.get('amount'))}")


def load_flows(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every recorded flow, oldest first. Missing file = no flows, not an error."""
    p = path or _flows_path()
    if not os.path.exists(p):
        return []
    out: List[Dict[str, Any]] = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # a torn last line must not lose the history
    out.sort(key=lambda r: int(r.get("ts") or 0))
    return out


def append_flows(rows: Sequence[Dict[str, Any]], path: Optional[str] = None) -> int:
    """Append rows not already present. Returns how many were actually written."""
    p = path or _flows_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    known = {r.get("key") for r in load_flows(p)}
    fresh = [r for r in rows if r.get("key") not in known]
    if not fresh:
        return 0
    for r in fresh:
        append_json_line(p, r)
    return len(fresh)


def classify(event: Dict[str, Any], user: str) -> Optional[Tuple[int, float, str]]:
    """(ts, signed_usd, kind) for a capital flow, or None if it is not one.

    Positive = capital in. Mirrors the classification in
    hl_client.fetch_aggregate_contributions_since, which is the tested authority
    on HL's transfer schema; kept in step with it deliberately rather than
    reimplemented differently.
    """
    d = event.get("delta") or {}
    t = d.get("type")
    try:
        amt = float(d.get("usdcValue", d.get("amount", 0)) or 0)
    except (TypeError, ValueError):
        return None
    if amt == 0:
        return None
    ts = int(event.get("time") or 0)
    user_lc = (user or "").lower()

    if t in _DEPOSIT_TYPES:
        return ts, amt, str(t)
    if t in _WITHDRAW_TYPES:
        return ts, -amt, str(t)
    if t in ("internalTransfer", "subAccountTransfer"):
        sender = (d.get("user") or "").lower()
        return ts, (-amt if sender == user_lc else amt), str(t)
    if t == "send":
        sender = (d.get("user") or "").lower()
        receiver = (d.get("destination") or "").lower()
        if sender == user_lc and receiver != user_lc:
            return ts, -amt, "send_out"
        if receiver == user_lc and sender != user_lc:
            return ts, amt, "send_in"
        return None          # self-transfer between own pools: not a flow
    return None


def record_flows(user: str, start_ms: int,
                 fetcher: Optional[Callable[[str, int], Any]] = None,
                 path: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the ledger since `start_ms` and persist any new capital flows.

    `fetcher` is injectable so this is testable without a network call. Returns
    a summary rather than raising: a ledger outage must not take down whatever
    called it.
    """
    if not user:
        return {"status": "no_user", "written": 0}
    if fetcher is None:
        from hermes_trader.client.hl_client import _http_post

        def fetcher(u: str, since: int):
            return _http_post("/info", {"type": "userNonFundingLedgerUpdates",
                                        "user": u, "startTime": int(since)})
    try:
        events = fetcher(user, start_ms) or []
    except Exception as exc:
        logger.warning(f"[capital-flows] ledger fetch failed: {exc}")
        return {"status": "fetch_failed", "written": 0, "error": str(exc)[:200]}
    if not isinstance(events, list):
        return {"status": "bad_response", "written": 0}

    rows: List[Dict[str, Any]] = []
    for e in events:
        got = classify(e, user)
        if got is None:
            continue
        ts, usd, kind = got
        rows.append({"ts": ts, "usd": round(usd, 6), "kind": kind,
                     "key": _event_key(e)})
    written = append_flows(rows, path)
    return {"status": "ok", "seen": len(rows), "written": written,
            "since": int(start_ms)}


def net_flow_between(t0: int, t1: int, flows: Optional[Sequence[Dict[str, Any]]] = None) -> float:
    """Net capital in over (t0, t1]. Half-open so a flow is counted exactly once
    when these intervals are chained across an equity curve."""
    rows = flows if flows is not None else load_flows()
    return sum(float(r.get("usd") or 0) for r in rows
               if t0 < int(r.get("ts") or 0) <= t1)


def nav_series(points: Sequence[Dict[str, Any]],
               flows: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Flow-neutral NAV index from an equity curve.

    `points` is [{"ts": ms, "equity": usd}, ...] oldest first, as
    _equity_curve_payload returns. The index starts at 1.0 and compounds the
    time-weighted return of each interval:

        r_i  = (equity_i - net_flow_i) / equity_{i-1}
        nav_i = nav_{i-1} * r_i

    A deposit raises equity and net_flow by the same amount, so r_i is
    unchanged — the whole point.

    Two guards, both load-bearing:
      - an interval starting from non-positive equity has no capital at risk and
        no defined return, so the index carries forward flat rather than
        dividing by zero
      - an interval whose net flow DWARFS the opening equity has no meaningful
        time-weighted return (a $24.46 deposit onto a $0.02 base gives r=-576).
        That is the standard TWR breakdown at near-zero capital. The flow ends
        the sub-period, the index carries forward, the post-flow balance becomes
        the new base, and the point is marked `anomaly`. Compounding it instead
        pinned the drawdown at -100% permanently on 2026-08-31
    """
    rows = list(flows) if flows is not None else load_flows()
    out: List[Dict[str, Any]] = []
    nav = 1.0
    prev_eq: Optional[float] = None
    prev_ts = 0
    for p in points:
        ts = int(p.get("ts") or 0)
        eq = float(p.get("equity") or 0)
        if prev_eq is None:
            out.append({"ts": ts, "nav": nav, "equity": eq})
            prev_eq, prev_ts = eq, ts
            continue
        flow = net_flow_between(prev_ts, ts, rows)
        if prev_eq > 0:
            # A flow that dwarfs the capital base makes the time-weighted
            # return meaningless. Observed live 2026-08-31: a $24.46 deposit
            # landed while equity was $0.02, giving r = (12.93 - 24.46)/0.02 =
            # -576. Compounding that pinned the index at 4.7e-10 and the panel
            # reported -100% while the account held $12.94.
            #
            # This is the standard TWR breakdown at near-zero capital, not a bad
            # reading. A fund handles it the same way: a large external flow
            # ENDS the measurement sub-period and the post-flow balance becomes
            # the new base. Carry the index, re-anchor, mark the point.
            if abs(flow) > prev_eq:
                out.append({"ts": ts, "nav": nav, "equity": eq, "anomaly": "flow_dwarfs_base"})
                prev_eq, prev_ts = eq, ts
                continue
            r = (eq - flow) / prev_eq
            if r > 0:
                nav = nav * r
            else:
                # Non-positive return with the flow small relative to the base:
                # this is real ruin, and the index should say so.
                nav = nav * 1e-9
        out.append({"ts": ts, "nav": nav, "equity": eq})
        prev_eq, prev_ts = eq, ts
    return out


def drawdown_from_nav(nav_points: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Current and max drawdown of a NAV index, in percent (<= 0)."""
    navs = [float(p["nav"]) for p in nav_points if p.get("nav") is not None]
    if not navs:
        return {"drawdown_pct": 0.0, "max_drawdown_pct": 0.0}
    peak = max(navs)
    cur = ((navs[-1] - peak) / peak * 100) if peak > 0 else 0.0
    max_dd, running = 0.0, 0.0
    for v in navs:
        running = max(running, v)
        if running > 0:
            max_dd = min(max_dd, (v - running) / running * 100)
    return {"drawdown_pct": round(cur, 2), "max_drawdown_pct": round(max_dd, 2)}


def coverage(points: Sequence[Dict[str, Any]],
             flows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Whether the flow record actually covers an equity window.

    A NAV drawdown is only honest if flows were being recorded for the whole
    span. If recording started after the window opened, the earlier part is
    still flow-blind and the panel must keep saying so rather than quietly
    upgrading its own confidence.
    """
    rows = list(flows) if flows is not None else load_flows()
    if not points:
        return {"covered": False, "reason": "no equity history"}
    first_pt = int(points[0].get("ts") or 0)
    if not rows:
        return {"covered": False, "reason": "no capital flows recorded yet"}
    first_flow = min(int(r.get("ts") or 0) for r in rows)
    recorded_from = min(first_flow, int(_recording_started_at() or first_flow))
    if recorded_from > first_pt:
        return {"covered": False,
                "reason": "flow recording starts after the equity window opens"}
    return {"covered": True, "reason": ""}


def _recording_started_at(path: Optional[str] = None) -> Optional[int]:
    """When flow recording began, as a marker row written on first record."""
    for r in load_flows(path):
        if r.get("kind") == "_recording_started":
            return int(r.get("ts") or 0)
    return None


def mark_recording_started(ts_ms: Optional[int] = None,
                           path: Optional[str] = None) -> None:
    """Write the marker that says 'flows are tracked from here'.

    Without it, an account with genuinely zero deposits since inception is
    indistinguishable from one where nothing was ever recorded.
    """
    if _recording_started_at(path) is not None:
        return
    ts = int(ts_ms if ts_ms is not None else time.time() * 1000)
    append_flows([{"ts": ts, "usd": 0.0, "kind": "_recording_started",
                   "key": f"_recording_started:{ts}"}], path)
