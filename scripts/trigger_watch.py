"""Wake the desk tick the minute a desk's entry rule passes. No LLM calls.

Operator, 2026-10-31: the loops went to every 4 hours for the weekend
("reduce loops for each to every 4 hours until Sunday evening"), which can
miss an entry by up to 4 hours. This watcher closes that gap for free: every
minute it reads each active desk's mid and asks the SAME function the Desk
page and the tick's rules use (services/strategies/next_trade.next_trade:
thesis side, never above a long's entry / below a short's, stop beyond the
24h range, target the far 24h edge minus 0.5%, 2R, the 20x rule) whether the
tick would trade right now. The first desk that says yes is printed as one
JSON line and the script EXITS, so the session that started it in the
background is notified and runs `/desk-tick <desk>`. The tick still makes
the decision and runs every safety check; this only decides WHEN it runs.

  python3 scripts/trigger_watch.py                 # all desks with an equity share
  python3 scripts/trigger_watch.py --once          # one pass, print, exit (debug)

Skips a desk that already holds a position or a resting entry (its tick and
its stop/target orders handle it), and a desk that fired in the last
--cooldown-min minutes (a tick that declined must not be re-woken every
minute). State and a per-minute log live under /tmp/trigger-watch/.
Read-only on the venue and the journal: it never places, cancels or writes
anything but its own log and cooldown file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))
sys.path.insert(0, _SCRIPTS)

STATE_DIR = "/tmp/trigger-watch"
LEVELS_REFRESH_S = 600      # the 24h range and ATR barely move inside 10 minutes
SLOT_REFRESH_S = 300        # positions/resting entries: the tick owns those anyway


def evaluate(side: Optional[str], thesis_entry: Optional[float], mid: Optional[float],
             levels: Dict[str, Any], coin: str, coin_max_leverage: Optional[float],
             leverage: float) -> Dict[str, Any]:
    """Pure: would the tick trade `coin` at `mid` right now? Same answer as
    GET /api/desks/{id}/next, with the mid refreshed between level reads."""
    from services.strategies.next_trade import next_trade, stop_buffer_for
    r24 = levels.get("range_24h") or {}
    if side not in ("long", "short"):
        return {"ok": False, "reason": f"thesis is {side or 'missing'}"}
    if mid is None or r24.get("low") is None or r24.get("high") is None:
        return {"ok": False, "reason": "no mid or 24h range"}
    buf = stop_buffer_for(coin, levels.get("atr14_1h"))
    if buf is None:
        return {"ok": False, "reason": "no ATR for the stop buffer"}
    out = next_trade(side, float(mid), float(r24["low"]), float(r24["high"]),
                     thesis_entry=thesis_entry, leverage=leverage,
                     coin_max_leverage=coin_max_leverage, stop_buffer=buf)
    return out


def cooled_down(fired: Dict[str, float], desk: str, now_s: float, cooldown_s: float) -> bool:
    """True when `desk` never fired or fired at least `cooldown_s` ago."""
    if desk not in fired:
        return True
    return now_s - float(fired[desk]) >= cooldown_s


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _log(line: str) -> None:
    with open(os.path.join(STATE_DIR, "watch.log"), "a") as fh:
        fh.write(line + "\n")


def watch(*, once: bool, cooldown_min: float, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.time) -> int:
    import _state_env
    _state_env.load_env_local(os.path.dirname(_SCRIPTS))
    from pathiel.agents.config_store import read_agent_config
    from pathiel.client.exchange import get_max_leverage
    from services.strategies import desk_share, journal, levels
    from services.strategies.desks import get_spec
    import desk_slot

    os.makedirs(STATE_DIR, exist_ok=True)
    fired_path = os.path.join(STATE_DIR, "fired.json")
    lv_cache: Dict[str, Any] = {}
    slot_cache: Dict[str, Any] = {}
    coin_max: Dict[str, Optional[float]] = {}

    while True:
        now = clock()
        cfg = read_agent_config()
        shares = desk_share.desk_shares(cfg)
        desks: List[str] = list(shares) or desk_share.active_desks(cfg)
        lev = float((cfg.get("risk_profile") or {}).get("max_leverage") or 20)
        fired = _read_json(fired_path)
        parts = []
        for desk in desks:
            t = journal.latest_thesis(desk)
            spec = get_spec(desk)
            primary = next((i.coin for i in spec.instruments if i.role == "primary"), spec.instruments[0].coin)
            coin = (t.call.instrument if t is not None else None) or primary
            if coin != primary:
                parts.append(f"{desk}: thesis on {coin}, not {primary}")
                continue
            if not cooled_down(fired, desk, now, cooldown_min * 60):
                parts.append(f"{desk}: cooling down")
                continue
            try:
                held = slot_cache.get(desk)
                if held is None or now - held[0] > SLOT_REFRESH_S:
                    out = _slot(desk_slot, desk)
                    slot_cache[desk] = held = (now, out)
                if held[1]:
                    parts.append(f"{desk}: {held[1]} (tick handles it)")
                    continue
                lv = lv_cache.get(coin)
                if lv is None or now - lv[0] > LEVELS_REFRESH_S:
                    lv_cache[coin] = lv = (now, levels.summarize_coin(coin))
                if coin not in coin_max:
                    coin_max[coin] = float(get_max_leverage(coin))
                mid = levels.fetch_mid(coin)
            except Exception as e:  # noqa: BLE001 - one bad read waits for the next minute
                parts.append(f"{desk}: read failed ({str(e)[:80]})")
                continue
            res = evaluate(t.call.side if t else None, t.call.entry_px if t else None, mid,
                           lv[1], coin, coin_max[coin], lev)
            if res.get("ok"):
                fired[desk] = now
                _write_json(fired_path, fired)
                line = {"fire": desk, "coin": coin, "utc": _utc(), "mid": mid,
                        "stop_px": res.get("stop_px"), "tp_px": res.get("tp_px"),
                        "r_now": res.get("r_now"), "verdict": res.get("verdict")}
                _log(f"{_utc()} FIRE {json.dumps(line)}")
                print(json.dumps(line), flush=True)
                return 0
            parts.append(f"{desk}: mid {mid} {res.get('verdict') or res.get('reason')}")
        _log(f"{_utc()} " + " | ".join(parts))
        if once:
            print(" | ".join(parts), flush=True)
            return 0
        sleep(60)


def _slot(desk_slot_mod: Any, desk: str) -> str:
    """'' when the desk holds nothing, else what it holds."""
    from services.accounts import positions as acc_pos
    from services.accounts.view import get_effective_active_account
    from services.strategies import desk_share
    acct = get_effective_active_account()
    if acct is None:
        raise RuntimeError("no active account")
    user = acct.main_address
    raws = [acc_pos._fetch_raw(user, d) for d in acc_pos._dexes_with_margin(user)]
    v = desk_slot_mod.slot_view(desk_share.desk_coins(desk), raws, own_slot=True)
    return f"holds {v['kind']} on {v['holder']}" if v.get("mine") else ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--cooldown-min", type=float, default=30.0)
    a = ap.parse_args(argv)
    return watch(once=a.once, cooldown_min=a.cooldown_min)


if __name__ == "__main__":
    raise SystemExit(main())
