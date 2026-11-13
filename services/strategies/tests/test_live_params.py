"""Gate tests for services/strategies/evals/live_params.py: the one place
that builds the LIVE Brent/silver `Params` for every eval that claims to
run "the live rules".

Context: several harnesses built their own "live" baseline by hand
(`dataclasses.replace(base_params()/scouting_params(...), starting_equity=...)`)
and every one of them forgot `Params.risk_cap_frac` -- the 15% risk cap the
live oil rule has used since 2026-11-02 (`LOSS_AUDIT_2026-11-02.md`). Their
"live" row was full-port size, not the real live rule, even where the
docstring said otherwise (`EXTENDED_HISTORY_TEST_2026-11-03.md`'s
Caveats). `live_brent_params`/`live_silver_params` are now the shared
source of truth; this file pins their values AND makes sure every harness
known to have had the gap still goes through them, so a future edit can't
quietly reintroduce the hand-built pattern and drop the cap again.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from services.strategies import next_trade as NT
from services.strategies.backtest.desk_tick_sim import Params, live_params
from services.strategies.backtest.desk_tick_sim import _fit_leverage_setup
from services.strategies.evals import live_params as LP
from services.strategies.evals.market_scout import scouting_params

EVALS_DIR = pathlib.Path(__file__).resolve().parents[1] / "evals"


def _code_without_module_docstring(path: pathlib.Path) -> str:
    """`path`'s source with its leading module docstring (and an optional
    shebang line before it) stripped, so a regression check below doesn't
    false-positive on the banned pattern appearing in PROSE (the
    correction notes each fixed file carries) rather than in code."""
    lines = path.read_text().splitlines(keepends=True)
    i = 1 if lines and lines[0].startswith("#!") else 0
    rest = "".join(lines[i:])
    if not rest.startswith('"""'):
        return "".join(lines)
    end = rest.find('"""', 3)
    return rest if end == -1 else rest[end + 3:]

# Every harness confirmed (2026-11-03) to have built a "live" Brent/silver
# baseline by hand and dropped risk_cap_frac. Each MUST import
# live_params.py going forward; none may rebuild its BRENT baseline with
# the exact pattern that caused the bug
# (`dataclasses.replace(base_params(), ...)` / a bare `live_params()` with
# no cap override).
HARNESSES_WITH_LIVE_BASELINE = (
    "brent_stop_buffer_test.py",
    "closer_target_test.py",
    "stop_cap_test.py",
    "silver_stop_floor_test.py",
    "profit_lock_test.py",
    "scalp_test.py",
)

# The exact hand-built Brent pattern that silently dropped risk_cap_frac
# before 2026-11-03. None of HARNESSES_WITH_LIVE_BASELINE may reintroduce
# it for Brent (silver is checked separately below -- two of them
# legitimately keep `scouting_params(...)` for a non-live stop-rule shape,
# with risk_cap_frac added back explicitly; see each file's own comment).
BANNED_BRENT_PATTERN = "dataclasses.replace(base_params()"


# ── the shared functions themselves ─────────────────────────────────────────

def test_live_brent_params_sets_the_15pct_risk_cap():
    p = LP.live_brent_params()
    assert p.risk_cap_frac == pytest.approx(0.15) == pytest.approx(LP.LIVE_RISK_CAP_FRAC)


def test_live_brent_params_matches_live_params_on_every_other_live_field():
    """Same as `desk_tick_sim.live_params()` (the decision rule itself:
    stop buffer, target offset, min R, entry mode/lookback, leverage) --
    only risk_cap_frac and entry_fit_leverage are added. A change here means
    live_params() itself changed, not a drift in this wrapper."""
    p = LP.live_brent_params()
    base = live_params()
    assert dataclasses.replace(p, risk_cap_frac=base.risk_cap_frac,
                               entry_fit_leverage=base.entry_fit_leverage) == base
    assert base.entry_fit_leverage is None      # the sim's default stays the pre-11-04 rule
    assert (p.stop_buffer, p.target_offset_frac, p.target_mode, p.min_r, p.leverage,
            p.entry_mode, p.entry_lookback_h, p.tick_minutes) == (
        0.75, 0.005, "limit", 2.0, 20.0, "market", 24, 15)


def test_live_brent_params_overrides_apply_last():
    p = LP.live_brent_params(starting_equity=225.0, size_slippage=True)
    assert (p.starting_equity, p.size_slippage, p.risk_cap_frac) == (225.0, True, 0.15)
    assert LP.live_brent_params(entry_fit_leverage=None).entry_fit_leverage is None   # the unfitted baseline, on request


