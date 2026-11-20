#!/usr/bin/env python3
"""What Glama's maintenance grade sees for a repo, next to what GitHub has.

Glama's Maintenance checklist reads seven signals. Four come from GitHub
through the Glama AI GitHub App (Dependabot alerts, Dependabot security
updates, code scanning, CI on the default branch): when the app is not
installed on the repo, all four read "unavailable" however well the repo is
set up. That was pathiel's case on 2026-11-20: Glama AI was authorized for
sign-in but never installed, so every one of those lines said
"unavailable" while GitHub had them all on and green.

This prints both sides so the gap is a fact, not a guess:

  Glama side    the public server page's maintenanceHealth (commits per
                week, issue responsiveness and how fresh that data is,
                releases), decoded from the page's embedded data
  GitHub side   .github/dependabot.yml, Dependabot alerts on, Dependabot
                security updates on, code scanning set up, CI check runs on
                the default branch's head, glama.json maintainers

    python3 scripts/glama_health.py                 # pathiel
    python3 scripts/glama_health.py OWNER/REPO

Whether the app is installed cannot be read with a user token; the report
names the install page when the GitHub side is green, so a remaining
"unavailable" on Glama means the app. Read only. Uses GITHUB_TOKEN or
`gh auth token` for the GitHub API (only ever sent to api.github.com).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seo_compare as C  # noqa: E402  (_get, sends_token)
import seo_rank as S  # noqa: E402  (_token)

PATHIEL = "Julian-dev28/pathiel"
INSTALL_URL = "https://github.com/apps/glama-ai/installations/new"
API = "https://api.github.com"

# Check-run conclusions that mean red; cancelled, skipped, neutral and stale do not.
FAILED = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

Getter = Callable[[str], Tuple[int, Optional[bytes]]]


# ── Glama side ──────────────────────────────────────────────────────────────

def decode_stream(html: str) -> Optional[list]:
    """The flat array of the page's turbo-stream payload (React Router): a
    JSON string passed to enqueue(...) whose first line is the array. Objects
    in it map "_<key index>" to a value index; negative ints are null-like."""
    for lit in re.findall(r'enqueue\((".*?")\)\s*;?\s*</script>', html, re.S):
        try:
            arr = json.loads(json.loads(lit).splitlines()[0])
        except (ValueError, IndexError):
            continue
        if isinstance(arr, list):
            return arr
    return None


def _deref(arr: list, i: int, depth: int = 0):
    v = arr[i]
    if depth > 8:
        return v
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            key = arr[int(k[1:])] if k.startswith("_") and k[1:].isdigit() else k
            out[key] = _deref(arr, x, depth + 1) if isinstance(x, int) and x >= 0 else (None if isinstance(x, int) else x)
        return out
    if isinstance(v, list):
        return [_deref(arr, x, depth + 1) if isinstance(x, int) and x >= 0 else x for x in v]
    return v


def maintenance(html: str) -> Optional[dict]:
    """Glama's maintenanceHealth object for the server page, or None."""
    arr = decode_stream(html)
    if not arr or "maintenanceHealth" not in arr:
        return None
    return _deref(arr, arr.index("maintenanceHealth") + 1)


# ── GitHub side ─────────────────────────────────────────────────────────────

def _json(body: Optional[bytes]):
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def github_side(repo: str, get: Getter) -> dict:
    out: dict = {}
    status, _ = get(f"{API}/repos/{repo}/contents/.github/dependabot.yml")
    out["dependabot_config"] = status == 200
    _, r = get(f"{API}/repos/{repo}")
    r = _json(r) or {}
    # Alerts and default setup are only readable by an admin; for anyone else
    # a 404 means "not yours to see", not "off".
    admin = bool((r.get("permissions") or {}).get("admin"))
    status, _ = get(f"{API}/repos/{repo}/vulnerability-alerts")
    out["dependabot_alerts"] = {204: True, 404: False}.get(status) if admin else (True if status == 204 else None)
    sec = r.get("security_and_analysis") or {}
    upd = (sec.get("dependabot_security_updates") or {}).get("status")
    out["dependabot_security_updates"] = None if upd is None else upd == "enabled"
    branch = r.get("default_branch") or "main"
    status, body = get(f"{API}/repos/{repo}/code-scanning/default-setup")
    setup = (_json(body) or {}).get("state") if status == 200 else None
    _, wf = get(f"{API}/repos/{repo}/contents/.github/workflows")
    codeql_file = any("codeql" in (f.get("name") or "").lower() for f in (_json(wf) or []) if isinstance(f, dict))
    out["code_scanning"] = True if (setup == "configured" or codeql_file) else (False if admin else None)
    _, head = get(f"{API}/repos/{repo}/commits/{branch}")
    sha = (_json(head) or {}).get("sha")
    runs = []
    if sha:
        _, cr = get(f"{API}/repos/{repo}/commits/{sha}/check-runs?per_page=100")
        runs = (_json(cr) or {}).get("check_runs") or []
    done = [c for c in runs if c.get("status") == "completed"]
    bad = sorted({c.get("name", "?") for c in done if c.get("conclusion") in FAILED})
    out["ci"] = {"branch": branch, "sha": (sha or "")[:7], "check_runs": len(runs), "failing": bad}
    status, body = get(f"{API}/repos/{repo}/contents/glama.json")
    meta = _json(body) or {}
    if status == 200 and meta.get("content"):
        import base64
        try:
            meta = json.loads(base64.b64decode(meta["content"]))
        except ValueError:
            meta = {}
    out["glama_maintainers"] = meta.get("maintainers") or []
    return out


