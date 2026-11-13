"""Testnet stand-ins for desk instruments, and no candle read for an unlisted
coin (decisions of 2026-11-12).

On testnet xyz:BRENTOIL is delisted and xyz:CL is live, so the oil desk trades
WTI there as the Brent stand-in (services/strategies/desks.substitutions). On
mainnet nothing changes and nothing is read. The analytics candle fetcher
skips a coin testnet does not list: hl_client.fetch_hl_candles retries a
non-list reply for about 24 s, which held desk_brief near the 30 s deadline.

Offline: exchange.coin_listing and fetch_hl_candles are fakes.
"""
from __future__ import annotations

import json

import pytest

from contracts.strategies.models import DeskSpec, Instrument, NetworkSubstitute
from pathiel.client import exchange, hl_client
from services.strategies import analytics as A
from services.strategies import desks, listing
from services.strategies.desks import oil

WTI = "Testnet: this desk trades WTI (xyz:CL) as the stand-in for Brent (xyz:BRENTOIL)"


class Listing:
    """A fake exchange.coin_listing: answers from a dict, counts calls."""

    def __init__(self, answers=None, default="listed"):
        self.answers = dict(answers or {})
        self.default = default
        self.calls = []

    def __call__(self, coin):
        self.calls.append(coin)
        value = self.answers.get(coin, self.default)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def venue(monkeypatch):
    fake = Listing({"xyz:BRENTOIL": "delisted", "xyz:ARM": "not_listed"})
    monkeypatch.setattr(exchange, "coin_listing", fake)
    return fake


@pytest.fixture
def testnet(monkeypatch):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")


def _primary(spec):
    return next(i for i in spec.instruments if i.role == "primary")


# ── the spec ──────────────────────────────────────────────────────────────────

def test_brent_carries_one_verified_substitute():
    spec = oil.build_spec()
    brent = _primary(spec)
    assert brent.coin == "xyz:BRENTOIL"
    sub = brent.testnet_substitute
    assert isinstance(sub, NetworkSubstitute)
    assert (sub.coin, sub.label) == ("xyz:CL", "WTI")
    assert sub.verified.startswith("2026-11-12 testnet")
    # only verified substitutes: no other desk instrument carries one
    others = [(s.id, i.coin) for s in (desks.registry_spec(d) for d in desks.list_desk_ids())
              for i in s.instruments if i.testnet_substitute is not None]
    assert others == [("oil", "xyz:BRENTOIL")]


def test_the_substitute_is_never_serialized():
    """Excluded from every dump: specs, briefs and journals stay
    byte-identical for the operator, and old readers never see the field."""
    spec = oil.build_spec()
    dumped = spec.model_dump(mode="json")
    assert "testnet_substitute" not in json.dumps(dumped)
    assert "testnet_substitute" not in spec.model_dump_json()
    assert set(dumped["instruments"][0]) == {"coin", "label", "role"}
    # a dumped spec still validates (round trip without the field)
    assert DeskSpec.model_validate(dumped).instruments[0].testnet_substitute is None


# ── mainnet: untouched, no read ───────────────────────────────────────────────

@pytest.mark.parametrize("install", [None, "public"])
def test_mainnet_spec_is_the_registry_spec_and_reads_nothing(monkeypatch, venue, install):
    if install:
        monkeypatch.setenv("PATHIEL_INSTALL", install)
        monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 20.0)
    for net in (None, "mainnet"):
        if net:
            monkeypatch.setenv("PATHIEL_NETWORK", net)
        spec = desks.get_spec("oil")
        assert _primary(spec).coin == "xyz:BRENTOIL"
        assert [p.label for p in spec.pairs] == ["Brent-WTI spread"]
        assert desks.substitutions(desks.registry_spec("oil")) == []
        assert desks.substitution_note(desks.registry_spec("oil")) is None
    assert venue.calls == [], "mainnet never asks the venue about listings"


