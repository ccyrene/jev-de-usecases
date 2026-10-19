"""E03 - Schema Drift Detection on PostgreSQL (PLAN section 6).

Each scenario is one migration applied to the synthetic ``shop`` schema (27 tables, 5 views). The
backend measures the real before/after catalog diff and runs a bank of 44 consumer queries (33
SELECTs plus 11 INSERT ingest probes that are always rolled back). Gold is a deterministic function
of those measurements; the generator carries an independent model of every change and
``oracle_check`` rejects a scenario if PostgreSQL disagrees with that model.

Change-management policy (the gold oracle)
------------------------------------------
drift_impact, first match wins:

1. Failures (migration error or failing consumer/probe), by SQLSTATE family:
   missing/dependent object (42703, 42P01, 2BP01, 42P16)    -> breaking_rename_drop
   type, cast, operator or value errors (42883, 42804, 22xxx, 0A000, 42846) -> type_or_cast_break
   integrity errors (23xxx)                                  -> constraint_or_nullability_break
2. Consumers that still run but return different data (same column set):
   documented meaning change in the catalog comment ("Meaning changed ...") -> semantic_change,
   otherwise (silent value change from a type change)                        -> type_or_cast_break
3. Documented meaning change without observed data change                    -> semantic_change
4. A column changes SQL type category (numeric/text/date/...)                -> type_or_cast_break
5. Only indexes, comments, storage or nothing changed                        -> no_change_or_unknown
6. Anything else (additive, widening, loosened or satisfied constraints,
   dropped columns that no consumer reads)                                   -> non_breaking_addition

migration_risk (0 safe .. 4 critical):
  migration fails at apply time (transactional, nothing deployed)            -> 2
  consumers fail: critical consumer and (>= 2 failures or irreversible)      -> 4
                  critical consumer, or >= 2 failures                        -> 3
                  otherwise                                                  -> 2
  results silently change: 2, +1 if a critical consumer changed, +1 if irreversible (max 4)
  no failure and no data change: no_change 0; documented meaning change or
  type category change 2; other compatible change 1, +1 if irreversible
Critical consumers (SLA tier): order_revenue_daily, customer_lookup_email, order_lines_join,
payment_reconcile, customer_orders_view, daily_sales_report, revenue_view, ingest_customer,
ingest_order, ingest_payment. Irreversible = dropped column or table (not a rename), narrowing or
category-changing type change, or data rewritten by the migration.

review_before_deploy = migration_risk >= 2 (manual review rule).

rules_baseline = catalog diff + consumer validation only: it sees SQLSTATEs and the diff, not the
result-digest comparison or reversibility, so it can under-rate silent changes.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from typing import Any

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import ScenarioSpec
from debench.evidence.postgres_schema import (
    BANK,
    CRITICAL,
    DIFF_KEYS,
    ERROR_CLASS,
    INIT_DIR,
    PostgresSchemaBackend,
)

CASE = "E03"
BACKEND = "postgres"
LABEL_SOURCE = "catalog_diff_and_consumer_queries_in_postgres"
Q_IMPACT, Q_RISK, Q_REVIEW = "drift_impact", "migration_risk", "review_before_deploy"

# ---------------------------------------------------------------------------------------------
# Independent model of the base schema (what the generator believes the SQL files create)
# ---------------------------------------------------------------------------------------------

_COLSPEC = {
    "categories": "category_id:int!d name:text! parent_id:int",
    "customers": "customer_id:bigint!d email:text! full_name:text! phone:text country_code:char(2)!d "
    "status:text!d loyalty_points:int!d created_at:timestamptz!d legacy_segment:text",
    "customer_addresses": "address_id:bigint!d customer_id:bigint! line1:text! city:text "
    "postal_code:varchar(10) is_default:boolean!d legacy_region:text",
    "suppliers": "supplier_id:int!d name:text! contact_email:text lead_time_days:smallint!d legacy_fax:text",
    "products": "product_id:bigint!d sku:text! name:text! category_id:int supplier_id:int "
    "unit_price:numeric(12,2)! weight_kg:numeric(8,3) is_active:boolean!d legacy_code:text",
    "warehouses": "warehouse_id:int!d code:text! city:text capacity_units:int",
    "inventory": "inventory_id:bigint!d product_id:bigint! warehouse_id:int! qty_on_hand:int! "
    "reorder_level:int!d updated_at:timestamptz!d legacy_bin:text",
    "inventory_movements": "movement_id:bigint!d product_id:bigint! warehouse_id:int! delta:int! "
    "reason:text! moved_at:timestamptz!d",
    "purchase_orders": "po_id:bigint!d supplier_id:int! status:text! ordered_at:timestamptz! "
    "total_amount:numeric(12,2)!",
    "purchase_order_lines": "po_line_id:bigint!d po_id:bigint! product_id:bigint! qty:int! "
    "unit_cost:numeric(12,2)!",
    "orders": "order_id:bigint!d customer_id:bigint! status:text! order_total:numeric(12,2)! "
    "currency:char(3)!d placed_at:timestamptz! channel:text internal_note:text",
    "order_items": "order_item_id:bigint!d order_id:bigint! product_id:bigint! quantity:int! "
    "unit_price:numeric(12,2)! discount_pct:numeric(5,2)!d",
    "shipments": "shipment_id:bigint!d order_id:bigint! carrier:text! tracking_no:text "
    "shipped_at:timestamptz delivered_at:timestamptz",
    "payment_methods": "method_id:int!d code:text! label:text",
    "payments": "payment_id:bigint!d order_id:bigint! method:text! amount:numeric(12,2)! "
    "currency:char(3)!d status:text! paid_at:timestamptz reference:text",
    "refunds": "refund_id:bigint!d payment_id:bigint! amount:numeric(12,2)! reason:text refunded_at:timestamptz!",
    "coupons": "coupon_id:int!d code:text! discount_pct:numeric(5,2)! valid_until:date",
    "order_coupons": "order_id:bigint! coupon_id:int!",
    "reviews": "review_id:bigint!d product_id:bigint! customer_id:bigint! rating:smallint! body:text "
    "created_at:timestamptz!d",
    "daily_sales_summary": "sales_date:date! orders_count:int! gross_amount:numeric(14,2)! "
    "refunded_amount:numeric(14,2)!d",
    "report_customer_ltv": "customer_id:bigint! lifetime_value:numeric(14,2)! last_order_at:timestamptz",
    "report_inventory_snapshot": "snapshot_id:bigint!d snapshot_date:date! product_id:bigint! qty:int!",
    "audit_log": "audit_id:bigint!d table_name:text! changed_at:timestamptz!d payload:jsonb",
    "etl_batches": "batch_id:bigint!d source:text! loaded_at:timestamptz!d row_count:int!",
    "stg_orders_import": "stg_id:bigint!d raw_order_id:text! customer_email:text amount:numeric(12,2)",
}

# constraints "name:kind" (p primary key, u unique, f foreign key, c check) and extra plain indexes
_OBJSPEC = {
    "categories": ("pk_categories:p uq_categories_name:u", ""),
    "customers": ("pk_customers:p uq_customers_email:u ck_customers_status:c", "ix_customers_country"),
    "customer_addresses": ("pk_customer_addresses:p fk_customer_addresses_customer:f", "ix_customer_addresses_customer"),
    "suppliers": ("pk_suppliers:p", ""),
    "products": ("pk_products:p uq_products_sku:u fk_products_category:f fk_products_supplier:f",
                 "ix_products_category ix_products_active"),
    "warehouses": ("pk_warehouses:p uq_warehouses_code:u", ""),
    "inventory": (
        "pk_inventory:p uq_inventory_product_wh:u ck_inventory_qty:c fk_inventory_product:f fk_inventory_warehouse:f",
        "ix_inventory_warehouse",
    ),
    "inventory_movements": ("pk_inventory_movements:p fk_movements_product:f fk_movements_warehouse:f",
                            "ix_movements_product"),
    "purchase_orders": ("pk_purchase_orders:p fk_po_supplier:f", ""),
    "purchase_order_lines": ("pk_purchase_order_lines:p fk_po_lines_po:f fk_po_lines_product:f", ""),
    "orders": ("pk_orders:p ck_orders_status:c fk_orders_customer:f",
               "ix_orders_customer ix_orders_placed_at ix_orders_status"),
    "order_items": ("pk_order_items:p ck_order_items_qty:c fk_order_items_order:f fk_order_items_product:f",
                    "ix_order_items_order"),
    "shipments": ("pk_shipments:p fk_shipments_order:f", "ix_shipments_order"),
    "payment_methods": ("pk_payment_methods:p uq_payment_methods_code:u", ""),
    "payments": ("pk_payments:p uq_payments_reference:u ck_payments_status:c fk_payments_order:f",
                 "ix_payments_order ix_payments_status"),
    "refunds": ("pk_refunds:p fk_refunds_payment:f", "ix_refunds_payment"),
    "coupons": ("pk_coupons:p uq_coupons_code:u", ""),
    "order_coupons": ("pk_order_coupons:p fk_order_coupons_order:f fk_order_coupons_coupon:f", ""),
    "reviews": ("pk_reviews:p ck_reviews_rating:c fk_reviews_product:f fk_reviews_customer:f", "ix_reviews_product"),
    "daily_sales_summary": ("pk_daily_sales_summary:p", ""),
    "report_customer_ltv": ("pk_report_customer_ltv:p", ""),
    "report_inventory_snapshot": ("pk_report_inventory_snapshot:p", ""),
    "audit_log": ("pk_audit_log:p", ""),
    "etl_batches": ("pk_etl_batches:p", ""),
    "stg_orders_import": ("pk_stg_orders_import:p", ""),
}
_KIND = {"p": "primary key", "u": "unique", "f": "foreign key", "c": "check"}

COLS: dict[tuple[str, str], dict[str, Any]] = {}
CONS: dict[str, tuple[str, str]] = {}  # name -> (table, kind label)
PLAIN_IDX: dict[str, str] = {}  # name -> table
for _t, _spec in _COLSPEC.items():
    for _tok in _spec.split():
        _name, _ty = _tok.split(":", 1)
        _nn, _df = False, False
        if _ty.endswith("!d"):
            _ty, _nn, _df = _ty[:-2], True, True
        elif _ty.endswith("!"):
            _ty, _nn = _ty[:-1], True
        COLS[(_t, _name)] = {"type": _ty, "nn": _nn, "default": _df}
for _t, (_cons, _idx) in _OBJSPEC.items():
    for _tok in _cons.split():
        _n, _k = _tok.split(":")
        CONS[_n] = (_t, _KIND[_k])
    for _n in _idx.split():
        PLAIN_IDX[_n] = _t


def _idx_names(t: str) -> list[str]:
    return [n for n, (tt, k) in CONS.items() if tt == t and k in ("primary key", "unique")] + [
        n for n, tt in PLAIN_IDX.items() if tt == t
    ]


# Column comments present in the base schema (they follow renames and vanish with dropped columns).
BASE_COMMENTS = {
    ("orders", "order_total"): "Order total including VAT.",
    ("products", "unit_price"): "List price in THB.",
    ("inventory", "qty_on_hand"): "Units on hand, single units.",
    ("customers", "status"): "Account lifecycle status.",
}
EMPTY_TABLES = {"report_inventory_snapshot", "audit_log", "etl_batches", "stg_orders_import"}
# Columns that hold NULLs in the seed data.
NULL_COLS = {
    ("customers", "phone"), ("products", "weight_kg"), ("shipments", "tracking_no"),
    ("shipments", "delivered_at"), ("payments", "paid_at"), ("reviews", "body"),
}
# Tables whose primary key is referenced by a foreign key, and tables used by views.
REFERENCED = {"categories", "customers", "suppliers", "products", "warehouses", "orders", "payments",
              "purchase_orders", "coupons"}
# Single-column constraints/indexes that disappear with their column.
COLOBJ = {
    ("customers", "email"): ["uq_customers_email"],
    ("customers", "status"): ["ck_customers_status"],
    ("customers", "country_code"): ["ix_customers_country"],
    ("products", "sku"): ["uq_products_sku"],
    ("products", "is_active"): ["ix_products_active"],
    ("inventory", "qty_on_hand"): ["ck_inventory_qty"],
    ("orders", "status"): ["ck_orders_status", "ix_orders_status"],
    ("orders", "placed_at"): ["ix_orders_placed_at"],
    ("order_items", "quantity"): ["ck_order_items_qty"],
    ("payments", "status"): ["ck_payments_status", "ix_payments_status"],
    ("payments", "reference"): ["uq_payments_reference"],
    ("reviews", "rating"): ["ck_reviews_rating"],
    ("categories", "name"): ["uq_categories_name"],
    ("warehouses", "code"): ["uq_warehouses_code"],
    ("coupons", "code"): ["uq_coupons_code"],
    ("payment_methods", "code"): ["uq_payment_methods_code"],
}

# Views: output column -> source columns, row-set columns, and every referenced base column.
VIEWS: dict[str, dict[str, Any]] = {
    "v_order_totals": {
        "out": {"order_id": ["orders.order_id"], "customer_id": ["orders.customer_id"],
                "placed_at": ["orders.placed_at"],
                "items_total": ["order_items.quantity", "order_items.unit_price"]},
        "rows": ["order_items.order_id"],
        "deps": ["orders.order_id", "orders.customer_id", "orders.placed_at", "order_items.order_id",
                 "order_items.quantity", "order_items.unit_price"],
        "from": "orders o JOIN order_items i ON i.order_id = o.order_id",
        "exprs": {"order_id": "o.order_id", "customer_id": "o.customer_id", "placed_at": "o.placed_at",
                  "items_total": "sum(i.quantity * i.unit_price)"},
        "group": "o.order_id, o.customer_id, o.placed_at",
        "extra": ("status", "o.status", "o.order_id, o.customer_id, o.placed_at, o.status"),
    },
    "v_customer_orders": {
        "out": {"customer_id": ["customers.customer_id"], "email": ["customers.email"],
                "orders_count": ["orders.order_id"], "lifetime_total": ["orders.order_total"]},
        "rows": ["orders.customer_id"],
        "deps": ["customers.customer_id", "customers.email", "orders.order_id", "orders.customer_id",
                 "orders.order_total"],
        "from": "customers c JOIN orders o ON o.customer_id = c.customer_id",
        "exprs": {"customer_id": "c.customer_id", "email": "c.email", "orders_count": "count(o.order_id)",
                  "lifetime_total": "sum(o.order_total)"},
        "group": "c.customer_id, c.email",
        "extra": ("country_code", "c.country_code", "c.customer_id, c.email, c.country_code"),
    },
    "v_payment_status": {
        "out": {"order_id": ["payments.order_id"], "amount": ["payments.amount"], "status": ["payments.status"]},
        "rows": [],
        "deps": ["payments.order_id", "payments.amount", "payments.status"],
        "from": "payments p",
        "exprs": {"order_id": "p.order_id", "amount": "p.amount", "status": "p.status"},
        "group": None,
        "extra": ("currency", "p.currency", None),
    },
    "v_low_stock": {
        "out": {"product_id": ["inventory.product_id"], "warehouse_id": ["inventory.warehouse_id"],
                "qty_on_hand": ["inventory.qty_on_hand"], "reorder_level": ["inventory.reorder_level"]},
        "rows": ["inventory.qty_on_hand", "inventory.reorder_level"],
        "deps": ["inventory.product_id", "inventory.warehouse_id", "inventory.qty_on_hand",
                 "inventory.reorder_level"],
        "from": "inventory s",
        "where": "s.qty_on_hand < s.reorder_level",
        "exprs": {"product_id": "s.product_id", "warehouse_id": "s.warehouse_id",
                  "qty_on_hand": "s.qty_on_hand", "reorder_level": "s.reorder_level"},
        "group": None,
        "extra": ("updated_at", "s.updated_at", None),
    },
    "v_daily_revenue": {
        "out": {"sales_day": ["orders.placed_at"], "revenue": ["orders.order_total"]},
        "rows": ["orders.placed_at"],
        "deps": ["orders.placed_at", "orders.order_total"],
        "from": "orders o",
        "exprs": {"sales_day": "date_trunc('day', o.placed_at)::date", "revenue": "sum(o.order_total)"},
        "group": "date_trunc('day', o.placed_at)::date",
        "extra": ("orders_count", "count(*)", "date_trunc('day', o.placed_at)::date"),
    },
}
VIEW_DEP_COLS = {tuple(d.split(".")) for v in VIEWS.values() for d in v["deps"]}
VIEW_BASE_TABLES = {t for t, _ in VIEW_DEP_COLS}

# What every consumer touches: "table.column:usage" (views are modelled as tables).
_USES = {
    "order_revenue_daily": "orders.placed_at:date_func orders.order_total:numeric_agg",
    "customer_lookup_email": "customers.email:text_eq customers.customer_id:plain customers.full_name:plain",
    "orders_by_status": "orders.status:group",
    "order_lines_join": "orders.order_id:join order_items.order_id:join order_items.product_id:plain "
    "order_items.quantity:numeric_arith order_items.unit_price:numeric_arith",
    "payment_reconcile": "payments.order_id:join payments.amount:numeric_arith orders.order_id:join "
    "orders.order_total:numeric_arith payments.status:text_eq",
    "payments_by_method": "payments.method:group payments.status:group payments.amount:numeric_agg",
    "stock_by_warehouse": "warehouses.code:group warehouses.warehouse_id:join inventory.warehouse_id:join "
    "inventory.qty_on_hand:numeric_agg",
    "low_stock_view": "v_low_stock.product_id:plain v_low_stock.warehouse_id:plain v_low_stock.qty_on_hand:plain",
    "price_list": "products.sku:plain products.name:plain products.unit_price:plain products.is_active:bool_filter",
    "expensive_products": "products.sku:plain products.unit_price:numeric_cmp",
    "shipping_weight": "products.sku:plain products.weight_kg:numeric_arith",
    "customers_by_country": "customers.country_code:group",
    "customer_phone_export": "customers.customer_id:plain customers.phone:plain",
    "customer_orders_view": "v_customer_orders.customer_id:plain v_customer_orders.email:plain "
    "v_customer_orders.orders_count:plain v_customer_orders.lifetime_total:plain",
    "order_totals_view": "v_order_totals.order_id:plain v_order_totals.items_total:plain",
    "daily_sales_report": "daily_sales_summary.sales_date:plain daily_sales_summary.gross_amount:plain",
    "ltv_report": "report_customer_ltv.customer_id:plain report_customer_ltv.lifetime_value:plain",
    "shipment_latency": "shipments.shipment_id:plain shipments.delivered_at:ts_arith shipments.shipped_at:ts_arith",
    "postal_prefix": "customer_addresses.address_id:plain customer_addresses.postal_code:text_like",
    "avg_rating_cast": "reviews.product_id:group reviews.rating:cast_numeric",
    "top_rated": "reviews.product_id:plain reviews.rating:numeric_cmp",
    "supplier_lead": "suppliers.supplier_id:plain suppliers.lead_time_days:numeric_arith",
    "refund_totals": "refunds.amount:numeric_agg refunds.payment_id:join payments.payment_id:join payments.method:group",
    "coupon_active": "coupons.code:plain coupons.discount_pct:plain coupons.valid_until:date_cmp",
    "customer_profile_star": "customers.*:star customers.customer_id:order",
    "order_snapshot_star": "orders.*:star orders.order_id:order",
    "order_ref_text": "orders.order_id:cast_text orders.status:text_concat",
    "po_cost": "purchase_order_lines.po_id:group purchase_order_lines.qty:numeric_arith "
    "purchase_order_lines.unit_cost:numeric_arith",
    "revenue_view": "v_daily_revenue.sales_day:plain v_daily_revenue.revenue:plain",
    "payment_status_view": "v_payment_status.order_id:plain v_payment_status.status:plain",
    "movement_net": "inventory_movements.product_id:group inventory_movements.delta:numeric_agg",
    "channel_mix": "orders.channel:group",
    "tracking_prefix": "shipments.shipment_id:plain shipments.tracking_no:text_like",
    "ingest_customer": "customers.email:write_text customers.full_name:write_text customers.phone:write_null",
    "ingest_order": "orders.customer_id:write_num orders.status:write_text orders.order_total:write_num "
    "orders.placed_at:write_ts",
    "ingest_order_item": "order_items.order_id:write_num order_items.product_id:write_num "
    "order_items.quantity:write_num order_items.unit_price:write_num",
    "ingest_payment": "payments.order_id:write_num payments.method:write_text payments.amount:write_num "
    "payments.status:write_text payments.reference:write_text",
    "ingest_movement": "inventory_movements.product_id:write_num inventory_movements.warehouse_id:write_num "
    "inventory_movements.delta:write_num inventory_movements.reason:write_text",
    "ingest_audit": "audit_log.table_name:write_text audit_log.payload:write_json",
    "ingest_etl_batch": "etl_batches.source:write_text etl_batches.row_count:write_num",
    "ingest_inventory_snapshot": "report_inventory_snapshot.snapshot_date:write_ts "
    "report_inventory_snapshot.product_id:write_num report_inventory_snapshot.qty:write_num",
    "ingest_stg_order": "stg_orders_import.raw_order_id:write_text stg_orders_import.customer_email:write_text "
    "stg_orders_import.amount:write_num",
    "ingest_review": "reviews.product_id:write_num reviews.customer_id:write_num reviews.rating:write_num",
    "ingest_shipment": "shipments.order_id:write_num shipments.carrier:write_text",
}
USES: dict[str, dict[tuple[str, str], str]] = {
    n: {tuple(tok.split(":")[0].split(".")): tok.split(":")[1] for tok in s.split()} for n, s in _USES.items()
}
assert set(USES) == {c.name for c in BANK}, "consumer bank and usage model out of sync"
PROBES = [c.name for c in BANK if c.kind == "write_probe"]
PROBE_TABLE = {p: next(iter(USES[p]))[0] for p in PROBES}

_CAT = {"int": "num", "smallint": "num", "bigint": "num", "numeric": "num", "text": "text", "varchar": "text",
        "char": "text", "date": "date", "timestamptz": "ts", "boolean": "bool", "jsonb": "json"}


def category(type_str: str) -> str:
    return _CAT[type_str.split("(")[0]]


_OK_BY_USAGE = {
    "numeric_agg": {"num"}, "numeric_arith": {"num"}, "numeric_cmp": {"num"}, "text_like": {"text"},
    "cast_numeric": {"num", "text"}, "date_func": {"ts", "date"}, "ts_arith": {"ts", "date"},
    "date_cmp": {"date", "ts"}, "bool_filter": {"bool"}, "write_num": {"num", "text"}, "write_ts": {"ts", "date", "text"},
    "write_text": {"text"}, "text_eq": {"text"}, "write_json": {"json", "text"},
}
_ERR_BY_USAGE = {
    "numeric_agg": "42883", "numeric_arith": "42883", "numeric_cmp": "42883", "text_like": "42883",
    "cast_numeric": "42846", "date_func": "42883", "ts_arith": "42883", "date_cmp": "42883",
    "bool_filter": "42804", "write_num": "42804", "write_ts": "42804", "write_text": "22P02",
    "text_eq": "22P02", "write_json": "42804",
}


def usage_error(usage: str, new_cat: str) -> str | None:
    """SQLSTATE when a consumer using a column in this way meets a column of another type category."""
    ok = _OK_BY_USAGE.get(usage)
    if ok is None or new_cat in ok:
        return None
    if usage in ("text_eq", "write_text") and new_cat in ("date", "ts"):
        return "22007"
    return _ERR_BY_USAGE[usage]


# ---------------------------------------------------------------------------------------------
# Effects model
# ---------------------------------------------------------------------------------------------

VALUE_USAGES = {"plain", "numeric_agg", "numeric_arith", "numeric_cmp", "star", "cast_numeric", "text_eq"}
SILENT_USAGES = {"plain", "numeric_agg", "numeric_arith"}


def consumers_of(t: str, c: str | None = None, usages: set[str] | None = None) -> list[str]:
    out = []
    for name, uses in USES.items():
        for (tt, cc), u in uses.items():
            if tt == t and (c is None or cc == c) and (usages is None or u in usages):
                out.append(name)
                break
    return out


def direct(t: str, c: str) -> list[str]:
    return [n for n in consumers_of(t, c) if USES[n].get((t, c)) != "star"]


def stars(t: str) -> list[str]:
    return consumers_of(t, "*")


def table_users(t: str) -> list[str]:
    return consumers_of(t)


def probes_of(t: str) -> list[str]:
    return [p for p in PROBES if PROBE_TABLE[p] == t]


def view_value_users(base_t: str, base_c: str, usages: set[str] | None = None) -> list[str]:
    """Consumers reading a view whose output or row set depends on a base column's values."""
    out = []
    for v, spec in VIEWS.items():
        affected_cols = {oc for oc, srcs in spec["out"].items() if f"{base_t}.{base_c}" in srcs}
        row_hit = f"{base_t}.{base_c}" in spec["rows"]
        for name, uses in USES.items():
            for tt, cc in uses:
                if tt == v and (row_hit or cc in affected_cols):
                    out.append(name)
                    break
    return sorted(set(out))


