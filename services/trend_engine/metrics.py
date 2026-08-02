"""Pure trend math. No network, no state, no imports outside the stdlib.

Every function here is same-input-same-output — this is the deterministic half
of the trend lane. The LLM never computes any of these numbers; it only reads
them (see `ai.py`).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── basics ───────────────────────────────────────────────────────────────────


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float]) -> float:
    """Sample stdev (n-1). 0.0 for fewer than 2 points."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def pct_change(a: float, b: float) -> Optional[float]:
    """(b/a - 1) * 100, in percent. None if `a` is non-positive."""
    if a is None or b is None or a <= 0:
        return None
    return (b / a - 1.0) * 100.0


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def norm_cdf(z: float) -> float:
    """Standard normal CDF (erf-based, exact to double precision)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal (Acklam's rational approximation, |err| < 1.15e-9).

    Used for forecast bands; a table lookup would be fine but this keeps any
    confidence level available without hardcoding z-scores.
    """
    p = clamp(p, 1e-9, 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ── trend shape ──────────────────────────────────────────────────────────────


def log_slope(closes: Sequence[float]) -> Tuple[float, float]:
    """OLS fit of ln(close) on bar index.

    Returns (slope_pct_per_bar, r2). The slope is the compounding drift in
    percent per bar; r2 is how *clean* the trend is (1.0 = a straight line,
    ~0 = noise around a flat mean). Direction and cleanliness are different
    questions and a trend read needs both: a +3%/day slope with r2=0.05 is
    a coin-flip dressed as a trend.
    """
    n = len(closes)
    if n < 3 or any(c <= 0 for c in closes):
        return 0.0, 0.0
    ys = [math.log(c) for c in closes]
    xs = list(range(n))
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return (math.exp(slope) - 1.0) * 100.0, r2


def linear_slope(ys: Sequence[float]) -> Tuple[float, float]:
    """OLS fit of y on index, in the units of y. Returns (slope_per_step, r2).

    The additive twin of `log_slope`, for series that live in a bounded linear
    space — probabilities. Fitting a probability in log space distorts the ends
    (a 1pp move at 0.02 is not a 1pp move at 0.50).
    """
    n = len(ys)
    if n < 3:
        return 0.0, 0.0
    xs = list(range(n))
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r2


def efficiency_ratio(closes: Sequence[float]) -> float:
    """Kaufman efficiency: |net move| / sum(|bar moves|), in [0, 1].

    1.0 = every bar moved the same way (pure trend). 0.1 = the coin travelled
    ten times the distance it covered (chop). This is the honest gate on
    whether "trend" is even the right word for what the tape did.
    """
    if len(closes) < 2:
        return 0.0
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        return 0.0
    return abs(closes[-1] - closes[0]) / path


def ema(values: Sequence[float], n: int) -> Optional[float]:
    """Exponential MA seeded on the first `n` values' SMA. None if too short."""
    if n <= 0 or len(values) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = mean(values[:n])
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def ema_stack(closes: Sequence[float], fast: int = 7, slow: int = 21) -> str:
    """Where price sits against its own fast/slow EMAs.

    'bull' = px > fast > slow, 'bear' = px < fast < slow, 'mixed' otherwise.
    """
    ef, es = ema(closes, fast), ema(closes, slow)
    if ef is None or es is None or not closes:
        return "unknown"
    px = closes[-1]
    if px > ef > es:
        return "bull"
    if px < ef < es:
        return "bear"
    return "mixed"


def streak(closes: Sequence[float]) -> int:
    """Consecutive same-direction bars ending at the last bar.

    Positive = up days, negative = down days, 0 = last bar was flat.
    """
    if len(closes) < 2:
        return 0
    d = 1 if closes[-1] > closes[-2] else (-1 if closes[-1] < closes[-2] else 0)
    if d == 0:
        return 0
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        step = 1 if closes[i] > closes[i - 1] else (-1 if closes[i] < closes[i - 1] else 0)
        if step != d:
            break
        n += 1
    return n * d


def daily_returns(closes: Sequence[float]) -> List[float]:
    return [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def atr_pct(bars: Sequence[Any], n: int = 14) -> float:
    """Average true range over the last `n` bars, as % of the last close.

    Accepts either objects with .h/.l/.c or dicts with 'h'/'l'/'c'.
    """
    def g(b: Any, k: str) -> float:
        return float(b[k] if isinstance(b, dict) else getattr(b, k))
    if len(bars) < 2:
        return 0.0
    trs: List[float] = []
    for i in range(max(1, len(bars) - n), len(bars)):
        h, l, pc = g(bars[i], "h"), g(bars[i], "l"), g(bars[i - 1], "c")
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    last = g(bars[-1], "c")
    if last <= 0 or not trs:
        return 0.0
    return mean(trs) / last * 100.0


def range_position(closes: Sequence[float], lookback: int = 7) -> Optional[float]:
    """Where the last close sits in its `lookback`-bar range, 0..1.

    1.0 = at the high of the window, 0.0 = at the low.
    """
    w = list(closes[-lookback:])
    if len(w) < 2:
        return None
    lo, hi = min(w), max(w)
    if hi <= lo:
        return 0.5
    return (w[-1] - lo) / (hi - lo)


def beta(coin_rets: Sequence[float], bench_rets: Sequence[float]) -> float:
    """OLS beta of coin on benchmark. 1.0 when the sample is too small."""
    n = min(len(coin_rets), len(bench_rets))
    if n < 8:
        return 1.0
    cr, br = list(coin_rets[-n:]), list(bench_rets[-n:])
    mb, mc = mean(br), mean(cr)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson r. 0.0 when degenerate or shorter than 3 points."""
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    a, b = list(xs[-n:]), list(ys[-n:])
    ma, mb = mean(a), mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def zscore(x: float, sample: Sequence[float]) -> float:
    s = stdev(sample)
    if s <= 0:
        return 0.0
    return (x - mean(sample)) / s


# ── statistics for the base-rate lanes ───────────────────────────────────────


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for k successes in n trials.

    Used everywhere a conditional base rate is reported: a 62% up-rate on n=13
    is not a pattern, and the interval is what says so.
    """
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def binom_two_sided_p(k: int, n: int, p0: float = 0.5) -> float:
    """Exact two-sided binomial p-value for k successes in n trials vs p0.

    Deterministic, no scipy. Sums the probability of every outcome at most as
    likely as the observed one. Computed in log space via lgamma: `math.comb`
    overflows to float at a few hundred trials and these lanes routinely grade
    thousands of windows. Falls back to the normal approximation past
    `_EXACT_MAX` trials, where the two agree to well past the 3rd decimal.
    """
    if n <= 0:
        return 1.0
    p0 = clamp(p0, 1e-12, 1 - 1e-12)
    if n > _EXACT_MAX:
        sd = math.sqrt(n * p0 * (1 - p0))
        if sd <= 0:
            return 1.0
        z = (abs(k - n * p0) - 0.5) / sd            # continuity-corrected
        return max(0.0, min(1.0, 2 * (1 - norm_cdf(z))))
    lg = math.lgamma
    base = lg(n + 1)
    lp, lq = math.log(p0), math.log1p(-p0)

    def logpmf(i: int) -> float:
        return base - lg(i + 1) - lg(n - i + 1) + i * lp + (n - i) * lq

    obs = logpmf(k)
    tol = obs + 1e-9
    return min(1.0, sum(math.exp(v) for v in (logpmf(i) for i in range(n + 1)) if v <= tol))


_EXACT_MAX = 50_000


def sharpe_like(rets: Sequence[float], periods_per_year: int = 365) -> float:
    """Annualised mean/stdev of a return series. 0.0 if flat or too short."""
    if len(rets) < 3:
        return 0.0
    s = stdev(rets)
    if s <= 0:
        return 0.0
    return mean(rets) / s * math.sqrt(periods_per_year)


def max_drawdown_pct(closes: Sequence[float]) -> float:
    """Worst peak-to-trough decline in the window, in percent (positive)."""
    if len(closes) < 2:
        return 0.0
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            worst = max(worst, (peak - c) / peak)
    return worst * 100.0


# ── labelling ────────────────────────────────────────────────────────────────

# Ordered strongest-up to strongest-down. The dashboard colours off these.
TREND_LABELS = ("STRONG_UP", "UP", "CHOP", "DOWN", "STRONG_DOWN")


def trend_label(slope_pct_day: float, r2: float, eff: float, stack: str) -> str:
    """Classify a trend from its shape, not just its sign.

    A move only earns STRONG when the drift is real (>=1%/day), the path was
    efficient (>=0.35), and the EMA stack agrees. Anything that fails the
    cleanliness test lands in CHOP no matter how big the net move was — that
    distinction is the whole point of the tab.
    """
    clean = (r2 >= 0.45 and eff >= 0.35)
    if abs(slope_pct_day) < 0.25 or eff < 0.18:
        return "CHOP"
    if slope_pct_day > 0:
        if slope_pct_day >= 1.0 and clean and stack == "bull":
            return "STRONG_UP"
        return "UP" if clean or slope_pct_day >= 0.6 else "CHOP"
    if slope_pct_day <= -1.0 and clean and stack == "bear":
        return "STRONG_DOWN"
    return "DOWN" if clean or slope_pct_day <= -0.6 else "CHOP"


def trend_score(slope_pct_day: float, r2: float, eff: float, ret_7d: float) -> float:
    """Single sortable number: signed drift scaled by how believable it is.

    score = 7d drift (%) x sqrt(r2 x efficiency). Two coins up 20% on the week
    rank apart when one climbed a staircase and the other spiked once.
    """
    quality = math.sqrt(max(0.0, r2) * max(0.0, eff))
    drift = slope_pct_day * 7.0 if slope_pct_day else ret_7d
    return round(drift * quality, 3)


def summarize(reads: Sequence[Dict[str, Any]], key: str) -> Dict[str, float]:
    """min/median/max/mean of one numeric field across reads (skips None)."""
    vals = sorted(float(r[key]) for r in reads if r.get(key) is not None)
    if not vals:
        return {"min": 0.0, "med": 0.0, "max": 0.0, "mean": 0.0, "n": 0}
    mid = len(vals) // 2
    med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return {"min": vals[0], "med": med, "max": vals[-1], "mean": mean(vals),
            "n": len(vals)}
