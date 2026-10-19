"""The SQL guard blocks every unsafe pattern before execution and never changes a database file."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from debench import sql_guard
from debench.sql_guard import ReadOnlyExecutor, check_sql, execute_guarded, open_read_only

TABLES = ["orders", "customers"]

# (id, sql) - each must be rejected by the guard and must never reach DuckDB.
UNSAFE: list[tuple[str, str]] = [
    ("delete", "DELETE FROM orders WHERE status = 'cancelled'"),
    ("update", "UPDATE orders SET status = 'completed'"),
    ("insert", "INSERT INTO orders SELECT * FROM orders"),
    ("insert_values", "INSERT INTO orders VALUES (1, 1, 'x')"),
    ("merge", ("MERGE INTO orders USING customers ON orders.customer_id = customers.customer_id "
               "WHEN MATCHED THEN DELETE")),
    ("drop_table", "DROP TABLE orders"),
    ("drop_if_exists", "DROP TABLE IF EXISTS customers"),
    ("alter", "ALTER TABLE orders ADD COLUMN note VARCHAR"),
    ("create_table", "CREATE TABLE t AS SELECT * FROM orders"),
    ("create_view", "CREATE VIEW v AS SELECT * FROM orders"),
    ("create_macro", "CREATE MACRO m() AS 1"),
    ("truncate", "TRUNCATE orders"),
    ("attach", "ATTACH 'other.duckdb' AS other"),
    ("detach", "DETACH other"),
    ("copy_to", "COPY (SELECT * FROM orders) TO '/tmp/out.csv'"),
    ("copy_from", "COPY orders FROM '/tmp/in.csv'"),
    ("export", "EXPORT DATABASE '/tmp/dump'"),
    ("import", "IMPORT DATABASE '/tmp/dump'"),
    ("install", "INSTALL httpfs"),
    ("load", "LOAD httpfs"),
    ("pragma", "PRAGMA database_list"),
    ("pragma_call", "PRAGMA table_info('orders')"),
    ("set", "SET enable_external_access = true"),
    ("set_variable", "SET VARIABLE x = 1"),
    ("reset", "RESET threads"),
    ("call", "CALL duckdb_tables()"),
    ("begin", "BEGIN TRANSACTION"),
    ("commit", "COMMIT"),
    ("rollback", "ROLLBACK"),
    ("use", "USE other"),
    ("checkpoint", "CHECKPOINT"),
    ("vacuum", "VACUUM"),
    ("explain", "EXPLAIN SELECT * FROM orders"),
    ("describe", "DESCRIBE orders"),
    ("show", "SHOW TABLES"),
    ("summarize", "SUMMARIZE orders"),
    ("select_into", "SELECT * INTO copy_of_orders FROM orders"),
    ("for_update", "SELECT * FROM orders FOR UPDATE"),
    ("stacked_drop", "SELECT 1; DROP TABLE orders"),
    ("stacked_select", "SELECT 1; SELECT 2"),
    ("stacked_load", "INSTALL httpfs; LOAD httpfs; SELECT 1"),
    ("transaction_wrap", "BEGIN; SELECT * FROM orders; COMMIT"),
    ("cte_delete", "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x"),
    ("read_csv", "SELECT * FROM read_csv('/etc/hostname')"),
    ("read_csv_auto", "SELECT * FROM read_csv_auto('data.csv')"),
    ("read_parquet", "SELECT * FROM read_parquet('data.parquet')"),
    ("read_parquet_s3", "SELECT * FROM read_parquet('s3://bucket/key.parquet')"),
    ("read_json_http", "SELECT * FROM read_json('https://example.org/a.json')"),
    ("read_text", "SELECT * FROM read_text('/etc/passwd')"),
    ("read_blob", "SELECT * FROM read_blob('/etc/passwd')"),
    ("read_in_subquery", "SELECT * FROM orders WHERE order_id IN (SELECT i FROM read_csv('x.csv'))"),
    ("read_in_cte", "WITH t AS (SELECT * FROM read_parquet('a.parquet')) SELECT * FROM t"),
    ("glob", "SELECT * FROM glob('/home/*')"),
    ("path_table", "SELECT * FROM 'data.csv'"),
    ("quoted_path_table", 'SELECT * FROM "/etc/passwd"'),
    ("url_table", "SELECT * FROM 'https://example.org/x.parquet'"),
    ("getenv", "SELECT getenv('HOME')"),
    ("query_fn", "SELECT * FROM query('SELECT 1')"),
    ("current_setting", "SELECT current_setting('enable_external_access')"),
    ("duckdb_settings", "SELECT * FROM duckdb_settings()"),
    ("duckdb_extensions", "SELECT * FROM duckdb_extensions()"),
    ("information_schema", "SELECT * FROM information_schema.tables"),
    ("pg_catalog", "SELECT * FROM pg_catalog.pg_tables"),
    ("qualified_other_db", "SELECT * FROM other.main.orders"),
    ("unknown_table", "SELECT * FROM secrets"),
    ("parse_error", "SELEC * FROM orders"),
    ("parse_error_unbalanced", "SELECT (1"),
    ("empty", ""),
    ("only_semicolon", ";"),
    ("only_comment", "-- nothing here"),
]


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders AS SELECT i AS order_id, i % 3 AS customer_id, 'completed' AS status "
                "FROM range(1, 7) t(i)")
    con.execute("CREATE TABLE customers AS SELECT i AS customer_id FROM range(0, 3) t(i)")
    con.execute("CHECKPOINT")
    con.close()
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("sql", [s for _, s in UNSAFE], ids=[i for i, _ in UNSAFE])
def test_guard_blocks_unsafe_statement(sql: str) -> None:
    result = check_sql(sql, TABLES)
    assert not result.allowed, f"guard allowed: {sql}"
    assert result.reasons
    # The same verdict without a table allowlist for everything that is not merely an unknown name.
    if "secrets" not in sql:
        assert not check_sql(sql).allowed


@pytest.mark.parametrize("sql", [s for _, s in UNSAFE], ids=[i for i, _ in UNSAFE])
def test_unsafe_never_reaches_duckdb_and_file_unchanged(
    sql: str, db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unsafe statement reached the executor")

    monkeypatch.setattr(sql_guard, "_run_query", forbidden)
    monkeypatch.setattr(sql_guard.duckdb, "connect", forbidden)
    before = _sha(db_file)
    result = ReadOnlyExecutor(db_file, TABLES).execute(sql)
    assert result.blocked and not result.executed and result.rows == []
    assert execute_guarded(sql, db_file, TABLES).blocked
    assert _sha(db_file) == before


def test_unsafe_block_catalogue_is_complete() -> None:
    needed = {"DELETE", "UPDATE", "INSERT", "MERGE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "COPY",
              "EXPORT", "IMPORT", "INSTALL", "LOAD", "PRAGMA", "SET", "CALL", "BEGIN", "COMMIT", "ROLLBACK",
              "READ_CSV", "READ_PARQUET", "READ_JSON", "GLOB", "READ_TEXT", "READ_BLOB"}
    text = " ".join(s.upper() for _, s in UNSAFE)
    assert {w for w in needed if w not in text} == set()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT COUNT(*) FROM orders",
        "select * from orders o join customers c on c.customer_id = o.customer_id",
        "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
        "(SELECT 1) UNION ALL (SELECT 2)",
        "FROM orders SELECT order_id",
        "SELECT * FROM range(3)",
        "SELECT * FROM main.orders",
        "SELECT order_id /* DROP TABLE orders; DELETE FROM x */ FROM orders",
        "SELECT 'DROP TABLE orders' AS note, 'read_csv' AS fn FROM orders",
        "SELECT order_id AS update_count, status AS copy_flag FROM orders",
        "SELECT 1;",
    ],
)
def test_guard_allows_plain_reads(sql: str) -> None:
    assert check_sql(sql, TABLES).allowed


def test_guard_reports_category_and_tables() -> None:
    assert check_sql("SELEC 1").category == sql_guard.PARSE_ERROR
    assert check_sql("SELECT 1; SELECT 2").category == sql_guard.MULTI_STATEMENT
    assert check_sql("DROP TABLE a").category == sql_guard.FORBIDDEN_NODE
    assert check_sql("SELECT * FROM read_csv('a')").category == sql_guard.FORBIDDEN_FUNCTION
    assert check_sql("SELECT * FROM nope", TABLES).category == sql_guard.FORBIDDEN_TABLE
    ok = check_sql("WITH t AS (SELECT * FROM orders) SELECT * FROM t JOIN customers c ON TRUE", TABLES)
    assert ok.allowed and set(ok.tables) == {"orders", "customers"}


def test_safe_query_executes_read_only(db_file: Path) -> None:
    before = _sha(db_file)
    result = execute_guarded("SELECT COUNT(*) AS n, SUM(order_id) AS s FROM orders", db_file, TABLES)
    assert result.ok and result.columns == ["n", "s"] and result.rows == [(6, 21)]
    assert _sha(db_file) == before


def test_connection_is_read_only_without_external_access(db_file: Path) -> None:
    con = open_read_only(db_file)
    try:
        assert con.execute("SELECT current_setting('enable_external_access')").fetchone() == (False,)
        assert con.execute("SELECT current_setting('lock_configuration')").fetchone() == (True,)
        flags = dict(con.execute("SELECT database_name, readonly FROM duckdb_databases()").fetchall())
        assert flags["tiny"] is True
    finally:
        con.close()


def test_row_cap_marks_truncation(db_file: Path) -> None:
    result = execute_guarded("SELECT * FROM range(1000)", db_file, TABLES, max_rows=10)
    assert result.ok and len(result.rows) == 10 and result.truncated


def test_runtime_errors_are_reported_not_raised(db_file: Path) -> None:
    result = execute_guarded("SELECT missing_column FROM orders", db_file, TABLES)
    assert not result.blocked and not result.executed and result.error and not result.timed_out


def test_timeout_watchdog_interrupts_runaway_query(db_file: Path) -> None:
    sql = "SELECT COUNT(*) FROM range(100000000000) a, range(100000000000) b"
    assert check_sql(sql, TABLES).allowed
    result = execute_guarded(sql, db_file, TABLES, timeout_s=0.5)
    assert result.timed_out and result.error == "timeout" and result.elapsed_ms < 15_000


def test_executor_reuses_one_connection_and_survives_blocked_calls(db_file: Path) -> None:
    with ReadOnlyExecutor(db_file, TABLES) as ex:
        assert ex.execute("SELECT 1").ok
        assert ex.execute("DROP TABLE orders").blocked
        assert ex.execute("SELECT COUNT(*) FROM orders").rows == [(6,)]
