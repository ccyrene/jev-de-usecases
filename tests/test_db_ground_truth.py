"""Built datasets must be DB-backed and fully verified before they can feed final results."""

from __future__ import annotations

import pytest

from conftest import CASES, build_report_or_skip, built_records_or_skip


@pytest.mark.parametrize("case", CASES)
def test_build_used_a_real_engine(case):
    report = build_report_or_skip(case)
    if report["evidence_mode"] != "db":
        pytest.skip(f"{case} was built offline (--mode expected); not usable for final results")
    assert report["runtime"] in {"embedded", "native_tarball", "docker"}
    assert report["engine_version"]


@pytest.mark.parametrize("case", CASES)
def test_all_gold_verified_against_db(case):
    report = build_report_or_skip(case)
    if report["evidence_mode"] != "db":
        pytest.skip(f"{case} was built offline")
    assert report["oracle_failures"] == {}, list(report["oracle_failures"].items())[:5]
    records = built_records_or_skip(case)
    assert all(r.gold_verified for r in records)
    assert all(r.db_retrieval_ms is not None for r in records)
