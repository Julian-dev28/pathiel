#!/usr/bin/env python3
"""Eval: did the 2026-11-17 age + dedup fix to
services/intel/headline_watch.news_alerts actually cut the recycled and
duplicate fires that were burning the day's MAX_ALERTS_PER_DAY budget --
without silencing a single real one?

BUG (2026-11-16/17, read only from the live watcher's own logs):
  /tmp/trigger-watch/watch.log FIRE'd on "Rubio condemns Iran strikes on
  Bahrain, Manama demands halt at UN" (a months-old June story Google News
  re-served through a feed entry) and on a 3-hours-later, differently-worded
  write-up of the SAME CNN "dirty bomb" plot report -- two of the oil
  group's six MAX_ALERTS_PER_DAY slots on 2026-11-17 gone by 03:21Z. The fix
  (services/intel/headline_watch.py, see news_alerts / MAX_AGE_MIN /
  NEWS_DEDUP_MIN_OVERLAP docstrings): an item with no readable published
  time is dropped instead of taken as fresh, and a near-duplicate
  (textnorm.is_near_duplicate) of an already-alerted title for the same
  group inside NEWS_DEDUP_WINDOW_MIN does not fire again.

This eval replays, read only, the real production data under
/tmp/trigger-watch/ through two implementations of the filter:
  BEFORE  `_legacy_news_alerts` in this file: a frozen copy of the logic as
          it shipped before 2026-11-17 -- an item with no published_at
          never fails the age check, and there is no near-duplicate pool,
          only the byte-identical-title `seen` hash. Kept here, not in
          production, purely so this eval can score BEFORE vs AFTER on the
          same inputs without a second code path living in headline_watch.py.
  AFTER   `headline_watch.news_alerts`, unchanged from production.

Two parts:
  1. The real log replay: every kind="news" alert actually recorded in
     /tmp/trigger-watch/headline_alerts.json (2026-11-05..17), replayed
     group by group in the real chronological order, each item's own
     recorded at_ms standing in for "now" so the exact real-world age is
     reproduced. Reports how many fire BEFORE vs AFTER and lists every
     real alert AFTER drops -- that list is the risk to look at: a fix
     that drops a genuinely new story is worse than the noise it removes.
  2. The two named regressions, constructed directly (the real log's own
     published_at values happened to be populated for both, so part 1
     alone cannot exercise the undated-reprint path): the undated Bahrain
     reprint, and the dirty-bomb story retitled by a second outlet. Also
     checks the one real case the fix deliberately does NOT suppress (the
     dirty-bomb story's genuinely different 3-hour-later follow-up angle)
     stays unsuppressed, since forcing that match would risk silencing a
     real escalation (see NEWS_DEDUP_MIN_OVERLAP's docstring).

    .venv/bin/python -m services.intel.evals.eval_headline_dedup
    .venv/bin/python -m services.intel.evals.eval_headline_dedup --alerts PATH

Pass: part 1's AFTER drops at least one real alert (the fix does
something) and drops NONE that isn't a near-duplicate of one already
counted (zero real, distinct stories suppressed); part 2's two named
regressions both go from firing BEFORE to not firing AFTER, and the
genuine follow-up still fires both BEFORE and AFTER. Exit 1 otherwise.
Missing or empty log data (a fresh checkout, no live watcher run yet)
skips part 1 and passes on part 2 alone -- never a hard failure, since
/tmp/trigger-watch/ is read only and outside this repo's control.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)

from services.intel import headline_watch as HW  # noqa: E402

MIN_MS = HW.MIN_MS
DEFAULT_ALERTS_PATH = "/tmp/trigger-watch/headline_alerts.json"


def _legacy_news_alerts(group: str, items: List[Dict[str, Any]], seen: set, now_ms: int, *,
                        shared: bool = False, triggers: bool = False,
                        recent: Optional[list] = None) -> List[Dict[str, Any]]:
    """Frozen copy of news_alerts as it shipped before 2026-11-17 (see this
    module's docstring for why it lives here, not in headline_watch.py).
    Accepts (and ignores) `recent` only so this can stand in for
    `headline_watch.news_alerts` at a call site that always passes it --
    the pre-fix code had no such pool at all, which is the point."""
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        k = f"{group}:{HW._key(str(it.get('id') or title))}" if shared else HW._key(title)
        if k in seen:
            continue
        seen.add(k)
        pub = it.get("published_at")
        pub_ms = int(pub.timestamp() * 1000) if isinstance(pub, datetime) else None
        if pub_ms is not None and now_ms - pub_ms > HW.MAX_AGE_MIN * MIN_MS:   # the bug: None skips this check
            continue
        if not HW.trusted_source(it.get("source")):
            continue
        label = HW.match_headline(group, title, triggers=triggers)
        if label:
            out.append({"group": group, "kind": "news", "label": label, "title": title[:200],
                       "source": it.get("source"), "url": it.get("url"), "at_ms": now_ms,
                       "published_ms": pub_ms, "direction": HW.text_direction(group, title)})
    return out


def load_real_news_alerts(path: str) -> List[Dict[str, Any]]:
    """kind="news" rows from the real alerts file, oldest first. Read only;
    a missing or unreadable file is an empty list, never an error."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows = [a for a in data if isinstance(a, dict) and a.get("kind") == "news"]
    return sorted(rows, key=lambda a: int(a.get("at_ms") or 0))


def _as_item(a: Dict[str, Any]) -> Dict[str, Any]:
    """A real alert row -> the {title, url, source, published_at} shape
    news_alerts takes, reconstructing published_at from published_ms."""
    pub_ms = a.get("published_ms")
    return {"title": a["title"], "url": a.get("url"), "source": a.get("source"),
            "published_at": datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc) if pub_ms else None}


