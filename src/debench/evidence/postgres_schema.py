"""E03 evidence backend: catalog snapshots, migrations and a consumer-query bank on PostgreSQL.

Each scenario runs in one transaction on the shared base schema ``shop``:

1. baseline snapshot + consumer run (computed once after load, cached);
2. the migration runs inside a savepoint (a failing migration is rolled back to the savepoint and its
   SQLSTATE recorded);
3. the after-snapshot is taken and the consumer bank is run again;
4. the whole transaction is rolled back, so scenarios never see each other's changes.

The bank has read consumers (SELECT) and ingest probes (INSERT), which are executed inside a savepoint
and always rolled back. A probe is the only way to observe ``NOT NULL``/default drift on a write path.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import psycopg

from debench.config import ROOT, Settings
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import BackendUnavailable, Evidence, timed
from debench.evidence.pg_runtime import PostgresRuntime

SCHEMA = "shop"
INIT_DIR = ROOT / "infra" / "init" / "e03_postgres"
BANK_VERSION = "e03-bank-v1"
STATEMENT_TIMEOUT_MS = 10_000

DIFF_KEYS = (
    "tables_added",
    "tables_removed",
    "columns_added",
    "columns_removed",
    "columns_changed",
    "constraints_added",
    "constraints_removed",
    "indexes_added",
    "indexes_removed",
    "views_added",
    "views_removed",
    "views_changed",
    "comments_changed",
)

# SQLSTATE -> psycopg error class name, the "actual PostgreSQL error class" reported in evidence.
ERROR_CLASS = {
    "0A000": "FeatureNotSupported",
    "22001": "StringDataRightTruncation",
    "22003": "NumericValueOutOfRange",
    "22007": "InvalidDatetimeFormat",
    "22P02": "InvalidTextRepresentation",
    "23502": "NotNullViolation",
    "23505": "UniqueViolation",
    "23514": "CheckViolation",
    "2BP01": "DependentObjectsStillExist",
    "42703": "UndefinedColumn",
    "42804": "DatatypeMismatch",
    "42883": "UndefinedFunction",
    "42P01": "UndefinedTable",
    "42P16": "InvalidTableDefinition",
}


@dataclass(frozen=True)
class Consumer:
    name: str
    kind: str  # "read" | "write_probe"
    tier: str  # "critical" | "standard" (SLA policy tier)
    sql: str


def _r(name: str, tier: str, sql: str) -> Consumer:
    return Consumer(name, "read", tier, sql)


def _w(name: str, tier: str, sql: str) -> Consumer:
    return Consumer(name, "write_probe", tier, sql)


BANK: tuple[Consumer, ...] = (
    _r("order_revenue_daily", "critical",
       "SELECT date_trunc('day', placed_at) AS d, sum(order_total) AS revenue FROM orders GROUP BY 1 ORDER BY 1"),
    _r("customer_lookup_email", "critical",
       "SELECT customer_id, full_name FROM customers WHERE email = 'user0007@example.com'"),
    _r("orders_by_status", "standard", "SELECT status, count(*) FROM orders GROUP BY status ORDER BY status"),
    _r("order_lines_join", "critical",
       "SELECT o.order_id, i.product_id, i.quantity * i.unit_price AS line_total FROM orders o "
       "JOIN order_items i ON i.order_id = o.order_id ORDER BY 1, 2, 3"),
    _r("payment_reconcile", "critical",
       "SELECT p.order_id, p.amount - o.order_total AS diff FROM payments p JOIN orders o ON o.order_id = p.order_id "
       "WHERE p.status = 'captured' ORDER BY 1"),
    _r("payments_by_method", "standard",
       "SELECT method, status, sum(amount) FROM payments GROUP BY 1, 2 ORDER BY 1, 2"),
    _r("stock_by_warehouse", "standard",
       "SELECT w.code, sum(i.qty_on_hand) FROM inventory i JOIN warehouses w ON w.warehouse_id = i.warehouse_id "
       "GROUP BY 1 ORDER BY 1"),
    _r("low_stock_view", "standard",
       "SELECT product_id, warehouse_id, qty_on_hand FROM v_low_stock ORDER BY 1, 2"),
    _r("price_list", "standard",
       "SELECT sku, name, unit_price FROM products WHERE is_active ORDER BY sku"),
    _r("expensive_products", "standard", "SELECT sku FROM products WHERE unit_price > 100 ORDER BY sku"),
    _r("shipping_weight", "standard",
       "SELECT sku, weight_kg * 1000 AS grams FROM products WHERE weight_kg IS NOT NULL ORDER BY sku"),
    _r("customers_by_country", "standard",
       "SELECT country_code, count(*) FROM customers GROUP BY 1 ORDER BY 1"),
    _r("customer_phone_export", "standard",
       "SELECT customer_id, phone FROM customers WHERE phone IS NOT NULL ORDER BY 1"),
    _r("customer_orders_view", "critical",
       "SELECT customer_id, email, orders_count, lifetime_total FROM v_customer_orders ORDER BY 1"),
    _r("order_totals_view", "standard", "SELECT order_id, items_total FROM v_order_totals ORDER BY 1"),
    _r("daily_sales_report", "critical",
       "SELECT sales_date, gross_amount FROM daily_sales_summary ORDER BY 1"),
    _r("ltv_report", "standard", "SELECT customer_id, lifetime_value FROM report_customer_ltv ORDER BY 1"),
    _r("shipment_latency", "standard",
       "SELECT shipment_id, delivered_at - shipped_at AS transit FROM shipments WHERE delivered_at IS NOT NULL ORDER BY 1"),
    _r("postal_prefix", "standard",
       "SELECT address_id FROM customer_addresses WHERE postal_code LIKE '10%' ORDER BY 1"),
    _r("avg_rating_cast", "standard",
       "SELECT product_id, avg(rating::numeric) AS avg_rating FROM reviews GROUP BY 1 ORDER BY 1"),
    _r("top_rated", "standard", "SELECT product_id FROM reviews WHERE rating >= 4 ORDER BY 1"),
    _r("supplier_lead", "standard",
       "SELECT supplier_id, lead_time_days + 2 AS promised_days FROM suppliers ORDER BY 1"),
    _r("refund_totals", "standard",
       "SELECT p.method, sum(r.amount) FROM refunds r JOIN payments p ON p.payment_id = r.payment_id "
       "GROUP BY 1 ORDER BY 1"),
    _r("coupon_active", "standard",
       "SELECT code, discount_pct FROM coupons WHERE valid_until >= DATE '2026-06-01' ORDER BY 1"),
    _r("customer_profile_star", "standard", "SELECT * FROM customers ORDER BY customer_id LIMIT 20"),
    _r("order_snapshot_star", "standard", "SELECT * FROM orders ORDER BY order_id LIMIT 20"),
    _r("order_ref_text", "standard",
       "SELECT order_id::text || '-' || status AS ref FROM orders ORDER BY order_id"),
    _r("po_cost", "standard",
       "SELECT po_id, sum(qty * unit_cost) FROM purchase_order_lines GROUP BY 1 ORDER BY 1"),
    _r("revenue_view", "critical", "SELECT sales_day, revenue FROM v_daily_revenue ORDER BY 1"),
    _r("payment_status_view", "standard", "SELECT order_id, status FROM v_payment_status ORDER BY 1"),
    _r("movement_net", "standard",
       "SELECT product_id, sum(delta) FROM inventory_movements GROUP BY 1 ORDER BY 1"),
    _r("channel_mix", "standard", "SELECT channel, count(*) FROM orders GROUP BY 1 ORDER BY 1"),
    _r("tracking_prefix", "standard",
       "SELECT shipment_id FROM shipments WHERE tracking_no LIKE '66%' ORDER BY 1"),
    _w("ingest_customer", "critical",
       "INSERT INTO customers (email, full_name, phone) VALUES ('probe01@example.com', 'Probe One', NULL)"),
    _w("ingest_order", "critical",
       "INSERT INTO orders (customer_id, status, order_total, placed_at) "
       "VALUES (1, 'pending', 10.50, timestamptz '2026-06-01 00:00:00+00')"),
    _w("ingest_order_item", "standard",
       "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (1, 1, 1, 10.00)"),
    _w("ingest_payment", "critical",
       "INSERT INTO payments (order_id, method, amount, status, reference) "
       "VALUES (1, 'card', 10.50, 'authorized', 'PROBE-0001')"),
    _w("ingest_movement", "standard",
       "INSERT INTO inventory_movements (product_id, warehouse_id, delta, reason) VALUES (1, 1, 5, 'receipt')"),
    _w("ingest_audit", "standard", "INSERT INTO audit_log (table_name, payload) VALUES ('orders', '{}')"),
    _w("ingest_etl_batch", "standard", "INSERT INTO etl_batches (source, row_count) VALUES ('probe', 0)"),
    _w("ingest_inventory_snapshot", "standard",
       "INSERT INTO report_inventory_snapshot (snapshot_date, product_id, qty) VALUES (DATE '2026-06-01', 1, 5)"),
    _w("ingest_stg_order", "standard",
       "INSERT INTO stg_orders_import (raw_order_id, customer_email, amount) VALUES ('X-1', 'probe@example.com', 9.99)"),
    _w("ingest_review", "standard",
       "INSERT INTO reviews (product_id, customer_id, rating) VALUES (1, 1, 5)"),
    _w("ingest_shipment", "standard",
       "INSERT INTO shipments (order_id, carrier) VALUES (1, 'kerry')"),
)
BANK_BY_NAME = {c.name: c for c in BANK}
CRITICAL = frozenset(c.name for c in BANK if c.tier == "critical")


def _norm_type(data_type: str, char_len: int | None, prec: int | None, scale: int | None) -> str:
    if data_type == "numeric":
        return f"numeric({prec},{scale})" if prec is not None else "numeric"
    if data_type == "character varying":
        return f"varchar({char_len})" if char_len else "varchar"
    if data_type == "character":
        return f"char({char_len})"
    return {"integer": "int", "timestamp with time zone": "timestamptz"}.get(data_type, data_type)


_CON_KIND = {"p": "primary key", "u": "unique", "f": "foreign key", "c": "check"}


def snapshot(cur: psycopg.Cursor) -> dict[str, Any]:
    """Catalog snapshot of schema ``shop``: columns, constraints, indexes, views, view dependencies, comments."""
    cur.execute(
        "SELECT c.table_name, c.column_name, c.data_type, c.character_maximum_length, c.numeric_precision, "
        "c.numeric_scale, c.is_nullable, c.column_default, c.is_identity "
        "FROM information_schema.columns c JOIN information_schema.tables t "
        "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE' ORDER BY 1, c.ordinal_position",
        (SCHEMA,),
    )
    columns: dict[str, dict[str, Any]] = {}
    tables: set[str] = set()
    for tbl, col, dt, cl, prec, scale, nullable, default, ident in cur.fetchall():
        tables.add(tbl)
        columns[f"{tbl}.{col}"] = {
            "type": _norm_type(dt, cl, prec, scale),
            "nn": nullable == "NO",
            "default": default is not None or ident == "YES",
        }
    cur.execute(
        "SELECT cl.relname, co.conname, co.contype FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
        "JOIN pg_namespace n ON n.oid = cl.relnamespace WHERE n.nspname = %s AND co.contype IN ('p','u','f','c')",
        (SCHEMA,),
    )
    constraints = {f"{t}.{n}": _CON_KIND[k] for t, n, k in cur.fetchall()}
    cur.execute("SELECT tablename, indexname FROM pg_indexes WHERE schemaname = %s", (SCHEMA,))
    indexes = {f"{t}.{n}": t for t, n in cur.fetchall()}
    cur.execute("SELECT viewname, definition FROM pg_views WHERE schemaname = %s", (SCHEMA,))
    views = {n: re.sub(r"\s+", " ", d).strip() for n, d in cur.fetchall()}
    cur.execute(
        "SELECT view_name, table_name, column_name FROM information_schema.view_column_usage "
        "WHERE view_schema = %s ORDER BY 1, 2, 3",
        (SCHEMA,),
    )
    view_deps = sorted(f"{v}<-{t}.{c}" for v, t, c in cur.fetchall())
    cur.execute(
        "SELECT c.relname, a.attname, d.description FROM pg_description d JOIN pg_class c ON c.oid = d.objoid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_attribute a "
        "ON a.attrelid = c.oid AND a.attnum = d.objsubid AND d.objsubid > 0 "
        "WHERE d.classoid = 'pg_class'::regclass AND n.nspname = %s",
        (SCHEMA,),
    )
    comments = {(f"{t}.{a}" if a else t): d for t, a, d in cur.fetchall()}
    return {
        "tables": sorted(tables),
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "views": views,
        "view_deps": view_deps,
        "comments": comments,
    }


def _col_str(key: str, col: dict[str, Any]) -> str:
    return f"{key} {col['type']} {'not null' if col['nn'] else 'null'}{' default' if col['default'] else ''}"


def catalog_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Sorted, human-readable diff. Columns/constraints/indexes of added or removed tables are folded
    into the table-level entry."""
    out: dict[str, list[str]] = {k: [] for k in DIFF_KEYS}
    t_before, t_after = set(before["tables"]), set(after["tables"])
    out["tables_added"] = sorted(t_after - t_before)
    out["tables_removed"] = sorted(t_before - t_after)
    skip = (t_after ^ t_before)

    def table_of(key: str) -> str:
        return key.split(".", 1)[0]

    bc, ac = before["columns"], after["columns"]
    for k in sorted(set(ac) - set(bc)):
        if table_of(k) not in skip:
            out["columns_added"].append(_col_str(k, ac[k]))
    for k in sorted(set(bc) - set(ac)):
        if table_of(k) not in skip:
            out["columns_removed"].append(_col_str(k, bc[k]))
    for k in sorted(set(bc) & set(ac)):
        b, a = bc[k], ac[k]
        if b["type"] != a["type"]:
            out["columns_changed"].append(f"{k} type {b['type']}->{a['type']}")
        if b["nn"] != a["nn"]:
            out["columns_changed"].append(f"{k} nullable {'not null' if b['nn'] else 'null'}->{'not null' if a['nn'] else 'null'}")
        if b["default"] != a["default"]:
            out["columns_changed"].append(f"{k} default {'set' if b['default'] else 'none'}->{'set' if a['default'] else 'none'}")
    for k in sorted(set(after["constraints"]) - set(before["constraints"])):
        if table_of(k) not in skip:
            out["constraints_added"].append(f"{k} {after['constraints'][k]}")
    for k in sorted(set(before["constraints"]) - set(after["constraints"])):
        if table_of(k) not in skip:
            out["constraints_removed"].append(f"{k} {before['constraints'][k]}")
    for k in sorted(set(after["indexes"]) - set(before["indexes"])):
        if table_of(k) not in skip:
            out["indexes_added"].append(k)
    for k in sorted(set(before["indexes"]) - set(after["indexes"])):
        if table_of(k) not in skip:
            out["indexes_removed"].append(k)
    bv, av = before["views"], after["views"]
    out["views_added"] = sorted(set(av) - set(bv))
    out["views_removed"] = sorted(set(bv) - set(av))
    out["views_changed"] = sorted(v for v in set(bv) & set(av) if bv[v] != av[v])
    bm, am = before["comments"], after["comments"]
    for k in sorted(set(bm) | set(am)):
        if bm.get(k) != am.get(k):
            out["comments_changed"].append(f"{k}: {am[k]}" if k in am else f"{k}: (removed)")
    return out


