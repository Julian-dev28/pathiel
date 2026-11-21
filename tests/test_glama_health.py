"""scripts/glama_health.py: Glama's maintenance view next to GitHub's (2026-11-20).

Glama read Dependabot, code scanning and CI as "unavailable" for pathiel while
GitHub had all of them on and green: the Glama AI app was authorized for
sign-in but never installed. These pin the decoding of Glama's page data and
the verdict, offline, with a fake GitHub."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("glama_health", ROOT / "scripts" / "glama_health.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

REPO = "me/srv"
API = "https://api.github.com/repos/me/srv"


def _page(arr: list) -> str:
    """A server page as React Router streams it: the flat array as the first
    line of a JSON string handed to enqueue(...)."""
    lit = json.dumps(json.dumps(arr) + "\n")
    return f"<html><script>window.__reactRouterContext.streamController.enqueue({lit});</script></html>"


# keys and values by index, as Glama's page holds them (2026-11-20 shape)
STREAM = ["x", "maintenanceHealth",
          {"_3": 4, "_6": 7, "_10": -5},              # 2
          "activitySeries", [5], {"_8": 9},           # 3, 4, 5
          "issueResponsiveness", {"_11": 12, "_13": 14, "_15": 16, "_17": 18, "_19": 20},  # 6, 7
          "commitCount", 77,                          # 8, 9
          "maintainerCount",                          # 10
          "handledCount", 1, "totalCount", 1, "medianResponseHours", 3305.5,  # 11..16
          "collectedAt", "2026-11-18T00:11:34Z", "stale", True]               # 17..20


def test_maintenance_decodes_the_stream_and_resolves_nulls():
    m = G.maintenance(_page(STREAM))
    assert m["activitySeries"] == [{"commitCount": 77}]
    assert m["maintainerCount"] is None
    assert m["issueResponsiveness"]["medianResponseHours"] == 3305.5
    assert m["issueResponsiveness"]["stale"] is True


def test_a_page_without_the_data_is_none_not_a_crash():
    assert G.maintenance("<html>no stream</html>") is None
    assert G.maintenance(_page(["nothing", "here"])) is None
    assert G.maintenance("<script>enqueue(\"not json\");</script>") is None


GLAMA_APP = {"contents": "read", "metadata": "read", "repository_advisories": "read", "security_events": "read"}


def fake_github(*, admin=True, alerts=204, setup="configured", runs=None, glama=("me",), dependabot=True,
                app=GLAMA_APP):
    runs = [{"name": "Tests", "status": "completed", "conclusion": "success"}] if runs is None else runs
    glama_json = base64.b64encode(json.dumps({"maintainers": list(glama)}).encode()).decode()
    table = {
        f"{API}/contents/.github/dependabot.yml": (200 if dependabot else 404, b"{}"),
        f"{API}/vulnerability-alerts": (alerts, b""),
        API: (200, json.dumps({"default_branch": "main", "permissions": {"admin": admin},
                               "security_and_analysis": {"dependabot_security_updates": {"status": "enabled"}}
                               if admin else None}).encode()),
        f"{API}/code-scanning/default-setup": (200, json.dumps({"state": setup}).encode()) if admin else (403, b""),
        f"{API}/contents/.github/workflows": (200, json.dumps([{"name": "ci.yml"}]).encode()),
        f"{API}/commits/main": (200, json.dumps({"sha": "abc1234def"}).encode()),
        f"{API}/commits/abc1234def/check-runs?per_page=100": (200, json.dumps({"check_runs": runs}).encode()),
        f"{API}/contents/glama.json": (200, json.dumps({"content": glama_json}).encode()),
        G.APP_URL: (200, json.dumps({"permissions": app}).encode()),
    }
    return lambda url: table.get(url, (404, None))


def test_all_green_names_the_app_install_as_the_remaining_cause():
    gh = G.github_side(REPO, fake_github())
    assert gh["dependabot_alerts"] and gh["dependabot_security_updates"] and gh["code_scanning"]
    assert gh["ci"] == {"branch": "main", "sha": "abc1234", "check_runs": 1, "failing": []}
    green, text = G.report(REPO, G.maintenance(_page(STREAM)), gh)
    assert green and G.INSTALL_URL in text and "FIX" not in text
    assert "median first response 138 days" in text and "(stale)" in text


def test_a_missing_github_signal_is_named_before_the_app():
    green, text = G.report(REPO, None, G.github_side(REPO, fake_github(alerts=404, setup="not-configured",
                                                                       dependabot=False, glama=())))
    assert not green and G.INSTALL_URL not in text
    for row in ("FIX Dependabot config", "FIX Dependabot alerts", "FIX Code scanning", "FIX glama.json"):
        assert row in text, row
    assert "page data not found" in text


def test_without_admin_a_404_is_unknown_not_off():
    gh = G.github_side(REPO, fake_github(admin=False, alerts=404))
    assert gh["dependabot_alerts"] is None and gh["dependabot_security_updates"] is None
    assert gh["code_scanning"] is None   # default setup unreadable, no CodeQL workflow file
    green, text = G.report(REPO, None, gh)
    assert green and "unknown (needs an admin token)" in text


def test_only_real_failures_turn_ci_red():
    runs = [{"name": n, "status": "completed", "conclusion": c} for n, c in
            (("a", "success"), ("b", "cancelled"), ("c", "skipped"), ("d", "neutral"))]
    runs.append({"name": "e", "status": "in_progress", "conclusion": None})
    assert G.github_side(REPO, fake_github(runs=runs))["ci"]["failing"] == []
    runs.append({"name": "tests", "status": "completed", "conclusion": "failure"})
    gh = G.github_side(REPO, fake_github(runs=runs))
    assert gh["ci"]["failing"] == ["tests"]
    assert not G.report(REPO, None, gh)[0]
    assert not G.report(REPO, None, G.github_side(REPO, fake_github(runs=[])))[0]


def test_the_token_only_goes_to_the_github_api():
    assert G.C.sends_token(f"{API}/vulnerability-alerts")
    assert not G.C.sends_token("https://glama.ai/mcp/servers/me/srv")


def test_the_app_not_requesting_dependabot_is_named_as_glamas_gap():
    """2026-11-20: after the install, code scanning and CI cleared on Glama and
    both Dependabot lines stayed: the app never requests vulnerability_alerts."""
    green, text = G.report(REPO, None, G.github_side(REPO, fake_github()))
    assert green
    assert "NO  Dependabot" in text and "vulnerability_alerts not requested" in text
    assert "ok  code scanning" in text
    assert 'still says code scanning, CI "unavailable"' in text   # Dependabot is not blamed on the install
    _, text = G.report(REPO, None, G.github_side(REPO, fake_github(app={**GLAMA_APP, "vulnerability_alerts": "read"})))
    assert "NO " not in text and 'still says Dependabot, code scanning, CI "unavailable"' in text
    _, text = G.report(REPO, None, G.github_side(REPO, fake_github(app=None)))
    assert "permissions not readable" in text
