"""Freeze question sets and datasets before any test-split run.

``python -m debench.freeze`` records each case's question-set hash and dataset content hash in
configs/prompts/FROZEN.json. The runner refuses test-split runs whose prompts or data differ.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from debench.config import CONFIG_DIR
from debench.dataset.build import GENERATED_DIR
from debench.questions import load_question_set

FROZEN_PATH = CONFIG_DIR / "prompts" / "FROZEN.json"
CASES = ("E01", "E02", "E03", "E04", "E05", "E06")


class NotFrozenError(RuntimeError):
    pass


def _dataset_hash(case: str, context_level: str) -> str | None:
    path = GENERATED_DIR / case.lower() / f"{context_level}.build.json"
    return json.loads(path.read_text())["content_sha256"] if path.exists() else None


def freeze(context_levels: tuple[str, ...] = ("common_fit", "natural_long")) -> dict[str, Any]:
    doc: dict[str, Any] = {"frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"), "cases": {}}
    for case in CASES:
        qset = load_question_set(case)
        doc["cases"][case] = {
            "questions_version": qset.version,
            "questions_hash": qset.content_hash,
            "datasets": {ctx: _dataset_hash(case, ctx) for ctx in context_levels},
        }
    FROZEN_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def check_frozen(case: str, context_level: str) -> None:
    if not FROZEN_PATH.exists():
        raise NotFrozenError("prompts are not frozen; run `python -m debench.freeze` after dev work")
    entry = json.loads(FROZEN_PATH.read_text())["cases"].get(case)
    if entry is None:
        raise NotFrozenError(f"{case} missing from FROZEN.json")
    if load_question_set(case).content_hash != entry["questions_hash"]:
        raise NotFrozenError(f"{case} question set changed after freezing")
    if _dataset_hash(case, context_level) != entry["datasets"].get(context_level):
        raise NotFrozenError(f"{case} {context_level} dataset changed after freezing")


def main() -> int:
    print(json.dumps(freeze(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