# ── verdict ─────────────────────────────────────────────────────────────────

def _yn(v) -> str:
    return "on" if v is True else "OFF" if v is False else "unknown (needs an admin token)"


def report(repo: str, glama: Optional[dict], gh: dict) -> Tuple[bool, str]:
    """(GitHub side all green, text). Pure."""
    ci = gh["ci"]
    ci_ok = ci["check_runs"] > 0 and not ci["failing"]
    rows = [
        ("Dependabot config", "present" if gh["dependabot_config"] else "MISSING .github/dependabot.yml",
         gh["dependabot_config"]),
        ("Dependabot alerts", _yn(gh["dependabot_alerts"]), gh["dependabot_alerts"] is not False),
        ("Dependabot security updates", _yn(gh["dependabot_security_updates"]),
         gh["dependabot_security_updates"] is not False),
        ("Code scanning", {True: "set up", False: "NOT set up (enable CodeQL default setup)"}.get(
            gh["code_scanning"], "unknown (needs an admin token)"), gh["code_scanning"] is not False),
        ("CI on " + ci["branch"], (f"{ci['check_runs']} check runs on {ci['sha']}, "
                                   + (f"failing: {', '.join(ci['failing'])}" if ci["failing"] else "all green")),
         ci_ok),
        ("glama.json maintainers", ", ".join(gh["glama_maintainers"]) or "MISSING", bool(gh["glama_maintainers"])),
    ]
    green = all(ok for _, _, ok in rows)
    lines = [f"GitHub side for {repo}:"]
    lines += [f"  {'ok ' if ok else 'FIX'} {name:<28} {text}" for name, text, ok in rows]
    lines.append("Glama side (public page):")
    if not glama:
        lines.append("  page data not found (layout changed or the server is not listed)")
    else:
        weeks = glama.get("activitySeries") or []
        commits = sum(int(w.get("commitCount") or 0) for w in weeks)
        iss = glama.get("issueResponsiveness") or {}
        rel = glama.get("releaseSeries") or []
        lines.append(f"  commits, last {len(weeks)} weeks: {commits}")
        if iss:
            hours = iss.get("medianResponseHours")
            median = f"median first response {hours / 24:.0f} days, " if isinstance(hours, (int, float)) else ""
            lines.append(f"  issues: {iss.get('handledCount')} of {iss.get('totalCount')} answered or closed, "
                         f"{median}collected {str(iss.get('collectedAt'))[:10]}"
                         + (" (stale)" if iss.get("stale") else ""))
        lines.append(f"  releases: {sum(int(r.get('releaseCount') or 0) for r in rel)} "
                     f"({', '.join(r.get('month', '?') for r in rel) or 'none'})")
    if green:
        lines.append("GitHub has every signal on. If Glama still says Dependabot, code scanning or CI "
                     f"\"unavailable\", the Glama AI app is not installed on {repo}: install it at "
                     f"{INSTALL_URL} (Only select repositories: {repo.split('/')[1]}), then Sync on Glama's admin page.")
    else:
        lines.append("Fix the FIX rows on GitHub first; Glama cannot report a signal GitHub does not have.")
    return green, "\n".join(lines)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = args[0] if args else PATHIEL
    token = S._token()

    def get(url: str):
        return C._get(url, token if C.sends_token(url) else None, accept_json=False)
    gh = github_side(repo, get)
    status, page = C._get(f"https://glama.ai/mcp/servers/{repo}", None, accept_json=False)
    glama = maintenance(page.decode("utf-8", "replace")) if status == 200 and page else None
    green, text = report(repo, glama, gh)
    print(text)
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
