"""E03 generator, policy and rendering tests (no database needed)."""

from __future__ import annotations

from collections import Counter, defaultdict

import pytest

from debench import SEED
from debench.dataset import e03
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import ScenarioRecord, ScenarioSpec
from debench.payload import assert_no_leakage, build_request
from debench.questions import load_question_set


@pytest.fixture(scope="module")
def specs() -> list[ScenarioSpec]:
    return e03.generate_specs(SEED)


def _facts(change: dict) -> dict:
    return e03.facts_from_effects(change)


def test_questions_follow_plan() -> None:
    q = load_question_set("E03")
    assert q.version == "e03-v1"
    assert q.questions["drift_impact"].labels() == [
        "non_breaking_addition", "breaking_rename_drop", "type_or_cast_break",
        "constraint_or_nullability_break", "semantic_change", "no_change_or_unknown",
    ]
    assert q.questions["migration_risk"].n_levels() == 5
    assert q.questions["review_before_deploy"].primitive == "noul"
    for lang in ("en", "th"):
        for qs in q.questions.values():
            texts = [qs.instructions[lang]]
            if qs.primitive == "choice":
                texts += [v[lang] for v in qs.criteria.values()]
            elif qs.primitive == "score":
                texts += [v[lang] for v in qs.criteria]
            assert all(texts)
    for qs in q.questions.values():
        if qs.primitive == "choice":
            assert all(len(v["en"].split()) <= 12 for v in qs.criteria.values())


def test_generation_is_deterministic(specs: list[ScenarioSpec]) -> None:
    again = e03.generate_specs(SEED)
    assert [s.scenario_id for s in specs] == [s.scenario_id for s in again]
    assert [s.facts for s in specs] == [s.facts for s in again]
    assert [s.gold for s in specs] == [s.gold for s in again]


def test_exactly_300_specs_with_many_small_groups(specs: list[ScenarioSpec]) -> None:
    assert len(specs) == 300
    assert [s.scenario_id for s in specs] == [f"E03-{i:04d}" for i in range(1, 301)]
    sizes = Counter(s.group_id for s in specs)
    assert len(sizes) >= 100
    assert max(sizes.values()) <= 3
    assert len({tuple(s.facts["_internal"]["change"]["stmts"]) for s in specs}) == 300


def test_groups_never_cross_splits(specs: list[ScenarioSpec]) -> None:
    assign_splits(specs, SEED)
    seen: dict[str, set[str]] = defaultdict(set)
    for s in specs:
        seen[s.group_id].add(s.split or "")
    assert all(len(v) == 1 for v in seen.values())
    assert Counter(s.split for s in specs) == {"test": 200, "dev": 60, "calibration": 40}


def test_class_balance_and_policy_coverage(specs: list[ScenarioSpec]) -> None:
    impact = Counter(s.gold["drift_impact"] for s in specs)
    assert set(impact) == set(load_question_set("E03").questions["drift_impact"].labels())
    assert min(impact.values()) >= 25
    assert set(Counter(s.gold["migration_risk"] for s in specs)) == {0, 1, 2, 3, 4}
    non_sufficient = sum(s.evidence_sufficiency != "sufficient" for s in specs)
    hard_neg = sum("hard_negative" in s.difficulty_tags for s in specs)
    assert non_sufficient >= 60 and hard_neg >= 60


def test_gold_equals_policy_of_expected_facts(specs: list[ScenarioSpec]) -> None:
    for s in specs:
        assert s.gold == e03.gold_from_facts(e03.expected_evidence(s))
        assert s.label_source == e03.LABEL_SOURCE
        assert "_internal" in s.facts


def test_every_spec_renders_a_valid_pair_without_leakage(specs: list[ScenarioSpec]) -> None:
    qset = load_question_set("E03")
    for level in ("common_fit", "natural_long"):
        for s in specs:
            facts = e03.expected_evidence(s)
            en, th = (e03.render_state(s, facts, lang, level) for lang in ("en", "th"))
            assert validate_pair(en, th, e03.protected_tokens(s, facts)) == [], s.scenario_id
            assert list(en) == list(th)
            for lang, state in (("en", en), ("th", th)):
                rec = ScenarioRecord(
                    s.scenario_id, "E03", "dev", SEED, lang, s.scenario_id, s.group_id, state, {}, level,
                    qset.version, s.gold, s.difficulty_tags, s.evidence_sufficiency, s.label_source, True, True,
                )
                assert_no_leakage(build_request(rec, qset), rec)
    en_words = [len(" ".join(map(str, e03.render_state(s, e03.expected_evidence(s), "en", "common_fit").values())).split())
                for s in specs]
    assert max(en_words) <= 110


def test_sql_is_byte_identical_in_both_languages(specs: list[ScenarioSpec]) -> None:
    for s in specs[:50]:
        facts = e03.expected_evidence(s)
        sql = facts["migration"]["sql"]
        for lang in ("en", "th"):
            assert e03.render_state(s, facts, lang, "common_fit")["migration"] == sql


# --- hand-verifiable fixtures (policy side; the DB side is in test_e03_db.py) ---------------------

def test_fixture_not_null_without_default_on_nonempty_table_fails_migration() -> None:
    f = _facts(e03.add_column("orders", "tax_ref", "text", True, None))
    assert f["migration"]["ok"] is False and f["migration"]["sqlstate"] == "23502"
    assert f["migration"]["error_class"] == "NotNullViolation"
    assert e03.gold_from_facts(f) == {"drift_impact": "constraint_or_nullability_break", "migration_risk": 2,
                                      "review_before_deploy": True}


