from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from debench.config import Settings
from debench.dataset.paired_generator import assign_splits, validate_pair
from debench.dataset.schema import ScenarioRecord, ScenarioSpec
from debench.normalize import normalize_answer, normalize_response
from debench.payload import LeakageError, build_request
from debench.providers.base import DecisionRequest, DecisionResponse
from debench.questions import load_question_set

YAML = textwrap.dedent(
    """
    case: E99
    version: e99-v1
    questions:
      family:
        type: choice
        instructions: {en: "Which family?", th: "ปัญหานี้ควรจัดอยู่ในประเภทใด"}
        criteria:
          schema_mismatch: {en: "Missing or renamed column", th: "คอลัมน์หายหรือถูกเปลี่ยนชื่อ"}
          connectivity: {en: "Network failure", th: "เครือข่ายล้มเหลว"}
          other_unknown: {en: "Other or unclear", th: "อื่น ๆ หรือไม่ชัดเจน"}
      severity:
        type: score
        instructions: {en: "How severe?", th: "รุนแรงแค่ไหน"}
        criteria:
          - {en: "minor", th: "เล็กน้อย"}
          - {en: "blocked", th: "งานหยุด"}
          - {en: "critical", th: "วิกฤต"}
      requires_human:
        type: noul
        instructions: {en: "Needs a human?", th: "ต้องให้คนตรวจสอบหรือไม่"}
        criteria:
          true: {en: "yes", th: "ใช่"}
          false: {en: "no", th: "ไม่ใช่"}
    """
)


@pytest.fixture()
def qset(tmp_path: Path):
    (tmp_path / "e99.yaml").write_text(YAML, encoding="utf-8")
    return load_question_set("E99", prompts_dir=tmp_path)


def _record(state, gold=None, sid="E99-0001", lang="en") -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id=sid,
        experiment="E99",
        split="test",
        seed=1,
        language=lang,
        pair_id=sid,
        group_id="g1",
        state=state,
        evidence_ref={},
        context_level="common_fit",
        questions_version="e99-v1",
        gold=gold or {"family": "schema_mismatch", "severity": 1, "requires_human": True},
        difficulty_tags=[],
        evidence_sufficiency="sufficient",
        label_source="test",
        gold_verified=True,
        semantic_pair_validated=True,
    )


# --- questions -------------------------------------------------------------------------------


def test_render_keeps_ascii_ids_and_localizes_text(qset):
    en, th = qset.render("en"), qset.render("th")
    assert list(en["family"]["criteria"]) == list(th["family"]["criteria"])
    assert th["family"]["instructions"] == "ปัญหานี้ควรจัดอยู่ในประเภทใด"
    assert th["requires_human"]["criteria"] == {"true": "ใช่", "false": "ไม่ใช่"}


def test_choice_permutation_preserves_label_mapping(qset):
    base = qset.render("en")["family"]["criteria"]
    order = ["other_unknown", "schema_mismatch", "connectivity"]
    permuted = qset.render("en", option_orders={"family": order})["family"]["criteria"]
    assert list(permuted) == order
    assert all(permuted[k] == base[k] for k in base)


def test_score_criteria_cannot_be_permuted(qset):
    with pytest.raises(ValueError):
        qset.render("en", option_orders={"severity": ["2", "1", "0"]})


def test_content_hash_changes_with_rubric(tmp_path: Path):
    (tmp_path / "e99.yaml").write_text(YAML, encoding="utf-8")
    h1 = load_question_set("E99", prompts_dir=tmp_path).content_hash
    (tmp_path / "e99.yaml").write_text(YAML.replace("How severe?", "How bad?"), encoding="utf-8")
    assert load_question_set("E99", prompts_dir=tmp_path).content_hash != h1


# --- normalize -------------------------------------------------------------------------------


