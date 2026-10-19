"""E06 warehouse snapshots and the full-oracle agreement over all 300 specs (embedded DuckDB)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from debench import SEED
from debench.dataset import e06
from debench.evidence import duckdb_sql as d

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    directory = tmp_path_factory.mktemp("e06_snapshots")
    return directory, d.ensure_snapshots(SEED, 1.0, directory)


def _q(directory: Path, snap: str, sql: str):
    con = duckdb.connect(str(directory / f"{snap}.duckdb"), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_snapshots_have_twelve_tables_and_enough_rows(warehouse) -> None:
    _, manifest = warehouse
    schema = d.load_schema()
    assert len(schema["tables"]) == 12
    assert set(manifest["snapshots"]) == set(d.SNAPSHOTS) and len(d.SNAPSHOTS) >= 3
    for name, info in manifest["snapshots"].items():
        assert set(info["rows"]) == set(schema["tables"])
        assert info["total_rows"] >= 100_000, name
        assert all(n > 0 for n in info["rows"].values())


def test_snapshots_are_independently_seeded(warehouse) -> None:
    _, manifest = warehouse
    seeds = [e["seed"] for e in manifest["snapshots"].values()]
    assert len(set(seeds)) == len(seeds)
    digests = {n: e["digest"]["orders"] for n, e in manifest["snapshots"].items()}
    assert len(set(digests.values())) == len(digests)
    # Reference tables are shared by design.
    assert len({e["digest"]["provinces"] for e in manifest["snapshots"].values()}) == 1


def test_primary_and_foreign_keys_hold(warehouse) -> None:
    directory, _ = warehouse
    schema = d.load_schema()
    for snap in d.SNAPSHOTS:
        for table, t in schema["tables"].items():
            pk = ", ".join(t["primary_key"])
            dup = _q(directory, snap, f"SELECT COUNT(*) FROM (SELECT {pk} FROM {table} GROUP BY {pk} HAVING COUNT(*) > 1)")
            assert dup == [(0,)], (snap, table, "pk")
            for fk in t["foreign_keys"]:
                cols, ref = fk["columns"], fk["references"]
                join = " AND ".join(f"c.{a} = p.{b}" for a, b in zip(cols, ref["columns"]))
                not_null = " AND ".join(f"c.{a} IS NOT NULL" for a in cols)
                orphan = _q(directory, snap,
                            f"SELECT COUNT(*) FROM {table} c WHERE {not_null} AND NOT EXISTS "
                            f"(SELECT 1 FROM {ref['table']} p WHERE {join})")
                assert orphan == [(0,)], (snap, table, cols)


def test_order_totals_and_fact_table_are_consistent(warehouse) -> None:
    directory, _ = warehouse
    for snap in d.SNAPSHOTS:
        assert _q(directory, snap, "SELECT COUNT(*) FROM orders o WHERE o.total_amount != (SELECT SUM(i.quantity * "
                  "i.unit_price - COALESCE(i.discount, 0)) FROM order_items i WHERE i.order_id = o.order_id)") == [(0,)]
        fact, orders = _q(directory, snap, "SELECT (SELECT SUM(revenue) FROM daily_fact_sales), "
                          "(SELECT SUM(total_amount) FROM orders WHERE status = 'completed')")[0]
        assert fact == orders


def test_base_snapshot_hides_what_adversarial_snapshots_expose(warehouse) -> None:
    directory, _ = warehouse
    probes = {
        "late_utc_orders": "SELECT COUNT(*) FROM orders WHERE hour(order_ts) >= 17",
        "test_accounts": "SELECT COUNT(*) FROM customers WHERE is_test",
        "customers_without_orders": "SELECT COUNT(*) FROM customers c WHERE NOT EXISTS "
                                    "(SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)",
        "null_discounts": "SELECT COUNT(*) FROM order_items WHERE discount IS NULL",
        "multi_captured": "SELECT COUNT(*) FROM (SELECT order_id FROM payments WHERE status = 'captured' "
                          "GROUP BY order_id HAVING COUNT(*) > 1)",
        "failed_on_completed": "SELECT COUNT(*) FROM payments p JOIN orders o USING (order_id) "
                               "WHERE o.status = 'completed' AND p.status = 'failed'",
        "duplicate_returns": "SELECT COUNT(*) FROM (SELECT order_item_id FROM returns GROUP BY 1 HAVING COUNT(*) > 1)",
    }
    for name, sql in probes.items():
        assert _q(directory, "base", sql) == [(0,)], f"base should not expose {name}"
        for snap in d.SNAPSHOTS:
            if snap != "base":
                assert _q(directory, snap, sql)[0][0] > 0, f"{snap} must expose {name}"


def test_same_seed_rebuilds_identical_content(tmp_path: Path) -> None:
    a = d.build_snapshot(tmp_path / "a.duckdb", SEED + 1, True, scale=0.05)
    b = d.build_snapshot(tmp_path / "b.duckdb", SEED + 1, True, scale=0.05)
    c = d.build_snapshot(tmp_path / "c.duckdb", SEED + 2, True, scale=0.05)
    assert a["digest"] == b["digest"] and a["rows"] == b["rows"]
    assert a["digest"]["orders"] != c["digest"]["orders"]


def test_full_oracle_agrees_with_gold_for_all_specs(warehouse) -> None:
    directory, _ = warehouse
    specs = e06.generate_specs(SEED)
    backend = d.DuckDBBackend(directory)
    backend.setup()
    backend.load({"seed": SEED, "scale": 1.0})
    try:
        assert backend.engine_version().startswith("v")
        failures, times = {}, []
        for spec in specs:
            ev = backend.retrieve(spec)
            times.append(ev.db_ms)
            problems = e06.oracle_check(spec, ev.facts)
            if problems:
                failures[spec.scenario_id] = problems
            # The provider-visible state never carries execution results.
            state = e06.render_state(spec, ev.facts, "en", "common_fit")
            assert list(state) == ["request", "schema", "candidate_sql", "constraints"]
            assert "_internal" not in str(state) and "overlap" not in str(state)
        assert failures == {}
        assert sorted(times)[len(times) // 2] < 2000
    finally:
        backend.teardown()
