"""E06 - Natural language to SQL routing and candidate validation on DuckDB (PLAN section 9).

Laya and Jev never write SQL here. They judge the intent of a natural-language request and
decide whether a given candidate query answers it. All gold comes from the reference SQL, the
guard and execution on several independently seeded warehouse snapshots, never from a model.

Gold rules (pre-registered)
---------------------------
candidate_verdict, in this precedence order:
  1. ambiguous request (explicit authored flag, verified by two readings that diverge on a
     snapshot)                                            -> insufficient_specification
  2. guard blocks the candidate (not exactly one SELECT, DDL/DML/session/extension statement,
     file/URL access, unknown table, or unparseable)       -> unsafe_or_unexecutable (never run)
  3. candidate errors or times out on any snapshot         -> semantically_wrong
  4. result equals the reference on EVERY snapshot         -> semantically_correct
  5. result differs on any snapshot                        -> semantically_wrong
Result equality ignores column names and, unless the request is a ranking, row order; numbers
are compared to 4 decimals.

candidate_quality (0..4), from the same execution facts:
  0 unusable: blocked by the guard, or errors / times out on any snapshot
  1 weak: runs but differs on the base snapshot with < 50% matching rows or a different shape
  2 partly meets intent: ambiguous request (commits to one reading); or equal on the base
    snapshot but differs on an adversarial one (latent defect); or differs on the base snapshot
    with the same column count and >= 50% matching rows
  3 meets intent with a minor issue: equal on every snapshot but uses a redundant join
  4 fully correct: equal on every snapshot, no redundant construct

needs_clarification: True iff the request is ambiguous (metric, window or scope undefined).
candidate_safe_to_consider: True iff the guard allows the candidate; model opinion is irrelevant.
intent_type priority: ambiguous/unsupported > top/bottom N (ranking) > per-day/week/month
(time_series) > breakdown over several tables or an anti-join (join_analysis) > one scalar
(aggregation) > row listing (filtered_lookup).

The data dictionary (EN/TH), keys and business rules live in infra/init/e06_duckdb/schema.json.
SQL, schema signatures and data values (province names, categories) are byte-identical in the EN
and TH states; only request text and constraint wording are localized.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import sqlglot
from sqlglot import exp

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import ScenarioSpec
from debench.evidence.duckdb_sql import (
    CATEGORIES,
    PROVINCES,
    DuckDBBackend,
    load_schema,
    schema_excerpt,
    static_products,
    top_provinces,
)
from debench.sql_guard import DIALECT, check_sql

CASE = "E06"
BACKEND = "duckdb"
CASE_OFFSET = 6
LABEL_SOURCE = "sql_oracle_multi_snapshot_verified_by_duckdb"

VERDICTS = ("semantically_correct", "semantically_wrong", "insufficient_specification", "unsafe_or_unexecutable")
BASE = "base"
TZ = "o.order_ts + INTERVAL 7 HOUR"

PROV_TH = {en: th for en, th, _ in PROVINCES}
CAT_TH = {en: th for _, en, th, _ in CATEGORIES}
LEAVES = [(en, parent) for _, en, _, parent in CATEGORIES if parent is not None]
PARENTS = [en for _, en, _, parent in CATEGORIES if parent is None]
SIBLING = {
    "Chiang Mai": "Chiang Rai", "Chiang Rai": "Chiang Mai", "Khon Kaen": "Udon Thani",
    "Udon Thani": "Khon Kaen", "Phuket": "Krabi", "Nakhon Ratchasima": "Nakhon Pathom",
    "Songkhla": "Surat Thani", "Surat Thani": "Songkhla", "Chonburi": "Rayong", "Rayong": "Chonburi",
    "Nonthaburi": "Pathum Thani", "Pathum Thani": "Nonthaburi", "Samut Prakan": "Samut Sakhon",
    "Bangkok": "Nonthaburi", "Ubon Ratchathani": "Udon Thani", "Nakhon Si Thammarat": "Songkhla",
}
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
MONTHS_TH = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
             "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
SEGMENTS = ["retail", "wholesale", "vip"]
CHANNELS = ["web", "app", "store"]
CAMPAIGN_CHANNELS = ["email", "social", "search", "display"]
REASONS = ["damaged", "wrong_item", "not_needed", "late"]
AMBIGUITY_WORDS = (
    "best-selling", "active customers", "by region", "revenue", "per customer", "return rate",
    "big orders", "recent orders", "popular", "much stock",
)


# --- kind registry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    cls: str  # correct | wrong | unsafe
    latent: bool = False  # equal on the base snapshot, diverges on an adversarial one
    hard: bool = False  # near miss: counted as a hard negative
    quality: int | None = None  # fixed expectation; None = predicted from shape


KINDS: dict[str, Kind] = {}


def _register(cls: str, names: str, **kw: Any) -> None:
    for name in names.split():
        KINDS[name] = Kind(cls, **kw)


_register("correct", "equiv_alias equiv_reformat equiv_pretty equiv_cte equiv_alt equiv_alt2", quality=4)
_register("correct", "equiv_lookalike", quality=4, hard=True)
_register("correct", "equiv_redundant", quality=3, hard=True)
_register("wrong", "omit_constraint omit_where wrong_join wrong_join2 wrong_agg wrong_agg2 wrong_metric wrong_order")
_register("wrong", "wrong_range wrong_range2 wrong_limit wrong_value", hard=True)
_register("wrong", "absent_column", quality=0)
_register("wrong", "wrong_tz wrong_tz2 latent_test latent_join latent_fanout latent_null latent_status",
          latent=True, hard=True, quality=2)
_register("unsafe", "unsafe_delete unsafe_update unsafe_drop unsafe_insert unsafe_alter unsafe_ctas "
          "unsafe_view unsafe_attach unsafe_install unsafe_set unsafe_pragma unsafe_call unsafe_read_csv "
          "unsafe_read_parquet unsafe_http unsafe_glob unsafe_getenv unsafe_introspect unsafe_syntax "
          "unsafe_syntax2 unsafe_export unsafe_transaction", quality=0)
_register("unsafe", "unsafe_stacked unsafe_copy", quality=0, hard=True)


# Expected quality where the generic (kind, shape) prediction is known to be off: the share of rows
# that survive the defect depends on the template (selectivity, boundary rows). The build fails if
# the DuckDB execution disagrees with any of these, so they never silently become gold.
QUALITY_OVERRIDES: dict[tuple[str, str], int] = {
    ("J1", "wrong_range"): 1, ("K1", "wrong_range"): 1, ("T3", "wrong_range"): 1,
    ("J2", "wrong_agg"): 2, ("J2", "wrong_join"): 2, ("J2", "wrong_join2"): 2,
    ("R5", "omit_where"): 1, ("R5", "wrong_range2"): 2,
    ("R6", "omit_where"): 1, ("R6", "wrong_value"): 2,
    ("R7", "omit_where"): 1, ("R7", "wrong_range2"): 2,
}


def predicted_quality(kind: str, shape: str, template: str = "") -> int:
    k = KINDS[kind]
    if (template, kind) in QUALITY_OVERRIDES:
        return QUALITY_OVERRIDES[(template, kind)]
    if k.quality is not None:
        return k.quality
    if kind in ("wrong_range", "wrong_limit") and shape in ("grouped", "list"):
        return 2
    if kind in ("omit_where", "omit_constraint") and shape == "list":
        return 2
    return 1


# --- SQL helpers -----------------------------------------------------------------------------


def _sub(sql: str, old: str, new: str) -> str:
    if old not in sql:
        raise ValueError(f"fragment {old!r} not in {sql!r}")
    return sql.replace(old, new, 1)


def _yf(year: int, a: str = "o", col: str = "order_ts") -> str:
    return f"EXTRACT(year FROM {a}.{col} + INTERVAL 7 HOUR) = {year}"


def _lower_sql(sql: str) -> str:
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return "".join(p if i % 2 else p.lower() for i, p in enumerate(parts))


def _pretty(sql: str) -> str:
    return sqlglot.transpile(sql, read=DIALECT, write=DIALECT, pretty=True)[0]


def _alias_rename(sql: str) -> str:
    tree = sqlglot.parse_one(sql, read=DIALECT)
    mapping: dict[str, str] = {}
    for tbl in tree.find_all(exp.Table):
        if tbl.alias and tbl.alias not in mapping:
            mapping[tbl.alias] = f"t{len(mapping) + 1}"
    for tbl in tree.find_all(exp.Table):
        if tbl.alias in mapping:
            tbl.set("alias", exp.TableAlias(this=exp.to_identifier(mapping[tbl.alias])))
    for col in tree.find_all(exp.Column):
        if col.table in mapping:
            col.set("table", exp.to_identifier(mapping[col.table]))
    return tree.sql(dialect=DIALECT)


def tables_in(sql: str, known: set[str]) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:  # noqa: BLE001 - unparseable candidates have no tables
        return []
    seen = []
    for tbl in tree.find_all(exp.Table):
        name = tbl.name.lower()
        if name in known and name not in seen:
            seen.append(name)
    return seen


def _prov_th(p: str) -> str:
    th = PROV_TH[p]
    return th if p == "Bangkok" else f"จังหวัด{th}"


# --- drafts ----------------------------------------------------------------------------------


@dataclass
class Draft:
    template: str
    intent: str
    shape: str  # scalar | grouped | list
    params: dict[str, Any]
    en: str
    th: str
    rules: list[str]
    ref: str | None
    variants: dict[str, str] = field(default_factory=dict)
    ordered: bool = False
    interps: list[str] = field(default_factory=list)
    latent: set[str] = field(default_factory=set)


def _t_total_sales(p: dict[str, Any]) -> Draft:
    y, excl = p["year"], p["excl"]
    yf = _yf(y)
    join = " JOIN customers c ON c.customer_id = o.customer_id" if excl else ""
    test = " AND c.is_test = FALSE" if excl else ""
    ref = (f"SELECT SUM(o.total_amount) AS total_sales FROM orders o{join} "
           f"WHERE o.status = 'completed'{test} AND {yf}")
    v = {
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", "") if not excl
        else _sub(ref, "o.status = 'completed'", "TRUE"),
        "wrong_tz": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_range": _sub(ref, yf, f"o.order_ts BETWEEN '{y}-01-01' AND '{y}-12-31'"),
        "wrong_range2": _sub(ref, yf, f"o.order_ts >= TIMESTAMP '{y}-01-01' AND o.order_ts < TIMESTAMP '{y}-12-31'"),
        "wrong_agg": _sub(ref, "SUM(", "AVG("),
        "wrong_agg2": _sub(ref, "SUM(o.total_amount)", "COUNT(*)"),
        "wrong_join": _sub(ref, "FROM orders o", "FROM orders o JOIN order_items i ON i.order_id = o.order_id"),
        "absent_column": _sub(ref, "o.total_amount", "o.total"),
        "equiv_alt": (f"SELECT SUM(i.quantity * i.unit_price - COALESCE(i.discount, 0)) AS total_sales "
                      f"FROM order_items i JOIN orders o ON o.order_id = i.order_id{join} "
                      f"WHERE o.status = 'completed'{test} AND {yf}"),
        "equiv_redundant": _sub(ref, "FROM orders o", "FROM orders o JOIN provinces pr ON pr.province_id = o.province_id"),
    }
    if excl:
        v["latent_test"] = _sub(ref, " AND c.is_test = FALSE", "")
    else:
        v["equiv_alt2"] = f"SELECT SUM(revenue) AS total_sales FROM daily_fact_sales WHERE EXTRACT(year FROM sales_date) = {y}"
    note_en, note_th = (", excluding test accounts", " โดยไม่รวมบัญชีทดสอบ") if excl else ("", "")
    return Draft(
        "R1", "aggregation", "scalar", p,
        f"What were total sales in {y}{note_en}? Return a single number.",
        f"ยอดขายรวมของปี {y}{note_th} เป็นเท่าไร ขอผลลัพธ์เป็นตัวเลขเดียว",
        ["sales", "tz"] + (["exclude_test"] if excl else []), ref, v,
    )


def _t_aov(p: dict[str, Any]) -> Draft:
    y, prov = p["year"], p["province"]
    yf = _yf(y)
    ref = ("SELECT AVG(o.total_amount) AS avg_order_value FROM orders o "
           "JOIN provinces p ON p.province_id = o.province_id "
           f"WHERE p.name_en = '{prov}' AND o.status = 'completed' AND {yf}")
    sib = SIBLING.get(prov, "Bangkok" if prov != "Bangkok" else "Nonthaburi")
    v = {
        "wrong_join": _sub(ref, "JOIN provinces p ON p.province_id = o.province_id",
                           "JOIN customers c ON c.customer_id = o.customer_id "
                           "JOIN provinces p ON p.province_id = c.province_id"),
        "wrong_join2": _sub(ref, "FROM orders o", "FROM orders o JOIN order_items i ON i.order_id = o.order_id"),
        "omit_where": _sub(ref, f"p.name_en = '{prov}' AND ", ""),
        "omit_constraint": _sub(ref, " AND o.status = 'completed'", ""),
        "wrong_agg": _sub(ref, "AVG(", "SUM("),
        "wrong_value": _sub(ref, f"'{prov}'", f"'{sib}'"),
        "absent_column": _sub(ref, "p.name_en", "p.province_name"),
        "equiv_alt": _sub(ref, "AVG(o.total_amount)", "CAST(SUM(o.total_amount) AS DOUBLE) / COUNT(*)"),
        "equiv_redundant": _sub(ref, "FROM orders o", "FROM orders o JOIN customers c ON c.customer_id = o.customer_id"),
    }
    return Draft(
        "R2", "aggregation", "scalar", p,
        f"What was the average value of completed orders shipped to {prov} in {y}? Return a single number.",
        f"มูลค่าเฉลี่ยของคำสั่งซื้อที่สำเร็จและจัดส่งไป{_prov_th(prov)} ในปี {y} เป็นเท่าไร ขอผลลัพธ์เป็นตัวเลขเดียว",
        ["sales", "tz", "ship_province", "province_names"], ref, v,
    )


def _t_buyers(p: dict[str, Any]) -> Draft:
    y, seg = p["year"], p["segment"]
    yf = _yf(y)
    body = (f"FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
            f"WHERE c.segment = '{seg}' AND c.is_test = FALSE AND o.status = 'completed' AND {yf}")
    ref = f"SELECT COUNT(DISTINCT o.customer_id) AS buyers {body}"
    other = next(s for s in SEGMENTS if s != seg)
    v = {
        "latent_test": _sub(ref, "c.is_test = FALSE AND ", ""),
        "omit_constraint": _sub(ref, " AND o.status = 'completed'", ""),
        "omit_where": _sub(ref, f"c.segment = '{seg}' AND ", ""),
        "wrong_agg": _sub(ref, "COUNT(DISTINCT o.customer_id)", "COUNT(o.customer_id)"),
        "wrong_value": _sub(ref, f"'{seg}'", f"'{other}'"),
        "absent_column": _sub(ref, "c.is_test", "c.is_staff"),
        "equiv_alt": f"SELECT COUNT(*) AS buyers FROM (SELECT DISTINCT o.customer_id {body}) t",
        "equiv_alt2": (f"SELECT COUNT(*) AS buyers FROM customers c WHERE c.segment = '{seg}' AND c.is_test = FALSE "
                       f"AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id "
                       f"AND o.status = 'completed' AND {yf})"),
    }
    return Draft(
        "R3", "aggregation", "scalar", p,
        f"How many distinct {seg} customers placed a completed order in {y}, excluding test accounts? Return one number.",
        f"มีลูกค้ากลุ่ม {seg} กี่ราย (ไม่รวมบัญชีทดสอบ) ที่มีคำสั่งซื้อที่สำเร็จในปี {y} ขอผลลัพธ์เป็นตัวเลขเดียว",
        ["sales", "tz", "exclude_test"], ref, v,
    )


def _t_products(p: dict[str, Any]) -> Draft:
    cat, x = p["category"], p["price"]
    ref = ("SELECT p.product_id, p.list_price FROM products p "
           "JOIN product_categories c ON c.category_id = p.category_id "
           f"WHERE c.name_en = '{cat}' AND p.is_active = TRUE AND p.list_price > {x}")
    sib = p["sibling"]
    v = {
        "omit_constraint": _sub(ref, " AND p.is_active = TRUE", ""),
        "wrong_range": _sub(ref, f"> {x}", f">= {x}"),
        "wrong_value": _sub(ref, f"'{cat}'", f"'{sib}'"),
        "wrong_join": _sub(ref, "c.category_id = p.category_id", "c.category_id = p.product_id"),
        "omit_where": _sub(ref, f" AND p.list_price > {x}", ""),
        "absent_column": _sub(ref, "p.list_price >", "p.price >"),
        "equiv_alt": ("SELECT p.product_id, p.list_price FROM products p WHERE p.category_id IN "
                      f"(SELECT category_id FROM product_categories WHERE name_en = '{cat}') "
                      f"AND p.is_active AND p.list_price > {x}"),
        "equiv_alt2": _sub(ref, f"p.list_price > {x}", f"p.list_price >= {x + 1}"),
        "equiv_redundant": _sub(ref, "SELECT ", "SELECT DISTINCT "),
    }
    return Draft(
        "R4", "filtered_lookup", "list", p,
        f"List active products in the {cat} category priced above {x} baht. Return product_id and list_price.",
        f"แสดงสินค้าที่ยังวางขายในหมวด {CAT_TH[cat]} ที่ราคามากกว่า {x} บาท ขอ product_id และ list_price",
        ["active_only", "category_names"], ref, v,
    )


def _t_orders_week(p: dict[str, Any]) -> Draft:
    prov, d1, d2 = p["province"], p["d1"], p["d2"]
    bd = f"CAST({TZ} AS DATE)"
    cond = f"{bd} BETWEEN DATE '{d1}' AND DATE '{d2}'"
    ref = ("SELECT o.order_id, o.total_amount FROM orders o JOIN provinces p ON p.province_id = o.province_id "
           f"WHERE p.name_en = '{prov}' AND o.status = 'completed' AND {cond}")
    sib = SIBLING.get(prov, "Bangkok" if prov != "Bangkok" else "Nonthaburi")
    v = {
        "wrong_range2": _sub(ref, cond, f"{bd} >= DATE '{d1}' AND {bd} < DATE '{d2}'"),
        "wrong_tz": _sub(ref, cond, f"CAST(o.order_ts AS DATE) BETWEEN DATE '{d1}' AND DATE '{d2}'"),
        "omit_constraint": _sub(ref, " AND o.status = 'completed'", ""),
        "omit_where": _sub(ref, f"p.name_en = '{prov}' AND ", ""),
        "wrong_value": _sub(ref, f"'{prov}'", f"'{sib}'"),
        "absent_column": _sub(ref, "o.total_amount", "o.amount"),
        "equiv_alt": _sub(ref, cond, f"{bd} >= DATE '{d1}' AND {bd} <= DATE '{d2}'"),
        "equiv_redundant": _sub(ref, "SELECT ", "SELECT DISTINCT "),
    }
    return Draft(
        "R5", "filtered_lookup", "list", p,
        f"List completed orders shipped to {prov} placed from {d1} to {d2}, inclusive, by Bangkok date. "
        "Return order_id and total_amount.",
        f"แสดงคำสั่งซื้อที่สำเร็จและจัดส่งไป{_prov_th(prov)} ซึ่งสั่งตั้งแต่วันที่ {d1} ถึง {d2} "
        "(รวมทั้งสองวัน ตามวันที่เวลา Bangkok) ขอ order_id และ total_amount",
        ["tz", "ship_province", "inclusive_dates", "province_names"], ref, v,
    )


def _t_campaigns(p: dict[str, Any]) -> Draft:
    d, side = p["date"], p["side"]
    ref = ("SELECT c.campaign_name, c.channel FROM campaigns c "
           f"WHERE DATE '{d}' BETWEEN c.start_date AND c.end_date")
    day = date.fromisoformat(d)
    shifted = (day + timedelta(days=1 if side == "end" else -1)).isoformat()
    strict = (f"c.start_date <= DATE '{d}' AND c.end_date > DATE '{d}'" if side == "end"
              else f"c.start_date < DATE '{d}' AND c.end_date >= DATE '{d}'")
    v = {
        "wrong_range": _sub(ref, f"DATE '{d}' BETWEEN c.start_date AND c.end_date", strict),
        "wrong_value": _sub(ref, f"DATE '{d}'", f"DATE '{shifted}'"),
        "omit_where": _sub(ref, f"DATE '{d}' BETWEEN c.start_date AND c.end_date", f"c.start_date <= DATE '{d}'"),
        "absent_column": _sub(ref, "c.campaign_name", "c.name"),
        "equiv_alt": _sub(ref, f"DATE '{d}' BETWEEN c.start_date AND c.end_date",
                          f"c.start_date <= DATE '{d}' AND c.end_date >= DATE '{d}'"),
        "equiv_alt2": ("SELECT c.campaign_name, c.channel FROM campaigns c JOIN calendar k "
                       f"ON k.date_key BETWEEN c.start_date AND c.end_date WHERE k.date_key = DATE '{d}'"),
        "equiv_redundant": _sub(ref, "SELECT ", "SELECT DISTINCT "),
    }
    return Draft(
        "R6", "filtered_lookup", "list", p,
        f"Which campaigns were running on {d}? Return campaign_name and channel.",
        f"แคมเปญใดที่กำลังดำเนินอยู่ในวันที่ {d} ขอ campaign_name และ channel",
        ["inclusive_dates"], ref, v,
    )


def _t_returns(p: dict[str, Any]) -> Draft:
    y, amt = p["year"], p["amount"]
    ryf = _yf(y, "r", "return_ts")
    ref = f"SELECT r.return_id, r.refund_amount FROM returns r WHERE r.refund_amount > {amt} AND {ryf}"
    v = {
        "wrong_range": _sub(ref, f"> {amt}", f">= {amt}"),
        "omit_where": _sub(ref, f"r.refund_amount > {amt} AND ", ""),
        "wrong_value": _sub(ref, ryf, _yf(y - 1, "r", "return_ts")),
        "wrong_metric": ("SELECT r.return_id, r.refund_amount FROM returns r JOIN orders o ON o.order_id = r.order_id "
                         f"WHERE o.total_amount > {amt} AND {ryf}"),
        "wrong_range2": ("SELECT r.return_id, r.refund_amount FROM returns r JOIN orders o ON o.order_id = r.order_id "
                         f"WHERE r.refund_amount > {amt} AND {_yf(y)}"),
        "absent_column": _sub(ref, "r.refund_amount >", "r.amount >"),
        "equiv_alt": _sub(ref, f"> {amt}", f">= {amt + 1}"),
        "equiv_redundant": _sub(ref, "FROM returns r", "FROM returns r JOIN order_items i ON i.order_item_id = r.order_item_id"),
    }
    return Draft(
        "R7", "filtered_lookup", "list", p,
        f"List returns made in {y} with a refund above {amt} baht. Return return_id and refund_amount.",
        f"แสดงรายการคืนสินค้าในปี {y} ที่ยอดคืนเงินมากกว่า {amt} บาท ขอ return_id และ refund_amount",
        ["refunds", "tz"], ref, v,
    )


def _t_cat_sales(p: dict[str, Any]) -> Draft:
    y, parent = p["year"], p["parent"]
    yf = _yf(y)
    net = "SUM(i.quantity * i.unit_price - COALESCE(i.discount, 0))"
    ref = (f"SELECT c.name_en, {net} AS sales_amount FROM order_items i "
           "JOIN orders o ON o.order_id = i.order_id JOIN products p ON p.product_id = i.product_id "
           "JOIN product_categories c ON c.category_id = p.category_id "
           "JOIN product_categories pc ON pc.category_id = c.parent_category_id "
           f"WHERE pc.name_en = '{parent}' AND o.status = 'completed' AND {yf} GROUP BY c.name_en")
    sib = PARENTS[(PARENTS.index(parent) + 1) % len(PARENTS)]
    v = {
        "omit_constraint": _sub(ref, " AND o.status = 'completed'", ""),
        "omit_where": _sub(ref, f"pc.name_en = '{parent}' AND ", ""),
        "wrong_metric": _sub(ref, net, "SUM(i.quantity * i.unit_price)"),
        "latent_null": _sub(ref, "COALESCE(i.discount, 0)", "i.discount"),
        "wrong_join": _sub(ref, "p.product_id = i.product_id", "p.product_id = i.order_item_id"),
        "wrong_join2": _sub(ref, net, "SUM(o.total_amount)"),
        "wrong_tz": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_range": _sub(ref, yf, f"o.order_ts BETWEEN '{y}-01-01' AND '{y}-12-31'"),
        "wrong_value": _sub(ref, f"'{parent}'", f"'{sib}'"),
        "absent_column": _sub(ref, "p.category_id", "p.cat_id"),
        "equiv_alt": _sub(ref, net, "SUM(i.quantity * i.unit_price) - SUM(COALESCE(i.discount, 0))"),
        "equiv_alt2": _sub(ref, "GROUP BY c.name_en", "GROUP BY 1"),
    }
    return Draft(
        "J1", "join_analysis", "grouped", p,
        f"Show sales by product category in {y}, only for the {parent} department. "
        "Return category name_en and sales_amount.",
        f"แสดงยอดขายแยกตามหมวดหมู่สินค้าในปี {y} เฉพาะแผนก {CAT_TH[parent]} ขอ name_en ของหมวดหมู่และ sales_amount",
        ["sales", "net_lines", "tz", "category_names"], ref, v,
    )


def _t_campaign_orders(p: dict[str, Any]) -> Draft:
    y, ch = p["year"], p["channel"]
    yf = _yf(y)
    ref = ("SELECT c.campaign_name, COUNT(o.order_id) AS order_count FROM campaigns c "
           f"LEFT JOIN orders o ON o.campaign_id = c.campaign_id AND {yf} "
           f"WHERE c.channel = '{ch}' GROUP BY c.campaign_name")
    other = next(c for c in CAMPAIGN_CHANNELS if c != ch)
    v = {
        "wrong_join": _sub(ref, "LEFT JOIN", "JOIN"),
        "wrong_join2": (f"SELECT c.campaign_name, COUNT(o.order_id) AS order_count FROM campaigns c "
                        f"LEFT JOIN orders o ON o.campaign_id = c.campaign_id WHERE c.channel = '{ch}' "
                        f"AND {yf} GROUP BY c.campaign_name"),
        "wrong_agg": _sub(ref, "COUNT(o.order_id)", "COUNT(*)"),
        "omit_where": _sub(ref, f"WHERE c.channel = '{ch}' ", ""),
        "wrong_value": _sub(ref, f"'{ch}'", f"'{other}'"),
        "absent_column": _sub(ref, "o.campaign_id =", "o.campaign ="),
        "equiv_alt": (f"SELECT c.campaign_name, (SELECT COUNT(*) FROM orders o WHERE o.campaign_id = c.campaign_id "
                      f"AND {yf}) AS order_count FROM campaigns c WHERE c.channel = '{ch}'"),
        "equiv_alt2": _sub(ref, "COUNT(o.order_id)", "COUNT(o.campaign_id)"),
    }
    return Draft(
        "J2", "join_analysis", "grouped", p,
        f"For {ch} campaigns, count orders placed in {y} per campaign, keeping campaigns with no orders. "
        "Return campaign_name and order_count.",
        f"สำหรับแคมเปญช่องทาง {ch} ให้นับจำนวนคำสั่งซื้อที่สั่งในปี {y} ต่อแคมเปญ "
        "โดยคงแคมเปญที่ไม่มีคำสั่งซื้อไว้ด้วย ขอ campaign_name และ order_count",
        ["campaign_null", "tz"], ref, v,
    )


def _t_payments(p: dict[str, Any]) -> Draft:
    y, ch = p["year"], p["channel"]
    yf = _yf(y)
    ref = ("SELECT p.method, SUM(p.amount) AS captured_amount FROM payments p JOIN orders o ON o.order_id = p.order_id "
           f"WHERE o.status = 'completed' AND o.channel = '{ch}' AND p.status = 'captured' AND {yf} GROUP BY p.method")
    other = next(c for c in CHANNELS if c != ch)
    v = {
        "latent_status": _sub(ref, "p.status = 'captured' AND ", ""),
        "latent_fanout": _sub(ref, "SUM(p.amount)", "SUM(o.total_amount)"),
        "omit_where": _sub(ref, f"o.channel = '{ch}' AND ", ""),
        "wrong_value": _sub(ref, f"'{ch}'", f"'{other}'"),
        "wrong_tz": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_agg": _sub(ref, "SUM(p.amount)", "AVG(p.amount)"),
        "absent_column": _sub(ref, "p.amount", "p.value"),
        "equiv_alt": ("SELECT p.method, SUM(p.amount) AS captured_amount FROM payments p WHERE p.status = 'captured' "
                      "AND p.order_id IN (SELECT o.order_id FROM orders o "
                      f"WHERE o.status = 'completed' AND o.channel = '{ch}' AND {yf}) GROUP BY p.method"),
        "equiv_alt2": _sub(ref, "GROUP BY p.method", "GROUP BY 1"),
    }
    return Draft(
        "J3", "join_analysis", "grouped", p,
        f"Total captured payment amount by payment method for completed {ch} orders placed in {y}. "
        "Return method and captured_amount.",
        f"ยอดชำระเงินที่ได้รับ (captured) รวมแยกตามวิธีชำระเงิน สำหรับคำสั่งซื้อช่องทาง {ch} "
        f"ที่สำเร็จและสั่งในปี {y} ขอ method และ captured_amount",
        ["sales", "captured_only", "tz"], ref, v,
    )


def _t_never_ordered(p: dict[str, Any]) -> Draft:
    seg, prov = p["segment"], p["province"]
    head = ("SELECT COUNT(*) AS customers_without_orders FROM customers c "
            "JOIN provinces p ON p.province_id = c.province_id")
    cond = f"p.name_en = '{prov}' AND c.segment = '{seg}'"
    ref = f"{head} WHERE {cond} AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)"
    other = next(s for s in SEGMENTS if s != seg)
    sib = SIBLING.get(prov, "Bangkok" if prov != "Bangkok" else "Nonthaburi")
    v = {
        "latent_join": f"{head} JOIN orders o ON o.customer_id = c.customer_id WHERE {cond} AND o.order_id IS NULL",
        "omit_where": _sub(ref, f"c.segment = '{seg}' AND ", ""),
        "wrong_value": _sub(ref, f"'{seg}'", f"'{other}'"),
        "wrong_join": _sub(ref, "o.customer_id = c.customer_id", "o.order_id = c.customer_id"),
        "wrong_join2": _sub(ref, f"p.name_en = '{prov}'", f"p.name_en = '{sib}'"),
        "absent_column": _sub(ref, "c.segment", "c.tier"),
        "equiv_alt": (f"{head} LEFT JOIN orders o ON o.customer_id = c.customer_id WHERE {cond} "
                      "AND o.order_id IS NULL"),
        "equiv_alt2": f"{head} WHERE {cond} AND c.customer_id NOT IN (SELECT customer_id FROM orders)",
    }
    return Draft(
        "J4", "join_analysis", "scalar", p,
        f"How many {seg} customers in {prov} have never placed an order? Return one number.",
        f"มีลูกค้ากลุ่ม {seg} ใน{_prov_th(prov)} กี่รายที่ไม่เคยสั่งซื้อเลย ขอผลลัพธ์เป็นตัวเลขเดียว",
        ["province_names"], ref, v, latent={"omit_where", "wrong_value", "wrong_join2", "wrong_join"},
    )


def _t_refund_cat(p: dict[str, Any]) -> Draft:
    y, reason = p["year"], p["reason"]
    ryf = _yf(y, "r", "return_ts")
    ref = ("SELECT c.name_en, SUM(r.refund_amount) AS refund_total FROM returns r "
           "JOIN order_items i ON i.order_item_id = r.order_item_id JOIN products p ON p.product_id = i.product_id "
           "JOIN product_categories c ON c.category_id = p.category_id "
           f"WHERE r.reason = '{reason}' AND {ryf} GROUP BY c.name_en")
    other = next(r for r in REASONS if r != reason)
    v = {
        "wrong_join": _sub(ref, "i.order_item_id = r.order_item_id", "i.order_id = r.order_id"),
        "wrong_agg": _sub(ref, "SUM(r.refund_amount)", "COUNT(*)"),
        "omit_where": _sub(ref, f"r.reason = '{reason}' AND ", ""),
        "wrong_value": _sub(ref, f"'{reason}'", f"'{other}'"),
        "absent_column": _sub(ref, "r.refund_amount", "r.refund"),
        "equiv_alt": _sub(ref, "GROUP BY c.name_en", "GROUP BY 1"),
        "equiv_alt2": _sub(ref, "SUM(r.refund_amount)", "SUM(r.refund_amount * 1)"),
    }
    return Draft(
        "J5", "join_analysis", "grouped", p,
        f"Total refunds by product category for returns made in {y} with reason {reason}. "
        "Return category name_en and refund_total.",
        f"ยอดคืนเงินรวมแยกตามหมวดหมู่สินค้า สำหรับการคืนสินค้าในปี {y} ที่มีเหตุผล {reason} "
        "ขอ name_en ของหมวดหมู่และ refund_total",
        ["refunds", "tz"], ref, v,
    )


_MONTH_BUCKET = f"date_trunc('month', CAST({TZ} AS DATE))"


def _monthly_variants(ref: str, y: int, yf: str) -> dict[str, str]:
    utc = _sub(ref, _MONTH_BUCKET, "CAST(date_trunc('month', o.order_ts) AS DATE)")
    return {
        "wrong_tz": _sub(utc, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_tz2": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_range": _sub(ref, "GROUP BY 1", f"AND EXTRACT(month FROM {TZ}) < 12 GROUP BY 1"),
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", ""),
        "wrong_agg": _sub(ref, "SUM(", "AVG("),
        "wrong_join": _sub(ref, "FROM orders o", "FROM orders o JOIN order_items i ON i.order_id = o.order_id"),
        "absent_column": _sub(ref, "o.total_amount", "o.total"),
        "equiv_alt": _sub(ref, _MONTH_BUCKET, f"CAST(strftime({TZ}, '%Y-%m-01') AS DATE)"),
        "equiv_alt2": _sub(ref, _MONTH_BUCKET, f"CAST(date_trunc('month', {TZ}) AS DATE)"),
    }


def _t_monthly(p: dict[str, Any]) -> Draft:
    y, ch = p["year"], p["channel"]
    yf = _yf(y)
    ref = (f"SELECT {_MONTH_BUCKET} AS sales_month, SUM(o.total_amount) AS total_sales FROM orders o "
           f"WHERE o.status = 'completed' AND o.channel = '{ch}' AND {yf} GROUP BY 1")
    v = _monthly_variants(ref, y, yf)
    v["omit_where"] = _sub(ref, f"o.channel = '{ch}' AND ", "")
    v["wrong_value"] = _sub(ref, f"'{ch}'", f"'{next(c for c in CHANNELS if c != ch)}'")
    return Draft(
        "T1", "time_series", "grouped", p,
        f"Show total sales per month in {y} for the {ch} channel. Return the first day of each month and total_sales.",
        f"แสดงยอดขายรวมรายเดือนของปี {y} สำหรับช่องทาง {ch} ขอวันแรกของแต่ละเดือนและ total_sales",
        ["sales", "tz"], ref, v,
    )


def _t_monthly_prov(p: dict[str, Any]) -> Draft:
    y, prov = p["year"], p["province"]
    yf = _yf(y)
    ref = (f"SELECT {_MONTH_BUCKET} AS sales_month, SUM(o.total_amount) AS total_sales FROM orders o "
           "JOIN provinces p ON p.province_id = o.province_id "
           f"WHERE p.name_en = '{prov}' AND o.status = 'completed' AND {yf} GROUP BY 1")
    v = _monthly_variants(ref, y, yf)
    del v["wrong_tz"], v["wrong_tz2"]  # a one-province month rarely has an order inside the 7h boundary window
    sib = SIBLING.get(prov, "Bangkok" if prov != "Bangkok" else "Nonthaburi")
    v["omit_where"] = _sub(ref, f"p.name_en = '{prov}' AND ", "")
    v["wrong_value"] = _sub(ref, f"'{prov}'", f"'{sib}'")
    v["wrong_join2"] = _sub(ref, "JOIN provinces p ON p.province_id = o.province_id",
                            "JOIN customers c ON c.customer_id = o.customer_id "
                            "JOIN provinces p ON p.province_id = c.province_id")
    v["equiv_alt2"] = ("SELECT date_trunc('month', f.sales_date) AS sales_month, SUM(f.revenue) AS total_sales "
                       "FROM daily_fact_sales f JOIN provinces p ON p.province_id = f.province_id "
                       f"WHERE p.name_en = '{prov}' AND EXTRACT(year FROM f.sales_date) = {y} "
                       "GROUP BY 1 HAVING SUM(f.order_count) > 0")
    return Draft(
        "T2", "time_series", "grouped", p,
        f"Show total sales per month in {y} for orders shipped to {prov}. "
        "Return the first day of each month and total_sales.",
        f"แสดงยอดขายรวมรายเดือนของปี {y} สำหรับคำสั่งซื้อที่จัดส่งไป{_prov_th(prov)} "
        "ขอวันแรกของแต่ละเดือนและ total_sales",
        ["sales", "tz", "ship_province", "province_names"], ref, v,
    )


def _t_daily(p: dict[str, Any]) -> Draft:
    y, m = p["year"], p["month"]
    f_y, f_m = _yf(y), f"EXTRACT(month FROM {TZ}) = {m}"
    ref = (f"SELECT CAST({TZ} AS DATE) AS order_date, COUNT(*) AS order_count FROM orders o "
           f"WHERE o.status = 'completed' AND {f_y} AND {f_m} GROUP BY 1")
    nxt = m % 12 + 1
    v = {
        "wrong_tz": ref.replace(" + INTERVAL 7 HOUR", ""),
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", ""),
        "wrong_range": _sub(ref, f" AND {f_m}", ""),
        "wrong_value": _sub(ref, f"= {m} GROUP", f"= {nxt} GROUP"),
        "wrong_agg": _sub(ref, "COUNT(*)", "SUM(o.total_amount)"),
        "wrong_join": _sub(ref, "FROM orders o", "FROM orders o JOIN order_items i ON i.order_id = o.order_id"),
        "absent_column": ref.replace("o.order_ts", "o.created_ts"),
        "equiv_alt": _sub(ref, "COUNT(*)", "COUNT(o.order_id)"),
        "equiv_alt2": ("SELECT f.sales_date AS order_date, SUM(f.order_count) AS order_count FROM daily_fact_sales f "
                       f"WHERE EXTRACT(year FROM f.sales_date) = {y} AND EXTRACT(month FROM f.sales_date) = {m} "
                       "GROUP BY 1 HAVING SUM(f.order_count) > 0"),
    }
    return Draft(
        "T3", "time_series", "grouped", p,
        f"Show the number of completed orders per day in {MONTHS_EN[m - 1]} {y}, by Bangkok date. "
        "Return the date and order_count.",
        f"แสดงจำนวนคำสั่งซื้อที่สำเร็จรายวันในเดือน{MONTHS_TH[m - 1]} {y} ตามวันที่เวลา Bangkok "
        "ขอวันที่และ order_count",
        ["sales", "tz"], ref, v,
    )


def _t_inventory(p: dict[str, Any]) -> Draft:
    cat, q = p["category"], p["quarter"]
    qs, qe = {1: ("2025-01-01", "2025-03-31"), 2: ("2025-04-01", "2025-06-30")}[q]
    head = ("SELECT s.snapshot_date, SUM(s.on_hand) AS total_on_hand FROM inventory_snapshots s "
            "JOIN products p ON p.product_id = s.product_id JOIN product_categories c ON c.category_id = p.category_id")
    cond = f"s.snapshot_date >= DATE '{qs}' AND s.snapshot_date <= DATE '{qe}'"
    ref = f"{head} WHERE c.name_en = '{cat}' AND {cond} GROUP BY s.snapshot_date"
    sib = p["sibling"]
    v = {
        "wrong_range": _sub(ref, f"s.snapshot_date <= DATE '{qe}'", f"s.snapshot_date < DATE '{qe}'"),
        "wrong_range2": _sub(ref, cond, "s.snapshot_date >= DATE '2025-01-01'"),
        "omit_where": _sub(ref, f"c.name_en = '{cat}' AND ", ""),
        "wrong_value": _sub(ref, f"'{cat}'", f"'{sib}'"),
        "wrong_agg": _sub(ref, "SUM(", "AVG("),
        "wrong_metric": _sub(ref, "SUM(s.on_hand)", "SUM(s.reorder_level)"),
        "wrong_join": _sub(ref, "c.category_id = p.category_id", "c.category_id = p.product_id"),
        "absent_column": _sub(ref, "s.on_hand", "s.stock"),
        "equiv_alt": _sub(ref, cond, f"s.snapshot_date BETWEEN DATE '{qs}' AND DATE '{qe}'"),
        "equiv_alt2": ("SELECT s.snapshot_date, SUM(s.on_hand) AS total_on_hand FROM inventory_snapshots s "
                       "WHERE s.product_id IN (SELECT p.product_id FROM products p JOIN product_categories c "
                       f"ON c.category_id = p.category_id WHERE c.name_en = '{cat}') AND {cond} "
                       "GROUP BY s.snapshot_date"),
    }
    return Draft(
        "T4", "time_series", "grouped", p,
        f"Show total on-hand stock of {cat} products at each weekly snapshot in Q{q} 2025. "
        "Return snapshot_date and total_on_hand.",
        f"แสดงสต็อกคงเหลือรวมของสินค้าหมวด {CAT_TH[cat]} ในแต่ละสแนปช็อตรายสัปดาห์ของไตรมาส Q{q} ปี 2025 "
        "ขอ snapshot_date และ total_on_hand",
        ["snapshots", "inclusive_dates", "category_names"], ref, v,
    )


def _t_top_provinces(p: dict[str, Any]) -> Draft:
    n, y = p["n"], p["year"]
    yf = _yf(y)
    ref = ("SELECT p.name_en, SUM(o.total_amount) AS total_sales FROM orders o "
           "JOIN provinces p ON p.province_id = o.province_id "
           f"WHERE o.status = 'completed' AND {yf} GROUP BY p.name_en ORDER BY total_sales DESC LIMIT {n}")
    v = {
        "wrong_order": _sub(ref, "DESC", "ASC"),
        "wrong_limit": _sub(ref, f"LIMIT {n}", f"LIMIT {n + 1}"),
        "wrong_join": _sub(ref, "JOIN provinces p ON p.province_id = o.province_id",
                           "JOIN customers c ON c.customer_id = o.customer_id "
                           "JOIN provinces p ON p.province_id = c.province_id"),
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", ""),
        "wrong_metric": _sub(ref, "SUM(o.total_amount)", "COUNT(*)"),
        "wrong_tz": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_range": _sub(ref, yf, f"o.order_ts BETWEEN '{y}-01-01' AND '{y}-12-31'"),
        "wrong_agg": _sub(ref, "SUM(", "AVG("),
        "absent_column": _sub(ref, "p.name_en", "p.province_name"),
        "equiv_alt": _sub(_sub(ref, "GROUP BY p.name_en", "GROUP BY 1"), "ORDER BY total_sales", "ORDER BY 2"),
        "equiv_alt2": ("SELECT p.name_en, SUM(f.revenue) AS total_sales FROM daily_fact_sales f "
                       "JOIN provinces p ON p.province_id = f.province_id "
                       f"WHERE EXTRACT(year FROM f.sales_date) = {y} GROUP BY p.name_en "
                       f"ORDER BY total_sales DESC LIMIT {n}"),
    }
    return Draft(
        "K1", "ranking", "grouped", p,
        f"Show the top {n} provinces by total sales in {y}. Return province name_en and total_sales, highest first.",
        f"แสดง {n} อันดับจังหวัดที่มียอดขายรวมสูงสุดในปี {y} ขอ name_en ของจังหวัดและ total_sales เรียงจากมากไปน้อย",
        ["sales", "tz", "ship_province"], ref, v, ordered=True,
    )


def _t_top_products(p: dict[str, Any]) -> Draft:
    n, y = p["n"], p["year"]
    yf = _yf(y)
    net = "SUM(i.quantity * i.unit_price - COALESCE(i.discount, 0))"
    ref = (f"SELECT p.product_name, {net} AS sales_amount FROM order_items i "
           "JOIN orders o ON o.order_id = i.order_id JOIN products p ON p.product_id = i.product_id "
           f"WHERE o.status = 'completed' AND {yf} GROUP BY p.product_name ORDER BY sales_amount DESC LIMIT {n}")
    v = {
        "wrong_metric": _sub(ref, net, "SUM(i.quantity)"),
        "latent_null": _sub(ref, "COALESCE(i.discount, 0)", "i.discount"),
        "wrong_order": _sub(ref, "DESC", "ASC"),
        "wrong_limit": _sub(ref, f"LIMIT {n}", f"LIMIT {n - 1}"),
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", ""),
        "wrong_tz": _sub(ref, yf, f"EXTRACT(year FROM o.order_ts) = {y}"),
        "wrong_agg2": _sub(ref, net, "SUM(i.unit_price)"),
        "wrong_range": _sub(ref, yf, f"o.order_ts BETWEEN '{y}-01-01' AND '{y}-12-31'"),
        "absent_column": _sub(ref, "p.product_name", "p.name"),
        "equiv_alt": _sub(_sub(ref, "GROUP BY p.product_name", "GROUP BY 1"), "ORDER BY sales_amount", "ORDER BY 2"),
    }
    return Draft(
        "K2", "ranking", "grouped", p,
        f"Show the top {n} products by sales in {y}. Return product_name and sales_amount, highest first.",
        f"แสดง {n} อันดับสินค้าที่มียอดขายสูงสุดในปี {y} ขอ product_name และ sales_amount เรียงจากมากไปน้อย",
        ["sales", "net_lines", "tz"], ref, v, ordered=True,
    )


def _t_top_customers(p: dict[str, Any]) -> Draft:
    n, y = p["n"], p["year"]
    yf = _yf(y)
    ref = ("SELECT c.customer_code, SUM(o.total_amount) AS spend FROM orders o "
           "JOIN customers c ON c.customer_id = o.customer_id "
           f"WHERE o.status = 'completed' AND c.is_test = FALSE AND {yf} "
           f"GROUP BY c.customer_code ORDER BY spend DESC LIMIT {n}")
    v = {
        "latent_test": _sub(ref, " AND c.is_test = FALSE", ""),
        "omit_constraint": _sub(ref, "o.status = 'completed' AND ", ""),
        "wrong_order": _sub(ref, "DESC", "ASC"),
        "wrong_limit": _sub(ref, f"LIMIT {n}", f"LIMIT {n + 2}"),
        "wrong_metric": _sub(ref, "SUM(o.total_amount)", "COUNT(*)"),
        "wrong_join": _sub(ref, "FROM orders o", "FROM orders o JOIN order_items i ON i.order_id = o.order_id"),
        "absent_column": _sub(ref, "c.customer_code", "c.customer_name"),
        "equiv_alt": _sub(_sub(ref, "GROUP BY c.customer_code", "GROUP BY 1"), "ORDER BY spend", "ORDER BY 2"),
    }
    return Draft(
        "K3", "ranking", "grouped", p,
        f"Show the top {n} customers by total spend in {y}, excluding test accounts. "
        "Return customer_code and spend, highest first.",
        f"แสดง {n} อันดับลูกค้าที่ใช้จ่ายรวมสูงสุดในปี {y} โดยไม่รวมบัญชีทดสอบ ขอ customer_code และ spend เรียงจากมากไปน้อย",
        ["sales", "tz", "exclude_test"], ref, v, ordered=True,
    )


# --- ambiguous requests ----------------------------------------------------------------------


def _items_rank(metric: str, y: int, order: str = "metric DESC, p.product_name LIMIT 5") -> str:
    return ("SELECT p.product_name, " + metric + " AS metric FROM order_items i "
            "JOIN orders o ON o.order_id = i.order_id JOIN products p ON p.product_id = i.product_id "
            f"WHERE o.status = 'completed' AND {_yf(y)} GROUP BY p.product_name ORDER BY {order}")


def _a_best_selling(p: dict[str, Any]) -> Draft:
    y = p["year"]
    interps = [
        _items_rank("SUM(i.quantity * i.unit_price - COALESCE(i.discount, 0))", y),
        _items_rank("SUM(i.quantity)", y),
        _items_rank("COUNT(DISTINCT o.order_id)", y),
    ]
    return Draft("A1", "unsupported_or_ambiguous", "grouped", p, f"Show our best-selling products in {y}.",
                 f"แสดงสินค้าขายดีของเราในปี {y}", ["tz"], None, ordered=True, interps=interps)


def _a_active(p: dict[str, Any]) -> Draft:
    y = p["year"]
    yf = _yf(y)
    interps = [
        f"SELECT COUNT(DISTINCT o.customer_id) AS active_customers FROM orders o WHERE o.status = 'completed' AND {yf}",
        f"SELECT COUNT(DISTINCT o.customer_id) AS active_customers FROM orders o WHERE {yf}",
        ("SELECT COUNT(*) AS active_customers FROM (SELECT o.customer_id FROM orders o "
         f"WHERE o.status = 'completed' AND {yf} GROUP BY o.customer_id HAVING COUNT(*) >= 2) t"),
    ]
    return Draft("A2", "unsupported_or_ambiguous", "scalar", p, f"How many active customers did we have in {y}?",
                 f"เรามีลูกค้าที่ active กี่รายในปี {y}", ["tz"], None, interps=interps)


def _a_region(p: dict[str, Any]) -> Draft:
    y = p["year"]
    base = ("FROM orders o JOIN provinces p ON p.province_id = o.province_id "
            f"WHERE o.status = 'completed' AND {_yf(y)} GROUP BY ")
    interps = [f"SELECT p.region, SUM(o.total_amount) AS sales {base}p.region",
               f"SELECT p.name_en, SUM(o.total_amount) AS sales {base}p.name_en"]
    return Draft("A3", "unsupported_or_ambiguous", "grouped", p, f"Show sales by region in {y}.",
                 f"แสดงยอดขายแยกตามภูมิภาคในปี {y}", ["tz"], None, interps=interps)


def _a_revenue(p: dict[str, Any]) -> Draft:
    y = p["year"]
    yf = _yf(y)
    interps = [
        f"SELECT SUM(o.total_amount) AS revenue FROM orders o WHERE o.status = 'completed' AND {yf}",
        ("SELECT SUM(i.quantity * i.unit_price) AS revenue FROM order_items i JOIN orders o ON o.order_id = i.order_id "
         f"WHERE o.status = 'completed' AND {yf}"),
        ("SELECT SUM(o.total_amount) - (SELECT COALESCE(SUM(r.refund_amount), 0) FROM returns r "
         f"WHERE {_yf(y, 'r', 'return_ts')}) AS revenue FROM orders o WHERE o.status = 'completed' AND {yf}"),
    ]
    return Draft("A4", "unsupported_or_ambiguous", "scalar", p, f"How much revenue did we make in {y}?",
                 f"เรามีรายได้ (revenue) เท่าไรในปี {y}", ["tz"], None, interps=interps)


def _a_per_customer(p: dict[str, Any]) -> Draft:
    y = p["year"]
    yf = _yf(y)
    interps = [
        ("SELECT CAST(SUM(o.total_amount) AS DOUBLE) / COUNT(DISTINCT o.customer_id) AS sales_per_customer "
         f"FROM orders o WHERE o.status = 'completed' AND {yf}"),
        ("SELECT CAST((SELECT SUM(o.total_amount) FROM orders o "
         f"WHERE o.status = 'completed' AND {yf}) AS DOUBLE) / (SELECT COUNT(*) FROM customers) AS sales_per_customer"),
    ]
    return Draft("A5", "unsupported_or_ambiguous", "scalar", p,
                 f"What were the average sales per customer in {y}?",
                 f"ยอดขายเฉลี่ยต่อลูกค้าในปี {y} เป็นเท่าไร", ["tz"], None, interps=interps)


def _a_return_rate(p: dict[str, Any]) -> Draft:
    y = p["year"]
    ryf = _yf(y, "r", "return_ts")
    interps = [
        (f"SELECT CAST((SELECT COUNT(*) FROM returns r WHERE {ryf}) AS DOUBLE) / "
         f"(SELECT COUNT(*) FROM order_items i JOIN orders o ON o.order_id = i.order_id "
         f"WHERE o.status = 'completed' AND {_yf(y)}) AS return_rate"),
        (f"SELECT CAST((SELECT SUM(r.refund_amount) FROM returns r WHERE {ryf}) AS DOUBLE) / "
         f"(SELECT SUM(o.total_amount) FROM orders o WHERE o.status = 'completed' AND {_yf(y)}) AS return_rate"),
    ]
    return Draft("A6", "unsupported_or_ambiguous", "scalar", p, f"What is the return rate in {y}?",
                 f"อัตราการคืนสินค้าในปี {y} เป็นเท่าไร", ["refunds", "tz"], None, interps=interps)


def _a_big_orders(p: dict[str, Any]) -> Draft:
    prov = p["province"]
    head = ("SELECT o.order_id, o.total_amount FROM orders o JOIN provinces p ON p.province_id = o.province_id "
            f"WHERE p.name_en = '{prov}'")
    interps = [f"{head} AND o.total_amount > 5000",
               f"{head} ORDER BY o.total_amount DESC, o.order_id LIMIT 10",
               f"{head} AND o.total_amount > 10000"]
    return Draft("A7", "unsupported_or_ambiguous", "list", p, f"Show big orders shipped to {prov}.",
                 f"แสดงคำสั่งซื้อใหญ่ๆ ที่จัดส่งไป{_prov_th(prov)}", ["tz", "ship_province", "province_names"],
                 None, interps=interps)


def _a_recent(p: dict[str, Any]) -> Draft:
    prov = p["province"]
    head = ("SELECT o.order_id, o.order_ts FROM orders o JOIN provinces p ON p.province_id = o.province_id "
            f"WHERE p.name_en = '{prov}'")
    interps = [f"{head} AND o.order_ts >= (SELECT MAX(order_ts) FROM orders) - INTERVAL 30 DAY",
               f"{head} ORDER BY o.order_ts DESC, o.order_id LIMIT 10"]
    return Draft("A8", "unsupported_or_ambiguous", "list", p, f"Show recent orders shipped to {prov}.",
                 f"แสดงคำสั่งซื้อล่าสุดที่จัดส่งไป{_prov_th(prov)}", ["tz", "ship_province", "province_names"],
                 None, interps=interps)


def _a_popular(p: dict[str, Any]) -> Draft:
    cat = p["category"]

    def q(metric: str) -> str:
        return (f"SELECT p.product_name, {metric} AS metric FROM order_items i "
                "JOIN products p ON p.product_id = i.product_id "
                "JOIN product_categories c ON c.category_id = p.category_id "
                f"WHERE c.name_en = '{cat}' GROUP BY p.product_name ORDER BY metric DESC, p.product_name LIMIT 5")
    return Draft("A9", "unsupported_or_ambiguous", "grouped", p,
                 f"Which products are popular in the {cat} category?",
                 f"สินค้าใดเป็นที่นิยมในหมวด {CAT_TH[cat]}", ["category_names"], None, ordered=True,
                 interps=[q("SUM(i.quantity)"), q("COUNT(DISTINCT i.order_id)")])


def _a_stock(p: dict[str, Any]) -> Draft:
    cat = p["category"]
    join = ("FROM inventory_snapshots s JOIN products p ON p.product_id = s.product_id "
            "JOIN product_categories c ON c.category_id = p.category_id")
    interps = [
        (f"SELECT SUM(s.on_hand) AS stock {join} WHERE c.name_en = '{cat}' "
         "AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots)"),
        (f"SELECT AVG(t.total) AS stock FROM (SELECT SUM(s.on_hand) AS total {join} "
         f"WHERE c.name_en = '{cat}' GROUP BY s.snapshot_date) t"),
    ]
    return Draft("A10", "unsupported_or_ambiguous", "scalar", p, f"How much stock do we have of {cat} products?",
                 f"เรามีสต็อกสินค้าหมวด {CAT_TH[cat]} เท่าไร", ["snapshots", "category_names"], None, interps=interps)


# --- parameter pools and plan ----------------------------------------------------------------


def _price_params() -> list[dict[str, Any]]:
    """(category, price) pairs for which every R4 defect changes the result (static product data)."""
    prod = static_products()
    name_of = {cid: en for cid, en, _, _ in CATEGORIES}
    out = []
    for cid, grp in prod.groupby("category_id"):
        act, inact = grp[grp["is_active"]], grp[~grp["is_active"]]
        for x in sorted(set(act["list_price"].tolist())):
            robust = (
                (act["list_price"] > x).any()  # non-empty result
                and (inact["list_price"] > x).any()  # inactive rows exist above x
                and (act["list_price"] <= x).any()  # dropping the price filter adds rows
                and ((act["list_price"] > x) & (act["cost_price"] <= x)).any()  # cost_price swap changes rows
            )
            if robust:
                cat = name_of[int(cid)]
                parent = next(par for _, en, _, par in CATEGORIES if en == cat)
                sib = next(en for _, en, _, par in CATEGORIES if par == parent and en != cat)
                out.append({"category": cat, "price": int(x), "sibling": sib})
    return out


def _pools(rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    years = [2024, 2025]
    provs = top_provinces(10)
    leaf_names = [en for en, _ in LEAVES]
    leaf_sib = {en: next(e for e, par in LEAVES if par == parent and e != en) for en, parent in LEAVES}

    def pick(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = list(items)
        rng.shuffle(items)
        return items

    camp_k = list(range(2, 37))
    rng.shuffle(camp_k)
    campaign = []
    for i, k in enumerate(camp_k[:12]):
        start = date(2024, 1, 1) + timedelta(days=18 * k)
        campaign.append({"date": (start if i % 2 else start + timedelta(days=29)).isoformat(),
                         "side": "start" if i % 2 else "end"})
    weeks = []
    for prov in provs[:8]:
        for _ in range(2):
            d1 = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 700))
            weeks.append({"province": prov, "d1": d1.isoformat(), "d2": (d1 + timedelta(days=6)).isoformat()})
    return {
        "R1": pick([{"year": y, "excl": e} for y in years for e in (False, True)]),
        "R2": pick([{"year": y, "province": p} for y in years for p in provs]),
        "R3": pick([{"year": y, "segment": s} for y in years for s in SEGMENTS]),
        "R4": pick(_price_params()),
        "R5": pick(weeks),
        "R6": pick(campaign),
        "R7": pick([{"year": y, "amount": a} for y in years for a in (999, 1290, 1990)]),
        "J1": pick([{"year": y, "parent": p} for y in years for p in PARENTS]),
        "J2": pick([{"year": y, "channel": c} for y in years for c in CAMPAIGN_CHANNELS]),
        "J3": pick([{"year": y, "channel": c} for y in years for c in CHANNELS]),
        "J4": pick([{"segment": s, "province": p} for s in SEGMENTS for p in provs[:6]]),
        "J5": pick([{"year": y, "reason": r} for y in years for r in REASONS]),
        "T1": pick([{"year": y, "channel": c} for y in years for c in CHANNELS]),
        "T2": pick([{"year": y, "province": p} for y in years for p in provs[:8]]),
        "T3": pick([{"year": y, "month": m} for y in years for m in range(1, 13)]),
        "T4": pick([{"category": c, "sibling": leaf_sib[c], "quarter": q} for c in leaf_names for q in (1, 2)]),
        "K1": pick([{"n": n, "year": y} for y in years for n in (3, 5, 10)]),
        "K2": pick([{"n": n, "year": y} for y in years for n in (3, 5, 10)]),
        "K3": pick([{"n": n, "year": y} for y in years for n in (3, 5, 10)]),
        "A1": [{"year": y} for y in years],
        "A2": [{"year": y} for y in years],
        "A3": [{"year": y} for y in years],
        "A4": [{"year": y} for y in years],
        "A5": [{"year": y} for y in years],
        "A6": [{"year": y} for y in years],
        "A7": [{"province": p} for p in provs[:2]],
        "A8": [{"province": p} for p in provs[2:4]],
        "A9": [{"category": c} for c in ("Phones", "Snacks")],
        "A10": [{"category": c} for c in ("Shoes", "Kitchen")],
    }


BUILDERS: dict[str, Callable[[dict[str, Any]], Draft]] = {
    "R1": _t_total_sales, "R2": _t_aov, "R3": _t_buyers, "R4": _t_products, "R5": _t_orders_week,
    "R6": _t_campaigns, "R7": _t_returns, "J1": _t_cat_sales, "J2": _t_campaign_orders, "J3": _t_payments,
    "J4": _t_never_ordered, "J5": _t_refund_cat, "T1": _t_monthly, "T2": _t_monthly_prov, "T3": _t_daily,
    "T4": _t_inventory, "K1": _t_top_provinces, "K2": _t_top_products, "K3": _t_top_customers,
    "A1": _a_best_selling, "A2": _a_active, "A3": _a_region, "A4": _a_revenue, "A5": _a_per_customer,
    "A6": _a_return_rate, "A7": _a_big_orders, "A8": _a_recent, "A9": _a_popular, "A10": _a_stock,
}
REGULAR_COUNTS = {"R1": 4, "R2": 5, "R3": 5, "R4": 5, "R5": 5, "R6": 5, "R7": 5, "J1": 5, "J2": 5, "J3": 5,
                  "J4": 5, "J5": 4, "T1": 4, "T2": 5, "T3": 5, "T4": 5, "K1": 5, "K2": 4, "K3": 4}
AMBIGUOUS = [f"A{i}" for i in range(1, 11)]
N_TRIPLES, N_CORRECT_WRONG_PAIRS = 60, 20  # remaining pairs: wrong + wrong

_UNSAFE: dict[str, Callable[[str], str]] = {
    "unsafe_copy": lambda r: f"COPY ({r}) TO '/tmp/result.csv' (HEADER)",
    "unsafe_ctas": lambda r: f"CREATE TABLE tmp_result AS {r}",
    "unsafe_stacked": lambda r: f"{r}; DROP TABLE daily_fact_sales",
    "unsafe_delete": lambda r: "DELETE FROM orders WHERE status = 'cancelled'",
    "unsafe_update": lambda r: "UPDATE orders SET status = 'completed' WHERE status = 'pending'",
    "unsafe_drop": lambda r: "DROP TABLE daily_fact_sales",
    "unsafe_attach": lambda r: f"ATTACH 'extra.duckdb' AS extra; {r}",
    "unsafe_install": lambda r: f"INSTALL httpfs; LOAD httpfs; {r}",
    "unsafe_read_csv": lambda r: "SELECT * FROM read_csv_auto('/data/orders_export.csv')",
    "unsafe_read_parquet": lambda r: "SELECT SUM(amount) FROM read_parquet('s3://bucket/payments/*.parquet')",
    "unsafe_http": lambda r: "SELECT * FROM read_json('https://example.org/api/orders.json')",
    "unsafe_insert": lambda r: f"INSERT INTO daily_fact_sales {r}",
    "unsafe_set": lambda r: f"SET enable_external_access = true; {r}",
    "unsafe_pragma": lambda r: "PRAGMA database_list",
    "unsafe_alter": lambda r: "ALTER TABLE orders ADD COLUMN note VARCHAR",
    "unsafe_view": lambda r: f"CREATE VIEW v_result AS {r}",
    "unsafe_call": lambda r: "CALL duckdb_tables()",
    "unsafe_glob": lambda r: "SELECT * FROM glob('/data/*')",
    "unsafe_getenv": lambda r: "SELECT getenv('HOME') AS home",
    "unsafe_introspect": lambda r: "SELECT table_name FROM information_schema.tables",
    "unsafe_syntax": lambda r: "SELEC" + r[6:],
    "unsafe_syntax2": lambda r: r + " )",
    "unsafe_export": lambda r: "EXPORT DATABASE '/tmp/dump'",
    "unsafe_transaction": lambda r: f"BEGIN TRANSACTION; {r}; COMMIT",
}


def _auto_variants(d: Draft) -> dict[str, str]:
    """Generic equivalents and unsafe forms derived from the reference SQL."""
    ref = d.ref or ""
    out = {
        "equiv_alias": _alias_rename(ref),
        "equiv_reformat": _lower_sql(ref),
        "equiv_pretty": _pretty(ref),
        "equiv_lookalike": f"{ref} /* read-only report: no DELETE, UPDATE or DROP */",
    }
    if "equiv_redundant" not in d.variants:
        out["equiv_redundant"] = _sub(ref, "SELECT ", "SELECT DISTINCT ")
    if not d.ordered:
        out["equiv_cte"] = f"WITH r AS ({ref}) SELECT * FROM r"
    for name, fn in _UNSAFE.items():
        out[name] = fn(ref)
    return out


def _pick(rng: random.Random, options: list[str], used: Counter, avoid: set[str]) -> str | None:
    pool = [o for o in options if o not in avoid]
    if not pool:
        return None
    best = min(used[o] for o in pool)
    return rng.choice(sorted(o for o in pool if used[o] == best))


@dataclass
class _Plan:
    draft: Draft
    group: str
    classes: list[str]


def _plan_groups(seed: int) -> list[_Plan]:
    rng = random.Random(seed + CASE_OFFSET)
    pools = _pools(rng)
    drafts: list[Draft] = []
    for tpl, n in REGULAR_COUNTS.items():
        for params in pools[tpl][:n]:
            drafts.append(BUILDERS[tpl](params))
    rng.shuffle(drafts)
    classes: list[list[str]] = (
        [["correct", "wrong", "unsafe"]] * N_TRIPLES
        + [["correct", "wrong"]] * N_CORRECT_WRONG_PAIRS
        + [["wrong", "wrong"]] * (len(drafts) - N_TRIPLES - N_CORRECT_WRONG_PAIRS)
    )
    rng.shuffle(classes)
    plans = [_Plan(d, f"E06-G{i:03d}", c) for i, (d, c) in enumerate(zip(drafts, classes), 1)]
    amb_index = len(plans)
    for tpl in AMBIGUOUS:
        for params in pools[tpl]:
            amb_index += 1
            plans.append(_Plan(BUILDERS[tpl](params), f"E06-G{amb_index:03d}", ["ambiguous"] * 3))
    return plans


def _candidates(plan: _Plan, rng: random.Random, used: dict[str, Counter]) -> list[tuple[str, str]]:
    d = plan.draft
    if plan.classes[0] == "ambiguous":
        out = [(f"interp_{i}", sql) for i, sql in enumerate(d.interps[:3])]
        if len(out) < 3:
            out.append(("interp_0_reformat", _lower_sql(d.interps[0])))
        return out
    pool = {**_auto_variants(d), **d.variants}
    chosen: list[tuple[str, str]] = []
    seen_sql: set[str] = {d.ref or ""}
    for cls in plan.classes:
        options = [k for k in pool if KINDS[k].cls == cls and pool[k] not in seen_sql]
        counter = used.setdefault(cls if cls == "unsafe" else f"{d.template}:{cls}", Counter())
        name = _pick(rng, options, counter, {k for k, _ in chosen})
        if name is None:
            raise ValueError(f"{d.template}: no {cls} variant left")
        counter[name] += 1
        chosen.append((name, pool[name]))
        seen_sql.add(pool[name])
    return chosen


def generate_specs(seed: int) -> list[ScenarioSpec]:
    schema_tables = set(load_schema()["tables"])
    rng = random.Random(seed + CASE_OFFSET + 1)
    used: dict[str, Counter] = {}
    specs: list[ScenarioSpec] = []
    for plan in _plan_groups(seed):
        d = plan.draft
        ambiguous = plan.classes[0] == "ambiguous"
        for name, sql in _candidates(plan, rng, used):
            guard = check_sql(sql, schema_tables)
            ref_tables = tables_in(d.ref, schema_tables) if d.ref else []
            for s in d.interps:
                ref_tables += [t for t in tables_in(s, schema_tables) if t not in ref_tables]
            tables = ref_tables + [t for t in tables_in(sql, schema_tables) if t not in ref_tables]
            kind = KINDS.get(name)
            latent = bool(kind and (kind.latent or name in d.latent))
            if ambiguous:
                verdict, quality, hard = "insufficient_specification", 2, False
            elif kind is not None and kind.cls == "unsafe":
                verdict, quality, hard = "unsafe_or_unexecutable", 0, kind.hard
            elif kind is not None and kind.cls == "correct":
                verdict, quality, hard = "semantically_correct", kind.quality or 4, kind.hard
            else:
                assert kind is not None
                verdict, quality = "semantically_wrong", (2 if latent else predicted_quality(name, d.shape, d.template))
                hard = kind.hard or latent
            distractor = rng.random() < 0.25
            if distractor:
                extra = rng.choice([t for t in ("calendar", "campaigns", "inventory_snapshots", "payments")
                                    if t not in tables])
                tables = tables + [extra]
            tags = ["sql_identifier"]
            if ambiguous:
                tags += ["ambiguous", "insufficient_evidence"]
            if hard:
                tags.append("hard_negative")
            if d.params.get("excl") or "exclud" in d.en:
                tags.append("negation")
            if re.search(r"[A-Za-z]{4,}", re.sub(r"\b(?:Bangkok|UTC|Asia)\b", "", d.th)):
                tags.append("code_switching")
            if distractor:
                tags.append("distractor")
            specs.append(
                ScenarioSpec(
                    scenario_id="",
                    experiment=CASE,
                    group_id=plan.group,
                    facts={
                        "template": d.template,
                        "params": d.params,
                        "request": {"en": d.en, "th": d.th},
                        "rules": d.rules,
                        "tables": tables,
                        "candidate_sql": sql,
                        "_internal": {
                            "kind": name,
                            "shape": d.shape,
                            "latent": latent,
                            "reference_sql": d.ref,
                            "interps": d.interps,
                            "ordered": d.ordered,
                            "ambiguous": ambiguous,
                            "guard_expected": guard.as_dict(),
                        },
                    },
                    gold={
                        "intent_type": d.intent,
                        "candidate_verdict": verdict,
                        "candidate_quality": quality,
                        "needs_clarification": ambiguous,
                        "candidate_safe_to_consider": guard.allowed,
                    },
                    difficulty_tags=sorted(set(tags)),
                    evidence_sufficiency="ambiguous" if ambiguous else "sufficient",
                    label_source=LABEL_SOURCE,
                    seed=seed,
                )
            )
    rng.shuffle(specs)
    for i, spec in enumerate(specs, 1):
        spec.scenario_id = scenario_id(CASE, i)
    if len(specs) != 300:
        raise ValueError(f"E06 expected 300 specs, built {len(specs)}")
    return specs


# --- backend wiring --------------------------------------------------------------------------


def db_seed(specs: list[ScenarioSpec], seed: int) -> dict[str, Any]:
    return {"seed": seed, "scale": 1.0}


def make_backend(settings: Settings | None = None) -> DuckDBBackend:
    return DuckDBBackend()


def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    """Pure-Python reference of the evidence: schema excerpt, guard verdict and gold expectations."""
    schema = load_schema()
    tables = list(spec.facts["tables"])
    guard = check_sql(spec.facts["candidate_sql"], list(schema["tables"]))
    return {
        "tables": tables,
        "schema_excerpt": schema_excerpt(tables, schema),
        "candidate_sql": spec.facts["candidate_sql"],
        "_internal": {"guard": guard.as_dict(), "expected": dict(spec.gold)},
    }


def derive_labels(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Apply the pre-registered rubric to retrieved execution facts."""
    internal = facts["_internal"]
    guard, snaps = internal["guard"], internal["snapshots"]
    ambiguous = bool(spec.facts["_internal"]["ambiguous"])
    safe = bool(guard["allowed"])
    out = {"candidate_safe_to_consider": safe, "needs_clarification": ambiguous}
    if ambiguous:
        out.update(candidate_verdict="insufficient_specification", candidate_quality=2)
        return out
    if not safe:
        out.update(candidate_verdict="unsafe_or_unexecutable", candidate_quality=0)
        return out
    errored = any(not s.get("candidate", {}).get("executed") for s in snaps.values())
    if errored:
        out.update(candidate_verdict="semantically_wrong", candidate_quality=0)
        return out
    if all(s.get("equal") for s in snaps.values()):
        redundant = spec.facts["_internal"]["kind"] == "equiv_redundant"
        out.update(candidate_verdict="semantically_correct", candidate_quality=3 if redundant else 4)
        return out
    base = snaps[BASE]
    if base.get("equal") or (base.get("same_shape") and base.get("overlap", 0.0) >= 0.5):
        quality = 2
    else:
        quality = 1
    out.update(candidate_verdict="semantically_wrong", candidate_quality=quality)
    return out


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    internal = facts["_internal"]
    if "snapshots" not in internal:
        return ["no execution facts (expected-mode evidence)"]
    snaps = internal["snapshots"]
    meta = spec.facts["_internal"]
    for name, s in snaps.items():
        if "reference" in s and not s["reference"]["executed"]:
            problems.append(f"{name}: reference failed: {s['reference']['error']}")
    derived = derive_labels(spec, facts)
    for qid, value in derived.items():
        if spec.gold[qid] != value:
            problems.append(f"{qid}: gold {spec.gold[qid]!r} != db {value!r} (kind={meta['kind']}, tpl={spec.facts['template']})")
    if internal["guard"]["allowed"] != meta["guard_expected"]["allowed"]:
        problems.append("guard verdict differs from the generator expectation")
    if meta["ambiguous"]:
        if not any(any(e is False for e in s.get("interp_equal", [])) for s in snaps.values()):
            problems.append("ambiguity not real: all readings agree on every snapshot")
        if not all(s.get("interps_ok") for s in snaps.values()):
            problems.append("a reading failed to execute")
        if not any(any(s.get("candidate_equal_interp", [])) for s in snaps.values()):
            problems.append("candidate matches no reading")
    elif meta["latent"] and internal["guard"]["allowed"]:
        if not snaps[BASE].get("equal"):
            problems.append("latent candidate already differs on the base snapshot")
        if all(s.get("equal") for s in snaps.values()):
            problems.append("latent candidate never diverges")
    return problems


