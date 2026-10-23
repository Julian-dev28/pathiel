from __future__ import annotations

from services.risk import books


def test_book_names_is_permanently_empty():
    """Headless desk, 2026-10-23: the seven strategy books
    (news_surge_short, news_surge_multi, social_trending, unlock_short_runin,
    xs_reversal, drawdown_ladder, copy_trade) are all deleted. BOOK_NAMES
    used to mirror pathiel.agents.rebalancer_owned._ACTIVE_CLAIM_BOOKS (also
    deleted, same reason) and now stays empty on purpose — see books.py's
    module docstring for why `all_book_specs()` does NOT follow suit."""
    assert books.BOOK_NAMES == ()
    assert books.RISK_SCALED_BOOK_NAMES == ()
    assert books._FIXED_BOOKS == {}
    assert books._UNSIZED_BOOKS == frozenset()


def test_config_key_for_is_the_identity_map():
    """No naming split survives the books (unlock_short_runin -> unlock_short
    was the one exception) — config_key_for is now just `name -> name`."""
    for name in ("main_engine", "anything", "unlock_short_runin"):
        assert books.config_key_for(name) == name


def test_main_engine_spec_is_always_enabled():
    """Unlike a book's BookSpec (enabled read from its own config block's
    `enabled` key), main_engine_spec forces enabled=True — whether this path
    may trade at all is decided elsewhere (mode == LIVE,
    main_engine.entries_enabled), not by a config block this path has never
    had."""
    spec = books.main_engine_spec({})
    assert spec.name == books.MAIN_ENGINE == "main_engine"
    assert spec.enabled is True


def test_main_engine_spec_resolves_sizing_through_book_params():
    cfg = {"leverage": 5, "default_stop_pct": 12.0}
    spec = books.main_engine_spec(cfg)
    assert spec.stop_pct == 12.0
    assert spec.max_leverage == 5.0


def test_main_engine_spec_coin_max_leverage_tightens_the_cap():
    cfg = {"leverage": 20}
    spec = books.main_engine_spec(cfg, coin_max_leverage=5)
    assert spec.max_leverage == 5.0   # the tighter of 20 (config) and 5 (coin)


def test_all_book_specs_is_the_one_item_main_engine_roster():
    """NOT `[]`: derive_limits computes can_trade = any(r.can_trade for r in
    rows), so an empty roster would make every risk-limits reader (the
    dashboard, GET /api/risk/limits, the MCP risk_limits tool) report
    'cannot trade' even on a funded account. See books.py's module
    docstring — this is the one regression this whole test file exists to
    catch."""
    specs = books.all_book_specs({})
    assert [s.name for s in specs] == [books.MAIN_ENGINE]
    assert specs[0].enabled is True


def test_all_book_specs_resolves_against_the_passed_config():
    cfg = {"leverage": 7, "default_stop_pct": 9.0}
    specs = books.all_book_specs(cfg)
    assert len(specs) == 1
    assert (specs[0].stop_pct, specs[0].max_leverage) == (9.0, 7.0)


def test_derive_limits_can_trade_with_the_main_engine_roster():
    """The regression this file exists to catch, end to end: a funded
    account must show can_trade=True from the real all_book_specs() output,
    not 'No book is configured to trade yet.'"""
    from contracts.risk.models import RiskProfile
    from services.risk.derive import VenueRules, derive_limits

    cfg = {"leverage": 3, "default_stop_pct": 15.0}
    limits = derive_limits(50.0, RiskProfile(), books.all_book_specs(cfg),
                           VenueRules(min_order_notional_usd=10.5))
    assert limits.can_trade is True
    assert "No book is configured to trade yet" not in limits.headline


def test_book_spec_from_config_still_works_for_an_arbitrary_name():
    """book_spec_from_config stays general-purpose (pathiel.agents.executor
    no longer calls it directly, but nothing stops a future caller from
    asking for a named config block's BookSpec)."""
    cfg = {"anything": {"enabled": True, "shadow_only": False,
                        "leverage": 10, "stop_pct": 6.0}}
    spec = books.book_spec_from_config(cfg, "anything")
    assert spec.enabled is True
    assert spec.stop_pct == 6.0
    assert spec.max_leverage == 10.0


def test_book_spec_from_config_disabled_when_block_absent():
    spec = books.book_spec_from_config({}, "anything")
    assert spec.enabled is False


def test_book_spec_from_config_disabled_when_shadow_only():
    cfg = {"anything": {"enabled": True, "shadow_only": True}}
    spec = books.book_spec_from_config(cfg, "anything")
    assert spec.enabled is False
