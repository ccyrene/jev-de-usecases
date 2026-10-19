"""Internal scenario objects.

``ScenarioSpec`` is language-invariant and internal: it holds the injected fault, gold labels and
split. ``ScenarioRecord`` is one (scenario, language) row as stored in data/generated. Neither is
ever sent to a provider directly; ``debench.payload`` builds requests from an explicit allowlist.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from debench import SEED

Split = Literal["dev", "calibration", "test"]
SPLITS: tuple[str, ...] = ("dev", "calibration", "test")
SPLIT_SIZES: dict[str, int] = {"dev": 60, "calibration": 40, "test": 200}
SCENARIOS_PER_CASE = sum(SPLIT_SIZES.values())
EVIDENCE_SUFFICIENCY = ("sufficient", "ambiguous", "insufficient")
CONTEXT_LEVELS = ("common_fit", "natural_long", "equal_information")


@dataclass
class ScenarioSpec:
    scenario_id: str  # "E01-0001"
    experiment: str  # "E01"
    # Template / incident / schema / graph family. Splits are assigned per group so mutated copies
    # of one template never cross splits.
    group_id: str
    # Language-invariant machine facts used to seed the DB and render states. Anything under the
    # "_internal" key (fault ids, injection parameters) must never reach a rendered state.
    facts: dict[str, Any]
    gold: dict[str, Any]  # {question_id: label | level | bool}
    difficulty_tags: list[str]
    evidence_sufficiency: str
    label_source: str
    split: str | None = None
    seed: int = SEED


@dataclass
class ScenarioRecord:
    scenario_id: str
    experiment: str
    split: str
    seed: int
    language: str  # language of the rendered state
    pair_id: str
    group_id: str
    state: dict[str, Any] | str
    evidence_ref: dict[str, Any]
    context_level: str
    questions_version: str
    gold: dict[str, Any]
    difficulty_tags: list[str]
    evidence_sufficiency: str
    label_source: str
    gold_verified: bool
    semantic_pair_validated: bool
    protected_tokens: list[str] = field(default_factory=list)
    p4_subset: bool = False
    db_retrieval_ms: float | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScenarioRecord:
        return cls(**d)


def write_jsonl(path: Path, records: list[ScenarioRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec.to_json() + "\n")


def read_jsonl(path: Path) -> list[ScenarioRecord]:
    with path.open(encoding="utf-8") as fh:
        return [ScenarioRecord.from_dict(json.loads(line)) for line in fh if line.strip()]
