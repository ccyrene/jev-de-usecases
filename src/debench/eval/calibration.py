"""Post-hoc calibration, abstention and cost-matrix thresholds (PLAN 3.4, 10, 12).

Fit functions take calibration-split data only and return frozen parameter objects; ``apply_*``
functions use those parameters on any other split. Raw and calibrated metrics are always
produced together (``raw_and_calibrated_*``).
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from debench.eval import metrics as M

T_BOUNDS = (0.05, 20.0)
PLATT_EPS = 1e-6


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


# --------------------------------------------------------------------------- temperature

@dataclass(frozen=True)
class TemperatureParams:
    temperature: float
    eps: float
    n_fit: int
    nll_before: float
    nll_after: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_temperature(probs: Any, params: TemperatureParams | float, eps: float | None = None) -> np.ndarray:
    """softmax(log(clip(p, eps)) / T): epsilon-clip the log, divide by T, renormalize."""
    t = params.temperature if isinstance(params, TemperatureParams) else float(params)
    e = params.eps if isinstance(params, TemperatureParams) else (eps if eps is not None else M.NLL_EPS)
    logp = np.log(np.clip(np.asarray(probs, float), e, 1.0))
    return _softmax(logp / t)


def fit_temperature(
    cal_probs: Any, cal_gold_idx: Any, eps: float = M.NLL_EPS, bounds: tuple[float, float] = T_BOUNDS
) -> TemperatureParams:
    """Fit T by bounded NLL minimization on the calibration split. ``cal_gold_idx`` indexes columns."""
    p, y = np.asarray(cal_probs, float), np.asarray(cal_gold_idx, int)
    rows = np.arange(len(y))

    def loss(t: float) -> float:
        return M.nll(apply_temperature(p, t, eps)[rows, y], eps)

    res = minimize_scalar(loss, bounds=bounds, method="bounded", options={"xatol": 1e-6})
    return TemperatureParams(float(res.x), eps, len(y), M.nll(p[rows, y], eps), float(res.fun))


# --------------------------------------------------------------------------- platt (noul)

@dataclass(frozen=True)
class PlattParams:
    a: float
    b: float
    eps: float
    n_fit: int
    nll_before: float
    nll_after: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _logit(p: Any, eps: float) -> np.ndarray:
    q = np.clip(np.asarray(p, float), eps, 1.0 - eps)
    return np.log(q / (1.0 - q))


def apply_platt(p: Any, params: PlattParams) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(params.a * _logit(p, params.eps) + params.b)))


def fit_platt(cal_p: Any, cal_y: Any, eps: float = PLATT_EPS, nll_eps: float = M.NLL_EPS) -> PlattParams:
    """Logit scaling sigma(a * logit(p) + b), fitted on the calibration split (bounded a, b)."""
    y = np.asarray(cal_y, int)
    if y.min() == y.max():
        raise ValueError("calibration split needs both noul classes")
    z = _logit(cal_p, eps)

    def loss(ab: np.ndarray) -> float:
        q = 1.0 / (1.0 + np.exp(-(ab[0] * z + ab[1])))
        return M.nll(np.where(y == 1, q, 1.0 - q), nll_eps)

    res = minimize(loss, x0=[1.0, 0.0], method="L-BFGS-B", bounds=[(1e-3, 50.0), (-20.0, 20.0)])
    before = M.nll(np.where(y == 1, np.clip(cal_p, 0, 1), 1.0 - np.clip(cal_p, 0, 1)), nll_eps)
    return PlattParams(float(res.x[0]), float(res.x[1]), eps, len(y), before, float(res.fun))


# --------------------------------------------------------------------------- abstention

def risk_coverage_curve(confidence: Any, correct: Any) -> dict[str, np.ndarray]:
    """Accept items from most to least confident; one point per distinct confidence value.

    Returns arrays ``threshold``, ``coverage`` (fraction accepted), ``risk`` (error rate among
    accepted) and ``aurc`` (trapezoid area of risk over coverage).
    """
    c, ok = np.asarray(confidence, float), np.asarray(correct, float)
    order = np.argsort(-c, kind="stable")
    c, ok = c[order], ok[order]
    ends = np.r_[np.flatnonzero(np.diff(c) != 0), len(c) - 1]
    n_acc = ends + 1
    risk = 1.0 - np.cumsum(ok)[ends] / n_acc
    cov = n_acc / len(c)
    return {"threshold": c[ends], "coverage": cov, "risk": risk,
            "aurc": np.array(float(np.trapezoid(risk, cov)))}


@dataclass(frozen=True)
class AbstentionParams:
    threshold: float  # accept when confidence >= threshold; inf = abstain on everything
    target_risk: float
    feasible: bool
    n_fit: int
    cal_coverage: float
    cal_risk: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fit_abstention_threshold(
    cal_confidence: Any, cal_correct: Any, target_risk: float, min_accepted: int = 1
) -> AbstentionParams:
    """Lowest threshold (max coverage) whose calibration selective risk is <= ``target_risk``."""
    n = len(cal_confidence)
    if n == 0:
        return AbstentionParams(float("inf"), target_risk, False, 0, 0.0, None)
    rc = risk_coverage_curve(cal_confidence, cal_correct)
    n_acc = np.rint(rc["coverage"] * n)
    ok = np.flatnonzero((rc["risk"] <= target_risk) & (n_acc >= min_accepted))
    if len(ok) == 0:
        return AbstentionParams(float("inf"), target_risk, False, n, 0.0, None)
    i = ok[-1]
    return AbstentionParams(float(rc["threshold"][i]), target_risk, True, n,
                            float(rc["coverage"][i]), float(rc["risk"][i]))


def apply_abstention(confidence: Any, params: AbstentionParams) -> np.ndarray:
    """Boolean mask of answers that are auto-accepted; the rest go to human review."""
    return np.asarray(confidence, float) >= params.threshold


def fit_group_abstention(
    groups: Sequence[Hashable], cal_confidence: Any, cal_correct: Any, target_risk: float, min_accepted: int = 1
) -> dict[Hashable, AbstentionParams]:
    """One threshold per group key, e.g. ``(model, language, question_type)``."""
    g = np.empty(len(groups), dtype=object)
    g[:] = list(groups)
    c, ok = np.asarray(cal_confidence, float), np.asarray(cal_correct, float)
    out = {}
    for key in dict.fromkeys(groups):
        m = np.array([x == key for x in g])
        out[key] = fit_abstention_threshold(c[m], ok[m], target_risk, min_accepted)
    return out


def apply_group_abstention(
    params: dict[Hashable, AbstentionParams], groups: Sequence[Hashable], confidence: Any
) -> np.ndarray:
    """Raises KeyError for a group with no calibration fit rather than guessing a threshold."""
    c = np.asarray(confidence, float)
    return np.array([c[i] >= params[k].threshold for i, k in enumerate(groups)], dtype=bool)


# --------------------------------------------------------------------------- cost matrix (noul)

@dataclass(frozen=True)
class CostThresholdParams:
    threshold: float  # p >= threshold -> human review; p < threshold -> auto-allow
    cost_false_allow: float
    cost_review: float
    n_fit: int
    cal_expected_cost: float
    cal_false_allow_rate: float
    cal_review_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fit_cost_threshold(cal_p: Any, cal_y: Any, cost_false_allow: float, cost_review: float) -> CostThresholdParams:
    """Choose the review threshold minimizing mean cost on the calibration split.

    Positive (y=1) is the unsafe event. Auto-allowing a positive costs ``cost_false_allow``;
    reviewing a negative costs ``cost_review``. Ties go to the lowest threshold (more review).
    Candidates are the observed probabilities plus ``inf`` (allow everything).
    """
    p, y = np.asarray(cal_p, float), np.asarray(cal_y, int)
    best: tuple[float, float] | None = None
    for t in [*np.unique(p), np.inf]:
        review = p >= t
        cost = (cost_false_allow * np.sum(~review & (y == 1)) + cost_review * np.sum(review & (y == 0))) / len(y)
        if best is None or cost < best[0] - 1e-15:
            best = (float(cost), float(t))
    cost, t = best  # type: ignore[misc]
    review = p >= t
    return CostThresholdParams(t, cost_false_allow, cost_review, len(y), cost,
                               float(np.mean(~review & (y == 1))), float(np.mean(review)))


def evaluate_cost_threshold(p: Any, y: Any, params: CostThresholdParams) -> dict[str, float]:
    """Apply a fitted operating point to another split."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    review = p >= params.threshold
    fa = np.sum(~review & (y == 1))
    ur = np.sum(review & (y == 0))
    npos = int(y.sum())
    return {
        "mean_cost": float((params.cost_false_allow * fa + params.cost_review * ur) / len(y)),
        "false_auto_allow": int(fa),
        "unnecessary_review": int(ur),
        "review_rate": float(review.mean()),
        "false_negative_rate_after_gating": float(fa / npos) if npos else float("nan"),
        "false_negative_rate_before_gating_at_0.5": float(np.sum((p < 0.5) & (y == 1)) / npos) if npos else float("nan"),
    }