def _digest(rows: list[tuple[Any, ...]]) -> str:
    lines = sorted("|".join("\x00" if v is None else str(v) for v in row) for row in rows)
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


def run_bank(cur: psycopg.Cursor, bank: tuple[Consumer, ...] = BANK) -> dict[str, dict[str, Any]]:
    """Run every consumer in its own savepoint; nothing a probe writes survives."""
    results: dict[str, dict[str, Any]] = {}
    for c in bank:
        cur.execute("SAVEPOINT consumer")
        try:
            cur.execute(c.sql)  # type: ignore[arg-type]
            if c.kind == "read":
                rows = cur.fetchall()
                shape = [d.name for d in cur.description or []]
                results[c.name] = {"ok": True, "shape": shape, "digest": _digest(rows)}
            else:
                results[c.name] = {"ok": True, "shape": [], "digest": ""}
        except psycopg.Error as exc:
            results[c.name] = {"ok": False, "sqlstate": exc.sqlstate, "error_class": type(exc).__name__}
        cur.execute("ROLLBACK TO SAVEPOINT consumer")
        cur.execute("RELEASE SAVEPOINT consumer")
    return results


def compare_runs(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failed, result_changed, shape_changed = {}, [], []
    for name, a in after.items():
        b = before[name]
        if not a["ok"]:
            failed[name] = {"sqlstate": a["sqlstate"], "error_class": a["error_class"]}
        elif b["ok"] and a["shape"] != b["shape"]:
            shape_changed.append(name)
        elif b["ok"] and a["digest"] != b["digest"]:
            result_changed.append(name)
    return {
        "total": len(after),
        "run": True,
        "failed": failed,
        "result_changed": sorted(result_changed),
        "shape_changed": sorted(shape_changed),
    }


def run_scenario(
    conn: psycopg.Connection, statements: list[str], base_snapshot: dict[str, Any], base_run: dict[str, Any]
) -> dict[str, Any]:
    """Apply one migration inside a transaction, measure, and roll everything back."""
    migration: dict[str, Any] = {
        "ok": True,
        "sqlstate": None,
        "error_class": None,
        "data_rewritten": False,
    }
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT migration")
            try:
                for stmt in statements:
                    cur.execute(stmt)  # type: ignore[arg-type]
                    if stmt.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) and cur.rowcount > 0:
                        migration["data_rewritten"] = True
            except psycopg.Error as exc:
                migration.update(ok=False, sqlstate=exc.sqlstate, error_class=type(exc).__name__)
                migration["data_rewritten"] = False
                cur.execute("ROLLBACK TO SAVEPOINT migration")
            after = snapshot(cur)
            if migration["ok"]:
                consumers = compare_runs(base_run, run_bank(cur))
            else:
                consumers = {"total": len(base_run), "run": False, "failed": {}, "result_changed": [], "shape_changed": []}
    finally:
        conn.rollback()
    return {
        "migration": migration,
        "catalog_diff": catalog_diff(base_snapshot, after),
        "consumers": consumers,
        "excerpt": after,
    }


