"""The Desk page's Wallets card (pathiel/static/oil_desk.js, renderWalletAccount).

Pins the 2026-10-24 operator asks, the way tests/test_page_polling.py pins
the polling shape: the exact source strings the behaviour hangs on. The
data side (margin_usd, margin_used_usd, errors, stale_s) is tested in
tests/test_watch_wallet.py and services/accounts/tests/test_positions.py.
"""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "pathiel" / "static" / "oil_desk.js").read_text()
CARD = JS[JS.index("function renderWalletAccount"):JS.index("async function loadWallets")]


def test_margin_is_shown_per_account_and_per_position():
    assert "Margin in use" in CARD and "margin_used_usd" in CARD
    assert "<th class=\"num\">Margin</th>" in CARD and "p.margin_usd" in CARD
    # Free margin is what is left to deploy, never negative
    assert "Math.max(0, equity - marginUsed)" in CARD


def test_a_flat_watch_only_wallet_renders_no_card():
    assert re.search(r"account\.mode === 'watch' && !positions\.length[^\n]*return ''", CARD)


def test_a_failed_or_stale_venue_read_never_reads_as_flat():
    assert "Venue read failed" in CARD and "snapshot.errors" in CARD
    assert "Last good read" in CARD and "snapshot.stale_s" in CARD
    # the empty-state line is only used when no dex failed
    assert "const body = failed ? failed :" in CARD


def test_headline_rows_never_print_the_word_unknown():
    intel = JS[JS.index("function renderIntel"):JS.index("async function loadLog")]
    assert "h.stance !== 'unknown'" in intel
    assert "'unknown'" not in intel.replace("h.stance !== 'unknown'", "")
    assert "fmtAge(h.published_at)" in intel and "function fmtAge" in JS
