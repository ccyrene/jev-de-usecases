"""E04 generator, graph shape, splits, rendering and baseline (no live engine needed)."""

from __future__ import annotations

import shutil
from collections import Counter

import networkx as nx
import pytest

from debench import SEED
from debench.config import ROOT
from debench.dataset import e04
from debench.dataset.build import build_case, validate_specs
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import SCENARIOS_PER_CASE, SPLITS
from debench.evidence.neo4j_lineage import LINEAGE_RELS
from debench.questions import load_question_set


@pytest.fixture(scope="module")
def specs():
    return e04.generate_specs(SEED)


def test_deterministic_and_exactly_300(specs):
    again = e04.generate_specs(SEED)
    assert len(specs) == SCENARIOS_PER_CASE == 300
    assert [(s.scenario_id, s.group_id, s.facts["asset_id"], s.gold) for s in specs] == [
        (s.scenario_id, s.group_id, s.facts["asset_id"], s.gold) for s in again
    ]
    assert e04.generate_specs(SEED + 1) != specs
    assert [s.scenario_id for s in specs][:2] == ["E04-0001", "E04-0002"]


def test_graph_size_labels_and_required_features():
    g = e04.build_graph(SEED)
    assets = [n for n in g.nodes if "Owner" not in n["labels"]]
    lineage = [e for e in g.edges if e["type"] in LINEAGE_RELS]
    assert len(g.nodes) >= 500 and len(lineage) >= 1500
    assert {l for n in g.nodes for l in n["labels"]} >= {
        "SourceTable", "Transformation", "DAG", "Dataset", "Dashboard", "CriticalBusinessProcess", "Owner"}
    assert len({n["props"]["asset_id"] for n in g.nodes}) == len(g.nodes)
    for n in assets:
        assert {"asset_id", "domain", "criticality", "freshness_sla", "description_en", "description_th"} <= set(n["props"])
    assert {e["status"] for e in lineage} == {"active", "stale", "pending"}
    assert any("owner_team" not in n["props"] for n in assets)  # missing owners
    assert any("|" in n["props"].get("owner_team", "") for n in assets)  # ambiguous ownership
    flow = nx.DiGraph()
    flow.add_nodes_from(n["props"]["asset_id"] for n in assets)
    for e in lineage:
        up, down = (e["src"], e["dst"]) if e["type"] == "PRODUCES" else (e["dst"], e["src"])
        flow.add_edge(up, down)
    assert not nx.is_directed_acyclic_graph(flow)  # cycles exist
    assert any(flow.degree(n) == 0 for n in flow)  # isolated nodes
    assert len({d["domain"] for d in (n["props"] for n in assets)}) <= 8


def test_groups_never_cross_splits_and_are_many(specs):
    assert len({s.group_id for s in specs}) >= 100
    assert max(Counter(s.group_id for s in specs).values()) <= 3
    assign_splits(specs, SEED)
    split_of: dict[str, set[str]] = {}
    for s in specs:
        split_of.setdefault(s.group_id, set()).add(s.split)
    assert all(len(v) == 1 for v in split_of.values())
    assert set(Counter(s.split for s in specs)) == set(SPLITS)


def test_specs_valid_balanced_and_sufficiency_labelled(specs):
    qset = load_question_set("E04")
    assert qset.version == "e04-v1"
    assert validate_specs(specs, qset, 300) == []
    assert Counter(s.gold["impact_tier"] for s in specs) == {t: 60 for t in e04.TIERS}
    assert sum(s.evidence_sufficiency in ("ambiguous", "insufficient") for s in specs) >= 60
    assert sum("hard_negative" in s.difficulty_tags for s in specs) >= 60
    assert all(s.evidence_sufficiency == "insufficient" for s in specs if s.gold["impact_tier"] == "unknown")
    assert {s.gold["needs_owner_escalation"] for s in specs} == {True, False}
    assert {s.gold["impact_severity"] for s in specs} == {0, 1, 2, 3, 4}


def test_question_set_matches_plan():
    qset = load_question_set("E04")
    assert qset.questions["impact_tier"].labels() == [
        "none", "single_noncritical", "multi_noncritical", "critical_downstream", "unknown"]
    assert qset.questions["impact_severity"].n_levels() == 5
    assert qset.questions["needs_owner_escalation"].primitive == "noul"


def test_reference_policy_reproduces_gold_and_baseline(specs):
    for s in specs:
        facts = e04.expected_evidence(s)
        assert e04.rules_baseline(s, facts) == s.gold
        assert e04.sufficiency(facts, s.gold["impact_tier"]) == s.evidence_sufficiency


def test_every_spec_renders_valid_pair_in_all_contexts(specs):
    for level in ("common_fit", "natural_long", "equal_information"):
        for s in specs:
            facts = e04.expected_evidence(s)
            en, th = (e04.render_state(s, facts, lang, level) for lang in ("en", "th"))
            assert validate_pair(en, th, e04.protected_tokens(s, facts)) == [], (level, s.scenario_id)
            assert "_internal" not in en and list(en) == list(th)


def test_common_fit_states_are_compact(specs):
    from debench.dataset.paired_generator import state_text

    words = sorted(len(state_text(e04.render_state(s, e04.expected_evidence(s), "en", "common_fit")).split()) for s in specs)
    assert words[len(words) // 2] <= 100 and words[-1] <= 140


def test_gold_critical_clue_never_clipped(specs):
    for s in specs:
        facts = e04.expected_evidence(s)
        shown = e04.render_state(s, facts, "en", "common_fit")["affected_consumers"]
        for cid in facts["critical_consumers"][:1]:
            assert any(line.startswith(cid) for line in shown)


def test_expected_mode_build_has_no_leakage_or_pair_failures():
    # build_case needs an output dir inside the repo (it stores a repo-relative path).
    out = ROOT / "data" / "tmp_test_e04"
    try:
        report = build_case("E04", evidence_mode="expected", out_dir=out)
        assert report.pair_failures == {} and report.records == 600
    finally:
        shutil.rmtree(out, ignore_errors=True)
