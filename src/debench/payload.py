"""Build provider requests from an explicit allowlist and refuse anything that could leak gold."""

from __future__ import annotations

import re
from typing import Any

from debench.dataset.paired_generator import state_text
from debench.dataset.schema import ScenarioRecord
from debench.providers.base import DecisionRequest, canonical_json
from debench.questions import QuestionSet

ALLOWED_BODY_KEYS = ("state", "questions")
# Bare "gold"/"split" are legitimate data-engineering words (gold layer, partition split), so only
# internal field names are matched.
FORBIDDEN_MARKERS = (
    "gold_label",
    "gold_verified",
    "injected",
    "label_source",
    "fault_id",
    "_internal",
    "difficulty_tags",
    "evidence_sufficiency",
    "semantic_pair",
)


class LeakageError(AssertionError):
    pass


def build_request(
    record: ScenarioRecord,
    qset: QuestionSet,
    question_language: str | None = None,
    option_orders: dict[str, list[str]] | None = None,
    only: list[str] | None = None,
) -> DecisionRequest:
    """State in the record's language; questions in ``question_language`` (defaults to the same)."""
    qlang = question_language or record.language
    request = DecisionRequest(
        scenario_id=record.scenario_id,
        language=record.language,  # type: ignore[arg-type]
        state=record.state,
        questions=qset.render(qlang, option_orders=option_orders, only=only),
    )
    assert_no_leakage(request, record)
    return request


def _gold_label_tokens(record: ScenarioRecord) -> list[str]:
    # Choice labels that are compound ids (e.g. "schema_mismatch") must not appear in the state.
    return [v for v in record.gold.values() if isinstance(v, str) and "_" in v]


def assert_no_leakage(request: DecisionRequest, record: ScenarioRecord) -> None:
    body = request.shared_body()
    if tuple(body) != ALLOWED_BODY_KEYS:
        raise LeakageError(f"unexpected body keys {list(body)}")
    text = state_text(request.state).lower()
    for marker in FORBIDDEN_MARKERS:
        if re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", text):
            raise LeakageError(f"{record.scenario_id}: forbidden marker {marker!r} in state")
    if record.scenario_id.lower() in text:
        raise LeakageError(f"{record.scenario_id}: scenario id in state")
    for label in _gold_label_tokens(record):
        if label.lower() in text:
            raise LeakageError(f"{record.scenario_id}: gold label {label!r} appears in state")
    internal_keys = {"gold", "split", "seed", "label_source", "difficulty_tags", "scenario_id"}
    if isinstance(request.state, dict) and internal_keys & set(map(str.lower, request.state)):
        raise LeakageError(f"{record.scenario_id}: internal field name used as a state key")


def payload_bytes(request: DecisionRequest) -> int:
    return len(canonical_json(request.shared_body()).encode("utf-8"))


def describe(request: DecisionRequest) -> dict[str, Any]:
    return {
        "scenario_id": request.scenario_id,
        "language": request.language,
        "request_hash": request.request_hash(),
        "request_bytes": payload_bytes(request),
        "question_count": len(request.questions),
    }
