"""@pytest.mark.online smoke tests — one per real source, actual network,
deselected by default (pyproject.toml's addopts). Run with:

    PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest \\
        services/intel/tests/test_online_smoke.py -m online -q

These assert the WIRING still works against the real endpoint today —
reachable, parses, shaped correctly — not that today's content looks any
particular way, and not that every source is always up: a transient 429
or timeout is tolerated as long as it is reported as an error, not a
crash. GDELT specifically is known to rate-limit this sandbox's shared
egress IP (see sources/news.py's module docstring), so its assertion is
looser than the others on purpose.
"""
from __future__ import annotations

import os

import pytest

from contracts.strategies.models import DeskSpec, Instrument
from services.intel.sources import catalysts, news, policy, positioning, predictions

pytestmark = pytest.mark.online


def _oil_spec() -> DeskSpec:
    return DeskSpec(
        id="oil", name="Oil", tagline="t",
        instruments=[Instrument(coin="xyz:CL", label="WTI"),
                     Instrument(coin="xyz:BRENTOIL", label="Brent", role="secondary")],
        news_queries=["crude oil price", "Strait of Hormuz", "OPEC+"],
        policy_topics=["Iran sanctions"],
        prediction_market_queries=["oil price"],
        catalyst_kinds=["eia_weekly"],
    )


def _btc_spec() -> DeskSpec:
    return DeskSpec(id="btc", name="Bitcoin", tagline="t",
                     instruments=[Instrument(coin="BTC", label="BTC")],
                     news_queries=["bitcoin price"], prediction_market_queries=["bitcoin price"])


def test_online_google_news():
    items, err = news.fetch_google_news(_oil_spec())
    assert err is None
    assert len(items) > 0


def test_online_oilprice_rss():
    items, err = news.fetch_oilprice(_oil_spec())
    assert err is None
    assert len(items) > 0


def test_online_crypto_rss():
    items, err = news.fetch_crypto_rss(_btc_spec())
    assert err is None
    assert len(items) > 0


def test_online_gdelt_reachable_even_if_rate_limited():
    items, err = news.fetch_gdelt(_oil_spec())
    assert isinstance(items, list)
    assert err is None or "gdelt" in err  # a 429 is acceptable, a crash is not


def test_online_federal_register():
    items, err = policy.fetch_federal_register(_oil_spec())
    assert err is None
    assert isinstance(items, list)


def test_online_ofac_recent_actions():
    """ofac.treasury.gov was observed to occasionally time out against
    this sandbox's default 4s connect / 6s read window during development
    (not a rate limit like GDELT — just a sometimes-slow government
    server) — tolerate a reported timeout the same way the GDELT smoke
    test does, as long as it is a clean (items, error) result, not a
    crash, and the HTML parser itself is still verified separately and
    deterministically in test_sources_policy.py against a real captured
    page."""
    items, err = policy.fetch_ofac(_oil_spec())
    assert isinstance(items, list)
    assert err is None or "timeout" in err
    if err is None:
        assert len(items) > 0


def test_online_congress_bills_reports_missing_key_without_crashing():
    had_key = os.environ.pop("CONGRESS_API_KEY", None)
    try:
        items, err = policy.fetch_congress_bills(_oil_spec())
        assert items == []
        assert err == "policy:congress_bills:CONGRESS_API_KEY_missing"
    finally:
        if had_key:
            os.environ["CONGRESS_API_KEY"] = had_key


def test_online_polymarket():
    odds, err = predictions.fetch_polymarket(_oil_spec())
    assert err is None
    assert isinstance(odds, list)


def test_online_kalshi():
    odds, err = predictions.fetch_kalshi(_oil_spec())
    assert isinstance(odds, list)
    # a transient series-catalog failure is tolerated; a crash is not


def test_online_cftc_cot():
    items, err = positioning.fetch_cot(_oil_spec())
    assert err is None
    assert len(items) > 0


def test_online_nhc_current_storms():
    import datetime as dt
    cats, err = catalysts.fetch_hurricane_catalysts(_oil_spec(), dt.datetime.now(dt.timezone.utc))
    assert err is None
    assert isinstance(cats, list)


def test_online_defillama_token_unlocks():
    import datetime as dt
    cats, err = catalysts.fetch_unlock_catalysts(_btc_spec(), dt.datetime.now(dt.timezone.utc))
    assert err is None
    assert isinstance(cats, list)


def test_online_sec_edgar_earnings():
    import datetime as dt
    spec = DeskSpec(id="aidesk", name="AI", tagline="t",
                     instruments=[Instrument(coin="NVDA", label="Nvidia")])
    cats, err = catalysts.fetch_earnings_catalysts(spec, dt.datetime.now(dt.timezone.utc))
    assert err is None
    assert isinstance(cats, list)
