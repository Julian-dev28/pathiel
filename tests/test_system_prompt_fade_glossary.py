"""System-prompt fade-signal glossary pin.

The fade family (rsiDivergence, mfiExtreme, bandSnapback, reversal candles)
surfaces via weight-0 bypasses; the LLM must at least know what the names
mean. The glossary line in "CONTEXT YOU RECEIVE" is pinned here so a prompt
rewrite can't silently drop it.

Deliberately NOT pinned: any change to the trend doctrine (rule 5/6, the
"Do NOT buy the dip" anti-pattern, the whale-flag-only counter-trend
exception). That is an owner policy decision priced by the §B.40/§B.41
shadow accruals, not a code default.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents.system_prompt import build_system_prompt


def _prompt():
    return build_system_prompt("LIVE", 0.55, 10)


def test_fade_glossary_names_all_four_triggers():
    p = _prompt()
    for name in ("rsiDivergence", "mfiExtreme", "bandSnapback",
                 "bullish/bearishReversalCandle"):
        assert name in p, f"glossary must name {name}"


def test_fade_glossary_defines_and_defers():
    """The line must define the class (exhaustion at an extreme) AND defer to
    the trend rules — it is a fact-definition, not a counter-trend sanction."""
    p = _prompt()
    assert "EXHAUSTING at a price extreme" in p
    assert "the trend rules below still govern what you may trade" in p


def test_trend_doctrine_unchanged():
    """Regression guard: the glossary must NOT have smuggled in a doctrine
    change — the dip-buying prohibition and the whale-only exception stand."""
    p = _prompt()
    assert "Do NOT 'buy the dip'" in p
    assert "no LONG when 4h AND 1d are both bearish" in p
    assert "ONE sanctioned" in p
