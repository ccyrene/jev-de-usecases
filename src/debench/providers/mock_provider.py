"""Deterministic pseudo-random provider. CI only: never a measured model.

Distributions are derived from ``(seed, request_hash, question id)`` and carry no information
about the state, so any score computed from them is meaningless. Every response is marked
``metadata["mock"] = True`` and final aggregation must reject the ``mock`` track.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from debench.providers.base import DecisionRequest, DecisionResponse


def _rng(seed: int, request_hash: str, qid: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}|{request_hash}|{qid}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _distribution(rng: random.Random, n: int) -> list[float]:
    raw = [rng.expovariate(1.0) + 1e-6 for _ in range(n)]
    total = sum(raw)
    return [x / total for x in raw]


class MockProvider:
    name = "mock"

    def __init__(self, seed: int = 0):
        self.seed = int(seed)

    def _answer(self, rng: random.Random, question: dict[str, Any]) -> dict[str, Any]:
        kind = question["type"]
        if kind == "choice":
            labels = list(question["criteria"])
            probs = _distribution(rng, len(labels))
            top = max(range(len(labels)), key=lambda i: probs[i])
            n = len(labels)
            return {
                "type": "choice",
                "choice": labels[top],
                "probabilities": dict(zip(labels, probs, strict=True)),
                "confidence": (probs[top] - 1 / n) / (1 - 1 / n),
            }
        if kind == "score":
            k = len(question["criteria"])
            probs = _distribution(rng, k)
            return {
                "type": "score",
                "score": sum(i * p for i, p in enumerate(probs)),
                "legend": {str(i): str(d) for i, d in enumerate(question["criteria"])},
                "probabilities": {str(i): p for i, p in enumerate(probs)},
                "confidence": 0.0,
            }
        return {"type": "noul", "noul": rng.random()}

    async def evaluate(self, request: DecisionRequest) -> DecisionResponse:
        h = request.request_hash()
        answers = {qid: self._answer(_rng(self.seed, h, qid), q) for qid, q in request.questions.items()}
        return DecisionResponse(
            provider=self.name,
            resolved_model="mock",
            answers=answers,
            wall_ms=0.0,
            usage_input_tokens=None,
            usage_output_tokens=None,
            error=None,
            metadata={"mock": True},
        )
