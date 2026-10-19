"""Neo4j Community lineage backend for E04 (data lineage impact analysis).

Edge direction (as stored)::

    (job:Transformation|DAG)-[:PRODUCES]->(data)         job writes data
    (job|Dashboard|CriticalBusinessProcess)-[:CONSUMES]->(data)   reader points at what it reads
    (dependent)-[:DEPENDS_ON]->(upstream)                dependent points at its upstream
    (asset)-[:OWNED_BY]->(Owner)                         ownership, never traversed

Data flows along PRODUCES forward and along CONSUMES / DEPENDS_ON backward.

**Downstream** of a changed asset X: every Dataset, Dashboard or CriticalBusinessProcess reachable
from X (excluding X) in 1..MAX_HOPS flow-direction steps over verified edges only
(``status = 'active'``). ``stale`` (retired) and ``pending`` (registered, not yet verified) edges are
never traversed. Intermediate Transformation / DAG nodes are traversed but are not consumers.
Hop distance is the number of edges on the shortest such path. Cycles are safe: the repeated
segment is a Cypher trail (no relationship twice) and the bound is a literal.

The oracle queries live in ``infra/init/e04_neo4j/*.cypher`` and are fixed and provider independent.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from neo4j.exceptions import DriverError, Neo4jError

from debench.config import ROOT, Settings
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import RUNTIME_NATIVE, BackendUnavailable, Evidence, timed

MAX_HOPS = 8
EXCERPT_UP_HOPS = 1  # excerpt edges leave the changed asset or assets one step downstream of it
EXCERPT_LIMIT = 10
LINEAGE_RELS = ("PRODUCES", "CONSUMES", "DEPENDS_ON")
KIND_LABELS = ("SourceTable", "Transformation", "DAG", "Dataset", "Dashboard", "CriticalBusinessProcess", "Owner")
CONSUMER_KINDS = ("Dataset", "Dashboard", "CriticalBusinessProcess")
CRITICAL_LEVELS = ("high", "critical")
INIT_DIR = ROOT / "infra" / "init" / "e04_neo4j"
NEO4J_SCRIPT = ROOT / "infra" / "native" / "neo4j.sh"
_CONNECT_ERRORS = (DriverError, Neo4jError, OSError)


def kind_of(labels: list[str]) -> str:
    for kind in KIND_LABELS:
        if kind in labels:
            return kind
    raise ValueError(f"no kind label in {labels}")


def ownership_state(n_owners: int) -> str:
    return "missing" if n_owners == 0 else "clear" if n_owners == 1 else "ambiguous"


def asset_view(props: dict[str, Any], labels: list[str], owner_domains: list[str], hops: int | None = None) -> dict[str, Any]:
    """Language-invariant view of one asset; shared by the DB path and the reference path."""
    view: dict[str, Any] = {
        "asset_id": props["asset_id"],
        "kind": kind_of(labels),
        "domain": props.get("domain"),
        "criticality": props.get("criticality"),
        "freshness_sla": props.get("freshness_sla"),
        "owner_team": props.get("owner_team"),
        "lineage_status": props.get("lineage_status", "complete"),
        "metadata_status": props.get("metadata_status", "current"),
        "ownership": ownership_state(len(owner_domains)),
        "owner_domains": sorted(set(owner_domains)) or ["unassigned"],
        "description": {"en": props.get("description_en", ""), "th": props.get("description_th", "")},
        "ownership_note": {"en": props.get("ownership_note_en", ""), "th": props.get("ownership_note_th", "")},
    }
    if hops is not None:
        view["hops"] = hops
    return view


def assemble_facts(
    asset: dict[str, Any],
    consumers: list[dict[str, Any]],
    pending_edges: int,
    excerpt: list[dict[str, Any]],
    in_cycle: bool,
) -> dict[str, Any]:
    consumers = sorted(consumers, key=lambda c: (c["hops"], c["asset_id"]))
    return {
        "asset": asset,
        "consumers": consumers,
        "n_consumers": len(consumers),
        "min_hops": consumers[0]["hops"] if consumers else None,
        "critical_consumers": sorted(c["asset_id"] for c in consumers if c["criticality"] in CRITICAL_LEVELS),
        "owner_domains": sorted({d for c in consumers for d in c["owner_domains"]}),
        "pending_edges": pending_edges,
        "in_cycle": in_cycle,
        "excerpt": excerpt,
        "max_hops": MAX_HOPS,
    }


def read_query(name: str) -> str:
    text = (INIT_DIR / name).read_text(encoding="utf-8")
    return text.replace("__MAX_HOPS__", str(MAX_HOPS)).replace("__EXCERPT_UP_HOPS__", str(EXCERPT_UP_HOPS)).strip().rstrip(";")


def _statements(name: str) -> list[str]:
    body = "\n".join(ln for ln in (INIT_DIR / name).read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("//"))
    return [s.strip() for s in body.split(";") if s.strip()]


class Neo4jLineageBackend:
    name = "neo4j"
    runtime = RUNTIME_NATIVE

    def __init__(self, settings: Settings):
        self._uri = f"bolt://127.0.0.1:{settings.get('NEO4J_BOLT_PORT', '7687')}"
        self._user = settings.get("NEO4J_USER", "neo4j")
        self._password = settings.secret("NEO4J_PASSWORD")
        self._driver: Any = None
        self._started_here = False
        self._queries: dict[str, str] = {}

    def _connect(self) -> Any:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        driver.verify_connectivity()
        return driver

    def setup(self) -> None:
        if self._password is None:
            raise BackendUnavailable("NEO4J_PASSWORD is not set")
        try:
            self._driver = self._connect()
        except _CONNECT_ERRORS:
            if os.environ.get("DEBENCH_NO_AUTOSTART") or not NEO4J_SCRIPT.exists():
                raise BackendUnavailable(f"Neo4j is not reachable at {self._uri}; run infra/native/neo4j.sh start") from None
            started = subprocess.run(["bash", str(NEO4J_SCRIPT), "start"], capture_output=True, text=True, check=False)
            if started.returncode != 0:
                raise BackendUnavailable(f"neo4j.sh start failed: {started.stderr.strip()[-300:]}") from None
            self._started_here = True
            try:
                self._driver = self._connect()
            except _CONNECT_ERRORS as exc:
                raise BackendUnavailable(f"Neo4j started but connection failed: {type(exc).__name__}") from None
        self._queries = {n: read_query(n) for n in (
            "10_oracle_downstream.cypher", "11_oracle_pending.cypher", "12_oracle_excerpt.cypher", "13_oracle_asset.cypher", "14_oracle_cycle.cypher")}

    def engine_version(self) -> str:
        with self._driver.session() as session:
            row = session.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition").single()
        return f"{row['name']} {row['versions'][0]} {row['edition']}"

    def load(self, seed_payload: Any) -> None:
        """Wipe the graph, create constraints and load ``{"nodes": [...], "edges": [...]}``."""
        nodes, edges = seed_payload["nodes"], seed_payload["edges"]
        with self._driver.session() as session:
            for stmt in _statements("01_wipe.cypher"):
                session.run(stmt).consume()
            for stmt in _statements("00_constraints.cypher"):
                session.run(stmt).consume()
            by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = {}
            for node in nodes:
                by_labels.setdefault(tuple(node["labels"]), []).append(node["props"])
            for labels, rows in by_labels.items():
                session.run(f"UNWIND $rows AS row CREATE (n:{':'.join(labels)}) SET n = row", rows=rows).consume()
            by_type: dict[str, list[dict[str, Any]]] = {}
            for edge in edges:
                by_type.setdefault(edge["type"], []).append(edge)
            for rel, rows in by_type.items():
                target = "Owner" if rel == "OWNED_BY" else "Asset"
                session.run(
                    f"UNWIND $rows AS row MATCH (a:Asset {{asset_id: row.src}}) MATCH (b:{target} {{asset_id: row.dst}}) "
                    f"CREATE (a)-[:{rel} {{status: coalesce(row.status, 'active')}}]->(b)",
                    rows=rows,
                ).consume()

    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        asset_id = spec.facts["asset_id"]
        q = self._queries
        with timed() as t, self._driver.session() as session:
            asset_row = session.run(q["13_oracle_asset.cypher"], asset_id=asset_id).single()
            reach = session.run(q["10_oracle_downstream.cypher"], asset_id=asset_id).data()
            pending = session.run(q["11_oracle_pending.cypher"], asset_id=asset_id).single()["pending_edges"]
            excerpt_rows = session.run(q["12_oracle_excerpt.cypher"], asset_id=asset_id, limit=EXCERPT_LIMIT).data()
            in_cycle = bool(session.run(q["14_oracle_cycle.cypher"], asset_id=asset_id).single()["in_cycle"])
        asset = asset_view(asset_row["props"], asset_row["labels"], asset_row["owner_domains"])
        consumers = [asset_view(r["props"], r["labels"], r["owner_domains"], r["hops"]) for r in reach]
        excerpt = [
            {
                "up": r["up"],
                "down": r["down"],
                "rel": r["rel"],
                "status": r["status"],
                "down_kind": kind_of(r["down_labels"]),
                "down_criticality": r["down_criticality"],
            }
            for r in excerpt_rows
        ]
        facts = assemble_facts(asset, consumers, pending, excerpt, in_cycle)
        ref = {"backend": self.name, "asset_id": asset_id, "oracle": "cypher_trail_downstream", "max_hops": MAX_HOPS}
        return Evidence(facts=facts, ref=ref, db_ms=t["ms"])

    def teardown(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
        if self._started_here:
            subprocess.run(["bash", str(NEO4J_SCRIPT), "stop"], capture_output=True, check=False)
            self._started_here = False
