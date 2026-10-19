"""E05 hand-verifiable fixtures: checksum, masking, policy precedence and the rules baseline."""

from __future__ import annotations

import random

import pytest

from debench.dataset import e05
from debench.dataset.schema import ScenarioSpec
from debench.evidence.postgres_pii import mask_token, mask_value, sample_row_ids


def _spec(field_id: str, version: int = 2, n: int = 3, offset: int = 0) -> ScenarioSpec:
    fd = e05.FIELD_BY_ID[field_id]
    a = e05.ARCHE_BY_KEY[fd.arche]
    return ScenarioSpec(
        scenario_id="E05-9999",
        experiment="E05",
        group_id="fixture",
        facts={
            "field_id": fd.field_id, "schema": fd.schema, "table": fd.table, "column": fd.column,
            "json_path": list(fd.path) or None, "meta_version": version, "n_samples": n, "offset": offset,
            "data_seed": 1, "_internal": {"archetype": a.key, "truth_flags": list(a.truth), "meta_level": a.meta},
        },
        gold=e05.gold_for(a),
        difficulty_tags=[],
        evidence_sufficiency=a.suff,
        label_source=e05.LABEL_SOURCE,
    )  # fmt: skip


def test_thai_id_checksum_by_hand():
    # 1*13+2*12+3*11+4*10+5*9+6*8+7*7+8*6+9*5+0*4+1*3+2*2 = 352 = 11*32 -> check digit (11-0)%10 = 1
    assert e05.thai_id_check_digit("123456789012") == 1
    assert e05.is_valid_thai_id("1234567890121")
    assert e05.is_valid_thai_id("1-2345-67890-12-1")
    assert not e05.is_valid_thai_id("1234567890120")
    assert not e05.is_valid_thai_id("12345")


def test_fake_thai_ids_always_fail_checksum():
    r = random.Random(7)
    for _ in range(2000):
        assert not e05.is_valid_thai_id(e05.fake_thai_id(r, dashed=r.random() < 0.5))


def test_mask_fixtures_by_hand():
    # Local part "tessa.mock12" has 11 alphanumerics: keep 2 at each end, separators stay.
    assert mask_value("tessa.mock12@example.com") == "te***.****12@example.com"
    assert mask_token("ab12") == "****"  # fewer than 5 alphanumerics: fully masked
    assert mask_token("12345") == "1***5"  # keep 1 + 1 of 5
    assert mask_value("ฟ้าใส ทดสอบ") == "***** *****"  # Thai script is masked completely


def test_mask_never_reveals_more_than_40_percent():
    r = random.Random(3)
    for _ in range(500):
        for s in (e05._phone(r), e05.fake_thai_id(r), e05.fake_thai_id(r, True), f"XZ{r.randrange(10**6, 10**7)}"):
            m = mask_value(s)
            kept = sum(1 for x, y in zip(s, m) if x == y and x.isalnum())
            assert kept <= 0.4 * sum(c.isalnum() for c in s)
            assert m != s


def test_sample_row_ids_distinct_and_in_range():
    for n in (600, 800, 1000, 3000, 4600):
        ids = sample_row_ids(n, 4321, 5)
        assert len(ids) == 5 and all(1 <= i <= n for i in ids)


def test_precedence_and_unknown_by_hand():
    assert e05.primary_class(("direct_identifier", "sensitive_personal"), "sufficient") == "sensitive_personal"
    assert e05.primary_class(("quasi_identifier", "direct_identifier"), "ambiguous") == "direct_identifier"
    assert e05.primary_class((), "sufficient") == "non_personal"
    # Hidden truth is direct, but with no usable context the primary class is unknown.
    assert e05.primary_class(("direct_identifier",), "insufficient") == "unknown"
    assert e05.handling_tier("sensitive_personal", False, False, False) == 3
    assert e05.handling_tier("direct_identifier", True, False, False) == 3
    assert e05.handling_tier("direct_identifier", False, False, False) == 2
    assert e05.handling_tier("quasi_identifier", False, True, False) == 2
    assert e05.handling_tier("quasi_identifier", False, False, False) == 1
    assert e05.handling_tier("non_personal", False, False, True) == 0
    assert e05.handling_tier("unknown", False, False, False) == 2


