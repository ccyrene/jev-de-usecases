"""E04 - Data lineage impact analysis on Neo4j Community (PLAN section 7).

Graph and direction: see ``debench.evidence.neo4j_lineage`` (PRODUCES forward, CONSUMES and
DEPENDS_ON backward; downstream = verified ``active`` edges only, at most 8 hops, cycle safe).
Consumers are Dataset / Dashboard / CriticalBusinessProcess nodes reached downstream.

Gold comes from the graph oracle (Cypher, cross-checked against an independent networkx traversal)
plus the deterministic policy below. Models only see the fact summary and a bounded excerpt.

Policy ``e04-policy-v1``
------------------------
Business weight by criticality: low 1, medium 2, high 4, critical 8. A consumer with
``freshness_sla`` <= 60 minutes and criticality high/critical counts double (capped at 8).
Every change in the catalogue is breaking-class, so the kind of change never alters the gold.

* ``gap``: the changed asset has ``lineage_status != complete``, or a pending (unverified) edge leaves
  the changed asset or any actively reached asset. Absence of impact cannot be proven when ``gap``.
* ``impact_tier``: ``critical_downstream`` if any consumer is high/critical (provable impact even with
  a gap); else ``unknown`` if ``gap``; else ``none`` (0 consumers), ``single_noncritical`` (1),
  ``multi_noncritical`` (>= 2).
* ``impact_severity`` (score = max effective weight + 1 if >= 6 consumers): 0 when no consumers;
  score 1 -> 1 low; 2-3 -> 2 medium; 4-7 -> 3 high; >= 8 -> 4 critical. With a gap the level is at
  least 1.
* ``needs_owner_escalation``: true if severity >= 3, or the tier is ``unknown``, or the changed
  asset or any non-low consumer has missing/ambiguous ownership, or a non-low consumer is owned
  by a domain other than the changed asset's domain (``unassigned`` does not count as a domain).

Evidence sufficiency: ``insufficient`` when the tier is ``unknown``; ``ambiguous`` when the answer
rests on stale criticality metadata or on missing/ambiguous ownership; otherwise ``sufficient``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import networkx as nx

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import SCENARIOS_PER_CASE, ScenarioSpec
from debench.evidence.neo4j_lineage import (
    CONSUMER_KINDS,
    CRITICAL_LEVELS,
    EXCERPT_LIMIT,
    EXCERPT_UP_HOPS,
    LINEAGE_RELS,
    MAX_HOPS,
    Neo4jLineageBackend,
    assemble_facts,
    asset_view,
)

CASE = "E04"
BACKEND = "neo4j"
CASE_OFFSET = 4040
POLICY_VERSION = "e04-policy-v1"
LABEL_SOURCE = f"graph_oracle+{POLICY_VERSION}"

DOMAINS = ("finance", "sales", "marketing", "supply_chain", "risk", "hr", "operations", "customer")
TEAM_SUFFIXES = ("data", "bi", "platform")
WEIGHTS = {"low": 1, "medium": 2, "high": 4, "critical": 8}
TIGHT_SLA_MIN = 60
BREADTH_CONSUMERS = 6
TIERS = ("none", "single_noncritical", "multi_noncritical", "critical_downstream", "unknown")
PER_TIER = SCENARIOS_PER_CASE // len(TIERS)
N_FAMILIES = 150
N_ORPHAN_FAMILIES = 8
PREFIX = {
    "SourceTable": "src",
    "Transformation": "tf",
    "DAG": "dag",
    "Dataset": "ds",
    "Dashboard": "dash",
    "CriticalBusinessProcess": "cbp",
}

# slug, English noun, Thai noun, domain
NOUNS = (
    ("orders", "order", "คำสั่งซื้อ", "sales"),
    ("quotes", "sales quote", "ใบเสนอราคา", "sales"),
    ("payments", "payment", "การชำระเงิน", "finance"),
    ("invoices", "invoice", "ใบแจ้งหนี้", "finance"),
    ("ledger", "general ledger", "บัญชีแยกประเภท", "finance"),
    ("campaigns", "marketing campaign", "แคมเปญการตลาด", "marketing"),
    ("leads", "lead", "ลีด", "marketing"),
    ("shipments", "shipment", "การจัดส่ง", "supply_chain"),
    ("inventory", "inventory", "สินค้าคงคลัง", "supply_chain"),
    ("suppliers", "supplier", "ซัพพลายเออร์", "supply_chain"),
    ("claims", "insurance claim", "เคลมประกัน", "risk"),
    ("fraud_alerts", "fraud alert", "การแจ้งเตือนการทุจริต", "risk"),
    ("employees", "employee", "พนักงาน", "hr"),
    ("payroll", "payroll", "เงินเดือน", "hr"),
    ("tickets", "support ticket", "ตั๋วซัพพอร์ต", "customer"),
    ("customers", "customer", "ลูกค้า", "customer"),
    ("sessions", "web session", "เซสชันเว็บ", "customer"),
    ("machines", "machine telemetry", "ข้อมูลเซนเซอร์เครื่องจักร", "operations"),
    ("deliveries", "delivery run", "รอบการส่งสินค้า", "operations"),
    ("capacity", "capacity plan", "แผนกำลังการผลิต", "operations"),
)
SYSTEMS = ("ERP", "CRM", "POS", "WMS", "HRIS", "ticketing")
GRAINS = (("Daily", "รายวัน"), ("Weekly", "รายสัปดาห์"), ("Monthly", "รายเดือน"))
AUDIENCES = (
    ("executive", "ผู้บริหาร"),
    ("finance control", "ฝ่ายควบคุมการเงิน"),
    ("operations", "ทีมปฏิบัติการ"),
    ("analyst", "นักวิเคราะห์ข้อมูล"),
)
PROCESSES = (
    ("month-end close", "การปิดบัญชีสิ้นเดือน"),
    ("regulatory reporting", "การรายงานต่อหน่วยงานกำกับดูแล"),
    ("order fulfilment", "การจัดส่งตามคำสั่งซื้อ"),
    ("payroll run", "การจ่ายเงินเดือน"),
    ("fraud review", "การตรวจสอบการทุจริต"),
)
COLUMNS = ("order_id", "net_amount", "customer_id", "status_code", "created_at", "region_code", "unit_price", "risk_score")
TYPE_PAIRS = (("int", "bigint"), ("varchar", "date"), ("float", "numeric"))

# criticality distribution per profile and kind
CRIT_TABLE: dict[str, dict[str, tuple[tuple[str, float], ...]]] = {
    "low": {
        "Dataset": (("low", 0.6), ("medium", 0.35), ("high", 0.05)),
        "Dashboard": (("low", 0.4), ("medium", 0.5), ("high", 0.1)),
        "CriticalBusinessProcess": (("high", 1.0),),
    },
    "mixed": {
        "Dataset": (("low", 0.35), ("medium", 0.35), ("high", 0.25), ("critical", 0.05)),
        "Dashboard": (("low", 0.15), ("medium", 0.3), ("high", 0.4), ("critical", 0.15)),
        "CriticalBusinessProcess": (("high", 0.4), ("critical", 0.6)),
    },
    "hot": {
        "Dataset": (("low", 0.2), ("medium", 0.3), ("high", 0.35), ("critical", 0.15)),
        "Dashboard": (("medium", 0.15), ("high", 0.35), ("critical", 0.5)),
        "CriticalBusinessProcess": (("high", 0.3), ("critical", 0.7)),
    },
}
PROFILE_WEIGHTS = (("low", 0.5), ("mixed", 0.3), ("hot", 0.2))
JOB_CRIT = (("low", 0.5), ("medium", 0.4), ("high", 0.1))
SOURCE_CRIT = (("low", 0.7), ("medium", 0.25), ("high", 0.05))


def _pick(rng: random.Random, table: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([k for k, _ in table], weights=[w for _, w in table])[0]


# --------------------------------------------------------------------------------------------
# Synthetic graph
# --------------------------------------------------------------------------------------------


@dataclass
class Graph:
    nodes: list[dict[str, Any]]  # {"labels": [...], "props": {...}}
    edges: list[dict[str, Any]]  # {"src","dst","type","status"} as stored
    families: dict[str, list[str]]  # family id -> asset ids

    def payload(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges}


class _Builder:
    def __init__(self, rng: random.Random, fam: str, counter: list[int]):
        self.rng, self.fam, self.counter = rng, fam, counter
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.pairs: set[tuple[str, str]] = set()

    def node(self, kind: str, noun: tuple[str, str, str, str], crit: str, domain: str, desc: tuple[str, str]) -> str:
        self.counter[0] += 1
        asset_id = f"{PREFIX[kind]}_{noun[0]}_{self.counter[0]:04d}"
        tight = crit in CRITICAL_LEVELS
        sla = self.rng.choice((15, 60, 240, 480, 1440) if tight else (240, 720, 1440))
        self.nodes[asset_id] = {
            "labels": ["Asset", kind],
            "props": {
                "asset_id": asset_id,
                "domain": domain,
                "criticality": crit,
                "freshness_sla": sla,
                "description_en": desc[0],
                "description_th": desc[1],
                "family_id": self.fam,
            },
        }
        return asset_id

    def link(self, rel: str, up: str, down: str) -> bool:
        """Add a lineage edge whose data flows up -> down, stored in the relationship's own direction."""
        if up == down or (up, down) in self.pairs:
            return False
        self.pairs.add((up, down))
        src, dst = (up, down) if rel == "PRODUCES" else (down, up)
        self.edges.append({"src": src, "dst": dst, "type": rel, "status": "active"})
        return True

    def kind(self, asset_id: str) -> str:
        return self.nodes[asset_id]["labels"][1]

    def flow_edges(self) -> list[tuple[dict[str, Any], str, str]]:
        out = []
        for e in self.edges:
            up, down = (e["src"], e["dst"]) if e["type"] == "PRODUCES" else (e["dst"], e["src"])
            out.append((e, up, down))
        return out


