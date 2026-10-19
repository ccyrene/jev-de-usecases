"""E02 spec generation, grouping, rendering and pandas reference vs gold."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest
from test_e02_common import get_specs

from debench import SEED
from debench.dataset import e02
from debench.dataset.build import validate_specs
from debench.dataset.paired_generator import assign_splits, scenario_id, validate_pair
from debench.dataset.schema import SPLITS
from debench.payload import FORBIDDEN_MARKERS
from debench.questions import load_question_set

QSET = load_question_set("E02")


def test_questions_follow_the_plan():
    assert QSET.version == "e02-v1"
    q = QSET.questions
    assert q["anomaly_family"].labels() == list(e02.FAMILIES)
    assert q["quality_impact"].n_levels() == 5 and q["investigate_now"].primitive == "noul"


def test_exactly_300_valid_specs_with_ids():
    specs = get_specs()
    assert validate_specs(specs, QSET, 300) == []
    assert [s.scenario_id for s in specs] == [scenario_id("E02", i) for i in range(1, 301)]


def test_generation_is_deterministic():
    # Re-drawing a member from the same arguments gives the identical spec.
    s = get_specs()[0]
    i = s.facts["_internal"]
    p = i["params"]
    tmpl = {
        "dataset": p["dataset_id"].removesuffix("_clean"),
        "tier": p["catalog"]["tier"],
        "jobs": p["catalog"]["dependent_jobs"],
        "critical": p["catalog"]["downstream_critical"],
        "sla": p["catalog"]["freshness_sla_min"],
        "completeness": p["catalog"]["completeness_sla"],
        "base_rate": p["base_rate"],
        "base_mean": p["base_mean"],
    }
    fam = {k: f for k, _, f in e02.KIND_PLAN}[i["kind"]]
    again = e02._make_member(SEED, i["kind"], i["variant"], fam, p["uid"], tmpl, s.group_id)
    assert again.facts["_internal"]["params"] == p and again.gold == s.gold


def test_groups_are_small_numerous_and_never_cross_splits():
    specs = get_specs()
    sizes = Counter(s.group_id for s in specs)
    assert len(sizes) >= 100 and max(sizes.values()) <= 3
    assign_splits(specs, SEED)
    by_group = defaultdict(set)
    for s in specs:
        by_group[s.group_id].add(s.split)
    assert all(len(v) == 1 for v in by_group.values())
    assert {s.split for s in specs} == set(SPLITS)


def test_class_coverage_and_ambiguity_quota():
    specs = get_specs()
    fams = Counter(s.gold["anomaly_family"] for s in specs)
    assert set(fams) == set(e02.FAMILIES) and min(fams.values()) >= 30
    assert set(Counter(s.gold["quality_impact"] for s in specs)) == {0, 1, 2, 3, 4}
    inv = Counter(s.gold["investigate_now"] for s in specs)
    assert min(inv.values()) >= 60
    weak = sum(s.evidence_sufficiency != "sufficient" for s in specs)
    hard = sum("hard_negative" in s.difficulty_tags for s in specs)
    assert weak >= 60 and hard >= 60
    assert all(isinstance(s.facts["_internal"]["threshold_decidable"], bool) for s in specs)


def test_all_scenario_kinds_present():
    kinds = Counter(s.facts["_internal"]["kind"] for s in get_specs())
    assert kinds == Counter({k: n for k, n, _ in e02.KIND_PLAN})


@pytest.fixture(scope="module")
def reference():
    return {s.scenario_id: e02.expected_evidence(s) for s in get_specs()}


def test_reference_facts_satisfy_the_oracle_and_the_policy(reference):
    for s in get_specs():
        assert e02.oracle_check(s, reference[s.scenario_id]) == [], s.scenario_id


def test_every_spec_renders_a_valid_leak_free_pair(reference):
    for s in get_specs():
        facts = reference[s.scenario_id]
        for level in ("common_fit", "natural_long"):
            en, th = (e02.render_state(s, facts, lang, level) for lang in ("en", "th"))
            assert validate_pair(en, th, e02.protected_tokens(s, facts)) == [], s.scenario_id
            text = " ".join(map(str, en.values())).lower()
            assert not any(m in text for m in FORBIDDEN_MARKERS)
            assert not any(f in text for f in e02.FAMILIES if "_" in f)


def test_common_fit_state_is_compact(reference):
    words = [
        len(" ".join(e02.render_state(s, reference[s.scenario_id], "en", "common_fit").values()).split())
        for s in get_specs()
    ]
    assert max(words) <= 110


def test_rules_baseline_is_exact_where_flagged_decidable(reference):
    decidable = 0
    for s in get_specs():
        facts = reference[s.scenario_id]
        out = e02.rules_baseline(s, facts)
        assert set(out) == set(s.gold)
        if facts["_internal"]["threshold_decidable"]:
            decidable += 1
            assert out == s.gold
    assert 0.5 < decidable / 300 < 0.95


def test_seed_data_has_over_100k_events():
    seed = e02.db_seed(get_specs(), SEED)
    assert len(seed["pipeline_events"]) >= 100_000
    assert len(seed["pipeline_events_raw"]) >= len(seed["pipeline_events"])