def test_fixture_misleading_customer_id_is_non_personal_and_beats_name_rule():
    spec = _spec("web.web_event.customer_id")
    assert spec.gold == {"data_class": "non_personal", "handling_tier": 1, "requires_masking_review": False}
    facts = e05.expected_evidence(spec)
    assert facts["samples_masked"] and all(s.startswith("CU") for s in facts["samples_masked"])
    base = e05.rules_baseline(spec, facts)
    assert base["data_class"] == "quasi_identifier"  # the column-name heuristic is fooled
    assert base["data_class"] != spec.gold["data_class"]


def test_fixture_innocent_name_holding_emails_is_direct_and_regex_catches_it():
    spec = _spec("crm.customer.ref_code")
    assert spec.gold == {"data_class": "direct_identifier", "handling_tier": 2, "requires_masking_review": True}
    facts = e05.expected_evidence(spec)
    assert all("@example." in s for s in facts["samples_masked"])
    assert e05.rules_baseline(spec, facts)["data_class"] == "direct_identifier"


def test_fixture_thai_id_is_highly_restricted():
    spec = _spec("crm.customer.national_id")
    assert spec.gold == {"data_class": "direct_identifier", "handling_tier": 3, "requires_masking_review": True}
    for i in range(1, 3001):
        assert not e05.is_valid_thai_id(str(e05.column_values(1, e05.FIELD_BY_ID[spec.facts["field_id"]].idx)[i - 1]))
    assert e05.rules_baseline(spec, e05.expected_evidence(spec)) == spec.gold


def test_fixture_opaque_field_is_unknown_with_review():
    spec = _spec("hr.employee.x_val")
    assert spec.gold == {"data_class": "unknown", "handling_tier": 2, "requires_masking_review": True}
    facts = e05.expected_evidence(spec)
    assert facts["purpose_en"] is None and facts["lineage_th"] is None
    assert e05.render_state(spec, facts, "en", "common_fit")["purpose"] == e05.MISSING["en"]


def test_fixture_health_note_with_name_takes_sensitive_over_direct():
    spec = _spec("audit.audit.attrs:claim.member_note")
    assert e05.ARCHE_BY_KEY["health_note_with_name"].truth == ("direct_identifier", "sensitive_personal")
    assert spec.gold["data_class"] == "sensitive_personal"


def test_fixture_hashed_vs_keyed_hash_tiers():
    unsalted = _spec("crm.customer.email_sha256").gold
    keyed = _spec("web.web_event.attrs:ids.user_hmac").gold
    assert unsalted["data_class"] == keyed["data_class"] == "quasi_identifier"
    assert (unsalted["handling_tier"], keyed["handling_tier"]) == (2, 1)


def test_oracle_flags_a_tampered_gold_and_tampered_sample():
    spec = _spec("crm.customer.email")
    facts = e05.expected_evidence(spec)
    assert e05.oracle_check(spec, facts) == []
    spec.gold["data_class"] = "non_personal"
    assert "gold_policy_mismatch" in e05.oracle_check(spec, facts)
    spec.gold = e05.gold_for(e05.ARCHE_BY_KEY["email"])
    bad = dict(facts, samples_masked=["a@example.com", "b@example.com"])
    assert "db_mismatch:samples_masked" in e05.oracle_check(spec, bad)


@pytest.mark.parametrize("lang", ["en", "th"])
def test_render_uses_language_specific_metadata_for_same_field(lang):
    spec = _spec("crm.customer.email")
    facts = e05.expected_evidence(spec)
    state = e05.render_state(spec, facts, lang, "common_fit")
    assert state["purpose"] == facts[f"purpose_{lang}"]
    assert state["field"] == "crm.customer.email"