def _desc(kind: str, noun: tuple[str, str, str, str], rng: random.Random, variant: str = "") -> tuple[str, str]:
    _, en, th, _ = noun
    if kind == "SourceTable":
        system = rng.choice(SYSTEMS)
        return (f"Raw {en} records ingested from the {system} system.", f"ข้อมูล{th}ดิบที่นำเข้าจากระบบ {system}")
    if kind == "Transformation":
        return (f"Nightly job that cleans and standardizes {en} data.", f"งานรายคืนสำหรับทำความสะอาดและจัดรูปแบบข้อมูล{th}")
    if kind == "DAG":
        return (f"Orchestrated pipeline that refreshes {en} tables on schedule.", f"pipeline ที่ตั้งเวลารีเฟรชตาราง{th}")
    if kind == "Dataset":
        g_en, g_th = rng.choice(GRAINS)
        return (f"{g_en} {en} summary table for analytics.", f"ตารางสรุป{th}{g_th}สำหรับการวิเคราะห์")
    if kind == "Dashboard":
        a_en, a_th = rng.choice(AUDIENCES)
        en_text, th_text = f"Dashboard for {a_en} users tracking {en} performance.", f"dashboard สำหรับ{a_th}ที่ติดตามผลด้าน{th}"
        if variant == "new":
            en_text, th_text = en_text + " Recently added.", th_text + " เพิ่งเพิ่มใหม่"
        return en_text, th_text
    p_en, p_th = rng.choice(PROCESSES)
    return f"Business process: {p_en}.", f"กระบวนการทางธุรกิจ: {p_th}"


