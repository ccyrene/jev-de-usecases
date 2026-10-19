from __future__ import annotations

import json
from collections import Counter

import pytest

from debench import SEED
from debench.dataset import e01
from debench.dataset.build import validate_specs
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import ScenarioRecord
from debench.payload import build_request
from debench.questions import load_question_set


@pytest.fixture(scope="module")
def specs():
    return e01.generate_specs(SEED)


@pytest.fixture(scope="module")
def qset():
    return load_question_set("E01")


def test_exactly_300_deterministic(specs):
    again = e01.generate_specs(SEED)
    assert len(specs) == 300
    assert [s.scenario_id for s in specs] == [f"E01-{i:04d}" for i in range(1, 301)]
    assert json.dumps([s.facts for s in specs], sort_keys=True) == json.dumps([s.facts for s in again], sort_keys=True)
    assert [s.gold for s in specs] == [s.gold for s in again]
    other = e01.generate_specs(SEED + 1)
    assert [s.gold for s in other] != [s.gold for s in specs]


def test_specs_valid_against_questions(specs, qset):
    assert qset.version == "e01-v1"
    assert validate_specs(specs, qset, 300) == []


def test_groups_never_cross_splits(specs):
    assert len({s.group_id for s in specs}) >= 100
    sizes = Counter(s.group_id for s in specs)
    assert max(sizes.values()) <= 3
    assign_splits(specs, SEED)
    by_group: dict[str, set[str]] = {}
    for s in specs:
        by_group.setdefault(s.group_id, set()).add(s.split)
    assert all(len(v) == 1 for v in by_group.values())
    assert Counter(s.split for s in specs) == {"dev": 60, "calibration": 40, "test": 200}


def test_class_coverage_and_rates(specs):
    fam = Counter(s.gold["failure_family"] for s in specs)
    assert set(fam) == set(load_question_set("E01").questions["failure_family"].labels())
    assert min(fam.values()) >= 30
    assert {s.gold["severity"] for s in specs} == {0, 1, 2, 3}
    assert {s.gold["requires_human"] for s in specs} == {True, False}
    hard = sum("hard_negative" in s.difficulty_tags for s in specs)
    unsure = sum(s.evidence_sufficiency != "sufficient" for s in specs)
    assert hard >= 60 and unsure >= 60
    # other_unknown gold only where the evidence is insufficient to disambiguate
    for s in specs:
        if s.gold["failure_family"] == "other_unknown":
            assert s.evidence_sufficiency == "insufficient"
        else:
            assert s.evidence_sufficiency != "insufficient"


def test_oracle_passes_on_reference_evidence(specs):
    bad = {s.scenario_id: e01.oracle_check(s, e01.expected_evidence(s)) for s in specs}
    assert {k: v for k, v in bad.items() if v} == {}


@pytest.mark.parametrize("level", ["common_fit", "natural_long", "equal_information"])
def test_every_spec_renders_valid_pair_without_leakage(specs, qset, level):
    for s in specs:
        facts = e01.expected_evidence(s)
        states = {lang: e01.render_state(s, facts, lang, level) for lang in ("en", "th")}
        assert validate_pair(states["en"], states["th"], e01.protected_tokens(s, facts)) == [], s.scenario_id
        for lang, state in states.items():
            rec = ScenarioRecord(
                scenario_id=s.scenario_id, experiment="E01", split="dev", seed=SEED, language=lang,
                pair_id=s.scenario_id, group_id=s.group_id, state=state, evidence_ref={}, context_level=level,
                questions_version="e01-v1", gold=s.gold, difficulty_tags=s.difficulty_tags,
                evidence_sufficiency=s.evidence_sufficiency, label_source=s.label_source,
                gold_verified=True, semantic_pair_validated=True,
            )
            build_request(rec, qset)  # raises on leakage


def test_common_fit_is_compact(specs):
    words = []
    for s in specs:
        state = e01.render_state(s, e01.expected_evidence(s), "en", "common_fit")
        words.append(len(" ".join(map(str, state.values())).split()))
    assert max(words) <= 110


def test_seed_payload_size_and_no_internal_ids(specs):
    payload = e01.db_seed(specs, SEED)
    docs = payload["docs"]
    assert len(docs) >= 10_000
    assert len({d["_id"] for d in docs}) == len(docs)
    allowed = {"event_ts", "dag_id", "task_id", "run_id", "status", "message", "exception_type",
               "retry_count", "duration_ms", "upstream_task", "deployment_id"}
    assert all(set(d["_source"]) == allowed for d in docs)
    blob = json.dumps(docs).lower()
    for marker in ("fault-", "_internal", "gold", "schema_mismatch", "other_unknown", "failure_family"):
        assert marker not in blob
    assert len({s.facts["run_id"] for s in specs}) == 300


def test_rules_baseline_wins_where_unambiguous_and_abstains_otherwise(specs):
    unsure_kinds = e01.AMBIGUOUS_KINDS | e01.INSUFFICIENT_KINDS
    n_abstain = 0
    for s in specs:
        out = e01.rules_baseline(s, e01.expected_evidence(s))
        kind = s.facts["_internal"]["kind"]
        if out["failure_family"] is not None:
            assert out["failure_family"] == s.gold["failure_family"], (s.scenario_id, kind)
        elif kind not in e01.INSUFFICIENT_KINDS:
            raise AssertionError(f"baseline abstained on resolvable {kind} {s.scenario_id}")
        if kind in e01.INSUFFICIENT_KINDS:
            assert out["failure_family"] is None
            n_abstain += 1
        assert out["severity"] == s.gold["severity"]
        if out["requires_human"] is not None:
            assert out["requires_human"] == s.gold["requires_human"]
        assert kind in unsure_kinds or out["failure_family"] == s.gold["failure_family"]
    assert n_abstain == sum(1 for s in specs if s.facts["_internal"]["kind"] in e01.INSUFFICIENT_KINDS)
