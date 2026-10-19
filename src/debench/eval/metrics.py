"""Quality metrics for the three decision primitives (PLAN 3.4, 12).

Conventions: choice probabilities are an ``(n, K)`` array whose columns follow ``labels``;
score levels are 0-based ordinals and probabilities an ``(n, K)`` array over levels; noul
probabilities are P(true) in ``[0, 1]``. Functions are pure and never read files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.stats import rankdata

NLL_EPS = 1e-12  # shared numerical-stability epsilon (configs/study.yaml statistics.nll_epsilon)
ECE_BINS = 15


# --------------------------------------------------------------------------- shared

def _arr(x: Any) -> np.ndarray:
    return np.asarray(x, dtype=float)


def nll(p_true: Any, eps: float = NLL_EPS) -> float:
    """Mean negative log likelihood from the probability given to the gold outcome."""
    p = np.clip(_arr(p_true), eps, 1.0)
    return float(-np.mean(np.log(p)))


def expected_calibration_error(
    confidence: Any, correct: Any, n_bins: int = ECE_BINS, strategy: str = "mass"
) -> float:
    """Top-label ECE = sum_b (n_b / n) * |acc_b - conf_b|.

    ``mass``: bins hold (nearly) equal counts after a stable sort by confidence (ties may
    straddle bins). ``width``: equal-width bins on [0, 1]; a confidence of exactly 1.0 falls in
    the last bin. Empty bins are skipped.
    """
    conf, corr = _arr(confidence), _arr(correct)
    n = len(conf)
    if n == 0:
        return float("nan")
    if strategy == "mass":
        groups = np.array_split(np.argsort(conf, kind="stable"), n_bins)
    elif strategy == "width":
        idx = np.minimum((conf * n_bins).astype(int), n_bins - 1)
        groups = [np.flatnonzero(idx == b) for b in range(n_bins)]
    else:
        raise ValueError(f"unknown ECE strategy {strategy!r}")
    total = 0.0
    for g in groups:
        if len(g):
            total += len(g) / n * abs(corr[g].mean() - conf[g].mean())
    return float(total)


def ece_both(confidence: Any, correct: Any, n_bins: int = ECE_BINS) -> dict[str, float]:
    return {
        "ece_equal_mass": expected_calibration_error(confidence, correct, n_bins, "mass"),
        "ece_equal_width": expected_calibration_error(confidence, correct, n_bins, "width"),
    }


# --------------------------------------------------------------------------- coverage

def coverage_metrics(n_attempted: int, n_answered: int, n_correct: int) -> dict[str, Any]:
    """Coverage and both accuracy denominators. Invalid/failed answers are never successes."""
    if not 0 <= n_correct <= n_answered <= n_attempted:
        raise ValueError("require 0 <= correct <= answered <= attempted")
    return {
        "n_attempted": n_attempted,
        "n_answered": n_answered,
        "n_correct": n_correct,
        "coverage": n_answered / n_attempted if n_attempted else None,
        "effective_accuracy": n_correct / n_attempted if n_attempted else None,
        "answered_only_accuracy": n_correct / n_answered if n_answered else None,
    }


# --------------------------------------------------------------------------- choice

def choice_per_class(labels: Sequence[str], y_true: Sequence[str], y_pred: Sequence[str]) -> dict[str, dict[str, float]]:
    """Per-class precision/recall/F1/support over the full label set (0 when undefined)."""
    tc, pc = Counter(y_true), Counter(y_pred)
    hits = Counter(t for t, p in zip(y_true, y_pred) if t == p)
    out = {}
    for lab in labels:
        tp, support, npred = hits[lab], tc[lab], pc[lab]
        prec = tp / npred if npred else 0.0
        rec = tp / support if support else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[lab] = {"precision": prec, "recall": rec, "f1": f1, "support": support}
    return out


def accuracy(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
    return float(np.mean([t == p for t, p in zip(y_true, y_pred)])) if len(y_true) else float("nan")


def macro_f1(labels: Sequence[str], y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    """Unweighted mean F1 over *all* ``labels``, including ones absent from predictions (F1 = 0)."""
    pc = choice_per_class(labels, y_true, y_pred)
    return float(np.mean([v["f1"] for v in pc.values()]))


def balanced_accuracy(labels: Sequence[str], y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    """Mean recall over labels with gold support > 0 (labels never in gold carry no recall)."""
    rec = [v["recall"] for v in choice_per_class(labels, y_true, y_pred).values() if v["support"] > 0]
    return float(np.mean(rec)) if rec else float("nan")


def _onehot(labels: Sequence[str], y_true: Sequence[str]) -> np.ndarray:
    index = {lab: i for i, lab in enumerate(labels)}
    y = np.zeros((len(y_true), len(labels)))
    for r, t in enumerate(y_true):
        y[r, index[t]] = 1.0
    return y


def multiclass_brier(probs: Any, labels: Sequence[str], y_true: Sequence[str]) -> float:
    """mean over items of sum_k (p_k - y_k)^2."""
    return float(np.mean(np.sum((_arr(probs) - _onehot(labels, y_true)) ** 2, axis=1)))


def choice_nll(probs: Any, labels: Sequence[str], y_true: Sequence[str], eps: float = NLL_EPS) -> float:
    p = _arr(probs)
    idx = np.array([list(labels).index(t) for t in y_true])
    return nll(p[np.arange(len(idx)), idx], eps)


def choice_metrics(
    labels: Sequence[str],
    y_true: Sequence[str],
    probs: Any,
    y_pred: Sequence[str] | None = None,
    eps: float = NLL_EPS,
    n_bins: int = ECE_BINS,
) -> dict[str, Any]:
    """All choice metrics. ``y_pred`` defaults to argmax; ECE always uses the argmax label."""
    labels, p = list(labels), _arr(probs)
    argmax = [labels[i] for i in p.argmax(axis=1)]
    pred = list(y_pred) if y_pred is not None else argmax
    out = {
        "n": len(y_true),
        "accuracy": accuracy(y_true, pred),
        "balanced_accuracy": balanced_accuracy(labels, y_true, pred),
        "macro_f1": macro_f1(labels, y_true, pred),
        "per_class": choice_per_class(labels, y_true, pred),
        "brier": multiclass_brier(p, labels, y_true),
        "nll": choice_nll(p, labels, y_true, eps),
        "nll_epsilon": eps,
        "argmax_agreement": accuracy(argmax, pred),
    }
    out.update(ece_both(p.max(axis=1), [a == t for a, t in zip(argmax, y_true)], n_bins))
    return out


# --------------------------------------------------------------------------- score

def expected_levels(probs: Any) -> np.ndarray:
    p = _arr(probs)
    return p @ np.arange(p.shape[1])


def quadratic_weighted_kappa(y_true: Any, y_pred: Any, n_levels: int) -> float | None:
    """1 - sum(w*O)/sum(w*E), w_ij = (i-j)^2/(K-1)^2; None when chance disagreement is zero."""
    t, q = np.asarray(y_true, int), np.asarray(y_pred, int)
    obs = np.zeros((n_levels, n_levels))
    np.add.at(obs, (t, q), 1.0)
    exp = np.outer(obs.sum(1), obs.sum(0)) / obs.sum()
    i = np.arange(n_levels)
    w = (i[:, None] - i[None, :]) ** 2 / max(n_levels - 1, 1) ** 2
    denom = (w * exp).sum()
    return None if denom == 0 else float(1.0 - (w * obs).sum() / denom)


def ranked_probability_score(probs: Any, y_true: Any, normalize: bool = False) -> float:
    """Mean over items of sum_{k<K-1} (CDF_p(k) - CDF_y(k))^2; divided by K-1 if ``normalize``."""
    p = _arr(probs)
    k = p.shape[1]
    cdf_p = np.cumsum(p, axis=1)[:, :-1]
    cdf_y = (np.arange(k - 1)[None, :] >= np.asarray(y_true, int)[:, None]).astype(float)
    rps = np.sum((cdf_p - cdf_y) ** 2, axis=1)
    return float(np.mean(rps) / (k - 1 if normalize else 1))


def score_metrics(y_true: Any, probs: Any) -> dict[str, Any]:
    p, y = _arr(probs), np.asarray(y_true, int)
    k = p.shape[1]
    ev = expected_levels(p)
    argmax = p.argmax(axis=1)  # ties -> lowest level, as in normalize.py
    return {
        "n": len(y),
        "mae_expected": float(np.mean(np.abs(ev - y))),
        "rmse_expected": float(np.sqrt(np.mean((ev - y) ** 2))),
        "accuracy_argmax": float(np.mean(argmax == y)),
        "qwk": quadratic_weighted_kappa(y, argmax, k),
        "rps": ranked_probability_score(p, y),
        "rps_normalized": ranked_probability_score(p, y, normalize=True),
    }


# --------------------------------------------------------------------------- noul

def auroc(y_true: Any, p: Any) -> float | None:
    """Rank-based AUROC with average ranks for ties; None if only one class is present."""
    y, s = np.asarray(y_true, int), _arr(p)
    npos, nneg = int(y.sum()), int((1 - y).sum())
    if npos == 0 or nneg == 0:
        return None
    ranks = rankdata(s)
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def auprc(y_true: Any, p: Any) -> float | None:
    """Average precision sum_n (R_n - R_{n-1}) P_n over distinct score thresholds (step-wise,
    no interpolation); tied scores form one threshold. None if only one class is present."""
    y, s = np.asarray(y_true, int), _arr(p)
    npos = int(y.sum())
    if npos == 0 or npos == len(y):
        return None
    order = np.argsort(-s, kind="stable")
    y, s = y[order], s[order]
    tp = np.cumsum(y)
    ends = np.r_[np.flatnonzero(np.diff(s) != 0), len(s) - 1]
    precision = tp[ends] / (ends + 1)
    recall = tp[ends] / npos
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def threshold_metrics(y_true: Any, p: Any, threshold: float) -> dict[str, float]:
    """Precision/recall/F1 for the positive class at ``p >= threshold`` (0 when undefined)."""
    y, pred = np.asarray(y_true, int), _arr(p) >= threshold
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"threshold": float(threshold), "precision": prec, "recall": rec, "f1": f1,
            "accuracy": float(np.mean(pred == (y == 1)))}


def noul_metrics(
    y_true: Any, p: Any, threshold: float | None = None, eps: float = NLL_EPS, n_bins: int = ECE_BINS
) -> dict[str, Any]:
    """``threshold`` is a calibration-split operating point; 0.5 is always reported."""
    y, s = np.asarray(y_true, int), _arr(p)
    p_gold = np.where(y == 1, s, 1.0 - s)
    out: dict[str, Any] = {
        "n": len(y),
        "prevalence": float(y.mean()) if len(y) else None,
        "auroc": auroc(y, s),
        "auprc": auprc(y, s),
        "brier": float(np.mean((s - y) ** 2)),
        "log_loss": nll(p_gold, eps),
        "nll_epsilon": eps,
        "at_0.5": threshold_metrics(y, s, 0.5),
    }
    # Top-label ECE: confidence max(p, 1-p), correct when the 0.5 decision is right.
    out.update(ece_both(np.maximum(s, 1 - s), (s >= 0.5) == (y == 1), n_bins))
    out["at_threshold"] = threshold_metrics(y, s, threshold) if threshold is not None else None
    return out


# --------------------------------------------------------------------------- summaries

def summarize_primitive(
    primitive: str,
    rows: Sequence[Any],
    gold: Sequence[Any],
    *,
    labels: Sequence[str] | None = None,
    n_levels: int | None = None,
    threshold: float | None = None,
    eps: float = NLL_EPS,
) -> dict[str, Any]:
    """One summary dict for a primitive from NormalizedAnswer-like rows and aligned gold.

    ``gold``: label (choice), 0-based level (score), bool/0/1 (noul). Every row counts as
    attempted; quality metrics use valid rows only and are reported next to coverage and
    effective accuracy (correct / attempted).
    """
    if len(rows) != len(gold):
        raise ValueError("rows and gold must be aligned")
    valid = [(r, g) for r, g in zip(rows, gold) if r.valid]
    errors = Counter(r.error_type for r in rows if not r.valid)
    vr, vg = [r for r, _ in valid], [g for _, g in valid]

    if primitive == "choice":
        labs = list(labels) if labels is not None else sorted({*map(str, vg), *(k for r in vr for k in r.distribution)})
        p = [[r.distribution[lab] for lab in labs] for r in vr]
        m = choice_metrics(labs, vg, p, [r.predicted_label for r in vr], eps) if vr else {}
        n_correct = sum(r.predicted_label == g for r, g in valid)
    elif primitive == "score":
        k = n_levels or (len(vr[0].distribution) if vr else 0)
        p = [[r.distribution[str(i)] for i in range(k)] for r in vr]
        m = score_metrics(vg, p) if vr else {}
        n_correct = sum(r.predicted_level == g for r, g in valid)
    elif primitive == "noul":
        s = [r.noul_probability for r in vr]
        y = [int(bool(g)) for g in vg]
        m = noul_metrics(y, s, threshold, eps) if vr else {}
        n_correct = sum((q >= 0.5) == bool(t) for q, t in zip(s, y))
    else:
        raise ValueError(f"unknown primitive {primitive!r}")

    return {
        "primitive": primitive,
        **coverage_metrics(len(rows), len(vr), n_correct),
        "invalid_rate": 1.0 - len(vr) / len(rows) if rows else None,
        "error_counts": dict(errors),
        "metrics": m,
    }
