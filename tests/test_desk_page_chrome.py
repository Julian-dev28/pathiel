"""The Desk and Thesis pages' chrome after the 2026-10-24 clean-up: no dead
controls, a structured call card, and a leverage line a trader can read.

Operator asks that day: "any leftover like this: Operator token / Offline?
delete them", "make this prettier" (the call rendered as a text dump with
"stale: intel ..." on top), "what does 20x OK mean?".
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "pathiel" / "static" / "oil_desk.js").read_text()


@pytest.fixture(scope="module")
def client():
    from pathiel.server import app
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/desk/oil"])
def test_no_dead_header_controls_on_either_page(client, path):
    body = client.get(path).text
    for leftover in ('id="status-pill"', ">Offline<", "Operator token", 'id="op-token-btn"',
                     'id="token-pop"', 'id="stop-trading-btn"'):
        assert leftover not in body, f"{path}: leftover {leftover}"


def test_the_call_card_is_structured_with_a_text_fallback(client):
    body = client.get("/").text
    for element in ('id="call-sentence"', 'id="call-side-badge"', 'id="call-decision-badge"',
                    'id="call-leverage"', 'id="call-pays"', 'id="call-risk"', 'id="call-catalyst"',
                    'id="call-out-if"', 'id="call-markdown" hidden'):
        assert element in body, element
    assert "function renderCallCard" in JS and "brief.quick_fields" in JS
    # the stale note never starts with the literal "stale:" prefix any more
    assert "`stale: ${" not in JS


def test_the_headlines_block_names_its_feeds_and_failures(client):
    """2026-10-25: five feeds were added to services/intel and two failing
    ones fixed, and the Desk page showed none of it — six headlines, no
    source summary, no failed-feed note. The block now carries a sources
    line: packet age, headline count, top feeds by count, failed feeds in
    red (from IntelPacket.sources_failed), or "all feeds answered"."""
    body = client.get("/").text
    assert 'id="intel-sources"' in body
    assert "function renderIntelSources" in JS and "intel.sources_failed" in JS
    assert "all feeds answered" in JS and "chip chip-red" in JS
    # the failed-source key shape the intel service emits renders readably
    assert "function failedSourceLabel" in JS


def test_the_leverage_line_explains_itself(client):
    body = client.get("/").text
    assert 'id="line-liq-label"' in body and "Stop uses of the way to liquidation" in body
    assert "Pays, per target" in body and "Stop / liq distance" not in body and "R to targets" not in body
    assert "Stop clears ${lev}x liquidation" in JS and "Stop too close for ${lev}x" in JS
    assert "(limit 50%)" in JS