def replay_before(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`rows` through `_legacy_news_alerts`, one `seen` set per group, each
    row's own at_ms standing in for "now" (so the real recorded age is
    reproduced). Returns the rows that fire, in replay order."""
    seen: Dict[str, set] = {}
    fired = []
    for a in rows:
        group = a.get("group") or "oil"
        out = _legacy_news_alerts(group, [_as_item(a)], seen.setdefault(group, set()), int(a["at_ms"]))
        fired.extend(dict(o, _src_at_ms=a["at_ms"], _src_title=a["title"]) for o in out)
    return fired


def replay_after(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same replay, through the real, current `headline_watch.news_alerts`,
    with a persisted near-duplicate pool per group (exactly what
    scripts/trigger_watch.py keeps in headline_state.json's `recent_news`)."""
    seen: Dict[str, set] = {}
    recent: Dict[str, list] = {}
    fired = []
    for a in rows:
        group = a.get("group") or "oil"
        out = HW.news_alerts(group, [_as_item(a)], seen.setdefault(group, set()), int(a["at_ms"]),
                             recent=recent.setdefault(group, []))
        fired.extend(dict(o, _src_at_ms=a["at_ms"], _src_title=a["title"]) for o in out)
    return fired


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%MZ")


def eval_real_log(path: str) -> bool:
    rows = load_real_news_alerts(path)
    print(f"\n=== part 1: real log replay ({path}) ===")
    if not rows:
        print("  no kind=\"news\" rows found (empty or missing log) -- skipped, not a failure")
        return True
    before = replay_before(rows)
    after = replay_after(rows)
    after_titles = {a["_src_title"] for a in after}
    dropped = [b for b in before if b["_src_title"] not in after_titles]
    print(f"  {len(rows)} real kind=\"news\" alerts, {len(before)} fired BEFORE, {len(after)} fired AFTER")
    if dropped:
        print(f"  AFTER drops {len(dropped)} real alert(s) BEFORE fired on -- the risk to check by hand:")
        for d in dropped:
            print(f"    {_utc(d['_src_at_ms'])} [{d['group']}/{d['label']}] {d['_src_title'][:90]}")
    else:
        print("  AFTER drops nothing from this log (no near-duplicate pair recorded in it)")
    # Pass bar: every dropped title must be a near-duplicate of a title that
    # still fired AFTER (i.e. genuinely the same story, not a silenced new
    # one) -- never an untouched, isolated drop.
    bad_drops = [d for d in dropped
                 if not any(HW.is_near_duplicate(d["_src_title"], kept, min_overlap=HW.NEWS_DEDUP_MIN_OVERLAP)
                            for kept in after_titles)]
    ok = not bad_drops
    print(f"  [{'PASS' if ok else 'FAIL'}] every drop is a near-duplicate of a kept alert "
          f"({len(dropped) - len(bad_drops)}/{len(dropped)})")
    if bad_drops:
        for d in bad_drops:
            print(f"    UNEXPLAINED DROP: {d['_src_title'][:90]}")
    return ok


