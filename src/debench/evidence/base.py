"""Evidence backends: a real database engine seeded with synthetic data and queried with a fixed,
provider-independent template. DB retrieval latency is measured here and never mixed into model
latency."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from debench.dataset.schema import ScenarioSpec

# How the engine is run. Recorded in every manifest so a substitution is never silent.
RUNTIME_DOCKER = "docker"
RUNTIME_NATIVE = "native_tarball"
RUNTIME_EMBEDDED = "embedded"


class BackendUnavailable(RuntimeError):
    """Engine cannot start here (missing Docker/Java/binary). Callers mark the case BLOCKED."""


@dataclass(frozen=True)
class Evidence:
    facts: dict[str, Any]  # language-invariant facts retrieved from the DB
    ref: dict[str, Any]  # evidence_ref, e.g. {"backend": "opensearch", "document_ids": [...]}
    db_ms: float
    extra: dict[str, Any] = field(default_factory=dict)


class EvidenceBackend(Protocol):
    name: str  # "opensearch", "clickhouse", "postgres", "neo4j", "duckdb"
    runtime: str  # one of the RUNTIME_* constants

    def engine_version(self) -> str: ...

    def setup(self) -> None:
        """Start or connect; raise BackendUnavailable if the engine cannot run."""

    def load(self, seed_payload: Any) -> None:
        """Reset to empty, then load the case's synthetic seed payload."""

    def retrieve(self, spec: ScenarioSpec) -> Evidence: ...

    def teardown(self) -> None: ...


@contextmanager
def timed() -> Iterator[dict[str, float]]:
    out: dict[str, float] = {}
    start = time.perf_counter()
    try:
        yield out
    finally:
        out["ms"] = (time.perf_counter() - start) * 1000.0