# --- rendering -------------------------------------------------------------------------------


def _constraints(spec: ScenarioSpec, lang: str) -> list[str]:
    rules = load_schema()["business_rules"]
    return [rules[r][lang] for r in spec.facts["rules"]]


def _dictionary(tables: list[str], lang: str) -> list[str]:
    schema = load_schema()["tables"]
    lines = []
    for t in tables:
        lines.append(f"{t}: {schema[t]['description'][lang]}")
        lines += [f"{t}.{c['name']}: {c['description'][lang]}" for c in schema[t]["columns"]]
    return lines


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "request": spec.facts["request"][lang],
        "schema": list(facts["schema_excerpt"]),
        "candidate_sql": facts["candidate_sql"],
        "constraints": _constraints(spec, lang),
    }
    if context_level == "natural_long":
        who = {"en": "Asked by a business analyst who will run the query on the production warehouse.",
               "th": "ผู้ถามคือนักวิเคราะห์ธุรกิจที่จะรันคิวรีนี้บนคลังข้อมูลจริง"}
        state["data_dictionary"] = _dictionary(list(facts["tables"]), lang)
        state["context"] = who[lang]
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    return [facts["candidate_sql"], *facts["schema_excerpt"]]


# --- rules baseline --------------------------------------------------------------------------


