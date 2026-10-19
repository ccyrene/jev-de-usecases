"""E04 reference traversal, direction semantics and policy on hand-checked graphs; live Cypher oracle."""

from __future__ import annotations

import pytest

from debench.config import load_settings
from debench.dataset import e04
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import BackendUnavailable
from debench.evidence.neo4j_lineage import MAX_HOPS, Neo4jLineageBackend, read_query


def _node(asset_id: str, kind: str, crit: str = "low", domain: str = "finance", sla: int = 1440, **extra):
    props = {"asset_id": asset_id, "domain": domain, "criticality": crit, "freshness_sla": sla,
             "description_en": f"{asset_id} en", "description_th": f"{asset_id} th"}
    props.update(extra)
    return {"labels": ["Asset", kind], "props": props}


def _edge(src: str, dst: str, rel: str, status: str = "active"):
    return {"src": src, "dst": dst, "type": rel, "status": status}


def tiny_payload() -> dict:
    """
    Flow (upstream -> downstream), edges are stored in their own direction:

        s1 -> t1 -> d1 -> d2 -> dash1(critical, 30m)        t1 CONSUMES s1, t1 PRODUCES d1
                   d1 -> d3 -> dash1                         d2/d3 DEPENDS_ON d1, dash1 CONSUMES d2/d3 (diamond)
                   d3 -> t2 -> d1                            cycle d1 -> d3 -> t2 -> d1
                   d1 -> dash_old(high)  [stale edge]        never traversed
        iso                                                  isolated dataset
    """
    nodes = [
        _node("s1", "SourceTable"), _node("t1", "Transformation"), _node("t2", "Transformation"),
        _node("d1", "Dataset"), _node("d2", "Dataset", "medium", "sales"), _node("d3", "Dataset", "low"),
        _node("dash1", "Dashboard", "critical", "finance", 30), _node("dash_old", "Dashboard", "high"),
        _node("iso", "Dataset"),
        {"labels": ["Owner"], "props": {"asset_id": "own_fin", "domain": "finance", "owner_team": "finance_bi"}},
        {"labels": ["Owner"], "props": {"asset_id": "own_sales", "domain": "sales", "owner_team": "sales_bi"}},
    ]
    edges = [
        _edge("t1", "s1", "CONSUMES"), _edge("t1", "d1", "PRODUCES"),
        _edge("d2", "d1", "DEPENDS_ON"), _edge("d3", "d1", "DEPENDS_ON"),
        _edge("dash1", "d2", "CONSUMES"), _edge("dash1", "d3", "CONSUMES"),
        _edge("t2", "d3", "CONSUMES"), _edge("t2", "d1", "PRODUCES"),
        _edge("dash_old", "d1", "CONSUMES", "stale"),
        _edge("dash1", "own_fin", "OWNED_BY"), _edge("d2", "own_sales", "OWNED_BY"),
        _edge("d1", "own_fin", "OWNED_BY"), _edge("d3", "own_fin", "OWNED_BY"), _edge("d3", "own_sales", "OWNED_BY"),
    ]
    return {"nodes": nodes, "edges": edges}


def _ids(facts: dict) -> list[str]:
    return [c["asset_id"] for c in facts["consumers"]]


def test_downstream_direction_and_hops_hand_checked():
    facts = e04.reference_facts(tiny_payload(), "d1")
    # d2, d3 one step; dash1 two steps; t2 (a job) is traversed but is not a consumer.
    assert [(c["asset_id"], c["hops"]) for c in facts["consumers"]] == [("d2", 1), ("d3", 1), ("dash1", 2)]
    assert facts["n_consumers"] == 3 and facts["min_hops"] == 1
    assert facts["critical_consumers"] == ["dash1"]
    assert facts["owner_domains"] == ["finance", "sales"]
    assert facts["in_cycle"] is True
    assert "dash_old" not in _ids(facts)  # stale edge is never traversed


