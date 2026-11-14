#!/usr/bin/env python3
"""Compare how findable pathiel is against another Claude Code tool repo.

Same inputs every run, so the comparison is measured, not eyeballed:

  GitHub repo signals    stars, forks, watchers, topics, description,
                         homepage, license, open issues, last push,
                         releases and the latest one's age
  GitHub search rank     each repo's position (top 100) for queries a
                         stranger types to find that kind of tool
  Directory listings     whether Glama, mcpservers.org and the official
                         MCP registry list it (HTTP status of its page)

    python3 scripts/seo_compare.py                         # pathiel vs midnight-expert
    python3 scripts/seo_compare.py OWNER/REPO OWNER/REPO

Uses GITHUB_TOKEN (or `gh auth token`) for the API. Read only. The search
helpers are scripts/seo_rank.py's, so ranks match the weekly SEO job.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo_rank as S  # noqa: E402  (search, rank_of, _token, _ssl_context)

PAIR = ("Julian-dev28/pathiel", "midnightntwrk/midnight-expert")
# queries a stranger would type for each repo's category, and for the shared
# category (a Claude Code plugin / MCP server)
QUERIES = {
    "Julian-dev28/pathiel": ("hyperliquid mcp", "hyperliquid trading agent", "ai trading agent",
                             "claude trading", "trading mcp server"),
    "midnightntwrk/midnight-expert": ("midnight blockchain", "compact language", "midnight network",
                                      "midnight claude", "zk smart contracts"),
    "shared": ("claude code plugin", "mcp server", "claude code skills"),
}


def sends_token(url: str) -> bool:
    """The GitHub token goes to the GitHub API over https and nowhere else:
    the parsed host, exactly (a substring test would also match
    https://evil.example/?api.github.com; CodeQL py/incomplete-url-substring-sanitization)."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "api.github.com"


def _get(url: str, token: str | None, accept_json: bool = True):
    req = urllib.request.Request(url, headers={"User-Agent": "pathiel-seo-compare"})
    if token and sends_token(url):
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30, context=S._ssl_context()) as r:
            body = r.read()
            return r.status, (json.loads(body) if accept_json else body)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001 - a dead page is a result, not a crash
        return 0, None


def repo_signals(repo: str, token: str | None) -> dict:
    _, r = _get(f"https://api.github.com/repos/{repo}", token)
    r = r or {}
    _, rels = _get(f"https://api.github.com/repos/{repo}/releases?per_page=100", token)
    rels = rels or []
    latest = rels[0]["published_at"] if rels else None
    now = dt.datetime.now(dt.timezone.utc)

    def age(ts):
        return (now - dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))).days if ts else None
    return {"stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
            "watchers": r.get("subscribers_count"), "topics": len(r.get("topics") or []),
            "description_chars": len(r.get("description") or ""), "homepage": bool(r.get("homepage")),
            "license": (r.get("license") or {}).get("spdx_id"), "open_issues": r.get("open_issues_count"),
            "days_since_push": age(r.get("pushed_at")), "releases": len(rels),
            "days_since_release": age(latest)}


def listings(repo: str) -> dict:
    owner, name = repo.split("/")
    pages = {
        "glama": f"https://glama.ai/mcp/servers/{owner}/{name}",
        "mcpservers.org": f"https://mcpservers.org/servers/{owner.lower()}/{name.lower()}",
        "mcp_registry": ("https://registry.modelcontextprotocol.io/v0/servers?search="
                         + urllib.parse.quote(f"io.github.{owner}/{name}")),
    }
    out = {}
    for key, url in pages.items():
        status, body = _get(url, None, accept_json=(key == "mcp_registry"))
        if key == "mcp_registry":
            found = bool(body and (body.get("servers") or []))
            out[key] = "listed" if found else ("not listed" if status == 200 else f"HTTP {status}")
        else:
            # 403/429 is bot protection (mcpservers.org), not an answer
            out[key] = ("listed" if status == 200 else
                        f"blocked (HTTP {status}): check by hand" if status in (403, 429) else
                        f"not listed (HTTP {status})")
    return out


def ranks(repos, token, pause: float = 2.0) -> dict:
    """{query: {repo: rank or None}} for every query of every repo."""
    queries = []
    for r in repos:
        queries += list(QUERIES.get(r, ()))
    queries += list(QUERIES["shared"])
    out = {}
    for q in dict.fromkeys(queries):
        names = [x["full_name"] for x in S.search(q, token)]
        out[q] = {r: S.rank_of(names, r) for r in repos}
        time.sleep(pause)
    return out


def report(repos, token) -> str:
    sig = {r: repo_signals(r, token) for r in repos}
    lst = {r: listings(r) for r in repos}
    rk = ranks(repos, token)
    short = [r.split("/")[1] for r in repos]
    lines = [f"# Findability: {short[0]} vs {short[1]} ({dt.date.today()})", "",
             "| signal | " + " | ".join(short) + " |", "|---|---|---|"]
    for k in sig[repos[0]]:
        lines.append(f"| {k} | " + " | ".join(str(sig[r][k]) for r in repos) + " |")
    for k in lst[repos[0]]:
        lines.append(f"| {k} | " + " | ".join(lst[r][k] for r in repos) + " |")
    lines += ["", "GitHub search rank (top 100; - = not in the top 100):", "",
              "| query | " + " | ".join(short) + " |", "|---|---|---|"]
    for q, by in rk.items():
        lines.append(f"| {q} | " + " | ".join(str(by[r]) if by[r] else "-" for r in repos) + " |")
    return "\n".join(lines)


def main(argv) -> int:
    repos = tuple(argv[:2]) if len(argv) >= 2 else PAIR
    print(report(repos, S._token()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
