from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
import random
import statistics
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache"
DEFAULT_OUTPUT_DIR = ROOT
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DOWNLOAD_RETRIES = 3  # retries after the first attempt, with exponential backoff
SOLAR_SIGNS = (
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
)
ZODIAC = (
    "Rat",
    "Ox",
    "Tiger",
    "Rabbit",
    "Dragon",
    "Snake",
    "Horse",
    "Goat",
    "Monkey",
    "Rooster",
    "Dog",
    "Pig",
)
TRINE = {
    "Rat": 0,
    "Dragon": 0,
    "Monkey": 0,
    "Ox": 1,
    "Snake": 1,
    "Rooster": 1,
    "Tiger": 2,
    "Horse": 2,
    "Dog": 2,
    "Rabbit": 3,
    "Goat": 3,
    "Pig": 3,
}
# Founder selection rule, PRE-DECLARED before any scoring ran:
# 1. Take the founder most identified as the company's founding leader
#    (typically the founding CEO or the name history attaches to the founding).
# 2. If no single founder fits step 1, take the eldest cofounder by birth year.
# The mapping below was frozen under that rule, with birth years verified via
# web search, before the birth-year rules were evaluated. Discarded alternates
# are noted in the report (Wozniak 1950, Allen 1953, Musk 1971, Randolph 1958,
# Geschke 1939, Lerner 1955, Moore 1929).
COMPANIES = (
    ("AAPL", "Apple", 1976, "Steve Jobs", 1955),
    ("MSFT", "Microsoft", 1975, "Bill Gates", 1955),
    ("AMZN", "Amazon", 1994, "Jeff Bezos", 1964),
    ("NVDA", "NVIDIA", 1993, "Jensen Huang", 1963),
    ("GOOGL", "Google", 1998, "Larry Page", 1973),
    ("META", "Facebook", 2004, "Mark Zuckerberg", 1984),
    ("TSLA", "Tesla", 2003, "Martin Eberhard", 1960),
    ("NFLX", "Netflix", 1997, "Reed Hastings", 1960),
    ("ORCL", "Oracle", 1977, "Larry Ellison", 1944),
    ("ADBE", "Adobe", 1982, "John Warnock", 1940),
    ("CSCO", "Cisco", 1984, "Leonard Bosack", 1952),
    ("INTC", "Intel", 1968, "Robert Noyce", 1927),
)
ERAS = (
    ("Roaring Twenties boom", dt.date(1928, 1, 1), dt.date(1929, 9, 30)),
    ("1929 crash", dt.date(1929, 10, 1), dt.date(1931, 12, 31)),
    ("Dot-com boom", dt.date(1997, 1, 1), dt.date(2000, 3, 31)),
    ("Dot-com unwind", dt.date(2000, 4, 1), dt.date(2002, 12, 31)),
    ("Global financial crisis", dt.date(2007, 7, 1), dt.date(2009, 3, 31)),
    ("Post-GFC bull", dt.date(2012, 1, 1), dt.date(2017, 12, 31)),
)
PRIME_DAYS = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31}


@dataclass(frozen=True)
class Bar:
    date: dt.date
    open: float
    close: float
    adjusted_close: float
    session_return: float


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    founding_year: int
    founder: str
    founder_birth_year: int

    @property
    def founding_zodiac(self) -> str:
        return chinese_zodiac(self.founding_year)

    @property
    def trine(self) -> int:
        return TRINE[self.founding_zodiac]

    @property
    def name_root(self) -> int:
        return pythagorean_root(self.name)

    @property
    def founder_zodiac(self) -> str:
        return chinese_zodiac(self.founder_birth_year)

    @property
    def founder_trine(self) -> int:
        return TRINE[self.founder_zodiac]

    @property
    def founder_birth_root(self) -> int:
        return digital_root(self.founder_birth_year)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress-test market folklore rules.")
    parser.add_argument("--refresh", action="store_true", help="Refresh Yahoo responses.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--trials", type=int, default=5_000)
    return parser.parse_args()


def validate_chart_payload(raw: bytes) -> dict:
    """Parse a Yahoo chart response and fail loudly on any non-data payload."""
    payload = json.loads(raw)
    results = payload.get("chart", {}).get("result")
    if not results or not results[0].get("timestamp"):
        raise ValueError(f"Payload is not a usable chart response: {raw[:120]!r}")
    return payload