def _owner_note(team: str | None, others: tuple[str, str] | None) -> tuple[str, str]:
    if others:
        return (
            f"Co-owned by {others[0]} and {others[1]}; no single approver is designated.",
            f"{others[0]} และ {others[1]} เป็นเจ้าของร่วมกัน และไม่ได้ระบุผู้อนุมัติหลัก",
        )
    if team:
        return f"Owned by team {team}.", f"ทีม {team} เป็นเจ้าของ"
    return "No owner is registered for this asset.", "ยังไม่มีการลงทะเบียนเจ้าของของ asset นี้"


def _build_family(rng: random.Random, fam: str, counter: list[int]) -> _Builder:
    b = _Builder(rng, fam, counter)
    noun = rng.choice(NOUNS)
    other = rng.choice([n for n in NOUNS if n[3] != noun[3]])
    profile = _pick(rng, PROFILE_WEIGHTS)
    table = CRIT_TABLE[profile]

    def make(kind: str, crit: str, cross: bool = False, variant: str = "") -> str:
        n = other if cross else noun
        return b.node(kind, n, crit, n[3], _desc(kind, n, rng, variant))

    sources = [make("SourceTable", _pick(rng, SOURCE_CRIT)) for _ in range(rng.choice((1, 1, 2)))]
    prev, datasets = sources, []
    for _ in range(rng.choice((1, 2, 2, 3))):
        level = []
        for _ in range(rng.choice((1, 2, 2, 3))):
            job = make(rng.choices(("Transformation", "DAG"), weights=(0.7, 0.3))[0], _pick(rng, JOB_CRIT))
            ds = make("Dataset", _pick(rng, table["Dataset"]), cross=rng.random() < 0.12)
            for up in rng.sample(prev, k=min(len(prev), rng.choice((1, 1, 2)))):
                b.link("CONSUMES", up, job)
            b.link("PRODUCES", job, ds)
            level.append(ds)
        if datasets and rng.random() < 0.2:
            b.link("DEPENDS_ON", rng.choice(datasets), rng.choice(level))
        datasets.extend(level)
        prev = level
    tail = datasets[-3:]
    dashboards = []
    for i in range(rng.choice((0, 1, 2, 2, 3))):
        dash = make("Dashboard", _pick(rng, table["Dashboard"]), cross=rng.random() < 0.15)
        for up in rng.sample(tail, k=min(len(tail), rng.choice((1, 2)))):
            b.link(rng.choice(("CONSUMES", "CONSUMES", "DEPENDS_ON")), up, dash)
        dashboards.append(dash)
    if (dashboards or datasets) and rng.random() < 0.25:
        cbp = make("CriticalBusinessProcess", _pick(rng, table["CriticalBusinessProcess"]))
        for up in rng.sample(dashboards or tail, k=min(len(dashboards or tail), rng.choice((1, 2)))):
            b.link("DEPENDS_ON", up, cbp)
    if rng.random() < 0.15:
        make("Dataset", "low")  # isolated dataset
    # dependency cycle: late dataset feeds back into an early one through a job
    if len(datasets) >= 3 and rng.random() < 0.2:
        job = make("Transformation", "low")
        late, early = datasets[-1], datasets[0]
        b.link("CONSUMES", late, job)
        b.link("PRODUCES", job, early)
    _apply_edge_faults(b, dashboards)
    _apply_node_faults(b)
    return b


