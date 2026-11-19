"""pathiel/static/entry_rows.js: the Levels card's entry rows (the Desk
page, "/"). Pure string/class builder, no DOM — runs here through node,
same convention as tests/test_position_line_js.py for position_line.js.

Checks every status the Levels card can render (services/strategies/
entry_card.py's STATUS_* keys, plus "in_position" which this file adds):
ready, waiting, blackout, chase, break, no_thesis, in_position. Each case
feeds `entryRows()` the shape GET /api/desks/{id}/levels's `entry` field
actually has (services/strategies/tests/test_entry_card.py pins the
Python side of that shape) so the two can never silently drift apart.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "pathiel" / "static" / "entry_rows.js"
JS = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
HTML = (ROOT / "pathiel" / "templates" / "oil_desk.html").read_text()

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(expr: str):
    script = (f"const m = require({json.dumps(str(HELPER))});"
              f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(out.stdout)


def _entry(status, status_text, **live_extra):
    live = {"status": status, "status_text": status_text, "ok": status == "ready",
            "entry_px": 87.98, "stop_px": 86.844, "tp_px": 90.25, "r_multiple": 2.0}
    live.update(live_extra)
    return {"ok": live["ok"], "has_thesis": True, "side": "long", "thesis_entry_px": 88.60,
            "live": live, "distance": {"price": 0.916, "pct": 0.0103, "atr": 1.19}}


def test_ready_status_buys_now_sentence():
    rows = _run(f"m.entryRows({json.dumps(_entry('ready', 'ready'))}, null)")
    assert rows["live"]["status"] == "ready" and rows["live"]["statusText"] == "ready"
    assert rows["live"]["badgeClass"] == "b-hold"
    assert rows["live"]["sentence"] == "Buys now at 87.98"
    assert rows["thesis"] == {"side": "Long", "price": 88.6}


def test_waiting_status_buy_at_or_lower_sentence():
    rows = _run(f"m.entryRows({json.dumps(_entry('waiting', 'waiting'))}, null)")
    assert rows["live"]["status"] == "waiting" and rows["live"]["badgeClass"] == "b-flat"
    assert rows["live"]["sentence"] == "Buy at 87.98 or lower"
    assert rows["live"]["stop"] == 86.844 and rows["live"]["tp"] == 90.25 and rows["live"]["r"] == 2.0
    assert rows["distance"] == {"price": 0.916, "pct": 0.0103, "atr": 1.19}


def test_waiting_status_on_a_short_is_sell_at_or_higher():
    entry = _entry("waiting", "waiting")
    entry["side"] = "short"
    rows = _run(f"m.entryRows({json.dumps(entry)}, null)")
    assert rows["live"]["sentence"] == "Sell at 87.98 or higher"


def test_blackout_status_shows_the_until_text_and_avoid_badge():
    entry = _entry("blackout", "blackout until 2026-11-04 17:30Z")
    rows = _run(f"m.entryRows({json.dumps(entry)}, null)")
    assert rows["live"]["status"] == "blackout"
    assert rows["live"]["statusText"] == "blackout until 2026-11-04 17:30Z"
    assert rows["live"]["badgeClass"] == "b-avoid"


def test_chase_status():
    rows = _run(f"m.entryRows({json.dumps(_entry('chase', 'chase'))}, null)")
    assert rows["live"]["status"] == "chase" and rows["live"]["badgeClass"] == "b-avoid"


def test_break_status():
    rows = _run(f"m.entryRows({json.dumps(_entry('break', 'break'))}, null)")
    assert rows["live"]["status"] == "break" and rows["live"]["badgeClass"] == "b-avoid"


def test_no_thesis_status_hides_the_thesis_and_distance_rows():
    entry = {"ok": False, "has_thesis": False, "side": None, "thesis_entry_px": None,
            "live": {"status": "no_thesis", "status_text": "no thesis", "ok": False,
                    "entry_px": None, "stop_px": None, "tp_px": None, "r_multiple": None},
            "distance": {"price": None, "pct": None, "atr": None}}
    rows = _run(f"m.entryRows({json.dumps(entry)}, null)")
    assert rows["thesis"] is None
    assert rows["live"]["status"] == "no_thesis" and rows["live"]["sentence"] is None
    assert rows["distance"] is None


def test_unavailable_entry_price_says_so_instead_of_a_null_sentence():
    entry = _entry("waiting", "waiting", entry_px=None)
    rows = _run(f"m.entryRows({json.dumps(entry)}, null)")
    assert rows["live"]["sentence"] == "Buy: price unavailable"


def test_in_position_overrides_the_pending_entry_with_the_positions_own_numbers():
    """The Desk page is already in it: the card shows the SAME numbers the
    Wallets table / Next-trade box already fetched (PathielPositionLine.
    positionLine's own shape), never a second, re-derived pending entry."""
    position = {"source": "position", "side": "long", "leverage": 20, "entry_px": 109.4579,
               "stop_px": 108.95, "targets": [113.75], "liq_px": 106.6556,
               "r_to_targets": [{"px": 113.75, "r_multiple": 8.45}], "ok": True, "no_stop": False}
    rows = _run(f"m.entryRows({json.dumps(_entry('ready', 'ready'))}, {json.dumps(position)})")
    assert rows["inPosition"] is True
    assert rows["live"]["status"] == "in_position" and rows["live"]["statusText"] == "in position"
    assert rows["live"]["badgeClass"] == "b-hold"
    assert rows["live"]["sentence"] == "In position at 109.4579"
    assert rows["live"]["stop"] == 108.95 and rows["live"]["tp"] == 113.75 and rows["live"]["r"] == 8.45
    assert rows["distance"] is None
    # The thesis-entry row is unaffected by being in position (still shown).
    assert rows["thesis"] == {"side": "Long", "price": 88.6}


def test_status_keys_match_entry_card_pys_status_constants():
    """services/strategies/entry_card.py's STATUS_* constants and this
    file's BADGE_CLASS keys must never drift apart: a status the Python
    side can emit that this file has no badge for would silently render
    with a blank class."""
    py = (ROOT / "services" / "strategies" / "entry_card.py").read_text()
    import re
    py_statuses = set(re.findall(r'^STATUS_\w+ = "(\w+)"', py, re.M))
    assert py_statuses == {"no_thesis", "ready", "waiting", "blackout", "chase", "break"}
    badge = _run("m.BADGE_CLASS")
    assert py_statuses <= set(badge.keys())


def test_the_page_loads_entry_rows_js_before_oil_desk_js():
    assert '<script src="/static/entry_rows.js' in HTML
    assert HTML.index('src="/static/entry_rows.js') < HTML.index('src="/static/oil_desk.js')
    assert "EntryRows.entryRows(l.entry, position)" in JS
    # an older cached page without entry_rows.js still renders the rest of the card
    assert "window.PathielEntryRows" in JS
    assert "renderLevels();" in JS
