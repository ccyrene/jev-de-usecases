"""ClickHouse evidence backend (E02) on chdb, the embedded ClickHouse engine.

The DDL in ``infra/init/e02_clickhouse`` is plain ClickHouse SQL and also runs on a server. The
backend returns raw per-window aggregates only; thresholds and policy live in ``debench.dataset.e02``.
A persistent session directory under ``~/.cache/de-jev/clickhouse`` is wiped on every load.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from debench.config import ROOT
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import RUNTIME_EMBEDDED, BackendUnavailable, Evidence, timed

CACHE_DIR = Path(os.environ.get("DEBENCH_CACHE", "~/.cache/de-jev")).expanduser()
INIT_DIR = ROOT / "infra" / "init" / "e02_clickhouse"
TABLES = (
    "pipeline_events",
    "pipeline_events_raw",
    "dataset_catalog",
    "business_calendar",
    "dq_window_measurements",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_]+$")
_SAFE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")
BASELINE_DAYS = 14


def split_sql(text: str) -> list[str]:
    """Split a .sql file into statements; ``--`` comment lines are dropped."""
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _num(value: Any, integer: bool = False) -> Any:
    if value is None:
        return None
    x = float(value)
    if math.isnan(x) or math.isinf(x):
        return None
    return int(x) if integer else x


class ClickHouseBackend:
    name = "clickhouse"
    runtime = RUNTIME_EMBEDDED

    def __init__(self, case: str = "e02", init_dir: Path = INIT_DIR, session_dir: Path | None = None):
        self.case = case.lower()
        self.init_dir = init_dir
        self.session_dir = session_dir or CACHE_DIR / "clickhouse" / self.case
        self._session: Any = None

    # -- lifecycle -------------------------------------------------------------------------------
    def setup(self) -> None:
        try:
            from chdb import session as chdb_session
        except ImportError as exc:  # pragma: no cover - optional extra
            raise BackendUnavailable("chdb not installed (uv sync --extra db)") from exc
        self._chdb_session = chdb_session
        self._open(fresh=True)

    def _open(self, fresh: bool) -> None:
        self._close()
        if fresh:
            shutil.rmtree(self.session_dir, ignore_errors=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._session = self._chdb_session.Session(str(self.session_dir / "data"))
        except Exception as exc:  # pragma: no cover
            raise BackendUnavailable(f"chdb session failed to start: {type(exc).__name__}") from exc

    def _close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def teardown(self) -> None:
        self._close()

    def engine_version(self) -> str:
        return str(self.rows("SELECT version() AS v")[0]["v"])

    # -- SQL helpers -----------------------------------------------------------------------------
    def execute(self, sql: str) -> None:
        self._session.query(sql, fmt="CSV")

    def rows(self, sql: str) -> list[dict[str, Any]]:
        out = self._session.query(sql, fmt="JSONEachRow")
        text = out.bytes().decode("utf-8") if hasattr(out, "bytes") else str(out)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def run_sql_file(self, name: str) -> None:
        for stmt in split_sql((self.init_dir / name).read_text(encoding="utf-8")):
            self.execute(stmt)

    # -- loading ---------------------------------------------------------------------------------
    def load(self, seed_payload: dict[str, Any]) -> None:
        """Reset all tables, run the DDL, bulk-insert each frame, then compute window measurements.

        ``seed_payload`` maps table name to a pandas frame whose columns follow the table order; time
        columns are ISO strings so the engine time zone cannot shift them.
        """
        self._open(fresh=True)
        for ddl in sorted(self.init_dir.glob("0[1-5]_*.sql")):
            for stmt in split_sql(ddl.read_text(encoding="utf-8")):
                self.execute(stmt)
        stage = self.session_dir / "stage"
        stage.mkdir(exist_ok=True)
        for table in TABLES[:4]:
            frame = seed_payload.get(table)
            if frame is None or len(frame) == 0:
                continue
            path = stage / f"{table}.parquet"
            frame.to_parquet(path, index=False)
            self.execute(f"INSERT INTO {table} SELECT * FROM file('{path}', 'Parquet')")
            path.unlink()
        self.run_sql_file("06_fill_measurements.sql")

    def count(self, table: str) -> int:
        if table not in TABLES:
            raise ValueError(table)
        return int(self.rows(f"SELECT count() AS n FROM {table}")[0]["n"])

    # -- fixed retrieval template ----------------------------------------------------------------
    def window_facts(self, pipeline_id: str, dataset_id: str, day: str, as_of: str) -> dict[str, Any]:
        for ident in (pipeline_id, dataset_id):
            if not _SAFE_ID.match(ident):
                raise ValueError(f"unsafe identifier {ident!r}")
        if not (_SAFE_TS.match(day) and _SAFE_TS.match(as_of)):
            raise ValueError("unsafe timestamp")
        key = f"pipeline_id = '{pipeline_id}' AND dataset_id = '{dataset_id}'"
        start = f"toDateTime('{day} 00:00:00')"
        in_window = f"event_time >= {start} AND event_time < {start} + INTERVAL 1 DAY"
        win = self.rows(
            f"""SELECT count() AS n_rows, uniqExact(record_id) AS uniq_records,
                 countIf(customer_id IS NULL) AS null_count,
                 min(amount) AS amount_min, max(amount) AS amount_max, avg(amount) AS amount_mean,
                 stddevPop(amount) AS amount_sd, quantileExactInclusive(0.95)(amount) AS amount_p95,
                 toString(max(event_time)) AS last_event,
                 dateDiff('second', max(event_time), toDateTime('{as_of}')) AS lag_s,
                 uniqExact(toHour(event_time)) AS active_hours
               FROM pipeline_events WHERE {key} AND {in_window}"""
        )[0]
        batches = self.rows(
            f"""SELECT batch_id, count() AS n_rows, uniqExact(record_id) AS uniq_records,
                 countIf(customer_id IS NULL) AS null_count, avg(amount) AS amount_mean
               FROM pipeline_events WHERE {key} AND {in_window} GROUP BY batch_id ORDER BY batch_id"""
        )
        raw = self.rows(
            f"""SELECT batch_id, count() AS raw_rows,
                 countIf(toFloat64OrNull(amount_raw) IS NULL) AS mismatch_count
               FROM pipeline_events_raw WHERE {key} AND {in_window} GROUP BY batch_id ORDER BY batch_id"""
        )
        weekday = f"toDayOfWeek(window_day) = toDayOfWeek(toDate('{day}'))"
        base_range = f"window_day >= toDate('{day}') - {BASELINE_DAYS} AND window_day < toDate('{day}')"
        base = self.rows(
            f"""SELECT count() AS history_days, avg(row_count) AS mean_rows,
                 countIf({weekday}) AS same_weekday_days, avgIf(row_count, {weekday}) AS same_weekday_mean_rows,
                 sum(null_count) / sum(row_count) AS null_rate, sum(amount_sum) / sum(row_count) AS amount_mean,
                 avg(amount_sd) AS amount_sd, max(amount_max) AS amount_max, avg(amount_p95) AS amount_p95
               FROM dq_window_measurements WHERE {key} AND {base_range}"""
        )[0]
        history = self.rows(
            f"""SELECT toString(window_day) AS d, row_count FROM dq_window_measurements
               WHERE {key} AND {base_range} ORDER BY window_day"""
        )
        catalog = self.rows(
            f"""SELECT tier, freshness_sla_min, completeness_sla, downstream_critical, dependent_jobs, ops_note
               FROM dataset_catalog WHERE {key}"""
        )
        calendar = self.rows(
            f"""SELECT is_holiday, expected_factor FROM business_calendar
               WHERE pipeline_id = '{pipeline_id}' AND day = toDate('{day}')"""
        )
        ints = ("n_rows", "uniq_records", "null_count", "lag_s", "active_hours")
        window = {k: (_num(v, k in ints) if k != "last_event" else v) for k, v in win.items()}
        raw_by_batch = {r["batch_id"]: r for r in raw}
        typed_by_batch = {b["batch_id"]: b for b in batches}
        merged = []
        for bid in sorted(set(raw_by_batch) | set(typed_by_batch)):
            t, r = typed_by_batch.get(bid), raw_by_batch.get(bid)
            merged.append(
                {
                    "batch_id": bid,
                    "n_rows": int(t["n_rows"]) if t else 0,
                    "uniq_records": int(t["uniq_records"]) if t else 0,
                    "null_count": int(t["null_count"]) if t else 0,
                    "amount_mean": _num(t["amount_mean"]) if t else None,
                    "raw_rows": int(r["raw_rows"]) if r else 0,
                    "mismatch_count": int(r["mismatch_count"]) if r else 0,
                }
            )
        window["raw_rows"] = sum(b["raw_rows"] for b in merged)
        window["mismatch_count"] = sum(b["mismatch_count"] for b in merged)
        baseline = {
            k: (_num(v, k in ("history_days", "same_weekday_days")) if v is not None else None)
            for k, v in base.items()
        }
        cat = catalog[0] if catalog else None
        if cat is not None:
            cat = {
                "tier": cat["tier"],
                "freshness_sla_min": int(cat["freshness_sla_min"]),
                "completeness_sla": float(cat["completeness_sla"]),
                "downstream_critical": bool(int(cat["downstream_critical"])),
                "dependent_jobs": list(cat["dependent_jobs"]),
                "ops_note": cat["ops_note"],
            }
        cal = None
        if calendar:
            cal = {
                "is_holiday": bool(int(calendar[0]["is_holiday"])),
                "expected_factor": float(calendar[0]["expected_factor"]),
            }
        return {
            "pipeline_id": pipeline_id,
            "dataset_id": dataset_id,
            "window_day": day,
            "as_of": as_of,
            "window": window,
            "batches": merged,
            "baseline": baseline,
            "history_rows": [[h["d"], int(h["row_count"])] for h in history],
            "catalog": cat,
            "calendar": cal,
        }

    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        f = spec.facts
        with timed() as t:
            facts = self.window_facts(f["pipeline_id"], f["dataset_id"], f["window_day"], f["as_of"])
        ref = {
            "backend": self.name,
            "tables": list(TABLES),
            "pipeline_id": f["pipeline_id"],
            "dataset_id": f["dataset_id"],
            "window_day": f["window_day"],
        }
        return Evidence(facts=facts, ref=ref, db_ms=t["ms"])
