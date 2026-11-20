"""Pure helpers added to pathiel/static/oil_desk.js for the 2026-11-20 Desk
page layout bug report, actually executed through node (not just pinned as
text) -- same convention test_desk_switcher_ui.py already uses for
lastDeskTickLine: pathiel/static/oil_desk.js is DOM-coupled top to bottom and
this repo carries no jsdom, but a function with no `document.`/`window.`
reference is pure and node can run it directly once extracted from the file.

Covers:
  - leverageChoicesFor: the Next-trade leverage picker's rungs, cut down to
    a desk's own cap (hype 10x -> 5/10; sp500 50x -> 5/10/20/50; oil 20x ->
    5/10/20) instead of the old fixed [5, 10, 20] that showed a 20x button,
    selected, on every desk.
  - extraBlockers: drops a next_trade() blocker whose exact text is already
    said in full by `verdict` (the trend-gate / entry-delay duplicate wait
    reason on HYPE).
  - distanceTile: "1.865 (2.1%, 2.3 ATR)" / "at the mid" for the Levels
    card's distance-to-live-entry cell (pulled in alongside desks.js's real
    fmtPrice/pctUnsigned -- no duplicated formatting logic to drift).
  - shouldReuseInFlightLoad: the Wallets-card refresh race's de-dupe guard.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
DESKS_JS = (ROOT / "pathiel" / "static" / "desks.js").read_text()

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract(start_marker: str, end_marker: str) -> str:
    start = JS.index(start_marker)
    end = JS.index(end_marker, start)
    src = JS[start:end]
    assert "document." not in src and "window." not in src, \
        f"{start_marker} must stay pure (no DOM) to run through node"
    return src


def _run_js(src: str, expr: str):
    script = f"{src}\nprocess.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(out.stdout)


# ── leverageChoicesFor ───────────────────────────────────────────────────────

LEVERAGE_CHOICES_SRC = _extract("const LEVERAGE_LADDER", "function currentDeskCap()")


def _choices(cap):
    return _run_js(LEVERAGE_CHOICES_SRC, f"leverageChoicesFor({json.dumps(cap)})")


def test_ladder_is_5_10_20_50():
    assert "const LEVERAGE_LADDER = [5, 10, 20, 50];" in JS


def test_hypes_10x_cap_offers_only_5_and_10():
    assert _choices(10) == [5, 10]


def test_sp500s_50x_cap_offers_the_whole_ladder():
    assert _choices(50) == [5, 10, 20, 50]


def test_oils_20x_cap_offers_5_10_20_not_50():
    assert _choices(20) == [5, 10, 20]


def test_a_cap_under_the_lowest_rung_still_gets_one_button():
    assert _choices(3) == [3]


# ── currentDeskCap / resetLeverageForActiveDesk: source-pinned wiring ───────
# (these read activeDesk/activeDeskMeta/currentLeverage, so unlike the above
# they are not run through node -- pinned the same way test_desk_switcher_ui.
# py pins loadActiveDesks's desk-resolution wiring.)

def test_current_desk_cap_reads_this_desks_own_meta_not_a_hardcoded_one():
    func = _extract("function currentDeskCap()", "\nfunction show(")
    assert "activeDeskMeta[activeDesk]" in func
    assert "meta.leverage_cap" in func
    assert "DEFAULT_LEVERAGE" in func   # the before-it-loads / older-server fallback


def test_render_lev_seg_builds_its_options_from_the_desks_own_cap():
    func = JS[JS.index("function renderLevSeg()"):JS.index("function loadLine()")]
    assert "leverageChoicesFor(currentDeskCap())" in func
    assert "LEVERAGE_CHOICES" not in JS   # the old fixed [5, 10, 20] constant is gone


def test_reset_leverage_sets_current_leverage_to_the_desks_cap_and_repaints():
    func = _extract("function resetLeverageForActiveDesk()", "\nfunction renderLevSeg()")
    assert "currentLeverage = currentDeskCap();" in func
    assert "renderLevSeg();" in func


def test_loading_active_desks_resets_leverage_after_resolving_the_desk():
    func = JS[JS.index("async function loadActiveDesks()"):JS.index("/** \"Oil")]
    resolution = func[func.index("const fromUrl = urlDesk();"):]
    assert resolution.index("resetLeverageForActiveDesk();") < resolution.index("renderDeskSwitcher();")


def test_switching_desks_resets_leverage_before_loading_the_new_desks_data():
    func = JS[JS.index("function renderDeskSwitcher()"):]
    handler = func[func.index("addEventListener('change'"):]
    assert handler.index("resetLeverageForActiveDesk();") < handler.index("loadAll();")


# ── extraBlockers ────────────────────────────────────────────────────────────

EXTRA_BLOCKERS_SRC = _extract("function extraBlockers(n)", "\n/** The next trade")


def _extra(n):
    return _run_js(EXTRA_BLOCKERS_SRC, f"extraBlockers({json.dumps(n)})")


def test_a_blocker_restated_verbatim_in_the_verdict_is_dropped():
    """The exact 2026-11-20 HYPE bug report: the trend-gate reason in both
    fields, verbatim."""
    reason = "120h return -5.80% (67.344 -> 63.436) is negative: no new long against a 120h downtrend"
    n = {"verdict": f"waits: {reason}", "blockers": [reason]}
    assert _extra(n) == []


def test_distinct_blockers_are_kept():
    n = {"verdict": "waits: buy at 110.01 or lower, but no 20x there (max 17x)",
        "blockers": ["pays 0.07R at 113.135, needs 2R", "stop too wide for 20x (max 17x)"]}
    assert _extra(n) == n["blockers"]


def test_no_blockers_and_no_verdict_never_throw():
    assert _extra({}) == []
    assert _extra({"verdict": "buys now", "blockers": []}) == []
    assert _extra({"blockers": ["only a blocker, no verdict"]}) == ["only a blocker, no verdict"]


def test_paint_next_trade_renders_the_deduped_list_not_the_raw_one():
    func = JS[JS.index("function paintNextTrade(n)"):JS.index("function paintLine(")]
    assert "const extra = extraBlockers(n);" in func
    assert "extra.length ? `Now: ${extra.join('; ')}.`" in func
    assert "n.blockers.join" not in func


# ── distanceTile ─────────────────────────────────────────────────────────────
# Pulled in alongside desks.js's real fmtPrice/pctUnsigned (not a duplicated
# copy): this is the exact formatter the rest of the page already uses.

FMT_HELPERS_SRC = DESKS_JS[DESKS_JS.index("const pctSigned ="):DESKS_JS.index("const fmtAgeMinutes =")]
DISTANCE_TILE_SRC = _extract("function distanceTile(d, ref)", "\nfunction renderLevels()")


def _distance_tile(d, ref=None):
    return _run_js(f"{FMT_HELPERS_SRC}\n{DISTANCE_TILE_SRC}", f"distanceTile({json.dumps(d)}, {json.dumps(ref)})")


def test_the_worked_example_from_the_bug_report():
    assert _distance_tile({"price": 1.865, "pct": 0.021, "atr": 2.3}) == "1.865 (2.1%, 2.3 ATR)"


def test_zero_distance_reads_at_the_mid_not_a_string_of_zeros():
    assert _distance_tile({"price": 0, "pct": 0.0, "atr": 0.0}) == "at the mid"


def test_missing_atr_omits_the_atr_clause():
    assert _distance_tile({"price": 1.865, "pct": 0.021, "atr": None}) == "1.865 (2.1%)"


def test_no_distance_data_is_an_em_dash():
    assert _distance_tile(None) == "—"
    assert _distance_tile({"price": None, "pct": None, "atr": None}) == "—"


def test_render_levels_uses_distance_tile_for_the_distance_cell():
    func = JS[JS.index("function renderLevels()"):JS.index("// ── 4. HEADLINES")]
    assert "distanceTile(rows.distance, l.mid)" in func


# 2026-11-20 follow-up: a 0.4475 gap on a $62 coin read "0.44750" next to
# prices printed to 3 decimals. A difference takes the coin's precision.

def test_a_sub_one_distance_on_a_62_dollar_coin_prints_three_decimals():
    assert _distance_tile({"price": 0.4475, "pct": 0.007, "atr": 0.4}, 62.352) == "0.448 (0.7%, 0.4 ATR)"


def test_a_distance_on_a_sub_dollar_coin_keeps_five_decimals():
    assert _distance_tile({"price": 0.01234, "pct": 0.02, "atr": 1.0}, 0.61) == "0.01234 (2.0%, 1.0 ATR)"


def test_a_distance_on_an_index_prints_two_decimals():
    assert _distance_tile({"price": 12.3456, "pct": 0.002, "atr": 0.5}, 6800.0) == "12.35 (0.2%, 0.5 ATR)"


def test_render_levels_formats_the_atr_at_the_mids_precision():
    func = JS[JS.index("function renderLevels()"):JS.index("// ── 4. HEADLINES")]
    assert "fmtDelta(l.atr14_1h, l.mid)" in func


# ── midPositionTile: the mid can trade outside the 24h range ────────────────
# (2026-11-20 follow-up bug report: HYPE's mid traded under the 24h low and
# the cell read a bare "-14.8%" instead of saying so in words.)

MID_POSITION_SRC = _extract("function midPositionTile(l)", "\nfunction renderLevels()")


def _mid_position(l):
    return _run_js(f"{FMT_HELPERS_SRC}\n{MID_POSITION_SRC}", f"midPositionTile({json.dumps(l)})")


def _mid_position_outside(l):
    src = _extract("function midPositionIsOutsideRange(l)", "\nfunction renderLevels()")
    return _run_js(src, f"midPositionIsOutsideRange({json.dumps(l)})")


def test_inside_the_range_is_still_a_plain_percentage():
    assert _mid_position({"mid_pos_in_24h": 0.62}) == "62.0%"
    assert _mid_position_outside({"mid_pos_in_24h": 0.62}) is False


def test_below_the_low_reads_in_words_not_a_negative_percent():
    l = {"mid_pos_in_24h": -0.019, "mid": 62.6, "range_24h": {"low": 63.79, "high": 70.0}}
    tile = _mid_position(l)
    assert tile.startswith("below the 24h low by ")
    assert "-" not in tile.split("by ", 1)[1].split(" (")[0]   # the distance itself is unsigned
    assert _mid_position_outside(l) is True


def test_above_the_high_reads_in_words():
    l = {"mid_pos_in_24h": 1.05, "mid": 71.0, "range_24h": {"low": 60.0, "high": 68.0}}
    assert _mid_position(l) == f"above the 24h high by {71.0 - 68.0:.3f} ({(71.0 - 68.0) / 71.0 * 100:.1f}%)"
    assert _mid_position_outside(l) is True


def test_exactly_at_an_edge_is_still_the_plain_percentage():
    assert _mid_position({"mid_pos_in_24h": 0.0}) == "0.0%"
    assert _mid_position({"mid_pos_in_24h": 1.0}) == "100.0%"
    assert _mid_position_outside({"mid_pos_in_24h": 0.0}) is False
    assert _mid_position_outside({"mid_pos_in_24h": 1.0}) is False


def test_no_data_is_an_em_dash():
    assert _mid_position({"mid_pos_in_24h": None}) == "—"
    assert _mid_position({}) == "—"


def test_render_levels_wires_mid_position_tile_and_switches_its_class_outside_the_range():
    func = JS[JS.index("function renderLevels()"):JS.index("// ── 4. HEADLINES")]
    assert "midPositionTile(l)" in func
    assert "midPositionIsOutsideRange(l) ? ' kv-text' : ''" in func


# ── the Live entry row: full-width, badge before the sentence ──────────────

def test_live_entry_is_its_own_full_width_cell_with_the_badge_before_the_sentence():
    func = JS[JS.index("function renderLevels()"):JS.index("// ── 4. HEADLINES")]
    assert 'class="lev-stat-full"' in func
    assert "lev-live-line" in func
    assert func.index("${liveBadge}") < func.index("${liveTile}")


# ── shouldReuseInFlightLoad (the Wallets-card refresh race) ─────────────────

REUSE_SRC = _extract("function shouldReuseInFlightLoad(", "\nasync function loadAll()")


def _reuse(promise, for_desk, desk):
    return _run_js(REUSE_SRC, f"shouldReuseInFlightLoad({json.dumps(promise)}, {json.dumps(for_desk)}, "
                              f"{json.dumps(desk)})")


def test_nothing_in_flight_never_reuses():
    assert _reuse(None, None, "oil") is False


def test_the_same_desk_in_flight_is_reused():
    assert _reuse({}, "hype", "hype") is True


def test_a_desk_switch_is_never_held_up_by_the_old_desks_in_flight_load():
    assert _reuse({}, "oil", "hype") is False


def test_load_all_guards_with_shouldReuseInFlightLoad_before_starting_a_new_round():
    func = JS[JS.index("async function loadAll()"):JS.index("async function startOilDesk()")]
    assert "if (shouldReuseInFlightLoad(loadAllPromise, loadAllForDesk, activeDesk)) return loadAllPromise;" in func
    assert "loadAllForDesk = activeDesk;" in func
    assert ".finally(() => { loadAllPromise = null; loadAllForDesk = null; });" in func