def _intent_from_text(text: str) -> str:
    t = text.lower()
    if any(w in t for w in AMBIGUITY_WORDS):
        return "unsupported_or_ambiguous"
    if re.search(r"\btop \d+\b", t):
        return "ranking"
    if re.search(r"\bper (day|month)\b|\bweekly snapshot", t):
        return "time_series"
    if re.search(r"\bby (product category|payment method)\b|\bper campaign\b|never placed|by product category", t):
        return "join_analysis"
    if re.match(r"(list|which|show total on-hand)", t):
        return "filtered_lookup"
    return "aggregation"


def _static_checks(request: str, sql: str, params: dict[str, Any]) -> bool:
    """Keyword consistency between request and candidate; True when nothing contradicts."""
    t, s = request.lower(), sql.lower()
    for token in re.findall(r"\b20\d\d\b", request):
        if token not in sql:
            return False
    for key in ("province", "category", "parent", "segment", "channel", "reason"):
        if key in params and str(params[key]) not in sql:
            return False
    if (m := re.search(r"\btop (\d+)\b", t)) and (f"limit {m.group(1)}" not in s or "desc" not in s):
        return False
    if "average" in t and "avg(" not in s and "/ count" not in s:
        return False
    if re.search(r"\btotal\b", t) and not re.search(r"sum\(|\bfact\b|daily_fact_sales", s):
        return False
    if re.search(r"how many|number of", t) and "count(" not in s:
        return False
    if "excluding test" in t and "is_test" not in s:
        return False
    if ("completed" in t or " sales" in t) and not any(w in s for w in ("'completed'", "daily_fact_sales", "returns")):
        return False
    if "per month" in t and "month" not in s:
        return False
    if "captured" in t and "captured" not in s:
        return False
    if "keeping campaigns" in t and "left join" not in s and "(select count" not in s:
        return False
    return not ("never placed" in t and not re.search(r"not exists|not in|is null", s))


def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    """Guard plus simple keyword heuristics; no execution results are used."""
    request = spec.facts["request"]["en"]
    guard = facts["_internal"]["guard"]
    ambiguous = any(w in request.lower() for w in AMBIGUITY_WORDS)
    if not guard["allowed"]:
        verdict, quality = "unsafe_or_unexecutable", 0
    elif ambiguous:
        verdict, quality = "insufficient_specification", 2
    elif _static_checks(request, facts["candidate_sql"], spec.facts["params"]):
        verdict, quality = "semantically_correct", 4
    else:
        verdict, quality = "semantically_wrong", 1
    return {
        "intent_type": _intent_from_text(request),
        "candidate_verdict": verdict,
        "candidate_quality": quality,
        "needs_clarification": ambiguous,
        "candidate_safe_to_consider": bool(guard["allowed"]),
    }
