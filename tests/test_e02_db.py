"""E02 on the real embedded ClickHouse engine (chdb)."""

from __future__ import annotations

import pandas as pd
import pytest
from test_e02_common import get_specs

from debench import SEED
from debench.dataset import e02
from debench.evidence.clickhouse import split_sql

pytestmark = pytest.mark.db
pytest.importorskip("chdb")


def _tiny_payload() -> dict[str, pd.DataFrame]:
    """Window 2026-03-10 (Tuesday): 10 typed rows, hand-countable aggregates; 3 earlier days of 4 rows."""

    def ev(ts, batch, rid, amount, cust, et="order_paid"):
        return (ts, "pl", "ds", batch, rid, amount, cust, et)

    win = [
        ev("2026-03-10 01:00:00", "b0", 1, 10.0, "cu_1"),
        ev("2026-03-10 01:10:00", "b0", 2, 20.0, None),
        ev("2026-03-10 01:20:00", "b0", 3, 30.0, "cu_3"),
        ev("2026-03-10 04:00:00", "b1", 4, 40.0, None),
        ev("2026-03-10 04:10:00", "b1", 5, 50.0, "cu_5"),
        ev("2026-03-10 04:10:00", "b1", 5, 50.0, "cu_5"),  # replayed row: same record_id
        ev("2026-03-10 07:00:00", "b2", 6, 60.0, "cu_6"),
        ev("2026-03-10 07:30:00", "b2", 7, 70.0, "cu_7"),
        ev("2026-03-10 22:00:00", "b7", 8, 80.0, "cu_8"),
        ev("2026-03-10 22:30:00", "b7", 9, 90.0, "cu_9"),
    ]
    hist = [
        ev(f"2026-03-0{d} 12:0{i}:00", f"h{d}", 100 * d + i, 100.0, "cu_h")
        for d in (7, 8, 9)
        for i in range(4)
    ]
    cols = [
        "event_time",
        "pipeline_id",
        "dataset_id",
        "batch_id",
        "record_id",
        "amount",
        "customer_id",
        "event_type",
    ]
    events = pd.DataFrame(hist + win, columns=cols)
    raw = events.rename(columns={"amount": "amount_raw"}).copy()
    raw["amount_raw"] = raw["amount_raw"].map(lambda a: f"{a:.2f}")
    extra = raw.iloc[[len(hist)]].copy()  # one more delivered row with an unparseable amount
    extra["record_id"], extra["amount_raw"] = 99, "N/A"
    raw = pd.concat([raw, extra], ignore_index=True)
    catalog = pd.DataFrame(
        [["pl", "ds", "tier1", 180, 0.8, 1, ["finance_close_export"], ""]],
        columns=[
            "pipeline_id",
            "dataset_id",
            "tier",
            "freshness_sla_min",
            "completeness_sla",
            "downstream_critical",
            "dependent_jobs",
            "ops_note",
        ],
    )
    cal = pd.DataFrame(
        [["pl", "2026-03-10", 1, 0.5]], columns=["pipeline_id", "day", "is_holiday", "expected_factor"]
    )
    return {
        "pipeline_events": events,
        "pipeline_events_raw": raw,
        "dataset_catalog": catalog,
        "business_calendar": cal,
    }


def test_hand_counted_aggregates_on_the_real_engine(tmp_path):
    be = e02.E02Backend("e02t", session_dir=tmp_path)
    be.setup()
    try:
        assert be.engine_version()[0].isdigit()
        be.load(_tiny_payload())
        f = be.window_facts("pl", "ds", "2026-03-10", "2026-03-11 00:30:00")
    finally:
        be.teardown()
    w = f["window"]
    assert (w["n_rows"], w["uniq_records"], w["null_count"]) == (10, 9, 2)
    assert (w["amount_min"], w["amount_max"]) == (10.0, 90.0)
    assert w["amount_mean"] == pytest.approx(500 / 10)
    assert w["last_event"] == "2026-03-10 22:30:00" and w["lag_s"] == 2 * 3600
    assert w["active_hours"] == 4 and (w["raw_rows"], w["mismatch_count"]) == (11, 1)
    assert [(x["batch_id"], x["n_rows"], x["raw_rows"], x["mismatch_count"]) for x in f["batches"]] == [
        ("b0", 3, 4, 1),
        ("b1", 3, 3, 0),
        ("b2", 2, 2, 0),
        ("b7", 2, 2, 0),
    ]
    b = f["baseline"]
    assert b["history_days"] == 3 and b["mean_rows"] == 4.0 and b["same_weekday_days"] == 0
    assert b["amount_mean"] == 100.0 and b["amount_sd"] == 0.0
    assert f["calendar"] == {"is_holiday": True, "expected_factor": 0.5}
    f = e02.finish_facts(f)
    d = f["derived"]
    assert d["dup_rate"] == pytest.approx(0.1) and d["null_rate"] == pytest.approx(0.2)
    assert d["lag_min"] == 120 and d["expected_rows"] is None  # only 3 history days


def test_sql_files_are_plain_clickhouse_ddl():
    from debench.evidence.clickhouse import INIT_DIR

    ddl = "\n".join(p.read_text() for p in sorted(INIT_DIR.glob("0[1-5]_*.sql")))
    assert "ENGINE = MergeTree" in ddl and "CREATE TABLE IF NOT EXISTS pipeline_events (" in ddl
    assert len(split_sql(ddl)) == 5


def test_db_facts_match_the_reference_for_a_sample(tmp_path):
    specs = get_specs()
    kinds_seen, sample = set(), []
    for s in specs:
        k = s.facts["_internal"]["kind"]
        if k not in kinds_seen:
            kinds_seen.add(k)
            sample.append(s)
    be = e02.E02Backend("e02t", session_dir=tmp_path)
    be.setup()
    try:
        be.load(e02.db_seed(sample, SEED))
        for s in sample:
            ev = be.retrieve(s)
            assert e02.oracle_check(s, ev.facts) == [], s.scenario_id
            assert ev.db_ms > 0
        bad = be.retrieve(sample[0])
        bad.facts["window"]["n_rows"] += 1
        assert e02.oracle_check(sample[0], bad.facts)
    finally:
        be.teardown()
