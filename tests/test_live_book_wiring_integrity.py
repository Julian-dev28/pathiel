"""Wiring-integrity checks that run on every commit, not just at review time
(operator instruction 2026-07-20: check every integration for collisions and
leaks, before/during/after — and prefer integration tests over mocked units
for anything touching shared state).

Two kinds of check live here:
1. Static scans across hermes_trader/agents/*.py: no two live books may reuse
   a _BOOK_NAME or a state_file(...) path. A collision here is silent —
   nothing raises, two books just corrupt each other's persisted state or
   fight over the same ledger rows.
2. Real-ClaimsRegistry integration tests (not mocks) proving that two books
   which can plausibly target the SAME coin from the SAME underlying trigger
   (an inverse pair, or a shadow/live pair) cannot both hold it at once.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "hermes_trader" / "agents"


def _scan(pattern: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in sorted(_AGENTS_DIR.glob("*.py")):
        src = path.read_text()
        for m in re.finditer(pattern, src):
            hits.setdefault(m.group(1), []).append(path.name)
    return hits


def _all_book_name_sites() -> dict[str, list[str]]:
    """Union of both ways a module names its ledger book: a `_BOOK_NAME`
    constant (most live-wired modules) or an inline shadow_ledger.record(...)
    / record_many(...) string literal (mover_recorders.py uses this — it has
    no _BOOK_NAME constant at all, so scanning only that pattern silently
    missed 4 real book names until this test was widened 2026-07-20)."""
    sites = _scan(r'_BOOK_NAME\s*=\s*"([^"]+)"')
    for k, v in _scan(r'shadow_ledger\.record(?:_many)?\(\s*"([^"]+)"').items():
        sites.setdefault(k, []).extend(v)
    return sites


def test_no_two_live_modules_share_a_book_name():
    dupes = {k: v for k, v in _all_book_name_sites().items() if len(set(v)) > 1}
    assert dupes == {}, f"duplicate ledger book name across modules: {dupes}"


def test_no_two_modules_share_a_state_file_path():
    dupes = {k: v for k, v in _scan(r'state_file\(\s*"([^"]+)"').items() if len(v) > 1}
    assert dupes == {}, f"duplicate state_file(...) path across modules: {dupes}"


# --------------------------------------------------------------- stop reachability
# executor.py:1023 clamps the on-exchange backup stop to
#   entry * (backup_sl_max_frac_of_liq / leverage)
# so a book whose configured stop_pct exceeds 60/leverage percent NEVER
# executes the stop it advertises — the clamp silently substitutes a tighter
# one, and the book's DSL max_loss_pct becomes dead weight (it sits beyond
# both the real stop AND liquidation at 100/leverage percent). That is the
# rally_exhaustion lesson in a different costume: the live stop width is not
# the graded stop width, and a tight live stop can invert a validated edge.
#
# The reverse-refuted books were re-graded at their TRUE clamped width before
# going live (+10.59%/sig news_surge_short, +6.09%/sig mover_pass_short, both
# OOS halves positive) — this test pins that their config keeps telling the
# truth. The six older fixed-notional books all predate this check and are
# knowingly capped; see findings/reverse_refuted_direction_audit.md.
_BACKUP_SL_MAX_FRAC_OF_LIQ = 0.60


def _effective_stop_pct(stop_pct: float, leverage: float) -> float:
    return min(stop_pct, 100.0 * _BACKUP_SL_MAX_FRAC_OF_LIQ / leverage)


@pytest.mark.parametrize("path", [
    ("news_surge_short",),
    ("news_surge_multi",),
    ("social_trending",),
    ("unlock_short",),
])
def test_reverse_refuted_books_configure_a_reachable_stop(path):
    """The stop these books advertise must be the stop that actually fires."""
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    for key in path:
        cfg = cfg[key]
    stop, lev = float(cfg["stop_pct"]), float(cfg["leverage"])
    assert _effective_stop_pct(stop, lev) == pytest.approx(stop), (
        f"{'.'.join(path)}: configured stop {stop}% is clamped to "
        f"{_effective_stop_pct(stop, lev):.1f}% at {lev:g}x — the book would "
        f"trade a geometry it was never graded at"
    )
    # and the stop must sit strictly inside liquidation, or it is decoration
    assert stop < 100.0 / lev


# --------------------------------------------------------------- books-only mode
def test_main_engine_entries_stay_disabled():
    """main-engine entries are the measured #1 loss source, twice over:
    2026-07-18 forensics (2,721 fills) and again 2026-07-20 (-$172.33 of a
    -$187.24 30-day loss across 157 trades, EVERY slice negative — side,
    entry path, regime, asset class). Inverting it is NOT supported (n=8,
    and the as-called sample was positive), so the fix is to stop taking
    the entries, not to flip them.

    Sub-2h holds are -$163.66 of the bleed; a minimum-hold gate is the wrong
    fix because those exits are stop-outs — forcing the hold would remove
    the stop from entries that were already wrong.

    Strategy books tag `strategy_book` and pass untouched; AI close-checks
    never route through maybe_execute, so exits and held-position management
    are unaffected. Flipping this back on needs fresh evidence that the
    engine's entries are +EV, not a hunch."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    assert cfg["main_engine"]["entries_enabled"] is False


