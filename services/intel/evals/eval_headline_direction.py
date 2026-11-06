"""Eval: the headline direction classifier on real alerts and fixtures, and
the hold-minutes the one-sided headline blackout removes for a long thesis.

Operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH HEADLINES". Every alert
now carries a direction (headline_watch.DIRECTION_WORDS / ODDS_POLARITY);
an alert that supports the trade side no longer holds it.

    .venv/bin/python -m services.intel.evals.eval_headline_direction [--days 60] [--alerts PATH] [--offline]

Three parts, read only:
  1. the live alerts file (default headline_watch.ALERTS_PATH, read only),
     one row per alert with its direction; the rows of 2026-11-05 must read
     as EXPECTED_REAL says (the Hormuz-opens headline is -1 for oil).
  2. the fixture headlines and posts (services/intel/tests/fixtures), as a
     table of direction per title, with the share left at 0 (unknown).
  3. public 1h Brent candles for --days: the shock (1%) and drift (1.75%)
     alarms replayed as a one-a-minute mid (each close held an hour, so
     this under-counts intrahour moves), each alert a 30-minute hold. Old
     rule: every alert holds. New rule, long thesis: only alerts against
     the long (down moves) hold. Prints both totals and the minutes removed.
Pass: part 1 matches EXPECTED_REAL, part 3 removes minutes for a long
without touching a single down-move hold. --offline skips part 3.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from services.intel import headline_watch as HW  # noqa: E402

M = HW.MIN_MS
FIXTURES = os.path.join(ROOT, "services", "intel", "tests", "fixtures")
# today's real alert titles and the direction each must read as, for oil
EXPECTED_REAL = {
    "xyz:BRENTOIL -1.02% in 15 min": -1,
    "Will Kazem Gharibabadi attend a US x Iran diplomatic meeting by December 31, 2026?: 50% -> 60%": -1,
    "Will Iran target an Arab country by November 15, 2026?: 68% -> 60%": -1,
    "Iran Opens Strait of Hormuz to China and India as U.S. Blockade Continues": -1,
    "U.S., Iran edge closer to deal": -1,
}


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%MZ")


def classify_rows(rows: Sequence[Dict[str, Any]]) -> List[Tuple[int, str, str, str]]:
    """(direction, kind, group, title) per alert row. Pure."""
    return [(HW.alert_direction(a), str(a.get("kind")), str(a.get("group")), str(a.get("title") or "")) for a in rows]


def check_real(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Which EXPECTED_REAL titles are in `rows` and whether each reads right."""
    seen: Dict[str, Any] = {}
    for a in rows:
        title = str(a.get("title") or "")
        for prefix, want in EXPECTED_REAL.items():
            if title.startswith(prefix):
                seen[prefix] = {"want": want, "got": HW.alert_direction(a), "ok": HW.alert_direction(a) == want}
    return {"checked": len(seen), "ok": all(v["ok"] for v in seen.values()), "rows": seen}


def fixture_titles() -> List[Tuple[str, str, str]]:
    """(group, kind, text) for the fixture news feeds and the Trump posts."""
    from services.intel.sources.news import parse_rss_items
    from services.intel.sources.social import parse_trumpstruth
    out: List[Tuple[str, str, str]] = []
    for name, group in (("google_news_oil.xml", "oil"), ("oilprice_main.xml", "oil"), ("google_news_query_opec.xml", "oil"),
                        ("yahoo_quote_headlines_oil.xml", "oil"), ("state_press_releases.xml", "oil"),
                        ("whitehouse_news.xml", "oil")):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
            items = parse_rss_items(fh.read(), split_source_from_title=name.startswith("google"))
        out += [(group, "news", str(i.get("title") or "")) for i in items]
    with open(os.path.join(FIXTURES, "trumpstruth_feed.xml"), encoding="utf-8") as fh:
        posts = parse_trumpstruth(fh.read())
    out += [("oil", "post", (p.get("text") or "").replace("\n", " ")) for p in posts if HW.match_post("oil", p.get("text") or "")]
    return out


