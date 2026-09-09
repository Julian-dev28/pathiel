#!/usr/bin/env python3
"""X (Twitter) headline intake, with a hard monthly budget that cannot be overrun.

WHY A BUDGET GUARD IS THE WHOLE DESIGN
The X free tier meters POSTS RETRIEVED, not requests, and the floor for
`search/recent` is max_results=10. So a 100-post monthly allowance is TEN CALLS
A MONTH. A poller on any normal interval burns the month in an afternoon and
then fails silently for four weeks — which is worse than not having it, because
the failure looks like "no news" rather than "no quota".

So the spend is tracked in a state file, checked BEFORE the request, and the
month's remaining balance is returned with every result. Nothing here retries,
backs off into the budget, or "just checks once more".

WHAT THIS IS AND IS NOT FOR
C12 measured GDELT conflict-news intensity against oil returns: the LAGGING
correlation (+0.449) is double the predictive one (+0.227). News follows price.
So this is a headline READER for a human deciding something, not a signal
generator for a book. Wiring it to an entry would be wiring in a rear-view
mirror. GDELT stays the unlimited free backbone; X is the expensive garnish.

CREDENTIALS
Bearer token from `X_BEARER_TOKEN` in the environment (.env.local is
gitignored). Never a literal in this file, never a CLI argument — an argument
lands in shell history and `ps`.

    export X_BEARER_TOKEN=...            # in .env.local
    python services/events/x_headlines.py --query "brent crude" --budget-status
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".state" / "x_api_budget.json"
API = "https://api.x.com/2/tweets/search/recent"
MIN_RESULTS = 10                 # the API's floor; a call cannot cost less
# Conservative default = the FREE tier. Set X_MONTHLY_POST_BUDGET in .env.local
# to match the tier actually being paid for:
#     free            100 posts/month   (10 calls — effectively unusable)
#     Standard Basic  15000             ($200/mo)
# The default stays low on purpose: an unconfigured deployment that guesses HIGH
# burns a paid quota silently, while one that guesses low just refuses early and
# says so.
DEFAULT_MONTHLY_POSTS = 100


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _budget() -> int:
    try:
        return int(os.environ.get("X_MONTHLY_POST_BUDGET", DEFAULT_MONTHLY_POSTS))
    except ValueError:
        return DEFAULT_MONTHLY_POSTS


def _load() -> Dict[str, Any]:
    try:
        d = json.loads(STATE.read_text())
    except Exception:
        d = {}
    if d.get("month") != _month():          # a new month resets the meter
        d = {"month": _month(), "posts": 0, "calls": 0}
    return d


def _save(d: Dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(STATE)                       # atomic: a crash cannot lose the meter


def remaining() -> int:
    return max(0, _budget() - _load()["posts"])


def fetch(query: str, max_results: int = MIN_RESULTS,
          timeout: float = 20.0) -> Dict[str, Any]:
    """Headlines for `query`, or a refusal. NEVER raises on quota or auth.

    Returns {ok, posts:[...], remaining, reason}. The caller gets a structured
    refusal rather than an exception, because a news reader failing must not be
    able to interrupt anything it is embedded in.
    """
    want = max(MIN_RESULTS, int(max_results))
    left = remaining()
    if want > left:
        return {"ok": False, "posts": [], "remaining": left,
                "reason": f"budget: {left} posts left this month, call costs {want}"}

    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        return {"ok": False, "posts": [], "remaining": left,
                "reason": "X_BEARER_TOKEN not set (put it in .env.local)"}

    try:
        r = requests.get(
            API, timeout=timeout,
            headers={"Authorization": f"Bearer {token}",
                     "User-Agent": "pathia-headlines/1.0"},
            params={"query": query, "max_results": want,
                    "tweet.fields": "created_at,public_metrics"})
    except Exception as e:
        # Not charged: no response means no posts were metered.
        return {"ok": False, "posts": [], "remaining": left,
                "reason": f"network: {type(e).__name__}"}

    if r.status_code == 403:
        return {"ok": False, "posts": [], "remaining": left,
                "reason": ("403 client-not-enrolled — the app is not attached to a "
                           "Project. Create one at developer.x.com and regenerate "
                           "the bearer token.")}
    if r.status_code == 429:
        return {"ok": False, "posts": [], "remaining": left,
                "reason": "429 rate limited by X (separate from the monthly cap)"}
    if r.status_code != 200:
        return {"ok": False, "posts": [], "remaining": left,
                "reason": f"HTTP {r.status_code}"}

    body = r.json()
    posts = body.get("data") or []
    # Charge what X says it returned, not what we asked for — a short response
    # still meters at its actual size, and guessing high would waste quota.
    d = _load()
    d["posts"] += len(posts)
    d["calls"] += 1
    d["last_query"] = query
    d["last_at"] = datetime.now(timezone.utc).isoformat()
    _save(d)
    return {"ok": True, "remaining": max(0, _budget() - d["posts"]),
            "reason": "", "posts": [
                {"text": p.get("text", "").replace("\n", " ")[:280],
                 "at": p.get("created_at"),
                 "likes": (p.get("public_metrics") or {}).get("like_count", 0)}
                for p in posts]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query")
    ap.add_argument("--max-results", type=int, default=MIN_RESULTS)
    ap.add_argument("--budget-status", action="store_true")
    a = ap.parse_args()

    d = _load()
    print(f"  month {d['month']}: {d['posts']}/{_budget()} posts used, "
          f"{remaining()} left ({d.get('calls', 0)} calls)")
    if a.budget_status and not a.query:
        return 0
    if not a.query:
        ap.error("--query required unless --budget-status")

    res = fetch(a.query, a.max_results)
    if not res["ok"]:
        print(f"  REFUSED: {res['reason']}")
        return 1
    print(f"  {len(res['posts'])} headlines, {res['remaining']} posts left\n")
    for p in res["posts"]:
        print(f"    [{p['at']}] {p['likes']:>5} likes  {p['text'][:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