def test_operator_mainnet_gets_the_cached_object_itself(monkeypatch, venue):
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)
    monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
    assert desks.get_spec("oil") is desks.registry_spec("oil")


# ── testnet ───────────────────────────────────────────────────────────────────

def test_testnet_oil_desk_trades_wti(testnet, venue):
    spec = desks.get_spec("oil")
    assert [(i.coin, i.label, i.role) for i in spec.instruments] == [
        ("xyz:CL", "WTI", "primary"), ("xyz:NATGAS", "Nat Gas", "hedge")]
    assert spec.pairs == [], "Brent-WTI would be WTI-WTI"
    assert "On testnet WTI (xyz:CL) stands in for Brent (xyz:BRENTOIL)" in spec.tagline
    assert _primary(desks.registry_spec("oil")).coin == "xyz:BRENTOIL", "registry untouched"
    assert [s.id for s in desks.list_specs()][0] == "oil"
    assert _primary(desks.list_specs()[0]).coin == "xyz:CL"


@pytest.mark.parametrize("status,why", [
    ("delisted", "which is delisted on testnet"),
    ("not_listed", "which is not listed on testnet"),
    ("unknown", "which is not confirmed as listed on testnet"),
])
def test_the_brief_line_says_it_plainly(testnet, monkeypatch, status, why):
    monkeypatch.setattr(exchange, "coin_listing", Listing({"xyz:BRENTOIL": status}))
    note = desks.substitution_note(desks.registry_spec("oil"))
    assert note.startswith(f"{WTI}, {why}.")
    assert "Prices, levels and sizes here are WTI, not Brent; mainnet trades Brent." in note
    assert _primary(desks.get_spec("oil")).coin == "xyz:CL"


def test_testnet_that_lists_brent_keeps_brent(testnet, monkeypatch):
    monkeypatch.setattr(exchange, "coin_listing", Listing({"xyz:BRENTOIL": "listed"}))
    assert _primary(desks.get_spec("oil")).coin == "xyz:BRENTOIL"
    assert desks.substitution_note(desks.registry_spec("oil")) is None


def test_a_raising_listing_read_swaps_and_never_raises(testnet, monkeypatch):
    monkeypatch.setattr(exchange, "coin_listing", Listing({"xyz:BRENTOIL": RuntimeError("down")}))
    assert listing.coin_status("xyz:BRENTOIL") == "unknown"
    assert _primary(desks.get_spec("oil")).coin == "xyz:CL"


def test_public_testnet_cuts_the_ladder_after_the_swap(testnet, venue, monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 3.0)
    spec = desks.get_spec("oil")
    assert _primary(spec).coin == "xyz:CL" and list(spec.leverage_ladder) == [2.0, 3.0]


def test_desks_without_a_substitute_are_untouched_on_testnet(testnet, venue):
    for desk_id in desks.list_desk_ids():
        if desk_id == "oil":
            continue
        assert desks.get_spec(desk_id) is desks.registry_spec(desk_id), desk_id


def test_a_substitute_on_a_secondary_keeps_its_role(testnet, monkeypatch):
    """The mechanism is per instrument: metals etc. can add one later."""
    monkeypatch.setattr(exchange, "coin_listing", Listing({"xyz:SILVER": "not_listed"}))
    spec = DeskSpec(id="demo", name="Demo", tagline="t", instruments=[
        Instrument(coin="xyz:GOLD", label="Gold", role="primary"),
        Instrument(coin="xyz:SILVER", label="Silver", role="secondary",
                   testnet_substitute=NetworkSubstitute(coin="xyz:PLATINUM", label="Platinum",
                                                         verified="test only"))])
    out = desks._for_network(spec)
    assert [(i.coin, i.role) for i in out.instruments] == [("xyz:GOLD", "primary"),
                                                           ("xyz:PLATINUM", "secondary")]


# ── the listing cache ─────────────────────────────────────────────────────────

def test_listing_is_cached_per_network_and_coin(testnet, venue):
    for _ in range(5):
        desks.get_spec("oil")
        listing.coin_status("xyz:BRENTOIL")
    assert venue.calls.count("xyz:BRENTOIL") == 1


