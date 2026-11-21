from __future__ import annotations

import re
from urllib.parse import urlparse

from services.intel import http_client
from services.research_mcp import tools


def test_fred_series_tool_refuses_cleanly_without_a_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    out = tools.fred_series_tool("DGS10")
    assert out["error"] == "FRED_API_KEY_missing"
    # Parse the link and compare its host exactly: a substring check on a
    # URL is what CodeQL's py/incomplete-url-substring-sanitization flags.
    urls = [urlparse(u) for u in re.findall(r"https?://\S+", out["help"])]
    assert any(u.hostname == "fred.stlouisfed.org" and u.path.endswith("/api_key.html")
               for u in urls), out["help"]


def test_fred_series_tool_never_logs_the_key(monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", "super-secret-key-value")
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: ({"observations": [{"date": "2026-01-02", "value": "4.1"}]}, None))
    tools.fred_series_tool("DGS10")
    captured = capsys.readouterr()
    assert "super-secret-key-value" not in captured.out
    assert "super-secret-key-value" not in captured.err


def test_fred_series_tool_happy_path(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: ({"observations": [
                            {"date": "2026-01-02", "value": "4.10"},
                            {"date": "2026-01-05", "value": "4.12"},
                        ]}, None))
    out = tools.fred_series_tool("DGS10", start="2026-01-01", end="2026-02-01")
    assert out["series_id"] == "DGS10"
    assert out["start"] == "2026-01-01" and out["end"] == "2026-02-01"
    assert len(out["observations"]) == 2
    assert "error" not in out


def test_fred_series_tool_surfaces_a_non_key_error(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: (None, "timeout"))
    out = tools.fred_series_tool("DGS10")
    assert out["error"] == "timeout"
    assert out["series_id"] == "DGS10"


def test_fred_series_tool_surfaces_a_bad_response_shape(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: ({"nope": True}, None))
    out = tools.fred_series_tool("DGS10")
    assert out["error"] == "bad_shape"
