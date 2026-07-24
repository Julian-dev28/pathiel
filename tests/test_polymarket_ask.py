"""Ad-hoc `ask` door: name a market, get a verdict. The rule under test is that
naming a market gets you the VERDICT but does not lower the bar for RECORDING —
a manual read must not widen the hypothesis the ledger is grading."""
from __future__ import annotations

import json
import time

import pytest

from services.polymarket_scout import ask, ledger, trending
from services.polymarket_scout.forecaster import StubForecaster
from services.polymarket_scout.run import SPORTS_LANE_CFG, TRENDING_CFG

NOW = int(time.time() * 1000)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))


def _row(**over):
    r = {"market_id": "1", "question": "Will the Fed hike 25bps in July?",
         "event_title": "Fed Decision in July?", "tags": ["economy"],
         "yes": 0.24, "ask": 0.26, "bid": 0.23, "yes_token": "y1", "no_token": "n1",
         "end_date": "2026-07-31T00:00:00Z", "volume_24h": 300_000.0,
         "change_24h": -0.02, "breaking": False, "live": False, "score": "",
         "sport": "", "url": "https://polymarket.com/event/fed"}
    r.update(over)
    return r


def _game(**over):
    g = {"market_id": "2", "question": "Set 2 Winner: Sherif vs Badosa",
         "event_title": "Hamburg European Open", "tags": ["wta", "tennis", "sports"],
         "sport": "wta", "live": True, "score": "7-6(7-4), 3-5",
         "yes": 0.11, "ask": 0.14}
    g.update(over)
    return _row(**g)


class FakeClient:
    def __init__(self, ask_px=(0.26, 100.0)):
        self._ask = ask_px
        self.calls = []

    def best_ask(self, token_id):
        self.calls.append(token_id)
        return self._ask


# ── selection ────────────────────────────────────────────────────────────────
def test_select_matches_question_or_event_title_case_insensitively():
    rows = [_row(), _game()]
    assert [r["market_id"] for r in ask.select(rows, ["sherif vs badosa"])] == ["2"]
    assert [r["market_id"] for r in ask.select(rows, ["fed decision"])] == ["1"]


def test_select_preserves_request_order_and_dedupes():
    rows = [_row(), _game()]
    got = ask.select(rows, ["Sherif", "Fed", "Sherif"])
    assert [r["market_id"] for r in got] == ["2", "1"]


def test_select_takes_exact_ids_first():
    rows = [_row(), _game()]
    assert [r["market_id"] for r in ask.select(rows, ["Fed"], ids=["2"])] == ["2", "1"]


def test_select_of_an_unknown_name_is_empty():
    assert ask.select([_row()], ["nothing like this"]) == []


# ── lane routing ─────────────────────────────────────────────────────────────
def test_a_sports_market_reached_by_name_still_grades_in_the_sports_lane():
    assert ask.lane_of(_game()) == "sports"
    assert ask.lane_of(_row()) == "trending"
    assert ask.cfg_for(_game()) is SPORTS_LANE_CFG
    assert ask.cfg_for(_row()) is TRENDING_CFG


def test_sports_lane_detection_survives_a_missing_sport_field():
    assert ask.lane_of(_row(tags=["esports", "cs2"], sport="")) == "sports"


# ── the bar does not move ────────────────────────────────────────────────────
def test_sub_threshold_read_is_reported_but_never_recorded():
    """The whole point: you asked, so you get the number — but a 5pp gap does
    not become a paper trade just because a human typed the market's name."""
    client = FakeClient()
    v = ask.ask(client, StubForecaster(lambda q, d: (0.29, "close to fair")),
                needles=["Fed"], rows=[_row()], printer=lambda s: None)[0]
    assert v["llm_yes"] == 0.29 and v["edge"] == pytest.approx(0.05)
    assert v["side"] is None and v["recorded"] is False
    assert "threshold" in v["skip_reason"]
    assert ledger.load() == []
    assert client.calls == []              # no book call without a decision


