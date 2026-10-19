"""E01 - Pipeline failure triage on OpenSearch (PLAN.md section 4).

Each scenario is one alerted Airflow-style task (``dag_id``/``task_id``/``run_id``) whose log events
are indexed into ``airflow-task-logs`` together with healthy tasks, sibling runs, stale events and
noise. The evidence the models see is the output of one fixed, time-bounded query template
(``debench.evidence.opensearch``).

Gold (never indexed, never rendered; it comes from the injected fault and the DAG catalog below):

* ``failure_family``: the family of the injected fault (``KIND_FAMILY``). Faults whose evidence does
  not disambiguate (opaque exit codes, a connection-closed message with no cause, two unrelated
  causes across attempts, an alert with no failed attempt) are gold ``other_unknown``.
* ``severity``: derived from the synthetic DAG graph (``DAGS``), blocked tasks and consumers:
    S0  outcome recovered (succeeded on retry) or alert only (no failed attempt)
    S1  retry still pending, or terminal failure at a task with 0 downstream tasks and 0 consumers
    S3  terminal failure (retries exhausted / upstream failed) and >= 3 downstream consumers
    S2  any other terminal failure (workflow blocked, 0-2 consumers)
  For an upstream-failed task the impact is computed from the failing upstream task.
* ``requires_human``: pre-defined runbook rules, evaluated in order (``human_required``):
    R0  outcome recovered, retry pending or alert only -> no (nothing to mutate, retries are automatic)
    R1  family authentication or schema_mismatch       -> yes (credential/grant or DDL changes)
    R2  family resource_exhaustion                      -> yes when severity >= 2 (capacity or limit
                                                           change); a severity 1 leaf task is cleaned up by runbook
    R3  family connectivity (retries exhausted)         -> yes only when severity 3 (page on-call)
    R4  family data_error                               -> yes only when severity 3
                                                           (quarantine-and-rerun is pre-approved)
    R5  family dependency_failure                       -> yes only when severity 3 (cross-team escalation)
    R6  family other_unknown (terminal)                 -> yes (never act on an undiagnosed failure)

``requires_human`` and ``severity`` are recomputed from the retrieved DB events in ``oracle_check``;
the injected exception signature is re-checked there too.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import EvidenceBackend
from debench.evidence.opensearch import (
    FAIL_STATUSES,
    IMPACT_STATUSES,
    OpenSearchBackend,
    rank_events,
)

CASE = "E01"
BACKEND = "opensearch"
CASE_OFFSET = 1
LABEL_SOURCE = "injected_fault_verified_by_db"
MIN_DOCS = 10_500
FIRST_RUN_DATE = date(2025, 11, 1)

# --------------------------------------------------------------------------------------------
# Synthetic DAG catalog (the "defined DAG graph, SLA and blocked-consumer count")
# --------------------------------------------------------------------------------------------
SENSOR = "wait_for_upstream_feed"


@dataclass(frozen=True)
class DagDef:
    dag_id: str
    sla_minutes: int
    chain: tuple[str, ...]
    leaf: tuple[str, int]  # (leaf task, index of its parent in chain)
    consumers: dict[int, tuple[str, ...]]  # chain index -> downstream consumers of that task's output

    def tasks(self) -> list[str]:
        return [SENSOR, *self.chain, self.leaf[0]]

    def parents(self, task: str) -> list[str]:
        if task == SENSOR:
            return []
        if task == self.leaf[0]:
            return [self.chain[self.leaf[1]]]
        i = self.chain.index(task)
        return [SENSOR] if i == 0 else [self.chain[i - 1]]

    def children(self, task: str) -> list[str]:
        out = [t for t in self.tasks() if task in self.parents(t)]
        return sorted(out, key=self.tasks().index)

    def descendants(self, task: str) -> set[str]:
        seen: set[str] = set()
        stack = self.children(task)
        while stack:
            t = stack.pop()
            if t not in seen:
                seen.add(t)
                stack.extend(self.children(t))
        return seen

    def ancestors(self, task: str) -> set[str]:
        return {t for t in self.tasks() if task in self.descendants(t)}

    def impact(self, task: str) -> tuple[int, int]:
        """(blocked downstream tasks, blocked consumers) if ``task`` fails terminally."""
        nodes = {task} | self.descendants(task)
        consumers = {c for i, cs in self.consumers.items() if self.chain[i] in nodes for c in cs}
        return len(nodes) - 1, len(consumers)


DAGS: dict[str, DagDef] = {
    d.dag_id: d
    for d in (
        DagDef("daily_customer_etl", 120,
               ("extract_customer_data", "validate_customer_data", "transform_customer_data",
                "load_customer_dim", "publish_customer_mart"), ("audit_log_export", 1),
               {4: ("daily_revenue_report", "crm_sync", "churn_features")}),
        DagDef("orders_ingest_hourly", 60,
               ("extract_orders", "dedupe_orders", "load_orders_fact", "refresh_orders_cache"),
               ("export_order_audit", 2), {2: ("sales_dashboard", "finance_recon")}),
        DagDef("inventory_snapshot", 180,
               ("pull_stock_levels", "normalize_sku", "write_snapshot"), ("notify_warehouse", 2),
               {2: ("replenishment_planner",)}),
        DagDef("payments_reconcile", 90,
               ("fetch_gateway_files", "parse_settlements", "match_ledger", "publish_recon_report"),
               ("archive_gateway_files", 0),
               {3: ("finance_close", "fraud_scoring", "treasury_dashboard", "audit_feed")}),
        DagDef("marketing_attribution", 240,
               ("extract_clicks", "sessionize", "attribute_conversions", "load_attribution_mart"),
               ("refresh_campaign_cache", 3), {3: ("campaign_dashboard",)}),
        DagDef("product_catalog_sync", 60,
               ("extract_catalog", "validate_catalog_schema", "upsert_catalog", "rebuild_search_index"),
               ("publish_catalog_stats", 2),
               {2: ("storefront_api", "search_indexer", "recommendation_features")}),
        DagDef("hr_headcount_weekly", 1440,
               ("extract_headcount", "mask_columns", "load_headcount"), ("send_summary_email", 2), {}),
        DagDef("sensor_telemetry_rollup", 30,
               ("ingest_events", "aggregate_hourly", "load_rollup", "compact_partitions"),
               ("emit_rollup_metrics", 1),
               {1: ("ops_dashboard", "anomaly_detector", "capacity_planner")}),
        DagDef("supplier_feed_ingest", 360,
               ("download_supplier_files", "parse_supplier_json", "load_supplier_stage",
                "publish_supplier_dim"), ("cleanup_supplier_tmp", 0),
               {3: ("procurement_report", "price_monitor")}),
        DagDef("ledger_export_nightly", 120,
               ("extract_ledger", "transform_ledger", "export_to_s3", "verify_export"),
               ("archive_old_exports", 2), {2: ("auditor_portal", "tax_reporting", "bi_gateway")}),
        DagDef("support_tickets_sync", 120,
               ("extract_tickets", "enrich_tickets", "load_ticket_fact", "refresh_sla_view"),
               ("post_sync_digest", 2), {3: ("support_dashboard",)}),
        DagDef("loyalty_points_calc", 300,
               ("extract_purchases", "calc_points", "publish_points"), ("log_calc_stats", 1),
               {2: ("mobile_app_api",)}),
        DagDef("fleet_gps_etl", 15,
               ("ingest_gps_pings", "snap_to_routes", "load_trip_fact"), ("emit_trip_metrics", 1),
               {1: ("dispatch_board", "eta_service", "driver_scorecard", "maintenance_planner")}),
        DagDef("billing_invoice_run", 180,
               ("collect_usage", "rate_usage", "generate_invoices", "send_invoices"),
               ("write_run_summary", 2), {2: ("customer_portal", "revenue_recognition", "dunning_service")}),
    )
}

# --------------------------------------------------------------------------------------------
# Gold rules
# --------------------------------------------------------------------------------------------
TERMINAL = ("failed", "upstream_failed")
NON_TERMINAL_OUTCOMES = ("retrying", "recovered", "alert_only")


def severity_level(outcome: str, n_tasks: int, n_consumers: int) -> int:
    if outcome in ("recovered", "alert_only"):
        return 0
    if outcome == "retrying":
        return 1
    if n_tasks == 0 and n_consumers == 0:
        return 1
    return 3 if n_consumers >= 3 else 2


def human_required(family: str, outcome: str, severity: int) -> bool:
    if outcome in NON_TERMINAL_OUTCOMES:
        return False
    if family in ("authentication", "schema_mismatch", "other_unknown"):
        return True
    if family == "resource_exhaustion":
        return severity >= 2
    return severity == 3  # connectivity, data_error, dependency_failure


# --------------------------------------------------------------------------------------------
# Fault kinds
# --------------------------------------------------------------------------------------------
KIND_FAMILY = {
    "undefined_column": "schema_mismatch",
    "missing_relation": "schema_mismatch",
    "ddl_rename": "schema_mismatch",
    "dns_refused": "connectivity",
    "socket_timeout": "connectivity",
    "wrapped_conn": "connectivity",
    "password_auth": "authentication",
    "access_denied": "authentication",
    "s3_access_denied": "authentication",
    "wrapped_auth": "authentication",
    "type_conversion": "data_error",
    "malformed_json": "data_error",
    "missing_s3_object": "data_error",
    "oom": "resource_exhaustion",
    "disk_full": "resource_exhaustion",
    "wrapped_resource": "resource_exhaustion",
    "dep_sensor_timeout": "dependency_failure",
    "dep_external_failed": "dependency_failure",
    "dep_upstream_failed": "dependency_failure",
    "alert_only": "other_unknown",
    "opaque_exit1": "other_unknown",
    "opaque_sigkill": "other_unknown",
    "server_closed": "other_unknown",
    "two_cause": "other_unknown",
}
KIND_COUNTS = {
    "undefined_column": 25, "missing_relation": 16, "ddl_rename": 16,
    "dns_refused": 16, "socket_timeout": 18, "wrapped_conn": 6,
    "password_auth": 16, "access_denied": 17, "s3_access_denied": 6, "wrapped_auth": 6,
    "type_conversion": 16, "malformed_json": 16, "missing_s3_object": 12,
    "oom": 16, "disk_full": 16, "wrapped_resource": 4,
    "dep_sensor_timeout": 14, "dep_external_failed": 12, "dep_upstream_failed": 12,
    "alert_only": 10, "opaque_exit1": 8, "opaque_sigkill": 6, "server_closed": 8, "two_cause": 8,
}
OUTCOME_WEIGHTS: dict[str, dict[str, float]] = {
    "dns_refused": {"failed": 0.4, "retrying": 0.3, "recovered": 0.3},
    "socket_timeout": {"failed": 0.4, "retrying": 0.3, "recovered": 0.3},
    "wrapped_conn": {"failed": 0.4, "retrying": 0.3, "recovered": 0.3},
    "oom": {"failed": 0.7, "recovered": 0.3},
    "missing_s3_object": {"failed": 0.5, "retrying": 0.2, "recovered": 0.3},
    "server_closed": {"failed": 0.7, "retrying": 0.3},
    "alert_only": {"alert_only": 1.0},
    "dep_upstream_failed": {"upstream_failed": 1.0},
}
AMBIGUOUS_KINDS = {"wrapped_conn", "wrapped_auth", "wrapped_resource", "dep_upstream_failed"}
INSUFFICIENT_KINDS = {"alert_only", "opaque_exit1", "opaque_sigkill", "server_closed", "two_cause"}
SQL_KINDS = {"undefined_column", "missing_relation", "ddl_rename", "type_conversion"}
DECOY_KINDS = {
    "undefined_column", "missing_relation", "ddl_rename", "type_conversion",
    "dns_refused", "socket_timeout", "wrapped_conn", "password_auth", "access_denied",
}
TWO_CAUSE_PAIRS = (
    ("password_auth", "missing_relation"),
    ("dns_refused", "type_conversion"),
    ("access_denied", "undefined_column"),
    ("socket_timeout", "malformed_json"),
)

# Exception type -> family where the type alone identifies the error.
TYPE_FAMILY = {
    "psycopg2.errors.UndefinedColumn": "schema_mismatch",
    "psycopg2.errors.UndefinedTable": "schema_mismatch",
    "socket.gaierror": "connectivity",
    "ConnectionRefusedError": "connectivity",
    "socket.timeout": "connectivity",
    "psycopg2.errors.InvalidPassword": "authentication",
    "psycopg2.errors.InsufficientPrivilege": "authentication",
    "psycopg2.errors.InvalidTextRepresentation": "data_error",
    "json.decoder.JSONDecodeError": "data_error",
    "botocore.errorfactory.NoSuchKey": "data_error",
    "MemoryError": "resource_exhaustion",
    "java.lang.OutOfMemoryError": "resource_exhaustion",
    "psycopg2.errors.DiskFull": "resource_exhaustion",
    "airflow.exceptions.AirflowSensorTimeout": "dependency_failure",
    "airflow.sensors.external_task.ExternalDagFailedError": "dependency_failure",
    "UpstreamTaskFailed": "dependency_failure",
}
# Types that wrap many causes; the message decides, and only when exactly one signature matches.
GENERIC_TYPES = {
    "psycopg2.OperationalError",
    "airflow.exceptions.AirflowException",
    "botocore.exceptions.ClientError",
    "OSError",
}
SIGNATURES = {
    "authentication": re.compile(r"password authentication failed|\(AccessDenied\)|permission denied|no pg_hba\.conf"),
    "connectivity": re.compile(r"could not translate host name|Name or service not known|Connection refused|timeout expired"),
    "resource_exhaustion": re.compile(r"No space left on device|OutOfMemory|Cannot allocate memory|OOMKilled"),
    "data_error": re.compile(r"\(NoSuchKey\)|invalid input syntax|JSONDecodeError"),
    "schema_mismatch": re.compile(r"(column|relation|table) \"?[\w.]+\"? does not exist"),
}

# --------------------------------------------------------------------------------------------
# Identifier pools (fictional; reserved example.org hosts and TEST-NET addresses)
# --------------------------------------------------------------------------------------------
TABLES = ["staging.customers_raw", "staging.orders_raw", "core.customer_dim", "core.orders_fact",
          "mart.daily_revenue", "raw.payments_events", "core.sku_master", "staging.supplier_feed",
          "core.ticket_fact", "mart.loyalty_points"]
COLUMNS = ["customer_id", "order_total", "sku_code", "created_at", "region_code", "invoice_no",
           "account_ref", "segment", "tax_amount", "channel_id"]
RENAMES = [("cust_id", "customer_id"), ("amount", "order_total"), ("sku", "sku_code"),
           ("ts", "created_at"), ("region", "region_code"), ("inv_no", "invoice_no")]
DECOY_COLUMNS = ["password_hash", "auth_token", "timeout_ms", "disk_quota_gb", "upstream_ref", "oom_score"]
DECOY_TABLES = ["staging.connection_events", "core.permission_grants", "raw.timeout_log", "staging.disk_usage_snapshot"]
HOSTS = ["warehouse-db.example.org", "staging-db.example.org", "ledger-db.example.org",
         "feeds-db.example.org", "metrics-db.example.org"]
DECOY_HOSTS = ["auth-gateway-db.example.org", "disk-archive-db.example.org", "permission-db.example.org"]
USERS = ["svc_etl", "svc_loader", "svc_report", "svc_ingest"]
DECOY_USERS = ["svc_timeout", "svc_diskwatch", "svc_dnsprobe"]
BUCKETS = ["dl-raw-example", "dl-curated-example", "partner-drop-example"]
BAD_VALUES = ["12x", "N/A", "1,204", "0x1F", "--"]
DECOY_VALUES = ["timeout", "denied", "disk full"]
NOTES = (None, "same_error_prev_run", "manual_rerun_failed", "owner_notified", "no_config_change", "first_occurrence")

MISLEADING_NOISE = [
    ("UserWarning", "Connection pool warm-up failed (non-fatal): connection refused, using fallback replica"),
    ("UserWarning", "Permission denied for temp dir /tmp/airflow_scratch, falling back to /var/tmp"),
    ("UserWarning", "Column cache refreshed; column \"legacy_id\" does not exist in cache, skipped"),
    ("UserWarning", "Health probe timed out once and was retried successfully"),
    ("UserWarning", "Worker volume at 71% usage; no space issue reported"),
]
PLAIN_NOISE = [
    ("DeprecationWarning", "pandas.DataFrame.append is deprecated and will be removed in a future version"),
    ("FutureWarning", "Passing method to Index.get_loc is deprecated"),
]


# --------------------------------------------------------------------------------------------
# Event construction
# --------------------------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(dag_id: str, run_id: str, ts: datetime, task: str, status: str, message: str, *,
           exc: str | None = None, retry: int = 0, dur: int = 0, up: str | None = None,
           dep: str | None = None) -> dict[str, Any]:
    return {
        "event_ts": _iso(ts), "dag_id": dag_id, "task_id": task, "run_id": run_id, "status": status,
        "message": message, "exception_type": exc, "retry_count": retry, "duration_ms": dur,
        "upstream_task": up, "deployment_id": dep,
    }


def make_ctx(rng: random.Random, kind: str, decoy: bool) -> dict[str, Any]:
    old, new = rng.choice(RENAMES)
    col = rng.choice(DECOY_COLUMNS if decoy and kind == "undefined_column" else COLUMNS)
    tbl = rng.choice(DECOY_TABLES if decoy and kind == "missing_relation" else TABLES)
    return {
        "table": tbl, "col": col, "old": old, "new": new,
        "host": rng.choice(DECOY_HOSTS if decoy else HOSTS), "ip": f"203.0.113.{rng.randint(2, 250)}",
        "user": rng.choice(DECOY_USERS if decoy else USERS),
        "bucket": rng.choice(BUCKETS), "obj": f"landing/part-{rng.randint(1, 99):04d}.parquet",
        "value": rng.choice(DECOY_VALUES if decoy else BAD_VALUES),
        "json_line": rng.randint(1, 40), "json_col": rng.randint(5, 120),
        "json_char": rng.randint(200, 9000), "gib": f"{rng.uniform(2, 24):.1f}",
        "secs": rng.choice([30, 60, 120]), "variant": rng.randint(0, 1),
        "sensor_s": rng.choice([3600, 7200]), "ext_dag": rng.choice(sorted(DAGS)),
        "ext_task": rng.choice(["load_done", "export_ready", "publish_complete"]),
        "pair": rng.choice(TWO_CAUSE_PAIRS), "dec": decoy,
    }


def attempt_error(kind: str, ctx: dict[str, Any], a: int, task: str) -> tuple[str, str, int]:
    """(exception_type, message, duration_ms) of failing attempt ``a`` for ``kind``."""
    v = ctx["variant"]
    c = ctx
    if kind == "undefined_column":
        return "psycopg2.errors.UndefinedColumn", f'column "{c["col"]}" does not exist', 900
    if kind == "missing_relation":
        return "psycopg2.errors.UndefinedTable", f'relation "{c["table"]}" does not exist', 700
    if kind == "ddl_rename":
        tbl = c["table"].split(".")[-1]
        return ("psycopg2.errors.UndefinedColumn",
                f'column "{c["old"]}" does not exist HINT: Perhaps you meant to reference the column "{tbl}.{c["new"]}".',
                950)
    if kind == "dns_refused":
        if v:
            return "ConnectionRefusedError", f'[Errno 111] Connection refused (host {c["host"]}, port 5432)', 1500
        return "socket.gaierror", f'[Errno -2] Name or service not known: {c["host"]}', 1200
    if kind == "socket_timeout":
        return "socket.timeout", f'timed out after {c["secs"]}s connecting to {c["host"]}:5432', c["secs"] * 1000
    if kind == "wrapped_conn":
        if v:
            return ("psycopg2.OperationalError",
                    f'connection to server at "{c["host"]}" ({c["ip"]}), port 5432 failed: timeout expired', 30000)
        return ("psycopg2.OperationalError",
                f'could not translate host name "{c["host"]}" to address: Name or service not known', 1100)
    if kind == "password_auth":
        if v:
            return "psycopg2.errors.InvalidPassword", f'password authentication failed for user "{c["user"]}"', 800
        return ("psycopg2.OperationalError",
                f'connection to server at "{c["host"]}" ({c["ip"]}), port 5432 failed: FATAL: password authentication failed for user "{c["user"]}"',
                850)
    if kind == "access_denied":
        return "psycopg2.errors.InsufficientPrivilege", f'permission denied for table {c["table"].split(".")[-1]}', 650
    if kind == "s3_access_denied":
        return ("botocore.exceptions.ClientError",
                "An error occurred (AccessDenied) when calling the GetObject operation: Access Denied", 420)
    if kind == "wrapped_auth":
        return ("airflow.exceptions.AirflowException",
                f'Bash command failed. stderr tail: psql: FATAL: password authentication failed for user "{c["user"]}"', 1800)
    if kind == "type_conversion":
        return "psycopg2.errors.InvalidTextRepresentation", f'invalid input syntax for type integer: "{c["value"]}"', 3100
    if kind == "malformed_json":
        return ("json.decoder.JSONDecodeError",
                f"Expecting ',' delimiter: line {c['json_line']} column {c['json_col']} (char {c['json_char']})", 400)
    if kind == "missing_s3_object":
        if v:
            return ("botocore.errorfactory.NoSuchKey",
                    f"An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist. key={c['obj']}",
                    380)
        return ("botocore.exceptions.ClientError",
                f"An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist. key={c['obj']}",
                380)
    if kind == "oom":
        if v:
            return "MemoryError", f"Unable to allocate {c['gib']} GiB for an array", 52000
        return "java.lang.OutOfMemoryError", "Java heap space", 61000
    if kind == "disk_full":
        if v:
            return "psycopg2.errors.DiskFull", f'could not extend file "base/16384/{rng_file(c)}": No space left on device', 2600
        return "OSError", f"[Errno 28] No space left on device: '/tmp/airflow/{task}/part-0007.parquet'", 4200
    if kind == "wrapped_resource":
        return ("airflow.exceptions.AirflowException",
                "Bash command failed. stderr tail: OSError: [Errno 28] No space left on device", 3900)
    if kind == "dep_sensor_timeout":
        return ("airflow.exceptions.AirflowSensorTimeout",
                f"Sensor has timed out; run duration of {c['sensor_s'] + 12} seconds exceeds the specified timeout of {c['sensor_s']}.",
                c["sensor_s"] * 1000)
    if kind == "dep_external_failed":
        return ("airflow.sensors.external_task.ExternalDagFailedError",
                f"external task {c['ext_dag']}.{c['ext_task']} is in failed state", 2300)
    if kind == "opaque_exit1":
        return "airflow.exceptions.AirflowException", "Task exited with return code 1", 5400
    if kind == "opaque_sigkill":
        return "airflow.exceptions.AirflowException", "Task exited with return code Negsignal.SIGKILL", 47000
    if kind == "server_closed":
        return ("psycopg2.OperationalError",
                "server closed the connection unexpectedly This probably means the server terminated abnormally before or while processing the request.",
                6200)
    if kind == "two_cause":
        return attempt_error(c["pair"][a % 2], c, a, task)
    raise KeyError(kind)


def rng_file(c: dict[str, Any]) -> int:
    return 24000 + c["json_char"]


# --------------------------------------------------------------------------------------------
# Spec construction
# --------------------------------------------------------------------------------------------
def terminal_class(dag: DagDef, task: str) -> int:
    t, c = dag.impact(task)
    return severity_level("failed", t, c)


def _node_pools() -> dict[int, list[tuple[str, str]]]:
    pools: dict[int, list[tuple[str, str]]] = {1: [], 2: [], 3: []}
    for dag_id in sorted(DAGS):
        dag = DAGS[dag_id]
        for task in dag.tasks():
            if task != SENSOR:
                pools[terminal_class(dag, task)].append((dag_id, task))
    return pools


NODE_POOLS = _node_pools()
ANY_NODES = sorted(n for ns in NODE_POOLS.values() for n in ns)
# (dag, parent) pairs usable as the failing upstream of a victim task, with severity >= 2
UPSTREAM_PARENTS = sorted(
    (d, t) for c in (2, 3) for d, t in NODE_POOLS[c] if DAGS[d].children(t)
)


def build_spec(
    index: int,
    kind: str,
    dag_id: str,
    task: str,
    outcome: str,
    ctx: dict[str, Any],
    rng: random.Random,
    group_id: str,
    *,
    max_retries: int = 2,
    deployment_id: str | None = None,
    note: str | None = None,
    mixed: bool = False,
    stale: bool = False,
    sibling: bool = False,
    noise: str = "none",  # none | plain | misleading
    retries_done: int | None = None,
    seed: int = 0,
) -> ScenarioSpec:
    """Assemble one scenario: fault events + surroundings, gold from the fault and DAG graph."""
    dag = DAGS[dag_id]
    run_date = FIRST_RUN_DATE + timedelta(days=index - 1)
    run_id = f"scheduled__{run_date.isoformat()}T02:00:00+00:00"
    t0 = datetime(run_date.year, run_date.month, run_date.day, 2, 0, 0)
    topo = dag.tasks()
    start_of = {t: t0 + timedelta(minutes=7 * i) for i, t in enumerate(topo)}
    events: list[dict[str, Any]] = []

    def ev(ts: datetime, tsk: str, status: str, msg: str, **kw: Any) -> None:
        events.append(_event(dag_id, run_id, ts, tsk, status, msg, **kw))

    upstream_failed = outcome == "upstream_failed"
    victim = task
    root = dag.parents(task)[0] if upstream_failed else task  # task whose failure causes the impact
    parent_of_task = dag.parents(task)[0] if dag.parents(task) else None
    dep = deployment_id

    # healthy ancestors of the root task
    for anc in sorted(dag.ancestors(root), key=topo.index):
        ev(start_of[anc] + timedelta(seconds=rng.randint(5, 50)), anc, "success", "Task finished successfully",
           dur=rng.randint(2000, 90000), up=(dag.parents(anc) or [None])[0])

    def fail_attempts(tsk: str, n: int, final: str, k_kind: str, base: datetime) -> datetime:
        """Emit n failing attempts for ``tsk``; the last gets status ``final``. Returns last ts."""
        ts = base
        for a in range(n):
            exc, msg, dur = attempt_error(k_kind, ctx, a, tsk)
            ts = base + timedelta(minutes=5 * a, seconds=rng.randint(0, 40))
            status = final if a == n - 1 else "up_for_retry"
            ev(ts, tsk, status, msg, exc=exc, retry=a, dur=dur, up=parent_of_task if tsk == task else None, dep=dep)
        return ts

    first_ts = start_of[root] + timedelta(seconds=rng.randint(10, 40))
    last_ts = first_ts
    retries_left = None
    if outcome == "failed":
        last_ts = fail_attempts(task, max_retries + 1, "failed", kind, start_of[task] + timedelta(seconds=15))
        first_ts = start_of[task] + timedelta(seconds=15)
    elif outcome == "retrying":
        k = retries_done if retries_done is not None else rng.randint(0, max_retries - 1)
        first_ts = start_of[task] + timedelta(seconds=15)
        last_ts = fail_attempts(task, k + 1, "up_for_retry", kind, first_ts)
        retries_left = max_retries - k
    elif outcome == "recovered":
        k = retries_done if retries_done is not None else rng.randint(1, max_retries)
        first_ts = start_of[task] + timedelta(seconds=15)
        fail_attempts(task, k, "up_for_retry", kind, first_ts)
        last_ts = first_ts + timedelta(minutes=5 * k, seconds=rng.randint(0, 40))
        ev(last_ts, task, "success", "Task finished successfully", retry=k, dur=rng.randint(20000, 120000),
           up=parent_of_task, dep=dep)
    elif outcome == "alert_only":
        first_ts = start_of[task] + timedelta(seconds=15)
        ev(first_ts, task, "running", "Task started", up=parent_of_task, dep=dep)
        last_ts = first_ts + timedelta(minutes=14, seconds=rng.randint(0, 40))
        ev(last_ts, task, "alert", "Duration alert: task running longer than the 900s threshold", up=parent_of_task,
           dep=dep, dur=900000)
        ev(last_ts + timedelta(minutes=3), task, "success", "Task finished successfully", dur=1080000,
           up=parent_of_task, dep=dep)
        last_ts += timedelta(minutes=3)
    elif upstream_failed:
        first_ts = start_of[root] + timedelta(seconds=15)
        last_up = fail_attempts(root, max_retries + 1, "failed", "opaque_exit1", first_ts)
        last_ts = last_up + timedelta(seconds=45)
        ev(last_ts, victim, "upstream_failed", f"Task not run: upstream task {root} failed (trigger_rule=all_success)",
           exc="UpstreamTaskFailed", up=root, dep=dep)
    else:
        raise ValueError(outcome)

    # impact (scheduler) event
    n_tasks, n_cons = dag.impact(root)
    if outcome in ("failed", "upstream_failed"):
        ev(last_ts + timedelta(seconds=20), "dag_monitor", "blocked",
           f"downstream impact: tasks={n_tasks} consumers={n_cons} sla_minutes={dag.sla_minutes}", dep=dep)
        for desc in sorted(dag.descendants(root) - {victim}, key=topo.index):
            ev(last_ts + timedelta(seconds=30), desc, "skipped", "Task not run: upstream task failed")
    elif outcome == "retrying":
        ev(last_ts + timedelta(seconds=20), "dag_monitor", "at_risk",
           f"downstream impact: tasks={n_tasks} consumers={n_cons} sla_minutes={dag.sla_minutes} "
           f"pending retry retries_left={retries_left}", dep=dep)
    shown_tasks = {task, root}

    # surroundings
    others = [t for t in topo if t not in shown_tasks and t not in dag.ancestors(root) and t not in dag.descendants(root)]
    free = others or [t for t in topo if t not in shown_tasks and t not in dag.ancestors(root)]
    if mixed and free:
        other = rng.choice(free)
        o_exc, o_msg, o_dur = attempt_error("socket_timeout" if KIND_FAMILY[kind] != "connectivity" else "missing_relation",
                                            ctx, 0, other)
        ev(first_ts + timedelta(minutes=1, seconds=3), other, "up_for_retry", o_msg, exc=o_exc, retry=0, dur=o_dur, dep=dep)
    if noise != "none" and free:
        pool = MISLEADING_NOISE if noise == "misleading" else PLAIN_NOISE
        for j in range(2 if noise == "misleading" else 1):
            n_exc, n_msg = pool[(index + j) % len(pool)]
            tsk = free[j % len(free)]
            ev(first_ts + timedelta(minutes=2 + j, seconds=7 * j + 1), tsk, "success", n_msg, exc=n_exc,
               dur=rng.randint(1000, 9000), dep=dep)
        if noise == "misleading":
            tsk = free[-1]
            ev(first_ts + timedelta(minutes=4, seconds=11), tsk, "success", "Task finished successfully",
               retry=1, dur=rng.randint(1000, 9000), dep=dep)
    if stale:
        s_exc, s_msg, s_dur = attempt_error("missing_relation" if KIND_FAMILY[kind] != "schema_mismatch" else "dns_refused",
                                            ctx, 0, task)
        ev(t0 - timedelta(hours=5), task, "failed", s_msg, exc=s_exc, retry=0, dur=s_dur)
    if sibling:
        s_exc, s_msg, s_dur = attempt_error("password_auth" if KIND_FAMILY[kind] != "authentication" else "oom", ctx, 0, task)
        sib = _event(dag_id, f"manual__{run_date.isoformat()}T04:15:00+00:00", first_ts + timedelta(minutes=2), task,
                     "failed", s_msg, exc=s_exc, retry=0, dur=s_dur)
        events.append(sib)

    window = {"start": _iso(first_ts - timedelta(minutes=20)), "end": _iso(last_ts + timedelta(minutes=20))}
    family = KIND_FAMILY[kind]
    severity = severity_level(outcome, n_tasks, n_cons)
    gold = {"failure_family": family, "severity": severity, "requires_human": human_required(family, outcome, severity)}

    if kind in INSUFFICIENT_KINDS:
        sufficiency = "insufficient"
    elif kind in AMBIGUOUS_KINDS or mixed:
        sufficiency = "ambiguous"
    else:
        sufficiency = "sufficient"
    tags: list[str] = []
    if kind in SQL_KINDS:
        tags.append("sql_identifier")
    if note is not None or outcome == "retrying":
        tags.append("code_switching")
    if dep is None or note == "no_config_change" or outcome == "alert_only":
        tags.append("negation")
    if ctx["dec"] or noise == "misleading":
        tags.append("hard_negative")
    if mixed or stale or sibling or noise != "none":
        tags.append("distractor")
    if sufficiency == "insufficient":
        tags.append("insufficient_evidence")
    if sufficiency == "ambiguous":
        tags.append("ambiguous")

    facts = {
        "dag_id": dag_id, "task_id": task, "run_id": run_id, "window": window, "outcome": outcome,
        "deployment_id": dep, "note": note, "mixed": mixed, "events": events,
        "_internal": {"fault_id": f"fault-{index:04d}", "kind": kind, "decoy": ctx["dec"],
                      "impact_task": root, "max_retries": max_retries},
    }
    return ScenarioSpec(
        scenario_id=scenario_id(CASE, index), experiment=CASE, group_id=group_id, facts=facts, gold=gold,
        difficulty_tags=tags, evidence_sufficiency=sufficiency, label_source=LABEL_SOURCE, seed=seed,
    )


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = sorted(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def generate_specs(seed: int) -> list[ScenarioSpec]:
    rng = random.Random(seed + CASE_OFFSET)
    groups: list[tuple[str, int]] = []  # (kind, size)
    for kind in sorted(KIND_COUNTS):
        n = KIND_COUNTS[kind]
        sizes = [2] * (n // 2)
        if n % 2:
            sizes[-1] = 3
        groups += [(kind, s) for s in sizes]
    rng.shuffle(groups)

    specs: list[ScenarioSpec] = []
    index = 0
    for g_i, (kind, size) in enumerate(groups, start=1):
        group_id = f"E01-G{g_i:03d}-{kind}"
        decoy = kind in DECOY_KINDS and rng.random() < 0.3
        ctx = make_ctx(rng, kind, decoy)
        weights = OUTCOME_WEIGHTS.get(kind, {"failed": 1.0})
        first_outcome = _weighted(rng, weights)
        # choose the alerted task from the DAG catalog
        if kind in ("dep_sensor_timeout", "dep_external_failed"):
            dag_id, task = rng.choice(sorted(DAGS)), SENSOR
        elif kind == "dep_upstream_failed":
            dag_id, parent = rng.choice(UPSTREAM_PARENTS)
            task = DAGS[dag_id].children(parent)[0]
        elif first_outcome in NON_TERMINAL_OUTCOMES:
            dag_id, task = rng.choice(ANY_NODES)
        else:
            target = int(_weighted(rng, {"1": 0.2, "2": 0.45, "3": 0.35}))
            dag_id, task = rng.choice(NODE_POOLS[target])
        dep_p = 0.8 if KIND_FAMILY[kind] == "schema_mismatch" else 0.4
        dep_id = f"dep-{rng.randint(1000, 9999)}" if rng.random() < dep_p else None
        for m in range(size):
            index += 1
            outcome = first_outcome if m == 0 else _weighted(rng, weights)
            sufficient_kind = kind not in AMBIGUOUS_KINDS and kind not in INSUFFICIENT_KINDS
            noise = "misleading" if (kind not in DECOY_KINDS and rng.random() < 0.25) else rng.choice(["none", "plain", "none"])
            specs.append(
                build_spec(
                    index, kind, dag_id, task, outcome, ctx, rng, group_id,
                    max_retries=rng.choice([1, 2, 3]),
                    deployment_id=dep_id,
                    note=rng.choice(NOTES),
                    mixed=sufficient_kind and rng.random() < 0.1,
                    stale=rng.random() < 0.2,
                    sibling=rng.random() < 0.2,
                    noise=noise,
                    seed=seed,
                )
            )
    return specs


# --------------------------------------------------------------------------------------------
# DB seed
# --------------------------------------------------------------------------------------------
BACKGROUND_MESSAGES = ("Task finished successfully", "Task finished successfully", "Rows written and committed")


def db_seed(specs: list[ScenarioSpec], seed: int) -> dict[str, Any]:
    """All scenario events plus healthy background runs, up to MIN_DOCS documents."""
    rng = random.Random(seed + CASE_OFFSET + 1000)
    sources: list[dict[str, Any]] = [dict(e) for s in specs for e in s.facts["events"]]
    dag_ids = sorted(DAGS)
    day = date(2025, 1, 1)
    n = 0
    while len(sources) < MIN_DOCS:
        dag = DAGS[dag_ids[n % len(dag_ids)]]
        d = day + timedelta(days=n // len(dag_ids))
        hour = rng.choice([0, 1, 3, 4, 5, 6])
        run_id = f"scheduled__{d.isoformat()}T{hour:02d}:30:00+00:00"
        t0 = datetime(d.year, d.month, d.day, hour, 30, 0)
        for i, t in enumerate(dag.tasks()):
            ts = t0 + timedelta(minutes=6 * i, seconds=rng.randint(0, 40))
            retry = 1 if rng.random() < 0.03 else 0
            sources.append(_event(dag.dag_id, run_id, ts, t, "success", rng.choice(BACKGROUND_MESSAGES), retry=retry,
                                  dur=rng.randint(1500, 120000), up=(dag.parents(t) or [None])[0]))
            if rng.random() < 0.05:
                exc, msg = rng.choice(PLAIN_NOISE)
                sources.append(_event(dag.dag_id, run_id, ts + timedelta(seconds=2), t, "success", msg, exc=exc))
        n += 1
    docs = [{"_id": f"log-{i:06d}", "_source": src} for i, src in enumerate(sources, start=1)]
    return {"docs": docs}


def make_backend(settings: Settings) -> EvidenceBackend:
    return OpenSearchBackend(settings)


# --------------------------------------------------------------------------------------------
# Evidence reference + oracle
# --------------------------------------------------------------------------------------------
def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    f = spec.facts
    events = rank_events(f["events"], f["window"], f["dag_id"], f["run_id"])
    return {"dag_id": f["dag_id"], "run_id": f["run_id"], "events": events}


IMPACT_RE = re.compile(r"tasks=(\d+) consumers=(\d+) sla_minutes=(\d+)")
LEFT_RE = re.compile(r"retries_left=(\d+)")


def parse_outcome(events: list[dict[str, Any]], task: str) -> dict[str, Any]:
    """Outcome and impact read back from retrieved events (independent of generator internals)."""
    mine = sorted((e for e in events if e["task_id"] == task), key=lambda e: (e["event_ts"], e["retry_count"]))
    impact = next((e for e in events if e["status"] in IMPACT_STATUSES), None)
    info: dict[str, Any] = {"outcome": None, "tasks": None, "consumers": None, "sla": None, "retries_left": None}
    if impact:
        m = IMPACT_RE.search(impact["message"])
        if m:
            info["tasks"], info["consumers"], info["sla"] = (int(x) for x in m.groups())
        lm = LEFT_RE.search(impact["message"])
        info["retries_left"] = int(lm.group(1)) if lm else None
    if any(e["status"] == "upstream_failed" for e in mine):
        info["outcome"] = "upstream_failed"
    elif mine and mine[-1]["status"] == "success" and mine[-1]["retry_count"] >= 1:
        info["outcome"] = "recovered"
    elif impact and impact["status"] == "at_risk":
        info["outcome"] = "retrying"
    elif any(e["status"] == "failed" for e in mine):
        info["outcome"] = "failed"
    elif any(e["status"] == "alert" for e in mine):
        info["outcome"] = "alert_only"
    return info


def _family_from_events(events: list[dict[str, Any]], task: str) -> str | None:
    fams: set[str] = set()
    ambiguous = False
    for e in events:
        if e["task_id"] != task or e["status"] not in FAIL_STATUSES:
            continue
        fam = TYPE_FAMILY.get(e["exception_type"] or "")
        if fam is None and e["exception_type"] in GENERIC_TYPES:
            hit = {f for f, rx in SIGNATURES.items() if rx.search(e["message"])}
            if len(hit) == 1:
                fam = next(iter(hit))
            elif len(hit) > 1:
                ambiguous = True
        if fam:
            fams.add(fam)
    return next(iter(fams)) if len(fams) == 1 and not ambiguous else None


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expected = expected_evidence(spec)
    if facts["events"] != expected["events"]:
        got = {(e["event_ts"], e["task_id"], e["retry_count"]) for e in facts["events"]}
        want = {(e["event_ts"], e["task_id"], e["retry_count"]) for e in expected["events"]}
        problems.append(f"retrieved events differ from reference: missing={sorted(want - got)[:3]} extra={sorted(got - want)[:3]}")
        return problems
    task = spec.facts["task_id"]
    kind = spec.facts["_internal"]["kind"]
    info = parse_outcome(facts["events"], task)
    if info["outcome"] != spec.facts["outcome"]:
        problems.append(f"outcome from DB {info['outcome']!r} != injected {spec.facts['outcome']!r}")
        return problems
    n_tasks, n_cons = (info["tasks"], info["consumers"]) if info["tasks"] is not None else (0, 0)
    sev = severity_level(info["outcome"], n_tasks, n_cons)
    if sev != spec.gold["severity"]:
        problems.append(f"severity from DB graph facts {sev} != gold {spec.gold['severity']}")
    # exception signature of the injected fault is present (or, for unresolved faults, deliberately absent)
    fam = _family_from_events(facts["events"], task)
    gold_family = spec.gold["failure_family"]
    if gold_family == "other_unknown":
        if fam is not None:
            problems.append(f"evidence disambiguates to {fam} but gold is other_unknown ({kind})")
    elif fam != gold_family:
        problems.append(f"log signature gives {fam!r}, gold {gold_family!r} ({kind})")
    if human_required(gold_family, info["outcome"], sev) != spec.gold["requires_human"]:
        problems.append("requires_human differs from runbook rules applied to DB facts")
    blob = " ".join(str(v) for e in facts["events"] for v in e.values()).lower()
    for marker in ("fault-", "gold", "_internal"):
        if marker in blob:
            problems.append(f"internal marker {marker!r} found in indexed evidence")
    return problems


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "failed": "Retries exhausted; the task is in failed state.",
        "retrying": "Attempt {a}/{n} failed; retry scheduled ({left} retries left).",
        "recovered": "The task failed earlier but succeeded on retry {k}.",
        "alert_only": "An alert fired, but no failed attempt is recorded.",
        "upstream_failed": "The task did not run because an upstream task failed.",
        "impact": "Downstream impact: {t} downstream task(s) and {c} consumer(s); SLA {s} minutes.",
        "impact_pending": "If retries are exhausted: {t} downstream task(s) and {c} consumer(s); SLA {s} minutes.",
        "no_impact": "No downstream impact is reported.",
        "dep_yes": "Deployment {d} is active for this run.",
        "dep_no": "No deployment is recorded for this run.",
        "same_error_prev_run": "The same error was seen in the previous run.",
        "manual_rerun_failed": "A manual rerun did not fix it.",
        "owner_notified": "The pipeline owner has been notified.",
        "no_config_change": "No configuration change was made today.",
        "first_occurrence": "This is the first occurrence this month.",
    },
    "th": {
        "failed": "retry ครบแล้ว และงานอยู่ในสถานะ failed",
        "retrying": "attempt {a}/{n} ล้มเหลว; ตั้งเวลา retry แล้ว (เหลือ retry อีก {left} ครั้ง)",
        "recovered": "งานล้มเหลวก่อนหน้านี้ แต่สำเร็จเมื่อ retry ครั้งที่ {k}",
        "alert_only": "มี alert แต่ไม่พบ attempt ที่ล้มเหลวในบันทึก",
        "upstream_failed": "งานไม่ได้รันเพราะ upstream task ล้มเหลว",
        "impact": "ผลกระทบ downstream: {t} task ปลายน้ำและผู้ใช้ข้อมูล {c} ราย; SLA {s} นาที",
        "impact_pending": "หาก retry ครบ: {t} task ปลายน้ำและผู้ใช้ข้อมูล {c} ราย; SLA {s} นาที",
        "no_impact": "ไม่มีรายงานผลกระทบ downstream",
        "dep_yes": "รอบการรันนี้ใช้ deployment {d}",
        "dep_no": "ไม่มี deployment ที่บันทึกไว้สำหรับรอบการรันนี้",
        "same_error_prev_run": "พบ error เดียวกันในรอบก่อนหน้า",
        "manual_rerun_failed": "ลอง rerun ด้วยตนเองแล้วแต่ยังไม่หาย",
        "owner_notified": "แจ้งเจ้าของ pipeline แล้ว",
        "no_config_change": "วันนี้ไม่มีการแก้ไข config",
        "first_occurrence": "เพิ่งเกิดเป็นครั้งแรกในเดือนนี้",
    },
}


def _log_line(e: dict[str, Any]) -> str:
    exc = f" {e['exception_type']}:" if e["exception_type"] else ""
    return f"{e['event_ts']} {e['status']}({e['retry_count']}){exc} {e['message']}"


def _shown_events(spec: ScenarioSpec, facts: dict[str, Any], context_level: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(events shown in log_excerpt, remaining retrieved events)."""
    task = spec.facts["task_id"]
    events = facts["events"]
    relevant = [e for e in events if e["status"] in FAIL_STATUSES or e["status"] == "alert"
                or (e["status"] == "success" and e["task_id"] == task and e["retry_count"] >= 1)]
    # keep the last attempt of repeated identical errors per task
    keep: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for e in relevant:
        keep[(e["task_id"], e["exception_type"], e["message"])] = e
    shown = sorted(keep.values(), key=lambda e: (e["event_ts"], e["task_id"], e["retry_count"]))
    if context_level != "natural_long" and len(shown) > 3:
        mine = [e for e in shown if e["task_id"] == task]
        rest = [e for e in shown if e["task_id"] != task]
        shown = sorted((mine + rest)[:3], key=lambda e: (e["event_ts"], e["task_id"], e["retry_count"]))
    ids = {id(e) for e in shown}
    remaining = [e for e in events if id(e) not in ids and e["status"] not in IMPACT_STATUSES]
    return shown, remaining


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    tx = TEXT[lang]
    task = spec.facts["task_id"]
    info = parse_outcome(facts["events"], task)
    shown, remaining = _shown_events(spec, facts, context_level)

    outcome = info["outcome"]
    mine = [e for e in facts["events"] if e["task_id"] == task]
    last_retry = max((e["retry_count"] for e in mine), default=0)
    if outcome == "retrying":
        left = info["retries_left"] or 0
        status = tx["retrying"].format(a=last_retry + 1, n=last_retry + 1 + left, left=left)
    elif outcome == "recovered":
        status = tx["recovered"].format(k=last_retry)
    elif outcome in ("failed", "upstream_failed", "alert_only"):
        status = tx[outcome]
    else:
        status = tx["failed"]

    if info["tasks"] is None:
        impact = tx["no_impact"]
    else:
        key = "impact_pending" if outcome == "retrying" else "impact"
        impact = tx[key].format(t=info["tasks"], c=info["consumers"], s=info["sla"])

    dep_ids = sorted({e["deployment_id"] for e in mine if e["deployment_id"]})
    parts = [tx["dep_yes"].format(d=dep_ids[0]) if dep_ids else tx["dep_no"]]
    note = spec.facts.get("note")
    if note and context_level != "equal_information":
        parts.append(tx[note])

    state: dict[str, Any] = {
        "dag": spec.facts["dag_id"],
        "task": task,
        "run_id": spec.facts["run_id"],
        "status": status,
        "log_excerpt": "\n".join(_log_line(e) for e in shown),
        "impact": impact,
    }
    if context_level != "equal_information":
        state["context"] = " ".join(parts)
    if context_level == "natural_long" and remaining:
        state["other_events"] = "\n".join(f"{e['task_id']}: " + _log_line(e) for e in remaining)
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    tokens = [spec.facts["dag_id"], spec.facts["task_id"], spec.facts["run_id"]]
    shown, _ = _shown_events(spec, facts, "common_fit")
    tokens += [_log_line(e) for e in shown]
    return tokens


# --------------------------------------------------------------------------------------------
# Rules baseline
# --------------------------------------------------------------------------------------------
def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Exception-type / signature regex / runbook mapping; abstains (None) when evidence is mixed."""
    task = spec.facts["task_id"]
    events = facts["events"]
    info = parse_outcome(events, task)
    family = _family_from_events(events, task)
    severity: int | None = None
    if info["outcome"] is not None:
        n_tasks, n_cons = (info["tasks"], info["consumers"]) if info["tasks"] is not None else (0, 0)
        severity = severity_level(info["outcome"], n_tasks, n_cons)
    human: bool | None = None
    if info["outcome"] in NON_TERMINAL_OUTCOMES:
        human = False
    elif family is not None and severity is not None:
        human = human_required(family, info["outcome"], severity)
    return {"failure_family": family, "severity": severity, "requires_human": human}