def test_fixture_not_null_without_default_on_empty_table_breaks_only_the_ingest_probe() -> None:
    f = _facts(e03.add_column("audit_log", "batch_ref", "text", True, None))
    assert f["migration"]["ok"] and list(f["consumers"]["failed"]) == ["ingest_audit"]
    assert e03.drift_label(f) == "constraint_or_nullability_break"


def test_fixture_nullable_column_addition_passes_every_consumer() -> None:
    f = _facts(e03.add_column("orders", "region_code", "text", False, None))
    assert f["consumers"]["failed"] == {} and f["consumers"]["result_changed"] == []
    assert f["consumers"]["shape_changed"] == ["order_snapshot_star"]
    assert e03.gold_from_facts(f) == {"drift_impact": "non_breaking_addition", "migration_risk": 1,
                                      "review_before_deploy": False}


def test_fixture_rename_breaks_exactly_the_referencing_consumers() -> None:
    f = _facts(e03.rename_column("orders", "order_total", "order_total_v2"))
    assert set(f["consumers"]["failed"]) == {"order_revenue_daily", "payment_reconcile", "ingest_order"}
    assert all(v["sqlstate"] == "42703" for v in f["consumers"]["failed"].values())
    assert e03.gold_from_facts(f)["drift_impact"] == "breaking_rename_drop"
    assert e03.risk_level(f) == 4  # critical consumers, several failures


def test_fixture_rename_covered_by_views_is_not_breaking() -> None:
    f = _facts(e03.rename_column("inventory", "reorder_level", "min_level"))
    assert f["consumers"]["failed"] == {}
    assert f["catalog_diff"]["views_changed"] == ["v_low_stock"]
    assert e03.drift_label(f) == "non_breaking_addition"


def test_fixture_drop_of_deprecated_column_without_consumers_is_compatible_but_reviewed() -> None:
    f = _facts(e03.drop_column("orders", "internal_note"))
    assert f["consumers"]["failed"] == {} and e03.drift_label(f) == "non_breaking_addition"
    assert e03.is_irreversible(f) and e03.risk_level(f) == 2 and e03.needs_review(f)


def test_fixture_drop_of_column_used_by_view_is_blocked_by_dependency() -> None:
    f = _facts(e03.drop_column("payments", "amount"))
    assert f["migration"]["sqlstate"] == "2BP01" and e03.drift_label(f) == "breaking_rename_drop"


def test_fixture_type_changes() -> None:
    assert e03.drift_label(_facts(e03.type_change("customers", "loyalty_points", "bigint"))) == "non_breaking_addition"
    assert e03.drift_label(_facts(e03.type_change("customers", "loyalty_points", "smallint", overflow="22003"))) \
        == "type_or_cast_break"
    f = _facts(e03.type_change("products", "unit_price", "text"))
    assert f["consumers"]["failed"] == {"expensive_products": {"sqlstate": "42883", "error_class": "UndefinedFunction"}}
    silent = _facts(e03.type_change("products", "unit_price", "numeric(12,0)"))
    assert silent["consumers"]["result_changed"] == ["price_list"]
    assert e03.drift_label(silent) == "type_or_cast_break"


def test_fixture_documented_meaning_change_is_semantic() -> None:
    t, c, upd, comment = e03._SEMANTIC_UPDATE[0]
    f = _facts(e03.semantic_column(t, c, upd, comment))
    assert e03.drift_label(f) == "semantic_change" and f["migration"]["data_rewritten"]
    assert "payment_reconcile" in f["consumers"]["result_changed"]
    doc_only = _facts(e03.semantic_column("orders", "status", None, e03._SEMANTIC_COMMENT[0][2]))
    assert doc_only["consumers"]["result_changed"] == [] and e03.drift_label(doc_only) == "semantic_change"


def test_fixture_index_only_and_noop_changes_are_no_change() -> None:
    for ch in (e03.create_index("orders", ["channel"], False), e03.drop_index("ix_orders_status"),
               e03.noop("orders", 0)):
        f = _facts(ch)
        assert e03.gold_from_facts(f) == {"drift_impact": "no_change_or_unknown", "migration_risk": 0,
                                          "review_before_deploy": False}


def test_rules_baseline_on_fixtures() -> None:
    spec = ScenarioSpec("E03-0001", "E03", "g", {}, {}, [], "sufficient", "x")
    broken = _facts(e03.rename_column("orders", "order_total", "order_total_v2"))
    assert e03.rules_baseline(spec, broken) == {"drift_impact": "breaking_rename_drop", "migration_risk": 4,
                                                "review_before_deploy": True}
    clean = _facts(e03.add_column("orders", "region_code", "text", False, None))
    assert e03.rules_baseline(spec, clean) == {"drift_impact": "non_breaking_addition", "migration_risk": 1,
                                               "review_before_deploy": False}
    silent = _facts(e03.type_change("products", "unit_price", "numeric(12,0)"))
    assert e03.rules_baseline(spec, silent)["drift_impact"] == "type_or_cast_break"


def test_oracle_check_flags_a_wrong_generator_assumption(specs: list[ScenarioSpec]) -> None:
    s = specs[0]
    facts = e03.expected_evidence(s)
    assert e03.oracle_check(s, facts) == []
    wrong = {**facts, "consumers": {**facts["consumers"], "failed": {"price_list": {"sqlstate": "42703", "error_class": "UndefinedColumn"}}}}
    assert e03.oracle_check(s, wrong)
