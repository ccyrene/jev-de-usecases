from __future__ import annotations

import json

import pytest

from conftest import CASES, built_records_or_skip
from debench.payload import build_request
from debench.providers.base import canonical_json
from debench.questions import load_question_set

INTERNAL_FIELDS = ("gold", "label_source", "difficulty_tags", "group_id", "split", "seed", "protected_tokens")


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("qlang", ["en", "th"])
def test_provider_payload_has_no_gold_or_internal_fields(case, qlang):
    records = built_records_or_skip(case)
    qset = load_question_set(case)
    for rec in records:
        req = build_request(rec, qset, question_language=qlang)
        body = json.loads(canonical_json(req.shared_body()))
        assert set(body) == {"state", "questions"}
        if isinstance(body["state"], dict):
            assert not set(body["state"]) & set(INTERNAL_FIELDS)
        assert set(body["questions"]) == set(qset.questions)
        for q in body["questions"].values():
            assert set(q) <= {"type", "instructions", "criteria"}


@pytest.mark.parametrize("case", CASES)
def test_state_does_not_name_the_gold_option(case):
    records = built_records_or_skip(case)
    qset = load_question_set(case)
    for rec in records:
        text = canonical_json(rec.state).lower()
        for qid, q in qset.questions.items():
            if q.primitive == "choice" and "_" in rec.gold[qid]:
                assert rec.gold[qid].lower() not in text, (rec.scenario_id, qid)
