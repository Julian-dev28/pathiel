"""pathiel.agents.hyperfeed: market-data lookups, asset classification, and
per-class funding regime.

Moved out of tests/test_cleanup.py 2026-10-23 (wave 2): that file was mostly
coverage for the retired autonomous engine (deleted with it), but hyperfeed.py
is kept (the MCP server's market/leaderboard tools route through it) and
this is real, otherwise-unduplicated coverage for it — tests/
test_hyperfeed_leaderboard.py covers the leaderboard discovery ranking/
caching, not these functions.

`classify_asset`/`_native_crypto_tickers` moved INTO hyperfeed.py from
pathiel.agents.market_regime (deleted the same wave: its trend detector fed
risk_gates.market_regime_gate, also deleted) — the classify_asset tests move
with them.
"""
from __future__ import annotations

from pathiel.models.types import Candle


# ── asset classification (moved in from market_regime.py) ──────────────────

def test_classify_asset():
    from pathiel.agents.hyperfeed import classify_asset
    # crypto default
    assert classify_asset("BTC") == "crypto"
    assert classify_asset("PEPE") == "crypto"
    assert classify_asset("randomcoin42") == "crypto"
    # equity perps
    assert classify_asset("TSLA") == "equity"
    assert classify_asset("nvda") == "equity"   # case-insensitive
    assert classify_asset("MSTR") == "equity"
    # commodity perps
    assert classify_asset("NATGAS") == "commodity"
    assert classify_asset("SILVER") == "commodity"


def test_classify_asset_hip3_namespaced(monkeypatch):
    """HIP-3 venues are mixed: unknown tokenized stocks default to equity (not
    the BTC-trend crypto default), but crypto names listed on a HIP-3 dex still
    resolve to crypto via the native-perp ticker set."""
    from pathiel.agents import hyperfeed as hf
    # Pretend the native HL dex lists these crypto majors.
    monkeypatch.setattr(hf, "_crypto_tickers_cache",
                        frozenset({"BTC", "ETH", "LINK", "FARTCOIN", "XMR"}))
    # Unknown tokenized stock (not in the allowlist) → equity, NOT crypto.
    assert hf.classify_asset("xyz:SNDK") == "equity"
    assert hf.classify_asset("xyz:CBRS") == "equity"
    # Known equity / commodity allowlist entries still win.
    assert hf.classify_asset("xyz:NVDA") == "equity"
    assert hf.classify_asset("xyz:GOLD") == "commodity"
    assert hf.classify_asset("km:USOIL") == "commodity"
    # Crypto names on a HIP-3 dex resolve to crypto via the native set.
    assert hf.classify_asset("hyna:BTC") == "crypto"
    assert hf.classify_asset("hyna:LINK") == "crypto"
    assert hf.classify_asset("cash:ETH") == "crypto"
    assert hf.classify_asset("flx:XMR") == "crypto"


def test_native_crypto_tickers_skips_namespaced_and_caches(monkeypatch):
    """_native_crypto_tickers pulls only main-dex perps (no ':') and caches."""
    from pathiel.agents import hyperfeed as hf
    hf._crypto_tickers_cache = None
    calls = {"n": 0}

    def fake_universe(**kw):
        calls["n"] += 1
        return [
            {"coin": "BTC", "type": "perp"},
            {"coin": "ETH", "type": "perp"},
            {"coin": "xyz:NVDA", "type": "perp"},  # namespaced → excluded
            {"coin": "@107", "type": "spot"},      # spot → excluded
        ]

    monkeypatch.setattr(hf, "get_universe", fake_universe)
    out = hf._native_crypto_tickers()
    assert out == frozenset({"BTC", "ETH"})
    hf._native_crypto_tickers()  # second call served from cache
    assert calls["n"] == 1


# ── market-data lookups ──────────────────────────────────────────────────

def test_hyperfeed_safe_float_default():
    from pathiel.agents.hyperfeed import _safe_float
    assert _safe_float("not-a-number", 7.0) == 7.0
    assert _safe_float(None) == 0.0
    assert _safe_float("3.5") == 3.5