def download(url: str, destination: Path, retries: int = DOWNLOAD_RETRIES, base_delay: float = 2.0) -> None:
    """Bounded, TLS-verified download via a curl subprocess.

    Certificate validation is NEVER disabled; there is no code path that adds
    ``--insecure``. Each attempt is bounded by curl's own timeouts plus a hard
    subprocess timeout, and failures back off exponentially (2s, 4s, 8s).
    """
    part_path = destination.with_suffix(destination.suffix + ".part")
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--proto", "=https",
        "--connect-timeout", "10",
        "--max-time", "60",
        "--user-agent", BROWSER_USER_AGENT,
        "--output", str(part_path),
        url,
    ]
    last_error = "no attempt made"
    for attempt in range(1 + retries):
        if attempt:
            time.sleep(base_delay * 2 ** (attempt - 1))
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            last_error = "curl subprocess exceeded the 90s hard timeout"
            continue
        if completed.returncode != 0:
            last_error = f"curl exit {completed.returncode}: {completed.stderr.strip()}"
            continue
        try:
            validate_chart_payload(part_path.read_bytes())
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last_error = f"invalid payload: {error}"
            continue
        part_path.replace(destination)
        return
    part_path.unlink(missing_ok=True)
    raise RuntimeError(f"Download failed after {1 + retries} attempts for {url}: {last_error}")


def chart_url(symbol: str) -> str:
    end = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).timestamp())
    params = urllib.parse.urlencode(
        {
            "period1": -1325376000,
            "period2": end,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    return YAHOO_URL.format(symbol=urllib.parse.quote(symbol, safe=""), query=params)


def parse_bars(payload: dict) -> list[Bar]:
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    bars: list[Bar] = []
    previous_close: float | None = None
    for stamp, opening, closing, adj_close in zip(
        timestamps, quote["open"], quote["close"], adjusted, strict=True
    ):
        if opening is None or closing is None or adj_close is None:
            continue
        if opening <= 0 or closing <= 0 or adj_close <= 0:
            continue
        date = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).date()
        opening = float(opening)
        closing = float(closing)
        # Yahoo's pre-1962 S&P bars carry a synthetic open equal to the close
        # (only closes were recorded). Treating those as open-to-close would
        # claim the market never moved for three decades, so fall back to the
        # prior-close-to-close session return. Calendar rules are known before
        # the session either way, so neither measure looks ahead.
        if opening != closing or previous_close is None:
            session_return = closing / opening - 1
        else:
            session_return = closing / previous_close - 1
        bars.append(Bar(date, opening, closing, float(adj_close), session_return))
        previous_close = closing
    return bars


def fetch_yahoo(symbol: str, refresh: bool, cache_dir: Path = CACHE_DIR) -> list[Bar]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{symbol.replace('^', 'INDEX_')}.json"
    if refresh or not cache_path.exists():
        download(chart_url(symbol), cache_path)
        time.sleep(0.2)
    bars = parse_bars(validate_chart_payload(cache_path.read_bytes()))
    if len(bars) < 250:
        raise RuntimeError(f"Yahoo returned too little usable data for {symbol}.")
    return bars


def digital_root(value: int) -> int:
    if value == 0:
        return 0
    return 1 + ((abs(value) - 1) % 9)


def pythagorean_root(name: str) -> int:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    values = {letter: (position % 9) + 1 for position, letter in enumerate(letters)}
    return digital_root(sum(values[letter] for letter in name.upper() if letter in values))


def chinese_zodiac(year: int) -> str:
    return ZODIAC[(year - 4) % 12]


def solar_longitude(date: dt.date) -> float:
    days = (date - dt.date(2000, 1, 1)).days + 0.5
    mean_longitude = (280.46 + 0.9856474 * days) % 360
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360)
    return (mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2 * mean_anomaly)) % 360


