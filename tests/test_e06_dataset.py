"""E06 generator, rendering and pairing checks that need no database."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from debench import SEED
from debench.dataset import e06
from debench.dataset.build import validate_specs
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import SPLIT_SIZES, ScenarioRecord
from debench.evidence.duckdb_sql import PROVINCES, load_schema
from debench.payload import build_request
from debench.questions import load_question_set
from debench.sql_guard import check_sql


@pytest.fixture(scope="module")
def specs():
    return e06.generate_specs(SEED)


@pytest.fixture(scope="module")
def qset():
    return load_question_set("E06")


def test_exactly_300_specs_and_valid_gold(specs, qset) -> None:
    assert len(specs) == 300
    assert validate_specs(specs, qset, 300) == []
    assert [s.scenario_id for s in specs][:2] == ["E06-0001", "E06-0002"]


def test_generation_is_deterministic(specs) -> None:
    again = e06.generate_specs(SEED)
    assert [(s.scenario_id, s.group_id, s.facts["candidate_sql"], s.gold) for s in specs] == [
        (s.scenario_id, s.group_id, s.facts["candidate_sql"], s.gold) for s in again
    ]


def test_questions_follow_the_plan(qset) -> None:
    assert qset.version == "e06-v1"
    q = qset.questions
    assert q["intent_type"].labels() == ["aggregation", "filtered_lookup", "join_analysis", "time_series",
                                         "ranking", "unsupported_or_ambiguous"]
    assert q["candidate_verdict"].labels() == list(e06.VERDICTS)
    assert q["candidate_quality"].n_levels() == 5
    assert q["needs_clarification"].primitive == q["candidate_safe_to_consider"].primitive == "noul"
    for lang in ("en", "th"):
        for qid, spec in q.items():
            texts = [spec.instructions[lang]]
            if spec.primitive == "choice":
                texts += [spec.criteria[lab][lang] for lab in spec.labels()]
            elif spec.primitive == "score":
                texts += [lvl[lang] for lvl in spec.criteria]
            if lang == "en":
                assert all(len(t.split()) <= 12 for t in texts), qid


def test_groups_are_many_small_and_never_cross_splits(specs) -> None:
    groups = Counter(s.group_id for s in specs)
    assert len(groups) >= 100 and max(groups.values()) <= 3
    assign_splits(specs, SEED)
    seen: dict[str, set[str]] = defaultdict(set)
    for s in specs:
        seen[s.group_id].add(s.split or "")
    assert all(len(v) == 1 for v in seen.values())
    sizes = Counter(s.split for s in specs)
    assert sum(sizes.values()) == 300 and all(abs(sizes[k] - n) <= 2 for k, n in SPLIT_SIZES.items())


def test_class_balance_and_difficulty_quotas(specs) -> None:
    verdicts = Counter(s.gold["candidate_verdict"] for s in specs)
    assert set(verdicts) == set(e06.VERDICTS) and min(verdicts.values()) >= 45
    assert set(Counter(s.gold["intent_type"] for s in specs)) == set(
        load_question_set("E06").questions["intent_type"].labels())
    assert sum(s.evidence_sufficiency != "sufficient" for s in specs) >= 60
    assert sum("hard_negative" in s.difficulty_tags for s in specs) >= 60
    assert {s.gold["candidate_quality"] for s in specs} == {0, 1, 2, 3, 4}
    assert sum(s.gold["needs_clarification"] for s in specs) == sum(
        s.gold["candidate_verdict"] == "insufficient_specification" for s in specs)


def test_guard_decides_safe_to_consider_and_unsafe_gold(specs) -> None:
    tables = list(load_schema()["tables"])
    for s in specs:
        guard = check_sql(s.facts["candidate_sql"], tables)
        assert s.gold["candidate_safe_to_consider"] == guard.allowed, s.scenario_id
        assert (s.gold["candidate_verdict"] == "unsafe_or_unexecutable") == (not guard.allowed), s.scenario_id
        ref = s.facts["_internal"]["reference_sql"]
        if ref:
            assert check_sql(ref, tables).allowed
        for reading in s.facts["_internal"]["interps"]:
            assert check_sql(reading, tables).allowed


def test_candidates_within_a_group_are_distinct_and_never_the_reference(specs) -> None:
    by_group: dict[str, list[str]] = defaultdict(list)
    for s in specs:
        by_group[s.group_id].append(s.facts["candidate_sql"])
        assert s.facts["candidate_sql"] != s.facts["_internal"]["reference_sql"]
    assert all(len(set(v)) == len(v) for v in by_group.values())


def _render(spec, lang: str, level: str = "common_fit") -> dict:
    return e06.render_state(spec, e06.expected_evidence(spec), lang, level)


def test_every_spec_renders_a_valid_pair(specs) -> None:
    for s in specs:
        facts = e06.expected_evidence(s)
        en, th = (e06.render_state(s, facts, lang, "common_fit") for lang in ("en", "th"))
        assert validate_pair(en, th, e06.protected_tokens(s, facts)) == [], s.scenario_id
        assert list(en) == list(th) == ["request", "schema", "candidate_sql", "constraints"]
        assert en["candidate_sql"].encode() == th["candidate_sql"].encode()
        assert en["schema"] == th["schema"]


def test_natural_long_adds_dictionary_without_dropping_facts(specs) -> None:
    s = specs[0]
    short, long_ = _render(s, "th"), _render(s, "th", "natural_long")
    assert all(long_[k] == short[k] for k in short)
    assert len(long_["data_dictionary"]) > 10 and "context" in long_


def test_thai_province_names_are_data_values_kept_apart_from_labels(specs) -> None:
    prov_th = {en: th for en, th, _ in PROVINCES}
    hits = 0
    for s in specs:
        prov = s.facts["params"].get("province")
        if not prov:
            continue
        hits += 1
        th = _render(s, "th")
        en = _render(s, "en")
        assert prov_th[prov] in th["request"] and prov_th[prov] not in en["request"]
        assert prov_th[prov] not in th["candidate_sql"]
    assert hits >= 30


def test_state_is_compact_and_has_no_gold_or_execution_facts(specs) -> None:
    words = []
    for s in specs:
        state = _render(s, "en")
        words.append(sum(len(str(v).split()) for v in state.values()))
        blob = " ".join(str(v) for v in state.values()).lower()
        assert "semantically_" not in blob and "unsafe_or_unexecutable" not in blob
        assert "snapshot_result" not in blob and "_internal" not in blob
    words.sort()
    assert words[len(words) // 2] <= 200


def test_payload_builder_accepts_every_state_without_leakage(specs, qset) -> None:
    for s in specs[:60]:
        facts = e06.expected_evidence(s)
        for lang in ("en", "th"):
            rec = ScenarioRecord(
                scenario_id=s.scenario_id, experiment="E06", split="dev", seed=s.seed, language=lang,
                pair_id=s.scenario_id, group_id=s.group_id, state=e06.render_state(s, facts, lang, "common_fit"),
                evidence_ref={}, context_level="common_fit", questions_version=qset.version, gold=s.gold,
                difficulty_tags=s.difficulty_tags, evidence_sufficiency=s.evidence_sufficiency,
                label_source=s.label_source, gold_verified=True, semantic_pair_validated=True,
            )
            build_request(rec, qset)


def test_rules_baseline_uses_guard_and_keywords_only(specs) -> None:
    correct = 0
    for s in specs:
        out = e06.rules_baseline(s, e06.expected_evidence(s))
        assert set(out) == set(s.gold)
        if s.gold["candidate_verdict"] == "unsafe_or_unexecutable":
            assert out["candidate_verdict"] == "unsafe_or_unexecutable" and out["candidate_safe_to_consider"] is False
        correct += out["candidate_verdict"] == s.gold["candidate_verdict"]
    assert 0.5 < correct / len(specs) < 1.0  # useful but not an oracle
