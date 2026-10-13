"""Gate tests for the Hyperliquid leaderboard behind the MCP leaderboard/discovery tools.

These tools returned an empty list for their whole lives: they read a
hard-coded empty TRUSTED_WALLETS set on the belief that HL's leaderboard is not
public. It is (stats-data.hyperliquid.xyz). No network here: the fetch and the
per-trader position lookup are stubbed.
"""
from __future__ import annotations

import pytest

from pathiel.agents import hyperfeed as hf
from pathiel.client import hl_client

_REAL_FETCH = hf._fetch_leaderboard_rows


def _row(addr, value, day_pnl, month_roi):
    return {"address": addr, "display_name": None, "account_value": value,
            "windows": {"day": {"pnl": day_pnl, "roi": 0.0, "vlm": 1.0},
                        "month": {"pnl": 0.0, "roi": month_roi, "vlm": 1.0}}}


ROWS = [
    _row("0xdust", 50.0, 1.0, 9.0),          # +900% on $50
    _row("0xwhale", 5_000_000.0, 900.0, 0.3),
    _row("0xflat", 2_000_000.0, 800.0, 0.5),  # no open positions
    _row("0xmid", 50_000.0, 100.0, 0.8),
]
POSITIONS = {"0xwhale": [{"coin": "BTC"}], "0xflat": [], "0xmid": [{"coin": "ETH"}],
             "0xdust": [{"coin": "DOGE"}]}


@pytest.fixture(autouse=True)
def board(monkeypatch):
    monkeypatch.setattr(hf, "_leaderboard_cache", None)
    calls = {"fetch": 0}

    def fetch():
        calls["fetch"] += 1
        return list(ROWS)
    monkeypatch.setattr(hf, "_fetch_leaderboard_rows", fetch)
    monkeypatch.setattr(hf, "_open_positions", lambda a: POSITIONS.get(a))
    return calls


def test_ranks_by_window_pnl_and_skips_flat_accounts():
    out = hf.leaderboard_get_top("DAILY", "PROFIT_AND_LOSS_UNREALIZED", limit=3)
    assert [t["address"] for t in out["traders"]] == ["0xwhale", "0xmid", "0xdust"]
    assert out["traders"][0]["rank"] == 1 and out["traders"][0]["pnl_usd"] == 900.0


def test_open_position_filter_off_keeps_flat_accounts():
    out = hf.leaderboard_get_top("DAILY", limit=2, open_position_filter=False)
    assert [t["address"] for t in out["traders"]] == ["0xwhale", "0xflat"]


def test_roi_discovery_floors_dust_accounts_by_default():
    """Without the floor, the top of an ROI board is a $50 account that doubled."""
    traders = hf.discovery_get_top_traders("MONTHLY", "RETURN_ON_INVESTMENT", limit=5)["data"]["traders"]
    assert [t["address"] for t in traders] == ["0xmid", "0xwhale"]
    assert traders[0]["roi_pct"] == pytest.approx(80.0)
    raw = hf.discovery_get_top_traders("MONTHLY", "RETURN_ON_INVESTMENT", limit=5,
                                       min_account_value=0)["data"]["traders"]
    assert raw[0]["address"] == "0xdust"


@pytest.mark.parametrize("tf,sb", [("YEARLY", "RETURN_ON_INVESTMENT"), ("DAILY", "SHARPE")])
def test_unknown_window_or_metric_raises(tf, sb):
    """Falling back to a default would rank by something the caller did not ask for."""
    with pytest.raises(ValueError):
        hf.leaderboard_get_top(tf, sb)


def test_limit_is_clamped():
    assert len(hf.leaderboard_get_top("DAILY", limit=10_000, open_position_filter=False)["traders"]) == 4


def test_failed_position_lookup_counts_as_flat(monkeypatch):
    monkeypatch.setattr(hf, "_open_positions", lambda a: None)
    assert hf.leaderboard_get_top("DAILY")["traders"] == []


def test_cache_serves_repeat_calls(board):
    hf.leaderboard_get_top("DAILY")
    hf.discovery_get_top_traders()
    assert board["fetch"] == 1


def test_failed_refresh_serves_stale_copy(monkeypatch):
    monkeypatch.setattr(hf, "_leaderboard_cache", (list(ROWS), 0.0))  # expired

    def down():
        raise RuntimeError("503")
    monkeypatch.setattr(hf, "_fetch_leaderboard_rows", down)
    assert hf.leaderboard_get_top("DAILY")["traders"]


def test_failed_first_fetch_raises(monkeypatch):
    def down():
        raise RuntimeError("503")
    monkeypatch.setattr(hf, "_fetch_leaderboard_rows", down)
    with pytest.raises(RuntimeError):
        hf.leaderboard_get_top("DAILY")


def test_fetch_normalizes_rows(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"leaderboardRows": [
                {"ethAddress": "0xABC", "accountValue": "12.5", "displayName": "x",
                 "windowPerformances": [["day", {"pnl": "1.5", "roi": "0.1", "vlm": "9"}], "junk"]},
                {"ethAddress": "", "accountValue": "1"},
            ]}
    monkeypatch.setattr(hf.requests, "get", lambda url, timeout: Resp())
    rows = _REAL_FETCH()
    assert rows == [{"address": "0xabc", "display_name": "x", "account_value": 12.5,
                     "windows": {"day": {"pnl": 1.5, "roi": 0.1, "vlm": 9.0}}}]


def test_trader_state_pnl_is_unrealized_not_equity(monkeypatch):
    """pnl_usd used to be account_value: a $1M account showed $1M of profit."""
    def post(path, payload):
        if payload["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "1000", "totalNtlPos": "500"},
                    "assetPositions": [{"position": {"coin": "BTC", "szi": "1",
                                                     "entryPx": "1", "unrealizedPnl": "100",
                                                     "leverage": {"value": 2}}}]}
        return []
    monkeypatch.setattr(hf, "_http_post", post)
    t = hf.discovery_get_trader_state(["0x1"])["data"]["traders"][0]
    assert t["pnl_usd"] == 100.0
    assert t["roi_pct"] == pytest.approx(100 / 900 * 100)


class TestStrictPost:
    def test_strict_raises_on_rate_budget(self, monkeypatch):
        monkeypatch.setattr(hl_client._HL_LIMITER, "acquire", lambda w, max_wait: False)
        with pytest.raises(hl_client.HLRequestError, match="rate budget"):
            hl_client._http_post_strict("/info", {"type": "meta"})

    def test_lenient_still_returns_none(self, monkeypatch):
        monkeypatch.setattr(hl_client._HL_LIMITER, "acquire", lambda w, max_wait: False)
        assert hl_client._http_post("/info", {"type": "meta"}) is None

    def test_strict_returns_json_null(self, monkeypatch):
        """A venue null (unknown vault) is an answer, not a failure."""
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return None

        class Sess:
            def post(self, *a, **k):
                return Resp()
        monkeypatch.setattr(hl_client._HL_LIMITER, "acquire", lambda w, max_wait: True)
        monkeypatch.setattr(hl_client, "_get_session", lambda: Sess())
        assert hl_client._http_post_strict("/info", {"type": "vaultDetails"}) is None