def test_clearing_read_is_recorded_at_the_touch():
    client = FakeClient(ask_px=(0.27, 50.0))
    v = ask.ask(client, StubForecaster(lambda q, d: (0.60, "hawkish surprise")),
                needles=["Fed"], rows=[_row()], printer=lambda s: None)[0]
    assert v["side"] == "YES" and v["recorded"] is True
    assert v["fill_px"] == 0.27            # the ask, not the 0.24 mid
    row = ledger.load()[0]
    assert row["lane"] == "trending" and row["meta"]["asked"] is True


def test_sports_read_needs_the_wider_sports_threshold():
    """Same 17pp gap: clears the trending bar, misses the sports bar."""
    trending_v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.41, "x")),
                         needles=["Fed"], rows=[_row()], printer=lambda s: None)[0]
    sports_v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.28, "x")),
                       needles=["Sherif"], rows=[_game()], printer=lambda s: None)[0]
    assert trending_v["recorded"] is True                 # 17pp > 15pp
    assert sports_v["recorded"] is False and sports_v["side"] is None  # 17pp < 20pp


def test_a_sports_read_that_clears_20pp_records_in_the_sports_lane():
    game = _game(question="Sherif vs Badosa: match winner", yes=0.42, ask=0.45)
    v = ask.ask(FakeClient(ask_px=(0.59, 5.0)),
                StubForecaster(lambda q, d: (0.05, "she is a double break down")),
                needles=["Sherif"], rows=[game], printer=lambda s: None)[0]
    assert v["side"] == "NO" and v["recorded"] is True
    assert ledger.load()[0]["lane"] == "sports"
    assert ledger.load()[0]["meta"]["live"] is True


def test_a_deep_longshot_can_never_produce_a_no_side_signal():
    """Structural, not a threshold choice: an 11c market has 11pp of room below
    it, so |edge| on the NO side is capped at 0.11 and can never clear a 15-20pp
    bar. The crazy-odds board can only ever say BUY YES or nothing."""
    v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.001, "no chance")),
                needles=["Sherif"], rows=[_game(yes=0.11)], printer=lambda s: None)[0]
    assert abs(v["edge"]) <= 0.11
    assert v["side"] is None and v["recorded"] is False


def test_no_record_flag_reports_without_writing():
    v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.90, "y")),
                needles=["Fed"], rows=[_row()], record=False,
                printer=lambda s: None)[0]
    assert v["side"] == "YES" and v["recorded"] is False
    assert v["skip_reason"] == "--no-record"
    assert ledger.load() == []


def test_an_already_judged_market_is_not_read_twice_into_the_ledger():
    ledger.record(market_id="1", question="q", side="YES", token_id="y1",
                  llm_yes=0.6, mkt_yes=0.24, fill_px=0.26, edge=0.36,
                  end_date="", lane="trending")
    v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.90, "y")),
                needles=["Fed"], rows=[_row()], printer=lambda s: None)[0]
    assert v["recorded"] is False and "already in the ledger" in v["skip_reason"]
    assert len(ledger.load()) == 1


def test_a_declining_brain_is_reported_not_swallowed():
    v = ask.ask(FakeClient(), StubForecaster(lambda q, d: None),
                needles=["Fed"], rows=[_row()], printer=lambda s: None)[0]
    assert v["llm_yes"] is None and v["recorded"] is False
    assert "declined" in v["skip_reason"]


def test_verdicts_are_returned_for_every_named_market_even_the_no_bets():
    vs = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.25, "fair")),
                 needles=["Fed", "Sherif"], rows=[_row(), _game()],
                 printer=lambda s: None)
    assert len(vs) == 2
    assert all(v["llm_yes"] == 0.25 for v in vs)
    assert [v["lane"] for v in vs] == ["trending", "sports"]


def test_verdict_carries_the_payout_multiple_for_the_longshots():
    v = ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.5, "x")),
                needles=["Sherif"], rows=[_game()], printer=lambda s: None)[0]
    assert v["payout_x"] == pytest.approx(trending.payout_x(_game()))


def test_no_match_returns_nothing_and_says_so():
    said = []
    assert ask.ask(FakeClient(), StubForecaster(lambda q, d: (0.9, "x")),
                   needles=["nope"], rows=[_row()], printer=said.append) == []
    assert "no market matched" in said[0]
