"""Static assets must be revalidated by the browser on every load.

Regression, 2026-10-18: /static/* had an ETag but no Cache-Control, so the
browser used heuristic freshness and ran an hours-old desks.js against the new
desk.html. The "Your size" line never rendered until a hard reload.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from pathiel.server import app

client = TestClient(app, client=("127.0.0.1", 41000))


def test_static_js_says_no_cache():
    r = client.get("/static/desks.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_unchanged_asset_revalidates_to_a_304():
    first = client.get("/static/pathiel.css")
    r = client.get("/static/pathiel.css", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
