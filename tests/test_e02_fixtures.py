"""Hand-verifiable E02 fixtures: thresholds, policy and rules baseline on small known numbers."""

from __future__ import annotations

from test_e02_common import make_facts

from debench.dataset import e02
from debench.dataset.schema import ScenarioSpec


def _spec(gold: dict, sufficiency: str = "sufficient") -> ScenarioSpec:
    return ScenarioSpec("E02-0001", "E02", "g", {}, gold, [], sufficiency, "test")


def test_row_collapse_on_critical_tier1_is_critical_and_urgent():
    # 20 rows vs same-weekday mean 100: ratio 0.20, shortfall 0.80 -> band 3, +1 critical consumer.
    f = e02.finish_facts(
        make_facts(n_rows=20, tier="tier1", critical=True, batch_rows=[2, 3, 2, 3, 2, 3, 2, 3])
    )
    d = f["derived"]
    assert d["expected_rows"] == 100.0 and d["volume_ratio"] == 0.2
    assert d["batches_low_volume"] == 8  # every batch holds < 0.5 * 12.5 rows
    assert e02.rules_family(d) == "volume_drop"
    assert e02.policy_impact("volume_drop", f) == 4
    assert e02.policy_investigate("volume_drop", 4, f) is True
    assert f["alert"]["rule"] == "row_count_vs_mean" and f["alert"]["observed"] == 0.2


def test_holiday_dip_is_normal_even_though_the_static_alert_fires():
    # 50 rows, calendar factor 0.5: expected 50, ratio 1.0; plain 14-day mean ratio 0.5 trips the monitor.
    f = e02.finish_facts(make_facts(n_rows=50, calendar_factor=0.5, batch_rows=[6, 7, 6, 6, 6, 7, 6, 6]))
    d = f["derived"]
    assert d["expected_rows"] == 50.0 and d["volume_ratio"] == 1.0 and d["naive_ratio"] == 0.5
    assert f["alert"]["rule"] == "row_count_vs_mean"
    assert e02.rules_family(d) == e02.NORMAL
    assert e02.policy_impact(e02.NORMAL, f) == 0
    gold = {"anomaly_family": e02.NORMAL, "quality_impact": 0, "investigate_now": False}
    spec = _spec(gold)
    assert e02.rules_baseline(spec, f) == gold
    assert f["_internal"]["threshold_decidable"] is True


def test_duplicate_replay_policy_depends_on_tier():
    # 120 rows, 100 unique: dup_rate 1/6 (band 2). Three batches replay 4 of 15 rows each.
    rows, uniq = [15, 15, 15, 15, 15, 15, 15, 15], [11, 11, 11, 15, 15, 15, 15, 15]
    kw = {
        "n_rows": 120,
        "uniq": 100,
        "batch_rows": rows,
        "batch_uniq": uniq,
        "same_mean": 120.0,
        "mean_rows": 120.0,
    }
    f2 = e02.finish_facts(make_facts(tier="tier2", **kw))
    assert f2["derived"]["dup_rate"] == round(1 / 6, 6) and f2["derived"]["batches_dup"] == 3
    assert e02.rules_family(f2["derived"]) == "duplicate_spike"
    assert e02.policy_impact("duplicate_spike", f2) == 2
    assert e02.policy_investigate("duplicate_spike", 2, f2) is False
    f1 = e02.finish_facts(make_facts(tier="tier1", **kw))
    assert e02.policy_investigate("duplicate_spike", 2, f1) is True


def test_insufficient_history_is_unconfirmed_but_tier1_alert_is_investigated():
    f = e02.finish_facts(
        make_facts(n_rows=40, hist_days=3, same_days=0, same_mean=None, mean_rows=100.0, tier="tier1")
    )
    d = f["derived"]
    assert d["expected_rows"] is None and d["volume_ratio"] is None and d["mean_shift_sd"] is None
    assert e02.rules_family(d) == e02.NORMAL
    assert f["alert"]["rule"] == "row_count_vs_mean"  # 0.4 of the 3-day mean
    assert e02.policy_impact(e02.NORMAL, f) == 0
    assert e02.policy_investigate(e02.NORMAL, 0, f) is True
    f["catalog"]["tier"] = "tier3"
    assert e02.policy_investigate(e02.NORMAL, 0, f) is False


def test_freshness_bands_and_isolation_rule():
    # lag 1000 min vs SLA 180: ratio 5.56 -> band 3. Two tail batches missing, no critical consumer.
    f = e02.finish_facts(
        make_facts(n_rows=90, lag_s=60_000, same_mean=100.0, batch_rows=[15, 15, 15, 15, 15, 15, 0, 0])
    )
    assert f["derived"]["lag_min"] == 1000 and f["derived"]["batches_missing"] == 2
    assert e02.rules_family(f["derived"]) == "freshness_lag"  # freshness outranks volume in the rule order
    assert e02.policy_impact("freshness_lag", f) == 3
    f["derived"]["batches_missing"] = 1  # a single late batch is isolated: -1
    assert e02.policy_impact("freshness_lag", f) == 2


def test_free_text_holiday_is_not_threshold_decidable():
    f = e02.finish_facts(make_facts(n_rows=45))  # ratio 0.45 with no calendar row
    f["catalog"]["ops_note"] = "regional_holiday"
    gold = {"anomaly_family": e02.NORMAL, "quality_impact": 0, "investigate_now": False}
    out = e02.rules_baseline(_spec(gold), f)
    assert out["anomaly_family"] == "volume_drop"  # thresholds alone are fooled
    assert f["_internal"]["threshold_decidable"] is False


def test_rules_baseline_decidable_flag_requires_sufficient_evidence():
    f = e02.finish_facts(make_facts(n_rows=100))
    gold = {"anomaly_family": e02.NORMAL, "quality_impact": 0, "investigate_now": False}
    assert e02.rules_baseline(_spec(gold, "ambiguous"), f) == gold
    assert f["_internal"]["threshold_decidable"] is False
    e02.rules_baseline(_spec(gold, "sufficient"), f)
    assert f["_internal"]["threshold_decidable"] is True