# --------------------------------------------------------------- margin floor
def test_margin_floor_preserves_a_real_liquidation_buffer():
    """2026-07-22 root-cause of the equity bleed: min_available_margin_pct was
    0.01, so the bot deployed the xyz dex to 97% utilization ($2.78 free of
    $90). With no buffer, every adverse tick FORCE-LIQUIDATED positions at ~5%
    — BEFORE the 6% backup stop — turning +EV mean-reversion shorts (graded
    +4.64%/73% win in up-regime) into forced losses and cascading as each
    liquidation drained the shared margin.

    The floor gives general margin headroom; the PRIMARY per-position
    liquidation defense is now the hip3 leverage cap (see the test below),
    which keeps xyz liquidation beyond the 6% stop by construction. So the
    floor can relax to a capital-efficient level once the cap is in place —
    but it must stay above the 1% that caused the 97%-utilization cascade."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    floor = float(cfg["min_available_margin_pct"])
    assert floor >= 0.08, f"margin floor {floor} too low — invites the utilization cascade"
    assert floor > 0.06


# --------------------------------------------------------------- hip3 leverage cap
def test_hip3_leverage_cap_keeps_the_stop_alive():
    """2026-07-22: xyz dex charges ~5% maintenance margin, so a 10x isolated
    position liquidates at ~5% — below the 6% backup stop, force-liquidating
    every +EV trade before it plays out. The hip3 cap must keep xyz liquidation
    beyond the 6% stop. At 6x: 1/6 - 0.05 = 11.7% buffer > 6%. Crypto is
    unaffected (~1-2% maintenance)."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    cap = int(cfg["hip3_max_leverage"])
    maint = 0.05
    stop = 0.06
    liq_buffer = 1.0 / cap - maint
    assert liq_buffer > stop, f"xyz {cap}x liq buffer {liq_buffer:.1%} <= 6% stop — stop dead"
    assert cap <= 8   # anything above ~9x puts liq below the stop on a 5%-maint dex


# --------------------------------------------- leverage x stop x liquidation (2026-07-22)
# The collision the operator flagged: leverage changes must not force any book's
# stop below its liquidation, or the position force-liquidates before its edge
# plays out. Rule: a stop of S fires before liq only if (1/lev - maint) > S.
# xyz maintenance ~= 5%. Each book's leverage must match its OWN stop width.
def _liq_buffer(lev, maint=0.05):
    return 1.0 / lev - maint


def _backup_sl_clamp(lev, max_frac=0.60):
    return max_frac / lev   # executor.py:1023 clamp


def test_short_books_at_6x_have_a_working_6pct_stop():
    """Short books: 6% stop, 1-day hold. At 6x the liq buffer (11.7%) sits well
    beyond the 6% stop AND beyond the clamped backup SL (10%)."""
    assert _liq_buffer(6) > 0.06                 # stop reachable
    assert _backup_sl_clamp(6) < _liq_buffer(6)  # clamp fires before liq


# ------------------------------------------- stop-honoring leverage cap (2026-07-24)
# The gap the liq-buffer tests above missed. `_liq_buffer` only asks "does the
# position survive to the stop". It never asks "is the stop still the width the
# book validated". The backup-SL clamp (max_frac_of_liq / leverage) silently
# SHRINKS the stop when leverage is too high, so a book can pass every
# liquidation test while trading an exit it never validated.
def _stop_after_clamp(lev, stop_pct, max_frac=0.60):
    """The stop width actually sent to the exchange. Mirrors the executor's
    relative tolerance so an exact fit (0.60/3 == 0.19999999999999998 in binary
    float) counts as delivering the full stop rather than a shrunk one."""
    want = stop_pct / 100.0
    cap = max_frac / lev
    return want if want <= cap * (1.0 + 1e-9) else cap


def test_backup_sl_clamp_silently_shrinks_a_20pct_stop_above_3x():
    """Pins the defect: 20% asked, 10% delivered at 6x. This is the whole bug."""
    import pytest
    assert _stop_after_clamp(3, 20.0) == pytest.approx(0.20)   # intended
    assert _stop_after_clamp(4, 20.0) == pytest.approx(0.15)   # silently retuned
    assert _stop_after_clamp(6, 20.0) == pytest.approx(0.10)   # different strategy


