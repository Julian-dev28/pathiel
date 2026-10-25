"""The Desk pages show what the account actually did, not only how the calls
graded (2026-10-25: a stop fired and the Track record card still read
"No calls graded yet"). TrackRecord carries n_trades / realized_pnl_usd /
open_window; both desk renderers have to read them and the empty state has
to name fills, so the card is never silent while fills exist.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_oil_desk_card_shows_fills_and_realized_pnl():
    js = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "tr.n_trades" in js
    assert "tr.realized_pnl_usd" in js
    assert "tr.open_window" in js
    # the card is shown when either side has something, never hidden by n_graded alone
    assert "(!tr.n_graded && !tr.n_trades)" in js


def test_the_generic_desk_card_shows_trades():
    js = (ROOT / "pathiel" / "static" / "desks.js").read_text()
    assert "tr.n_trades" in js
    assert "tr.realized_pnl_usd" in js
    assert "tr.open_window" in js


def test_the_empty_state_names_fills_too():
    html = (ROOT / "pathiel" / "templates" / "oil_desk.html").read_text()
    assert "No calls graded and no fills yet." in html


def test_track_record_contract_carries_the_open_window():
    from contracts.strategies.models import OpenWindowTrades, TrackRecord
    assert "open_window" in TrackRecord.model_fields
    assert TrackRecord().open_window is None                 # old JSON without the field still loads
    assert set(OpenWindowTrades.model_fields) == {"thesis_id", "since", "computed_at", "n_fills", "realized_pnl_usd"}