# --------------------------------------------------------------------------- raw + calibrated

def raw_and_calibrated_choice(
    labels: Sequence[str], cal_probs: Any, cal_gold: Sequence[str], eval_probs: Any, eval_gold: Sequence[str],
    eps: float = M.NLL_EPS,
) -> dict[str, Any]:
    """Fit T on the calibration split, then report raw and calibrated metrics on the eval split."""
    labs = list(labels)
    params = fit_temperature(cal_probs, [labs.index(g) for g in cal_gold], eps)
    cal_eval = apply_temperature(eval_probs, params)
    return {
        "params": params.to_dict(),
        "raw": M.choice_metrics(labs, eval_gold, eval_probs, eps=eps),
        "calibrated": M.choice_metrics(labs, eval_gold, cal_eval, eps=eps),
    }


def raw_and_calibrated_noul(
    cal_p: Any, cal_y: Any, eval_p: Any, eval_y: Any, eps: float = M.NLL_EPS
) -> dict[str, Any]:
    params = fit_platt(cal_p, cal_y, nll_eps=eps)
    return {
        "params": params.to_dict(),
        "raw": M.noul_metrics(eval_y, eval_p, eps=eps),
        "calibrated": M.noul_metrics(eval_y, apply_platt(eval_p, params), eps=eps),
    }