def test_normalize_choice_score_noul(qset):
    q = qset.render("en")
    choice = normalize_answer(
        "family",
        q["family"],
        {"type": "choice", "choice": "connectivity", "probabilities": {"schema_mismatch": 0.2, "connectivity": 0.7, "other_unknown": 0.1}},
    )
    assert choice.valid and choice.predicted_label == "connectivity" and choice.api_choice_matches_argmax
    assert choice.max_probability == pytest.approx(0.7)
    score = normalize_answer("severity", q["severity"], {"type": "score", "probabilities": {"0": 0.2, "1": 0.5, "2": 0.3}})
    # expected index = 0*0.2 + 1*0.5 + 2*0.3 = 1.1
    assert score.valid and score.predicted_level == 1 and score.score_value == pytest.approx(1.1)
    noul = normalize_answer("requires_human", q["requires_human"], {"type": "noul", "noul": 0.25})
    assert noul.valid and noul.noul_probability == 0.25 and noul.max_probability == 0.75


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ({"type": "choice", "choice": "connectivity", "probabilities": {"connectivity": 1.0}}, "label_set_mismatch"),
        ({"type": "choice", "choice": "x", "probabilities": {"schema_mismatch": 0.2, "connectivity": 0.7, "other_unknown": 0.1}}, "unknown_choice"),
        ({"type": "choice", "choice": "connectivity", "probabilities": {"schema_mismatch": 0.5, "connectivity": 0.7, "other_unknown": 0.1}}, "bad_sum"),
        ({"type": "choice", "choice": "connectivity", "probabilities": {"schema_mismatch": float("nan"), "connectivity": 0.7, "other_unknown": 0.1}}, "non_finite"),
        ({"type": "score", "choice": "connectivity"}, "type_mismatch"),
    ],
)
def test_invalid_choice_answers_are_flagged(qset, raw, kind):
    ans = normalize_answer("family", qset.render("en")["family"], raw)
    assert not ans.valid and ans.error_type == kind


def test_two_decimal_rounding_is_accepted_but_coarse_sums_are_not():
    q = {"type": "choice", "criteria": {f"o{i}": "x" for i in range(7)}}
    # Jev-style 2 dp output: 0.93 + 0.01 * 6 = 0.99, within 7 * 0.005 = 0.035.
    jev = {"choice": "o0", "probabilities": {"o0": 0.93, **{f"o{i}": 0.01 for i in range(1, 6)}, "o6": 0.01}}
    assert normalize_answer("q", q, jev).valid
    # Float noise in a 2 dp value must not shrink the allowance (observed in real Jev output).
    noisy = {"choice": "o2", "probabilities": {"o0": 0.0, "o1": 0.03, "o2": 0.8200000000000001, "o3": 0.03, "o4": 0.11}}
    assert normalize_answer("q", {"type": "choice", "criteria": {f"o{i}": "x" for i in range(5)}}, noisy).valid
    q2 = {"type": "choice", "criteria": {"a": "x", "b": "y"}}
    # 1-dp values get only the 2-dp allowance (2 * 0.005 = 0.01), so 1.1 is rejected.
    assert normalize_answer("q", q2, {"choice": "b", "probabilities": {"a": 0.5, "b": 0.6}}).error_type == "bad_sum"


def test_score_tie_resolves_to_lowest_level(qset):
    ans = normalize_answer("severity", qset.render("en")["severity"], {"probabilities": [0.4, 0.4, 0.2]})
    assert ans.predicted_level == 0


def test_provider_failure_is_invalid_not_wrong(qset):
    req = DecisionRequest("E99-0001", "en", {"a": "b"}, qset.render("en"))
    resp = DecisionResponse("jev_114", "", {}, 10.0, None, None, "timeout")
    rows = normalize_response(req, resp)
    assert len(rows) == 3 and all(not r.valid and r.error_type == "timeout" for r in rows)


def test_missing_answer_is_invalid(qset):
    req = DecisionRequest("E99-0001", "en", {"a": "b"}, qset.render("en"))
    resp = DecisionResponse("laya_routed", "laya", {}, 10.0, None, None, None)
    assert {r.error_type for r in normalize_response(req, resp)} == {"missing_answer"}


