"""Unit tests for services/intel/sources/catalysts.py: the always-on /
content-sniffed kind gating, the deterministic calendar merge, and the
three live checks (NHC storms, token unlocks, earnings) against real
captured data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import desk_rules, http_client
from services.intel.sources import catalysts
from services.intel.tests.conftest import fixture_json

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def _spec(coin="xyz:CL", label="WTI", catalyst_kinds=None) -> DeskSpec:
    # id is deliberately NOT "oil" — desk_rules.is_oil_desk checks the id
    # string too, and a generic id here makes sure these tests are
    # actually exercising the INSTRUMENT-based sniffing, not accidentally
    # passing because the id itself contains "oil".
    return DeskSpec(id="desk", name="Desk", tagline="t",
                     instruments=[Instrument(coin=coin, label=label)],
                     catalyst_kinds=catalyst_kinds or [], horizon_days=7)


# ── active_kinds gating ──────────────────────────────────────────────

def test_fomc_and_cpi_always_on_even_if_not_listed():
    kinds = catalysts.active_kinds(_spec(catalyst_kinds=[]))
    assert "fomc" in kinds and "cpi" in kinds


def test_oil_desk_gets_hurricane_kind_even_if_not_listed():
    kinds = catalysts.active_kinds(_spec(catalyst_kinds=["eia_weekly"]))
    assert "hurricane_atlantic" in kinds


def test_crypto_desk_gets_unlock_kind():
    spec = _spec(coin="BTC", label="BTC", catalyst_kinds=[])
    assert "token_unlock" in catalysts.active_kinds(spec)


def test_equity_desk_gets_earnings_kind():
    spec = _spec(coin="NVDA", label="Nvidia", catalyst_kinds=[])
    assert "earnings" in catalysts.active_kinds(spec)


def test_oil_desk_does_not_get_unlock_or_earnings():
    kinds = catalysts.active_kinds(_spec())
    assert "token_unlock" not in kinds
    assert "earnings" not in kinds


def test_explicit_catalyst_kinds_are_respected():
    kinds = catalysts.active_kinds(_spec(catalyst_kinds=["opec_meeting", "baker_hughes"]))
    assert "opec_meeting" in kinds and "baker_hughes" in kinds


# ── calendar_catalysts (pure, deterministic) ─────────────────────────

def test_calendar_catalysts_respects_window():
    spec = _spec(catalyst_kinds=["eia_weekly"])
    cats = catalysts.calendar_catalysts(spec, NOW)
    start, end = catalysts.desk_window(spec, NOW)
    assert all(start <= c.at.date() <= end + timedelta(days=1) for c in cats)
    assert all(c.at >= NOW for c in cats)


def test_calendar_catalysts_oil_fixture_kinds_present():
    spec = _spec(catalyst_kinds=["eia_weekly", "api_weekly", "opec_meeting", "cftc_cot",
                                  "baker_hughes", "fomc"])
    cats = catalysts.calendar_catalysts(spec, NOW)
    kinds_present = {c.kind for c in cats}
    assert {"eia_weekly", "api_weekly", "cftc_cot", "baker_hughes", "fomc"} <= kinds_present


def test_calendar_catalysts_ids_stable():
    spec = _spec(catalyst_kinds=["eia_weekly"])
    a = catalysts.calendar_catalysts(spec, NOW)
    b = catalysts.calendar_catalysts(spec, NOW)
    assert [c.id for c in a] == [c.id for c in b]


def test_calendar_catalysts_assumption_shift_sets_expected_field():
    """A generator whose note mentions an assumption/shift must surface
    that on the Catalyst's `expected` field — nothing about an unverified
    rule should be silently indistinguishable from the verified ones."""
    spec = _spec(catalyst_kinds=["opec_meeting"])
    cats = catalysts.calendar_catalysts(spec, NOW)
    opec = [c for c in cats if c.kind == "opec_meeting"]
    assert len(opec) == 1
    assert opec[0].expected is not None and "ASSUMPTION" in opec[0].expected


# ── NHC Atlantic storms ──────────────────────────────────────────────

def test_nhc_real_fixture_has_no_atlantic_storms_right_now(monkeypatch):
    """The real capture (2026-10-17) has two active East Pacific storms
    and zero Atlantic ones — a genuine, useful negative test: the basin
    filter must correctly produce an EMPTY catalyst list here, not
    silently include Pacific storms as if they were Atlantic."""
    fixture = fixture_json("nhc_current_storms.json")
    assert all(not s["id"].lower().startswith("al") for s in fixture["activeStorms"])
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    cats, err = catalysts.fetch_hurricane_catalysts(_spec(), NOW)
    assert err is None
    assert cats == []


def test_nhc_synthetic_atlantic_storm_is_captured(monkeypatch):
    data = {"activeStorms": [{
        "id": "al052026", "name": "Testia", "classification": "HU", "intensity": "100",
        "lastUpdate": "2026-10-17T00:00:00.000Z",
        "publicAdvisory": {"advNum": "012"},
    }]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    cats, err = catalysts.fetch_hurricane_catalysts(_spec(), NOW)
    assert err is None
    assert len(cats) == 1
    assert cats[0].kind == "hurricane_atlantic"
    assert cats[0].importance == 3  # "HU" classification
    assert "Testia" in cats[0].title


def test_nhc_skipped_for_non_oil_desk():
    cats, err = catalysts.fetch_hurricane_catalysts(_spec(coin="BTC", label="BTC"), NOW)
    assert cats == [] and err is None


def test_nhc_http_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    cats, err = catalysts.fetch_hurricane_catalysts(_spec(), NOW)
    assert cats == [] and "catalysts:nhc_storms:timeout" == err


# ── token unlocks ────────────────────────────────────────────────────

def test_unlock_catalyst_filters_by_desk_ticker_and_min_pct(monkeypatch):
    spec = _spec(coin="BTC", label="BTC")
    monkeypatch.setattr(desk_rules, "UNLOCK_MAP_PATH", desk_rules.UNLOCK_MAP_PATH)  # no-op, real map
    soon = (NOW + timedelta(days=3)).timestamp()
    data = {"data": [
        {"name": "Bitcoin", "circSupply": 1_000_000, "events": [
            {"timestamp": soon, "noOfTokens": [50_000]},  # 5% of circ, above the 1% floor
        ]},
        {"name": "Ethereum", "circSupply": 1_000_000, "events": [
            {"timestamp": soon, "noOfTokens": [50_000]},  # real token, but not in this desk
        ]},
    ]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    cats, err = catalysts.fetch_unlock_catalysts(spec, NOW)
    assert err is None
    assert len(cats) == 1
    assert cats[0].kind == "token_unlock"
    assert "BTC" in cats[0].title
    assert "5.0%" in cats[0].title


def test_unlock_catalyst_below_min_pct_is_dropped(monkeypatch):
    spec = _spec(coin="BTC", label="BTC")
    soon = (NOW + timedelta(days=3)).timestamp()
    data = {"data": [{"name": "Bitcoin", "circSupply": 1_000_000, "events": [
        {"timestamp": soon, "noOfTokens": [100]},  # 0.01% of circ — below MIN_UNLOCK_PCT_CIRC
    ]}]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    cats, err = catalysts.fetch_unlock_catalysts(spec, NOW)
    assert cats == [] and err is None


def test_unlock_catalyst_outside_window_is_dropped(monkeypatch):
    spec = _spec(coin="BTC", label="BTC")
    far_future = (NOW + timedelta(days=365)).timestamp()
    data = {"data": [{"name": "Bitcoin", "circSupply": 1_000_000, "events": [
        {"timestamp": far_future, "noOfTokens": [50_000]},
    ]}]}
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (data, None))
    cats, err = catalysts.fetch_unlock_catalysts(spec, NOW)
    assert cats == [] and err is None


def test_unlock_skipped_for_oil_desk():
    cats, err = catalysts.fetch_unlock_catalysts(_spec(), NOW)
    assert cats == [] and err is None


def test_unlock_http_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    cats, err = catalysts.fetch_unlock_catalysts(_spec(coin="BTC", label="BTC"), NOW)
    assert cats == [] and "token_unlock" in err and "timeout" in err


# ── earnings estimate ────────────────────────────────────────────────

def test_earnings_estimate_real_fixture_projects_next_quarter(monkeypatch):
    tickers_fixture = fixture_json("sec_company_tickers_sample.json")
    sub_fixture = fixture_json("sec_nvda_submissions_sample.json")
    recent = sub_fixture["filings"]["recent"]
    last_10q_date = max(d for f, d in zip(recent["form"], recent["filingDate"]) if f in ("10-Q", "10-K"))
    last_dt = datetime.strptime(last_10q_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    expected_est = (last_dt + timedelta(days=91)).date()

    def fake(url, **kw):
        if "company_tickers" in url:
            return tickers_fixture, None
        return sub_fixture, None

    monkeypatch.setattr(http_client, "fetch_json", fake)
    spec = _spec(coin="NVDA", label="Nvidia")
    # Widen the horizon so the projected date actually falls inside the window
    # regardless of how far `expected_est` lands from "today" in this fixture.
    spec = spec.model_copy(update={"horizon_days": 400})
    cats, err = catalysts.fetch_earnings_catalysts(spec, NOW)
    if expected_est < NOW.date() or expected_est > (NOW + timedelta(days=400 + catalysts.EXTRA_LOOKAHEAD_DAYS)).date():
        assert cats == []  # projected date doesn't land in a sane window — acceptable, not a bug
    else:
        assert err is None
        assert len(cats) == 1
        assert cats[0].kind == "earnings"
        assert "NVDA" in cats[0].title
        assert "estimated" in cats[0].expected


def test_earnings_skipped_for_non_equity_desk():
    cats, err = catalysts.fetch_earnings_catalysts(_spec(), NOW)
    assert cats == [] and err is None


def test_earnings_unmapped_ticker_is_clean_noop(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ({}, None))
    cats, err = catalysts.fetch_earnings_catalysts(_spec(coin="ZZZZZ", label="Unknown Co"), NOW)
    assert cats == [] and err is None


def test_earnings_sec_tickers_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    cats, err = catalysts.fetch_earnings_catalysts(_spec(coin="NVDA", label="Nvidia"), NOW)
    assert cats == [] and "sec_tickers" in err and "timeout" in err


# ── full fetch_catalysts convenience path ───────────────────────────

def test_fetch_catalysts_sorted_by_time(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    spec = _spec(catalyst_kinds=["eia_weekly", "fomc"])
    cats, failed = catalysts.fetch_catalysts(spec, NOW)
    assert cats == sorted(cats, key=lambda c: c.at)
    assert len(failed) > 0  # the mocked live checks all fail, and must say so