def test_upstream_is_not_downstream():
    facts = e04.reference_facts(tiny_payload(), "dash1")
    assert facts["n_consumers"] == 0 and facts["min_hops"] is None
    assert e04.reference_facts(tiny_payload(), "iso")["n_consumers"] == 0


def test_source_reaches_through_job_and_diamond_without_double_counting():
    facts = e04.reference_facts(tiny_payload(), "s1")
    assert [(c["asset_id"], c["hops"]) for c in facts["consumers"]] == [("d1", 2), ("d2", 3), ("d3", 3), ("dash1", 4)]
    assert facts["n_consumers"] == 4  # dash1 is reachable by two paths but counted once


def test_ownership_states_and_excerpt_order():
    facts = e04.reference_facts(tiny_payload(), "d1")
    by_id = {c["asset_id"]: c for c in facts["consumers"]}
    assert by_id["d3"]["ownership"] == "ambiguous" and by_id["d3"]["owner_domains"] == ["finance", "sales"]
    assert by_id["dash1"]["ownership"] == "clear"
    assert [(e["up"], e["down"], e["status"]) for e in facts["excerpt"]] == [
        ("d1", "d2", "active"), ("d1", "d3", "active"), ("d1", "dash_old", "stale"),
        ("d2", "dash1", "active"), ("d3", "dash1", "active"), ("d3", "t2", "active"),
    ]


def test_hop_bound_truncates_long_chains():
    nodes = [_node(f"c{i}", "Dataset") for i in range(MAX_HOPS + 4)]
    edges = [_edge(f"c{i + 1}", f"c{i}", "DEPENDS_ON") for i in range(MAX_HOPS + 3)]
    facts = e04.reference_facts({"nodes": nodes, "edges": edges}, "c0")
    assert facts["n_consumers"] == MAX_HOPS and facts["consumers"][-1]["hops"] == MAX_HOPS


def test_pending_edge_counts_only_from_reached_assets():
    p = tiny_payload()
    p["nodes"] += [_node("dash_new", "Dashboard", "critical"), _node("far", "Dataset")]
    p["edges"] += [_edge("dash_new", "d2", "CONSUMES", "pending"), _edge("dash_new", "far", "CONSUMES", "pending")]
    assert e04.reference_facts(p, "d1")["pending_edges"] == 1
    assert e04.reference_facts(p, "iso")["pending_edges"] == 0


def test_cypher_templates_are_fully_substituted():
    for name in ("10_oracle_downstream.cypher", "11_oracle_pending.cypher", "12_oracle_excerpt.cypher", "14_oracle_cycle.cypher"):
        assert "__" not in read_query(name)
    assert f"{{1,{MAX_HOPS}}}" in read_query("10_oracle_downstream.cypher")


# --- policy -------------------------------------------------------------------------------


def _fx(consumers=(), asset=None, pending=0):
    base = {"asset_id": "a", "domain": "sales", "criticality": "low", "lineage_status": "complete", "metadata_status": "current",
            "ownership": "clear"}
    base.update(asset or {})
    cons = [{"asset_id": f"c{i}", "owner_domains": ["sales"], "ownership": "clear", "metadata_status": "current",
             "freshness_sla": 1440, "hops": 1, **c} for i, c in enumerate(consumers)]
    return {"asset": base, "consumers": cons, "pending_edges": pending,
            "critical_consumers": [c["asset_id"] for c in cons if c["criticality"] in ("high", "critical")]}


def test_policy_low_value_table_feeding_executive_dashboard_is_critical():
    out = e04.decide(_fx([{"criticality": "critical", "freshness_sla": 15}]))
    assert out == {"impact_tier": "critical_downstream", "impact_severity": 4, "needs_owner_escalation": True}


def test_policy_no_consumers_clear_owner_is_none_and_no_escalation():
    assert e04.decide(_fx()) == {"impact_tier": "none", "impact_severity": 0, "needs_owner_escalation": False}


