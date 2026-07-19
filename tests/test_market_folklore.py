import datetime as dt
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "research" / "market_folklore" / "run.py"
SPEC = spec_from_file_location("market_folklore", MODULE_PATH)
assert SPEC and SPEC.loader
market_folklore = module_from_spec(SPEC)
sys.modules[SPEC.name] = market_folklore
SPEC.loader.exec_module(market_folklore)


def make_payload(rows: list[tuple[dt.date, float, float]]) -> dict:
    stamps = [
        int(dt.datetime.combine(day, dt.time(14, 30), tzinfo=dt.timezone.utc).timestamp())
        for day, _, _ in rows
    ]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [opening for _, opening, _ in rows],
                                "close": [closing for _, _, closing in rows],
                            }
                        ],
                        "adjclose": [{"adjclose": [closing for _, _, closing in rows]}],
                    },
                }
            ]
        }
    }


def make_bars(closes: list[float]) -> list:
    start = dt.date(2020, 1, 1)
    return [
        market_folklore.Bar(start + dt.timedelta(days=index), value, value, value, 0.0)
        for index, value in enumerate(closes)
    ]


# --- Core folklore math -----------------------------------------------------


def test_digital_root_and_name_root_are_stable():
    assert market_folklore.digital_root(20260719) == 9
    assert market_folklore.digital_root(18) == 9
    assert market_folklore.digital_root(0) == 0
    assert market_folklore.digital_root(9) == 9
    assert market_folklore.digital_root(10) == 1
    assert market_folklore.pythagorean_root("Apple") == 5


def test_chinese_zodiac_cycles_every_twelve_years():
    assert market_folklore.chinese_zodiac(1976) == "Dragon"
    assert market_folklore.chinese_zodiac(1988) == "Dragon"


def test_solar_cardinal_window_covers_equinox_but_not_midseason():
    assert market_folklore.cardinal_window(market_folklore.dt.date(2026, 3, 21))
    assert not market_folklore.cardinal_window(market_folklore.dt.date(2026, 5, 15))


def test_solar_longitude_hits_cardinal_points_on_2026_ingress_dates():
    def angular_distance(value: float, target: float) -> float:
        difference = abs(value - target) % 360
        return min(difference, 360 - difference)

    cases = [
        (dt.date(2026, 3, 20), 0.0),
        (dt.date(2026, 6, 21), 90.0),
        (dt.date(2026, 9, 23), 180.0),
        (dt.date(2026, 12, 21), 270.0),
    ]
    for day, target in cases:
        longitude = market_folklore.solar_longitude(day)
        assert angular_distance(longitude, target) < 1.5, (day, longitude, target)


def test_normalized_path_rebases_to_zero():
    bars = make_bars([100.0, 110.0, 121.0, 133.1])
    path = market_folklore.normalized_path(bars, 0, 3)
    assert path[0] == 0.0
    assert abs(path[1] - 0.10) < 1e-12
    assert abs(path[2] - 0.21) < 1e-12
    shifted = market_folklore.normalized_path(bars, 1, 3)
    assert shifted[0] == 0.0
    assert abs(shifted[1] - 0.10) < 1e-12
    assert abs(market_folklore.correlation(path, path) - 1.0) < 1e-12


def test_select_non_overlapping_spaces_windows_by_full_window():
    candidates = [
        {"end_index": 100},
        {"end_index": 120},
        {"end_index": 200},
        {"end_index": 400},
    ]
    kept = market_folklore.select_non_overlapping(candidates, window=63)
    assert [row["end_index"] for row in kept] == [100, 200, 400]
    limited = market_folklore.select_non_overlapping(candidates, window=63, limit=2)
    assert [row["end_index"] for row in limited] == [100, 200]


def test_rule_verdict_is_blunt():
    assert market_folklore.rule_verdict(5.0, 2.0, 0.01, 4.0, 6.0) == "SUPPORTED"
    assert market_folklore.rule_verdict(1.0, 2.0, 0.01, 4.0, 6.0) == "REFUTED"
    assert market_folklore.rule_verdict(5.0, 2.0, 0.70, 4.0, 6.0) == "REFUTED"
    assert market_folklore.rule_verdict(5.0, 2.0, 0.20, 4.0, 6.0) == "INCONCLUSIVE"
    assert market_folklore.rule_verdict(5.0, 2.0, 0.01, -1.0, 6.0) == "INCONCLUSIVE"


