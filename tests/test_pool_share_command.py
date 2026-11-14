"""Gate tests for the /pathiel:share plugin command (commands/share.md).

Checks the flow the brief fixes, not the exact wording: the dry run comes
before the confirm call, the user is asked before anything is signed, a key
is never asked for or printed, no live-money tool is ever named, and the
handful of facts a user must be told (pr_url, master_command given
verbatim, the 15 minute signing window, the leave path, that coming back is
a brand new join and never a deleted leave file, the scoreboard URL, the
24 hour delay, that this is not a fee waiver, and that nothing is paid on
returns) are all there. See tests/test_pool_copy.py for the claims/style
lint on this same file.

Offline, no network, well under a second.
"""
from __future__ import annotations

import re
from pathlib import Path

import contracts.pool as pool

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "commands" / "share.md"

LIVE_MONEY_TOOLS = ("place_bracket", "bracket_sweep", "set_target", "close_position",
                    "set_leverage", "cancel_order")


def _text() -> str:
    return SHARE.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Whitespace collapsed to one space, and a blockquote continuation's
    leading '>' folded away too (step 0 is a blockquote), the same trick
    tests/test_plugin.py uses on commands/setup.md's own disclosure block."""
    text = re.sub(r"\s*\n\s*(>\s*)?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def test_the_file_exists():
    assert SHARE.is_file()


def test_starts_with_frontmatter_that_has_a_description():
    text = _text()
    assert text.startswith("---\ndescription:")
    head, _, rest = text.partition("\n---\n")
    assert head.startswith("---\ndescription:")
    assert rest.strip(), "no body after the frontmatter"


def test_names_all_four_pool_tools():
    """By their public names (contracts/mcp_public): pool_status and
    pool_report are get_pool_status (report=true), the two signers
    join_pool and leave_pool."""
    import contracts.mcp_public as public_toolset
    text = _text()
    for name in ("get_pool_status", "join_pool", "leave_pool"):
        assert f"`{name}" in text, name
    assert "report: true" in text
    assert public_toolset.RENAMES["join_pool"] == "pool_join"
    assert public_toolset.RENAMES["leave_pool"] == "pool_leave"
    assert len(pool.MCP_TOOLS) == 4


def test_never_names_a_live_money_tool():
    text = _text()
    hits = [tool for tool in LIVE_MONEY_TOOLS if tool in text]
    assert hits == [], hits


def test_the_dry_run_comes_before_the_confirm_call():
    text = _text()
    dry_run_at = text.index("Call `join_pool` with no arguments")
    confirm_at = text.index("confirm: true")
    assert dry_run_at < confirm_at


def test_says_to_show_the_message_exactly_and_ask_before_confirming():
    text = _text()
    assert "EXACTLY" in text
    assert "sign this statement, yes or no" in text.lower()
    confirm_at = text.index("confirm: true")
    ask_at = text.lower().index("sign this statement, yes or no")
    assert ask_at < confirm_at, "the confirm call must come after the user is asked"
    assert "explicit yes" in text.lower()


def test_never_asks_for_or_prints_a_key():
    flat = _flat(_text()).lower()
    assert "never print or ask for the value of a key" in flat
    assert "never ask for a key in the" in flat
    assert "never run that line yourself" in flat


def test_mentions_pr_url_and_master_command_given_verbatim():
    text = _text()
    assert "pr_url" in text
    assert "master_command" in text
    flat = _flat(text)
    assert "master_command` line from the tool's answer verbatim" in flat


def test_mentions_the_fifteen_minute_limit():
    flat = _flat(_text())
    assert "15 minutes have passed" in flat
    assert "(code expired)" in flat


def test_mentions_the_leave_path_and_coming_back_rule():
    text = _text()
    flat = _flat(text)
    assert "## Leaving" in text
    assert "## Coming back" in text
    assert "brand new join" in flat
    assert "never suggest deleting the leave file" in flat.lower()
    assert "pull request check refuses it" in flat.lower()


def test_mentions_the_scoreboard_url_and_the_24h_delay():
    flat = _flat(_text())
    assert pool.SCOREBOARD_URL in flat
    assert "at least 24 hours late" in flat


def test_states_it_is_not_a_fee_waiver_and_nothing_is_paid_on_returns():
    flat = _flat(_text())
    assert "not a fee waiver" in flat
    assert "No payout, refund or reward is ever tied to your returns" in flat


def test_mentions_pathiel_setup_for_no_account():
    text = _text()
    assert "no_account" in text
    assert "/pathiel:setup" in text


def test_the_checks_above_can_fail():
    """Each assertion above is proof only if a doctored copy trips it."""
    bad_order = "Only call confirm: true here. pool_join is shown only after."
    assert not (bad_order.index("pool_join") < bad_order.index("confirm: true"))

    with_live_money = _text() + "\nplace_bracket\n"
    assert any(tool in with_live_money for tool in LIVE_MONEY_TOOLS)

    no_fee_waiver = _text().replace("not a fee waiver", "a special deal", 1)
    assert "not a fee waiver" not in _flat(no_fee_waiver)