def _apply_edge_faults(b: _Builder, dashboards: list[str]) -> None:
    rng = b.rng
    flow = b.flow_edges()
    consumer_edges = [t for t in flow if b.kind(t[2]) in CONSUMER_KINDS and b.kind(t[1]) != "SourceTable"]
    if consumer_edges and rng.random() < 0.45:
        high = [t for t in consumer_edges if b.nodes[t[2]]["props"]["criticality"] in CRITICAL_LEVELS]
        edge, _, _ = rng.choice(high if high and rng.random() < 0.7 else consumer_edges)
        edge["status"] = "stale"
    if dashboards and rng.random() < 0.12:
        new = rng.choice(dashboards)
        props = b.nodes[new]["props"]
        props["description_en"] = props["description_en"].removesuffix(" Recently added.") + " Recently added."
        if not props["description_th"].endswith("เพิ่งเพิ่มใหม่"):
            props["description_th"] += " เพิ่งเพิ่มใหม่"
        for edge, _, down in flow:
            if down == new:
                edge["status"] = "pending"


def _apply_node_faults(b: _Builder) -> None:
    rng = b.rng
    for node in b.nodes.values():
        props, kind = node["props"], node["labels"][1]
        if kind in ("SourceTable", "Dataset", "Transformation", "DAG") and rng.random() < 0.07:
            props["lineage_status"] = "partial"
        else:
            props["lineage_status"] = "complete"
        props["metadata_status"] = "stale" if rng.random() < 0.08 else "current"


def _assign_owners(rng: random.Random, nodes: list[dict[str, Any]], owner_ids: dict[str, str]) -> list[dict[str, Any]]:
    """Mutate asset props with owner_team / ownership notes; return OWNED_BY edges."""
    edges = []
    for node in nodes:
        if "Owner" in node["labels"]:
            continue
        props = node["props"]
        domain = props["domain"]
        roll = rng.random()
        if roll < 0.05:
            team, owners, others = None, [], None
        elif roll < 0.09:
            d2 = rng.choice([d for d in DOMAINS if d != domain])
            t1, t2 = f"{domain}_{rng.choice(TEAM_SUFFIXES)}", f"{d2}_{rng.choice(TEAM_SUFFIXES)}"
            team, owners, others = f"{t1}|{t2}", [t1, t2], (t1, t2)
        else:
            team = f"{domain}_{rng.choice(TEAM_SUFFIXES)}"
            owners, others = [team], None
        note_en, note_th = _owner_note(team, others)
        props["ownership_note_en"], props["ownership_note_th"] = note_en, note_th
        if team:
            props["owner_team"] = team
        for t in owners:
            edges.append({"src": props["asset_id"], "dst": owner_ids[t], "type": "OWNED_BY", "status": "active"})
    return edges


def _owner_nodes() -> tuple[list[dict[str, Any]], dict[str, str]]:
    nodes, ids = [], {}
    for domain in DOMAINS:
        for suffix in TEAM_SUFFIXES:
            team = f"{domain}_{suffix}"
            oid = f"own_{team}"
            ids[team] = oid
            nodes.append(
                {
                    "labels": ["Owner"],
                    "props": {
                        "asset_id": oid,
                        "domain": domain,
                        "owner_team": team,
                        "description_en": f"Data owner team for the {domain} domain.",
                        "description_th": f"ทีมเจ้าของข้อมูลของโดเมน {domain}",
                    },
                }
            )
    return nodes, ids


@lru_cache(maxsize=4)
def build_graph(seed: int) -> Graph:
    rng = random.Random(seed + CASE_OFFSET)
    counter = [0]
    owner_nodes, owner_ids = _owner_nodes()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    families: dict[str, list[str]] = {}
    for i in range(1, N_FAMILIES + 1):
        fam = f"fam{i:03d}"
        b = _build_family(rng, fam, counter)
        nodes.extend(b.nodes.values())
        edges.extend(b.edges)
        families[fam] = list(b.nodes)
    for i in range(N_FAMILIES + 1, N_FAMILIES + N_ORPHAN_FAMILIES + 1):
        fam = f"fam{i:03d}"
        b = _Builder(rng, fam, counter)
        noun = rng.choice(NOUNS)
        for kind in rng.choice((("Dataset",), ("SourceTable", "Dataset"), ("Dataset", "Dashboard"))):
            b.node(kind, noun, rng.choice(("low", "medium")), noun[3], _desc(kind, noun, rng))
        _apply_node_faults(b)
        nodes.extend(b.nodes.values())
        families[fam] = list(b.nodes)
    owned = _assign_owners(rng, nodes, owner_ids)
    return Graph(nodes=owner_nodes + nodes, edges=edges + owned, families=families)


