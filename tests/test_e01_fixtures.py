"""Hand-verifiable E01 fixtures. Expected values are derived by hand from the DAG catalog:

daily_customer_etl (SLA 120): wait -> extract -> validate -> transform -> load_dim -> publish_mart;
  audit_log_export hangs off validate; publish_customer_mart feeds 3 consumers.
hr_headcount_weekly (SLA 1440): ... load_headcount feeds send_summary_email, no consumers.
"""

from __future__ import annotations

import random

from debench.dataset import e01
from debench.dataset.paired_generator import validate_pair


def _spec(kind, dag, task, outcome, *, index=1, decoy=False, **kw):
    ctx = e01.make_ctx(random.Random(7), kind, decoy)
    ctx.update(kw.pop("ctx", {}))
    return e01.build_spec(index, kind, dag, task, outcome, ctx, random.Random(11), "G-fixture", **kw)


def test_fixture_undefined_column_blocks_three_consumers():
    spec = _spec("undefined_column", "daily_customer_etl", "transform_customer_data", "failed",
                 ctx={"col": "customer_id", "variant": 0}, max_retries=2, deployment_id="dep-4821",
                 stale=True, sibling=True)
    # transform blocks load_customer_dim + publish_customer_mart (2 tasks) and 3 consumers -> severity 3
    assert e01.DAGS["daily_customer_etl"].impact("transform_customer_data") == (2, 3)
    assert spec.gold == {"failure_family": "schema_mismatch", "severity": 3, "requires_human": True}
    facts = e01.expected_evidence(spec)
    failing = [e for e in facts["events"] if e["task_id"] == "transform_customer_data"]
    assert [e["retry_count"] for e in failing] == [0, 1, 2]
    assert [e["status"] for e in failing] == ["up_for_retry", "up_for_retry", "failed"]
    assert all(e["message"] == 'column "customer_id" does not exist' for e in failing)
    impact = [e for e in facts["events"] if e["status"] == "blocked"]
    assert [e["message"] for e in impact] == ["downstream impact: tasks=2 consumers=3 sla_minutes=120"]
    # stale (earlier than the window) and sibling-run failures are excluded by the query template
    assert not any("dns" in e["message"].lower() or "password" in e["message"] for e in facts["events"])
    # 3 failing attempts + the scheduler impact event (+ 2 skipped descendants matched by the message regex)
    assert sorted(e["status"] for e in facts["events"]) == ["blocked", "failed", "skipped", "skipped", "up_for_retry", "up_for_retry"]
    assert e01.oracle_check(spec, facts) == []
    assert e01.rules_baseline(spec, facts) == {"failure_family": "schema_mismatch", "severity": 3, "requires_human": True}


def test_fixture_pending_timeout_retry_is_low_severity_no_human():
    spec = _spec("socket_timeout", "hr_headcount_weekly", "load_headcount", "retrying",
                 max_retries=2, retries_done=0, ctx={"host": "warehouse-db.example.org", "secs": 30})
    facts = e01.expected_evidence(spec)
    mine = [e for e in facts["events"] if e["task_id"] == "load_headcount"]
    assert len(mine) == 1 and mine[0]["status"] == "up_for_retry"
    assert mine[0]["exception_type"] == "socket.timeout"
    assert mine[0]["message"] == "timed out after 30s connecting to warehouse-db.example.org:5432"
    at_risk = [e for e in facts["events"] if e["status"] == "at_risk"]
    assert "retries_left=2" in at_risk[0]["message"]
    assert spec.gold == {"failure_family": "connectivity", "severity": 1, "requires_human": False}
    assert e01.rules_baseline(spec, facts) == spec.gold
    en = e01.render_state(spec, facts, "en", "common_fit")
    th = e01.render_state(spec, facts, "th", "common_fit")
    assert "retry scheduled (2 retries left)" in en["status"]
    assert "เหลือ retry อีก 2 ครั้ง" in th["status"]
    assert validate_pair(en, th, e01.protected_tokens(spec, facts)) == []


def test_fixture_leaf_disk_full_and_hard_negative_value():
    leaf = _spec("disk_full", "daily_customer_etl", "audit_log_export", "failed", ctx={"variant": 0})
    # leaf: no downstream tasks, no consumers -> severity 1; resource family but sev < 2 -> no human
    assert e01.DAGS["daily_customer_etl"].impact("audit_log_export") == (0, 0)
    assert leaf.gold == {"failure_family": "resource_exhaustion", "severity": 1, "requires_human": False}
    facts = e01.expected_evidence(leaf)
    assert e01.rules_baseline(leaf, facts) == leaf.gold  # generic OSError resolved by its signature

    hard = _spec("type_conversion", "orders_ingest_hourly", "dedupe_orders", "failed", decoy=True,
                 ctx={"value": "denied"})
    f2 = e01.expected_evidence(hard)
    assert any(e["message"] == 'invalid input syntax for type integer: "denied"' for e in f2["events"])
    assert "hard_negative" in hard.difficulty_tags
    # the word "denied" must not pull the answer to authentication
    assert e01.rules_baseline(hard, f2)["failure_family"] == "data_error"
    assert hard.gold["failure_family"] == "data_error"
    # dedupe_orders -> load_orders_fact, refresh cache and audit leaf; 2 consumers on load_orders_fact -> severity 2
    assert hard.gold["severity"] == 2 and hard.gold["requires_human"] is False


def test_fixture_unresolved_evidence_is_other_unknown_and_baseline_abstains():
    spec = _spec("two_cause", "payments_reconcile", "match_ledger", "failed",
                 ctx={"pair": ("password_auth", "missing_relation")}, max_retries=2)
    facts = e01.expected_evidence(spec)
    types = [e["exception_type"] for e in facts["events"] if e["task_id"] == "match_ledger"]
    # attempts alternate between the two unrelated causes
    assert types == ["psycopg2.OperationalError", "psycopg2.errors.UndefinedTable", "psycopg2.OperationalError"]
    assert spec.gold["failure_family"] == "other_unknown" and spec.gold["requires_human"] is True
    assert spec.evidence_sufficiency == "insufficient"
    out = e01.rules_baseline(spec, facts)
    assert out["failure_family"] is None and out["requires_human"] is None
    assert out["severity"] == 3  # match_ledger -> publish_recon_report with 4 consumers
    assert e01.oracle_check(spec, facts) == []

    alert = _spec("alert_only", "fleet_gps_etl", "snap_to_routes", "alert_only")
    f2 = e01.expected_evidence(alert)
    assert alert.gold == {"failure_family": "other_unknown", "severity": 0, "requires_human": False}
    assert e01.rules_baseline(alert, f2) == {"failure_family": None, "severity": 0, "requires_human": False}


def test_oracle_flags_wrong_evidence():
    spec = _spec("undefined_column", "daily_customer_etl", "transform_customer_data", "failed")
    facts = e01.expected_evidence(spec)
    broken = {**facts, "events": [e for e in facts["events"] if e["status"] != "blocked"]}
    assert e01.oracle_check(spec, broken)
