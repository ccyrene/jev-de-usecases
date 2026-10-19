"""Hand-verifiable E06 fixtures: tiny snapshots whose results can be checked by eye."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from debench.dataset import e06
from debench.dataset.schema import ScenarioSpec
from debench.evidence.duckdb_sql import DuckDBBackend
from debench.sql_guard import ReadOnlyExecutor

TABLES = ["orders", "payments", "customers", "provinces"]
YEAR_2025 = "EXTRACT(year FROM o.order_ts + INTERVAL 7 HOUR) = 2025"
REF_SALES = f"SELECT SUM(o.total_amount) AS total_sales FROM orders o WHERE o.status = 'completed' AND {YEAR_2025}"


def _snapshot(path: Path, orders: list[tuple], payments: list[tuple]) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders(order_id INTEGER, customer_id INTEGER, province_id INTEGER, "
                "order_ts TIMESTAMP, status VARCHAR, total_amount DECIMAL(12,2))")
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", orders)
    con.execute("CREATE TABLE payments(payment_id INTEGER, order_id INTEGER, amount DECIMAL(12,2), status VARCHAR)")
    con.executemany("INSERT INTO payments VALUES (?, ?, ?, ?)", payments)
    con.execute("CREATE TABLE customers(customer_id INTEGER, is_test BOOLEAN)")
    con.execute("INSERT INTO customers VALUES (1, FALSE), (2, FALSE)")
    con.execute("CREATE TABLE provinces(province_id INTEGER, name_en VARCHAR)")
    con.execute("INSERT INTO provinces VALUES (1, 'Bangkok')")
    con.execute("CHECKPOINT")
    con.close()


@pytest.fixture()
def backend(tmp_path: Path):
    # Order 4 is Dec 31 2024 10:00 UTC (17:00 in Bangkok, still 2024); order 5 exists only on the
    # adversarial snapshot at Dec 31 2024 18:30 UTC, which is Jan 1 2025 01:30 in Bangkok.
    base_orders = [
        (1, 1, 1, "2025-03-10 05:00:00", "completed", 100),
        (2, 2, 1, "2025-06-01 08:00:00", "completed", 250),
        (3, 1, 1, "2025-06-02 08:00:00", "cancelled", 999),
        (4, 2, 1, "2024-12-31 10:00:00", "completed", 40),
    ]
    base_pay = [(1, 1, 100, "captured"), (2, 2, 250, "captured"), (3, 4, 40, "captured"), (4, 3, 999, "failed")]
    adv_orders = base_orders + [(5, 1, 1, "2024-12-31 18:30:00", "completed", 70)]
    adv_pay = [(1, 1, 60, "captured"), (2, 1, 40, "captured"), (3, 2, 250, "captured"), (4, 4, 40, "captured"),
               (5, 5, 70, "captured"), (6, 3, 999, "failed")]
    _snapshot(tmp_path / "base.duckdb", base_orders, base_pay)
    _snapshot(tmp_path / "adv.duckdb", adv_orders, adv_pay)
    be = DuckDBBackend(tmp_path)
    be.setup()
    be._exec = {n: ReadOnlyExecutor(tmp_path / f"{n}.duckdb", TABLES) for n in ("base", "adv")}
    be._schema = {"tables": {t: be.schema["tables"][t] for t in TABLES}}
    yield be
    be.teardown()


def _spec(ref: str | None, cand: str, kind: str, request: str = "What were total sales in 2025?", **internal: Any) -> ScenarioSpec:
    meta = {"kind": kind, "shape": "scalar", "latent": False, "reference_sql": ref, "interps": [],
            "ordered": False, "ambiguous": False, "guard_expected": {"allowed": True}}
    meta.update(internal)
    return ScenarioSpec(
        scenario_id="E06-9999", experiment="E06", group_id="g",
        facts={"template": "T", "params": {}, "request": {"en": request, "th": request}, "rules": ["sales", "tz"],
               "tables": ["orders"], "candidate_sql": cand, "_internal": meta},
        gold={}, difficulty_tags=[], evidence_sufficiency="sufficient", label_source="fixture",
    )


def _facts(backend: DuckDBBackend, spec: ScenarioSpec) -> dict[str, Any]:
    return backend.retrieve(spec).facts


def test_reference_result_is_hand_verifiable(backend: DuckDBBackend) -> None:
    # Completed 2025 Bangkok-date orders: 100 + 250 on both snapshots, plus 70 only on the adversarial one.
    assert backend._exec["base"].execute(REF_SALES).rows == [(350,)]
    assert backend._exec["adv"].execute(REF_SALES).rows == [(420,)]


def test_timezone_bucket_coincides_on_base_and_diverges_on_adversarial(backend: DuckDBBackend) -> None:
    utc = REF_SALES.replace("o.order_ts + INTERVAL 7 HOUR", "o.order_ts")
    assert backend._exec["base"].execute(utc).rows == [(350,)]  # same as the reference on base
    assert backend._exec["adv"].execute(utc).rows == [(350,)]  # reference is 420
    spec = _spec(REF_SALES, utc, "wrong_tz", latent=True)
    facts = _facts(backend, spec)
    snaps = facts["_internal"]["snapshots"]
    assert snaps["base"]["equal"] is True and snaps["adv"]["equal"] is False
    assert e06.derive_labels(spec, facts) == {
        "candidate_safe_to_consider": True, "needs_clarification": False,
        "candidate_verdict": "semantically_wrong", "candidate_quality": 2,
    }
    spec.gold = e06.derive_labels(spec, facts)
    assert e06.oracle_check(spec, facts) == []


def test_omitted_status_filter_differs_everywhere_with_weak_quality(backend: DuckDBBackend) -> None:
    cand = REF_SALES.replace("o.status = 'completed' AND ", "")
    spec = _spec(REF_SALES, cand, "omit_constraint")
    facts = _facts(backend, spec)
    assert backend._exec["base"].execute(cand).rows == [(1349,)]  # 100 + 250 + 999
    labels = e06.derive_labels(spec, facts)
    assert labels["candidate_verdict"] == "semantically_wrong" and labels["candidate_quality"] == 1


def test_equivalent_reformatting_is_correct_on_every_snapshot(backend: DuckDBBackend) -> None:
    cand = (f"select sum(x.total_amount) from orders as x where {YEAR_2025.replace('o.', 'x.')} "
            "and x.status = 'completed'")
    spec = _spec(REF_SALES, cand, "equiv_reformat")
    facts = _facts(backend, spec)
    labels = e06.derive_labels(spec, facts)
    assert labels["candidate_verdict"] == "semantically_correct" and labels["candidate_quality"] == 4
    assert all(s["equal"] for s in facts["_internal"]["snapshots"].values())


def test_payment_fanout_matches_on_base_and_double_counts_on_adversarial(backend: DuckDBBackend) -> None:
    ref = ("SELECT SUM(p.amount) AS captured FROM payments p JOIN orders o ON o.order_id = p.order_id "
           "WHERE o.status = 'completed' AND p.status = 'captured' AND o.order_id IN (1, 2)")
    cand = ref.replace("SUM(p.amount)", "SUM(o.total_amount)")
    assert backend._exec["base"].execute(ref).rows == backend._exec["base"].execute(cand).rows == [(350,)]
    assert backend._exec["adv"].execute(ref).rows == [(350,)]
    assert backend._exec["adv"].execute(cand).rows == [(450,)]  # order 1 (100) is counted twice
    facts = _facts(backend, _spec(ref, cand, "latent_fanout", latent=True))
    snaps = facts["_internal"]["snapshots"]
    assert snaps["base"]["equal"] and not snaps["adv"]["equal"]


def test_absent_column_errors_and_is_wrong_not_unsafe(backend: DuckDBBackend) -> None:
    cand = REF_SALES.replace("o.total_amount", "o.total")
    spec = _spec(REF_SALES, cand, "absent_column")
    facts = _facts(backend, spec)
    assert facts["_internal"]["guard"]["allowed"] is True
    assert all(s["candidate"]["executed"] is False for s in facts["_internal"]["snapshots"].values())
    labels = e06.derive_labels(spec, facts)
    assert labels["candidate_verdict"] == "semantically_wrong" and labels["candidate_quality"] == 0
    assert labels["candidate_safe_to_consider"] is True


def test_unsafe_candidate_is_blocked_and_never_executed(backend: DuckDBBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    from debench import sql_guard

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("executed")

    cand = "DELETE FROM orders WHERE status = 'cancelled'"
    spec = _spec(REF_SALES, cand, "unsafe_delete", guard_expected={"allowed": False})
    # Reference executions are fine; the candidate must not add any further call.
    calls: list[str] = []
    real = sql_guard._run_query

    def spy(con: Any, sql: str, *a: Any, **k: Any):
        calls.append(sql)
        return real(con, sql, *a, **k)

    monkeypatch.setattr(sql_guard, "_run_query", spy)
    facts = _facts(backend, spec)
    assert cand not in calls
    labels = e06.derive_labels(spec, facts)
    assert labels == {"candidate_safe_to_consider": False, "needs_clarification": False,
                      "candidate_verdict": "unsafe_or_unexecutable", "candidate_quality": 0}
    assert backend._exec["base"].execute("SELECT COUNT(*) FROM orders").rows == [(4,)]


def test_ambiguous_request_has_diverging_readings_and_keeps_quality_two(backend: DuckDBBackend) -> None:
    a = "SELECT COUNT(*) FROM orders o WHERE o.status = 'completed'"
    b = "SELECT COUNT(*) FROM orders o"
    spec = _spec(None, a, "interp_0", request="How many active customers did we have?", ambiguous=True,
                 interps=[a, b])
    facts = _facts(backend, spec)
    spec.gold = e06.derive_labels(spec, facts)
    assert backend._exec["base"].execute(a).rows == [(3,)] and backend._exec["base"].execute(b).rows == [(4,)]
    assert spec.gold["candidate_verdict"] == "insufficient_specification" and spec.gold["needs_clarification"]
    assert e06.oracle_check(spec, facts) == []


def test_oracle_flags_a_wrong_gold_label(backend: DuckDBBackend) -> None:
    spec = _spec(REF_SALES, REF_SALES.replace("o.status = 'completed' AND ", ""), "omit_constraint")
    spec.gold = {"intent_type": "aggregation", "candidate_verdict": "semantically_correct",
                 "candidate_quality": 4, "needs_clarification": False, "candidate_safe_to_consider": True}
    problems = e06.oracle_check(spec, _facts(backend, spec))
    assert any("candidate_verdict" in p for p in problems) and any("candidate_quality" in p for p in problems)


def test_rules_baseline_on_fixtures(backend: DuckDBBackend) -> None:
    request = "What were total sales in 2025? Return a single number."
    blocked = _spec(REF_SALES, "DROP TABLE orders", "unsafe_drop", request=request, guard_expected={"allowed": False})
    out = e06.rules_baseline(blocked, _facts(backend, blocked))
    assert out["candidate_verdict"] == "unsafe_or_unexecutable" and out["candidate_safe_to_consider"] is False

    good = _spec(REF_SALES, REF_SALES, "equiv_reformat", request=request)
    assert e06.rules_baseline(good, _facts(backend, good))["candidate_verdict"] == "semantically_correct"

    no_status = _spec(REF_SALES, REF_SALES.replace("o.status = 'completed' AND ", ""), "omit_constraint", request=request)
    assert e06.rules_baseline(no_status, _facts(backend, no_status))["candidate_verdict"] == "semantically_wrong"

    vague = _spec(None, "SELECT COUNT(*) FROM orders", "interp_0", request="How many active customers did we have?",
                  ambiguous=True, interps=["SELECT COUNT(*) FROM orders"])
    out = e06.rules_baseline(vague, _facts(backend, vague))
    assert out["candidate_verdict"] == "insufficient_specification" and out["needs_clarification"] is True
