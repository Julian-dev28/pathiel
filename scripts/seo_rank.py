#!/usr/bin/env python3
"""Where pathiel ranks when someone searches GitHub for a trading agent.

An agent asked to "find an AI trading agent" searches GitHub (directly or
through tools like octocode), the web, and plugin directories. GitHub search
is the one we can measure for free and exactly, so this script asks it the
queries a stranger would type and records pathiel's position in the top 100.

    python scripts/seo_rank.py                     # print the table
    python scripts/seo_rank.py --log docs/seo/github_rank.jsonl   # and append a row

Outcome it tracks (2026-11-12 baseline in docs/seo/github_rank.jsonl): the
number of queries where pathiel is in the top 10, and its rank on each.
Read only. Uses GITHUB_TOKEN, or `gh auth token`, or no token (10 searches a
minute without one, enough for this list).
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "Julian-dev28/pathiel"

# What a stranger types, broad to narrow. Edit with care: changing the list
# breaks comparison with older rows in the log.
QUERIES = (
    "autonomous trading agent",
    "ai trading agent",
    "trading agent",
    "hyperliquid trading agent",
    "hyperliquid ai",
    "hyperliquid mcp",
    "claude trading",
    "trading mcp server",
    "claude code plugin trading",
)
PER_PAGE = 100
TOP = 10


def rank_of(names: list[str], repo: str = REPO) -> int | None:
    """1-based position of repo in names, case-insensitive, or None."""
    want = repo.lower()
    for i, name in enumerate(names, 1):
        if name.lower() == want:
            return i
    return None


def summarize(results: dict[str, int | None], top: int = TOP) -> dict:
    ranked = [r for r in results.values() if r is not None]
    return {
        "queries": len(results),
        "in_top_100": len(ranked),
        f"in_top_{top}": sum(1 for r in ranked if r <= top),
        "best": min(ranked) if ranked else None,
    }


def table(results: dict[str, int | None], leaders: dict[str, str]) -> str:
    rows = ["| Query | pathiel | #1 |", "|---|---|---|"]
    for q, r in results.items():
        rows.append(f"| {q} | {'#' + str(r) if r else 'not in top 100'} | {leaders.get(q, '')} |")
    return "\n".join(rows)


def _token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _ssl_context() -> ssl.SSLContext:
    """certifi's bundle when installed (python.org builds on macOS ship
    without system roots), else the platform default."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def search(query: str, token: str | None) -> list[dict]:
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": query, "per_page": PER_PAGE})
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "pathiel-seo-rank"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        return json.load(resp).get("items") or []


def run(queries=QUERIES, token: str | None = None, pause: float = 2.0):
    results, leaders = {}, {}
    for i, q in enumerate(queries):
        if i:
            time.sleep(pause)   # search API: 30/min with a token, 10/min without
        items = search(q, token)
        results[q] = rank_of([it["full_name"] for it in items])
        if items:
            leaders[q] = f"{items[0]['full_name']} ★{items[0]['stargazers_count']}"
    return results, leaders


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, help="append one JSON row to this file")
    args = ap.parse_args(argv)
    token = _token()
    results, leaders = run(token=token, pause=2.0 if token else 6.5)
    print(table(results, leaders))
    summary = summarize(results)
    print(f"\n{summary}")
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "repo": REPO,
               "ranks": results, "leaders": leaders, **summary}
        with args.log.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
