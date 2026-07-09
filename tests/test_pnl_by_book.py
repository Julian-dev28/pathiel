"""Attribution tests for scripts/pnl_by_book.py (audit 2026-07-09).

Pins the misattribution bug: BOOK_PRIORITY omitted neg_funding_fade and the vol
books, so 100% of their realized PnL was rebadged "main-engine" — and the
b7881e9 main-engine sizing decision was made on that polluted number (the true
main-engine over the window was +$2.51, not -$57.72).
"""
import importlib.util
import os
import time

import pytest

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "pnl_by_book.py")


@pytest.fixture(scope="module")
def pbb():
    spec = importlib.util.spec_from_file_location("pnl_by_book_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_book_is_in_priority(pbb):
    """Regression: an omitted book silently becomes 'main-engine'."""
    for book in ("neg_funding_fade", "vol_breakout_long", "vol_breakout_wide",
                 "rally_exhaustion", "engulf_short", "crash_continue_div_short",
                 "premium_fade_short", "hail_mary_short", "extreme_fade",
                 "xs_momentum", "external_alpha"):
        assert book in pbb.BOOK_PRIORITY, f"{book} missing from BOOK_PRIORITY"


def test_open_line_regex_parses_loop_log(pbb):
    line = ("2026-06-27 08:07:01,658 INFO:hermes_trader.agents.rally_exhaustion_live:"
            "[rally-exhaustion] LIVE opened short XPL (rally +12.8%, dvol $22.3M)")
    m = pbb.OPEN_LINE_RE.match(line)
    assert m is not None
    stamp, ms, mod, side, coin = m.groups()
    assert pbb._log_module_to_book(mod) == "rally_exhaustion"
    assert side == "short" and coin == "XPL" and ms == "658"


def test_open_line_regex_vol_book_short_flip(pbb):
    line = ("2026-07-01 12:00:00,000 INFO:hermes_trader.agents.vol_breakout_long_live:"
            "[vol-breakout-long] LIVE opened short MANTA (breakout 3.2x, confirm 1.8x)")
    m = pbb.OPEN_LINE_RE.match(line)
    assert m is not None
    assert pbb._log_module_to_book(m.group(3)) == "vol_breakout_long"
    assert m.group(4) == "short"


def _episode(pbb, coin="MANTA", side="short", open_ts=1_000_000):
    return pbb.Episode(coin, side, open_ts)


def test_exact_match_wins_over_legacy(pbb):
    ep = _episode(pbb)
    exact = {b: [] for b in pbb.BOOK_PRIORITY}
    legacy = {b: [] for b in pbb.BOOK_PRIORITY}
    exact["vol_breakout_long"].append(("MANTA", "short", 1_000_000 + 60_000))
    legacy["engulf_short"].append(("MANTA", "short", 1_000_000))
    book, source = pbb.attribute(ep, exact, legacy)
    assert (book, source) == ("vol_breakout_long", "exact")


def test_legacy_fallback_then_main_default(pbb):
    ep = _episode(pbb)
    empty = {b: [] for b in pbb.BOOK_PRIORITY}
    legacy = {b: [] for b in pbb.BOOK_PRIORITY}
    legacy["neg_funding_fade"].append(("MANTA", "short", 1_000_000))
    assert pbb.attribute(ep, empty, legacy) == ("neg_funding_fade", "legacy")
    assert pbb.attribute(ep, empty, {b: [] for b in pbb.BOOK_PRIORITY}) == ("main-engine", "default")


def test_side_and_window_still_enforced(pbb):
    ep = _episode(pbb)
    exact = {b: [] for b in pbb.BOOK_PRIORITY}
    exact["vol_breakout_long"].append(("MANTA", "long", 1_000_000))          # wrong side
    exact["engulf_short"].append(("MANTA", "short", 1_000_000 + 16 * 60_000))  # outside window
    assert pbb.attribute(ep, exact, {b: [] for b in pbb.BOOK_PRIORITY})[0] == "main-engine"


def test_extract_exact_footprints_reads_both_sources(pbb, tmp_path):
    open_ts_ms = 1_751_000_000_000
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"ts": %d, "event": "book_open", "book": "neg_funding_fade", "coin": "ALT", "side": "short"}\n'
        '{"ts": %d, "event": "neg_funding_fade", "opened": 1, "candidates": [{"coin": "NOISE"}]}\n'
        % (open_ts_ms, open_ts_ms))
    # loop-log line: local-time stamp -> epoch ms must round-trip through mktime
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(open_ts_ms / 1000))
    log = tmp_path / "trading_loop.log"
    log.write_text(f"{stamp},123 INFO:hermes_trader.agents.engulf_short_live:"
                   f"[engulf-short] LIVE opened short DOGE (body-ratio 1.50, dvol $25.0M)\n"
                   f"{stamp},124 INFO:hermes_trader.agents.engulf_short_live:"
                   f"[engulf-short] SHADOW btc_up=True signals=3\n")
    foot = pbb.extract_exact_footprints(0, loop_log=str(log), session_log=str(session))
    assert foot["neg_funding_fade"] == [("ALT", "short", open_ts_ms)]
    assert len(foot["engulf_short"]) == 1
    coin, side, ts = foot["engulf_short"][0]
    assert (coin, side) == ("DOGE", "short")
    assert abs(ts - open_ts_ms) < 1_000   # mktime round-trip within the same second
    # the exact extractor must NEVER read candidates lists (that was the over-attribution)
    assert all("NOISE" not in [c for (c, _, _) in v] for v in foot.values())


def test_build_episodes_flat_to_flat(pbb):
    fills = [
        {"coin": "ALT", "side": "B", "sz": "10", "closedPnl": "0", "fee": "0.1", "time": 1000, "tid": 1},
        {"coin": "ALT", "side": "A", "sz": "10", "closedPnl": "5.0", "fee": "0.1", "time": 2000, "tid": 2},
        {"coin": "ALT", "side": "A", "sz": "4", "closedPnl": "0", "fee": "0.05", "time": 3000, "tid": 3},
    ]
    eps = pbb.build_episodes(fills)
    assert len(eps) == 2
    closed, still_open = eps
    assert closed.side == "long" and closed.open_done is True
    assert closed.closed_pnl == pytest.approx(5.0) and closed.fee == pytest.approx(0.2)
    assert still_open.side == "short" and still_open.open_done is False
