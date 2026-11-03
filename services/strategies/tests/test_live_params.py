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

from services.strategies.backtest.desk_tick_sim import Params, live_params
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
    only risk_cap_frac is added. A change here means live_params() itself
    changed, not a drift in this wrapper."""
    p = LP.live_brent_params()
    base = live_params()
    assert dataclasses.replace(p, risk_cap_frac=base.risk_cap_frac) == base
    assert (p.stop_buffer, p.target_offset_frac, p.target_mode, p.min_r, p.leverage,
            p.entry_mode, p.entry_lookback_h, p.tick_minutes) == (
        0.75, 0.005, "limit", 2.0, 20.0, "market", 24, 15)


def test_live_brent_params_overrides_apply_last():
    p = LP.live_brent_params(starting_equity=225.0, size_slippage=True)
    assert (p.starting_equity, p.size_slippage, p.risk_cap_frac) == (225.0, True, 0.15)


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
    stop_buffer_floor_frac=0.008)`), plus risk_cap_frac. Proves the fix
    changed exactly one thing."""
    old = dataclasses.replace(scouting_params(20.0, 0.6), stop_buffer_atr=1.3, stop_buffer_floor_frac=0.008)
    new = LP.live_silver_params(20.0, 0.6)
    assert dataclasses.replace(new, risk_cap_frac=old.risk_cap_frac) == old
    assert old.risk_cap_frac is None and new.risk_cap_frac == pytest.approx(0.15)


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
