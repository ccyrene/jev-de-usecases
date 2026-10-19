"""Validate provider answers and derive comparable probability fields.

Vendor ``confidence`` values use different formulas per provider, so they are stored as
``confidence_vendor`` only. Cross-model comparisons use ``max_probability`` and
``normalized_entropy`` computed here from each probability distribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from debench.providers.base import ERROR_INVALID_OUTPUT, DecisionRequest, DecisionResponse

PROB_SUM_TOL = 1e-3
# Providers round reported probabilities (Laya to 4 dp, Jev to 2 dp), so a sum may miss 1 by up to
# K * half a unit in the last reported decimal. Precision coarser than 2 dp gets no extra allowance.
MIN_ROUNDING_DECIMALS = 2


class InvalidAnswer(ValueError):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


def check_probabilities(p: dict[str, Any], tol: float = PROB_SUM_TOL) -> None:
    if not p:
        raise InvalidAnswer("empty_distribution", "empty probability distribution")
    vals = list(p.values())
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vals):
        raise InvalidAnswer("non_finite", "non-numeric or non-finite probability")
    if not all(0.0 <= x <= 1.0 for x in vals):
        raise InvalidAnswer("out_of_range", "probability outside [0, 1]")
    if abs(sum(vals) - 1.0) > tol:
        raise InvalidAnswer("bad_sum", f"probabilities sum to {sum(vals):.6f}")


def _decimals(x: float, max_decimals: int = 8) -> int:
    """Fewest decimals that reproduce x up to float noise (0.8200000000000001 -> 2)."""
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
        return max_decimals  # validity is judged by check_probabilities, not here
    for d in range(max_decimals + 1):
        scaled = float(x) * 10**d
        if abs(scaled - round(scaled)) < 1e-6:
            return d
    return max_decimals


def rounding_tolerance(values: list[float]) -> float:
    """Sum tolerance explained by the precision the provider actually reported."""
    d = max(MIN_ROUNDING_DECIMALS, max((_decimals(v) for v in values), default=0))
    return max(PROB_SUM_TOL, len(values) * 0.5 * 10.0**-d)


def _renormalize(p: dict[str, float]) -> dict[str, float]:
    total = sum(p.values())
    return {k: v / total for k, v in p.items()}


def normalized_entropy(probs: list[float]) -> float:
    k = len(probs)
    if k < 2:
        return 0.0
    h = -sum(x * math.log(x) for x in probs if x > 0)
    return h / math.log(k)


@dataclass(frozen=True)
class NormalizedAnswer:
    question_id: str
    primitive: str
    valid: bool
    error_type: str | None
    distribution: dict[str, float] | None = None
    predicted_label: str | None = None
    predicted_level: int | None = None
    score_value: float | None = None
    noul_probability: float | None = None
    max_probability: float | None = None
    normalized_entropy: float | None = None
    confidence_vendor: float | None = None
    api_choice_matches_argmax: bool | None = None


def _invalid(qid: str, primitive: str, kind: str) -> NormalizedAnswer:
    return NormalizedAnswer(question_id=qid, primitive=primitive, valid=False, error_type=kind)


def _vendor_confidence(raw: dict[str, Any]) -> float | None:
    c = raw.get("confidence")
    return float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None


def _normalize_choice(qid: str, question: dict[str, Any], raw: dict[str, Any]) -> NormalizedAnswer:
    criteria = question["criteria"]
    labels = list(criteria.keys()) if isinstance(criteria, dict) else [str(x) for x in criteria]
    probs = raw.get("probabilities")
    if not isinstance(probs, dict) or set(map(str, probs)) != set(labels):
        raise InvalidAnswer("label_set_mismatch", f"{qid}: probabilities keys differ from options")
    probs = {str(k): v for k, v in probs.items()}
    check_probabilities(probs, rounding_tolerance(list(probs.values())))
    dist = _renormalize({lab: float(probs[lab]) for lab in labels})
    chosen = raw.get("choice")
    if chosen not in dist:
        raise InvalidAnswer("unknown_choice", f"{qid}: selected label not among options")
    pmax = max(dist.values())
    return NormalizedAnswer(
        question_id=qid,
        primitive="choice",
        valid=True,
        error_type=None,
        distribution=dist,
        predicted_label=chosen,
        max_probability=pmax,
        normalized_entropy=normalized_entropy(list(dist.values())),
        confidence_vendor=_vendor_confidence(raw),
        api_choice_matches_argmax=math.isclose(dist[chosen], pmax, abs_tol=1e-9),
    )


def _normalize_score(qid: str, question: dict[str, Any], raw: dict[str, Any]) -> NormalizedAnswer:
    k = len(question["criteria"])
    probs = raw.get("probabilities")
    if isinstance(probs, list):
        probs = {str(i): v for i, v in enumerate(probs)}
    expected_keys = {str(i) for i in range(k)}
    if not isinstance(probs, dict) or set(map(str, probs)) != expected_keys:
        raise InvalidAnswer("level_set_mismatch", f"{qid}: score levels differ from 0..{k - 1}")
    probs = {str(key): v for key, v in probs.items()}
    check_probabilities(probs, rounding_tolerance(list(probs.values())))
    dist = _renormalize({str(i): float(probs[str(i)]) for i in range(k)})
    values = [dist[str(i)] for i in range(k)]
    pmax = max(values)
    return NormalizedAnswer(
        question_id=qid,
        primitive="score",
        valid=True,
        error_type=None,
        distribution=dist,
        # Ties resolve to the lowest level so argmax is deterministic.
        predicted_level=values.index(pmax),
        score_value=sum(i * p for i, p in enumerate(values)),
        max_probability=pmax,
        normalized_entropy=normalized_entropy(values),
        confidence_vendor=_vendor_confidence(raw),
    )


def _normalize_noul(qid: str, raw: dict[str, Any]) -> NormalizedAnswer:
    p = raw.get("noul")
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not math.isfinite(p):
        raise InvalidAnswer("non_finite", f"{qid}: noul probability missing or non-finite")
    if not 0.0 <= p <= 1.0:
        raise InvalidAnswer("out_of_range", f"{qid}: noul probability outside [0, 1]")
    p = float(p)
    return NormalizedAnswer(
        question_id=qid,
        primitive="noul",
        valid=True,
        error_type=None,
        distribution={"false": 1.0 - p, "true": p},
        noul_probability=p,
        max_probability=max(p, 1.0 - p),
        normalized_entropy=normalized_entropy([p, 1.0 - p]),
        confidence_vendor=_vendor_confidence(raw),
    )


def normalize_answer(qid: str, question: dict[str, Any], raw: dict[str, Any] | None) -> NormalizedAnswer:
    primitive = question["type"]
    if raw is None:
        return _invalid(qid, primitive, "missing_answer")
    if raw.get("type") not in (None, primitive):
        return _invalid(qid, primitive, "type_mismatch")
    try:
        if primitive == "choice":
            return _normalize_choice(qid, question, raw)
        if primitive == "score":
            return _normalize_score(qid, question, raw)
        if primitive == "noul":
            return _normalize_noul(qid, raw)
    except InvalidAnswer as exc:
        return _invalid(qid, primitive, exc.kind)
    return _invalid(qid, primitive, ERROR_INVALID_OUTPUT)


def normalize_response(request: DecisionRequest, response: DecisionResponse) -> list[NormalizedAnswer]:
    """One NormalizedAnswer per asked question; provider failures stay visible as invalid rows."""
    out = []
    for qid, question in request.questions.items():
        if response.error is not None:
            out.append(_invalid(qid, question["type"], response.error))
        else:
            out.append(normalize_answer(qid, question, response.answers.get(qid)))
    return out
