from __future__ import annotations

import pytest

from debench import SEED
from debench.config import load_settings
from debench.dataset import e01
from debench.evidence.base import BackendUnavailable
from debench.evidence.opensearch import build_query


def test_query_template_is_fixed_and_time_bounded():
    q = build_query("d", "r", "2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z")
    flt = q["query"]["bool"]["filter"]
    assert {"term": {"dag_id": "d"}} in flt and {"term": {"run_id": "r"}} in flt
    assert flt[2]["range"]["event_ts"] == {"gte": "2025-01-01T00:00:00Z", "lte": "2025-01-01T01:00:00Z"}
    assert "gold" not in str(q).lower()


@pytest.mark.db
def test_live_retrieval_matches_reference_for_sample():
    specs = e01.generate_specs(SEED)
    backend = e01.make_backend(load_settings())
    try:
        backend.setup()
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    try:
        backend.load(e01.db_seed(specs, SEED))
        assert backend.runtime == "native_tarball"
        for spec in specs[::25]:
            ev = backend.retrieve(spec)
            assert ev.facts == e01.expected_evidence(spec)
            assert e01.oracle_check(spec, ev.facts) == []
            assert ev.ref["document_ids"] and ev.db_ms > 0
    finally:
        backend.teardown()