def test_policy_lineage_gap_is_unknown_but_critical_evidence_still_wins():
    assert e04.decide(_fx(asset={"lineage_status": "partial"}))["impact_tier"] == "unknown"
    assert e04.decide(_fx(pending=1))["impact_severity"] == 1
    assert e04.decide(_fx([{"criticality": "high"}], pending=1))["impact_tier"] == "critical_downstream"


def test_policy_high_with_loose_sla_is_severity_three_and_tight_sla_is_four():
    assert e04.decide(_fx([{"criticality": "high"}]))["impact_severity"] == 3
    assert e04.decide(_fx([{"criticality": "high", "freshness_sla": 60}]))["impact_severity"] == 4


def test_policy_noncritical_tiers_and_ownership_escalation():
    one = e04.decide(_fx([{"criticality": "medium"}]))
    assert one == {"impact_tier": "single_noncritical", "impact_severity": 2, "needs_owner_escalation": False}
    many = e04.decide(_fx([{"criticality": "low"}, {"criticality": "low"}]))
    assert many["impact_tier"] == "multi_noncritical" and many["impact_severity"] == 1
    assert e04.decide(_fx([{"criticality": "low"}], asset={"ownership": "missing"}))["needs_owner_escalation"] is True
    cross = e04.decide(_fx([{"criticality": "medium", "owner_domains": ["risk"]}]))
    assert cross["needs_owner_escalation"] is True
    # a low-criticality consumer in another domain does not force escalation
    assert e04.decide(_fx([{"criticality": "low", "owner_domains": ["risk"]}]))["needs_owner_escalation"] is False


def test_policy_breadth_bumps_low_consumers_to_medium():
    assert e04.decide(_fx([{"criticality": "low"}] * 6))["impact_severity"] == 2


# --- live Cypher oracle ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def backend(monkeypatch_module):
    monkeypatch_module.setenv("DEBENCH_NO_AUTOSTART", "1")
    be = Neo4jLineageBackend(load_settings())
    try:
        be.setup()
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    yield be
    be.teardown()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _spec(asset_id: str) -> ScenarioSpec:
    return ScenarioSpec("E04-0000", "E04", "g", {"asset_id": asset_id}, {}, [], "sufficient", "test")


@pytest.mark.db
def test_cypher_matches_networkx_on_tiny_graph(backend):
    payload = tiny_payload()
    backend.load(payload)
    assert backend.engine_version().startswith("Neo4j Kernel 5.26")
    assert backend.engine_version().endswith("community")
    for asset in ("d1", "s1", "dash1", "iso", "t2"):
        ev = backend.retrieve(_spec(asset))
        assert ev.facts == e04.reference_facts(payload, asset), asset
    d1 = backend.retrieve(_spec("d1")).facts
    assert [(c["asset_id"], c["hops"]) for c in d1["consumers"]] == [("d2", 1), ("d3", 1), ("dash1", 2)]
    assert d1["in_cycle"] is True


@pytest.mark.db
def test_cypher_hop_bound_matches_networkx(backend):
    nodes = [_node(f"c{i}", "Dataset") for i in range(MAX_HOPS + 4)]
    edges = [_edge(f"c{i + 1}", f"c{i}", "DEPENDS_ON") for i in range(MAX_HOPS + 3)]
    payload = {"nodes": nodes, "edges": edges}
    backend.load(payload)
    assert backend.retrieve(_spec("c0")).facts == e04.reference_facts(payload, "c0")


@pytest.mark.db
def test_cypher_matches_networkx_on_seeded_scenarios(backend):
    from debench import SEED

    specs = e04.generate_specs(SEED)
    backend.load(e04.db_seed(specs, SEED))
    for spec in specs[:40]:
        ev = backend.retrieve(spec)
        assert e04.oracle_check(spec, ev.facts) == [], spec.scenario_id