def test_an_unknown_answer_expires_sooner(testnet, monkeypatch):
    fake = Listing(default="unknown")
    monkeypatch.setattr(exchange, "coin_listing", fake)
    now = [1000.0]
    monkeypatch.setattr(listing.time, "monotonic", lambda: now[0])
    listing.coin_status("xyz:X")
    now[0] += listing.UNKNOWN_TTL_S + 1
    listing.coin_status("xyz:X")
    assert len(fake.calls) == 2
    fake.default = "listed"
    listing.coin_status("xyz:X")
    now[0] += listing.UNKNOWN_TTL_S + 1
    listing.coin_status("xyz:X")
    assert len(fake.calls) == 3, "a venue answer lives LISTED_TTL_S"
    assert listing.UNKNOWN_TTL_S < listing.LISTED_TTL_S


def test_concurrent_cold_reads_ask_the_venue_once(testnet, monkeypatch):
    import threading
    import time as _time
    fake = Listing({"xyz:CL": "listed"})

    def slow(coin):
        _time.sleep(0.05)
        return fake(coin)

    monkeypatch.setattr(exchange, "coin_listing", slow)
    threads = [threading.Thread(target=listing.coin_status, args=("xyz:CL",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.calls == ["xyz:CL"]


# ── analytics: no candle read for an unlisted coin ───────────────────────────

class Candles:
    def __init__(self):
        self.calls = []

    def __call__(self, coin, interval, count):
        self.calls.append((coin, interval))
        return ["bar"]


def test_testnet_unlisted_coin_gets_no_candle_read(testnet, venue, monkeypatch):
    fetch = Candles()
    monkeypatch.setattr(hl_client, "fetch_hl_candles", fetch)
    assert A.default_candle_fetcher("xyz:ARM", "1d", 30) == []
    assert A.default_candle_fetcher("xyz:BRENTOIL", "1h", 30) == []
    assert fetch.calls == []
    assert A.default_candle_fetcher("xyz:CL", "1d", 30) == ["bar"]
    assert fetch.calls == [("xyz:CL", "1d")]


def test_testnet_unknown_listing_still_reads(testnet, monkeypatch):
    monkeypatch.setattr(exchange, "coin_listing", Listing(default="unknown"))
    fetch = Candles()
    monkeypatch.setattr(hl_client, "fetch_hl_candles", fetch)
    assert A.default_candle_fetcher("xyz:ARM", "1d", 30) == ["bar"]


def test_mainnet_candles_never_ask_about_listings(monkeypatch, venue):
    monkeypatch.delenv("PATHIEL_NETWORK", raising=False)
    fetch = Candles()
    monkeypatch.setattr(hl_client, "fetch_hl_candles", fetch)
    assert A.default_candle_fetcher("xyz:ARM", "1d", 30) == ["bar"]
    assert venue.calls == [] and fetch.calls == [("xyz:ARM", "1d")]


def test_the_30s_brief_regression_no_retry_sleep_for_an_unlisted_coin(testnet, venue, monkeypatch):
    """The bug: candleSnapshot for a coin testnet does not list never answers
    with a list, so fetch_hl_candles slept 0.5+1+2+4+8+8 s in retries. With
    the real fetcher and a venue that answers None, an unlisted coin now
    costs no request and no sleep; a listed one still gets the old retries."""
    posts, sleeps = [], []
    monkeypatch.setattr(hl_client, "_http_post", lambda path, payload, **k: posts.append(payload))
    monkeypatch.setattr(hl_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(hl_client, "_CANDLE_CACHE", {})
    assert A.default_candle_fetcher("xyz:ARM", "1d", 400) == []
    assert posts == [] and sleeps == []
    assert A.default_candle_fetcher("xyz:CL", "1d", 400) == []
    assert len(posts) == 7 and sum(sleeps) == 23.5, "listed coins keep the transient-error retries"
