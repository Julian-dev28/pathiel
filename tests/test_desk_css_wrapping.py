"""The Desk page renders the call and the loop log inside <pre> blocks.
2026-10-23: the call's lines ran past the card edge (screenshot from the
operator). Both blocks must wrap inside the card."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "pathiel" / "static" / "desks.css").read_text()


def _block(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{(.*?)\}", CSS, re.S)
    assert m, f"{selector} rule missing"
    return m.group(1)


def test_call_and_log_pre_blocks_wrap_inside_the_card():
    for sel in (".call-sentence", ".log-body"):
        body = _block(sel)
        flat = body.replace(" ", "")
        assert "white-space:pre-wrap" in flat, sel
        assert "overflow-wrap:anywhere" in flat or "word-break:break-word" in flat, sel