def _fx(touched: list[str]) -> dict[str, Any]:
    return {
        "migration_error": None,
        "data_rewritten": False,
        "diff": {k: [] for k in DIFF_KEYS},
        "failed": {},
        "result_changed": [],
        "shape_changed": [],
        "touched": sorted(set(touched)),
    }


def _col_str(t: str, c: str, typ: str | None = None, nn: bool | None = None, df: bool | None = None) -> str:
    m = COLS.get((t, c)) or {"nn": None, "default": None}
    typ = typ if typ is not None else m["type"]
    nn = m["nn"] if nn is None else nn
    df = m["default"] if df is None else df
    return f"{t}.{c} {typ} {'not null' if nn else 'null'}{' default' if df else ''}"


def _change(kind: str, group: str, stmts: list[str], fx: dict[str, Any], *, tags: list[str] | None = None,
            suff: str = "sufficient", variant: str = "") -> dict[str, Any]:
    fx["shape_changed"] = [n for n in fx["shape_changed"] if n not in fx["failed"]]
    for k in ("result_changed", "shape_changed"):
        fx[k] = sorted(set(fx[k]))
    for k in DIFF_KEYS:
        fx["diff"][k] = sorted(fx["diff"][k])
    return {"kind": kind, "group": group, "stmts": stmts, "effects": fx, "tags": tags or [], "suff": suff,
            "variant": variant, "touched": fx["touched"]}


