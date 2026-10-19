"""Perturbation robustness: flips, distribution shift, negation consistency (PLAN 10, 12)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

KL_EPS = 1e-12


def choice_flip_rate(pred_a: Sequence[str | None], pred_b: Sequence[str | None]) -> float:
    """Share of paired items whose predicted label differs. Pairs with a missing side are
    excluded (NaN if none remain)."""
    pairs = [(a, b) for a, b in zip(pred_a, pred_b) if a is not None and b is not None]
    return float(np.mean([a != b for a, b in pairs])) if pairs else float("nan")


def tvd(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """0.5 * sum_i |p_i - q_i| with both vectors reindexed by label key (missing key = 0)."""
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def mean_tvd(dists_a: Sequence[Mapping[str, float] | None], dists_b: Sequence[Mapping[str, float] | None]) -> float:
    vals = [tvd(p, q) for p, q in zip(dists_a, dists_b) if p is not None and q is not None]
    return float(np.mean(vals)) if vals else float("nan")


def kl_divergence(p: Mapping[str, float], q: Mapping[str, float], eps: float = KL_EPS) -> float:
    """KL(p || q) over the union of keys with epsilon-clipped q (nats)."""
    return sum(pv * math.log(pv / max(q.get(k, 0.0), eps)) for k, pv in p.items() if pv > 0)


def score_ev_shift(ev_a: Sequence[float | None], ev_b: Sequence[float | None]) -> dict[str, float]:
    """Mean absolute and mean signed (B - A) shift of the expected ordinal value."""
    d = [b - a for a, b in zip(ev_a, ev_b) if a is not None and b is not None]
    if not d:
        return {"mean_abs_shift": float("nan"), "mean_signed_shift": float("nan"), "n": 0}
    return {"mean_abs_shift": float(np.mean(np.abs(d))), "mean_signed_shift": float(np.mean(d)), "n": len(d)}


def noul_abs_shift(p_a: Sequence[float | None], p_b: Sequence[float | None]) -> float:
    d = [abs(a - b) for a, b in zip(p_a, p_b) if a is not None and b is not None]
    return float(np.mean(d)) if d else float("nan")


def negation_gap(p_x: Sequence[float], p_not_x: Sequence[float]) -> np.ndarray:
    """Per-item |P(not X) - (1 - P(X))|."""
    return np.abs(np.asarray(p_not_x, float) - (1.0 - np.asarray(p_x, float)))


def negation_consistency(p_x: Sequence[float], p_not_x: Sequence[float]) -> dict[str, Any]:
    g = negation_gap(p_x, p_not_x)
    return {"n": len(g), "mean_gap": float(g.mean()) if len(g) else float("nan"),
            "max_gap": float(g.max()) if len(g) else float("nan")}
