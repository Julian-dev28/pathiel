"""Is this desk's thesis due a rewrite? One deterministic answer per tick.

  python3 scripts/thesis_due.py --desk oil
  -> {"due": true, "reasons": ["daily: thesis is 24.3h old"], "thesis_id": "...", ...}

(The 1.5% move is measured from the price when the thesis was written: the
operator said "from the thesis entry", but a WAIT thesis's entry sits away
from price on purpose and would trigger a rewrite every hour.)

The desk-tick agent runs this after the brief and calls `desk_refresh` only
when `due` is true (operator, 2026-10-28: "Rewrite once a day, plus after
each scheduled catalyst (API on Tuesday, EIA on Wednesday, OPEC), after a
position closes, and when price moves 1.5% or more from the thesis entry"). Since 2026-11-02 also
when a closed 1h candle breaks the thesis's own stop (operator: "go").
A rewrite costs about a dollar of local model time, so no reason fires
inside an hour of the last thesis.

Since 2026-11-05 (operator: "FIX THEM") a surprise headline is a reason too:
an alert the price watcher recorded (services/intel/headline_watch.py: a
matching headline, an odds jump, a price shock) on this desk's coins after
the thesis was written makes it due as "headline: <label>: <title>", and it
waits only HEADLINE_MIN_GAP_MIN (30) after the last thesis, not the hour.
The after-catalyst reasons also cover FOMC, CPI, NFP and elections now
(11-04: the midterm result dropped Brent 9% and the desk reacted late).

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
MOVE_FRAC = 0.015                # 1.5% from where price stood when the thesis was written
# the scheduled releases the thesis lists (services/intel/sources/catalysts.py
# kinds plus services/intel/data/scheduled_events.json kinds) that earn a
# rewrite once they have printed
CATALYST_KINDS = ("api_weekly", "eia_weekly", "opec_meeting", "fomc", "cpi", "nfp", "election")
CATALYST_SETTLE_MINUTES = 15.0   # let the number print and the market react first
HEADLINE_MIN_GAP_MINUTES = 30.0  # a headline alert overrides the hour, but not this


def refresh_due(thesis: Dict[str, Any], now: datetime, mid: Optional[float],
                closes_since: Iterable[Dict[str, Any]] = (),
                px_at_writing: Optional[float] = None,
                hour_closes: Iterable[Dict[str, Any]] = (),
                headline_alerts: Iterable[Dict[str, Any]] = (),
                primary_coin: Optional[str] = None) -> Dict[str, Any]:
    """Pure. `thesis` = the journal's thesis as a dict (created_at, expires_at,
    call, catalysts); `closes_since` = closing fills on this desk's coins after
    the thesis was written; `px_at_writing` = the price when it was written;
    `hour_closes` = 1h candles ({"t": open ms, "c": close}) that opened after
    the thesis was written. A CLOSED one beyond the call's stop_px (below for
    a long, above for a short) invalidates the thesis and makes it due.
    `headline_alerts` = headline_watch alerts ({"at_ms", "label", "title"})
    on this desk's coins; one newer than the thesis makes it due past the
    hourly gap (HEADLINE_MIN_GAP_MINUTES still holds). `primary_coin` = the
    desk's primary instrument: a thesis with a side on another coin (the
    metals desk's silver theses after gold became its primary, 2026-11-05)
    can never trade, so it is due at once, past every gap.

    The 1.5% move is measured from `px_at_writing`, not from the call's entry:
    a WAIT thesis puts its entry away from price on purpose (2026-10-28: a
    110.50 to 111.00 dip zone with Brent at 112.45, already 1.5% apart), and
    measuring from it would rewrite every hour, about $24 a day."""
    created = _ts(thesis["created_at"])
    age_h = (now - created).total_seconds() / 3600.0
    out: Dict[str, Any] = {"thesis_id": thesis.get("id"), "age_h": round(age_h, 2), "reasons": []}
    wrong = wrong_instrument(thesis, primary_coin)
    if wrong:
        out.update(due=True, reasons=[wrong])
        return out
    headline = headline_reason(created, headline_alerts, now)
    if age_h * 60.0 < HEADLINE_MIN_GAP_MINUTES:
        out.update(due=False, held_back=f"last thesis is {age_h * 60:.0f} min old; "
                   f"even a headline waits {HEADLINE_MIN_GAP_MINUTES:.0f} min")
        return out
    if age_h * 60.0 < MIN_GAP_MINUTES:
        if headline:
            out.update(due=True, reasons=[headline],
                       held_back=f"last thesis is {age_h * 60:.0f} min old; a headline alert overrides the hour")
            return out
        out.update(due=False, held_back=f"last thesis is {age_h * 60:.0f} min old; at most one rewrite an hour")
        return out
    reasons: List[str] = [headline] if headline else []
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
    broke = invalidating_close(thesis, hour_closes, now)
    if broke:
        reasons.append(broke)
    closes = list(closes_since)
    if closes:
        reasons.append(f"position closed since the thesis ({len(closes)} closing fill(s))")
    ref = px_at_writing
    if ref and mid:
        move = abs(mid - ref) / ref
        out["move_since_written_pct"] = round(move * 100, 2)
        if move >= MOVE_FRAC:
            reasons.append(f"price moved {move:.1%} since the thesis was written ({ref:g} -> {mid:g})")
    out.update(due=bool(reasons), reasons=reasons)
    return out


def wrong_instrument(thesis: Dict[str, Any], primary_coin: Optional[str]) -> Optional[str]:
    """Pure. A reason when the call has a side on a coin that is not the
    desk's primary (2026-11-05: the metals desk moved from silver to gold;
    its silver theses never trade and must be rewritten first). None when no
    primary is given, the call is flat, or the instrument matches."""
    call = thesis.get("call") or {}
    coin = call.get("instrument")
    if not primary_coin or not coin or coin == primary_coin or call.get("side") not in ("long", "short"):
        return None
    return f"instrument {coin} is not the desk's primary {primary_coin}: rewrite first"


def headline_reason(created: datetime, alerts: Iterable[Dict[str, Any]], now: datetime) -> Optional[str]:
    """Pure. The newest headline alert recorded after `created` (and not in
    the future) as "headline: <label>: <title>"; None when there is none."""
    created_ms = int(created.timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)
    hits = [a for a in alerts if created_ms < int(a.get("at_ms") or 0) <= now_ms]
    if not hits:
        return None
    a = max(hits, key=lambda a: int(a["at_ms"]))
    return f"headline: {a.get('label') or a.get('kind') or 'alert'}: {str(a.get('title') or '')[:120]}"


def invalidating_close(thesis: Dict[str, Any], hour_closes: Iterable[Dict[str, Any]],
                       now: datetime) -> Optional[str]:
    """Pure. The first CLOSED 1h candle since the thesis was written whose
    close is beyond the call's stop_px, as a reason string; None otherwise.

    2026-11-02 03:00Z: the oil long (entry 111.70, stop 110.83) was
    invalidated by a 110.17 hourly close, but no rewrite reason fired (the
    move was 1.43%, under the 1.5% trigger), so the watcher kept waking the
    tick to buy a dead thesis at 2.3R and only a hand-written instruction
    held it back. The tick's rule already reads invalidation off closed 1h
    candles; this makes it a rewrite trigger too."""
    call = thesis.get("call") or {}
    side, stop = call.get("side"), call.get("stop_px")
    if side not in ("long", "short") or stop is None:
        return None
    stop = float(stop)
    created = _ts(thesis["created_at"])
    for bar in sorted(hour_closes, key=lambda b: int(b.get("t") or 0)):
        opened = datetime.fromtimestamp(int(bar["t"]) / 1000, tz=timezone.utc)
        if opened < created or opened + timedelta(hours=1) > now:
            continue          # opened before the thesis, or still forming
        c = float(bar["c"])
        if (side == "long" and c < stop) or (side == "short" and c > stop):
            return (f"invalidated: 1h close {c:g} at {opened + timedelta(hours=1):%H:%MZ} "
                    f"{'below' if side == 'long' else 'above'} the thesis stop {stop:g}")
    return None


def _hour_closes(coin: str, since: datetime) -> List[Dict[str, Any]]:
    from pathiel.client.exchange import _http_post
    rows = _http_post("/info", {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "1h", "startTime": int(since.timestamp() * 1000),
        "endTime": int(datetime.now(timezone.utc).timestamp() * 1000)}}) or []
    return [{"t": int(r["t"]), "c": float(r["c"])} for r in rows if r.get("t") is not None]


def _ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _px_at(coin: str, when: datetime) -> Optional[float]:
    """Close of the last 15m candle that opened at or before `when`."""
    from pathiel.client.exchange import _http_post
    t = int(when.timestamp() * 1000)
    rows = _http_post("/info", {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": "15m", "startTime": t - 2 * 15 * 60_000, "endTime": t + 15 * 60_000}}) or []
    before = [r for r in rows if int(r.get("t") or 0) <= t]
    return float(before[-1]["c"]) if before else None


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
    spec = get_spec(a.desk)
    primary = next((i.coin for i in spec.instruments if i.role == "primary"), spec.instruments[0].coin)
    coin = thesis["call"].get("instrument") or primary
    coins = [i.coin for i in spec.instruments]
    wrong = wrong_instrument(thesis, primary)
    if wrong:
        print(json.dumps({"due": True, "reasons": [wrong], "thesis_id": thesis.get("id"), "desk": a.desk,
                          "coin": coin, "primary": primary}))
        return 0
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
    px0 = None
    try:
        px0 = _px_at(coin, _ts(thesis["created_at"]))
    except Exception as e:  # noqa: BLE001 - a missing candle only drops the price reason
        print(f"[thesis_due] price at writing unavailable: {e}", file=sys.stderr)
    bars: List[Dict[str, Any]] = []
    try:
        bars = _hour_closes(coin, _ts(thesis["created_at"]))
    except Exception as e:  # noqa: BLE001 - a missing candle read only drops the invalidation reason
        print(f"[thesis_due] 1h candles unavailable: {e}", file=sys.stderr)
    alerts: List[Dict[str, Any]] = []
    try:
        from services.intel import headline_watch
        alerts = headline_watch.alerts_for(coins, headline_watch.read_alerts())
    except Exception as e:  # noqa: BLE001 - a missing alert file only drops the headline reason
        print(f"[thesis_due] headline alerts unavailable: {e}", file=sys.stderr)
    out = refresh_due(thesis, datetime.now(timezone.utc), mid, closes, px_at_writing=px0,
                      hour_closes=bars, headline_alerts=alerts, primary_coin=primary)
    out.update(desk=a.desk, coin=coin, mid=mid, px_at_writing=px0, headline_alerts=len(alerts))
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