def _fail(fx: dict[str, Any], names: list[str], state: str) -> None:
    for n in names:
        fx["failed"].setdefault(n, state)


# ------------------------------------------------------------------------------ builders

def add_column(t: str, c: str, typ: str, nn: bool, default: str | None) -> dict[str, Any]:
    stmt = f"ALTER TABLE {t} ADD COLUMN {c} {typ}{' NOT NULL' if nn else ''}{f' DEFAULT {default}' if default else ''};"
    fx = _fx([t])
    if nn and default is None and t not in EMPTY_TABLES:
        fx["migration_error"] = "23502"
    else:
        fx["diff"]["columns_added"].append(_col_str(t, c, typ, nn, default is not None))
        fx["shape_changed"] += stars(t)
        if nn and default is None:
            _fail(fx, probes_of(t), "23502")
    kind = "add_notnull_nodefault" if nn and default is None else "add_column"
    tags = ["hard_negative"] if nn and default is not None else []
    return _change(kind, f"addcol:{t}", [stmt], fx, tags=tags, variant=f"{c}")


def rename_column(t: str, a: str, b: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["columns_removed"].append(_col_str(t, a))
    fx["diff"]["columns_added"].append(_col_str(t, b, COLS[(t, a)]["type"], COLS[(t, a)]["nn"], COLS[(t, a)]["default"]))
    if (t, a) in BASE_COMMENTS:
        fx["diff"]["comments_changed"] += [f"{t}.{a}: (removed)", f"{t}.{b}: {BASE_COMMENTS[(t, a)]}"]
    fx["diff"]["views_changed"] += [v for v, s in VIEWS.items() if f"{t}.{a}" in s["deps"]]
    users = direct(t, a)
    _fail(fx, users, "42703")
    fx["shape_changed"] += stars(t)
    tags = ["hard_negative"] if not users and any(f"{t}.{a}" in s["deps"] for s in VIEWS.values()) else []
    suff = "ambiguous" if tags else "sufficient"
    fx["touched"] = sorted(set(fx["touched"]) | {v for v, s in VIEWS.items() if f"{t}.{a}" in s["deps"]})
    return _change("rename_column", f"rename:{t}.{a}", [f"ALTER TABLE {t} RENAME COLUMN {a} TO {b};"], fx,
                   tags=tags, suff=suff)


def drop_column(t: str, c: str) -> dict[str, Any]:
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} DROP COLUMN {c};"
    if (t, c) in VIEW_DEP_COLS:
        fx["migration_error"] = "2BP01"
        fx["touched"] = sorted(set(fx["touched"]) | {v for v, s in VIEWS.items() if f"{t}.{c}" in s["deps"]})
        return _change("drop_column", f"drop:{t}.{c}", [stmt], fx)
    fx["diff"]["columns_removed"].append(_col_str(t, c))
    if (t, c) in BASE_COMMENTS:
        fx["diff"]["comments_changed"].append(f"{t}.{c}: (removed)")
    for name in COLOBJ.get((t, c), []):
        if name in CONS:
            fx["diff"]["constraints_removed"].append(f"{t}.{name} {CONS[name][1]}")
        if name in PLAIN_IDX or CONS.get(name, ("", ""))[1] == "unique":
            fx["diff"]["indexes_removed"].append(f"{t}.{name}")
    users = direct(t, c)
    _fail(fx, users, "42703")
    fx["shape_changed"] += stars(t)
    tags, suff = ([], "sufficient") if users else (["hard_negative"], "ambiguous")
    return _change("drop_column", f"drop:{t}.{c}", [stmt], fx, tags=tags, suff=suff)


def set_not_null(t: str, c: str) -> dict[str, Any]:
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL;"
    if (t, c) in NULL_COLS:
        fx["migration_error"] = "23502"
    else:
        fx["diff"]["columns_changed"].append(f"{t}.{c} nullable null->not null")
        for p in probes_of(t):
            if (t, c) not in USES[p] or USES[p][(t, c)] == "write_null":
                _fail(fx, [p], "23502")
    tags = ["hard_negative"] if not fx["failed"] and not fx["migration_error"] else []
    return _change("set_not_null", f"notnull:{t}.{c}", [stmt], fx, tags=tags,
                   suff="ambiguous" if tags else "sufficient")