# --- payload / leakage -----------------------------------------------------------------------


def test_clean_record_builds_request_and_crossover(qset):
    rec = _record({"log_excerpt": "UndefinedColumn: customer_id", "note": "งานล้มเหลวหลัง deploy"}, lang="th")
    req = build_request(rec, qset, question_language="en")
    assert req.language == "th" and req.questions["family"]["instructions"] == "Which family?"
    assert set(req.shared_body()) == {"state", "questions"}


@pytest.mark.parametrize(
    "state",
    [
        {"note": "this looks like schema_mismatch"},
        {"note": "fault_id=17"},
        {"note": "see E99-0001"},
        {"gold": "x"},
        {"_internal": "x"},
    ],
)
def test_leakage_is_rejected(qset, state):
    with pytest.raises(LeakageError):
        build_request(_record(state), qset)


def test_gold_layer_word_is_not_a_leak(qset):
    build_request(_record({"note": "the gold layer table gold.daily_sales was split by date"}), qset)


def test_request_hash_depends_on_option_order(qset):
    rec = _record({"note": "ok"})
    a = build_request(rec, qset)
    b = build_request(rec, qset, option_orders={"family": ["connectivity", "schema_mismatch", "other_unknown"]})
    assert a.request_hash() != b.request_hash()


# --- pairing and splits ----------------------------------------------------------------------


def test_assign_splits_never_splits_a_group():
    # Mixed group sizes (3, 2, 1, 3, 2, 1, ...) so exact split targets are reachable.
    group_of, gid, left = [], 0, 3
    for _ in range(300):
        group_of.append(gid)
        left -= 1
        if left == 0:
            gid += 1
            left = 3 - gid % 3
    specs = [ScenarioSpec(f"E99-{i:04d}", "E99", f"g{g}", {}, {}, [], "sufficient", "t") for i, g in enumerate(group_of)]
    assign_splits(specs, seed=7)
    by_group: dict[str, set[str]] = {}
    for s in specs:
        by_group.setdefault(s.group_id, set()).add(s.split)
    assert all(len(v) == 1 for v in by_group.values())
    counts = {sp: sum(s.split == sp for s in specs) for sp in ("dev", "calibration", "test")}
    assert counts == {"dev": 60, "calibration": 40, "test": 200}


def test_validate_pair_detects_problems():
    en = {"log": "UndefinedColumn: customer_id at 2026-10-19T03:00:00Z, 3 retries", "note": "Task failed"}
    th_ok = {"log": "UndefinedColumn: customer_id at 2026-10-19T03:00:00Z, 3 retries", "note": "งานล้มเหลว"}
    assert validate_pair(en, th_ok, ["customer_id"]) == []
    th_bad = {"log": "UndefinedColumn: customer_id at 2026-10-19T03:00:00Z, ๓ retries", "note": "งานล้มเหลว"}
    problems = validate_pair(en, th_bad, ["customer_id"])
    assert any("Thai digits" in p for p in problems) and any("numeric tokens" in p for p in problems)
    assert any("missing in th" in p for p in validate_pair(en, {"log": "x", "note": "งาน"}, ["customer_id"]))


# --- settings never expose secrets -----------------------------------------------------------


def test_settings_redaction():
    s = Settings(values={"TYPESAFE_API_KEY": "sk-test-not-real", "NEO4J_PASSWORD": "pw", "JEV_MODEL": "jev-1.14.0"})
    red = s.redacted()
    assert red["TYPESAFE_API_KEY"] == "<set>" and red["NEO4J_PASSWORD"] == "<set>"
    assert red["JEV_MODEL"] == "jev-1.14.0"
    assert "sk-test" not in repr(s) and "pw" not in repr(s)
    assert not s.remote_enabled
    with pytest.raises(KeyError):
        s.secret("JEV_MODEL")