# ── the leverage fit: the eval baseline reads it off the live tick (2026-11-13) ──
#
# RULE_FUSION_2026-11-13.md section 5: the desk has fitted Brent's leverage
# from 20/15 since 11-04 (next_trade.LEVERAGE_FIT, applied by
# services/strategies/api.py and the MCP server's handle_place_bracket via
# next_trade.leverage_fit_for), but live_brent_params never set
# Params.entry_fit_leverage, so every "live" Brent eval sized wide-stop
# entries differently from the desk. The expected value below is DERIVED
# from next_trade, never typed: if the live fit changes, this still passes
# only when live_params.py follows it.

@pytest.mark.parametrize("coin, build", [
    (NT.BRENT, lambda: LP.live_brent_params()),
    (NT.SILVER, lambda: LP.live_silver_params(25.0, 0.5)),
    (NT.GOLD, lambda: LP.live_gold_params(25.0, 12.0)),
])
def test_live_params_carry_exactly_the_live_leverage_fit(coin, build):
    live_fit = NT.leverage_fit_for(coin)
    expected = tuple(float(c) for c in live_fit) if live_fit else None
    assert build().entry_fit_leverage == expected
    assert LP.live_leverage_fit(coin) == expected


def test_live_brent_fit_is_on_and_is_the_live_tick_s_candidates():
    """Brent IS a fitted coin live (LEVERAGE_FIT_COINS), so the eval
    baseline must not be the fixed-leverage sim default."""
    assert NT.BRENT in NT.LEVERAGE_FIT_COINS
    p = LP.live_brent_params()
    assert p.entry_fit_leverage is not None
    assert set(p.entry_fit_leverage) == set(NT.LEVERAGE_FIT)
    assert max(p.entry_fit_leverage) == pytest.approx(p.leverage)      # the top candidate is the configured 20x


def test_a_coin_without_a_live_fit_gets_none():
    assert NT.leverage_fit_for("xyz:CL") == ()
    assert LP.live_leverage_fit("xyz:CL") is None


def test_sim_fit_agrees_with_the_live_fit_on_a_wide_and_a_tight_stop():
    """The sim's `_fit_leverage_setup` and the live `next_trade.fit_leverage`
    pick the same leverage from live_brent_params' candidates: the tight
    stop stays at 20x, the wide stop drops to 15x where the fixed-20x
    baseline refused ("leverage"). This is the trade the old eval baseline
    never took and the live desk did."""
    p = LP.live_brent_params()
    coin_max = p.max_leverage
    # 20x liq sits 2.56% away (mmr 2.5%), so a stop over 1.28% of price fails 20x;
    # 15x liq sits 4.27% away, so a stop under 2.14% still fits 15x.
    tight = {"entry_low": 94.75, "entry_high": 100.0, "atr14": 0.6, "target_low": 94.75, "target_high": 100.0}   # stop 94.0, 1.05%
    wide = {"entry_low": 94.95, "entry_high": 103.0, "atr14": 0.6, "target_low": 94.95, "target_high": 103.0}    # stop 94.2, 1.88%
    from services.strategies.backtest.desk_tick_sim import Direction
    d = Direction(side="long")
    for lv, mid in ((tight, 95.0), (wide, 96.0)):
        stop = lv["entry_low"] - p.stop_buffer
        live_lev, live_gate = NT.fit_leverage(mid, stop, p.leverage, coin_max, p.entry_fit_leverage)
        setup, sim_lev = _fit_leverage_setup(d, mid, lv, p)
        assert sim_lev == pytest.approx(live_lev), (lv, setup)
        assert live_gate.get("ok") and setup is not None and not setup.get("refused"), (lv, live_gate, setup)
    # and the wide one really is the 15x case
    stop = wide["entry_low"] - p.stop_buffer
    assert NT.fit_leverage(96.0, stop, p.leverage, coin_max, p.entry_fit_leverage)[0] == pytest.approx(15.0)
    assert not NT.check_leverage(96.0, stop, 20.0, coin_max).get("ok")


def test_live_silver_params_sets_the_cap_and_the_real_live_stop_rule():
    p = LP.live_silver_params(venue_max_leverage=25.0, atr_price_mean=0.5)
    assert p.risk_cap_frac == pytest.approx(0.15)
    # the real live rule is the per-TICK ATR buffer (stop_buffer_atr), not
    # scouting_params' own default fixed, mean-ATR buffer -- SILVER_STOP_
    # FLOOR_TEST_2026-11-02.md's floor rides along with it.
    assert p.stop_buffer_atr == pytest.approx(1.3)
    assert p.stop_buffer_floor_frac == pytest.approx(0.008)
    assert p.leverage == pytest.approx(20.0)        # min(live leverage, venue max)
    assert p.max_leverage == pytest.approx(25.0)


