"""Gate tests for the demo sub-application.

The property under test is isolation, and it is the only thing standing between
a public dashboard and a page of invented equity presented as real trading.

`pathia/dashboard.py` freezes its dataset paths at import, so serving two
datasets from one process means importing it twice. Two earlier attempts at that
looked like they worked and did not:

  1. Loading dashboard.py a second time — both copies resolved `session_log`
     from `sys.modules` and read the same log.
  2. Popping the path-frozen modules from `sys.modules` — `dashboard.py` reaches
     them as `from pathia import session_log`, an attribute lookup on the
     already-imported package, so the re-import resolved straight back.

Both produced a "demo" instance pointed at live paths. Neither raised. The tests
below compare the resolved paths directly rather than trusting that the import
dance did what it says.
"""

from __future__ import annotations

import sys
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def demo_app():
    from services.demo.router import build_demo_app
    return build_demo_app(tempfile.mkdtemp(prefix="demo-router-"))


@pytest.fixture(scope="module")
def live_dashboard(demo_app):
    import pathia.dashboard
    return pathia.dashboard


@pytest.fixture(scope="module")
def demo_dashboard(demo_app):
    return sys.modules["pathia_dashboard_demo"]


class TestIsolation:
    def test_the_two_dashboards_are_different_modules(self, live_dashboard, demo_dashboard):
        assert live_dashboard is not demo_dashboard

    def test_they_read_different_session_logs(self, live_dashboard, demo_dashboard):
        """The failure both earlier attempts produced silently."""
        assert str(live_dashboard._LOG_PATH) != str(demo_dashboard._LOG_PATH)

    def test_the_live_modules_are_put_back(self):
        """The demo import shadows `pathia.session_log` and friends. If the
        restore is skipped or partial, the LIVE dashboard starts reading
        generated data — the exact failure this whole design exists to prevent,
        arriving silently and in the wrong direction.
        """
        import pathia.session_log
        import pathia.positions_snapshot
        from pathia.agents import config_store, memory
        for module in (pathia.session_log, pathia.positions_snapshot,
                       config_store, memory):
            assert module is sys.modules[module.__name__]

    def test_the_live_session_log_is_not_the_demo_one(self, live_dashboard):
        import pathia.session_log
        assert pathia.session_log.SESSION_LOG_FILE == str(live_dashboard._LOG_PATH)

    def test_they_do_not_share_caches(self, live_dashboard, demo_dashboard):
        """Separate module objects mean separate cache dicts. Sharing one would
        let a demo response be served from a live key or the reverse."""
        assert live_dashboard._TTL_CACHE is not demo_dashboard._TTL_CACHE
        assert live_dashboard._LOG_STATE is not demo_dashboard._LOG_STATE


class TestDemoServesGeneratedData:
    def test_summary_is_populated(self, demo_app):
        body = TestClient(demo_app).get("/api/dashboard/summary").json()
        assert body["equity"] > 0
        assert body["status"] == "scanning"

    def test_there_is_a_trade_history(self, demo_app):
        """The reason someone clicks Demo at all."""
        trades = TestClient(demo_app).get("/api/dashboard/closed-trades").json()
        assert len(trades) >= 5
        assert all("pnl_pct" in t for t in trades)

    def test_positions_are_populated(self, demo_app):
        assert TestClient(demo_app).get("/api/dashboard/positions").json()

    def test_it_says_what_it_is_when_asked(self, demo_app):
        body = TestClient(demo_app).get("/api/demo/whoami").json()
        assert body["demo"] is True

    def test_every_page_renders(self, demo_app):
        client = TestClient(demo_app)
        for path in ("/", "/activity", "/news", "/trends", "/analytics"):
            assert client.get(path).status_code == 200, path


class TestToggleIsNavigationNotState:
    """The toggle is a path prefix, so it cannot be spoofed into the live view.

    A client-side mode flag would have to be trusted by the fetch layer, and a
    bug there renders generated numbers on the live page. A prefix cannot: the
    code serving demo data is not mounted under the live path.
    """

    def test_the_front_end_switches_by_navigating(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[3] / "pathia" / "static" / "pathia.js").read_text()
        assert "PathiaMode" in js
        assert "/demo" in js
        # No fetch rewriting: every dashboard call stays relative, and the page
        # it was served from decides which app answers.
        assert "fetch(DEMO_PREFIX" not in js
        assert "apiBase" not in js

    def test_the_toggle_renders_on_load(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[3] / "pathia" / "static" / "pathia.js").read_text()
        assert "PathiaMode.render()" in js
