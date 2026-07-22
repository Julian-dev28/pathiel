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

import glob
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


def test_news_surge_short_registered_everywhere_it_needs_to_be():
    """A book that opens live orders but is missing from claim rights or
    priority attribution is a silent leak: it trades, but claims/PnL don't
    know it exists (the exact 2026-07-18 demolition trap, in reverse)."""
    import scripts.pnl_by_book as pbb
    from hermes_trader.agents.rebalancer_owned import active_claim_books
    from hermes_trader.agents import news_surge_short_live as nssl

    assert nssl._BOOK_NAME in active_claim_books()
    assert nssl._BOOK_NAME in pbb.BOOK_PRIORITY


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
    ("mover_recorders", "pass_short_live"),
    ("mover_recorders", "young_short_live"),
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


# --------------------------------------------------------------- real-registry collisions
def test_news_surge_short_and_engulf_short_cannot_both_claim_the_same_coin(tmp_path):
    """Two unrelated live books racing the same claims file: whichever
    claims first wins, the second must see it and back off. Real
    ClaimsRegistry, not a mock — proves the file-backed contract holds."""
    import hermes_trader.agents.rebalancer_owned as ro

    claims_path = str(tmp_path / ".rebalancer_claims.json")
    registry = ro.ClaimsRegistry(claims_path).load()

    assert registry.claim("XPL", "engulf_short") is True
    assert "XPL" in registry.claimed_by_others("news_surge_short")
    assert registry.claim("XPL", "news_surge_short") is False
    registry.release("XPL", "engulf_short")
    assert registry.claim("XPL", "news_surge_short") is True


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


def test_xs_momentum_is_crypto_and_never_hit_by_the_hip3_cap():
    """The operator's #1 worry: xs_momentum (the one earner) has a 5-day hold
    and no tight DSL stop. It trades CRYPTO — the hip3 cap only fires on ':'
    coins, so it can NEVER touch xs_momentum's leverage. Its exit override is
    also untouched (leverage/margin changes don't rewrite dsl_exit_override)."""
    for coin in ("BTC", "ETH", "FARTCOIN", "kBONK", "SOL"):
        assert ":" not in coin           # no hip3 cap path
    # xs_xyz IS xyz -> cap applies (that's why it gets its own low leverage)
    assert ":" in "xyz:AAPL"


def test_short_books_at_6x_have_a_working_6pct_stop():
    """Short books: 6% stop, 1-day hold. At 6x the liq buffer (11.7%) sits well
    beyond the 6% stop AND beyond the clamped backup SL (10%)."""
    assert _liq_buffer(6) > 0.06                 # stop reachable
    assert _backup_sl_clamp(6) < _liq_buffer(6)  # clamp fires before liq


def test_xs_xyz_at_3x_restores_its_validated_20pct_stop():
    """xs_xyz: 20% disaster stop, 5-day hold. It needs <=4x for the stop to
    fire before liquidation; 3x gives liq ~28% >> the 20% stop. The old 12x
    liquidated at 3.3% (stop dead) — the force-liquidation bug."""
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    lev = int(cfg["xs_xyz_equities"]["leverage"])
    assert lev <= 4, f"xs_xyz {lev}x too high for its 20% stop"
    assert _liq_buffer(lev) > 0.20               # 20% disaster stop reachable
    # and the OLD 12x was broken (liq below the stop) — pins the bug we fixed
    assert _liq_buffer(12) < 0.06


def test_xs_xyz_analysis_actually_emits_the_low_leverage():
    """End-to-end: the built analysis must carry the 3x override so the
    executor's min() lands on 3, not the global 12/6-cap."""
    from hermes_trader.agents import xs_xyz_live as xl
    a = xl._analysis("xyz:AAPL", "long", 0.05, leverage=3)
    assert a["leverage_override"] == 3
    assert a["backup_sl_pct_override"] == 20.0   # validated wide stop intact
    assert a["dsl_exit_override"]["max_loss_pct"] == 20.0
