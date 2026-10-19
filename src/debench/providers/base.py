"""Common decision interface shared by every provider adapter.

Both Laya and the TypeSafe API return answers in the same per-question shape, which is
the canonical shape adapters must pass through unchanged in ``DecisionResponse.answers``:

    choice: {"type": "choice", "choice": <label>, "probabilities": {label: p, ...}, ...}
    score:  {"type": "score", "score": <expected index>, "probabilities": {"0": p0, ...}, ...}
    noul:   {"type": "noul", "noul": <P(true)>, ...}

``debench.normalize`` validates these and derives comparable fields. Vendor-specific extras
(``confidence``, ``answer_confidence``, ``routing`` ...) are kept but never compared directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Language = Literal["en", "th"]
Primitive = Literal["choice", "score", "noul"]
PRIMITIVES: tuple[str, ...] = ("choice", "score", "noul")

# Provider-level failure kinds. A failure is never scored as a wrong answer or a success.
ERROR_TIMEOUT = "timeout"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_HTTP = "http_error"
ERROR_AUTH = "auth_failed"
ERROR_BUDGET = "budget_exhausted"
ERROR_BLOCKED = "blocked"
ERROR_VERSION = "version_mismatch"
ERROR_UNSUPPORTED_CONTEXT = "unsupported_context"
ERROR_INVALID_OUTPUT = "invalid_output"
ERROR_EXCEPTION = "exception"


def canonical_json(obj: Any) -> str:
    """Serialization used for hashing and for the shared wire body.

    Key order is preserved on purpose: choice option order is an experimental variable (P5).
    """
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionRequest:
    scenario_id: str
    language: Language
    state: str | dict[str, Any] | list[str]
    questions: dict[str, dict[str, Any]]

    def shared_body(self) -> dict[str, Any]:
        """The provider-visible payload, identical for every provider."""
        return {"state": self.state, "questions": self.questions}

    def request_hash(self) -> str:
        return sha256_text(canonical_json(self.shared_body()))


@dataclass(frozen=True)
class DecisionResponse:
    provider: str
    resolved_model: str
    answers: dict[str, dict[str, Any]]
    wall_ms: float
    usage_input_tokens: int | None
    usage_output_tokens: int | None
    error: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


@runtime_checkable
class DecisionProvider(Protocol):
    # Model track name from configs/models.yaml, e.g. "laya_routed" or "jev_114".
    name: str

    async def evaluate(self, request: DecisionRequest) -> DecisionResponse: ...