def test_leaderboard_get_markets_ranks_by_volume(monkeypatch):
    from pathiel.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "SMALL", "type": "perp", "dayNtlVlm": 1e6, "openInterest": 1},
        {"coin": "BIG", "type": "perp", "dayNtlVlm": 1e9, "openInterest": 9},
        {"coin": "@SPOT", "type": "spot", "dayNtlVlm": 1e12},  # excluded
    ])
    out = hyperfeed.leaderboard_get_markets()["markets"]
    assert [m["asset"] for m in out] == ["BIG", "SMALL"]
    assert out[0]["rank"] == 1


def test_leaderboard_get_trader_positions_unwraps_and_coerces(monkeypatch):
    """Nested position unwrap, string-leverage coercion, szi==0 skip."""
    from pathiel.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "_http_post", lambda path, body: {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "2.0", "entryPx": "60000",
                          "leverage": "10", "unrealizedPnl": "500"}},
            {"position": {"coin": "ETH", "szi": "0"}},  # skipped
        ],
    })
    out = hyperfeed.leaderboard_get_trader_positions("0xABC")["positions"]
    assert len(out) == 1
    assert out[0]["side"] == "long"
    assert out[0]["leverage"] == {"value": "10"}  # str coerced to obj


def test_leaderboard_get_trader_positions_empty_when_no_state(monkeypatch):
    from pathiel.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "_http_post", lambda path, body: None)
    assert hyperfeed.leaderboard_get_trader_positions("0xABC") == {"positions": []}


def test_market_get_asset_data_collects_candles_and_context(monkeypatch):
    from pathiel.agents import hyperfeed
    fake_candles = [Candle(t=1, o=1, h=2, l=0.5, c=1.5, v=100)]
    monkeypatch.setattr(hyperfeed, "fetch_hl_candles",
                        lambda asset, interval, n: fake_candles)
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "BTC", "funding": -0.0001, "openInterest": 5e7,
         "prevDayPx": 59000, "midPx": 60000, "dayNtlVlm": 1e9},
    ])
    out = hyperfeed.market_get_asset_data("BTC", intervals=["1h"])["data"]
    assert out["asset"] == "BTC"
    assert len(out["candles"]["1h"]) == 1
    assert out["funding_rate"] == -0.0001
    assert out["mid_px"] == 60000


def test_market_get_asset_data_candle_error_yields_empty(monkeypatch):
    from pathiel.agents import hyperfeed

    def boom(asset, interval, n):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(hyperfeed, "fetch_hl_candles", boom)
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [])
    out = hyperfeed.market_get_asset_data("BTC", intervals=["5m"])["data"]
    assert out["candles"]["5m"] == []


def test_market_list_instruments_counts_and_strips(monkeypatch):
    from pathiel.agents import hyperfeed
    monkeypatch.setattr(hyperfeed, "get_universe", lambda: [
        {"coin": "BTC", "type": "perp", "maxLeverage": 40},
        {"coin": "@107", "type": "spot", "maxLeverage": 0},
    ])
    out = hyperfeed.market_list_instruments()
    assert out["counts"] == {"perps": 1, "spot": 1, "total": 2}
    symbols = {i["symbol"] for i in out["instruments"]}
    assert "107" in symbols  # @ stripped


def test_market_get_mids_passthrough(monkeypatch):
    from pathiel.agents import hyperfeed
    seen = {}

    def fake_mids(*, include_hip3=False):
        seen["include_hip3"] = include_hip3
        return {"BTC": "60000", "xyz:IBM": "280"}

    monkeypatch.setattr(hyperfeed, "fetch_all_mids", fake_mids)
    assert hyperfeed.market_get_mids() == {"BTC": "60000", "xyz:IBM": "280"}
    assert seen["include_hip3"] is True


