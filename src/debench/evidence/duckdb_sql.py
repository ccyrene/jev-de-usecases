"""Embedded DuckDB evidence backend for E06 (NL-to-SQL routing and candidate validation).

The deterministic synthetic warehouse is built as several independently seeded snapshots: one base
snapshot and several adversarial ones whose data breaks the coincidences that let subtly wrong
queries pass on the base snapshot (UTC hours that stay on the same Bangkok day, no test accounts,
no multi-payment orders, no customers without orders, no NULL discounts, no failed payments on
completed orders, no duplicate returns). Retrieval runs the guard and then the reference and
candidate SQL read-only on every snapshot; results go under ``facts["_internal"]`` and are used
only by the oracle and the rules baseline, never rendered into a provider-visible state.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from debench.config import ROOT
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import RUNTIME_EMBEDDED, BackendUnavailable, Evidence, timed
from debench.sql_guard import ExecResult, ReadOnlyExecutor, check_sql

BUILDER_VERSION = "e06-warehouse-v1"
SCHEMA_PATH = ROOT / "infra" / "init" / "e06_duckdb" / "schema.json"
WAREHOUSE_DIR = ROOT / "data" / "generated" / "e06" / "snapshots"
STATIC_SEED = 606
# name -> (seed offset from the case seed, adversarial)
SNAPSHOTS: dict[str, tuple[int, bool]] = {
    "base": (6000, False),
    "adv_a": (6101, True),
    "adv_b": (6202, True),
    "adv_c": (6303, True),
}
BUSINESS_TZ_HOURS = 7
N_CUSTOMERS, N_ORDERS = 6000, 24000
FIRST_DAY = np.datetime64("2024-01-01")
N_DAYS = 731  # 2024-01-01 .. 2025-12-31
CAL_START, CAL_END = date(2024, 1, 1), date(2026, 12, 31)
INVENTORY_FIRST_MONDAY = np.datetime64("2025-01-06")
INVENTORY_WEEKS = 52
N_PRODUCTS = 400
N_CAMPAIGNS = 40
CAMPAIGN_STEP_DAYS, CAMPAIGN_LENGTH_DAYS = 18, 30

# (name_en, name_th, region); Bangkok first, rest alphabetical by English name.
PROVINCES: list[tuple[str, str, str]] = [
    ("Bangkok", "กรุงเทพมหานคร", "central"), ("Amnat Charoen", "อำนาจเจริญ", "northeast"),
    ("Ang Thong", "อ่างทอง", "central"), ("Bueng Kan", "บึงกาฬ", "northeast"),
    ("Buriram", "บุรีรัมย์", "northeast"), ("Chachoengsao", "ฉะเชิงเทรา", "east"),
    ("Chai Nat", "ชัยนาท", "central"), ("Chaiyaphum", "ชัยภูมิ", "northeast"),
    ("Chanthaburi", "จันทบุรี", "east"), ("Chiang Mai", "เชียงใหม่", "north"),
    ("Chiang Rai", "เชียงราย", "north"), ("Chonburi", "ชลบุรี", "east"),
    ("Chumphon", "ชุมพร", "south"), ("Kalasin", "กาฬสินธุ์", "northeast"),
    ("Kamphaeng Phet", "กำแพงเพชร", "north"), ("Kanchanaburi", "กาญจนบุรี", "west"),
    ("Khon Kaen", "ขอนแก่น", "northeast"), ("Krabi", "กระบี่", "south"),
    ("Lampang", "ลำปาง", "north"), ("Lamphun", "ลำพูน", "north"), ("Loei", "เลย", "northeast"),
    ("Lopburi", "ลพบุรี", "central"), ("Mae Hong Son", "แม่ฮ่องสอน", "north"),
    ("Maha Sarakham", "มหาสารคาม", "northeast"), ("Mukdahan", "มุกดาหาร", "northeast"),
    ("Nakhon Nayok", "นครนายก", "central"), ("Nakhon Pathom", "นครปฐม", "central"),
    ("Nakhon Phanom", "นครพนม", "northeast"), ("Nakhon Ratchasima", "นครราชสีมา", "northeast"),
    ("Nakhon Sawan", "นครสวรรค์", "central"), ("Nakhon Si Thammarat", "นครศรีธรรมราช", "south"),
    ("Nan", "น่าน", "north"), ("Narathiwat", "นราธิวาส", "south"),
    ("Nong Bua Lamphu", "หนองบัวลำภู", "northeast"), ("Nong Khai", "หนองคาย", "northeast"),
    ("Nonthaburi", "นนทบุรี", "central"), ("Pathum Thani", "ปทุมธานี", "central"),
    ("Pattani", "ปัตตานี", "south"), ("Phang Nga", "พังงา", "south"), ("Phatthalung", "พัทลุง", "south"),
    ("Phayao", "พะเยา", "north"), ("Phetchabun", "เพชรบูรณ์", "north"),
    ("Phetchaburi", "เพชรบุรี", "west"), ("Phichit", "พิจิตร", "north"),
    ("Phitsanulok", "พิษณุโลก", "north"), ("Phra Nakhon Si Ayutthaya", "พระนครศรีอยุธยา", "central"),
    ("Phrae", "แพร่", "north"), ("Phuket", "ภูเก็ต", "south"), ("Prachinburi", "ปราจีนบุรี", "east"),
    ("Prachuap Khiri Khan", "ประจวบคีรีขันธ์", "west"), ("Ranong", "ระนอง", "south"),
    ("Ratchaburi", "ราชบุรี", "west"), ("Rayong", "ระยอง", "east"), ("Roi Et", "ร้อยเอ็ด", "northeast"),
    ("Sa Kaeo", "สระแก้ว", "east"), ("Sakon Nakhon", "สกลนคร", "northeast"),
    ("Samut Prakan", "สมุทรปราการ", "central"), ("Samut Sakhon", "สมุทรสาคร", "central"),
    ("Samut Songkhram", "สมุทรสงคราม", "central"), ("Saraburi", "สระบุรี", "central"),
    ("Satun", "สตูล", "south"), ("Sing Buri", "สิงห์บุรี", "central"), ("Sisaket", "ศรีสะเกษ", "northeast"),
    ("Songkhla", "สงขลา", "south"), ("Sukhothai", "สุโขทัย", "north"),
    ("Suphan Buri", "สุพรรณบุรี", "central"), ("Surat Thani", "สุราษฎร์ธานี", "south"),
    ("Surin", "สุรินทร์", "northeast"), ("Tak", "ตาก", "west"), ("Trang", "ตรัง", "south"),
    ("Trat", "ตราด", "east"), ("Ubon Ratchathani", "อุบลราชธานี", "northeast"),
    ("Udon Thani", "อุดรธานี", "northeast"), ("Uthai Thani", "อุทัยธานี", "central"),
    ("Uttaradit", "อุตรดิตถ์", "north"), ("Yala", "ยะลา", "south"), ("Yasothon", "ยโสธร", "northeast"),
]

# (id, name_en, name_th, parent_id)
_PARENTS = [("Electronics", "อิเล็กทรอนิกส์"), ("Home", "ของใช้ในบ้าน"), ("Fashion", "แฟชั่น"),
            ("Beauty", "ความงาม"), ("Grocery", "ของชำ"), ("Sports", "กีฬา")]
_LEAVES = [
    ("Phones", "โทรศัพท์"), ("Laptops", "แล็ปท็อป"), ("Audio", "เครื่องเสียง"),
    ("Kitchen", "เครื่องครัว"), ("Furniture", "เฟอร์นิเจอร์"), ("Decor", "ของตกแต่ง"),
    ("Menswear", "เสื้อผ้าบุรุษ"), ("Womenswear", "เสื้อผ้าสตรี"), ("Shoes", "รองเท้า"),
    ("Skincare", "ดูแลผิว"), ("Makeup", "เครื่องสำอาง"), ("Haircare", "ดูแลผม"),
    ("Snacks", "ขนม"), ("Beverages", "เครื่องดื่ม"), ("Pantry", "เครื่องปรุงและของแห้ง"),
    ("Fitness", "ฟิตเนส"), ("Outdoor", "กลางแจ้ง"), ("Cycling", "จักรยาน"),
]
CATEGORIES: list[tuple[int, str, str, int | None]] = [
    (i + 1, en, th, None) for i, (en, th) in enumerate(_PARENTS)
] + [(7 + i, en, th, i // 3 + 1) for i, (en, th) in enumerate(_LEAVES)]
LEAF_CATEGORY_IDS = [c[0] for c in CATEGORIES if c[3] is not None]
PRICE_POINTS = [49, 59, 79, 99, 129, 149, 199, 249, 299, 349, 499, 599, 799, 999, 1290, 1990, 2990, 4990]


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_excerpt(tables: list[str], schema: dict[str, Any] | None = None) -> list[str]:
    """Compact language-neutral signatures, e.g. ``orders(order_id PK, customer_id FK->customers, ...)``."""
    schema = schema or load_schema()
    lines = []
    for name in tables:
        t = schema["tables"][name]
        fk_target = {c: f["references"]["table"] for f in t["foreign_keys"] for c in f["columns"]}
        cols = []
        for col in t["columns"]:
            c = col["name"]
            tag = " PK" if c in t["primary_key"] else ""
            if c in fk_target:
                tag += f" FK->{fk_target[c]}"
            cols.append(c + tag)
        lines.append(f"{name}({', '.join(cols)})")
    return lines


# --- warehouse build -------------------------------------------------------------------------


def province_weights() -> np.ndarray:
    """Order-share weights per province (index 0 = Bangkok, always the heaviest)."""
    rank = np.random.default_rng(STATIC_SEED + 1).permutation(len(PROVINCES))
    rank[np.argmax(rank == 0)], rank[0] = rank[0], 0
    w = 1.0 / (rank + 4.0)
    return w / w.sum()


def top_provinces(n: int) -> list[str]:
    """English names of the n provinces with the most orders (static, snapshot-independent)."""
    order = np.argsort(-province_weights(), kind="stable")[:n]
    return [PROVINCES[i][0] for i in order]


def static_products() -> pd.DataFrame:
    return _static_tables(1.0)["products"]


def _static_tables(scale: float) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(STATIC_SEED)
    prov = pd.DataFrame(
        {"province_id": np.arange(1, len(PROVINCES) + 1), "name_en": [p[0] for p in PROVINCES],
         "name_th": [p[1] for p in PROVINCES], "region": [p[2] for p in PROVINCES]}
    )
    cats = pd.DataFrame(
        {"category_id": [c[0] for c in CATEGORIES], "name_en": [c[1] for c in CATEGORIES],
         "name_th": [c[2] for c in CATEGORIES],
         "parent_category_id": pd.array([c[3] for c in CATEGORIES], dtype="Int64")}
    )
    leaf = rng.choice(LEAF_CATEGORY_IDS, size=N_PRODUCTS)
    band = (np.asarray(leaf) - 7) % 6
    price = np.array([PRICE_POINTS[min(len(PRICE_POINTS) - 1, int(rng.integers(0, 12)) + b)] for b in band])
    products = pd.DataFrame(
        {"product_id": np.arange(1, N_PRODUCTS + 1), "product_name": [f"Item P{i:04d}" for i in range(1, N_PRODUCTS + 1)],
         "category_id": leaf, "list_price": price, "cost_price": (price * 0.6).astype(int),
         "is_active": rng.random(N_PRODUCTS) < 0.85}
    )
    k = np.arange(N_CAMPAIGNS)
    start = FIRST_DAY + (k * CAMPAIGN_STEP_DAYS).astype("timedelta64[D]")
    campaigns = pd.DataFrame(
        {"campaign_id": k + 1, "campaign_name": [f"Campaign {i + 1:02d}" for i in k],
         "channel": np.array(["email", "social", "search", "display"])[k % 4],
         "start_date": start, "end_date": start + np.timedelta64(CAMPAIGN_LENGTH_DAYS - 1, "D"),
         "budget": 10000 + (k % 7) * 5000}
    )
    days = pd.date_range(CAL_START, CAL_END, freq="D")
    holidays = {(1, 1), (4, 13), (4, 14), (4, 15), (5, 1), (8, 12), (10, 23), (12, 5), (12, 10), (12, 31)}
    calendar = pd.DataFrame(
        {"date_key": days, "year": days.year, "month": days.month, "day": days.day,
         "quarter": (days.month - 1) // 3 + 1, "iso_dow": days.dayofweek + 1,
         "is_weekend": days.dayofweek >= 5,
         "is_holiday": [(d.month, d.day) in holidays for d in days]}
    )
    return {"provinces": prov, "product_categories": cats, "products": products, "campaigns": campaigns,
            "calendar": calendar}


def _variable_tables(rng: np.random.Generator, adversarial: bool, scale: float,
                     products: pd.DataFrame) -> dict[str, pd.DataFrame]:
    n_cust, n_ord = max(50, int(N_CUSTOMERS * scale)), max(100, int(N_ORDERS * scale))
    n_prov = len(PROVINCES)
    pw = province_weights()

    cust_prov = rng.choice(n_prov, size=n_cust, p=pw) + 1
    is_test = (rng.random(n_cust) < 0.04) if adversarial else np.zeros(n_cust, dtype=bool)
    customers = pd.DataFrame(
        {"customer_id": np.arange(1, n_cust + 1),
         "customer_code": [f"C{i:06d}" for i in range(1, n_cust + 1)],
         "email": [f"c{i:06d}@example.com" for i in range(1, n_cust + 1)],
         "province_id": cust_prov,
         "segment": rng.choice(["retail", "wholesale", "vip"], size=n_cust, p=[0.7, 0.2, 0.1]),
         "signup_date": np.datetime64("2022-01-01") + rng.integers(0, 700, n_cust).astype("timedelta64[D]"),
         "is_test": is_test}
    )

    cw = rng.lognormal(0.0, 0.8, n_cust)
    if adversarial:
        cw = np.where(is_test, cw * 8.0, cw)
        cw = np.where((~is_test) & (rng.random(n_cust) < 0.08), 0.0, cw)  # customers with no orders
        cust_of_order = rng.choice(n_cust, size=n_ord, p=cw / cw.sum()) + 1
    else:
        first = rng.permutation(n_cust) + 1  # every customer orders at least once
        rest = rng.choice(n_cust, size=n_ord - n_cust, p=cw / cw.sum()) + 1
        cust_of_order = rng.permutation(np.concatenate([first, rest]))

    day = rng.integers(0, N_DAYS, n_ord)
    if adversarial:
        secs = rng.integers(0, 86400, n_ord)  # hours 17-23 UTC fall on the next Bangkok day
    else:
        secs = rng.integers(3 * 3600, 17 * 3600, n_ord)  # 03:00-16:59 UTC stays on the same Bangkok day
    order_ts = (FIRST_DAY.astype("datetime64[s]") + day.astype("timedelta64[D]").astype("timedelta64[s]")
                + secs.astype("timedelta64[s]"))
    status = rng.choice(["completed", "cancelled", "pending"], size=n_ord, p=[0.8, 0.12, 0.08])
    home = cust_prov[cust_of_order - 1]
    other = rng.choice(n_prov, size=n_ord, p=pw) + 1
    ship = np.where(rng.random(n_ord) < 0.8, home, other)
    camp = np.full(n_ord, -1)
    for k in range(N_CAMPAIGNS):
        lo = k * CAMPAIGN_STEP_DAYS
        hit = (day >= lo) & (day < lo + CAMPAIGN_LENGTH_DAYS) & (camp < 0) & (rng.random(n_ord) < 0.5)
        camp[hit] = k + 1

    n_lines = rng.choice([1, 2, 3, 4, 5], size=n_ord, p=[0.35, 0.3, 0.2, 0.1, 0.05])
    order_of_line = np.repeat(np.arange(n_ord), n_lines) + 1
    n_li = order_of_line.size
    ranks = np.random.default_rng(STATIC_SEED + 2).permutation(N_PRODUCTS)
    prw = 1.0 / (ranks + 3.0) ** 0.9
    prw /= prw.sum()
    pid = rng.choice(N_PRODUCTS, size=n_li, p=prw) + 1
    qty = rng.choice([1, 2, 3, 4], size=n_li, p=[0.6, 0.25, 0.1, 0.05])
    price = products["list_price"].to_numpy()[pid - 1]
    gross = qty * price
    disc = np.where(rng.random(n_li) < 0.25, rng.choice([5, 10, 20, 50], size=n_li), 0)
    disc = np.minimum(disc, gross // 2)
    disc_arr: Any = pd.array(disc, dtype="Int64")
    if adversarial:
        null_mask = (disc == 0) & (rng.random(n_li) < 0.7)
        disc_arr = pd.array(np.where(null_mask, 0, disc), dtype="Int64")
        disc_arr[null_mask] = pd.NA
    items = pd.DataFrame(
        {"order_item_id": np.arange(1, n_li + 1), "order_id": order_of_line, "product_id": pid,
         "quantity": qty, "unit_price": price, "discount": disc_arr}
    )
    net = gross - disc
    total = np.bincount(order_of_line - 1, weights=net, minlength=n_ord).astype(np.int64)
    orders = pd.DataFrame(
        {"order_id": np.arange(1, n_ord + 1), "customer_id": cust_of_order, "province_id": ship,
         "campaign_id": pd.array(np.where(camp < 0, 0, camp), dtype="Int64"), "order_ts": order_ts,
         "status": status, "channel": rng.choice(["web", "app", "store"], size=n_ord, p=[0.5, 0.35, 0.15]),
         "total_amount": total}
    )
    orders.loc[camp < 0, "campaign_id"] = pd.NA

    pay_rows: list[tuple[int, str, int, Any, str]] = []  # order_id, method, amount, ts, status
    methods = np.array(["card", "qr", "bank_transfer", "cod"])
    done = np.flatnonzero(status == "completed")
    mpick = rng.choice(4, size=n_ord, p=[0.4, 0.35, 0.15, 0.1])
    delay = rng.integers(60, 1800, n_ord).astype("timedelta64[s]")
    split = rng.random(n_ord) < (0.18 if adversarial else 0.0)
    failed_first = rng.random(n_ord) < (0.06 if adversarial else 0.0)
    for i in done:
        oid, amt, ts = i + 1, int(total[i]), order_ts[i]
        if failed_first[i]:
            pay_rows.append((oid, methods[mpick[i]], amt, ts + delay[i] // 2, "failed"))
        if split[i] and amt >= 2:
            p1 = amt * 6 // 10
            pay_rows.append((oid, methods[mpick[i]], p1, ts + delay[i], "captured"))
            pay_rows.append((oid, methods[(mpick[i] + 1) % 4], amt - p1, ts + delay[i] * 2, "captured"))
        else:
            pay_rows.append((oid, methods[mpick[i]], amt, ts + delay[i], "captured"))
    cancelled = np.flatnonzero(status == "cancelled")
    for i in cancelled[rng.random(cancelled.size) < 0.4]:
        pay_rows.append((i + 1, methods[mpick[i]], int(total[i]), order_ts[i] + delay[i], "failed"))
    pay_rows.sort(key=lambda r: (r[0], r[3]))
    payments = pd.DataFrame(pay_rows, columns=["order_id", "method", "amount", "paid_ts", "status"])
    payments.insert(0, "payment_id", np.arange(1, len(payments) + 1))

    line_done = status[order_of_line - 1] == "completed"
    ret_idx = np.flatnonzero(line_done & (rng.random(n_li) < 0.05))
    if adversarial and ret_idx.size:
        dup = ret_idx[rng.random(ret_idx.size) < 0.1]
        ret_idx = np.concatenate([ret_idx, dup])
    r_net = net[ret_idx]
    partial = rng.random(ret_idx.size) < 0.3
    returns = pd.DataFrame(
        {"return_id": np.arange(1, ret_idx.size + 1), "order_item_id": ret_idx + 1,
         "order_id": order_of_line[ret_idx],
         "return_ts": order_ts[order_of_line[ret_idx] - 1] + rng.integers(3, 31, ret_idx.size).astype("timedelta64[D]").astype("timedelta64[s]"),
         "reason": rng.choice(["damaged", "wrong_item", "not_needed", "late"], size=ret_idx.size),
         "refund_amount": np.where(partial, r_net // 2, r_net)}
    )

    mondays = INVENTORY_FIRST_MONDAY + (np.arange(INVENTORY_WEEKS) * 7).astype("timedelta64[D]")
    inv = pd.DataFrame(
        {"snapshot_date": np.repeat(mondays, N_PRODUCTS), "product_id": np.tile(np.arange(1, N_PRODUCTS + 1), INVENTORY_WEEKS),
         "on_hand": rng.integers(0, 500, INVENTORY_WEEKS * N_PRODUCTS),
         "reorder_level": np.tile(50 + (np.arange(N_PRODUCTS) * 7) % 101, INVENTORY_WEEKS)}
    )
    return {"customers": customers, "orders": orders, "order_items": items, "payments": payments,
            "returns": returns, "inventory_snapshots": inv}


_FACT_SQL = f"""
CREATE TABLE daily_fact_sales AS
WITH sales AS (
  SELECT CAST(o.order_ts + INTERVAL {BUSINESS_TZ_HOURS} HOUR AS DATE) AS sales_date, o.province_id,
         COUNT(*) AS order_count, SUM(o.total_amount) AS revenue
  FROM orders o WHERE o.status = 'completed' GROUP BY 1, 2
), units AS (
  SELECT CAST(o.order_ts + INTERVAL {BUSINESS_TZ_HOURS} HOUR AS DATE) AS sales_date, o.province_id,
         SUM(i.quantity) AS units
  FROM orders o JOIN order_items i ON i.order_id = o.order_id WHERE o.status = 'completed' GROUP BY 1, 2
)
SELECT c.date_key AS sales_date, p.province_id,
       CAST(COALESCE(s.order_count, 0) AS INTEGER) AS order_count,
       CAST(COALESCE(u.units, 0) AS INTEGER) AS units,
       CAST(COALESCE(s.revenue, 0) AS DECIMAL(14,2)) AS revenue
