"""Versioned bilingual question sets loaded from configs/prompts/eXX.yaml.

YAML layout (option ids are stable ASCII and never translated; score levels are 0-based and
never reordered):

    case: E01
    version: e01-v1
    questions:
      failure_family:
        type: choice
        instructions: {en: "...", th: "..."}
        criteria:
          schema_mismatch: {en: "...", th: "..."}
      severity:
        type: score
        instructions: {en: "...", th: "..."}
        criteria:
          - {en: "...", th: "..."}      # level 0
      requires_human:
        type: noul
        instructions: {en: "...", th: "..."}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from debench.providers.base import PRIMITIVES, canonical_json, sha256_text

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "configs" / "prompts"
LANGUAGES = ("en", "th")


@dataclass(frozen=True)
class QuestionSpec:
    qid: str
    primitive: str
    instructions: dict[str, str]
    # choice: {label: {lang: text}}, score: [{lang: text}, ...], noul: {"true"|"false": {lang: text}} | None
    criteria: Any

    def labels(self) -> list[str]:
        if self.primitive != "choice":
            raise TypeError(f"{self.qid} is not a choice question")
        return list(self.criteria.keys())

    def n_levels(self) -> int:
        if self.primitive != "score":
            raise TypeError(f"{self.qid} is not a score question")
        return len(self.criteria)

    def render(self, lang: str, option_order: list[str] | None = None) -> dict[str, Any]:
        q: dict[str, Any] = {"type": self.primitive, "instructions": self.instructions[lang]}
        if self.primitive == "choice":
            order = option_order or self.labels()
            if sorted(order) != sorted(self.labels()):
                raise ValueError(f"{self.qid}: option_order must be a permutation of the labels")
            q["criteria"] = {lab: self.criteria[lab][lang] for lab in order}
        elif self.primitive == "score":
            if option_order is not None:
                raise ValueError("score criteria are ordinal and must never be permuted")
            q["criteria"] = [level[lang] for level in self.criteria]
        elif self.criteria is not None:
            q["criteria"] = {k: self.criteria[k][lang] for k in ("true", "false")}
        return q


@dataclass(frozen=True)
class QuestionSet:
    case: str
    version: str
    questions: dict[str, QuestionSpec]
    content_hash: str

    def render(
        self,
        lang: str,
        option_orders: dict[str, list[str]] | None = None,
        only: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        option_orders = option_orders or {}
        qids = only or list(self.questions)
        return {qid: self.questions[qid].render(lang, option_orders.get(qid)) for qid in qids}


def _localized(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(LANGUAGES):
        raise ValueError(f"{where}: needs exactly {LANGUAGES} translations")
    for lang, text in value.items():
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}.{lang}: empty text")
    return value


def _parse_question(qid: str, body: dict[str, Any]) -> QuestionSpec:
    primitive = body.get("type")
    if primitive not in PRIMITIVES:
        raise ValueError(f"{qid}: unknown type {primitive!r}")
    instructions = _localized(body.get("instructions"), f"{qid}.instructions")
    raw = body.get("criteria")
    if primitive == "choice":
        if not isinstance(raw, dict) or len(raw) < 2:
            raise ValueError(f"{qid}: choice needs >= 2 options")
        criteria = {}
        for label, text in raw.items():
            if not (isinstance(label, str) and label.isascii() and label.strip() == label and label):
                raise ValueError(f"{qid}: option id {label!r} must be non-empty ASCII")
            criteria[label] = _localized(text, f"{qid}.{label}")
    elif primitive == "score":
        if not isinstance(raw, list) or not 2 <= len(raw) <= 10:
            raise ValueError(f"{qid}: score needs 2..10 ordered levels")
        criteria = [_localized(t, f"{qid}[{i}]") for i, t in enumerate(raw)]
    else:
        criteria = None
        if raw is not None:
            # YAML 1.1 parses bare true/false keys as booleans.
            raw = {str(k).lower(): v for k, v in raw.items()}
            if set(raw) != {"true", "false"}:
                raise ValueError(f"{qid}: noul criteria keys must be true/false")
            criteria = {k: _localized(v, f"{qid}.{k}") for k, v in raw.items()}
    return QuestionSpec(qid=qid, primitive=primitive, instructions=instructions, criteria=criteria)


def load_question_set(case: str, prompts_dir: Path = PROMPTS_DIR) -> QuestionSet:
    path = prompts_dir / f"{case.lower()}.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc.get("case") != case.upper():
        raise ValueError(f"{path}: case field must be {case.upper()}")
    questions = {qid: _parse_question(qid, body) for qid, body in doc["questions"].items()}
    content_hash = sha256_text(canonical_json(doc))
    return QuestionSet(case=doc["case"], version=doc["version"], questions=questions, content_hash=content_hash)
