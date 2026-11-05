#!/usr/bin/env python3
"""Eval for the social / official feeds of the headline watcher (network,
read only: three GETs, nothing written anywhere).

For each live source (services/intel/sources/social.py): item count, the
newest item's age, and which items of the last 24 hours would have alerted
(Trump posts through headline_watch.match_post per group, official feeds
through match_headline, the same functions the watcher runs), with the
match rate over the whole page as the false-positive gauge.

    python3 -m services.intel.evals.eval_social_feed

Exit 0 when every source answered, 1 when one failed (its line says why).
Thresholds: a source whose newest item is older than STALE_H prints STALE;
a Trump-post match rate above MAX_POST_HIT_RATE prints NOISY. Both are
printed, not fatal: a quiet weekend is not a broken feed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.intel import headline_watch as HW  # noqa: E402
from services.intel.sources import social  # noqa: E402

STALE_H = 48
MAX_POST_HIT_RATE = 0.25
GROUPS = ("oil", "silver")


def _age(now: datetime, items: List[Dict[str, Any]]) -> str:
    stamps = [i["published_at"] for i in items if i.get("published_at")]
    if not stamps:
        return "no dates"
    newest = max(stamps)
    m = (now - newest).total_seconds() / 60
    flag = "  STALE" if m > STALE_H * 60 else ""
    return f"newest {newest:%Y-%m-%d %H:%MZ} ({m:.0f} min ago){flag}"


def _recent(now: datetime, items: List[Dict[str, Any]], hours: int = 24) -> List[Dict[str, Any]]:
    cut = now - timedelta(hours=hours)
    return [i for i in items if i.get("published_at") and i["published_at"] >= cut]


def main() -> int:
    now = datetime.now(timezone.utc)
    failed = 0

    posts, err = social.fetch_trump_posts()
    print(f"trumpstruth.org (Truth Social archive): {len(posts)} post(s)" + (f"  FAILED {err}" if err else ""))
    if err:
        failed += 1
    else:
        print("  " + _age(now, posts))
        hits = 0
        for p in posts:
            labels = {g: HW.match_post(g, p["text"]) for g in GROUPS}
            if any(labels.values()):
                hits += 1
                recent = "ALERT " if p in _recent(now, posts) else "old   "
                print(f"  {recent}{p['published_at']:%m-%d %H:%MZ} {p['post_type']:<8} "
                      f"{', '.join(f'{g}:{v}' for g, v in labels.items() if v)}: {p['text'][:110]}")
        rate = hits / len(posts) if posts else 0.0
        print(f"  match rate {hits}/{len(posts)} = {rate:.0%}" + ("  NOISY" if rate > MAX_POST_HIT_RATE else ""))
        print(f"  empty/link-only posts: {sum(1 for p in posts if HW._POST_EMPTY.match(p['text']))}")

    items, errs = social.fetch_official_feeds()
    for e in errs:
        print(f"official feed FAILED: {e}")
        failed += 1
    for source, _url in social.OFFICIAL_FEEDS:
        rows = [i for i in items if i.get("source") == source]
        if not rows and any(source in e for e in errs):
            continue
        print(f"{source}: {len(rows)} item(s)")
        print("  " + _age(now, rows))
        for r in rows:
            labels = {g: HW.match_headline(g, r["title"]) for g in GROUPS}
            if any(labels.values()):
                recent = "ALERT " if r in _recent(now, rows) else "old   "
                print(f"  {recent}{r['published_at']:%m-%d %H:%MZ} "
                      f"{', '.join(f'{g}:{v}' for g, v in labels.items() if v)}: {r['title'][:110]}")
        assert all(HW.trusted_source(r["source"]) for r in rows), f"{source} is not a trusted source"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