def test_stop_honoring_leverage_caps_to_fit_the_stop():
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    assert cap(6, 20.0) == 3          # 0.60/0.20
    assert cap(4, 20.0) == 3
    assert cap(3, 20.0) == 3          # exact fit must NOT floor to 2
    assert cap(6, 15.0) == 4          # 0.60/0.15
    assert cap(12, 6.0) == 10         # 0.60/0.06
    assert cap(2, 20.0) == 2          # already fits -> untouched
    assert cap(6, 0.0) == 6           # no stop requested -> no cap
    assert cap(6, 20.0, 0.0) == 6     # clamp disabled -> no cap
    assert cap(1, 20.0) >= 1          # never below 1


def test_capped_leverage_always_delivers_the_requested_stop():
    """The invariant the cap exists to hold, over every book/stop combination."""
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    for stop in (6.0, 15.0, 20.0, 25.0):
        for lev in range(1, 13):
            eff = cap(lev, stop)
            delivered = _stop_after_clamp(eff, stop)
            assert delivered >= stop / 100.0 - 1e-9, (
                f"{lev}x/{stop}% capped to {eff}x still delivers "
                f"{delivered:.1%}, not {stop/100:.1%}")


# ------------------------- maintenance-aware liq bound (2026-07-24, operator)
# "I DON'T MIND A CRAZY DRAWDOWN AS LONG AS THE SWING IS WORTH IT, NEVER GET
# LIQUIDATED." The naive bound (0.60/lev) treats the liq distance as 1/lev,
# which ignores maintenance margin. On a low-maxLeverage coin maint dominates
# and the naive rule authorizes a stop OUTSIDE liquidation.
def test_naive_bound_permits_a_stop_outside_liquidation_on_low_maxlev_coins():
    """Pins the hole: BOME-class (maxLev 3 -> maint 16.7%) liquidates at 16.7%
    at 3x, but the naive rule authorizes a 20% stop there."""
    maint = 1.0 / (2 * 3)
    liq_at_3x = 1.0 / 3 - maint
    import pytest
    assert liq_at_3x < 0.20                              # dies at 16.7%
    assert _backup_sl_clamp(3) == pytest.approx(0.20)    # yet 20% is allowed


def test_maint_aware_cap_never_authorizes_liquidation():
    """The operator constraint, over every coin class / stop / leverage."""
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    for coin_max in (3, 5, 10, 20, 40):
        maint = 1.0 / (2 * coin_max)
        for stop in (6.0, 15.0, 20.0, 25.0, 30.0, 40.0):
            for lev in range(1, 13):
                eff = cap(lev, stop, 0.60, coin_max)
                assert eff >= 1
                liq = 1.0 / eff - maint
                assert stop / 100.0 < liq or eff == 1, (
                    f"maxLev {coin_max}, {stop}% stop, {lev}x -> {eff}x "
                    f"liquidates at {liq:.1%} before the stop")


def test_both_bounds_are_load_bearing():
    """Width bound and liq bound each bind alone; neither implies the other."""
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    # xyz (maint 2.5%): the WIDTH clamp binds first — liq alone would allow 3x
    # for a 25% stop (0.85 * 30.8% = 26.2%), but 0.60/3 = 20% < 25%.
    assert cap(6, 25.0, 0.60, 20, 0.85) == 2
    # BOME-class (maint 16.7%): the LIQ bound binds first — width alone would
    # allow 3x for a 20% stop (0.60/3 = 20%), but liq at 3x is only 16.7%.
    assert cap(6, 20.0, 0.60, 3, 0.85) == 2
    assert cap(6, 20.0, 0.60, 3, 0.0) == 3      # liq bound off -> width only


def test_maint_aware_cap_is_stricter_than_the_naive_one():
    """A 20% stop fits at 3x under the naive bound but only 2x once maintenance
    margin is counted. Stricter is the point — liquidation is the thing we will
    not trade against."""
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    assert cap(6, 20.0, 0.60, 0) == 3          # no coin meta -> width bound
    assert cap(6, 20.0, 0.60, 20, 0.60) == 2   # strict safety -> 2x
    # the cap only ever walks DOWN from what the book asked for
    assert cap(6, 6.0, 0.60, 20) == 6          # tight stop -> request untouched
    assert cap(12, 6.0, 0.60, 20) == 10        # ...and 6% fits up to 10x
    assert cap(2, 20.0, 0.60, 20) == 2         # already fits -> untouched


def test_cap_degrades_to_1x_rather_than_authorizing_a_dead_stop():
    """A stop so wide that no leverage fits must fall to 1x, never to a
    leverage where the stop cannot fire."""
    from hermes_trader.agents.executor import stop_honoring_leverage as cap
    assert cap(6, 90.0, 0.60, 20) == 1


