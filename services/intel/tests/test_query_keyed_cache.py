"""2026-11-02 regression: the oil desk added "Iran ceasefire" to its
prediction-market queries, but the intel cache was keyed by source name
only, so the old queries' cached odds kept being served and the new
ceasefire odds never reached the thesis."""
from __future__ import annotations

from services.intel import api
from services.intel.evals.eval_war_odds import check_rendered
from services.strategies.desks import require_spec


def test_changing_a_desk_query_changes_the_cache_file():
    spec = require_spec("oil")
    other = spec.model_copy(update={"prediction_market_queries": ["Iran deal"]})
    key = "predictions.fetch_polymarket"
    a = api._cache_key("predictions", key, (spec,))
    assert a == api._cache_key("predictions", key, (spec,))  # stable
    assert a != api._cache_key("predictions", key, (other,))
    assert a.startswith(key + "@")


def test_sources_that_ignore_the_queries_keep_their_plain_key():
    spec = require_spec("oil")
    assert api._cache_key("positioning", "positioning.fetch_cot", (spec,)) == "positioning.fetch_cot"
    assert api._cache_key("eia", "eia.fetch", (spec,)) == "eia.fetch"


def test_oil_and_metals_search_the_iran_war_odds_and_oil_the_strike_news():
    oil = require_spec("oil")
    assert "Iran ceasefire" in oil.prediction_market_queries
    assert "US strike Iran" in oil.news_queries[:6]   # GDELT reads the first 6
    assert "Iran oil exports" in oil.news_queries[:6]
    assert "Iran ceasefire" in require_spec("metals").prediction_market_queries


def test_war_odds_eval_flags_a_prompt_without_the_ceasefire_row():
    blind = ("PREDICTION MARKETS:\n"
             "[a] polymarket p=0.41 $4795K vol US announces end of Iranian blockade by December 31, 2026?\n"
             "POSITIONING:\n")
    seen = ("HEADLINES:\n[h] bear rel=0.8 1h Reuters Trump names Iran's Pickaxe Mountain as strike target\n"
            "PREDICTION MARKETS:\n"
            "[b] polymarket p=0.37 $1114K vol US x Iran ceasefire continues through November 30?\n"
            "POSITIONING:\n")
    assert check_rendered("oil", blind)["ok"] is False
    r = check_rendered("oil", seen)
    assert r["ok"] is True and r["odds_rows"] and r["strike_headlines"]


def test_metals_searches_the_fed_meeting_odds_and_gold_drivers():
    """Gold is the metals primary since 2026-11-05: the Fed meeting odds stay
    (gold trades the same rate risk silver did), the silver supply phrases
    gave way to gold's drivers (central bank buying, ETF flows, the dollar)."""
    m = require_spec("metals")
    assert {"Fed December", "gold price"} <= set(m.prediction_market_queries)
    assert {"gold price", "central bank gold buying", "gold ETF flows", "dollar index"} <= set(m.news_queries)
    assert len(m.news_queries) <= 8   # GNEWS_MAX_QUERIES


def test_war_odds_eval_needs_the_fed_meeting_row_for_metals():
    base = ("PREDICTION MARKETS:\n"
            "[b] polymarket p=0.74 $1173K vol US x Iran ceasefire continues through November 8?\n")
    fed = "[f] polymarket p=0.70 $2000K vol Will the Fed increase interest rates by 25 bps after the December 2026 meeting?\n"
    assert check_rendered("metals", base + "POSITIONING:\n")["ok"] is False
    assert check_rendered("metals", base + fed + "POSITIONING:\n")["ok"] is True