FROM calendar c CROSS JOIN provinces p
LEFT JOIN sales s ON s.sales_date = c.date_key AND s.province_id = p.province_id
LEFT JOIN units u ON u.sales_date = c.date_key AND u.province_id = p.province_id
WHERE c.date_key BETWEEN DATE '2024-01-01' AND DATE '2026-01-01'
"""


def _create_table(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame, spec: dict[str, Any]) -> None:
    cols = ", ".join(f"CAST({c['name']} AS {c['type']}) AS {c['name']}" for c in spec["columns"])
    con.register("_src", df)
    con.execute(f"CREATE TABLE {name} AS SELECT {cols} FROM _src")
    con.unregister("_src")


def build_snapshot(path: Path, seed: int, adversarial: bool, scale: float = 1.0) -> dict[str, Any]:
    """Build one snapshot file; returns row counts and an order-independent content digest."""
    schema = load_schema()
    rng = np.random.default_rng(seed)
    tables = _static_tables(scale)
    tables.update(_variable_tables(rng, adversarial, scale, tables["products"]))
    tmp = path.with_suffix(".building")
    for stale in (tmp, Path(str(tmp) + ".wal")):
        stale.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp), config={"memory_limit": "1GB", "threads": 2})
    try:
        for name, df in tables.items():
            _create_table(con, name, df, schema["tables"][name])
        con.execute(_FACT_SQL)
        counts, digests = {}, {}
        for name in schema["tables"]:
            counts[name] = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            digests[name] = con.execute(f"SELECT CAST(bit_xor(hash(t)) AS VARCHAR) FROM {name} t").fetchone()[0]
        con.execute("CHECKPOINT")
    finally:
        con.close()
    path.unlink(missing_ok=True)
    shutil.move(str(tmp), str(path))
    return {"rows": counts, "total_rows": sum(counts.values()), "digest": digests}


def ensure_snapshots(seed: int, scale: float = 1.0, directory: Path = WAREHOUSE_DIR) -> dict[str, Any]:
    """Build missing or stale snapshots; reuse files whose manifest entry matches seed and scale."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("builder_version") != BUILDER_VERSION or manifest.get("static_seed") != STATIC_SEED:
        manifest = {}
    manifest.setdefault("snapshots", {})
    manifest.update({"builder_version": BUILDER_VERSION, "static_seed": STATIC_SEED})
    for name, (offset, adversarial) in SNAPSHOTS.items():
        snap_seed = seed + offset
        file = directory / f"{name}.duckdb"
        entry = manifest["snapshots"].get(name)
        if file.exists() and entry and entry["seed"] == snap_seed and entry["scale"] == scale:
            continue
        info = build_snapshot(file, snap_seed, adversarial, scale)
        manifest["snapshots"][name] = {"seed": snap_seed, "scale": scale, "adversarial": adversarial,
                                       "file": file.name, **info}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


