"""Is this desk's thesis due a rewrite? One deterministic answer per tick.

  python3 scripts/thesis_due.py --desk oil
  -> {"due": true, "reasons": ["daily: thesis is 24.3h old"], "thesis_id": "...", ...}

The desk-tick agent runs this after the brief and calls `desk_refresh` only
when `due` is true (operator, 2026-10-28: "Rewrite once a day, plus after
each scheduled catalyst (API on Tuesday, EIA on Wednesday, OPEC), after a
position closes, and when price moves 1.5% or more from the thesis entry").
A rewrite costs about a dollar of local model time, so no reason fires
inside an hour of the last thesis.

Read-only: the journal on disk, the public info API for the mid and the
account's fills; no key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))
sys.path.insert(0, _SCRIPTS)

DAILY_HOURS = 24.0
MIN_GAP_MINUTES = 60.0           # at most one rewrite an hour
MOVE_FRAC = 0.015                # 1.5% from the thesis entry
CATALYST_KINDS = ("api_weekly", "eia_weekly", "opec_meeting")
CATALYST_SETTLE_MINUTES = 15.0   # let the number print and the market react first


def _ref_price(call: Dict[str, Any]) -> Optional[float]:
    """The thesis's entry: its exact entry, else the middle of its entry zone."""
    if call.get("entry_px"):
        return float(call["entry_px"])
    zone = call.get("entry_zone") or []
    if zone:
        return (float(zone[0]) + float(zone[-1])) / 2.0
    return None


def refresh_due(thesis: Dict[str, Any], now: datetime, mid: Optional[float],
                closes_since: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Pure. `thesis` = the journal's thesis as a dict (created_at, expires_at,
    call, catalysts); `closes_since` = closing fills on this desk's coins after
    the thesis was written. Returns {due, reasons, age_h, ...}."""
    created = _ts(thesis["created_at"])
    age_h = (now - created).total_seconds() / 3600.0
    out: Dict[str, Any] = {"thesis_id": thesis.get("id"), "age_h": round(age_h, 2), "reasons": []}
    if age_h * 60.0 < MIN_GAP_MINUTES:
        out.update(due=False, held_back=f"last thesis is {age_h * 60:.0f} min old; at most one rewrite an hour")
        return out
    reasons: List[str] = []
    expires = thesis.get("expires_at")
    if expires and now >= _ts(expires):
        reasons.append("expired")
    if age_h >= DAILY_HOURS:
        reasons.append(f"daily: thesis is {age_h:.1f}h old")
    settle = timedelta(minutes=CATALYST_SETTLE_MINUTES)
    for c in thesis.get("catalysts") or []:
        if c.get("kind") in CATALYST_KINDS and c.get("at"):
            at = _ts(c["at"])
            if created < at and at + settle <= now:
                reasons.append(f"catalyst passed: {c.get('title') or c['kind']} at {at:%Y-%m-%d %H:%MZ}")
    closes = list(closes_since)
    if closes:
        reasons.append(f"position closed since the thesis ({len(closes)} closing fill(s))")
    ref = _ref_price(thesis.get("call") or {})
    if ref and mid:
        move = abs(mid - ref) / ref
        out["move_from_entry_pct"] = round(move * 100, 2)
        if move >= MOVE_FRAC:
            reasons.append(f"price moved {move:.1%} from the thesis entry {ref:g} (mid {mid:g})")
    out.update(due=bool(reasons), reasons=reasons)
    return out


def _ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _closing_fills(user: str, coins: List[str], since: datetime) -> List[Dict[str, Any]]:
    from pathiel.client.exchange import _http_post
    rows = _http_post("/info", {"type": "userFillsByTime", "user": user,
                                "startTime": int(since.timestamp() * 1000)}) or []
    return [f for f in rows if f.get("coin") in set(coins) and str(f.get("dir", "")).startswith("Close")]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desk", required=True)
    a = ap.parse_args(argv)
    import _state_env
    _state_env.load_env_local(os.path.dirname(_SCRIPTS))
    from services.strategies import journal
    from services.strategies.desks import get_spec
    t = journal.latest_thesis(a.desk)
    if t is None:
        print(json.dumps({"due": True, "reasons": ["no thesis yet"]}))
        return 0
    thesis = json.loads(t.model_dump_json())
    coin = thesis["call"].get("instrument") or get_spec(a.desk).instruments[0].coin
    coins = [i.coin for i in get_spec(a.desk).instruments]
    mid = None
    try:
        from pathiel.client.exchange import get_hl_price
        mid = float(get_hl_price(coin))
    except Exception as e:  # noqa: BLE001 - a missing mid only drops the price reason
        print(f"[thesis_due] mid unavailable for {coin}: {e}", file=sys.stderr)
    closes: List[Dict[str, Any]] = []
    try:
        from services.accounts.view import get_effective_active_account
        acct = get_effective_active_account()
        if acct is not None:
            closes = _closing_fills(acct.main_address, coins, _ts(thesis["created_at"]))
    except Exception as e:  # noqa: BLE001 - a missing fill read only drops that reason
        print(f"[thesis_due] fills unavailable: {e}", file=sys.stderr)
    out = refresh_due(thesis, datetime.now(timezone.utc), mid, closes)
    out.update(desk=a.desk, coin=coin, mid=mid)
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
