"""Trending/BREAKING lane — flattening, the latency/ladder rejects, the
tradeability gate, and the forecast queue's breaking-first ordering. No network:
a fake client returns canned Gamma event payloads."""
from __future__ import annotations

import json
import time

import pytest

from services.polymarket_scout import ledger, trending
from services.polymarket_scout.forecaster import StubForecaster
from services.polymarket_scout.run import TRENDING_CFG, scan_trending

NOW = int(time.time() * 1000)
HOUR_MS = 3_600_000


def _iso(hours_from_now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime((NOW + int(hours_from_now * HOUR_MS)) / 1000))


def _market(**over):
    m = {"id": "1", "question": "Will the ceasefire hold through August?",
         "clobTokenIds": json.dumps(["yes_tok", "no_tok"]),
         "outcomePrices": json.dumps(["0.42", "0.58"]),
         "endDate": _iso(72), "liquidity": "8000", "volume24hr": 50_000.0,
         "oneDayPriceChange": 0.02, "bestBid": 0.41, "bestAsk": 0.43, "spread": 0.02,
         "enableOrderBook": True, "active": True, "closed": False, "icon": "i.png"}
    m.update(over)
    return m


def _event(markets=None, **over):
    e = {"id": "e1", "title": "Israel x Iran ceasefire", "slug": "israel-iran",
         "icon": "e.png", "volume24hr": 90_000.0,
         "tags": [{"slug": "geopolitics"}, {"slug": "iran"}],
         "markets": markets if markets is not None else [_market()]}
    e.update(over)
    return e


class FakeClient:
    """Stands in for PolymarketClient: canned events, a fixed touch."""

    def __init__(self, events, ask=(0.43, 100.0)):
        self._events = events
        self._ask = ask
        self.ask_calls = []

    def open_events(self, limit=100, pages=3, exclude_tag_ids=()):
        self.exclude = exclude_tag_ids
        return self._events

    def best_ask(self, token_id):
        self.ask_calls.append(token_id)
        return self._ask


# ── normalisation ────────────────────────────────────────────────────────────
def test_flatten_event_carries_event_context_onto_every_market():
    rows = trending.flatten_event(_event(markets=[_market(id="1"), _market(id="2")]))
    assert [r["market_id"] for r in rows] == ["1", "2"]
    r = rows[0]
    assert r["event_title"] == "Israel x Iran ceasefire"
    assert r["tags"] == ["geopolitics", "iran"]
    assert r["url"] == "https://polymarket.com/event/israel-iran"
    assert r["yes"] == 0.42
    assert r["yes_token"] == "yes_tok" and r["no_token"] == "no_tok"


def test_flatten_event_tolerates_junk():
    assert trending.flatten_event(None) == []
    assert trending.flatten_event({"markets": ["not-a-dict"]}) == []


def test_flatten_market_without_two_tokens_has_empty_token_ids():
    rows = trending.flatten_event(_event(markets=[_market(clobTokenIds=json.dumps(["only"]))]))
    assert rows[0]["yes_token"] == "" and rows[0]["no_token"] == ""


# ── the rejects ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("question,event_title", [
    ("Bitcoin Up or Down - 5PM ET", ""),
    ("S&P 500 (SPX) Opens Up or Down on July 24?", ""),
    ("Ethereum higher or lower today", ""),
    ("Will WTI Crude Oil (WTI) hit (HIGH) $95 in July?", "What will WTI hit in July 2026?"),
    ("Bitcoin above $120,000 on July 24?", "What price will Bitcoin hit in July?"),
    ("Will Elon Musk post 40-64 tweets from July 23 to July 25, 2026?",
     "Elon Musk # tweets July 17 - July 24, 2026?"),
])
def test_ladder_and_updown_markets_are_rejected(question, event_title):
    assert trending.is_ladder_row({"question": question, "event_title": event_title}) is True


def test_judgment_market_is_not_a_ladder():
    assert trending.is_ladder_row(
        {"question": "Will Trump meet with Netanyahu by July 31, 2026?",
         "event_title": "Trump x Netanyahu"}) is False


@pytest.mark.parametrize("over,why", [
    ({"enableOrderBook": False}, "no book"),
    ({"closed": True}, "closed"),
    ({"active": False}, "inactive"),
    ({"volume24hr": 100.0}, "not traded today"),
    ({"liquidity": "10"}, "no resting size"),
    ({"endDate": _iso(2)}, "settles in 2h — a settlement race"),
    ({"endDate": _iso(24 * 400)}, "settles past the horizon cap"),
    ({"endDate": "garbage"}, "unparseable end date"),
    ({"outcomePrices": json.dumps(["0.995", "0.005"])}, "already settled in price"),
    ({"spread": 0.25}, "spread eats the edge"),
    ({"clobTokenIds": json.dumps(["one"])}, "not a binary book"),
])
def test_untradeable_rows_are_filtered(over, why):
    row = trending.flatten_event(_event(markets=[_market(**over)]))[0]
    assert trending.is_tradeable(row, NOW) is False, why