def drop_not_null(t: str, c: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["columns_changed"].append(f"{t}.{c} nullable not null->null")
    return _change("drop_not_null", f"dropnn:{t}.{c}", [f"ALTER TABLE {t} ALTER COLUMN {c} DROP NOT NULL;"], fx,
                   tags=["hard_negative"])


def drop_default(t: str, c: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["columns_changed"].append(f"{t}.{c} default set->none")
    for p in probes_of(t):
        if (t, c) not in USES[p]:
            _fail(fx, [p], "23502")
    return _change("drop_default", f"dropdef:{t}.{c}", [f"ALTER TABLE {t} ALTER COLUMN {c} DROP DEFAULT;"], fx)


def set_default(t: str, c: str, expr: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["columns_changed"].append(f"{t}.{c} default none->set")
    return _change("set_default", f"setdef:{t}.{c}", [f"ALTER TABLE {t} ALTER COLUMN {c} SET DEFAULT {expr};"], fx)


def add_unique(t: str, cols: list[str], data_ok: bool, probe_fails: tuple[str, ...] = ()) -> dict[str, Any]:
    name = f"uq_{t}_{'_'.join(cols)}"
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} ADD CONSTRAINT {name} UNIQUE ({', '.join(cols)});"
    if not data_ok:
        fx["migration_error"] = "23505"
    else:
        fx["diff"]["constraints_added"].append(f"{t}.{name} unique")
        fx["diff"]["indexes_added"].append(f"{t}.{name}")
        _fail(fx, list(probe_fails), "23505")
    tags = ["hard_negative"] if data_ok and not probe_fails else []
    return _change("add_unique", f"unique:{t}.{'+'.join(cols)}", [stmt], fx, tags=tags,
                   suff="ambiguous" if tags else "sufficient")


def add_check(t: str, name: str, expr: str, data_ok: bool, probe_fails: tuple[str, ...] = ()) -> dict[str, Any]:
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} ADD CONSTRAINT {name} CHECK ({expr});"
    if not data_ok:
        fx["migration_error"] = "23514"
    else:
        fx["diff"]["constraints_added"].append(f"{t}.{name} check")
        _fail(fx, list(probe_fails), "23514")
    tags = ["hard_negative"] if data_ok and not probe_fails else []
    return _change("add_check", f"check:{t}.{name}", [stmt], fx, tags=tags,
                   suff="ambiguous" if tags else "sufficient")


def replace_check(t: str, old: str, new: str, expr: str, data_ok: bool, probe_fails: tuple[str, ...] = ()) -> dict[str, Any]:
    fx = _fx([t])
    stmts = [f"ALTER TABLE {t} DROP CONSTRAINT {old};", f"ALTER TABLE {t} ADD CONSTRAINT {new} CHECK ({expr});"]
    if not data_ok:
        fx["migration_error"] = "23514"
    else:
        fx["diff"]["constraints_removed"].append(f"{t}.{old} check")
        fx["diff"]["constraints_added"].append(f"{t}.{new} check")
        _fail(fx, list(probe_fails), "23514")
    return _change("replace_check", f"replacecheck:{t}.{old}", stmts, fx,
                   tags=["hard_negative"] if data_ok and not probe_fails else [])


def drop_constraint(name: str) -> dict[str, Any]:
    t, kind = CONS[name]
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} DROP CONSTRAINT {name};"
    if kind == "primary key" and t in REFERENCED:
        fx["migration_error"] = "2BP01"
    else:
        fx["diff"]["constraints_removed"].append(f"{t}.{name} {kind}")
        if kind in ("primary key", "unique"):
            fx["diff"]["indexes_removed"].append(f"{t}.{name}")
    return _change("drop_constraint", f"dropcon:{name}", [stmt], fx, tags=["hard_negative"])


# curated type changes: (table, column, new type, USING?, lossy?)
def type_change(t: str, c: str, new: str, using: bool = False, overflow: str | None = None,
                lossy: bool = False) -> dict[str, Any]:
    old = COLS[(t, c)]["type"]
    fx = _fx([t])
    stmt = f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {new}{f' USING {c}::{new}' if using else ''};"
    if (t, c) in VIEW_DEP_COLS:
        fx["migration_error"] = "0A000"
        fx["touched"] = sorted(set(fx["touched"]) | {v for v, s in VIEWS.items() if f"{t}.{c}" in s["deps"]})
        return _change("type_change", f"type:{t}.{c}", [stmt], fx)
    if overflow:
        fx["migration_error"] = overflow
        return _change("type_change", f"type:{t}.{c}", [stmt], fx)
    fx["diff"]["columns_changed"].append(f"{t}.{c} type {old}->{new}")
    old_cat, new_cat = category(old), category(new)
    tags: list[str] = []
    suff = "sufficient"
    if old_cat != new_cat:
        for name in direct(t, c):
            err = usage_error(USES[name][(t, c)], new_cat)
            if err:
                fx["failed"].setdefault(name, err)
        for name in probes_of(t):
            u = USES[name].get((t, c))
            if u and (err := usage_error(u, new_cat)):
                fx["failed"].setdefault(name, err)
        if lossy:
            fx["result_changed"] += [n for n in direct(t, c) if n not in fx["failed"] and n not in PROBES] + stars(t)
        if not fx["failed"] and not fx["result_changed"]:
            suff = "ambiguous"
    else:
        silent = _silent_precision(old, new)
        if silent:
            fx["result_changed"] += [n for n in consumers_of(t, c, SILENT_USAGES) if n not in fx["failed"]]
            fx["result_changed"] += view_value_users(t, c)
        elif not _narrowing(old, new):
            tags = ["hard_negative"]
        else:
            tags, suff = ["hard_negative"], "ambiguous"
        if silent and not fx["result_changed"]:
            tags, suff = ["hard_negative"], "ambiguous"
    return _change("type_change", f"type:{t}.{c}", [stmt], fx, tags=tags, suff=suff)


def _num_ps(t: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"numeric\((\d+),(\d+)\)", t)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _silent_precision(old: str, new: str) -> bool:
    a, b = _num_ps(old), _num_ps(new)
    return bool(a and b and b[1] < a[1])


_RANK = {"smallint": 1, "int": 2, "bigint": 3}


def narrowing(old: str, new: str) -> bool:
    """True when the new type can hold fewer values than the old one (or changes category)."""
    if category(old) != category(new):
        return True
    if old in _RANK and new in _RANK:
        return _RANK[new] < _RANK[old]
    a, b = _num_ps(old), _num_ps(new)
    if a and b:
        return (b[0] - b[1]) < (a[0] - a[1]) or b[1] < a[1]
    if old.startswith("varchar") or old == "text":
        n_old = int(re.findall(r"\d+", old)[0]) if "(" in old else 10**9
        n_new = int(re.findall(r"\d+", new)[0]) if "(" in new else 10**9
        return n_new < n_old
    return False


_narrowing = narrowing


