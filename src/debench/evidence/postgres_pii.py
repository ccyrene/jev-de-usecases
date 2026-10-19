"""PostgreSQL + JSONB evidence backend for E05 (PII / sensitive data classification).

``retrieve`` reads a candidate field's versioned metadata from ``meta.column_catalog`` (and the
column comment for scalar columns), samples 2-5 rows of that field (JSONB paths included) and returns
them minimized and partially masked. Raw values never leave this module: masking happens before the
facts are built, so the provider-visible state cannot contain a full identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from debench.config import ROOT, Settings
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import BackendUnavailable, Evidence, timed
from debench.evidence.pg_runtime import PostgresRuntime

INIT_DIR = ROOT / "infra" / "init" / "e05_postgres"
BASE_COLUMNS = ("id", "created_at", "status", "row_version", "attrs")
MAX_NEIGHBORS = 6
SAMPLE_STRIDE = 997  # prime, coprime with every table size, so sampled row ids are distinct


def sample_row_ids(row_count: int, offset: int, n: int) -> list[int]:
    """Deterministic 1-based row ids to sample (shared by the DB query and the reference)."""
    return sorted({1 + (offset + k * SAMPLE_STRIDE) % row_count for k in range(n)})


# --- masking -------------------------------------------------------------------------------------

_ALNUM = re.compile(r"[A-Za-z0-9]")
_LONG_TEXT = 24


def _mask_alnum(token: str, head: int, tail: int) -> str:
    positions = [i for i, ch in enumerate(token) if _ALNUM.match(ch)]
    keep = set(positions[:head]) | set(positions[len(positions) - tail :] if tail else [])
    return "".join("*" if (i in positions and i not in keep) else ch for i, ch in enumerate(token))


def mask_token(token: str) -> str:
    """Keep at most ~40% of the alphanumerics (first/last 20%, <= 2 each); separators stay."""
    n = len(_ALNUM.findall(token))
    keep = min(2, n // 5)
    return _mask_alnum(token, keep, keep)


def mask_value(value: str) -> str:
    """Partially mask a sample value so it can never reveal a full identifier.

    Non-ASCII (Thai script) is masked completely because EN states may not contain Thai letters.
    Email domains stay (they are fixed example domains); long free text keeps one letter per word.
    """
    if not value.isascii():
        return re.sub(r"\S", "*", value)
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{mask_token(local)}@{domain}"
    if len(_ALNUM.findall(value)) > _LONG_TEXT:
        return " ".join(
            _mask_alnum(w, 1, 0) if len(_ALNUM.findall(w)) >= 3 else _mask_alnum(w, 0, 0) for w in value.split(" ")
        )
    return mask_token(value)


# --- seed payload ----------------------------------------------------------------------------------


@dataclass
class PiiSeed:
    columns: dict[str, list[tuple[str, str]]]  # "crm.customer" -> [(scalar field column, sql type)]
    rows: dict[str, tuple[list[str], list[tuple[Any, ...]]]]  # table -> (column names, rows)
    catalog: list[tuple[Any, ...]]  # meta.column_catalog rows
    comments: list[tuple[str, str]] = field(default_factory=list)  # ("crm.customer.email", text)
    table_comments: dict[str, str] = field(default_factory=dict)


# --- backend -------------------------------------------------------------------------------------


def _expr(spec_facts: dict[str, Any]) -> sql.Composable:
    """Value expression (as text) of the candidate field."""
    if spec_facts["json_path"]:
        return sql.SQL("attrs #>> {}").format(sql.Literal(list(spec_facts["json_path"])))
    return sql.SQL("{}::text").format(sql.Identifier(spec_facts["column"]))


class PostgresPiiBackend:
    name = "postgres"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self._rt = PostgresRuntime("E05")
        self.runtime = self._rt.runtime
        self._conn: psycopg.Connection | None = None

    # -- lifecycle ---------------------------------------------------------------------------
    def setup(self) -> None:
        try:
            uri = self._rt.start()
            self._conn = psycopg.connect(uri, autocommit=True)
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable(f"embedded PostgreSQL could not start: {type(exc).__name__}") from exc

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            raise BackendUnavailable("setup() has not been called")
        return self._conn

    def engine_version(self) -> str:
        return str(self.conn.execute("select version()").fetchone()[0])

    def teardown(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._rt.stop()

    # -- load ----------------------------------------------------------------------------------
    def load(self, seed_payload: PiiSeed) -> None:
        seed = seed_payload
        conn = self.conn
        for path in sorted(Path(INIT_DIR).glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        for qualified, cols in seed.columns.items():
            schema, table = qualified.split(".")
            for col, sqltype in cols:
                conn.execute(
                    sql.SQL("alter table {}.{} add column {} {}").format(
                        sql.Identifier(schema), sql.Identifier(table), sql.Identifier(col), sql.SQL(sqltype)
                    )
                )
        for qualified, (names, rows) in seed.rows.items():
            schema, table = qualified.split(".")
            stmt = sql.SQL("copy {}.{} ({}) from stdin").format(
                sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, names))
            )
            with conn.cursor() as cur, cur.copy(stmt) as copy:
                for row in rows:
                    copy.write_row(row)
        with conn.cursor() as cur:
            cur.executemany(
                "insert into meta.column_catalog (schema_name, table_name, column_path, version, purpose_en, "
                "purpose_th, lineage_en, lineage_th, steward, retention_days) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                seed.catalog,
            )
        for qualified, text in seed.comments:
            schema, table, col = qualified.split(".")
            conn.execute(
                sql.SQL("comment on column {}.{}.{} is {}").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Identifier(col), sql.Literal(text)
                )
            )
        for qualified, text in seed.table_comments.items():
            schema, table = qualified.split(".")
            conn.execute(
                sql.SQL("comment on table {}.{} is {}").format(
                    sql.Identifier(schema), sql.Identifier(table), sql.Literal(text)
                )
            )
        for qualified in seed.rows:
            conn.execute(sql.SQL("analyze {}.{}").format(*map(sql.Identifier, qualified.split("."))))

    # -- retrieve --------------------------------------------------------------------------------
    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        f = spec.facts
        schema, table, column = f["schema"], f["table"], f["column"]
        path = list(f["json_path"]) if f["json_path"] else None
        qualified = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
        column_path = ".".join([column, *path]) if path else column
        with timed() as t:
            conn = self.conn
            cat = conn.execute(
                "select purpose_en, purpose_th, lineage_en, lineage_th, steward, retention_days "
                "from meta.column_catalog where schema_name=%s and table_name=%s and column_path=%s and version=%s",
                (schema, table, column_path, f["meta_version"]),
            ).fetchone() or (None,) * 6
            comment = None
            if not path:
                row = conn.execute(
                    "select col_description(a.attrelid, a.attnum) from pg_attribute a "
                    "where a.attrelid = (%s || '.' || %s)::regclass and a.attname = %s",
                    (schema, table, column),
                ).fetchone()
                comment = row[0] if row else None
            row_count = conn.execute(sql.SQL("select count(*) from {}").format(qualified)).fetchone()[0]
            ids = sample_row_ids(row_count, f["offset"], f["n_samples"])
            expr = _expr(f)
            samples = [
                r[0]
                for r in conn.execute(
                    sql.SQL("select {} from {} where id = any(%s) order by id").format(expr, qualified), (ids,)
                )
            ]
            distinct = conn.execute(
                sql.SQL("select count(distinct {}) from {}").format(expr, qualified)
            ).fetchone()[0]
            if path:
                kinds = conn.execute(
                    sql.SQL("select distinct jsonb_typeof(attrs #> {}) from {} where id = any(%s)").format(
                        sql.Literal(path), qualified
                    ),
                    (ids,),
                ).fetchall()
                column_type = "jsonb:" + "|".join(sorted(k[0] for k in kinds))
                parent = sql.Literal(path[:-1])
                neighbors = [
                    r[0]
                    for r in conn.execute(
                        sql.SQL(
                            "select k from jsonb_object_keys((select attrs #> {} from {} where id = %s)) k "
                            'where k <> %s order by k collate "C" limit %s'
                        ).format(parent, qualified),
                        (ids[0], path[-1], MAX_NEIGHBORS),
                    )
                ]
            else:
                column_type = conn.execute(
                    "select data_type from information_schema.columns where table_schema=%s and table_name=%s "
                    "and column_name=%s",
                    (schema, table, column),
                ).fetchone()[0]
                neighbors = [
                    r[0]
                    for r in conn.execute(
                        "select column_name from information_schema.columns where table_schema=%s and table_name=%s "
                        "and column_name <> all(%s) order by ordinal_position limit %s",
                        (schema, table, [*BASE_COLUMNS, column], MAX_NEIGHBORS),
                    )
                ]
        facts = {
            "field_id": f["field_id"],
            "schema": schema,
            "table": table,
            "column": column,
            "json_path": path,
            "column_type": column_type,
            "catalog_version": f["meta_version"],
            "purpose_en": cat[0],
            "purpose_th": cat[1],
            "lineage_en": cat[2],
            "lineage_th": cat[3],
            "steward": cat[4],
            "retention_days": cat[5],
            "comment": comment,
            "row_count": row_count,
            "distinct_count": distinct,
            "neighbor_fields": neighbors,
            "sample_row_ids": ids,
            "samples_masked": [mask_value(v) for v in samples],
            "_internal": f["_internal"],
        }
        ref = {
            "backend": self.name,
            "runtime": self.runtime,
            "field": f["field_id"],
            "catalog_version": f["meta_version"],
            "row_ids": ids,
        }
        return Evidence(facts=facts, ref=ref, db_ms=t["ms"])
