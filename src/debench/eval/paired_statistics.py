"""Paired inference: cluster bootstrap, McNemar, Holm, zero-event bounds (PLAN 3.5, 10)."""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy import stats

DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 20261019

MetricOnIndex = Callable[[np.ndarray], float]


def bind(metric_fn: Callable[..., float], *arrays: Any) -> MetricOnIndex:
    """Turn ``metric_fn(*arrays)`` into a function of an item-index array (with repeats)."""
    arrs = [np.asarray(a) for a in arrays]
    return lambda idx: metric_fn(*(a[idx] for a in arrs))


@dataclass(frozen=True)
class BootstrapResult:
    estimate: float
    ci_low: float
    ci_high: float
    n_scenarios: int
    n_resamples: int
    n_valid_resamples: int
    seed: int
    confidence: float
    metric_a: float
    metric_b: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cluster_index(ids: Sequence[Hashable]) -> dict[Hashable, np.ndarray]:
    out: dict[Hashable, list[int]] = {}
    for i, k in enumerate(ids):
        out.setdefault(k, []).append(i)
    return {k: np.asarray(v) for k, v in out.items()}


def paired_bootstrap_diff(
    ids_a: Sequence[Hashable],
    ids_b: Sequence[Hashable],
    metric_a: MetricOnIndex,
    metric_b: MetricOnIndex,
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Percentile CI for metric(A) - metric(B), resampling scenario ids with replacement.

    Both sides are restricted to the scenario ids present in both; each drawn scenario brings
    all its items on each side, so the pairing and within-scenario clustering are preserved.
    Resamples where either metric is undefined (None/NaN) are dropped and counted.
    """
    if n_resamples < 2000:
        raise ValueError("PLAN 3.5 requires at least 2000 resamples")
    ca, cb = _cluster_index(ids_a), _cluster_index(ids_b)
    common = sorted(set(ca) & set(cb), key=str)
    if not common:
        raise ValueError("no common scenario ids")
    ia = [ca[k] for k in common]
    ib = [cb[k] for k in common]
    full_a = metric_a(np.concatenate(ia))
    full_b = metric_b(np.concatenate(ib))
    rng = np.random.default_rng(seed)
    n = len(common)
    diffs = []
    for _ in range(n_resamples):
        pick = rng.integers(0, n, n)
        va = metric_a(np.concatenate([ia[j] for j in pick]))
        vb = metric_b(np.concatenate([ib[j] for j in pick]))
        if va is not None and vb is not None and math.isfinite(va) and math.isfinite(vb):
            diffs.append(va - vb)
    alpha = (1 - confidence) / 2
    lo, hi = (np.quantile(diffs, [alpha, 1 - alpha]) if diffs else (float("nan"),) * 2)
    return BootstrapResult(float(full_a - full_b), float(lo), float(hi), n, n_resamples, len(diffs),
                           seed, confidence, float(full_a), float(full_b))


def bootstrap_ci(
    ids: Sequence[Hashable], metric: MetricOnIndex, n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED, confidence: float = 0.95,
) -> BootstrapResult:
    """Cluster-bootstrap CI of a single metric (``metric_b`` is the constant 0)."""
    return paired_bootstrap_diff(ids, ids, metric, lambda idx: 0.0, n_resamples, seed, confidence)


def language_gap(
    ids_th: Sequence[Hashable], ids_en: Sequence[Hashable], metric_th: MetricOnIndex, metric_en: MetricOnIndex,
    **kw: Any,
) -> BootstrapResult:
    """Within-model gap metric(th) - metric(en), paired by scenario id (negative = Thai worse
    for higher-is-better metrics). Head-to-head gaps use the same function with A/B swapped
    for models."""
    return paired_bootstrap_diff(ids_th, ids_en, metric_th, metric_en, **kw)


# --------------------------------------------------------------------------- McNemar

def mcnemar(correct_a: Any, correct_b: Any, exact_below: int = 25) -> dict[str, Any]:
    """Two-sided McNemar on paired correctness.

    b = A right/B wrong, c = A wrong/B right. Exact binomial (p=0.5, doubled tail, capped at 1)
    when b + c < ``exact_below``, otherwise chi-square with continuity correction.
    """
    a, bb = np.asarray(correct_a, bool), np.asarray(correct_b, bool)
    b = int(np.sum(a & ~bb))
    c = int(np.sum(~a & bb))
    d = b + c
    if d == 0:
        return {"b": b, "c": c, "n_discordant": 0, "method": "exact", "statistic": None, "p_value": 1.0}
    if d < exact_below:
        p = min(1.0, 2.0 * float(stats.binom.cdf(min(b, c), d, 0.5)))
        return {"b": b, "c": c, "n_discordant": d, "method": "exact", "statistic": None, "p_value": p}
    chi2 = (abs(b - c) - 1) ** 2 / d
    return {"b": b, "c": c, "n_discordant": d, "method": "chi2_cc", "statistic": float(chi2),
            "p_value": float(stats.chi2.sf(chi2, 1))}


def holm_bonferroni(p_values: Sequence[float], alpha: float = 0.05) -> dict[str, list]:
    """Holm step-down: adj_(i) = max_{j<=i} min(1, (m - j + 1) p_(j)); returns input order."""
    p = np.asarray(p_values, float)
    m = len(p)
    order = np.argsort(p, kind="stable")
    adj_sorted = np.minimum(1.0, np.maximum.accumulate((m - np.arange(m)) * p[order]))
    adj = np.empty(m)
    adj[order] = adj_sorted
    return {"adjusted": adj.tolist(), "reject": (adj <= alpha).tolist()}


# --------------------------------------------------------------------------- rare events

def rule_of_three(n: int) -> float:
    """Approximate 95% one-sided upper bound 3/n for the event rate after 0 events in n trials."""
    return min(1.0, 3.0 / n)


def clopper_pearson_upper(k: int, n: int, confidence: float = 0.95) -> float:
    """Exact one-sided upper bound; for k = 0 it equals 1 - (1 - confidence)^(1/n)."""
    if k >= n:
        return 1.0
    return float(stats.beta.ppf(confidence, k + 1, n - k))


def wilson_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Two-sided Wilson score interval."""
    z = float(stats.norm.ppf(1 - (1 - confidence) / 2))
    ph = k / n
    denom = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / denom
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def zero_event_bounds(n: int, confidence: float = 0.95) -> dict[str, Any]:
    """Upper bounds on the event rate when 0 events were seen in ``n`` trials."""
    lo, hi = wilson_interval(0, n, confidence)
    return {"n": n, "events": 0, "rule_of_three": rule_of_three(n),
            "clopper_pearson_upper": clopper_pearson_upper(0, n, confidence),
            "wilson_low": lo, "wilson_high": hi, "confidence": confidence}
