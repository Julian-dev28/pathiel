"""One end-to-end regression test per bug the 2026-10-17 ux-qa sweep fixed
that can only be proven in a real browser (a JS crash, a CSS cascade
override, a stuck client-side skeleton) — the server-side and pure-JS-logic
counterparts of each live beside the code they test in
tests/test_dashboard_pages.py and tests/test_dashboard_perf.py, which run on
every commit; these are the slow, real-Chromium confirmation that the fix
holds all the way through rendering.

Deselected from the commit gate (-m browser). Run with:
  .venv/bin/python -m pytest -m browser tests/browser/test_regressions_browser.py -q
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_ENV = (
    "HYPERLIQUID_PRIVATE_KEY", "PRIVATE_KEY_HEX",
    "HYPERLIQUID_ACCOUNT_ADDRESS", "HL_ACCOUNT_ADDRESS",
    "PATHIEL_OPERATOR_TOKEN",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def crafted_server(tmp_path) -> Iterator[str]:
    """A dashboard whose session log is hand-written, not generated.

    Needed for the malformed-scan-event regression below: the exact shape
    that crashed the page is narrower than what services/demo/generator.py
    currently emits (see the ux-qa final report's "for the integrator"
    section — the generator's own coin_scores shape is itself the thing
    that originally surfaced this), so this test controls the one event
    directly rather than depending on the generator's current output.
    """
    log_path = tmp_path / "session-log.jsonl"
    now = int(time.time() * 1000)
    events = [
        {"ts": now - 300_000, "event": "loop_heartbeat", "equity": 1000.0,
         "daily_pnl": 5.0, "open_positions": 0},
        # the exact shape that crashed fullLoad(): coin_scores as a
        # {coin: score} dict, which dashboard.py's scan classification
        # (`e.get("coin_scores") or e.get("coins") or []`) prefers over
        # `coins` whenever it is present and truthy.
        {"ts": now - 200_000, "event": "scan", "triggers": 2,
         "coins": ["BTC", "ETH"], "coin_scores": {"BTC": 71.2, "ETH": 64.0}},
        {"ts": now - 100_000, "event": "scan", "triggers": 1,
         "coins": ["SOL"], "coin_scores": {"SOL": 68.0}},
        {"ts": now - 40_000, "event": "loop_heartbeat", "equity": 1005.0,
         "daily_pnl": 10.0, "open_positions": 0},
    ]
    with open(log_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    snap_path = tmp_path / "positions-snapshot.json"
    snap_path.write_text(json.dumps({"saved_at": now, "asset_positions": []}))
    cfg_path = tmp_path / "agent-config.json"
    cfg_path.write_text(json.dumps({"mode": "LIVE", "leverage": 1}))

    env = {k: v for k, v in os.environ.items() if k not in _FORBIDDEN_ENV}
    env["HOME"] = str(tmp_path / "home")
    os.makedirs(env["HOME"], exist_ok=True)
    env["SESSION_LOG_PATH"] = str(log_path)
    env["PATHIEL_POSITIONS_SNAPSHOT_FILE"] = str(snap_path)
    env["PATHIEL_AGENT_CONFIG_FILE"] = str(cfg_path)
    env["PATHIEL_STATE_DIR"] = str(tmp_path / "state")
    env["PATHIEL_PUBLIC_DASHBOARD"] = "1"
    env["PATHIEL_DASHBOARD_READONLY"] = "1"

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn; from pathiel.server import app;"
         f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("dashboard did not come up")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


def test_activity_survives_a_malformed_scan_event(browser, crafted_server):
    """Regression (2026-10-17): one scan event with coin_scores logged as a
    dict (not the list scripts/trading_loop.py actually emits) used to crash
    activity.html's fullLoad() outright — ALL events in that load, not just
    the bad one, failed to render, and the trade pane got stuck forever on
    the generic 'feed load failed — is the server up?' banner. With the
    Array.isArray guard (pathiel/templates/activity.html's freshText) and
    the per-event try/catch in fullLoad/pollNew, the two GOOD heartbeats and
    the second (clean) scan event must still render."""
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(crafted_server + "/activity", wait_until="load", timeout=20000)
    page.wait_for_timeout(2500)

    assert not errors, f"uncaught page errors: {errors}"
    trade_html = page.evaluate("document.getElementById('flow-trade').innerHTML")
    assert "feed load failed" not in trade_html, (
        "the trade pane is stuck on the generic failure banner — "
        "fullLoad() crashed and never recovered")
    machine_html = page.evaluate("document.getElementById('flow-machine').innerHTML")
    assert "$1,005.00" in machine_html or "1005.00" in machine_html, (
        "the good heartbeat after the malformed scan event never rendered")
    context.close()


def test_hl_more_disclose_button_stays_truly_hidden(browser, demo_server):
    """Regression (2026-10-17): the `hidden` HTML attribute's UA-stylesheet
    rule (`[hidden]{display:none}`) is normal-priority, author-origin-losing
    — .disclose{display:flex} (pathiel.css) silently overrode it regardless
    of selector specificity, and #hl-more (trends.html) rendered as an
    empty, fully visible, clickable button. The fix is a blanket
    [hidden]{display:none !important} rule; this proves it holds for the
    real element in the real cascade, not just in source-text."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(demo_server + "/trends", wait_until="load", timeout=20000)
    page.wait_for_timeout(1500)
    box = page.evaluate("""
        () => {
            const el = document.getElementById('hl-more');
            const r = el.getBoundingClientRect();
            return {hidden: el.hidden, display: getComputedStyle(el).display,
                    w: r.width, h: r.height};
        }
    """)
    assert box["hidden"] is True
    assert box["display"] == "none", box
    assert box["w"] == 0 and box["h"] == 0, box
    context.close()


def test_trends_hl_strip_and_rows_do_not_stay_stuck_on_loading(browser, demo_server):
    """Regression (2026-10-17): renderHL()'s not-ok branch (no cached
    trend_engine read — the state of every fresh environment, including
    this demo) called emptyState() on #hl-majors but returned before ever
    touching #hl-strip or calling renderHLRows(), so #hl-strip's static
    '… loading' skeleton and #hl-rows' static 'loading…' row sat there
    permanently — looking like a stuck request rather than the same honest
    'no data yet' #hl-majors already showed correctly right next to them."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(demo_server + "/trends", wait_until="load", timeout=20000)
    page.wait_for_timeout(2500)
    strip = page.evaluate("document.getElementById('hl-strip').innerHTML")
    rows = page.evaluate("document.getElementById('hl-rows').innerHTML")
    assert "skeleton" not in strip, f"#hl-strip still shows its loading skeleton: {strip!r}"
    assert strip.strip(), "#hl-strip is empty — no feedback at all"
    assert "loading…" not in rows, f"#hl-rows is stuck on its loading row: {rows!r}"
    context.close()