# --------------------------------------------------------------------------------------------
# Independent reference: networkx implementation of the same reachability and metrics
# --------------------------------------------------------------------------------------------


def _flow_digraph(payload: dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in payload["nodes"]:
        g.add_node(node["props"]["asset_id"], labels=node["labels"], props=node["props"], owners=[])
    for e in payload["edges"]:
        if e["type"] == "OWNED_BY":
            g.nodes[e["src"]]["owners"].append(g.nodes[e["dst"]]["props"]["domain"])
        elif e["type"] in LINEAGE_RELS:
            up, down = (e["src"], e["dst"]) if e["type"] == "PRODUCES" else (e["dst"], e["src"])
            g.add_edge(up, down, rel=e["type"], status=e["status"])
    return g


def _view(g: nx.DiGraph, asset_id: str, hops: int | None = None) -> dict[str, Any]:
    n = g.nodes[asset_id]
    return asset_view(n["props"], n["labels"], n["owners"], hops)


def _active_reach(g: nx.DiGraph, start: str, statuses: tuple[str, ...] = ("active",)) -> dict[str, int]:
    sub = nx.subgraph_view(g, filter_edge=lambda u, v: g[u][v]["status"] in statuses)
    return dict(nx.single_source_shortest_path_length(sub, start, cutoff=MAX_HOPS))


def reference_facts(payload: dict[str, Any], asset_id: str) -> dict[str, Any]:
    return _reference_from_graph(_flow_digraph(payload), asset_id)


def _reference_from_graph(g: nx.DiGraph, asset_id: str) -> dict[str, Any]:
    reach = _active_reach(g, asset_id)
    consumers = [
        _view(g, n, h) for n, h in reach.items() if n != asset_id and any(k in g.nodes[n]["labels"] for k in CONSUMER_KINDS)
    ]
    pending = sum(1 for u in reach for _, _, d in g.out_edges(u, data=True) if d["status"] == "pending")
    in_cycle = any(g.has_edge(u, asset_id) and h + 1 <= MAX_HOPS for u, h in reach.items())
    near = dict(nx.single_source_shortest_path_length(g, asset_id, cutoff=EXCERPT_UP_HOPS))
    rows = []
    for u, ud in near.items():
        for _, v, d in g.out_edges(u, data=True):
            kind = next(k for k in ("SourceTable", "Transformation", "DAG", "Dataset", "Dashboard", "CriticalBusinessProcess") if k in g.nodes[v]["labels"])
            rows.append(
                (ud, u, v, {"up": u, "down": v, "rel": d["rel"], "status": d["status"], "down_kind": kind,
                            "down_criticality": g.nodes[v]["props"].get("criticality")})
            )
    rows.sort(key=lambda r: r[:3])
    excerpt = [r[3] for r in rows[:EXCERPT_LIMIT]]
    return assemble_facts(_view(g, asset_id), consumers, pending, excerpt, in_cycle)


@lru_cache(maxsize=4)
def _graph_nx(seed: int) -> nx.DiGraph:
    return _flow_digraph(build_graph(seed).payload())


def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    return _reference_from_graph(_graph_nx(spec.seed), spec.facts["asset_id"])


# --------------------------------------------------------------------------------------------
# Deterministic policy
# --------------------------------------------------------------------------------------------


def _effective_weight(c: dict[str, Any]) -> int:
    w = WEIGHTS.get(c["criticality"], 1)
    sla = c["freshness_sla"]
    if c["criticality"] in CRITICAL_LEVELS and sla is not None and sla <= TIGHT_SLA_MIN:
        w = min(8, w * 2)
    return w


def _gap(facts: dict[str, Any]) -> bool:
    return facts["asset"]["lineage_status"] != "complete" or facts["pending_edges"] > 0


def _non_low(facts: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in facts["consumers"] if c["criticality"] != "low"]


def decide(facts: dict[str, Any]) -> dict[str, Any]:
    consumers, gap = facts["consumers"], _gap(facts)
    if facts["critical_consumers"]:
        tier = "critical_downstream"
    elif gap:
        tier = "unknown"
    else:
        tier = "none" if not consumers else "single_noncritical" if len(consumers) == 1 else "multi_noncritical"
    score = max((_effective_weight(c) for c in consumers), default=0)
    if len(consumers) >= BREADTH_CONSUMERS:
        score += 1
    severity = 0 if score == 0 else 1 if score == 1 else 2 if score <= 3 else 3 if score <= 7 else 4
    if gap:
        severity = max(severity, 1)
    asset = facts["asset"]
    relevant = _non_low(facts)
    cross_domain = any(d not in (asset["domain"], "unassigned") for c in relevant for d in c["owner_domains"])
    ownership_issue = asset["ownership"] != "clear" or any(c["ownership"] != "clear" for c in relevant)
    escalate = severity >= 3 or tier == "unknown" or ownership_issue or cross_domain
    return {"impact_tier": tier, "impact_severity": severity, "needs_owner_escalation": escalate}


def sufficiency(facts: dict[str, Any], tier: str) -> str:
    if tier == "unknown":
        return "insufficient"
    relevant = _non_low(facts)
    stale_meta = facts["asset"]["metadata_status"] == "stale" or any(c["metadata_status"] == "stale" for c in facts["consumers"])
    ownership_issue = facts["asset"]["ownership"] != "clear" or any(c["ownership"] != "clear" for c in relevant)
    return "ambiguous" if stale_meta or ownership_issue else "sufficient"


def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Graph traversal facts plus the deterministic policy; never abstains."""
    return decide(facts)


# --------------------------------------------------------------------------------------------
# Specs
# --------------------------------------------------------------------------------------------

CHANGES = {
    "SourceTable": ("rename_column", "drop_column", "change_type", "schedule", "retire"),
    "Transformation": ("redefine", "backfill", "schedule", "retire"),
    "DAG": ("redefine", "backfill", "schedule", "retire"),
    "Dataset": ("rename_column", "drop_column", "change_type", "redefine", "backfill", "retire"),
    "Dashboard": ("schedule", "retire"),
    "CriticalBusinessProcess": ("schedule", "retire"),
}


def _change_params(rng: random.Random, kind: str, asset_kind: str) -> dict[str, Any]:
    col, col2 = rng.sample(COLUMNS, 2)
    t_from, t_to = rng.choice(TYPE_PAIRS)
    return {"kind": kind, "column": col, "column_new": col2, "type_from": t_from, "type_to": t_to, "days": rng.choice((30, 90, 180))}


def _tags(facts: dict[str, Any], tier: str, suff: str, stale_tier: str) -> list[str]:
    tags = []
    asset = facts["asset"]
    low_to_exec = (
        asset["criticality"] == "low"
        and tier == "critical_downstream"
        and any(c["kind"] == "Dashboard" and c["criticality"] == "critical" for c in facts["consumers"])
    )
    if low_to_exec or (tier != "critical_downstream" and stale_tier == "critical_downstream"):
        tags.append("hard_negative")
    if facts["in_cycle"] or any(e["status"] != "active" for e in facts["excerpt"]) or len(facts["excerpt"]) >= 7:
        tags.append("distractor")
    if suff == "insufficient":
        tags.append("insufficient_evidence")
    if suff == "ambiguous":
        tags.append("ambiguous")
    if not facts["consumers"]:
        tags.append("negation")
    return tags


@lru_cache(maxsize=4)
def _candidates(seed: int) -> tuple[dict[str, Any], ...]:
    graph = build_graph(seed)
    g = _graph_nx(seed)
    out = []
    for fam, ids in graph.families.items():
        for asset_id in ids:
            facts = _reference_from_graph(g, asset_id)
            gold = decide(facts)
            stale_reach = _active_reach(g, asset_id, ("active", "stale"))
            stale_tier = (
                "critical_downstream"
                if any(
                    n != asset_id and g.nodes[n]["props"].get("criticality") in CRITICAL_LEVELS and any(k in g.nodes[n]["labels"] for k in CONSUMER_KINDS)
                    for n in stale_reach
                )
                else ""
            )
            suff = sufficiency(facts, gold["impact_tier"])
            out.append(
                {
                    "family": fam,
                    "asset_id": asset_id,
                    "kind": facts["asset"]["kind"],
                    "gold": gold,
                    "suff": suff,
                    "tags": _tags(facts, gold["impact_tier"], suff, stale_tier),
                }
            )
    return tuple(out)


def _select(cands: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    cands = list(cands)
    rng.shuffle(cands)
    chosen: list[dict[str, Any]] = []
    per_tier: dict[str, int] = {t: 0 for t in TIERS}
    per_group: dict[str, int] = {}
    hard_target = round(SCENARIOS_PER_CASE * 0.22)
    per_severity: dict[int, int] = {3: 0, 4: 0}

    def take(c: dict[str, Any], cap: int) -> bool:
        tier = c["gold"]["impact_tier"]
        if per_tier[tier] >= PER_TIER or per_group.get(c["family"], 0) >= cap or c in chosen:
            return False
        sev = c["gold"]["impact_severity"]
        if tier == "critical_downstream":
            if per_severity[sev] >= PER_TIER // 2:  # split critical-tier severities evenly (levels 3 / 4)
                return False
            per_severity[sev] += 1
        chosen.append(c)
        per_tier[tier] += 1
        per_group[c["family"]] = per_group.get(c["family"], 0) + 1
        return True

    hard = [c for c in cands if "hard_negative" in c["tags"]]
    for c in hard:
        if sum("hard_negative" in x["tags"] for x in chosen) < hard_target:
            take(c, 2)
    for cap in (1, 2, 3):
        for c in cands:
            take(c, cap)
    if any(n < PER_TIER for n in per_tier.values()):
        raise ValueError(f"E04: cannot fill every tier quota: {per_tier}")
    return chosen


def generate_specs(seed: int) -> list[ScenarioSpec]:
    rng = random.Random(seed + CASE_OFFSET + 1)
    chosen = _select(list(_candidates(seed)), rng)
    rng.shuffle(chosen)
    specs = []
    for i, c in enumerate(chosen, 1):
        change = _change_params(rng, rng.choice(CHANGES[c["kind"]]), c["kind"])
        specs.append(
            ScenarioSpec(
                scenario_id=scenario_id(CASE, i),
                experiment=CASE,
                group_id=c["family"],
                facts={"asset_id": c["asset_id"], "change": change, "_internal": {"policy": POLICY_VERSION}},
                gold=dict(c["gold"]),
                difficulty_tags=list(c["tags"]),
                evidence_sufficiency=c["suff"],
                label_source=LABEL_SOURCE,
                seed=seed,
            )
        )
    return specs


def db_seed(specs: list[ScenarioSpec], seed: int) -> dict[str, Any]:
    """Whole graph, including every family not used by a scenario (distractors)."""
    return build_graph(seed).payload()


def make_backend(settings: Settings) -> Neo4jLineageBackend:
    return Neo4jLineageBackend(settings)


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    ref = expected_evidence(spec)
    problems = [f"cypher_vs_networkx:{key}" for key in ref if facts.get(key) != ref[key]]
    problems += [f"unexpected_fact:{key}" for key in facts if key not in ref and key != "_internal"]
    got = decide(facts)
    problems += [f"gold_vs_policy:{q}" for q, v in spec.gold.items() if got.get(q) != v]
    if sufficiency(facts, got["impact_tier"]) != spec.evidence_sufficiency:
        problems.append("sufficiency_vs_policy")
    return problems


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------

CRIT_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _display_consumers(facts: dict[str, Any], limit: int | None) -> list[dict[str, Any]]:
    ordered = sorted(facts["consumers"], key=lambda c: (-CRIT_RANK[c["criticality"]], c["hops"], c["asset_id"]))
    return ordered[:limit]


def _display_edges(facts: dict[str, Any], limit: int | None) -> list[dict[str, Any]]:
    # Unverified and retired edges first so they are never clipped away.
    ordered = sorted(enumerate(facts["excerpt"]), key=lambda p: (p[1]["status"] == "active", p[0]))
    return [e for _, e in ordered[:limit]]


def _owner(team: str | None) -> str:
    return team or "none"


def _asset_line(a: dict[str, Any]) -> str:
    return f"{a['asset_id']} {a['kind']} domain={a['domain']} criticality={a['criticality']} sla={a['freshness_sla']}m owner={_owner(a['owner_team'])}"


def _consumer_line(c: dict[str, Any]) -> str:
    return f"{c['asset_id']} {c['kind']} {c['criticality']} hops={c['hops']} sla={c['freshness_sla']}m owner={_owner(c['owner_team'])}"


def _edge_line(e: dict[str, Any]) -> str:
    tail = f"({e['down_criticality']})" if e["down_kind"] in CONSUMER_KINDS else ""
    return f"{e['up']} -> {e['down']}{tail} [{e['status']}]"


def _change_text(spec: ScenarioSpec, lang: str) -> str:
    ch, a = spec.facts["change"], spec.facts["asset_id"]
    en, th = {
        "rename_column": (
            f"Rename column {ch['column']} to {ch['column_new']} in {a}.",
            f"เปลี่ยนชื่อคอลัมน์ {ch['column']} เป็น {ch['column_new']} ใน {a}",
        ),
        "drop_column": (f"Drop column {ch['column']} from {a}.", f"ลบคอลัมน์ {ch['column']} ออกจาก {a}"),
        "change_type": (
            f"Change the type of {ch['column']} from {ch['type_from']} to {ch['type_to']} in {a}.",
            f"เปลี่ยนชนิดข้อมูลของ {ch['column']} ใน {a} จาก {ch['type_from']} เป็น {ch['type_to']}",
        ),
        "redefine": (
            f"Redefine {ch['column']} to exclude cancelled records in {a}.",
            f"ปรับนิยามของ {ch['column']} ใน {a} ให้ไม่รวมรายการที่ถูกยกเลิก",
        ),
        "backfill": (
            f"Backfill {a} for the last {ch['days']} days with corrected logic.",
            f"ทำ backfill ข้อมูล {a} ย้อนหลัง {ch['days']} วันด้วยลอจิกที่แก้ไขแล้ว",
        ),
        "schedule": (
            f"Move the refresh of {a} from hourly to daily.",
            f"ย้ายรอบรีเฟรชของ {a} จากรายชั่วโมงเป็นรายวัน",
        ),
        "retire": (f"Retire {a} and stop refreshing it.", f"เลิกใช้ {a} และหยุดรีเฟรช"),
    }[ch["kind"]]
    return en if lang == "en" else th


def _summary(facts: dict[str, Any], lang: str) -> str:
    n, k, doms = facts["n_consumers"], len(facts["critical_consumers"]), ", ".join(facts["owner_domains"])
    if lang == "en":
        head = f"Verified downstream consumers (active edges, up to {MAX_HOPS} hops): "
        if not n:
            return head + "none."
        return head + f"{n}, of which {k} high or critical; nearest distance in hops: {facts['min_hops']}; owner domains: {doms}."
    head = f"consumer ปลายทางที่ยืนยันแล้ว (เฉพาะ edge ที่ active, ไม่เกิน {MAX_HOPS} hops): "
    if not n:
        return head + "ไม่มี"
    return head + f"{n} รายการ โดย {k} รายการเป็น high หรือ critical ระยะใกล้สุด (hops): {facts['min_hops']} โดเมนเจ้าของ: {doms}"


def _caveats(facts: dict[str, Any], lang: str) -> list[str]:
    en: list[str] = []
    th: list[str] = []
    asset = facts["asset"]
    if asset["lineage_status"] != "complete":
        en.append("Lineage of the changed asset is marked partial; some readers may be unregistered.")
        th.append("lineage ของ asset ที่เปลี่ยนถูกระบุว่าไม่สมบูรณ์ และอาจมี reader ที่ยังไม่ได้ลงทะเบียน")
    if asset["metadata_status"] == "stale":
        en.append("Criticality metadata of the changed asset was last reviewed over a year ago.")
        th.append("metadata ของ criticality ของ asset ที่เปลี่ยนไม่ได้รับการทบทวนมานานกว่าหนึ่งปี")
    if facts["pending_edges"]:
        en.append(f"Pending (unverified) lineage edges, not counted: {facts['pending_edges']}.")
        th.append(f"edge ที่ยัง pending (ยังไม่ยืนยัน) และไม่ถูกนับ: {facts['pending_edges']}")
    stale_edges = sum(e["status"] == "stale" for e in facts["excerpt"])
    if stale_edges:
        en.append(f"Stale (retired) edges in the excerpt, not counted: {stale_edges}.")
        th.append(f"edge แบบ stale (เลิกใช้แล้ว) ใน excerpt ซึ่งไม่ถูกนับ: {stale_edges}")
    stale_consumers = sum(c["metadata_status"] == "stale" for c in facts["consumers"])
    if stale_consumers:
        en.append(f"Affected consumers whose criticality metadata is over a year old: {stale_consumers}.")
        th.append(f"consumer ที่ metadata ของ criticality เก่ากว่าหนึ่งปี: {stale_consumers}")
    if facts["in_cycle"]:
        en.append("The changed asset sits on a dependency cycle.")
        th.append("asset ที่เปลี่ยนอยู่บน dependency cycle")
    return en if lang == "en" else th


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    asset = facts["asset"]
    long = context_level == "natural_long"
    equal = context_level == "equal_information"
    consumers = _display_consumers(facts, None if (long or equal) else 4)
    edges = _display_edges(facts, None if (long or equal) else 6)
    state: dict[str, Any] = {"change_request": _change_text(spec, lang), "changed_asset": _asset_line(asset)}
    if long:
        state["asset_note"] = asset["description"][lang]
    if not equal:
        state["ownership"] = asset["ownership_note"][lang]
    state["graph_summary"] = _summary(facts, lang)
    state["affected_consumers"] = [_consumer_line(c) for c in consumers]
    if long:
        state["consumer_notes"] = [f"{c['asset_id']}: {c['description'][lang]} {c['ownership_note'][lang]}" for c in consumers]
    state["downstream_edges"] = [_edge_line(e) for e in edges]
    state["graph_caveats"] = _caveats(facts, lang)
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    ch = spec.facts["change"]
    tokens = [spec.facts["asset_id"]]
    if ch["kind"] in ("rename_column", "drop_column", "change_type", "redefine"):
        tokens.append(ch["column"])
    if ch["kind"] == "rename_column":
        tokens.append(ch["column_new"])
    if ch["kind"] == "change_type":
        tokens += [ch["type_from"], ch["type_to"]]
    tokens += [c["asset_id"] for c in _display_consumers(facts, 4)]
    return tokens