def test_live_silver_params_caps_leverage_at_the_venue_max():
    p = LP.live_silver_params(venue_max_leverage=10.0, atr_price_mean=0.5)
    assert p.leverage == pytest.approx(10.0)
    assert p.max_leverage == pytest.approx(10.0)


def test_live_silver_params_matches_scouting_params_plus_the_cap_and_floor():
    """Byte-for-byte the pre-fix hand-built pattern
    (`dataclasses.replace(scouting_params(...), stop_buffer_atr=1.3,
    stop_buffer_floor_frac=0.008)`), plus risk_cap_frac (2026-11-03) and
    the live leverage fit (2026-11-13). Proves the fixes changed exactly
    those two things."""
    old = dataclasses.replace(scouting_params(20.0, 0.6), stop_buffer_atr=1.3, stop_buffer_floor_frac=0.008)
    new = LP.live_silver_params(20.0, 0.6)
    assert dataclasses.replace(new, risk_cap_frac=old.risk_cap_frac, entry_fit_leverage=old.entry_fit_leverage) == old
    assert old.risk_cap_frac is None and new.risk_cap_frac == pytest.approx(0.15)
    assert old.entry_fit_leverage is None and new.entry_fit_leverage == LP.live_leverage_fit(NT.SILVER)


def test_live_silver_params_overrides_apply_last():
    p = LP.live_silver_params(20.0, 0.5, starting_equity=225.0)
    assert p.starting_equity == pytest.approx(225.0)
    assert p.risk_cap_frac == pytest.approx(0.15)   # override doesn't clobber the cap


def test_params_default_risk_cap_frac_is_still_none():
    """The dataclass default stays untouched (None = the old, pre-2026-11-02
    full-port behaviour), so every golden test of the sim's old output
    stays byte-identical. Only live_params.py and its callers know about
    the cap."""
    assert Params().risk_cap_frac is None
    assert live_params().risk_cap_frac is None


# ── regression: no harness may rebuild the live baseline by hand ──────────

@pytest.mark.parametrize("filename", HARNESSES_WITH_LIVE_BASELINE)
def test_harness_imports_the_shared_live_params_module(filename):
    """Each harness known to have built its 'live' Brent/silver baseline by
    hand, and dropped risk_cap_frac doing it, must import live_params.py
    (directly, or -- scalp_test.py -- aliased as `desk_live_params`), and
    must not reintroduce the exact hand-built Brent pattern that caused
    the bug. A harness that silently reverts to
    `dataclasses.replace(base_params(), ...)` -- forgetting the cap again
    -- fails this test instead of shipping a quietly-wrong 'live' row."""
    src = (EVALS_DIR / filename).read_text()
    assert "services.strategies.evals.live_params" in src, (
        f"{filename} no longer imports live_params.py -- it can silently drop risk_cap_frac again")
    code = _code_without_module_docstring(EVALS_DIR / filename)
    assert BANNED_BRENT_PATTERN not in code, (
        f"{filename} rebuilds its Brent live baseline by hand again ({BANNED_BRENT_PATTERN!r})")


def test_brent_stop_buffer_and_closer_target_use_live_silver_params_directly():
    """The two files confirmed by EXTENDED_HISTORY_TEST_2026-11-03.md
    (brent_stop_buffer_test.py, closer_target_test.py) build their ENTIRE
    silver baseline through live_silver_params -- not a hand-rolled
    scouting_params() call -- since neither needs a different stop-rule
    shape than the real live rule."""
    for filename in ("brent_stop_buffer_test.py", "closer_target_test.py"):
        code = _code_without_module_docstring(EVALS_DIR / filename)
        assert "live_silver_params(" in code, f"{filename} no longer calls live_silver_params"
        assert "dataclasses.replace(scouting_params(" not in code, (
            f"{filename} rebuilds its silver baseline by hand instead of calling live_silver_params")


def test_scouting_params_harnesses_apply_the_cap_constant_to_every_hand_built_silver_params():
    """stop_cap_test.py, silver_stop_floor_test.py and profit_lock_test.py
    deliberately keep a hand-built `scouting_params(...)` call for silver
    (each is testing a DIFFERENT stop-rule shape against the real live
    one), so they can't route through live_silver_params without changing
    what they test. Each must still apply the shared LIVE_RISK_CAP_FRAC
    constant to its hand-built silver Params."""
    for filename in ("stop_cap_test.py", "silver_stop_floor_test.py", "profit_lock_test.py"):
        code = _code_without_module_docstring(EVALS_DIR / filename)
        assert "LIVE_RISK_CAP_FRAC" in code, f"{filename} no longer imports LIVE_RISK_CAP_FRAC"
        assert "risk_cap_frac=LIVE_RISK_CAP_FRAC" in code, (
            f"{filename} no longer applies LIVE_RISK_CAP_FRAC to its hand-built silver Params")


