"""The Desk page shows unfilled bracket entries (2026-10-25: a resting NVDA
bracket held the account's one slot for hours while the Wallets card read
"No open positions"). The positions payload carries `resting_entries` and
`stranded_orders` per dex; the page has to render both.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_wallets_card_renders_resting_entries_and_stranded_orders():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "a.resting_entries" in js
    assert "a.stranded_orders" in js
    assert "function renderRestingEntries" in js
    # the flat-account copy says a resting entry holds the slot, instead of a bare "No open positions"
    assert "the account's one slot" in js
    # a watch wallet with only a resting entry still earns a card
    assert "!positions.length && !resting.length" in js


def test_an_entry_without_a_stop_is_flagged():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "NO STOP" in js
