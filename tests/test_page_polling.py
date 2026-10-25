"""Pages poll the server with pathiel.js's pollEvery, never setInterval.

2026-10-19: the dashboard polled its summary every 6 s and positions every
10 s with setInterval. With account reads taking 6-12 s the polls stacked up
behind each other and held the browser's six connections to the site, so a
click on Desks, in any tab, waited behind them. pollEvery schedules the next
run only after the last one settles and stops while the tab is hidden; its
behavior is pinned in tests/browser/test_desks_browser.py
(test_polls_never_stack_and_stop_in_a_hidden_tab). This keeps every page on it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted((ROOT / "pathiel" / "templates").glob("*.html")) + [
    p for p in sorted((ROOT / "pathiel" / "static").glob("*.js"))]

# Re-render from data already on the page; no request.
UI_ONLY = {"foldAged", "setPageMeta"}
_SERVER_CALL = re.compile(r"\b(?:fetch|load\w*|refresh\w*|poll\w*)\s*\(")


def _interval_calls(text: str):
    for m in re.finditer(r"setInterval\(\s*", text):
        rest = text[m.end():]
        name = re.match(r"([A-Za-z_$][\w$]*)\s*,", rest)
        if name:
            yield name.group(1), None
        else:                                  # an inline function: look inside it
            end = re.search(r"\},\s*\d+\s*\)", rest)
            yield None, rest[: end.start() if end else 400]


def test_no_page_polls_the_server_with_setinterval():
    offenders = []
    for path in PAGES:
        for name, body in _interval_calls(path.read_text()):
            if name is not None and name not in UI_ONLY:
                offenders.append(f"{path.name}: setInterval({name}, ...)")
            elif body is not None and _SERVER_CALL.search(body):
                offenders.append(f"{path.name}: setInterval(() => {{{body.strip()[:60]}...")
    assert not offenders, ("poll the server with pollEvery(fn, ms) from pathiel.js, "
                           f"not setInterval: {offenders}")


def test_the_dashboard_polls_are_on_pollevery():
    text = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "pollEvery(loadAll, REFRESH_MS)" in text


def test_pollevery_is_shared_from_pathiel_js():
    text = (ROOT / "pathiel" / "static" / "pathiel.js").read_text()
    assert "function pollEvery(fn, ms)" in text
    assert "document.hidden" in text and "visibilitychange" in text


def test_the_checker_catches_a_server_poll_on_setinterval():
    """The check itself must bite (a mutation of this test file's own logic)."""
    bad = "setInterval(refreshSummary, 6000);\nsetInterval(() => { load(1); }, 5000);"
    found = list(_interval_calls(bad))
    assert found[0] == ("refreshSummary", None)
    assert _SERVER_CALL.search(found[1][1])
    countdown = "const tick = setInterval(() => {\n el.textContent = 'refreshing… ' + 1 + 's';\n}, 1000);"
    assert not _SERVER_CALL.search(list(_interval_calls(countdown))[0][1])
