"""The Desk page's 20x box follows the live position, not the call.

2026-10-28, operator: "this part of the fe doesnt really keep up with the
ticks". The call card read entry 110.40 at 5x and "Stop too close for 20x"
(liquidation 107.57, stop using 51.2%) while the account was long 13.08
from 109.46 at 20x, venue liquidation 106.66, stops all at 108.95. The pure
helper is pathiel/static/position_line.js; it runs here through node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "pathiel" / "static" / "position_line.js"
JS = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
HTML = (ROOT / "pathiel" / "templates" / "oil_desk.html").read_text()

NODE = shutil.which("node")

# the live row from /api/accounts/{id}/positions, 2026-10-28 00:53Z
LIVE = {"coin": "xyz:BRENTOIL", "side": "long", "size": 13.08, "entry_px": 109.4579,
        "leverage": 20, "liq_px": 106.6555671607,
        "stop_px": ["108.95", "108.95", "108.95"], "tp_px": ["113.75", "113.75", "113.75"]}
CALL = {"side": "long", "instrument": "xyz:BRENTOIL", "entry_px": 110.4, "stop_px": 108.95,
        "targets": [113.75, 116.0], "leverage": 5.0}


def _run(expr: str):
    script = (f"const m = require({json.dumps(str(HELPER))});"
              f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_the_box_uses_the_positions_entry_leverage_and_venue_liquidation():
    line = _run(f"m.positionLine({json.dumps(LIVE)})")
    assert line["source"] == "position"
    assert line["entry_px"] == 109.4579 and line["leverage"] == 20
    assert line["liq_px"] == pytest.approx(106.6556, abs=1e-4)     # the venue's, not an estimate
    assert line["stop_px"] == 108.95 and line["targets"] == [113.75]  # three fills, one level
    # (109.4579 - 108.95) / (109.4579 - 106.6556) = 0.181, not the call's 51.2%
    assert line["stop_frac_of_liq"] == pytest.approx(0.1812, abs=1e-3)
    assert line["ok"] is True and line["no_stop"] is False
    assert line["r_to_targets"] == [{"px": 113.75, "r_multiple": 8.45}]


def test_a_position_with_no_stop_is_never_ok():
    line = _run(f"m.positionLine({json.dumps({**LIVE, 'stop_px': []})})")
    assert line["ok"] is False and line["no_stop"] is True and line["stop_frac_of_liq"] is None


def test_the_tightest_stop_wins_and_a_stop_in_profit_uses_none_of_the_distance():
    line = _run(f"m.positionLine({json.dumps({**LIVE, 'stop_px': ['108.5', '108.95']})})")
    assert line["stop_px"] == 108.95
    short = {**LIVE, "side": "short", "entry_px": 100.0, "liq_px": 104.0, "stop_px": ["99.0"]}
    assert _run(f"m.positionLine({json.dumps(short)})")["stop_frac_of_liq"] == 0


def test_only_a_same_side_position_on_the_calls_coin_answers_for_it():
    by_coin = {"xyz:BRENTOIL": LIVE}
    assert _run(f"m.positionForCall({json.dumps(CALL)}, {json.dumps(by_coin)})")["entry_px"] == 109.4579
    short_call = {**CALL, "side": "short"}
    assert _run(f"m.positionForCall({json.dumps(short_call)}, {json.dumps(by_coin)})") is None
    assert _run(f"m.positionForCall({json.dumps(CALL)}, {{}})") is None
    assert _run(f"m.positionLine({json.dumps({**LIVE, 'liq_px': None})})") is None


def test_the_page_paints_the_box_from_the_position_when_one_is_open():
    assert '<script src="/static/position_line.js' in HTML
    assert HTML.index('src="/static/position_line.js') < HTML.index('src="/static/oil_desk.js')
    assert "PositionLine.positionLine(position)" in JS
    # an older cached page without position_line.js still renders the call
    assert "window.PathielPositionLine\n  || { positionForCall: () => null, positionLine: () => null }" in JS
    assert "paintLine(live, true)" in JS
    assert "Your position: liquidation at ${lev}x" in JS
    # the call card's Leverage row follows the position too ("5x" while running 20x)
    assert "function renderCallLeverage" in JS and "your open position" in JS
    # wallets and call both repaint the box, whichever lands first
    assert JS.count("renderLine();") >= 2
