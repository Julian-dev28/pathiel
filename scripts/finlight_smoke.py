#!/usr/bin/env python3
"""Finlight smoke test: is the stream wired, and what does one headline do?
Not collected by pytest (scripts/). Read only on the venue, no orders, and
the key is never printed.

    .venv/bin/python scripts/finlight_smoke.py [--ws-seconds 20] [--notional 2500]

  (a) the WebSocket for --ws-seconds: "ws ok" (admitted, N articles) or
      "fallback to REST (reason)";
  (b) one REST fetch of the oil query: count and two titles;
  (c) the mock headline "Tanker struck by missile in Strait of Hormuz,
      UKMTO confirms" through Stage 1 (headline_watch) and Stage 2
      (event_score: the local model when EVENT_SCORER_URL is set, else the
      rule scorer);
  (d) a live read-only xyz:BRENTOIL (and xyz:CL) book check through the
      venue rate budget: spread and slippage for --notional.
Exit 0 when (b), (c) and (d) worked; (a) falling back to REST is not a
failure (the free plan has no WebSocket).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, List

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_SCRIPTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, _SCRIPTS)

MOCK = "Tanker struck by missile in Strait of Hormuz, UKMTO confirms"


async def probe_ws(provider: Any, seconds: float) -> Dict[str, Any]:
    """Run the provider's WebSocket for `seconds`; report admitted / fallback."""
    from services.intel.sources.finlight import TierError
    got: List[Dict[str, Any]] = []
    stop = asyncio.Event()
    out: Dict[str, Any] = {"ws": None, "reason": None, "articles": 0}

    async def run() -> None:
        try:
            await provider._run_ws(got.append, stop)   # noqa: SLF001 - the smoke test reads the transport directly
            out["ws"] = provider.mode == "ws" or bool(got)
        except TierError as exc:
            out["ws"] = False
            out["reason"] = str(exc)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except asyncio.TimeoutError:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            task.cancel()
    if out["ws"] is None:
        out["ws"] = provider.mode == "ws"
    if not out["ws"] and not out["reason"]:
        out["reason"] = provider.last_error or f"not admitted within {seconds:g}s"
    out["articles"] = len(got)
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ws-seconds", type=float, default=20.0)
    ap.add_argument("--notional", type=float, default=None)
    a = ap.parse_args(argv)

    import _state_env
    _state_env.load_env_local(ROOT)
    from services.intel import book_guard, event_score
    from services.intel import headline_watch as HW
    from services.intel.sources.finlight import KEY_ENV, FinlightProvider, api_key
    ok = True
    report: Dict[str, Any] = {}

    if not api_key():
        print(f"(a) {KEY_ENV} not set: skipping (a) and (b)")
        report["ws"] = report["rest"] = "no key"
    else:
        provider = FinlightProvider()
        print(f"(a) websocket probe, {a.ws_seconds:g}s ...", flush=True)
        ws = asyncio.run(probe_ws(provider, a.ws_seconds))
        if ws["ws"]:
            print(f"    ws ok ({ws['articles']} article(s) in {a.ws_seconds:g}s)")
        else:
            print(f"    fallback to REST ({ws['reason']})")
        p = provider.poll
        print(f"    rest poll: requested {p['requested_s']:g}s, budget floor {p['floor_s']:g}s "
              f"(cap {p['monthly_cap']}/month) -> effective {p['effective_s']:g}s ({p['why']})")
        report["ws"] = ws
        print("(b) one REST fetch (48h window; the watcher asks for 45 min) ...", flush=True)
        t0 = time.time()
        try:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            items = asyncio.run(provider.fetch_recent(now - timedelta(hours=48)))
            fresh = [i for i in items if i.get("published_at") and now - i["published_at"] <= timedelta(minutes=45)]
            newest = max((i["published_at"] for i in items if i.get("published_at")), default=None)
            age = f"{(now - newest).total_seconds() / 3600:.1f}h" if newest else "n/a"
            with_sent = sum(1 for i in items if i.get("sentiment"))
            print(f"    {len(items)} article(s) in {time.time() - t0:.1f}s; {len(fresh)} inside 45 min; "
                  f"newest {age} old; {with_sent} with sentiment")
            if newest and now - newest > timedelta(hours=2):
                print(f"    DELAYED: the newest article is {age} old; nothing this plan returns passes the "
                      f"45-minute age filter, so no Finlight headline can alert until the plan delivers live data")
            for it in items[:2]:
                pub = it["published_at"].strftime("%m-%d %H:%MZ") if it.get("published_at") else "?"
                print(f"    - {pub} {it['source']} {it.get('sentiment')}: {it['title'][:100]}")
            report["rest"] = {"count": len(items), "fresh": len(fresh), "newest_age": age,
                              "titles": [i["title"] for i in items[:2]]}
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED: {type(exc).__name__}: {str(exc)[:160]}")
            ok = False

    print("(c) mock headline:", MOCK)
    label = HW.match_headline("oil", MOCK, triggers=True)
    direction = HW.text_direction("oil", MOCK)
    print(f"    stage 1: label={label} direction={direction:+d}")
    s2 = event_score.score_event_sync(MOCK, source="reuters.com", sentiment="negative", sentiment_confidence=0.9,
                                      stage1_label=label)
    print(f"    stage 2: {json.dumps(s2)}")
    report["stage1"] = label
    report["stage2"] = s2
    if not label or s2.get("expected_oil_direction") != "bullish":
        ok = False

    print("(d) live book (read only, through the venue rate budget) ...", flush=True)
    snap = book_guard.book_snapshot(notional=a.notional)
    for coin, row in snap.items():
        if row.get("error"):
            print(f"    {coin}: {row['error']}")
            ok = False
        else:
            print(f"    {coin}: mid {row['mid']} spread {row['spread_bps']} bps | ${row['notional_usd']:g} "
                  f"buy {row['buy']['bps']} bps (filled {row['buy']['filled']}) "
                  f"sell {row['sell']['bps']} bps (filled {row['sell']['filled']}) | "
                  f"levels {row['depth_levels']}")
    report["book"] = snap
    print("RESULT", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