# Files that legitimately call `dataclasses.replace(base_params(), ...)` /
# `dataclasses.replace(scouting_params(...), ...)` WITHOUT mentioning
# risk_cap_frac, and are not a live-baseline bug:
#   strategy_sweep.py    `params_with` is a generic **overrides grid helper
#                         for the sweep's OWN variants, not a fixed "live"
#                         baseline -- its callers apply whatever risk_cap_frac
#                         (if any) they need.
#   loss_fix_test.py      THE study that introduced risk_cap_frac
#                         (LOSS_AUDIT_2026-11-02.md); its "live" baseline
#                         is deliberately the PRE-cap, full-port rule --
#                         risk_cap_frac is one of its own candidate sizes,
#                         swept against that baseline, not missing from it.
KNOWN_INTENTIONAL_HAND_BUILDERS = frozenset({"strategy_sweep.py", "loss_fix_test.py"})


def test_affected_harnesses_list_is_exhaustive_over_known_live_baseline_builders():
    """Every OTHER eval harness that still builds a Params via
    `dataclasses.replace(base_params(), starting_equity=...)` or
    `dataclasses.replace(scouting_params(...), starting_equity=...)` --
    the exact pattern that dropped risk_cap_frac -- must already be one of
    HARNESSES_WITH_LIVE_BASELINE, must apply `risk_cap_frac` itself (e.g.
    oil_regime_test.py / pullback_correlation_test.py / desk_split_test.py,
    which set it explicitly as their own study variable), or be a KNOWN,
    documented exception above. A genuinely new harness that reintroduces
    the hand-built pattern without setting risk_cap_frac anywhere in the
    same file fails here instead of shipping a silently wrong 'live' row."""
    checked = set(HARNESSES_WITH_LIVE_BASELINE) | {"live_params.py"} | KNOWN_INTENTIONAL_HAND_BUILDERS
    for path in sorted(EVALS_DIR.glob("*.py")):
        if path.name in checked or path.name == "__init__.py":
            continue
        code = _code_without_module_docstring(path)
        hand_built = ("dataclasses.replace(base_params()" in code
                      or "dataclasses.replace(scouting_params(" in code)
        if not hand_built:
            continue
        assert "risk_cap_frac" in code, (
            f"{path.name} builds a Params baseline by hand "
            f"(base_params()/scouting_params()) and never mentions risk_cap_frac -- "
            f"it is probably missing the live 15% risk cap; add it to "
            f"HARNESSES_WITH_LIVE_BASELINE and route it through live_params.py, add it to "
            f"KNOWN_INTENTIONAL_HAND_BUILDERS with a reason, or set risk_cap_frac explicitly "
            f"if the hand-built baseline is otherwise intentional")


# ── gold (2026-11-05) ────────────────────────────────────────────────────────

def test_live_gold_params_is_live_silver_params_plus_golds_floor_and_fit():
    """The gold rule is silver's shape with gold's own floor (0.8%, the
    GOLD_STRATEGY_TEST_2026-11-05.md plateau) and the 20/15 leverage fit the
    live tick applies on gold (since 2026-11-13 silver carries its own fit
    too, read off next_trade the same way); the cap rides along."""
    gold = LP.live_gold_params(25.0, 12.0)
    silver = LP.live_silver_params(25.0, 12.0)
    assert dataclasses.replace(gold, entry_fit_leverage=silver.entry_fit_leverage,
                               stop_buffer_floor_frac=silver.stop_buffer_floor_frac) == silver
    assert gold.entry_fit_leverage == (20.0, 15.0) == LP.live_leverage_fit(NT.GOLD)
    assert gold.stop_buffer_floor_frac == pytest.approx(0.008) and gold.risk_cap_frac == pytest.approx(0.15)
    assert gold.max_leverage == pytest.approx(25.0) and gold.leverage == pytest.approx(20.0)


def test_live_gold_params_overrides_apply_last():
    p = LP.live_gold_params(25.0, 12.0, starting_equity=225.0, tick_minutes=60)
    assert (p.starting_equity, p.tick_minutes, p.risk_cap_frac, p.entry_fit_leverage) == (225.0, 60, 0.15, (20.0, 15.0))
