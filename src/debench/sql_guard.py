"""Deterministic SQL safety layer: the final authority on whether a candidate may be executed.

A statement is allowed only if sqlglot (DuckDB dialect) parses it as exactly one read query
(``SELECT``, ``WITH ... SELECT`` or a set operation of selects). Parse errors, multiple statements,
any DDL/DML/session/transaction/extension node, file or URL table functions, table names outside
the allowlist and environment/introspection functions are all unsafe. Model opinion never overrides
this verdict.

Execution is defence in depth: the guard runs first, then the query runs on a read-only DuckDB
connection with external access disabled, a locked configuration, a row cap and a watchdog thread
that interrupts the connection on timeout. Blocked statements never reach a connection.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "duckdb"
# sqlglot warns on every statement it can only parse as a generic command; the guard blocks those anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_ROWS = 50_000

# Guard categories (stable ids; used by the evidence layer and the rules baseline).
OK = "ok"
PARSE_ERROR = "parse_error"
EMPTY = "empty_statement"
MULTI_STATEMENT = "multiple_statements"
NOT_SELECT = "not_a_select"
FORBIDDEN_NODE = "forbidden_statement"
FORBIDDEN_FUNCTION = "forbidden_function"
FORBIDDEN_TABLE = "forbidden_table"

_BANNED_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterColumn", "AlterRename",
    "Attach", "Detach", "Copy", "Install", "Pragma", "Set", "SetItem", "Command", "Transaction",
    "Commit", "Rollback", "Use", "Describe", "Show", "Summarize", "TruncateTable", "Into", "Lock",
    "Analyze", "Cache", "Uncache", "Refresh", "LoadData", "Grant", "Revoke", "Kill", "Declare",
    "Export", "Import", "Call", "Comment", "AddConstraint", "Execute",
)
_BANNED_NODES: tuple[type[exp.Expression], ...] = tuple(
    cls for name in _BANNED_NODE_NAMES if isinstance(cls := getattr(exp, name, None), type)
)
_ROOT_TYPES = (exp.Select, exp.SetOperation, exp.Subquery)
_STATEMENT_KEYWORDS = re.compile(
    r"\b(?:attach|detach|copy|export|import|install|load|pragma|call|checkpoint|vacuum|set|reset|begin|"
    r"commit|rollback|use|explain)\b",
    re.IGNORECASE,
)

# Table functions that cannot touch files or the network.
_ALLOWED_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})
_BLOCKED_FUNCTION_NAMES = frozenset(
    {
        "getenv", "query", "query_table", "glob", "sniff_csv", "which_secret", "current_setting",
        "json_execute_serialized_sql", "read_blob", "read_text", "load_extension", "install_extension",
        "force_install", "getvariable", "setseed_external", "excel_text_fn",
    }
)
_BLOCKED_FUNCTION_PREFIXES = ("read_", "scan_", "parquet_", "duckdb_", "pragma_", "sqlite_", "iceberg_", "delta_")
_PATHLIKE = re.compile(r"[/\\*:]|\.(?:csv|tsv|parquet|json|jsonl|ndjson|txt|db|duckdb|sqlite|xlsx)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    category: str
    reasons: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "category": self.category, "reasons": list(self.reasons)}


def _block(category: str, *reasons: str) -> GuardResult:
    return GuardResult(False, category, tuple(reasons))


def _function_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    if type(node).__name__.startswith("Read") and isinstance(node, exp.Func):
        return type(node).__name__.lower()
    return None


def _function_blocked(name: str) -> bool:
    return name in _BLOCKED_FUNCTION_NAMES or name.startswith(_BLOCKED_FUNCTION_PREFIXES) or name.startswith("read")


def _cte_names(tree: exp.Expression) -> set[str]:
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


def _check_tables(
    tree: exp.Expression, allowed_tables: Collection[str] | None
) -> tuple[list[str], tuple[str, ...]]:
    problems: list[str] = []
    seen: list[str] = []
    ctes = _cte_names(tree)
    allowed = {t.lower() for t in allowed_tables} if allowed_tables is not None else None
    for tbl in tree.find_all(exp.Table):
        source = tbl.this
        if not isinstance(source, exp.Identifier):
            fname = (_function_name(source) or getattr(source, "key", "") or "").lower()
            if isinstance(source, (exp.Unnest, exp.GenerateSeries)) or fname in _ALLOWED_TABLE_FUNCTIONS:
                continue
            problems.append(f"table function {fname or source.sql(DIALECT)[:40]!r} is not allowed")
            continue
        name = str(source.name)
        if tbl.args.get("catalog") or (tbl.args.get("db") and str(tbl.args["db"].name).lower() != "main"):
            problems.append(f"qualified table {tbl.sql(DIALECT)!r} is not allowed")
            continue
        low = name.lower()
        if low in ctes:
            continue
        seen.append(low)
        if allowed is not None and low not in allowed:
            problems.append(f"table {name!r} is not in the allowlist")
        elif allowed is None and _PATHLIKE.search(name):
            problems.append(f"path-like table {name!r} is not allowed")
    return problems, tuple(dict.fromkeys(seen))


def check_sql(sql: str, allowed_tables: Collection[str] | None = None) -> GuardResult:
    """Classify one candidate statement. Pure, no database access."""
    if not isinstance(sql, str) or not sql.strip().strip(";").strip():
        return _block(EMPTY, "empty statement")
    try:
        statements = sqlglot.parse(sql, read=DIALECT, error_level=sqlglot.ErrorLevel.RAISE)
    except (SqlglotError, ValueError, RecursionError) as exc:
        return _block(PARSE_ERROR, f"unparseable: {type(exc).__name__}")
    statements = [s for s in statements if s is not None]
    if not statements:
        return _block(EMPTY, "no statement")
    if len(statements) != 1:
        return _block(MULTI_STATEMENT, f"{len(statements)} statements")
    tree = statements[0]
    banned = sorted({type(n).__name__ for n in tree.walk() if isinstance(n, _BANNED_NODES)})
    if banned:
        return _block(FORBIDDEN_NODE, *(f"{n} is not allowed" for n in banned))
    if not isinstance(tree, _ROOT_TYPES):
        return _block(NOT_SELECT, f"{type(tree).__name__} is not a SELECT")
    if isinstance(tree, exp.Subquery) and not isinstance(tree.unnest(), (exp.Select, exp.SetOperation)):
        return _block(NOT_SELECT, "parenthesised statement is not a SELECT")
    fn_problems = []
    for node in tree.walk():
        fname = _function_name(node)
        if fname is not None and _function_blocked(fname):
            fn_problems.append(f"function {fname}() is not allowed")
    if fn_problems:
        return _block(FORBIDDEN_FUNCTION, *sorted(set(fn_problems)))
    table_problems, tables = _check_tables(tree, allowed_tables)
    if table_problems:
        return _block(FORBIDDEN_TABLE, *table_problems)
    return GuardResult(True, OK, (), tables)


def mentions_statement_keyword(sql: str) -> bool:
    """Crude lexical hint (comments and string literals included); never used as the guard."""
    return bool(_STATEMENT_KEYWORDS.search(sql or ""))


# --- execution -------------------------------------------------------------------------------

READ_ONLY_CONFIG: dict[str, Any] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_community_extensions": False,
    "allow_unsigned_extensions": False,
    "lock_configuration": True,
    "threads": 2,
    "memory_limit": "1GB",
}


def open_read_only(path: str | Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True, config=dict(READ_ONLY_CONFIG))


@dataclass
class ExecResult:
    guard: GuardResult
    executed: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    error: str | None = None
    timed_out: bool = False
    truncated: bool = False
    elapsed_ms: float = 0.0

    @property
    def blocked(self) -> bool:
        return not self.guard.allowed

    @property
    def ok(self) -> bool:
        return self.executed and self.error is None


def _run_query(con: duckdb.DuckDBPyConnection, sql: str, timeout_s: float, max_rows: int) -> ExecResult:
    """Run an already-vetted query with a watchdog; the only place SQL reaches DuckDB."""
    result = ExecResult(guard=GuardResult(True, OK))
    fired = threading.Event()

    def _interrupt() -> None:
        fired.set()
        con.interrupt()

    timer = threading.Timer(timeout_s, _interrupt)
    start = time.perf_counter()
    timer.start()
    try:
        cur = con.execute(sql)
        result.columns = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(max_rows + 1)
        result.truncated = len(rows) > max_rows
        result.rows = rows[:max_rows]
        result.executed = True
    except duckdb.Error as exc:
        result.timed_out = fired.is_set()
        result.error = "timeout" if result.timed_out else f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    finally:
        timer.cancel()
        result.elapsed_ms = (time.perf_counter() - start) * 1000.0
    return result


class ReadOnlyExecutor:
    """Guard-then-execute against one read-only database file. Connections open lazily."""

    def __init__(
        self,
        path: str | Path,
        allowed_tables: Sequence[str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_rows: int = DEFAULT_MAX_ROWS,
    ):
        self.path = Path(path)
        self.allowed_tables = list(allowed_tables) if allowed_tables is not None else None
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self._con: duckdb.DuckDBPyConnection | None = None

    def execute(self, sql: str, timeout_s: float | None = None) -> ExecResult:
        guard = check_sql(sql, self.allowed_tables)
        if not guard.allowed:
            return ExecResult(guard=guard)
        if self._con is None:
            self._con = open_read_only(self.path)
        out = _run_query(self._con, sql, timeout_s or self.timeout_s, self.max_rows)
        out.guard = guard
        return out

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def execute_guarded(
    sql: str,
    path: str | Path,
    allowed_tables: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> ExecResult:
    with ReadOnlyExecutor(path, allowed_tables, timeout_s, max_rows) as ex:
        return ex.execute(sql)
