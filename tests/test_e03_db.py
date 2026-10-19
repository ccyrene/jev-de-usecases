"""E03 tests against a real PostgreSQL (embedded PG 18). Everything runs in rolled-back transactions."""

from __future__ import annotations

import pytest

from debench import SEED
from debench.dataset import e03
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import BackendUnavailable
from debench.evidence.postgres_schema import BANK, snapshot

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def backend():
    b = e03.make_backend()
    try:
        b.setup()
    except BackendUnavailable as exc:  # pragma: no cover
        pytest.skip(str(exc))
    b.load(None)
    yield b
    b.teardown()


def _spec(change: dict) -> ScenarioSpec:
    facts = e03.facts_from_effects(change)
    internal = {k: change[k] for k in ("kind", "stmts", "effects", "touched", "variant")}
    return ScenarioSpec("E03-9999", "E03", change["group"], {"_internal": {"change": internal}},
                        e03.gold_from_facts(facts), [], "sufficient", "x")


def test_engine_and_base_schema(backend) -> None:
    assert backend.runtime == "embedded"
    assert backend.engine_version().startswith("PostgreSQL 18")
    assert backend.baseline_failures() == []
    snap = backend._base_snapshot
    assert len(snap["tables"]) >= 20 and len(snap["views"]) == 5
    assert len(snap["indexes"]) >= 30 and snap["view_deps"] and len(BANK) == 44
    counts = backend.table_row_counts()
    assert all(counts[t] == 0 for t in e03.EMPTY_TABLES) and counts["orders"] == 200


def test_fixture_not_null_no_default_fails_on_nonempty_but_not_on_empty_table(backend) -> None:
    non_empty = backend.measure(["ALTER TABLE orders ADD COLUMN tax_ref text NOT NULL;"])
    assert non_empty["migration"]["sqlstate"] == "23502"
    assert non_empty["migration"]["error_class"] == "NotNullViolation"
    assert non_empty["catalog_diff"]["columns_added"] == []  # rolled back to the savepoint
    empty = backend.measure(["ALTER TABLE audit_log ADD COLUMN batch_ref text NOT NULL;"])
    assert empty["migration"]["ok"]
    assert empty["consumers"]["failed"] == {"ingest_audit": {"sqlstate": "23502", "error_class": "NotNullViolation"}}


def test_fixture_nullable_addition_passes_all_consumers(backend) -> None:
    res = backend.measure(["ALTER TABLE orders ADD COLUMN region_code text;"])
    assert res["migration"]["ok"] and res["consumers"]["failed"] == {}
    assert res["consumers"]["result_changed"] == []
    assert res["catalog_diff"]["columns_added"] == ["orders.region_code text null"]


def test_fixture_rename_breaks_the_consumer_that_references_it(backend) -> None:
    res = backend.measure(["ALTER TABLE orders RENAME COLUMN order_total TO order_total_v2;"])
    failed = res["consumers"]["failed"]
    assert set(failed) == {"order_revenue_daily", "payment_reconcile", "ingest_order"}
    assert {v["error_class"] for v in failed.values()} == {"UndefinedColumn"}
    # views follow a rename: the view consumers keep passing
    assert "customer_orders_view" not in failed and "revenue_view" not in failed


def test_fixture_view_blocks_type_change_and_drop(backend) -> None:
    assert backend.measure(["ALTER TABLE orders ALTER COLUMN order_total TYPE text;"])["migration"]["sqlstate"] == "0A000"
    assert backend.measure(["ALTER TABLE payments DROP COLUMN amount;"])["migration"]["sqlstate"] == "2BP01"


def test_fixture_silent_precision_reduction_changes_results_without_errors(backend) -> None:
    res = backend.measure(["ALTER TABLE products ALTER COLUMN unit_price TYPE numeric(12,0);"])
    assert res["consumers"]["failed"] == {} and res["consumers"]["result_changed"] == ["price_list"]


def test_scenarios_are_isolated_and_repeatable(backend) -> None:
    before = backend._conn.cursor().execute("select 1").fetchone()
    backend._conn.rollback()
    first = backend.measure(["ALTER TABLE orders DROP COLUMN channel;"])
    second = backend.measure(["ALTER TABLE orders DROP COLUMN channel;"])
    assert first["consumers"] == second["consumers"] and first["catalog_diff"] == second["catalog_diff"]
    with backend._conn.cursor() as cur:
        assert snapshot(cur) == backend._base_snapshot
    backend._conn.rollback()
    assert before == (1,)


def test_retrieve_records_the_actual_error_class(backend) -> None:
    spec = _spec(e03.rename_column("customers", "email", "email_v2"))
    ev = backend.retrieve(spec)
    assert ev.db_ms > 0 and ev.ref["backend"] == "postgres"
    assert ev.facts["consumers"]["failed"]["customer_lookup_email"]["error_class"] == "UndefinedColumn"
    assert e03.oracle_check(spec, ev.facts) == []


def test_every_candidate_change_matches_the_generator_model(backend) -> None:
    pool = e03._dedupe(e03._candidate_pool())
    assert len(pool) > 600
    bad = {}
    for ch in pool:
        spec = _spec(ch)
        problems = e03.oracle_check(spec, backend.retrieve(spec).facts)
        if problems:
            bad[ch["stmts"][0]] = problems[:2]
    assert bad == {}


def test_generated_specs_are_oracle_verified(backend) -> None:
    for spec in e03.generate_specs(SEED)[:60]:
        assert e03.oracle_check(spec, backend.retrieve(spec).facts) == [], spec.scenario_id


def test_fixture_rename_only_seen_through_a_view_keeps_every_consumer_passing(backend) -> None:
    res = backend.measure(["ALTER TABLE inventory RENAME COLUMN reorder_level TO min_level;"])
    assert res["migration"]["ok"] and res["consumers"]["failed"] == {}
    assert res["catalog_diff"]["views_changed"] == ["v_low_stock"]
