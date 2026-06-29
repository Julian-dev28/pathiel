"""The TA-sidestep upgrades AI-PASS -> LONG on a magnitude-based composite, so a violent
SELLOFF (big red candle + huge volume) fired the same triggers as a breakout and the bot
bought the falling knife (xyz:SMSN 2026-06-29, -9.3% ROE). _sidestep_bearish_block_reason
restores the symmetric 'require bullish direction' check the short gate already has."""
from hermes_trader.agents.executor import _sidestep_bearish_block_reason

CFG = {"runner_entry_gate": {"sidestep_require_bullish": True, "sidestep_bearish_move_pct": -3.0}}


def _a(**kw):
    base = {"coin": "xyz:SMSN", "uptrend_momentum_fired": False,
            "downtrend_momentum_fired": False, "daily_move_pct": None}
    base.update(kw)
    return base


def test_blocks_downtrend_momentum_long_the_smsn_case():
    r = _sidestep_bearish_block_reason(_a(downtrend_momentum_fired=True), CFG)
    assert r and "downtrend momentum fired" in r


def test_blocks_clearly_negative_24h_move():
    r = _sidestep_bearish_block_reason(_a(daily_move_pct=-7.5), CFG)
    assert r and "-7.5%" in r


def test_allows_uptrend_even_with_downtrend_noise():
    # explicit bullish momentum = a real upside setup, allow (uptrend wins)
    assert _sidestep_bearish_block_reason(
        _a(uptrend_momentum_fired=True, downtrend_momentum_fired=True, daily_move_pct=-9), CFG) == ""


def test_allows_neutral_breakout_no_downtrend():
    # a flat/up setup with no bearish signal sails through (the sidestep's real purpose).
    # (daily_move_pct=None is intentionally NOT tested here: the helper then does a live
    # universe lookup of the real 24h move, which is correct in production but non-
    # deterministic in a test — see test_live_move_fallback_blocks_selloff for that path.)
    assert _sidestep_bearish_block_reason(_a(coin="DEFINITELY_NOT_A_REAL_COIN", daily_move_pct=2.0), CFG) == ""


def test_small_negative_move_not_blocked():
    # -1% is noise, not a selloff — don't over-block (threshold is -3%)
    assert _sidestep_bearish_block_reason(_a(daily_move_pct=-1.0), CFG) == ""


def test_flag_off_disables_guard():
    cfg = {"runner_entry_gate": {"sidestep_require_bullish": False}}
    assert _sidestep_bearish_block_reason(_a(downtrend_momentum_fired=True), cfg) == ""