# --- result comparison -----------------------------------------------------------------------


def _norm_value(v: Any) -> Any:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), 4)
    if isinstance(v, datetime):
        # Midnight timestamps compare as dates: date_trunc on a DATE may return either type.
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [tuple(_norm_value(v) for v in r) for r in rows]


def _key(row: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(repr(v) for v in row)


def compare_results(ref: ExecResult, cand: ExecResult, ordered: bool) -> dict[str, Any]:
    """Row-multiset comparison (ordered when the request asks for a ranking); column names ignored."""
    a, b = normalize_rows(ref.rows), normalize_rows(cand.rows)
    ca, cb = Counter(map(_key, a)), Counter(map(_key, b))
    same_shape = len(ref.columns) == len(cand.columns)
    equal = same_shape and (list(map(_key, a)) == list(map(_key, b)) if ordered else ca == cb)
    denom = max(len(a), len(b))
    overlap = 1.0 if denom == 0 else sum((ca & cb).values()) / denom
    return {"equal": equal, "overlap": round(overlap, 4), "same_shape": same_shape,
            "n_rows": len(b), "n_cols": len(cand.columns), "ref_rows": len(a), "ref_cols": len(ref.columns)}


def _exec_summary(res: ExecResult) -> dict[str, Any]:
    return {"executed": res.ok, "error": res.error, "timed_out": res.timed_out,
            "ms": round(res.elapsed_ms, 2)}


class DuckDBBackend:
    name = "duckdb"
    runtime = RUNTIME_EMBEDDED

    def __init__(self, directory: Path = WAREHOUSE_DIR, timeout_s: float = 10.0):
        self.directory = directory
        self.timeout_s = timeout_s
        self.manifest: dict[str, Any] = {}
        self._exec: dict[str, ReadOnlyExecutor] = {}
        self._schema: dict[str, Any] | None = None

    def engine_version(self) -> str:
        con = duckdb.connect(":memory:")
        try:
            return str(con.execute("SELECT version()").fetchone()[0])
        finally:
            con.close()

    def setup(self) -> None:
        if not SCHEMA_PATH.exists():
            raise BackendUnavailable(f"missing {SCHEMA_PATH}")
        self._schema = load_schema()

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is None:
            self._schema = load_schema()
        return self._schema

    def load(self, seed_payload: Any) -> None:
        seed = int(seed_payload["seed"])
        scale = float(seed_payload.get("scale", 1.0))
        self.teardown()
        self.manifest = ensure_snapshots(seed, scale, self.directory)
        tables = list(self.schema["tables"])
        for name, entry in self.manifest["snapshots"].items():
            self._exec[name] = ReadOnlyExecutor(self.directory / entry["file"], tables, self.timeout_s)

    def _run_everywhere(self, sql: str | None) -> dict[str, ExecResult | None]:
        return {n: (ex.execute(sql) if sql else None) for n, ex in self._exec.items()}

    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        internal = spec.facts["_internal"]
        candidate: str = spec.facts["candidate_sql"]
        reference: str | None = internal.get("reference_sql")
        interps: list[str] = internal.get("interps") or []
        ordered = bool(internal.get("ordered"))
        with timed() as t:
            guard = check_sql(candidate, list(self.schema["tables"]))
            cand_runs = self._run_everywhere(candidate) if guard.allowed else {}
            ref_runs = self._run_everywhere(reference) if reference else {}
            interp_runs = [self._run_everywhere(s) for s in interps]
            snaps: dict[str, Any] = {}
            for name in self._exec:
                entry: dict[str, Any] = {}
                cres, rres = cand_runs.get(name), ref_runs.get(name)
                if rres is not None:
                    entry["reference"] = _exec_summary(rres)
                if cres is not None:
                    entry["candidate"] = _exec_summary(cres)
                if cres is not None and rres is not None and cres.ok and rres.ok:
                    entry.update(compare_results(rres, cres, ordered))
                if interps:
                    base = interp_runs[0][name]
                    entry["interps_ok"] = all(r[name] is not None and r[name].ok for r in interp_runs)
                    entry["interp_equal"] = [
                        compare_results(base, r[name], ordered)["equal"] if entry["interps_ok"] else None
                        for r in interp_runs[1:]
                    ]
                    entry["candidate_equal_interp"] = [
                        bool(cres is not None and cres.ok and entry["interps_ok"]
                             and compare_results(r[name], cres, ordered)["equal"])
                        for r in interp_runs
                    ]
                snaps[name] = entry
        tables = list(spec.facts["tables"])
        facts = {
            "tables": tables,
            "schema_excerpt": schema_excerpt(tables, self.schema),
            "candidate_sql": candidate,
            "_internal": {"guard": guard.as_dict(), "snapshots": snaps},
        }
        ref = {"backend": self.name, "snapshots": list(self._exec),
               "builder_version": self.manifest.get("builder_version"), "tables": tables}
        return Evidence(facts=facts, ref=ref, db_ms=t["ms"])

    def teardown(self) -> None:
        for ex in self._exec.values():
            ex.close()
        self._exec = {}


def make_backend(directory: Path = WAREHOUSE_DIR) -> DuckDBBackend:
    return DuckDBBackend(directory)