def test_discovery_get_trader_state_win_rate_is_percentage(monkeypatch):
    """win_rate is a 0-100 percentage; positions unwrapped; ROI computed."""
    from pathiel.agents import hyperfeed
    calls = {"clearinghouse": {
        "marginSummary": {"accountValue": "8000", "totalNtlPos": "4000"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "1", "entryPx": "60000",
                          "unrealizedPnl": "100", "leverage": {"value": "5"}}},
        ],
    }}

    def fake_post(path, body):
        if body["type"] == "clearinghouseState":
            return calls["clearinghouse"]
        if body["type"] == "userFills":
            return [{"closedPnl": "5"}, {"closedPnl": "-2"}, {"closedPnl": "3"}]
        return {}

    monkeypatch.setattr(hyperfeed, "_http_post", fake_post)
    out = hyperfeed.discovery_get_trader_state(["0xABC"])["data"]["traders"]
    assert len(out) == 1
    t = out[0]
    assert t["total_trades"] == 3
    assert abs(t["win_rate"] - (2 / 3 * 100)) < 1e-6  # 2 of 3 winners
    assert t["open_positions"] == 1


# ── per-class funding regime ────────────────────────────────────────────

def test_funding_regime_cache_short_circuits_repeated_calls(monkeypatch):
    """The 5-min cache on market_get_funding_regime must avoid refetching the
    universe on every call."""
    from pathiel.agents import hyperfeed

    monkeypatch.setattr(hyperfeed, "_funding_regime_cache", None)

    calls = {"count": 0}

    def fake_compute():
        calls["count"] += 1
        return {"regime": "SHORT_CROWDED", "assets": []}

    monkeypatch.setattr(hyperfeed, "_compute_funding_regime", fake_compute)

    r1 = hyperfeed.market_get_funding_regime()
    r2 = hyperfeed.market_get_funding_regime()
    r3 = hyperfeed.market_get_funding_regime()
    assert r1["regime"] == "SHORT_CROWDED"
    assert r2["regime"] == "SHORT_CROWDED"
    assert r3["regime"] == "SHORT_CROWDED"
    assert calls["count"] == 1


def test_compute_funding_regime_includes_hip3(monkeypatch):
    """`_compute_funding_regime` must fetch the universe WITH HIP-3 so
    equity / commodity perps are visible. Regression guard for the bug
    where xyz:CL and xyz:ARM weren't in the regime scan at all."""
    from pathiel.agents import hyperfeed

    captured = {}

    def fake_get_universe(*, include_hip3=False, **kw):
        captured["include_hip3"] = include_hip3
        return [
            {"coin": "BTC",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e9},
            {"coin": "ETH",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 5e8},
            {"coin": "SOL",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 3e8},
            {"coin": "DOGE",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 2e8},
            {"coin": "AVAX",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "XRP",    "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "LINK",   "funding": -0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8},
            {"coin": "xyz:CL", "funding":  0.0002, "openInterest": 5e6, "dayNtlVlm": 1e7},
        ]

    monkeypatch.setattr(hyperfeed, "get_universe", fake_get_universe)
    out = hyperfeed._compute_funding_regime()
    assert captured["include_hip3"] is True
    assert out["regimes_by_class"]["crypto"] == "SHORT_CROWDED"
    assert out["regimes_by_class"]["commodity"] == "NEUTRAL"
    assert out["regime"] == "SHORT_CROWDED"


def test_compute_funding_regime_long_crowded_margin(monkeypatch):
    """A class needs a >5 long-over-short margin to be LONG_CROWDED."""
    from pathiel.agents import hyperfeed
    universe = [
        {"coin": c, "funding": 0.0002, "openInterest": 5e7, "dayNtlVlm": 1e8}
        for c in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "LINK")
    ]
    monkeypatch.setattr(hyperfeed, "get_universe",
                        lambda *, include_hip3=False, **k: universe)
    out = hyperfeed._compute_funding_regime()
    assert out["regimes_by_class"]["crypto"] == "LONG_CROWDED"
    assert out["regime"] == "LONG_CROWDED"