def replay_alerts(coin: str, closes: Sequence[Tuple[int, float]]) -> List[Dict[str, Any]]:
    """Shock and drift alerts (with direction) for (open_ms, close) 1h bars,
    oldest first, with the watcher's re-arm rules. Pure."""
    hist: List[Tuple[int, float]] = []
    last_shock = last_drift = 0
    out: List[Dict[str, Any]] = []
    for t0, c in closes:
        for k in range(60):
            now = int(t0) + 3_600_000 + k * M
            shock = HW.price_shock(coin, hist, now, float(c))
            drift = HW.price_drift(coin, hist, now, float(c))
            hist.append((now, float(c)))
            del hist[:-HW.HISTORY_KEEP_MIN]
            if shock and now - last_shock >= HW.PRICE_REARM_MIN * M:
                last_shock = now
                out.append(shock)
            elif drift and not shock and now - last_drift >= HW.DRIFT_REARM_MIN * M:
                last_drift = now
                out.append(drift)
    return out


def hold_minutes(alerts: Sequence[Dict[str, Any]], side: Optional[str]) -> int:
    """Minutes inside at least one HEADLINE_BLACKOUT_MIN window of an alert
    that holds `side` (side None: every alert). Overlaps count once. Pure."""
    windows = sorted((int(a["at_ms"]), int(a["at_ms"]) + HW.HEADLINE_BLACKOUT_MIN * M)
                     for a in alerts if not HW.supports(a, side))
    total, cur_s, cur_e = 0, None, None
    for s, e in windows:
        if cur_e is None or s >= cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total // M


def candle_study(coin: str, bars: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    closes = sorted(((int(b["t"]), float(b["c"])) for b in bars), key=lambda r: r[0])
    alerts = replay_alerts(coin, closes)
    up = [a for a in alerts if a["direction"] > 0]
    down = [a for a in alerts if a["direction"] < 0]
    old = hold_minutes(alerts, None)
    new_long = hold_minutes(alerts, "long")
    new_short = hold_minutes(alerts, "short")
    return {"coin": coin, "bars": len(closes), "alerts": len(alerts), "up": len(up), "down": len(down),
            "hold_min_old_rule": old, "hold_min_long_thesis": new_long, "hold_min_short_thesis": new_short,
            "removed_for_long": old - new_long, "removed_for_long_pct": round(100.0 * (old - new_long) / old, 1) if old else 0.0,
            "removed_for_short": old - new_short,
            "down_holds_untouched": new_long == hold_minutes(down, None),
            "last_up": [_utc(a["at_ms"]) + " " + a["title"].split(" (")[0] for a in up[-4:]]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--alerts", default=HW.ALERTS_PATH)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    ok = True
    print(f"== 1. live alerts ({a.alerts}, read only)")
    rows = HW.read_alerts(a.alerts)
    for d, kind, group, title in classify_rows(rows):
        print(f"  {d:+d}  {kind:<5} {group:<6} {title[:110]}")
    real = check_real(rows)
    print(f"  expected rows checked: {real['checked']}/{len(EXPECTED_REAL)}, all right: {real['ok']}")
    for k, v in real["rows"].items():
        if not v["ok"]:
            print(f"  WRONG: {k[:80]} want {v['want']:+d} got {v['got']:+d}")
    ok &= real["ok"]
    print("== 2. fixture headlines and posts")
    fx = fixture_titles()
    unknown = 0
    for group, kind, text in fx:
        d = HW.text_direction(group, text)
        unknown += d == 0
        print(f"  {d:+d}  {kind:<5} {group:<6} {text[:110]}")
    print(f"  {len(fx)} titles, {unknown} unknown ({100.0 * unknown / max(1, len(fx)):.0f}%)")
    if a.offline:
        print(json.dumps({"pass": bool(ok)}))
        return 0 if ok else 1
    print(f"== 3. {a.days} days of 1h xyz:BRENTOIL candles: hold minutes, old rule vs long thesis")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import oil_levels
    import time
    bars = oil_levels.fetch_candles("xyz:BRENTOIL", "1h", a.days * 24, int(time.time() * 1000))
    study = candle_study("xyz:BRENTOIL", bars)
    print(json.dumps(study, indent=1))
    ok &= study["alerts"] > 0 and study["removed_for_long"] > 0 and study["down_holds_untouched"]
    print(json.dumps({"pass": bool(ok)}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