def test_permutation_pvalue_is_seeded_and_sane():
    returns = [0.01, -0.02, 0.03, 0.0, 0.015, -0.005, 0.02, -0.01]
    first = market_folklore.permutation_pvalue(returns, 3, 99.0, trials=200, seed=7)
    second = market_folklore.permutation_pvalue(returns, 3, 99.0, trials=200, seed=7)
    assert first == second == 1 / 201
    assert market_folklore.permutation_pvalue(returns, 3, -99.0, trials=200, seed=7) == 1.0


def test_founder_mapping_is_declared_and_stable():
    companies = [market_folklore.Company(*row) for row in market_folklore.COMPANIES]
    assert len(companies) == 12
    by_symbol = {company.symbol: company for company in companies}
    for company in companies:
        assert company.founder, company.symbol
        assert 1900 < company.founder_birth_year < 2010, company.symbol

    jobs = by_symbol["AAPL"]
    assert (jobs.founder, jobs.founder_birth_year) == ("Steve Jobs", 1955)
    assert jobs.founder_zodiac == "Goat" and jobs.founder_trine == 3
    assert jobs.founder_birth_root == 2  # 1+9+5+5 = 20 -> 2

    bosack = by_symbol["CSCO"]  # eldest-cofounder clause; 1952 per Wikipedia, not 1951
    assert (bosack.founder, bosack.founder_birth_year) == ("Leonard Bosack", 1952)
    assert bosack.founder_zodiac == "Dragon" and bosack.founder_trine == 0
    assert bosack.founder_birth_root == 8

    tesla = by_symbol["TSLA"]  # founding CEO, not Musk (joined 2004)
    assert (tesla.founder, tesla.founder_birth_year) == ("Martin Eberhard", 1960)

    noyce = by_symbol["INTC"]
    assert noyce.founder_zodiac == "Rabbit" and noyce.founder_birth_root == 1


def test_company_portfolio_birth_modes_select_matching_years():
    def company(symbol: str) -> market_folklore.Company:
        return market_folklore.Company(symbol, symbol.title(), 2000, "Founder", 1960)

    companies = [company("A"), company("B")]
    # 2020 is a Rat year (trine 0), 2022 a Tiger year (trine 2).
    series = {"A": {2020: 0.10, 2022: 0.20}, "B": {2020: 0.30, 2022: 0.40}}
    years, selected, benchmark = market_folklore.company_portfolios(
        series, companies, {"A": 0, "B": 2}, "birth_trine"
    )
    assert years == [2020, 2022]
    assert selected == [0.10, 0.40]
    assert all(abs(actual - expected) < 1e-12 for actual, expected in zip(benchmark, [0.20, 0.30]))

    # Digit roots: 2024 -> 8, 2025 -> 9.
    series = {"A": {2024: 0.05, 2025: 0.15}, "B": {2024: 0.25, 2025: 0.35}}
    years, selected, _ = market_folklore.company_portfolios(
        series, companies, {"A": 8, "B": 9}, "birth_root"
    )
    assert years == [2024, 2025]
    assert selected == [0.05, 0.35]


def test_company_rule_metrics_supports_birth_modes():
    companies = [market_folklore.Company(*row) for row in market_folklore.COMPANIES]
    years = range(2015, 2025)
    series = {
        company.symbol: {year: 0.01 * ((index * 7 + year) % 21 - 10) for year in years}
        for index, company in enumerate(companies)
    }
    metrics = market_folklore.company_rule_metrics(series, companies, "birth_trine", trials=50)
    assert metrics["name"] == "Founder birth-year zodiac trine"
    assert 0 < metrics["permutation_p"] <= 1
    metrics = market_folklore.company_rule_metrics(series, companies, "birth_root", trials=50)
    assert metrics["name"] == "Founder birth-year root resonance"


# --- Bar parsing ------------------------------------------------------------


def test_parse_bars_falls_back_to_close_to_close_for_synthetic_opens():
    rows = [
        (dt.date(1930, 1, 2), 100.0, 100.0),  # synthetic open, no prior close
        (dt.date(1930, 1, 3), 102.0, 102.0),  # synthetic open -> prior-close fallback
        (dt.date(1930, 1, 6), 103.0, 105.0),  # genuine open -> open-to-close
    ]
    bars = market_folklore.parse_bars(make_payload(rows))
    assert [bar.date for bar in bars] == [day for day, _, _ in rows]
    assert bars[0].session_return == 0.0
    assert abs(bars[1].session_return - 0.02) < 1e-12
    assert abs(bars[2].session_return - (105.0 / 103.0 - 1)) < 1e-12