def solar_sign(date: dt.date) -> str:
    return SOLAR_SIGNS[int(solar_longitude(date) // 30)]


def cardinal_window(date: dt.date) -> bool:
    return solar_longitude(date) % 90 <= 2.5


def intraday_return(bar: Bar) -> float:
    return bar.session_return


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def annualized_return(returns: list[float], periods_per_year: int) -> float:
    if not returns:
        return 0.0
    equity = math.prod(1 + value for value in returns)
    return equity ** (periods_per_year / len(returns)) - 1


def max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    largest = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        largest = min(largest, equity / peak - 1)
    return largest


def normal_one_sided_pvalue(selected: list[float], others: list[float]) -> float:
    if len(selected) < 2 or len(others) < 2:
        return 1.0
    selected_variance = statistics.variance(selected)
    other_variance = statistics.variance(others)
    standard_error = math.sqrt(selected_variance / len(selected) + other_variance / len(others))
    if standard_error == 0:
        return 1.0
    z_score = (mean(selected) - mean(others)) / standard_error
    return 0.5 * math.erfc(z_score / math.sqrt(2))


def permutation_pvalue(all_returns: list[float], selected_count: int, observed_sum: float, trials: int, seed: int) -> float:
    if selected_count == 0:
        return 1.0
    rng = random.Random(seed)
    wins = 0
    for _ in range(trials):
        if sum(rng.sample(all_returns, selected_count)) >= observed_sum:
            wins += 1
    return (wins + 1) / (trials + 1)


def split_half(values: list[float], flags: list[bool]) -> tuple[float, float]:
    midpoint = len(values) // 2
    first = [value for value, flag in zip(values[:midpoint], flags[:midpoint], strict=True) if flag]
    second = [value for value, flag in zip(values[midpoint:], flags[midpoint:], strict=True) if flag]
    return mean(first), mean(second)


def rule_verdict(average_bp: float, all_day_bp: float, p_value: float, first_half_bp: float, second_half_bp: float) -> str:
    """Blunt, predeclared verdict for a fixed rule claiming a positive edge.

    SUPPORTED needs a low permutation p, a lift over the unconditional session
    average, and a positive mean in both chronological halves. REFUTED means
    the rule offers no lift at all or does no better than the median random
    draw. Everything else is INCONCLUSIVE.
    """
    if p_value < 0.05 and average_bp > all_day_bp and first_half_bp > 0 and second_half_bp > 0:
        return "SUPPORTED"
    if average_bp <= all_day_bp or p_value >= 0.5:
        return "REFUTED"
    return "INCONCLUSIVE"


def fixed_rule_metrics(name: str, bars: list[Bar], predicate: Callable[[Bar], bool], trials: int, seed: int) -> dict:
    returns = [intraday_return(bar) for bar in bars]
    flags = [predicate(bar) for bar in bars]
    selected = [value for value, flag in zip(returns, flags, strict=True) if flag]
    strategy_returns = [value if flag else 0.0 for value, flag in zip(returns, flags, strict=True)]
    first_half, second_half = split_half(returns, flags)
    p_value = permutation_pvalue(returns, len(selected), sum(selected), trials, seed)
    average_bp = 10_000 * mean(selected)
    all_day_bp = 10_000 * mean(returns)
    return {
        "name": name,
        "sessions": len(selected),
        "exposure": len(selected) / len(returns),
        "average_bp": average_bp,
        "all_day_bp": all_day_bp,
        "annualized": annualized_return(strategy_returns, 252),
        "max_drawdown": max_drawdown(strategy_returns),
        "p_value": p_value,
        "first_half_bp": 10_000 * first_half,
        "second_half_bp": 10_000 * second_half,
        "verdict": rule_verdict(average_bp, all_day_bp, p_value, 10_000 * first_half, 10_000 * second_half),
    }


def exploratory_rows(bars: list[Bar], label: str, buckets: list[str], bucket_for: Callable[[Bar], str]) -> list[dict]:
    returns = [intraday_return(bar) for bar in bars]
    rows: list[dict] = []
    for bucket in buckets:
        selected = [value for bar, value in zip(bars, returns, strict=True) if bucket_for(bar) == bucket]
        others = [value for bar, value in zip(bars, returns, strict=True) if bucket_for(bar) != bucket]
        raw_p = normal_one_sided_pvalue(selected, others)
        rows.append(
            {
                "family": label,
                "bucket": bucket,
                "sessions": len(selected),
                "average_bp": 10_000 * mean(selected),
                "relative_bp": 10_000 * (mean(selected) - mean(others)),
                "raw_p": raw_p,
                "family_p": min(1.0, raw_p * len(buckets)),
            }
        )
    return rows


def normalized_path(bars: list[Bar], start: int, window: int) -> list[float]:
    base = bars[start].close
    return [bars[start + offset].close / base - 1 for offset in range(window)]


def correlation(left: list[float], right: list[float]) -> float:
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    return numerator / denominator if denominator else 0.0


def analog_row(bars: list[Bar], current: list[float], end_index: int, window: int, forward_days: int) -> dict:
    start_index = end_index - window + 1
    path = normalized_path(bars, start_index, window)
    differences = [candidate - latest for candidate, latest in zip(path, current, strict=True)]
    return {
        "end_index": end_index,
        "end_date": bars[end_index].date.isoformat(),
        "rmse_pct": 100 * math.sqrt(mean(difference * difference for difference in differences)),
        "max_deviation_pct": 100 * max(abs(difference) for difference in differences),
        "correlation": correlation(path, current),
        "forward_21d_pct": 100 * (bars[end_index + forward_days].close / bars[end_index].close - 1),
        "path": path,
    }


def select_non_overlapping(candidates: list[dict], window: int, limit: int | None = None) -> list[dict]:
    """Greedy pick of best-first candidates whose windows share no session."""
    kept: list[dict] = []
    for candidate in candidates:
        if all(abs(candidate["end_index"] - row["end_index"]) >= window for row in kept):
            kept.append(candidate)
        if limit is not None and len(kept) == limit:
            break
    return kept


def historical_analogs(bars: list[Bar], window: int = 63, forward_days: int = 21) -> tuple[list[dict], list[dict], dict]:
    current = normalized_path(bars, len(bars) - window, window)
    candidates = [
        analog_row(bars, current, end_index, window, forward_days)
        for end_index in range(window - 1, len(bars) - window - forward_days)
    ]
    candidates.sort(key=lambda row: (row["rmse_pct"], row["max_deviation_pct"]))
    spaced = select_non_overlapping(candidates, window, limit=10)
    era_rows: list[dict] = []
    for name, start, end in ERAS:
        in_era = [
            row
            for row in candidates
            if start <= dt.date.fromisoformat(row["end_date"]) <= end
        ]
        if in_era:
            era_rows.append({"era": name, **in_era[0]})
    by_deviation = sorted(candidates, key=lambda row: row["max_deviation_pct"])
    summary = {
        "window": window,
        "current_start": bars[-window].date.isoformat(),
        "current_end": bars[-1].date.isoformat(),
        "within_2pct": len(
            select_non_overlapping([row for row in by_deviation if row["max_deviation_pct"] <= 2.0], window)
        ),
        "within_3pct": len(
            select_non_overlapping([row for row in by_deviation if row["max_deviation_pct"] <= 3.0], window)
        ),
        "candidate_count": len(candidates),
    }
    return spaced, era_rows, summary


def yearly_returns(bars: list[Bar]) -> dict[int, float]:
    by_year: dict[int, list[Bar]] = {}
    for bar in bars:
        by_year.setdefault(bar.date.year, []).append(bar)
    return {
        year: rows[-1].adjusted_close / rows[0].adjusted_close - 1
        for year, rows in by_year.items()
        if len(rows) >= 200
    }


def company_portfolios(series: dict[str, dict[int, float]], companies: list[Company], labels: dict[str, int], mode: str) -> tuple[list[int], list[float], list[float]]:
    years = sorted(set.union(*(set(values) for values in series.values())))
    selected_returns: list[float] = []
    benchmark_returns: list[float] = []
    kept_years: list[int] = []
    for year in years:
        eligible = [company for company in companies if year in series[company.symbol]]
        if not eligible:
            continue
        benchmark_returns.append(mean(series[company.symbol][year] for company in eligible))
        if mode in ("trine", "birth_trine"):
            candidates = [company for company in eligible if labels[company.symbol] == TRINE[chinese_zodiac(year)]]
        else:  # "name" and "birth_root" both key on the calendar-year digit root
            candidates = [company for company in eligible if labels[company.symbol] == digital_root(year)]
        selected_returns.append(mean(series[company.symbol][year] for company in candidates) if candidates else 0.0)
        kept_years.append(year)
    return kept_years, selected_returns, benchmark_returns


COMPANY_MODES = {
    "trine": ("Founding-year zodiac trine", lambda company: company.trine, 20260719),
    "name": ("Name/year root resonance", lambda company: company.name_root, 20260720),
    "birth_trine": ("Founder birth-year zodiac trine", lambda company: company.founder_trine, 20260723),
    "birth_root": ("Founder birth-year root resonance", lambda company: company.founder_birth_root, 20260724),
}


def company_rule_metrics(series: dict[str, dict[int, float]], companies: list[Company], mode: str, trials: int) -> dict:
    rule_name, label_for, seed = COMPANY_MODES[mode]
    labels = {company.symbol: label_for(company) for company in companies}
    years, observed, benchmark = company_portfolios(series, companies, labels, mode)
    observed_annualized = annualized_return(observed, 1)
    rng = random.Random(seed)
    label_values = list(labels.values())
    null_annualized: list[float] = []
    for _ in range(trials):
        shuffled = label_values[:]
        rng.shuffle(shuffled)
        shuffled_labels = {company.symbol: value for company, value in zip(companies, shuffled, strict=True)}
        _, permuted, _ = company_portfolios(series, companies, shuffled_labels, mode)
        null_annualized.append(annualized_return(permuted, 1))
    return {
        "name": rule_name,
        "years": years,
        "annualized": observed_annualized,
        "benchmark_annualized": annualized_return(benchmark, 1),
        "max_drawdown": max_drawdown(observed),
        "permutation_p": (sum(value >= observed_annualized for value in null_annualized) + 1) / (trials + 1),
        "first_half": annualized_return(observed[: len(observed) // 2], 1),
        "second_half": annualized_return(observed[len(observed) // 2 :], 1),
        "average_selected_return": mean(observed),
        "null_median": statistics.median(null_annualized),
    }


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    separator = ["---"] * len(headers)
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(separator) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows]
    )


def percent(value: float, digits: int = 2) -> str:
    return f"{100 * value:.{digits}f}%"


def bp(value: float) -> str:
    return f"{value:+.2f}"


def write_analogs_csv(output_dir: Path, rows: list[dict], era_rows: list[dict]) -> None:
    with (output_dir / "analogs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["group", "label", "end_date", "rmse_pct", "max_deviation_pct", "correlation", "forward_21d_pct"],
        )
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            writer.writerow({"group": "overall", "label": f"rank {index}", **{key: row[key] for key in writer.fieldnames[2:]}})
        for row in era_rows:
            writer.writerow({"group": "era", "label": row["era"], **{key: row[key] for key in writer.fieldnames[2:]}})


def svg_polyline(path: list[float], minimum: float, maximum: float, color: str) -> str:
    points: list[str] = []
    for index, value in enumerate(path):
        x_value = 55 + 880 * index / (len(path) - 1)
        y_value = 365 - 325 * (value - minimum) / (maximum - minimum)
        points.append(f"{x_value:.1f},{y_value:.1f}")
    return f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(points)}" />'


def write_analogs_html(output_dir: Path, bars: list[Bar], era_rows: list[dict], summary: dict) -> None:
    current = normalized_path(bars, len(bars) - summary["window"], summary["window"])
    paths = [current] + [row["path"] for row in era_rows]
    minimum = min(min(path) for path in paths)
    maximum = max(max(path) for path in paths)
    padding = max(0.01, (maximum - minimum) * 0.08)
    minimum -= padding
    maximum += padding
    colors = ["#0f766e", "#dc2626", "#7c3aed", "#ea580c", "#2563eb", "#16a34a", "#64748b"]
    lines = [svg_polyline(current, minimum, maximum, colors[0])]
    legend = [f'<span style="color:{colors[0]}">■ Latest 63 sessions</span>']
    for index, row in enumerate(era_rows, start=1):
        lines.append(svg_polyline(row["path"], minimum, maximum, colors[index]))
        legend.append(f'<span style="color:{colors[index]}">■ {html.escape(row["era"])} ({row["end_date"]})</span>')
    zero_y = 365 - 325 * (0 - minimum) / (maximum - minimum)
    body = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>S&P 500 folklore analogs</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:2rem auto;color:#172033}} svg{{width:100%;border:1px solid #cbd5e1}} .legend span{{display:inline-block;margin:.5rem 1rem .2rem 0}} table{{border-collapse:collapse}}td,th{{padding:.35rem .6rem;border:1px solid #cbd5e1;text-align:right}}th:first-child,td:first-child{{text-align:left}}</style>
<h1>Normalized 63-session S&amp;P analogs</h1>
<p>Latest window: {summary["current_start"]} to {summary["current_end"]}. Each path starts at 0%; this compares shape, not index level. A 3% match means every point on a candidate path stays within 3 percentage points of the latest path.</p>
<div class="legend">{"".join(legend)}</div>
<svg viewBox="0 0 960 410" role="img" aria-label="Normalized market paths">
<line x1="55" y1="{zero_y:.1f}" x2="935" y2="{zero_y:.1f}" stroke="#94a3b8" stroke-dasharray="5 5" />
<text x="10" y="45">{maximum * 100:.1f}%</text><text x="10" y="{zero_y:.1f}">0%</text><text x="10" y="370">{minimum * 100:.1f}%</text>
{"".join(lines)}</svg>
<table><tr><th>Era</th><th>End date</th><th>RMSE</th><th>Max deviation</th><th>Correlation</th><th>Next 21 sessions</th></tr>
{"".join(f"<tr><td>{html.escape(row['era'])}</td><td>{row['end_date']}</td><td>{row['rmse_pct']:.2f}%</td><td>{row['max_deviation_pct']:.2f}%</td><td>{row['correlation']:.3f}</td><td>{row['forward_21d_pct']:+.2f}%</td></tr>" for row in era_rows)}</table>
<p>Descriptive only: the forward columns are historical outcomes, not predictions.</p>
</html>"""
    (output_dir / "analogs.html").write_text(body)


def write_report(
    output_dir: Path,
    bars: list[Bar],
    fixed: list[dict],
    exploratory: list[dict],
    analogs: list[dict],
    era_rows: list[dict],
    analog_summary: dict,
    company_metrics: list[dict],
    companies: list[Company],
) -> None:
    fixed_rows = [
        [
            row["name"],
            str(row["sessions"]),
            percent(row["exposure"]),
            bp(row["average_bp"]),
            percent(row["annualized"]),
            percent(row["max_drawdown"]),
            f"{row['p_value']:.3f}",
            bp(row["first_half_bp"]),
            bp(row["second_half_bp"]),
            row["verdict"],
        ]
        for row in fixed
    ]
    exploratory_sorted = sorted(exploratory, key=lambda row: row["relative_bp"], reverse=True)
    exploratory_rows = [
        [
            row["family"],
            row["bucket"],
            str(row["sessions"]),
            bp(row["average_bp"]),
            bp(row["relative_bp"]),
            f"{row['raw_p']:.4f}",
            f"{row['family_p']:.4f}",
        ]
        for row in exploratory_sorted
    ]
    analog_rows = [
        [str(index), row["end_date"], f"{row['rmse_pct']:.2f}%", f"{row['max_deviation_pct']:.2f}%", f"{row['correlation']:.3f}", f"{row['forward_21d_pct']:+.2f}%"]
        for index, row in enumerate(analogs, start=1)
    ]
    era_table_rows = [
        [row["era"], row["end_date"], f"{row['rmse_pct']:.2f}%", f"{row['max_deviation_pct']:.2f}%", f"{row['correlation']:.3f}", f"{row['forward_21d_pct']:+.2f}%"]
        for row in era_rows
    ]
    company_rows = [
        [
            row["name"],
            f"{row['years'][0]}–{row['years'][-1]}",
            percent(row["annualized"]),
            percent(row["benchmark_annualized"]),
            percent(row["max_drawdown"]),
            f"{row['permutation_p']:.3f}",
            percent(row["first_half"]),
            percent(row["second_half"]),
        ]
        for row in company_metrics
    ]
    universe_rows = [
        [
            company.symbol,
            company.name,
            str(company.founding_year),
            company.founding_zodiac,
            str(company.name_root),
            company.founder,
            str(company.founder_birth_year),
            company.founder_zodiac,
            str(company.founder_birth_root),
        ]
        for company in companies
    ]
    report = f"""# Market Folklore Stress Test — Results

> Deliberately speculative research, not investment advice or a live-trading input.

## Dataset

- Daily S&P Composite / S&P 500 chart data: **{bars[0].date.isoformat()} to {bars[-1].date.isoformat()}** ({len(bars):,} sessions).
- Price source: Yahoo Finance public chart endpoint, fetched on {dt.date.today().isoformat()}. The date rules use open-to-close returns, so each signal is known before the open.
- Data caveat: Yahoo's pre-1962 bars carry a synthetic open equal to the close (only closes were recorded). For those sessions the rule return falls back to prior-close-to-close; calendar signals are known before the session either way, so neither measure looks ahead.
- The S&P 500 launched in 1957; earlier observations are the historical predecessor/back-tested composite. That makes 1929 useful for a shape comparison but not identical to the modern index.

## Predeclared Rules

These are the only date/solar rules treated as strategies rather than post-hoc discovery. `p` is a one-sided permutation p-value against random session selections of identical size. A persuasive signal should have a low p-value **and** retain its sign in both chronological halves.

{markdown_table(["Rule", "Sessions", "Exposure", "Avg bp/trade", "Annualized", "Max DD", "p", "1st half bp", "2nd half bp", "Verdict"], fixed_rows)}

Interpretation: no row qualifies as a reliable trading signal merely because it has a positive annualized return. The all-session intraday average is **{bp(10_000 * mean(intraday_return(bar) for bar in bars))} bp**. Verdicts are mechanical: SUPPORTED needs `p < 0.05`, a lift over the unconditional average, and a positive mean in both chronological halves; REFUTED means no lift at all or `p >= 0.5` (no better than the median random draw); everything else is INCONCLUSIVE.

## Exploratory Fishing Net

I scanned solar-sign and date-root buckets to make the inevitable selection bias visible. `Family p` is the raw one-sided normal-approximation p-value multiplied by 12 or 9 tests. These rows are diagnostics, **not** signals selected for deployment.

{markdown_table(["Family", "Bucket", "Sessions", "Avg bp", "Vs rest bp", "Raw p", "Family p"], exploratory_rows)}

## 63-Session Chart Analogs

The latest normalized path runs from **{analog_summary['current_start']}** through **{analog_summary['current_end']}**. Paths are rebased to 0%, so the test is shape-only. Across {analog_summary['candidate_count']:,} eligible historical endpoints, **{analog_summary['within_2pct']}** non-overlapping windows stayed within 2 percentage points at every point and **{analog_summary['within_3pct']}** stayed within 3 points (windows counted so no two share a session; ranked rows below are non-overlapping too).

{markdown_table(["Overall rank", "End date", "RMSE", "Max deviation", "Correlation", "Following 21 sessions"], analog_rows)}

### Named crash and boom eras

{markdown_table(["Era", "Best endpoint", "RMSE", "Max deviation", "Correlation", "Following 21 sessions"], era_table_rows)}

Open `analogs.html` for the overlaid paths and `analogs.csv` for the raw ranking. The final column is what happened afterward in history, not a forecast. Pattern matching is particularly vulnerable to data mining and regime changes.

## Company Name / Founding-Year Rules

The trine rule buys the listed companies only in calendar years whose Chinese-zodiac trine matches the company's founding-year trine. The name rule buys a company if its Pythagorean name root matches the calendar-year root. The two founder rules re-key the same machinery to the founder's **birth** year: the birth-trine rule holds a company when the calendar year's trine matches the founder's birth-year trine, and the birth-root rule holds it when the calendar-year digit root matches the birth-year digit root. All four rebalance annually, use split-adjusted returns, and compare with static label shuffles across the same firms (the `--trials` count). A year with no matching company sits in cash. The benchmark is an equal-weight portfolio of whichever universe names have data that year, so early years hold only a couple of firms.

Founder selection rule, pre-declared before scoring: take the founder most identified as the company's founding leader (typically the founding CEO); where no single founder fits, take the eldest cofounder. Applications worth noting: TSLA maps to Martin Eberhard (founding CEO, b. 1960), not Elon Musk (b. 1971, joined 2004); CSCO is genuinely ambiguous between the married cofounders, so the eldest-cofounder clause picks Leonard Bosack (b. 1952, verified — not 1951 as sometimes quoted) over Sandy Lerner (b. 1955); ADBE keeps John Warnock (co-founding CEO, b. 1940) although Charles Geschke (b. 1939) was elder; NFLX keeps Reed Hastings (b. 1960) although Marc Randolph (b. 1958) held the first CEO title; AAPL and MSFT keep Jobs and Gates (both b. 1955) although Wozniak (b. 1950) and Allen (b. 1953) were elder. Birth years verified 2026-07-19 against Wikipedia biographies: [Jobs](https://en.wikipedia.org/wiki/Steve_Jobs), [Gates](https://en.wikipedia.org/wiki/Bill_Gates), [Bezos](https://en.wikipedia.org/wiki/Jeff_Bezos), [Huang](https://en.wikipedia.org/wiki/Jensen_Huang), [Page](https://en.wikipedia.org/wiki/Larry_Page), [Zuckerberg](https://en.wikipedia.org/wiki/Mark_Zuckerberg), [Eberhard](https://en.wikipedia.org/wiki/Martin_Eberhard), [Hastings](https://en.wikipedia.org/wiki/Reed_Hastings), [Ellison](https://en.wikipedia.org/wiki/Larry_Ellison), [Warnock](https://en.wikipedia.org/wiki/John_Warnock), [Bosack](https://en.wikipedia.org/wiki/Leonard_Bosack), [Noyce](https://en.wikipedia.org/wiki/Robert_Noyce). Zodiac uses the calendar year throughout, consistent with the founding-year rule; by the lunar calendar Bezos (born 1964-01-12, before that year's lunar new year) would be a Rabbit rather than a Dragon.

{markdown_table(["Rule", "Years", "Annualized", "Equal-weight", "Max DD", "Shuffle p", "1st half", "2nd half"], company_rows)}

{markdown_table(["Ticker", "Founding name", "Year", "Zodiac", "Name root", "Founder", "Born", "Birth zodiac", "Birth root"], universe_rows)}

This company test is strongly survivor-biased and uses a tiny manually declared universe of current large firms. It cannot support capital allocation even if a result looks good; the shuffle p-value only says whether the labels beat relabelings within this already-biased sample.

## Bottom Line

{chr(10).join(f"- **{row['name']}** — {row['verdict']} (p = {row['p_value']:.3f}, {bp(row['average_bp'])} bp/session vs {bp(row['all_day_bp'])} bp unconditional)." for row in fixed)}

This is a stress test for charming market stories, not evidence that astrology or numerology dictates prices. The implementation preserves the results so claims can be audited, but none should enter a live strategy without a separately precommitted, out-of-sample protocol and conventional risk controls.
"""
    (output_dir / "RESULTS.md").write_text(report)


def main() -> None:
    args = parse_args()
    if args.trials < 100:
        raise SystemExit("--trials must be at least 100.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    spx = fetch_yahoo("^GSPC", args.refresh)
    fixed = [
        fixed_rule_metrics(
            "Date digit-root 1 / 4 / 7",
            spx,
            lambda bar: digital_root(int(bar.date.strftime("%Y%m%d"))) in {1, 4, 7},
            args.trials,
            20260719,
        ),
        fixed_rule_metrics("Solar cardinal 0–2.5°", spx, lambda bar: cardinal_window(bar.date), args.trials, 20260720),
        fixed_rule_metrics("Prime day-of-month", spx, lambda bar: bar.date.day in PRIME_DAYS, args.trials, 20260721),
        fixed_rule_metrics(
            "Friday the 13th event",
            spx,
            lambda bar: bar.date.weekday() == 4 and bar.date.day == 13,
            args.trials,
            20260722,
        ),
    ]
    exploratory = exploratory_rows(spx, "Solar sign", list(SOLAR_SIGNS), lambda bar: solar_sign(bar.date))
    exploratory.extend(
        exploratory_rows(
            spx,
            "Date root",
            [str(value) for value in range(1, 10)],
            lambda bar: str(digital_root(int(bar.date.strftime("%Y%m%d")))),
        )
    )
    analogs, era_rows, analog_summary = historical_analogs(spx)
    companies = [Company(*row) for row in COMPANIES]
    company_series = {company.symbol: yearly_returns(fetch_yahoo(company.symbol, args.refresh)) for company in companies}
    company_metrics = [
        company_rule_metrics(company_series, companies, mode, args.trials)
        for mode in ("trine", "name", "birth_trine", "birth_root")
    ]
    write_analogs_csv(args.output_dir, analogs, era_rows)
    write_analogs_html(args.output_dir, spx, era_rows, analog_summary)
    write_report(args.output_dir, spx, fixed, exploratory, analogs, era_rows, analog_summary, company_metrics, companies)
    print(f"Wrote {args.output_dir / 'RESULTS.md'}")
    print(f"Wrote {args.output_dir / 'analogs.html'}")
    print(f"Wrote {args.output_dir / 'analogs.csv'}")


if __name__ == "__main__":
    main()
