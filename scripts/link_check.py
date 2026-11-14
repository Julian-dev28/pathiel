#!/usr/bin/env python3
"""Every link users and agents follow still resolves.

Why (2026-11-14): the Midnight memo's content-freshness metric ("CI pings
every URL; nothing stale > 7 days"): its harness went stale within a day when
an indexer was decommissioned and an example repo archived. This checks the
files people and agents read: README.md, the plugin's commands, agents and
skills, the public docs and the site.

- A link into this repository (github.com/Julian-dev28/pathiel/blob|tree/main/
  PATH) is checked against the git tree, exactly and offline.
- Any other https link is fetched (HEAD, then GET). 2xx and 3xx pass; 404,
  410, 5xx and connection failures fail. 401, 403 and 429 are bot walls or
  login pages, reported as unverified, not failed.

    python3 scripts/link_check.py          # online
    python3 scripts/link_check.py --list   # the links, no requests
"""
from __future__ import annotations

import argparse
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
REPO_PREFIX = re.compile(r"^https://github\.com/Julian-dev28/pathiel/(?:blob|tree)/main/([^#?]+)")
URL = re.compile(r"https://[^\s)>\"'`\]]+")
SOURCES = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "site/index.html")
SOURCE_DIRS = ("commands", "agents", "skills", "docs")
SKIP_DOCS = ("docs/strategy/learning/", "docs/overhaul/", "docs/security/")   # dated records, not guidance
UNVERIFIED = {401, 403, 429}


def files() -> List[Path]:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    out = [ROOT / f for f in SOURCES if f in tracked]
    for f in tracked:
        if f.split("/")[0] in SOURCE_DIRS and f.endswith(".md") and not f.startswith(SKIP_DOCS):
            out.append(ROOT / f)
    return out


def extract(text: str) -> Set[str]:
    urls = set()
    for u in URL.findall(text):
        u = u.rstrip(".,;:")
        if any(c in u for c in "<>{}$*") or "localhost" in u or "127.0.0.1" in u or u.endswith("..."):
            continue   # placeholders and templates, not links
        urls.add(u)
    return urls


def repo_path_ok(url: str, tracked: Set[str]) -> bool:
    m = REPO_PREFIX.match(url)
    path = m.group(1).rstrip("/")
    return path in tracked or any(t.startswith(path + "/") for t in tracked)


def fetch_status(url: str) -> int:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "pathiel-link-check"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 404, 501):
                continue   # some hosts refuse HEAD; ask again with GET
            return e.code
        except Exception:  # noqa: BLE001 - unreachable is a result
            if method == "HEAD":
                continue
            return 0
    return 0


def verdict(status: int) -> str:
    if 200 <= status < 400:
        return "ok"
    if status in UNVERIFIED:
        return "unverified"
    return "broken"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    by_url: Dict[str, List[str]] = {}
    for f in files():
        for u in extract(f.read_text(errors="replace")):
            by_url.setdefault(u, []).append(str(f.relative_to(ROOT)))
    if a.list:
        print("\n".join(sorted(by_url)))
        return 0
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split())
    results: List[Tuple[str, str, str]] = []
    external = []
    for u in sorted(by_url):
        if REPO_PREFIX.match(u):
            results.append((u, "ok" if repo_path_ok(u, tracked) else "broken", "repo path"))
        else:
            external.append(u)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for u, st in zip(external, pool.map(fetch_status, external)):
            results.append((u, verdict(st), f"HTTP {st}" if st else "unreachable"))
    broken = [r for r in results if r[1] == "broken"]
    unverified = [r for r in results if r[1] == "unverified"]
    print(f"links: {len(results)} checked, {len(broken)} broken, {len(unverified)} unverified")
    for u, _, why in broken:
        print(f"  BROKEN {why}: {u}  (in {', '.join(sorted(set(by_url[u]))[:3])})")
    for u, _, why in unverified:
        print(f"  unverified {why}: {u}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