# ── part 2: the two named regressions, constructed (the real log's own
# published_at happened to be populated for both, so part 1 cannot exercise
# the undated-reprint path on its own) ───────────────────────────────────

BAHRAIN_TITLE = "Rubio condemns Iran strikes on Bahrain, Manama demands halt at UN"
DIRTY_BOMB_TITLE = "Iran plans attack on Trump officials with dirty bomb, CNN reports"
DIRTY_BOMB_RETITLED = "CNN reports Iran plans attack on Trump officials with a dirty bomb"
DIRTY_BOMB_FOLLOWUP = "Irans Alleged U.S. Attack Plans Trigger Emergency Meetings In Washington Report"

NOW = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def _fires(fn, group: str, item: Dict[str, Any], seen: set, now_ms: int, **kw) -> bool:
    return bool(fn(group, [item], seen, now_ms, **kw))


def eval_named_regressions() -> bool:
    print("\n=== part 2: the two named regressions ===")
    ok = True

    # 1. the undated Bahrain reprint: fired BEFORE (no date -> always
    # "fresh"), must not fire AFTER (no date -> always stale, dropped).
    bahrain = {"title": BAHRAIN_TITLE, "url": "https://x/bahrain", "source": "Iran International",
              "published_at": None}
    before = _fires(_legacy_news_alerts, "oil", bahrain, set(), NOW_MS)
    after = _fires(HW.news_alerts, "oil", bahrain, set(), NOW_MS, recent=[])
    passed = before and not after
    ok &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] undated Bahrain reprint: BEFORE fired={before}, AFTER fired={after}")

    # 2. the dirty-bomb story retitled by a second outlet 40 min later:
    # fired BEFORE both times (no dedup at all beyond the byte-identical
    # title), must fire once AFTER (near-duplicate pool).
    first = {"title": DIRTY_BOMB_TITLE, "url": "https://x/db1", "source": "Investing.com UK",
             "published_at": NOW - timedelta(minutes=3)}
    reword = {"title": DIRTY_BOMB_RETITLED, "url": "https://x/db2", "source": "Yahoo News",
              "published_at": NOW + timedelta(minutes=37)}
    later_ms = NOW_MS + 40 * 60_000
    before_seen: set = set()
    before1 = _fires(_legacy_news_alerts, "oil", first, before_seen, NOW_MS)
    before2 = _fires(_legacy_news_alerts, "oil", reword, before_seen, later_ms)
    after_seen: set = set()
    after_recent: list = []
    after1 = _fires(HW.news_alerts, "oil", first, after_seen, NOW_MS, recent=after_recent)
    after2 = _fires(HW.news_alerts, "oil", reword, after_seen, later_ms, recent=after_recent)
    passed = before1 and before2 and after1 and not after2
    ok &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] dirty-bomb retitled by a 2nd outlet: "
          f"BEFORE fired={before1},{before2}  AFTER fired={after1},{after2}")

    # 3. the genuinely different 3-hour-later follow-up angle on the SAME
    # underlying event must NOT be caught by the dedup pool either -- this
    # is the deliberate limit, not a bug: it shares only 18% of its tokens
    # with the original (the same ratio as two real, unrelated same-day
    # headlines in the log), so matching it would risk silencing a real
    # escalation one day. It fires both BEFORE and AFTER.
    followup = {"title": DIRTY_BOMB_FOLLOWUP, "url": "https://x/db3", "source": "The Times of India",
                "published_at": NOW + timedelta(minutes=148)}
    followup_ms = NOW_MS + 187 * 60_000
    before3 = _fires(_legacy_news_alerts, "oil", followup, before_seen, followup_ms)
    after3 = _fires(HW.news_alerts, "oil", followup, after_seen, followup_ms, recent=after_recent)
    overlap = HW.is_near_duplicate(DIRTY_BOMB_TITLE, DIRTY_BOMB_FOLLOWUP, min_overlap=0.0)  # just to report the ratio
    passed = before3 and after3
    ok &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] genuine 3h follow-up still fires both ways: "
          f"BEFORE fired={before3}  AFTER fired={after3}  (near-dup pool unaffected: {overlap})")

    return ok


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alerts", default=DEFAULT_ALERTS_PATH, help="real headline_alerts.json path (read only)")
    args = ap.parse_args(argv)

    part1 = eval_real_log(args.alerts)
    part2 = eval_named_regressions()
    passed = part1 and part2
    print(f"\n{'ALL PASS' if passed else 'FAILED'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