# --- Downloader (mocked subprocess, no network) -----------------------------


VALID_URL = "https://query1.finance.yahoo.com/v8/finance/chart/TEST"


def tiny_valid_payload() -> dict:
    return make_payload([(dt.date(2026, 1, 2), 10.0, 11.0)])


def test_download_runs_bounded_tls_verified_curl(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, capture_output, text, timeout):
        calls.append((command, timeout))
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(tiny_valid_payload()))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(market_folklore.subprocess, "run", fake_run)
    destination = tmp_path / "TEST.json"
    market_folklore.download(VALID_URL, destination)

    assert len(calls) == 1
    command, timeout = calls[0]
    assert command[0] == "curl"
    assert command[-1] == VALID_URL
    assert "--insecure" not in command and "-k" not in command
    assert "--fail" in command
    assert command[command.index("--proto") + 1] == "=https"
    assert command[command.index("--connect-timeout") + 1] == "10"
    assert command[command.index("--max-time") + 1] == "60"
    assert "Mozilla" in command[command.index("--user-agent") + 1]
    assert timeout == 90
    assert json.loads(destination.read_text())["chart"]["result"]
    assert not destination.with_suffix(".json.part").exists()


def test_download_retries_with_backoff_then_succeeds(tmp_path, monkeypatch):
    attempts = []
    delays = []

    def fake_run(command, capture_output, text, timeout):
        attempts.append(command)
        if len(attempts) < 3:
            return SimpleNamespace(returncode=35, stdout="", stderr="ssl handshake failed")
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps(tiny_valid_payload()))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(market_folklore.subprocess, "run", fake_run)
    monkeypatch.setattr(market_folklore.time, "sleep", delays.append)
    destination = tmp_path / "TEST.json"
    market_folklore.download(VALID_URL, destination)

    assert len(attempts) == 3
    assert delays == [2.0, 4.0]
    assert destination.exists()


def test_download_gives_up_after_three_retries(tmp_path, monkeypatch):
    attempts = []
    delays = []

    def fake_run(command, capture_output, text, timeout):
        attempts.append(command)
        return SimpleNamespace(returncode=6, stdout="", stderr="could not resolve host")

    monkeypatch.setattr(market_folklore.subprocess, "run", fake_run)
    monkeypatch.setattr(market_folklore.time, "sleep", delays.append)
    destination = tmp_path / "TEST.json"
    try:
        market_folklore.download(VALID_URL, destination)
        raise AssertionError("download should have raised")
    except RuntimeError as error:
        assert "could not resolve host" in str(error)

    assert len(attempts) == 4  # first try + 3 retries
    assert delays == [2.0, 4.0, 8.0]
    assert not destination.exists()
    assert not destination.with_suffix(".json.part").exists()


def test_download_rejects_non_chart_payload(tmp_path, monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({"chart": {"result": None, "error": "rate limited"}}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(market_folklore.subprocess, "run", fake_run)
    monkeypatch.setattr(market_folklore.time, "sleep", lambda _: None)
    destination = tmp_path / "TEST.json"
    try:
        market_folklore.download(VALID_URL, destination)
        raise AssertionError("download should have raised")
    except RuntimeError as error:
        assert "invalid payload" in str(error)
    assert not destination.exists()


def test_fetch_yahoo_uses_cache_without_touching_network(tmp_path, monkeypatch):
    start = dt.date(2024, 1, 1)
    rows = [
        (start + dt.timedelta(days=index), 100.0 + index, 100.5 + index)
        for index in range(260)
    ]
    (tmp_path / "INDEX_GSPC.json").write_text(json.dumps(make_payload(rows)))

    def refuse_network(*args, **kwargs):
        raise AssertionError("cache hit must not spawn a subprocess")

    monkeypatch.setattr(market_folklore.subprocess, "run", refuse_network)
    bars = market_folklore.fetch_yahoo("^GSPC", refresh=False, cache_dir=tmp_path)
    assert len(bars) == 260
    assert bars[0].date == start
    assert abs(bars[10].session_return - (110.5 / 110.0 - 1)) < 1e-12