class PostgresSchemaBackend:
    name = "postgres"

    def __init__(self, settings: Settings | None = None, case: str = "E03"):
        self._rt = PostgresRuntime(case)
        self.runtime = self._rt.runtime
        self._conn: psycopg.Connection | None = None
        self._base_snapshot: dict[str, Any] = {}
        self._base_run: dict[str, Any] = {}
        self._version = ""

    def engine_version(self) -> str:
        return self._version

    def setup(self) -> None:
        try:
            uri = self._rt.start()
            self._conn = psycopg.connect(uri, autocommit=True)
        except BackendUnavailable:
            raise
        except Exception as exc:  # engine could not start or accept connections
            raise BackendUnavailable(f"PostgreSQL not available: {type(exc).__name__}") from exc
        self._version = self._conn.execute("select version()").fetchone()[0]  # type: ignore[index]

    def load(self, seed_payload: Any = None) -> None:
        assert self._conn is not None
        self._conn.autocommit = True
        for path in sorted(INIT_DIR.glob("*.sql")):
            self._conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        self._conn.execute(f"SET search_path = {SCHEMA}")
        self._conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        self._conn.autocommit = False
        with self._conn.cursor() as cur:
            self._base_snapshot = snapshot(cur)
            self._base_run = run_bank(cur)
        self._conn.rollback()

    def baseline_failures(self) -> list[str]:
        return sorted(n for n, r in self._base_run.items() if not r["ok"])

    def table_row_counts(self) -> dict[str, int]:
        assert self._conn is not None
        out = {}
        with self._conn.cursor() as cur:
            for t in self._base_snapshot["tables"]:
                cur.execute(f'SELECT count(*) FROM "{t}"')  # type: ignore[arg-type]
                out[t] = cur.fetchone()[0]  # type: ignore[index]
        self._conn.rollback()
        return out

    def measure(self, statements: list[str]) -> dict[str, Any]:
        assert self._conn is not None
        return run_scenario(self._conn, statements, self._base_snapshot, self._base_run)

    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        change = spec.facts["_internal"]["change"]
        statements: list[str] = change["stmts"]
        with timed() as t:
            res = self.measure(statements)
        facts = {
            "migration": {"sql": "\n".join(statements), **res["migration"]},
            "catalog_diff": res["catalog_diff"],
            "consumers": res["consumers"],
            "baseline_failed": self.baseline_failures(),
            "excerpt": _excerpt(res["excerpt"], change),
        }
        ref = {
            "backend": "postgres",
            "schema": SCHEMA,
            "bank_version": BANK_VERSION,
            "migration_sha256": hashlib.sha256(facts["migration"]["sql"].encode()).hexdigest(),
            "consumer_count": len(BANK),
        }
        return Evidence(facts=facts, ref=ref, db_ms=t["ms"])

    def teardown(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._rt.stop()


def _excerpt(after: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Post-migration catalog lines for the objects the change touches (used by long-context rendering)."""
    touched = set(change.get("touched", []))
    cols = [_col_str(k, v) for k, v in sorted(after["columns"].items()) if k.split(".", 1)[0] in touched]
    deps = [d for d in after["view_deps"] if any(d.split("<-", 1)[1].startswith(t + ".") for t in touched)]
    return {"columns_after": cols, "view_deps": deps}


def make_backend(settings: Settings | None = None) -> PostgresSchemaBackend:
    return PostgresSchemaBackend(settings)