def test_tradeable_row_passes():
    row = trending.flatten_event(_event())[0]
    assert trending.is_tradeable(row, NOW) is True


# ── breaking ─────────────────────────────────────────────────────────────────
def test_breaking_needs_both_a_move_and_volume():
    big_move_thin = trending.flatten_event(
        _event(markets=[_market(oneDayPriceChange=-0.30, volume24hr=1_000.0)]))[0]
    big_move_thick = trending.flatten_event(
        _event(markets=[_market(oneDayPriceChange=-0.30, volume24hr=90_000.0)]))[0]
    quiet_thick = trending.flatten_event(
        _event(markets=[_market(oneDayPriceChange=0.01, volume24hr=90_000.0)]))[0]
    assert trending.is_breaking(big_move_thin) is False
    assert trending.is_breaking(big_move_thick) is True
    assert trending.is_breaking(quiet_thick) is False


def test_breaking_score_is_absolute_but_direction_is_kept():
    row = trending.flatten_event(_event(markets=[_market(oneDayPriceChange=-0.22)]))[0]
    assert trending.breaking_score(row) == pytest.approx(0.22)
    assert row["change_24h"] == pytest.approx(-0.22)


def test_collect_dedupes_annotates_and_ranks():
    dup = _market(id="9", volume24hr=10_000.0)
    hot = _market(id="7", volume24hr=500_000.0, oneDayPriceChange=0.4)
    client = FakeClient([_event(markets=[dup, hot]), _event(markets=[dup])])
    rows = trending.collect(client, now_ms=NOW)
    assert [r["market_id"] for r in rows] == ["9", "7"]      # deduped
    assert all(r["hours_to_end"] == pytest.approx(72, abs=0.1) for r in rows)
    assert trending.rank_trending(rows)[0]["market_id"] == "7"
    assert [r["market_id"] for r in trending.rank_breaking(rows)] == ["7"]
    assert client.exclude == trending.DEFAULT_EXCLUDE_TAGS   # sports excluded server-side


def test_forecast_queue_puts_breaking_first_and_honours_skips():
    # separate events, separate tags — otherwise the decorrelation pass collapses
    # them to one, which is its own test below
    hot = _market(id="7", volume24hr=500_000.0, oneDayPriceChange=0.4)
    calm = _market(id="8", volume24hr=900_000.0, oneDayPriceChange=0.0)
    rows = trending.collect(FakeClient([
        _event(id="e1", markets=[hot]),
        _event(id="e2", slug="fed", tags=[{"slug": "economy"}], markets=[calm]),
    ]), now_ms=NOW)
    q = trending.forecast_queue(rows, limit=5)
    assert [r["market_id"] for r in q] == ["7", "8"]         # breaking outranks volume
    assert [r["market_id"] for r in trending.forecast_queue(rows, skip_ids={"7"})] == ["8"]


# ── the scan ─────────────────────────────────────────────────────────────────
def test_scan_trending_records_divergence_at_the_touch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    client = FakeClient([_event(markets=[_market(id="42", volume24hr=500_000.0,
                                                 oneDayPriceChange=0.4)])], ask=(0.47, 50.0))
    fc = StubForecaster(lambda q, d: (0.85, "strong evidence"))   # market 0.42 -> +43pp
    rec = scan_trending(client, fc, limit=3, skip_ids=set())
    assert len(rec) == 1
    row = rec[0]
    assert row["side"] == "YES"
    assert row["fill_px"] == 0.47                 # the ask, never the 0.42 mid
    assert row["lane"] == "trending"
    assert row["meta"]["breaking"] is True
    assert row["meta"]["url"].endswith("/event/israel-iran")
    assert client.ask_calls == ["yes_tok"]
    assert ledger.load()[0]["market_id"] == "42"


def test_scan_trending_takes_the_no_token_when_the_edge_is_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    client = FakeClient([_event(markets=[_market(id="43")])], ask=(0.60, 10.0))
    rec = scan_trending(client, StubForecaster(lambda q, d: (0.10, "no")), skip_ids=set())
    assert rec[0]["side"] == "NO" and client.ask_calls == ["no_tok"]