def rename_table(t: str, new: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["tables_removed"].append(t)
    fx["diff"]["tables_added"].append(new)
    for (bt, bc), text in BASE_COMMENTS.items():
        if bt == t:
            fx["diff"]["comments_changed"] += [f"{t}.{bc}: (removed)", f"{new}.{bc}: {text}"]
    views = [v for v, s in VIEWS.items() if any(d.startswith(t + ".") for d in s["deps"])]
    fx["diff"]["views_changed"] += views
    fx["touched"] = sorted(set(fx["touched"]) | set(views))
    _fail(fx, table_users(t), "42P01")
    return _change("rename_table", f"renametable:{t}", [f"ALTER TABLE {t} RENAME TO {new};"], fx)


def drop_table(t: str) -> dict[str, Any]:
    fx = _fx([t])
    stmt = f"DROP TABLE {t};"
    if t in REFERENCED or t in VIEW_BASE_TABLES:
        fx["migration_error"] = "2BP01"
    else:
        fx["diff"]["tables_removed"].append(t)
        _fail(fx, table_users(t), "42P01")
    return _change("drop_table", f"droptable:{t}", [stmt], fx,
                   tags=[] if fx["failed"] or fx["migration_error"] else ["hard_negative"],
                   suff="sufficient" if fx["failed"] or fx["migration_error"] else "ambiguous")


def create_table(name: str, ddl: str) -> dict[str, Any]:
    fx = _fx([name])
    fx["diff"]["tables_added"].append(name)
    return _change("create_table", f"createtable:{name}", [ddl], fx)


def create_view(name: str, sql: str) -> dict[str, Any]:
    fx = _fx([name])
    fx["diff"]["views_added"].append(name)
    return _change("create_view", f"createview:{name}", [sql], fx)


def _view_sql(v: str, drop: str | None = None, extra: bool = False, where: str | None = None, replace: bool = False) -> str:
    s = VIEWS[v]
    exprs = {k: e for k, e in s["exprs"].items() if k != drop}
    group = s["group"]
    if extra:
        name, expr, grp = s["extra"]
        exprs[name] = expr
        group = grp
    cols = ", ".join(f"{e} AS {k}" for k, e in exprs.items())
    where = where if where is not None else s.get("where")
    sql = f"{'CREATE OR REPLACE VIEW' if replace else 'CREATE VIEW'} {v} AS SELECT {cols} FROM {s['from']}"
    if where:
        sql += f" WHERE {where}"
    if group:
        sql += f" GROUP BY {group}"
    return sql + ";"


def view_drop_column(v: str, col: str, replace: bool) -> dict[str, Any]:
    fx = _fx([v])
    if replace:
        stmts = [_view_sql(v, drop=col, replace=True)]
        fx["migration_error"] = "42P16"
    else:
        stmts = [f"DROP VIEW {v};", _view_sql(v, drop=col)]
        fx["diff"]["views_changed"].append(v)
        _fail(fx, direct(v, col), "42703")
    return _change("view_drop_column", f"view:{v}", stmts, fx,
                   tags=[] if fx["failed"] or fx["migration_error"] else ["hard_negative"],
                   suff="sufficient" if fx["failed"] or fx["migration_error"] else "ambiguous")


def view_add_column(v: str) -> dict[str, Any]:
    fx = _fx([v])
    fx["diff"]["views_changed"].append(v)
    return _change("view_add_column", f"view:{v}", [_view_sql(v, extra=True, replace=True)], fx)


def view_drop(v: str) -> dict[str, Any]:
    fx = _fx([v])
    fx["diff"]["views_removed"].append(v)
    _fail(fx, table_users(v), "42P01")
    return _change("view_drop", f"view:{v}", [f"DROP VIEW {v};"], fx)


def semantic_view(v: str, where: str, comment: str) -> dict[str, Any]:
    fx = _fx([v])
    fx["diff"]["views_changed"].append(v)
    fx["diff"]["comments_changed"].append(f"{v}: {comment}")
    fx["result_changed"] += table_users(v)
    stmts = [_view_sql(v, where=where, replace=True), f"COMMENT ON VIEW {v} IS '{comment}';"]
    return _change("semantic_view", f"semview:{v}", stmts, fx, suff="ambiguous")


def semantic_column(t: str, c: str, update: str | None, comment: str) -> dict[str, Any]:
    fx = _fx([t])
    stmts = []
    if update:
        stmts.append(update)
        fx["data_rewritten"] = True
        fx["result_changed"] += consumers_of(t, c, VALUE_USAGES) + stars(t) + view_value_users(t, c)
    stmts.append(f"COMMENT ON COLUMN {t}.{c} IS '{comment}';")
    fx["diff"]["comments_changed"].append(f"{t}.{c}: {comment}")
    return _change("semantic_column", f"semantic:{t}.{c}", stmts, fx,
                   suff="sufficient" if update else "ambiguous", variant="update" if update else "comment")


def create_index(t: str, cols: list[str], unique: bool, data_ok: bool = True) -> dict[str, Any]:
    name = f"{'ux' if unique else 'ix'}_{t}_{'_'.join(cols)}"
    fx = _fx([t])
    stmt = f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {t} ({', '.join(cols)});"
    if not data_ok:
        fx["migration_error"] = "23505"
    else:
        fx["diff"]["indexes_added"].append(f"{t}.{name}")
    return _change("create_index", f"index:{t}", [stmt], fx)


def drop_index(name: str) -> dict[str, Any]:
    t = PLAIN_IDX[name]
    fx = _fx([t])
    fx["diff"]["indexes_removed"].append(f"{t}.{name}")
    return _change("drop_index", f"index:{t}", [f"DROP INDEX {name};"], fx, tags=["hard_negative"])


def rename_index(name: str) -> dict[str, Any]:
    t = PLAIN_IDX[name]
    fx = _fx([t])
    fx["diff"]["indexes_removed"].append(f"{t}.{name}")
    fx["diff"]["indexes_added"].append(f"{t}.{name}_v2")
    return _change("rename_index", f"index:{t}", [f"ALTER INDEX {name} RENAME TO {name}_v2;"], fx)


def noop(t: str, variant: int) -> dict[str, Any]:
    fx = _fx([t])
    c = next(c for (tt, c), m in COLS.items()
             if tt == t and m["type"] in ("text", "int", "bigint", "boolean") and (tt, c) not in VIEW_DEP_COLS)
    typ = COLS[(t, c)]["type"]
    options = [
        (f"ALTER TABLE {t} SET (fillfactor = 85);", "storage"),
        (f"ALTER TABLE {t} ALTER COLUMN {c} SET DATA TYPE {typ};", "same_type"),
        (f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {c} {typ};", "idempotent"),
        (f"ANALYZE {t};", "analyze"),
        (f"GRANT SELECT ON {t} TO PUBLIC;", "grant"),
    ]
    stmt, label = options[variant % len(options)]
    suff = "insufficient"
    return _change("noop", f"noop:{t}", [stmt], fx, tags=["hard_negative"], suff=suff, variant=label)


def table_comment(t: str, text: str) -> dict[str, Any]:
    fx = _fx([t])
    fx["diff"]["comments_changed"].append(f"{t}: {text}")
    return _change("table_comment", f"noop:{t}", [f"COMMENT ON TABLE {t} IS '{text}';"], fx, tags=["hard_negative"],
                   suff="ambiguous")


# ---------------------------------------------------------------------------------------------
# Facts and policy
# ---------------------------------------------------------------------------------------------

def facts_from_effects(change: dict[str, Any]) -> dict[str, Any]:
    fx = change["effects"]
    err = fx["migration_error"]
    ok = err is None
    return {
        "migration": {
            "sql": "\n".join(change["stmts"]), "ok": ok, "sqlstate": err,
            "error_class": ERROR_CLASS.get(err) if err else None,
            "data_rewritten": bool(fx["data_rewritten"]) and ok,
        },
        "catalog_diff": {k: list(fx["diff"][k]) if ok else [] for k in DIFF_KEYS},
        "consumers": {
            "total": len(BANK), "run": ok,
            "failed": {n: {"sqlstate": s, "error_class": ERROR_CLASS[s]} for n, s in sorted(fx["failed"].items())} if ok else {},
            "result_changed": list(fx["result_changed"]) if ok else [],
            "shape_changed": list(fx["shape_changed"]) if ok else [],
        },
        "baseline_failed": [],
    }


RENAME_DROP = {"42703", "42P01", "2BP01", "42P16"}
TYPE_CODES = {"42883", "42804", "42846", "0A000"}
MEANING_MARK = "Meaning changed"
STRUCTURAL = ("tables_added", "tables_removed", "columns_added", "columns_removed", "columns_changed",
              "constraints_added", "constraints_removed", "views_added", "views_removed", "views_changed")


def failure_codes(f: dict[str, Any]) -> list[str]:
    codes = [] if f["migration"]["ok"] else [f["migration"]["sqlstate"]]
    return codes + [v["sqlstate"] for v in f["consumers"]["failed"].values()]


def documented_meaning_change(f: dict[str, Any]) -> bool:
    return any(c.split(": ", 1)[1].startswith(MEANING_MARK) for c in f["catalog_diff"]["comments_changed"])


def category_change(f: dict[str, Any]) -> bool:
    for line in f["catalog_diff"]["columns_changed"]:
        m = re.search(r" type (.+)->(.+)$", line)
        if m and category(m.group(1)) != category(m.group(2)):
            return True
    return False


def is_irreversible(f: dict[str, Any]) -> bool:
    d = f["catalog_diff"]
    added = {(c.split(".")[0], c.split()[1]) for c in d["columns_added"]}
    for line in d["columns_removed"]:
        key, typ = line.split()[0], line.split()[1]
        if (key.split(".")[0], typ) not in added:
            return True
    if len(d["tables_removed"]) != len(d["tables_added"]):
        return True
    for line in d["columns_changed"]:
        m = re.search(r" type (.+)->(.+)$", line)
        if m and narrowing(m.group(1), m.group(2)):
            return True
    return bool(f["migration"]["data_rewritten"])


def drift_label(f: dict[str, Any]) -> str:
    codes = set(failure_codes(f))
    if codes:
        if codes & RENAME_DROP:
            return "breaking_rename_drop"
        if codes & TYPE_CODES or any(c.startswith("22") for c in codes):
            return "type_or_cast_break"
        if any(c.startswith("23") for c in codes):
            return "constraint_or_nullability_break"
        return "no_change_or_unknown"
    if f["consumers"]["result_changed"]:
        return "semantic_change" if documented_meaning_change(f) else "type_or_cast_break"
    if documented_meaning_change(f):
        return "semantic_change"
    if category_change(f):
        return "type_or_cast_break"
    if any(f["catalog_diff"][k] for k in STRUCTURAL):
        return "non_breaking_addition"
    return "no_change_or_unknown"


def risk_level(f: dict[str, Any]) -> int:
    if not f["migration"]["ok"]:
        return 2
    failed = list(f["consumers"]["failed"])
    irrev = is_irreversible(f)
    if failed:
        crit = [n for n in failed if n in CRITICAL]
        if crit and (len(failed) >= 2 or irrev):
            return 4
        if crit or len(failed) >= 2:
            return 3
        return 2
    changed = f["consumers"]["result_changed"]
    if changed:
        return min(4, 2 + any(n in CRITICAL for n in changed) + irrev)
    label = drift_label(f)
    if label == "no_change_or_unknown":
        return 0
    if documented_meaning_change(f) or category_change(f):
        return 2
    return 1 + irrev


def needs_review(f: dict[str, Any]) -> bool:
    return risk_level(f) >= 2


def gold_from_facts(f: dict[str, Any]) -> dict[str, Any]:
    return {Q_IMPACT: drift_label(f), Q_RISK: risk_level(f), Q_REVIEW: needs_review(f)}


def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Catalog diff plus SQLSTATE of failing consumers: what a plain compatibility check would report.

    It has no result-digest comparison and no reversibility model, and it flags every type change, so it
    over-calls widening and cannot see silent value changes.
    """
    codes = set(failure_codes(facts))
    d = facts["catalog_diff"]
    failed = list(facts["consumers"]["failed"])
    type_change_seen = any(" type " in x for x in d["columns_changed"])
    if codes:
        label = drift_label({**facts, "consumers": {**facts["consumers"], "result_changed": []}})
    elif documented_meaning_change(facts):
        label = "semantic_change"
    elif type_change_seen:
        label = "type_or_cast_break"
    elif any(d[k] for k in STRUCTURAL):
        label = "non_breaking_addition"
    else:
        label = "no_change_or_unknown"
    if any(n in CRITICAL for n in failed):
        risk = 4
    elif failed:
        risk = 3
    elif codes or label in ("semantic_change", "type_or_cast_break") or d["columns_changed"]:
        risk = 2
    elif label == "non_breaking_addition":
        risk = 1
    else:
        risk = 0
    return {Q_IMPACT: label, Q_RISK: risk, Q_REVIEW: risk >= 2}


# ---------------------------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------------------------

_NEW_COLS = ["priority_level", "region_code", "tax_ref", "source_tag", "review_flag", "batch_ref"]
_ADD_NN_TABLES = ["customers", "orders", "products", "payments", "inventory", "shipments", "order_items", "reviews",
                  "suppliers", "warehouses", "coupons", "refunds", "customer_addresses", "daily_sales_summary",
                  "purchase_orders", "categories", "report_customer_ltv", "inventory_movements",
                  "purchase_order_lines", "payment_methods"]

TYPES_WIDEN = [
    ("customers", "loyalty_points", "bigint"), ("suppliers", "lead_time_days", "int"),
    ("order_items", "quantity", "bigint"), ("purchase_order_lines", "qty", "bigint"),
    ("warehouses", "capacity_units", "bigint"), ("customer_addresses", "postal_code", "varchar(20)"),
    ("customer_addresses", "postal_code", "text"), ("products", "weight_kg", "numeric(12,3)"),
    ("coupons", "discount_pct", "numeric(6,2)"), ("inventory_movements", "delta", "bigint"),
    ("reviews", "rating", "int"), ("refunds", "amount", "numeric(14,2)"),
    ("report_customer_ltv", "lifetime_value", "numeric(16,2)"), ("daily_sales_summary", "orders_count", "bigint"),
    ("daily_sales_summary", "gross_amount", "numeric(16,2)"), ("inventory", "qty_on_hand", "bigint"),
    ("inventory", "reorder_level", "bigint"), ("payments", "amount", "numeric(14,2)"),
    ("orders", "order_total", "numeric(14,2)"), ("products", "unit_price", "numeric(14,2)"),
    ("etl_batches", "row_count", "bigint"), ("report_inventory_snapshot", "qty", "bigint"),
]
TYPES_NARROW_FIT = [
    ("inventory_movements", "delta", "smallint"), ("purchase_order_lines", "qty", "smallint"),
    ("warehouses", "capacity_units", "smallint"), ("products", "unit_price", "numeric(8,2)"),
    ("products", "weight_kg", "numeric(6,3)"), ("customer_addresses", "postal_code", "varchar(5)"),
    ("coupons", "discount_pct", "numeric(4,2)"), ("daily_sales_summary", "orders_count", "smallint"),
    ("refunds", "amount", "numeric(8,2)"),
]
TYPES_NARROW_OVER = [
    ("customers", "loyalty_points", "smallint", "22003"), ("products", "unit_price", "numeric(4,2)", "22003"),
    ("customer_addresses", "postal_code", "varchar(3)", "22001"), ("products", "name", "varchar(5)", "22001"),
    ("refunds", "amount", "numeric(4,2)", "22003"), ("daily_sales_summary", "gross_amount", "numeric(6,2)", "22003"),
    ("report_customer_ltv", "lifetime_value", "numeric(6,2)", "22003"), ("shipments", "carrier", "varchar(4)", "22001"),
    ("customers", "full_name", "varchar(8)", "22001"), ("products", "sku", "varchar(6)", "22001"),
    ("customers", "email", "varchar(10)", "22001"), ("suppliers", "name", "varchar(5)", "22001"),
    ("warehouses", "code", "varchar(3)", "22001"), ("coupons", "code", "varchar(4)", "22001"),
    ("payments", "reference", "varchar(8)", "22001"), ("payments", "method", "varchar(4)", "22001"),
    ("orders", "channel", "varchar(3)", "22001"), ("inventory_movements", "reason", "varchar(4)", "22001"),
    ("order_items", "discount_pct", "numeric(2,2)", "22003"), ("purchase_order_lines", "unit_cost", "numeric(3,2)", "22003"),
    ("inventory", "legacy_bin", "varchar(2)", "22001"),
]
TYPES_BAD_CAST = [
    ("customers", "full_name", "int"), ("products", "name", "int"), ("orders", "channel", "int"),
    ("payments", "method", "bigint"), ("reviews", "body", "int"), ("shipments", "carrier", "bigint"),
    ("customers", "email", "bigint"), ("warehouses", "city", "int"), ("suppliers", "contact_email", "int"),
    ("refunds", "reason", "int"),
]
TYPES_SILENT = [
    ("products", "unit_price", "numeric(12,0)"), ("products", "weight_kg", "numeric(8,1)"),
    ("refunds", "amount", "numeric(12,0)"), ("coupons", "discount_pct", "numeric(5,0)"),
    ("daily_sales_summary", "gross_amount", "numeric(14,0)"), ("report_customer_ltv", "lifetime_value", "numeric(14,0)"),
    ("purchase_order_lines", "unit_cost", "numeric(12,0)"), ("daily_sales_summary", "refunded_amount", "numeric(14,0)"),
    ("orders", "order_total", "numeric(12,0)"), ("payments", "amount", "numeric(12,0)"),
]
TYPES_CATEGORY = [
    ("shipments", "tracking_no", "bigint", True, False), ("customer_addresses", "postal_code", "int", True, False),
    ("products", "unit_price", "text", False, False), ("products", "weight_kg", "text", False, False),
    ("suppliers", "lead_time_days", "text", False, False), ("coupons", "valid_until", "text", False, False),
    ("inventory_movements", "delta", "text", False, False), ("refunds", "amount", "text", False, False),
    ("purchase_order_lines", "qty", "text", False, False), ("customers", "loyalty_points", "text", False, False),
    ("customers", "phone", "bigint", True, True), ("daily_sales_summary", "gross_amount", "text", False, False),
    ("report_customer_ltv", "lifetime_value", "text", False, False),
    ("daily_sales_summary", "refunded_amount", "text", False, False),
    ("customers", "created_at", "date", False, True), ("orders", "order_total", "text", False, False),
]
_RENAME_TABLES = {
    "reviews": "product_reviews", "shipments": "order_shipments", "refunds": "payment_refunds",
    "coupons": "promo_coupons", "suppliers": "vendors", "warehouses": "depots",
    "customer_addresses": "customer_address_book", "inventory_movements": "stock_movements",
    "purchase_order_lines": "po_lines", "daily_sales_summary": "sales_daily", "report_customer_ltv": "report_ltv",
    "audit_log": "audit_trail", "etl_batches": "etl_runs", "categories": "product_categories",
    "payment_methods": "pay_methods", "order_coupons": "applied_coupons", "stg_orders_import": "stg_order_rows",
    "products": "catalog_products", "customers": "customer_accounts", "orders": "sales_orders",
}
_DROP_TABLES = ["customer_addresses", "inventory_movements", "purchase_order_lines", "shipments", "payment_methods",
                "refunds", "order_coupons", "reviews", "daily_sales_summary", "report_customer_ltv",
                "report_inventory_snapshot", "audit_log", "etl_batches", "stg_orders_import",
                "customers", "products", "orders", "payments", "suppliers", "warehouses", "categories", "order_items"]
_CHECKS: list[tuple[str, str, str, bool, tuple[str, ...]]] = [
    ("orders", "ck_orders_total_min", "order_total > 20", True, ("ingest_order",)),
    ("orders", "ck_orders_total_cap", "order_total > 500", False, ()),
    ("orders", "ck_orders_not_pending", "status <> 'pending'", True, ("ingest_order",)),
    ("orders", "ck_orders_currency", "currency = 'THB'", True, ()),
    ("customers", "ck_customers_points", "loyalty_points < 40000", False, ()),
    ("customers", "ck_customers_country", "country_code IN ('TH', 'SG', 'JP')", True, ()),
    ("customers", "ck_customers_domain", "email LIKE '%@example.com'", True, ()),
    ("products", "ck_products_price_cap", "unit_price < 300", False, ()),
    ("products", "ck_products_weight", "weight_kg < 100", True, ()),
    ("payments", "ck_payments_amount_pos", "amount > 0", True, ()),
    ("payments", "ck_payments_not_auth", "status <> 'authorized'", False, ()),
    ("inventory", "ck_inventory_cap", "qty_on_hand < 80", False, ()),
    ("inventory", "ck_inventory_reorder", "reorder_level >= 5", True, ()),
    ("order_items", "ck_items_qty_cap", "quantity <= 3", False, ()),
    ("order_items", "ck_items_discount", "discount_pct <= 10", True, ()),
    ("reviews", "ck_reviews_min2", "rating >= 2", False, ()),
    ("reviews", "ck_reviews_len", "length(body) < 500", True, ()),
    ("shipments", "ck_shipments_dates", "delivered_at >= shipped_at", True, ()),
    ("suppliers", "ck_suppliers_lead", "lead_time_days <= 30", True, ()),
    ("refunds", "ck_refunds_cap", "amount < 5000", True, ()),
    ("etl_batches", "ck_etl_rows_pos", "row_count > 0", True, ("ingest_etl_batch",)),
    ("report_inventory_snapshot", "ck_snapshot_qty_big", "qty > 10", True, ("ingest_inventory_snapshot",)),
    ("audit_log", "ck_audit_not_orders", "table_name <> 'orders'", True, ("ingest_audit",)),
    ("stg_orders_import", "ck_stg_amount_small", "amount < 5", True, ("ingest_stg_order",)),
]
_REPLACE_CHECKS = [
    ("orders", "ck_orders_status", "ck_orders_status_v2", "status IN ('paid', 'shipped', 'cancelled')", True, ("ingest_order",)),
    ("orders", "ck_orders_status", "ck_orders_status_v3", "status IN ('pending', 'paid', 'shipped', 'cancelled', 'returned')", True, ()),
    ("customers", "ck_customers_status", "ck_customers_status_v2", "status IN ('active', 'suspended')", False, ()),
    ("customers", "ck_customers_status", "ck_customers_status_v3", "status IN ('active', 'suspended', 'closed', 'review')", True, ()),
    ("payments", "ck_payments_status", "ck_payments_status_v2", "status IN ('authorized', 'captured', 'failed', 'refunded', 'chargeback')", True, ()),
    ("inventory", "ck_inventory_qty", "ck_inventory_qty_v2", "qty_on_hand >= 0 AND qty_on_hand < 80", False, ()),
    ("reviews", "ck_reviews_rating", "ck_reviews_rating_v2", "rating BETWEEN 1 AND 10", True, ()),
    ("order_items", "ck_order_items_qty", "ck_order_items_qty_v2", "quantity BETWEEN 1 AND 4", False, ()),
]
_UNIQUES: list[tuple[str, list[str], bool, tuple[str, ...]]] = [
    ("customers", ["full_name"], True, ()), ("products", ["name"], True, ()), ("shipments", ["tracking_no"], True, ()),
    ("reviews", ["product_id", "customer_id"], True, ("ingest_review",)), ("customer_addresses", ["line1"], True, ()),
    ("suppliers", ["name"], True, ()), ("refunds", ["payment_id"], True, ()), ("orders", ["customer_id"], False, ()),
    ("payments", ["method"], False, ()), ("customers", ["country_code"], False, ()), ("products", ["category_id"], False, ()),
    ("order_items", ["order_id"], False, ()), ("inventory", ["product_id"], False, ()), ("shipments", ["carrier"], False, ()),
    ("reviews", ["product_id"], False, ()), ("orders", ["status"], False, ()),
]
_SET_NN = ["customers.phone", "customers.legacy_segment", "orders.channel", "orders.internal_note",
           "products.weight_kg", "products.category_id", "products.legacy_code", "customer_addresses.city",
           "shipments.tracking_no", "shipments.delivered_at", "payments.paid_at", "payments.reference",
           "reviews.body", "suppliers.contact_email", "warehouses.city", "coupons.valid_until", "refunds.reason",
           "audit_log.payload", "stg_orders_import.customer_email", "stg_orders_import.amount",
           "report_customer_ltv.last_order_at", "inventory.legacy_bin", "products.supplier_id"]
_DROP_NN = ["orders.currency", "customers.country_code", "customers.full_name", "products.unit_price",
            "order_items.discount_pct", "payments.method", "shipments.carrier", "suppliers.lead_time_days",
            "inventory.reorder_level", "coupons.discount_pct", "refunds.amount", "daily_sales_summary.refunded_amount",
            "etl_batches.source", "audit_log.table_name", "reviews.rating", "customer_addresses.line1"]
_DROP_DEF = ["orders.currency", "customers.country_code", "customers.status", "customers.loyalty_points",
             "customers.created_at", "payments.currency", "order_items.discount_pct", "inventory_movements.moved_at",
             "audit_log.changed_at", "etl_batches.loaded_at", "reviews.created_at", "suppliers.lead_time_days",
             "customer_addresses.is_default", "products.is_active", "inventory.reorder_level", "inventory.updated_at",
             "daily_sales_summary.refunded_amount"]
_CREATE_INDEXES: list[tuple[str, list[str], bool, bool]] = [
    ("orders", ["channel"], False, True), ("orders", ["currency"], False, True), ("customers", ["status"], False, True),
    ("customers", ["email", "status"], False, True), ("products", ["name"], False, True), ("products", ["sku", "is_active"], False, True),
    ("payments", ["method"], False, True), ("payments", ["paid_at"], False, True), ("shipments", ["tracking_no"], False, True),
    ("shipments", ["carrier"], False, True), ("reviews", ["customer_id"], False, True), ("inventory", ["product_id"], False, True),
    ("order_items", ["product_id"], False, True), ("refunds", ["refunded_at"], False, True), ("coupons", ["valid_until"], False, True),
    ("customers", ["full_name"], True, True), ("products", ["name"], True, True), ("shipments", ["tracking_no"], True, True),
    ("orders", ["customer_id"], True, False), ("payments", ["method"], True, False), ("customers", ["country_code"], True, False),
    ("order_items", ["order_id"], True, False), ("reviews", ["rating"], False, True), ("audit_log", ["table_name"], False, True),
    ("etl_batches", ["source"], False, True), ("stg_orders_import", ["raw_order_id"], False, True),
]
_SEMANTIC_UPDATE: list[tuple[str, str, str, str]] = [
    ("orders", "order_total", "UPDATE orders SET order_total = round(order_total / 1.07, 2);",
     "Meaning changed in v2: excludes VAT (previously included VAT)."),
    ("products", "unit_price", "UPDATE products SET unit_price = unit_price * 100;",
     "Meaning changed in v2: price in satang (1/100 THB), was THB."),
    ("refunds", "amount", "UPDATE refunds SET amount = amount * 100;",
     "Meaning changed in v2: amount in satang, was THB."),
    ("customers", "loyalty_points", "UPDATE customers SET loyalty_points = loyalty_points * 10;",
     "Meaning changed in v2: points counted in tenths."),
    ("suppliers", "lead_time_days", "UPDATE suppliers SET lead_time_days = greatest(1, lead_time_days / 7);",
     "Meaning changed in v2: lead time in weeks, was days."),
    ("coupons", "discount_pct", "UPDATE coupons SET discount_pct = discount_pct / 100;",
     "Meaning changed in v2: fraction 0-1, was percent."),
    ("inventory", "qty_on_hand", "UPDATE inventory SET qty_on_hand = qty_on_hand / 12;",
     "Meaning changed in v2: counted in packs of 12, was single units."),
    ("payments", "amount", "UPDATE payments SET amount = amount * 100;",
     "Meaning changed in v2: amount in satang, was THB."),
    ("daily_sales_summary", "gross_amount", "UPDATE daily_sales_summary SET gross_amount = gross_amount - refunded_amount;",
     "Meaning changed in v2: net of refunds, was gross."),
]
_SEMANTIC_COMMENT: list[tuple[str, str, str]] = [
    ("orders", "status", "Meaning changed in v2: pending now means awaiting payment capture, was awaiting packing."),
    ("customers", "status", "Meaning changed in v2: suspended now includes fraud holds."),
    ("shipments", "delivered_at", "Meaning changed in v2: set when handed to carrier, was customer receipt."),
    ("reviews", "rating", "Meaning changed in v2: 1 is best, 5 is worst."),
    ("coupons", "valid_until", "Meaning changed in v2: exclusive end date, was inclusive."),
    ("inventory", "qty_on_hand", "Meaning changed in v2: excludes reserved stock."),
    ("products", "weight_kg", "Meaning changed in v2: gross shipping weight, was net weight."),
    ("payments", "status", "Meaning changed in v2: captured includes partial captures."),
    ("orders", "placed_at", "Meaning changed in v2: UTC checkout time, was local store time."),
    ("customers", "created_at", "Meaning changed in v2: first purchase date, was signup date."),
    ("orders", "order_total", "Meaning changed in v2: includes shipping fee."),
    ("products", "unit_price", "Meaning changed in v2: per pack of 12, was per unit."),
    ("daily_sales_summary", "orders_count", "Meaning changed in v2: counts paid orders only."),
    ("suppliers", "lead_time_days", "Meaning changed in v2: business days, was calendar days."),
    ("refunds", "amount", "Meaning changed in v2: negative amounts mean reversals."),
    ("report_customer_ltv", "lifetime_value", "Meaning changed in v2: 24-month window, was all time."),
    ("payments", "amount", "Meaning changed in v2: includes gateway fee."),
    ("inventory_movements", "delta", "Meaning changed in v2: positive means outbound."),
]
_SEMANTIC_VIEWS = [
    ("v_daily_revenue", "o.status <> 'cancelled'", "Meaning changed in v2: excludes cancelled orders."),
    ("v_customer_orders", "o.status = 'paid'", "Meaning changed in v2: counts paid orders only."),
    ("v_payment_status", "p.status <> 'failed'", "Meaning changed in v2: failed payments hidden."),
    ("v_low_stock", "s.qty_on_hand < s.reorder_level / 2", "Meaning changed in v2: only severe shortages."),
]
_TABLE_COMMENTS = ["Customer master data.", "Order headers.", "Payment attempts and captures.", "Warehouse stock levels.",
                   "Product catalogue.", "Shipment tracking.", "Customer reviews.", "Supplier master data.",
                   "Refund requests.", "Coupon definitions.", "Reporting copy, refreshed daily.", "Ingest staging area."]
_CREATE_TABLES = [
    ("audit_archive", "CREATE TABLE audit_archive (audit_id bigint PRIMARY KEY, table_name text NOT NULL, archived_at timestamptz NOT NULL DEFAULT now());"),
    ("order_notes", "CREATE TABLE order_notes (note_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, order_id bigint REFERENCES orders (order_id), note text);"),
    ("product_tags", "CREATE TABLE product_tags (product_id bigint NOT NULL REFERENCES products (product_id), tag text NOT NULL, PRIMARY KEY (product_id, tag));"),
    ("customer_consents", "CREATE TABLE customer_consents (consent_id bigint PRIMARY KEY, customer_id bigint NOT NULL REFERENCES customers (customer_id), granted boolean NOT NULL DEFAULT false);"),
    ("fx_rates", "CREATE TABLE fx_rates (rate_date date NOT NULL, currency char(3) NOT NULL, rate numeric(12,6) NOT NULL, PRIMARY KEY (rate_date, currency));"),
    ("carrier_zones", "CREATE TABLE carrier_zones (zone_id int PRIMARY KEY, carrier text NOT NULL, zone_name text);"),
]
_CREATE_VIEWS = [
    ("v_open_orders", "CREATE VIEW v_open_orders AS SELECT order_id, customer_id, order_total FROM orders WHERE status = 'paid';"),
    ("v_active_products", "CREATE VIEW v_active_products AS SELECT product_id, sku, unit_price FROM products WHERE is_active;"),
    ("v_late_shipments", "CREATE VIEW v_late_shipments AS SELECT shipment_id, order_id FROM shipments WHERE delivered_at IS NULL;"),
    ("v_refund_summary", "CREATE VIEW v_refund_summary AS SELECT payment_id, sum(amount) AS refunded FROM refunds GROUP BY payment_id;"),
]
_DROP_INDEXES = list(PLAIN_IDX)
_LEGACY = [("orders", "internal_note"), ("customers", "legacy_segment"), ("products", "legacy_code"),
           ("customer_addresses", "legacy_region"), ("inventory", "legacy_bin"), ("suppliers", "legacy_fax")]


def _is_key(t: str, c: str) -> bool:
    return c.endswith("_id") or (t, c) in {("daily_sales_summary", "sales_date")}


def _candidate_pool() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # additive columns: nullable, NOT NULL with default, NOT NULL without default (fails when the table has rows)
    for i, t in enumerate(_ADD_NN_TABLES):
        c = _NEW_COLS[i % len(_NEW_COLS)]
        out += [add_column(t, c, "text", False, None), add_column(t, c + "_n", "int", True, "0"),
                add_column(t, c + "_r", "text", True, None)]
    for i, t in enumerate(sorted(EMPTY_TABLES)):
        c = _NEW_COLS[i % len(_NEW_COLS)]
        out += [add_column(t, c, "timestamptz", False, None), add_column(t, c + "_r", "text", True, None),
                add_column(t, c + "_d", "boolean", True, "false")]
    used_cols = {(t, c) for u in USES.values() for (t, c) in u if c != "*" and t in _COLSPEC}
    view_only = {tuple(d.split(".")) for v in VIEWS.values() for d in v["deps"]}
    rename_targets = sorted(
        used_cols | view_only | set(_LEGACY) | {(t, c) for (t, c) in COLS if t in EMPTY_TABLES and not _is_key(t, c)}
    )
    for t, c in rename_targets:
        out.append(rename_column(t, c, f"{c}_v2"))
        if (t, c) in used_cols and not _is_key(t, c):
            out.append(rename_column(t, c, f"{c}_new"))
    for t, c in sorted(used_cols | view_only | set(_LEGACY) | {(t, c) for (t, c) in COLS if t in EMPTY_TABLES}):
        if not _is_key(t, c) or (t, c) in view_only:
            out.append(drop_column(t, c))
    for t, c in [x.split(".") for x in _SET_NN]:
        out.append(set_not_null(t, c))
    for t, c in [x.split(".") for x in _DROP_NN]:
        out.append(drop_not_null(t, c))
    for t, c in [x.split(".") for x in _DROP_DEF]:
        out.append(drop_default(t, c))
    out.append(set_default("orders", "channel", "'web'"))
    out.append(set_default("customers", "phone", "'unknown'"))
    out.append(set_default("shipments", "tracking_no", "'pending'"))
    out.append(set_default("payments", "paid_at", "now()"))
    out.append(set_default("coupons", "valid_until", "DATE '2030-12-31'"))
    out.append(set_default("refunds", "reason", "'n/a'"))
    for t, cols, ok, pf in _UNIQUES:
        out.append(add_unique(t, cols, ok, pf))
    for t, name, expr, ok, pf in _CHECKS:
        out.append(add_check(t, name, expr, ok, pf))
    for t, old, new, expr, ok, pf in _REPLACE_CHECKS:
        out.append(replace_check(t, old, new, expr, ok, pf))
    for name, (t, kind) in CONS.items():
        if kind in ("check", "unique") or (kind == "primary key" and (t in REFERENCED or t in EMPTY_TABLES)):
            out.append(drop_constraint(name))
    for t, c, new in TYPES_WIDEN:
        out.append(type_change(t, c, new))
    for t, c, new in TYPES_NARROW_FIT:
        out.append(type_change(t, c, new))
    for t, c, new, code in TYPES_NARROW_OVER:
        out.append(type_change(t, c, new, overflow=code))
    for t, c, new in TYPES_BAD_CAST:
        out.append(type_change(t, c, new, using=True, overflow="22P02"))
    for t, c, new in TYPES_SILENT:
        out.append(type_change(t, c, new))
    for t, c, new, using, lossy in TYPES_CATEGORY:
        out.append(type_change(t, c, new, using=using, lossy=lossy))
    for t, new in _RENAME_TABLES.items():
        out.append(rename_table(t, new))
    for t in _DROP_TABLES:
        out.append(drop_table(t))
    for name, ddl in _CREATE_TABLES:
        out.append(create_table(name, ddl))
    for name, sql in _CREATE_VIEWS:
        out.append(create_view(name, sql))
    for v, spec in VIEWS.items():
        for col in spec["exprs"]:
            out.append(view_drop_column(v, col, replace=False))
            out.append(view_drop_column(v, col, replace=True))
        out.append(view_add_column(v))
        out.append(view_drop(v))
    for v, where, comment in _SEMANTIC_VIEWS:
        out.append(semantic_view(v, where, comment))
    for t, c, upd, comment in _SEMANTIC_UPDATE:
        out.append(semantic_column(t, c, upd, comment))
        out.append(semantic_column(t, c, None, comment))
    for t, c, comment in _SEMANTIC_COMMENT:
        out.append(semantic_column(t, c, None, comment))
    for t, cols, unique, ok in _CREATE_INDEXES:
        out.append(create_index(t, cols, unique, ok))
    for name in _DROP_INDEXES:
        out.append(drop_index(name))
        out.append(rename_index(name))
    for i, t in enumerate(_COLSPEC):
        out.append(noop(t, i))
        out.append(noop(t, i + 1))
    for t, text in zip(["customers", "orders", "payments", "inventory", "products", "shipments", "reviews", "suppliers",
                        "refunds", "coupons", "daily_sales_summary", "stg_orders_import"], _TABLE_COMMENTS):
        out.append(table_comment(t, text))
    return out


QUOTAS = {"non_breaking_addition": 54, "breaking_rename_drop": 54, "type_or_cast_break": 54,
          "constraint_or_nullability_break": 54, "semantic_change": 30, "no_change_or_unknown": 54}


RISK_QUOTAS = {0: 54, 1: 54, 2: 100, 3: 52, 4: 40}


def _dedupe(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for ch in pool:
        key = tuple(ch["stmts"])
        if key not in seen:
            seen.add(key)
            out.append(ch)
    return out


def generate_specs(seed: int) -> list[ScenarioSpec]:
    rng = random.Random(seed + 3)
    pool = _dedupe(_candidate_pool())
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ch in pool:
        ch["facts"] = facts_from_effects(ch)
        ch["gold"] = gold_from_facts(ch["facts"])
        groups[ch["group"]].append(ch)
    gids = sorted(groups)
    rng.shuffle(gids)
    label_quota, risk_quota = dict(QUOTAS), dict(RISK_QUOTAS)
    chosen: list[dict[str, Any]] = []
    taken: dict[str, int] = defaultdict(int)
    used: set[tuple[str, ...]] = set()

    def take(ch: dict[str, Any], respect_risk: bool) -> None:
        label, risk = ch["gold"][Q_IMPACT], ch["gold"][Q_RISK]
        if tuple(ch["stmts"]) in used or taken[ch["group"]] >= 3 or label_quota[label] <= 0:
            return
        if respect_risk and risk_quota[risk] <= 0:
            return
        label_quota[label] -= 1
        risk_quota[risk] -= 1
        taken[ch["group"]] += 1
        used.add(tuple(ch["stmts"]))
        chosen.append(ch)

    for risk in (4, 3, 1, 0, 2):  # scarce levels first so label quotas are not used up by level 2
        for pass_no in range(3):
            for gid in gids:
                if pass_no < len(groups[gid]) and groups[gid][pass_no]["gold"][Q_RISK] == risk:
                    take(groups[gid][pass_no], True)
    for pass_no in range(3):
        for gid in gids:
            if pass_no < len(groups[gid]):
                take(groups[gid][pass_no], False)
    # Shortfall in a class (small candidate pool): fill from remaining candidates, <= 3 per group.
    leftovers = [c for gid in gids for c in groups[gid] if tuple(c["stmts"]) not in used]
    rng.shuffle(leftovers)
    for ch in leftovers:
        if len(chosen) >= 300:
            break
        if taken[ch["group"]] < 3:
            taken[ch["group"]] += 1
            chosen.append(ch)
    chosen = chosen[:300]
    rng.shuffle(chosen)
    specs = []
    for i, ch in enumerate(chosen, start=1):
        gold = ch["gold"]
        tags = sorted({"sql_identifier", "code_switching", *ch["tags"]} | (
            {"ambiguous"} if ch["suff"] == "ambiguous" else {"insufficient_evidence"} if ch["suff"] == "insufficient" else set()))
        specs.append(ScenarioSpec(
            scenario_id=scenario_id(CASE, i), experiment=CASE, group_id=f"E03-{ch['group']}",
            facts={"_internal": {"change": {k: ch[k] for k in ("kind", "stmts", "effects", "touched", "variant")}}},
            gold=gold, difficulty_tags=tags, evidence_sufficiency=ch["suff"], label_source=LABEL_SOURCE, seed=seed,
        ))
    return specs


# ---------------------------------------------------------------------------------------------
# Case interface
# ---------------------------------------------------------------------------------------------

def db_seed(specs: list[ScenarioSpec], seed: int) -> dict[str, Any]:
    return {"init_files": [p.name for p in sorted(INIT_DIR.glob("*.sql"))], "scenarios": len(specs), "seed": seed}


def make_backend(settings: Settings | None = None) -> PostgresSchemaBackend:
    return PostgresSchemaBackend(settings)


def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    return facts_from_effects(spec.facts["_internal"]["change"])


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    exp = expected_evidence(spec)
    problems: list[str] = []
    if facts.get("baseline_failed"):
        problems.append(f"baseline consumers failing before migration: {facts['baseline_failed']}")
    for key in ("ok", "sqlstate", "error_class", "data_rewritten"):
        if facts["migration"][key] != exp["migration"][key]:
            problems.append(f"migration.{key}: expected {exp['migration'][key]!r}, got {facts['migration'][key]!r}")
    for key in DIFF_KEYS:
        if facts["catalog_diff"][key] != exp["catalog_diff"][key]:
            problems.append(f"catalog_diff.{key}: expected {exp['catalog_diff'][key]}, got {facts['catalog_diff'][key]}")
    ec, oc = exp["consumers"], facts["consumers"]
    if ec["run"] != oc["run"]:
        problems.append(f"consumers.run: expected {ec['run']}, got {oc['run']}")
    exp_failed = {n: v["sqlstate"] for n, v in ec["failed"].items()}
    obs_failed = {n: v["sqlstate"] for n, v in oc["failed"].items()}
    if exp_failed != obs_failed:
        problems.append(f"consumers.failed: expected {exp_failed}, got {obs_failed}")
    for key in ("result_changed", "shape_changed"):
        if ec[key] != oc[key]:
            problems.append(f"consumers.{key}: expected {ec[key]}, got {oc[key]}")
    observed_gold = gold_from_facts(facts)
    for qid, g in spec.gold.items():
        if observed_gold[qid] != g:
            problems.append(f"gold {qid}: spec {g!r} != from observed facts {observed_gold[qid]!r}")
    return problems


# ------------------------------------------------------------------------------ rendering

def _diff_lines(d: dict[str, list[str]]) -> list[str]:
    lines = [f"+table {x}" for x in d["tables_added"]] + [f"-table {x}" for x in d["tables_removed"]]
    lines += [f"+col {x}" for x in d["columns_added"]] + [f"-col {x}" for x in d["columns_removed"]]
    lines += [f"~col {x}" for x in d["columns_changed"]]
    lines += [f"+constraint {x}" for x in d["constraints_added"]] + [f"-constraint {x}" for x in d["constraints_removed"]]
    lines += [f"+index {x}" for x in d["indexes_added"]] + [f"-index {x}" for x in d["indexes_removed"]]
    lines += [f"+view {x}" for x in d["views_added"]] + [f"-view {x}" for x in d["views_removed"]]
    lines += [f"~view {x}" for x in d["views_changed"]] + [f"~comment {x}" for x in d["comments_changed"]]
    return lines


def _touched(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    return list(spec.facts["_internal"]["change"]["touched"])


def _shown(names: list[str], cap: int = 3) -> tuple[list[str], int]:
    return names[:cap], max(0, len(names) - cap)


def _test_parts(facts: dict[str, Any]) -> dict[str, Any]:
    c = facts["consumers"]
    failed = [f"{n} ({v['error_class']} {v['sqlstate']})" for n, v in c["failed"].items()]
    shown_f, more_f = _shown(failed)
    shown_r, more_r = _shown(c["result_changed"])
    shown_s, more_s = _shown(c["shape_changed"])
    return {"c": c, "n_fail": len(failed), "failed": shown_f, "more_f": more_f, "changed": shown_r, "more_r": more_r,
            "shape": shown_s, "more_s": more_s}


def _join(items: list[str], more: int, lang: str) -> str:
    text = "; ".join(items)
    if more:
        text += f" (+{more} more)" if lang == "en" else f" (+{more} รายการ)"
    return text


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    mig, diff = facts["migration"], facts["catalog_diff"]
    p = _test_parts(facts)
    c = p["c"]
    lines = _diff_lines(diff)
    touched = ", ".join(_touched(spec, facts))
    if not mig["ok"]:
        test = (f"Migration failed when applied: {mig['error_class']} {mig['sqlstate']}. The transaction was rolled back "
                f"and the {c['total']} consumer queries were not re-run." if lang == "en" else
                f"migration ล้มเหลวตอนรันจริง: {mig['error_class']} {mig['sqlstate']}. ระบบ rollback ธุรกรรมแล้ว "
                f"และไม่ได้รัน consumer query ซ้ำทั้ง {c['total']} รายการ")
    elif lang == "en":
        test = f"Compatibility run on {c['total']} consumer queries: {c['total'] - p['n_fail']} pass, {p['n_fail']} fail."
        if p["failed"]:
            test += f" Failing: {_join(p['failed'], p['more_f'], lang)}."
        if p["changed"]:
            test += f" Results changed: {_join(p['changed'], p['more_r'], lang)}."
        if p["shape"]:
            test += f" Column set changed: {_join(p['shape'], p['more_s'], lang)}."
    else:
        test = f"ทดสอบความเข้ากันได้กับ consumer query {c['total']} รายการ: ผ่าน {c['total'] - p['n_fail']} ไม่ผ่าน {p['n_fail']}."
        if p["failed"]:
            test += f" รายการที่ไม่ผ่าน: {_join(p['failed'], p['more_f'], lang)}."
        if p["changed"]:
            test += f" ผลลัพธ์เปลี่ยน: {_join(p['changed'], p['more_r'], lang)}."
        if p["shape"]:
            test += f" ชุดคอลัมน์เปลี่ยน: {_join(p['shape'], p['more_s'], lang)}."
    state: dict[str, Any] = {
        "context": (f"Pending migration on the shop database touching: {touched}. Assess it before deployment."
                    if lang == "en" else f"migration ที่รอ deploy บนฐานข้อมูล shop แก้ไขอ็อบเจ็กต์: {touched}. โปรดประเมินก่อนนำขึ้นระบบจริง"),
        "migration": mig["sql"],
        "catalog_diff": "\n".join(lines) if lines else (
            "No catalog changes detected." if lang == "en" else "ไม่พบการเปลี่ยนแปลงในแคตตาล็อก"),
        "consumer_tests": test,
    }
    if context_level == "natural_long":
        ex = facts.get("excerpt") or {}
        if ex.get("columns_after"):
            state["columns_after"] = "\n".join(ex["columns_after"])
        if ex.get("view_deps"):
            state["view_dependencies"] = "\n".join(ex["view_deps"])
        if mig["ok"] and c["total"] - p["n_fail"]:
            passing = [n for n in (b.name for b in BANK) if n not in c["failed"]]
            state["passing_consumers"] = ", ".join(passing)
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    mig = facts["migration"]
    p = _test_parts(facts)
    tokens = [mig["sql"]]
    tokens += _diff_lines(facts["catalog_diff"])
    if not mig["ok"]:
        tokens += [mig["error_class"], mig["sqlstate"]]
    tokens += p["failed"] + p["changed"] + p["shape"]
    tokens += _touched(spec, facts)
    return tokens
