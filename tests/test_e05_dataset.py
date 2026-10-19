"""E05 generator contract: determinism, grouping, balance, EN/TH pairs and leakage."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from debench import SEED
from debench.dataset import e05
from debench.dataset.build import validate_specs
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import CONTEXT_LEVELS, SCENARIOS_PER_CASE, ScenarioRecord, ScenarioSpec
from debench.payload import build_request
from debench.questions import load_question_set


@pytest.fixture(scope="module")
def specs():
    return e05.generate_specs(SEED)


@pytest.fixture(scope="module")
def qset():
    return load_question_set("E05")


def test_exactly_300_specs_and_deterministic(specs):
    assert len(specs) == SCENARIOS_PER_CASE == 300
    again = e05.generate_specs(SEED)
    assert [(s.scenario_id, s.group_id, s.gold, s.facts) for s in specs] == [
        (s.scenario_id, s.group_id, s.gold, s.facts) for s in again
    ]
    assert e05.generate_specs(SEED + 1) != specs


def test_question_set_matches_plan(qset):
    assert qset.version == "e05-v1"
    q = qset.questions
    assert q["data_class"].labels() == [
        "direct_identifier", "quasi_identifier", "sensitive_personal", "non_personal", "unknown"
    ]  # fmt: skip
    assert q["handling_tier"].n_levels() == 4
    assert q["requires_masking_review"].primitive == "noul"
    for qs in q.values():
        assert len(qs.instructions["en"].split()) <= 14
    for lab in q["data_class"].labels():
        assert len(q["data_class"].criteria[lab]["en"].split()) <= 12


def test_specs_valid_and_grouped(specs, qset):
    assert validate_specs(specs, qset, 300) == []
    groups = Counter(s.group_id for s in specs)
    assert len(groups) >= 100 and max(groups.values()) <= 3 and min(groups.values()) >= 2


def test_groups_never_cross_splits(specs):
    assign_splits(specs, SEED)
    by_group = defaultdict(set)
    for s in specs:
        by_group[s.group_id].add(s.split)
    assert all(len(v) == 1 for v in by_group.values())
    counts = Counter(s.split for s in specs)
    assert sum(counts.values()) == 300 and set(counts) == {"dev", "calibration", "test"}
    assert counts["test"] >= 200 and counts["dev"] >= 58


def test_balance_sufficiency_and_hard_negatives(specs):
    n = len(specs)
    assert sum(s.evidence_sufficiency != "sufficient" for s in specs) >= 0.2 * n
    assert sum("hard_negative" in s.difficulty_tags for s in specs) >= 0.2 * n
    classes = Counter(s.gold["data_class"] for s in specs)
    assert set(classes) == set(load_question_set("E05").questions["data_class"].labels())
    assert min(classes.values()) >= 0.12 * n
    assert set(Counter(s.gold["handling_tier"] for s in specs)) == {0, 1, 2, 3}


def test_unknown_gold_iff_insufficient_and_multilabel_is_independent(specs):
    for s in specs:
        assert (s.gold["data_class"] == "unknown") == (s.evidence_sufficiency == "insufficient")
        assert "truth_flags" in s.facts["_internal"]
    # Some insufficient scenarios hide a real direct/quasi truth: single-label gold stays unknown.
    hidden = [s for s in specs if s.gold["data_class"] == "unknown" and s.facts["_internal"]["truth_flags"]]
    assert hidden


def test_field_defs_are_consistent():
    ids = [fd.field_id for fd in e05.FIELD_DEFS]
    assert len(set(ids)) == len(ids)
    cols = defaultdict(set)
    paths = defaultdict(list)
    for fd in e05.FIELD_DEFS:
        if fd.path:
            paths[fd.qualified_table].append(fd.path)
        else:
            assert fd.column not in cols[fd.qualified_table] | {"id", "created_at", "status", "row_version", "attrs"}
            cols[fd.qualified_table].add(fd.column)
    for ps in paths.values():
        for p in ps:
            assert not any(q != p and q[: len(p)] == p for q in ps), f"{p} is a prefix of another path"


def _record(s: ScenarioSpec, lang: str, state) -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id=s.scenario_id, experiment="E05", split="dev", seed=s.seed, language=lang, pair_id=s.scenario_id,
        group_id=s.group_id, state=state, evidence_ref={}, context_level="common_fit", questions_version="e05-v1",
        gold=s.gold, difficulty_tags=s.difficulty_tags, evidence_sufficiency=s.evidence_sufficiency,
        label_source=s.label_source, gold_verified=True, semantic_pair_validated=True,
    )  # fmt: skip


@pytest.mark.parametrize("level", CONTEXT_LEVELS)
def test_every_spec_renders_a_valid_pair_without_leakage(specs, qset, level):
    for s in specs:
        facts = e05.expected_evidence(s)
        assert e05.oracle_check(s, facts) == []
        en, th = (e05.render_state(s, facts, lang, level) for lang in ("en", "th"))
        assert validate_pair(en, th, e05.protected_tokens(s, facts)) == [], s.scenario_id
        assert list(en) == list(th)
        for lang, st in (("en", en), ("th", th)):
            build_request(_record(s, lang, st), qset)


def test_common_fit_is_compact(specs):
    for s in specs:
        en = e05.render_state(s, e05.expected_evidence(s), "en", "common_fit")
        words = " ".join(str(v) if not isinstance(v, list) else " ".join(v) for v in en.values()).split()
        assert len(words) <= 110


def test_baseline_returns_valid_answers_and_is_imperfect(specs):
    right = Counter()
    for s in specs:
        base = e05.rules_baseline(s, e05.expected_evidence(s))
        assert base["data_class"] in load_question_set("E05").questions["data_class"].labels()
        assert base["handling_tier"] in range(4) and isinstance(base["requires_masking_review"], bool)
        right[base["data_class"] == s.gold["data_class"]] += 1
    assert 0.3 < right[True] / len(specs) < 0.95
