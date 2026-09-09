"""The X monthly budget must be impossible to overrun.

The free tier meters POSTS RETRIEVED, not requests, and search/recent has a
floor of max_results=10. A 100-post month is therefore TEN CALLS. Burn it and
the reader fails for the rest of the month while looking exactly like "no news"
— which is worse than not having it at all.

So the guard is checked BEFORE the request, the meter is charged by what X
actually returned, and every refusal is a structured result rather than an
exception: a headline reader failing must never interrupt what it is embedded
in.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "x_headlines", ROOT / "services" / "events" / "x_headlines.py")
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "STATE", tmp_path / "budget.json")
    monkeypatch.setenv("X_MONTHLY_POST_BUDGET", "100")
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    yield


def test_a_spent_budget_refuses_before_any_request(monkeypatch):
    """No network call may happen once the month is gone."""
    X._save({"month": X._month(), "posts": 100, "calls": 10})

    def _boom(*a, **k):
        raise AssertionError("made a request with no budget left")

    monkeypatch.setattr(X.requests, "get", _boom)
    r = X.fetch("anything")
    assert r["ok"] is False and "budget" in r["reason"]


def test_a_call_that_would_exceed_the_budget_is_refused_whole(monkeypatch):
    """Partial spend is not a thing — the API floor is 10, so 5 left cannot
    fund a call and must refuse rather than try."""
    X._save({"month": X._month(), "posts": 95, "calls": 9})
    monkeypatch.setattr(X.requests, "get",
                        lambda *a, **k: pytest.fail("should not fire"))
    assert X.fetch("q")["ok"] is False
    assert X.remaining() == 5


def test_the_meter_charges_what_was_RETURNED_not_what_was_asked(monkeypatch):
    class R:
        status_code = 200
        @staticmethod
        def json():
            return {"data": [{"text": "a", "created_at": "t"}] * 3}
    monkeypatch.setattr(X.requests, "get", lambda *a, **k: R())
    r = X.fetch("q", max_results=10)
    assert r["ok"] is True and len(r["posts"]) == 3
    assert X.remaining() == 97, "asked for 10, got 3 — charge 3"


def test_a_new_month_resets_the_meter():
    X._save({"month": "2000-01", "posts": 100, "calls": 10})
    assert X.remaining() == 100


def test_network_failure_is_not_charged(monkeypatch):
    monkeypatch.setattr(X.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    r = X.fetch("q")
    assert r["ok"] is False and X.remaining() == 100, "no response, no posts, no charge"


@pytest.mark.parametrize("code,needle", [(403, "not-enrolled"), (429, "rate limited")])
def test_api_errors_are_structured_refusals_not_exceptions(monkeypatch, code, needle):
    class R:
        status_code = code
        @staticmethod
        def json():
            return {}
    monkeypatch.setattr(X.requests, "get", lambda *a, **k: R())
    r = X.fetch("q")
    assert r["ok"] is False and needle in r["reason"]
    assert X.remaining() == 100, "a failed call must not be metered"


def test_a_missing_token_refuses_without_a_request(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(X.requests, "get",
                        lambda *a, **k: pytest.fail("should not fire"))
    assert "X_BEARER_TOKEN" in X.fetch("q")["reason"]


def test_no_credential_literal_is_committed():
    """The bearer must come from the environment, never the source."""
    src = (ROOT / "services" / "events" / "x_headlines.py").read_text()
    assert "AAAAAAAA" not in src, "a bearer token literal is in the source"
    assert "X_BEARER_TOKEN" in src


def test_the_meter_survives_a_crash_mid_write():
    """Atomic replace: a torn write would either lose the month's spend or
    corrupt it into a free reset."""
    src = (ROOT / "services" / "events" / "x_headlines.py").read_text()
    assert ".replace(STATE)" in src