def test_scan_trending_ignores_small_divergence(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    client = FakeClient([_event()])
    # market 0.42, llm 0.50 -> 8pp, under the trending lane's 15pp threshold
    assert scan_trending(client, StubForecaster(lambda q, d: (0.50, "meh")), skip_ids=set()) == []
    assert client.ask_calls == []                 # no book call without a decision


def test_scan_trending_falls_back_to_the_quote_when_the_book_call_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    client = FakeClient([_event(markets=[_market(id="44", bestAsk=0.49)])], ask=None)
    rec = scan_trending(client, StubForecaster(lambda q, d: (0.90, "yes")), skip_ids=set())
    assert rec[0]["fill_px"] == 0.49


def test_scan_trending_skips_a_declining_forecaster(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    assert scan_trending(FakeClient([_event()]), StubForecaster(lambda q, d: None),
                         skip_ids=set()) == []


def test_trending_cfg_demands_a_wider_edge_than_the_judgment_lane():
    from services.polymarket_scout.run import CFG
    assert TRENDING_CFG["edge_threshold"] > CFG["edge_threshold"]


# ── sports lane ──────────────────────────────────────────────────────────────
class FakeSportsClient(FakeClient):
    """Mirrors the real client's split: judgment feeds exclude tag 1, the sports
    feed selects it. Records which way it was asked."""

    def open_events(self, limit=100, pages=3, exclude_tag_ids=(), tag_ids=()):
        self.exclude, self.tag_ids = exclude_tag_ids, tag_ids
        return self._events


def _game(**over):
    m = _market(id="g1", question="Set 2 Winner: Sherif vs Badosa",
                endDate=_iso(3), volume24hr=780_000.0)   # settles in 3 hours
    m.update(over)
    return {"id": "s1", "title": "Hamburg Open: Sherif vs Badosa", "slug": "hamburg",
            "icon": "s.png", "live": True, "score": "7-6(7-4), 2-4",
            "startTime": "2026-07-24T12:00:00Z",
            "sport": {"sport": "tennis"},
            "teams": [{"name": "Sherif"}, {"name": "Badosa"}],
            "tags": [{"slug": "tennis"}, {"slug": "sports"}],
            "markets": [m]}


def test_sports_rows_carry_live_score_and_sport():
    row = trending.flatten_event(_game())[0]
    assert row["live"] is True
    assert row["score"] == "7-6(7-4), 2-4"
    assert row["sport"] == "tennis" and row["teams"] == ["Sherif", "Badosa"]


def test_non_sports_rows_still_have_the_sports_fields_defaulted():
    row = trending.flatten_event(_event())[0]
    assert row["live"] is False and row["score"] == "" and row["sport"] == ""


def test_sports_cfg_drops_the_settlement_race_floor():
    """A game line settles in hours — the judgment lanes' 6h floor would delete
    the entire in-play board, so SPORTS_CFG must not apply it."""
    row = trending.flatten_event(_game())[0]
    assert trending.is_tradeable(row, NOW) is False                      # 3h < 6h floor
    assert trending.is_tradeable(row, NOW, trending.SPORTS_CFG) is True


def test_collect_sports_selects_the_sports_tag_not_excludes_it():
    client = FakeSportsClient([_game()])
    rows = trending.collect_sports(client, now_ms=NOW)
    assert client.tag_ids == (trending.SPORTS_TAG_ID,)
    assert client.exclude == ()
    assert len(rows) == 1 and rows[0]["live"] is True


def test_rank_sports_puts_live_games_first():
    live = trending.flatten_event(_game())[0]
    later = trending.flatten_event(
        {**_game(), "live": False,
         "markets": [_market(id="g2", endDate=_iso(5), volume24hr=9_000_000.0)]})[0]
    ranked = trending.rank_sports([later, live])
    assert [r["market_id"] for r in ranked] == ["g1", "g2"]   # live beats volume


# ── crazy odds / longshots ───────────────────────────────────────────────────
def test_payout_multiple_uses_the_ask_not_the_mid():
    row = trending.flatten_event(_event(markets=[_market(outcomePrices=json.dumps(["0.25", "0.75"]),
                                                         bestAsk=0.33)]))[0]
    assert trending.payout_x(row) == 3.03            # 1/0.33, not 1/0.25 = 4.0


def test_payout_multiple_falls_back_to_the_price_without_a_quote():
    assert trending.payout_x({"ask": 0, "yes": 0.20}) == 5.0
    assert trending.payout_x({"ask": 0, "yes": None}) is None


def test_longshots_keeps_only_3x_and_up():
    rows = [trending.flatten_event(_event(markets=[_market(
        id=str(i), outcomePrices=json.dumps([f"{p:.2f}", f"{1 - p:.2f}"]),
        bestAsk=p + 0.01)]))[0] for i, p in enumerate((0.05, 0.33, 0.34, 0.60))]
    got = trending.longshots(rows)
    assert [r["yes"] for r in got] == [0.05, 0.33]    # 0.34 is under 3x, excluded
    assert all(r["payout_x"] >= 2.9 for r in got)


def test_longshots_rank_by_our_disagreement_before_volume():
    thin_but_judged = trending.flatten_event(_event(markets=[_market(
        id="judged", outcomePrices=json.dumps(["0.10", "0.90"]), volume24hr=6_000.0)]))[0]
    thin_but_judged["live_edge"] = 0.30
    fat_unjudged = trending.flatten_event(_event(markets=[_market(
        id="fat", outcomePrices=json.dumps(["0.10", "0.90"]), volume24hr=900_000.0)]))[0]
    got = trending.longshots([fat_unjudged, thin_but_judged])
    assert [r["market_id"] for r in got] == ["judged", "fat"]


def test_longshot_threshold_is_the_3x_line():
    assert trending.LONGSHOT_MAX_PROB == pytest.approx(0.33)
    assert 1 / trending.LONGSHOT_MAX_PROB >= 3.0


def test_scan_trending_labels_the_sports_lane_and_keeps_live_in_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    from services.polymarket_scout.run import SPORTS_LANE_CFG
    client = FakeSportsClient([_game()], ask=(0.30, 20.0))
    rows = trending.collect_sports(client, now_ms=NOW, cfg=SPORTS_LANE_CFG)
    rec = scan_trending(client, StubForecaster(lambda q, d: (0.05, "she is down a set")),
                        cfg=SPORTS_LANE_CFG, rows=rows, skip_ids=set(), lane="sports")
    assert rec[0]["lane"] == "sports"
    assert rec[0]["side"] == "NO"                    # market 0.42 vs llm 0.05
    assert rec[0]["meta"]["live"] is True and rec[0]["meta"]["sport"] == "tennis"


def test_sports_lane_demands_the_widest_edge_of_all():
    from services.polymarket_scout.run import SPORTS_LANE_CFG
    assert SPORTS_LANE_CFG["edge_threshold"] > TRENDING_CFG["edge_threshold"]


def test_every_lane_name_is_registered_for_grading():
    assert set(ledger.LANES) == {"judgment", "trending", "sports"}


# ── decorrelation ────────────────────────────────────────────────────────────
def _tagged(mid, event_id, tag, vol=100_000.0):
    return {"market_id": mid, "event_id": event_id, "event_title": f"E{event_id}",
            "tags": [tag], "volume_24h": vol, "yes": 0.4, "change_24h": 0.0,
            "breaking": False}


def test_diversify_keeps_one_market_per_event():
    """Measured on the first 15 live reads: two rows were the SAME Israel-Iran
    ceasefire event priced days apart. That is one bet the gate counts as two."""
    rows = [_tagged("1", "e1", "iran"), _tagged("2", "e1", "iran"),
            _tagged("3", "e2", "economy")]
    assert [r["market_id"] for r in trending.diversify(rows, limit=10)] == ["1", "3"]


def test_diversify_caps_a_single_theme():
    rows = [_tagged(str(i), f"e{i}", "iran") for i in range(5)]
    rows.append(_tagged("x", "ex", "economy"))
    got = trending.diversify(rows, limit=10, max_per_tag=2)
    assert [r["market_id"] for r in got] == ["0", "1", "x"]


def test_diversify_preserves_rank_order():
    rows = [_tagged("hi", "e1", "a"), _tagged("mid", "e2", "b"), _tagged("lo", "e3", "c")]
    assert [r["market_id"] for r in trending.diversify(rows, limit=3)] == ["hi", "mid", "lo"]


def test_diversify_respects_the_limit():
    rows = [_tagged(str(i), f"e{i}", f"t{i}") for i in range(20)]
    assert len(trending.diversify(rows, limit=4)) == 4


def test_diversify_tolerates_untagged_rows():
    rows = [_tagged("1", "e1", ""), _tagged("2", "e2", "")]
    assert len(trending.diversify(rows, limit=5, max_per_tag=1)) == 2


def test_forecast_queue_is_diversified():
    hot = _market(id="a", volume24hr=900_000.0, oneDayPriceChange=0.4)
    hot2 = _market(id="b", volume24hr=800_000.0, oneDayPriceChange=0.4)
    ev = _event(markets=[hot, hot2])            # same event, two breaking rows
    rows = trending.collect(FakeClient([ev]), now_ms=NOW)
    assert len(rows) == 2
    assert len(trending.forecast_queue(rows, limit=5)) == 1   # one per event
