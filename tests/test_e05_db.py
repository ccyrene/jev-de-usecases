"""E05 against a live embedded PostgreSQL (own data directory)."""

from __future__ import annotations

import pytest

from debench import SEED
from debench.dataset import e05
from debench.evidence.base import BackendUnavailable

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def backend_and_specs():
    specs = e05.generate_specs(SEED)
    backend = e05.make_backend(None)  # type: ignore[arg-type]
    try:
        backend.setup()
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    backend.load(e05.db_seed(specs, SEED))
    yield backend, specs
    backend.teardown()


def test_engine_reports_version_and_runtime(backend_and_specs):
    backend, _ = backend_and_specs
    assert backend.engine_version().startswith("PostgreSQL")
    assert backend.runtime in {"embedded", "docker"}


def test_row_counts_match_the_plan(backend_and_specs):
    backend, _ = backend_and_specs
    total = sum(backend.conn.execute(f"select count(*) from {t}").fetchone()[0] for t in e05.ROW_COUNTS)
    assert total == 10_000


def test_retrieval_matches_reference_for_scalar_and_jsonb_fields(backend_and_specs):
    backend, specs = backend_and_specs
    picks = {}
    for s in specs:
        kind = (bool(s.facts["json_path"]), s.facts["_internal"]["meta_level"])
        picks.setdefault(kind, s)
    assert len(picks) >= 4
    for s in picks.values():
        ev = backend.retrieve(s)
        assert e05.oracle_check(s, ev.facts) == [], s.facts["field_id"]
        assert ev.db_ms > 0 and 2 <= len(ev.facts["samples_masked"]) <= 5


def test_thai_metadata_and_samples_survive_the_round_trip(backend_and_specs):
    backend, specs = backend_and_specs
    s = next(x for x in specs if x.facts["field_id"] == "crm.customer.full_name_th")
    ev = backend.retrieve(s)
    assert ev.facts["purpose_th"] and ev.facts["purpose_en"]
    assert all(set(m) <= {"*", " "} for m in ev.facts["samples_masked"])
    assert e05.oracle_check(s, ev.facts) == []
