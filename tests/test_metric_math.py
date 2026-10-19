"""Hand-computed checks for the evaluation math, cross-checked against sklearn where possible."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import (
    average_precision_score,
    cohen_kappa_score,
    f1_score,
    roc_auc_score,
)

from debench.eval import calibration as C
from debench.eval import cost, latency, robustness
from debench.eval import metrics as M
from debench.eval import paired_statistics as S

LABELS = ["a", "b", "c"]


# ------------------------------------------------------------------ choice

def test_macro_f1_includes_absent_labels_and_matches_sklearn():
    y_true = ["a", "a", "b", "b", "c"]
    y_pred = ["a", "b", "b", "b", "a"]
    # a: tp1 pred2 sup2 -> P .5 R .5 F1 .5 ; b: tp2 pred3 sup2 -> P 2/3 R 1 F1 .8 ; c: tp0 -> 0
    # macro = (.5 + .8 + 0)/3
    assert M.macro_f1(LABELS, y_true, y_pred) == pytest.approx((0.5 + 0.8) / 3)
    assert M.macro_f1(LABELS, y_true, y_pred) == pytest.approx(
        f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0))
    # label d never predicted nor in gold: adds a zero F1, macro = 1.3/4
    assert M.macro_f1(LABELS + ["d"], y_true, y_pred) == pytest.approx(1.3 / 4)


def test_per_class_and_balanced_accuracy():
    y_true = ["a", "a", "b", "b", "c"]
    y_pred = ["a", "b", "b", "b", "a"]
    pc = M.choice_per_class(LABELS, y_true, y_pred)
    assert pc["b"] == pytest.approx({"precision": 2 / 3, "recall": 1.0, "f1": 0.8, "support": 2})
    assert pc["c"]["f1"] == 0.0 and pc["c"]["support"] == 1
    # recalls .5, 1, 0 -> mean 0.5 ; accuracy 3/5
    assert M.balanced_accuracy(LABELS, y_true, y_pred) == pytest.approx(0.5)
    assert M.accuracy(y_true, y_pred) == pytest.approx(0.6)


def test_brier_and_nll_choice():
    probs = [[0.7, 0.2, 0.1], [0.1, 0.6, 0.3]]
    gold = ["a", "b"]
    # item1: .09+.04+.01=.14 ; item2: .01+.16+.09=.26 ; mean .20
    assert M.multiclass_brier(probs, LABELS, gold) == pytest.approx(0.20)
    # -(ln .7 + ln .6)/2
    assert M.choice_nll(probs, LABELS, gold) == pytest.approx(-(math.log(0.7) + math.log(0.6)) / 2)


def test_nll_epsilon_clip():
    # p_gold = 0 is clipped to eps: -ln(1e-12)/2 for two items with one zero
    assert M.nll([1.0, 0.0], eps=1e-12) == pytest.approx(-math.log(1e-12) / 2)
    assert M.nll([1.0, 0.0], eps=1e-6) == pytest.approx(-math.log(1e-6) / 2)


def test_ece_equal_mass_and_width():
    conf = [0.9, 0.9, 0.6, 0.6]
    corr = [1, 0, 1, 1]
    # 2 equal-mass bins: [.6,.6] acc 1 -> gap .4 ; [.9,.9] acc .5 -> gap .4 ; ECE = .5*.4+.5*.4 = .4
    assert M.expected_calibration_error(conf, corr, 2, "mass") == pytest.approx(0.4)
    # width bins [0,.5),[.5,1]: all in the top bin: acc .75, conf .75 -> 0
    assert M.expected_calibration_error(conf, corr, 2, "width") == pytest.approx(0.0)
    # confidence exactly 1.0 lands in the last width bin, perfectly confident+correct -> 0
    assert M.expected_calibration_error([1.0, 1.0], [1, 1], 15, "width") == 0.0
    both = M.ece_both(conf, corr, 2)
    assert set(both) == {"ece_equal_mass", "ece_equal_width"}


def test_choice_metrics_bundle():
    probs = [[0.7, 0.2, 0.1], [0.1, 0.6, 0.3], [0.2, 0.2, 0.6]]
    m = M.choice_metrics(LABELS, ["a", "b", "a"], probs)
    assert m["accuracy"] == pytest.approx(2 / 3)
    assert m["macro_f1"] == pytest.approx(f1_score(["a", "b", "a"], ["a", "b", "c"], labels=LABELS,
                                                    average="macro", zero_division=0))
    assert "ece_equal_mass" in m and "ece_equal_width" in m and m["nll_epsilon"] == 1e-12


# ------------------------------------------------------------------ score

def test_score_expected_value_metrics():
    probs = [[0.5, 0.5, 0.0], [0.0, 0.2, 0.8]]
    gold = [0, 1]
    # EV: .5 and .2*1+.8*2 = 1.8 ; errors .5, .8
    assert M.expected_levels(probs).tolist() == pytest.approx([0.5, 1.8])
    m = M.score_metrics(gold, probs)
    assert m["mae_expected"] == pytest.approx(0.65)
    assert m["rmse_expected"] == pytest.approx(math.sqrt((0.25 + 0.64) / 2))
    # argmax: tie -> level 0 ; level 2. accuracy = 1/2
    assert m["accuracy_argmax"] == pytest.approx(0.5)


def test_rps_hand_computed():
    probs = [[0.5, 0.3, 0.2]]
    # gold 1: cdf_p = [.5, .8], cdf_y = [0, 1] -> .25 + .04 = .29
    assert M.ranked_probability_score(probs, [1]) == pytest.approx(0.29)
    assert M.ranked_probability_score(probs, [1], normalize=True) == pytest.approx(0.145)
    # perfect forecast -> 0
    assert M.ranked_probability_score([[0, 1, 0]], [1]) == 0.0


def test_qwk_matches_sklearn():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 4, 200)
    q = np.clip(y + rng.integers(-1, 2, 200), 0, 3)
    assert M.quadratic_weighted_kappa(y, q, 4) == pytest.approx(
        cohen_kappa_score(y, q, weights="quadratic", labels=[0, 1, 2, 3]))
    assert M.quadratic_weighted_kappa([0, 1, 2], [0, 1, 2], 3) == pytest.approx(1.0)
    # constant predictions on constant gold -> undefined
    assert M.quadratic_weighted_kappa([1, 1], [1, 1], 3) is None


# ------------------------------------------------------------------ noul

def test_auroc_auprc_match_sklearn_with_ties():
    rng = np.random.default_rng(3)
    y = rng.integers(0, 2, 300)
    p = np.round(np.clip(0.3 * y + rng.random(300) * 0.7, 0, 1), 1)  # many ties
    assert M.auroc(y, p) == pytest.approx(roc_auc_score(y, p))
    assert M.auprc(y, p) == pytest.approx(average_precision_score(y, p))


def test_auroc_auprc_hand_and_single_class():
    y, p = [0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]
    # pos-neg pairs: (.35>.1) (.35<.4) (.8>.1) (.8>.4) -> 3/4
    assert M.auroc(y, p) == pytest.approx(0.75)
    # sorted desc: .8(1) .4(0) .35(1) .1(0): P=1 R=.5 ; P=2/3 R=1 -> .5*1 + .5*2/3 = 5/6
    assert M.auprc(y, p) == pytest.approx(5 / 6)
    assert M.auroc([1, 1], [0.2, 0.9]) is None
    assert M.auprc([0, 0], [0.2, 0.9]) is None


def test_noul_threshold_and_brier_logloss():
    y, p = [0, 0, 1, 1], [0.1, 0.6, 0.4, 0.9]
    m = M.noul_metrics(y, p, threshold=0.35)
    # at .5 predicted pos: idx1,3 -> tp1 fp1 fn1 -> P .5 R .5 F1 .5
    assert m["at_0.5"]["f1"] == pytest.approx(0.5)
    # at .35 predicted pos: idx1,2,3 -> tp2 fp1 fn0 -> P 2/3 R 1 F1 .8
    assert m["at_threshold"]["f1"] == pytest.approx(0.8)
    assert m["at_threshold"]["recall"] == 1.0
    assert m["prevalence"] == 0.5
    # brier: (.01 + .36 + .36 + .01)/4 = .185
    assert m["brier"] == pytest.approx(0.185)
    assert m["log_loss"] == pytest.approx(-(math.log(.9) + math.log(.4) + math.log(.4) + math.log(.9)) / 4)
    assert M.noul_metrics(y, p)["at_threshold"] is None


# ------------------------------------------------------------------ coverage & summary

def test_coverage_vs_effective_accuracy():
    c = M.coverage_metrics(10, 8, 6)
    assert c["coverage"] == 0.8
    assert c["effective_accuracy"] == 0.6
    assert c["answered_only_accuracy"] == 0.75
    with pytest.raises(ValueError):
        M.coverage_metrics(5, 6, 1)


def _row(valid, dist=None, label=None, level=None, p=None, err=None):
    return SimpleNamespace(valid=valid, distribution=dist, predicted_label=label, predicted_level=level,
                           noul_probability=p, error_type=err)


def test_summary_invalid_counts_as_failure():
    rows = [_row(True, {"a": 0.8, "b": 0.2}, "a"), _row(True, {"a": 0.3, "b": 0.7}, "b"),
            _row(False, err="timeout"), _row(False, err="bad_sum")]
    s = M.summarize_primitive("choice", rows, ["a", "a", "b", "b"], labels=["a", "b"])
    assert s["n_attempted"] == 4 and s["n_answered"] == 2
    assert s["coverage"] == 0.5
    assert s["effective_accuracy"] == 0.25  # 1 correct / 4 attempted
    assert s["answered_only_accuracy"] == 0.5
    assert s["error_counts"] == {"timeout": 1, "bad_sum": 1}
    assert s["metrics"]["accuracy"] == 0.5


def test_summary_score_and_noul():
    rows = [_row(True, {"0": 0.9, "1": 0.1}, level=0), _row(False, err="x")]
    s = M.summarize_primitive("score", rows, [0, 1])
    assert s["effective_accuracy"] == 0.5 and s["metrics"]["mae_expected"] == pytest.approx(0.1)
    n = M.summarize_primitive("noul", [_row(True, p=0.8), _row(True, p=0.3)], [True, True])
    assert n["effective_accuracy"] == 0.5 and n["metrics"]["prevalence"] == 1.0
    assert n["metrics"]["auroc"] is None


# ------------------------------------------------------------------ calibration

def test_temperature_identity_on_calibrated_data():
    rng = np.random.default_rng(0)
    probs = rng.dirichlet([1.0, 1.0, 1.0], 20000)
    gold = np.array([rng.choice(3, p=p) for p in probs])
    par = C.fit_temperature(probs, gold)
    assert par.temperature == pytest.approx(1.0, abs=0.1)
    assert par.nll_after <= par.nll_before + 1e-9


def test_temperature_detects_overconfidence():
    rng = np.random.default_rng(1)
    probs = rng.dirichlet([1.0, 1.0, 1.0], 20000)
    gold = np.array([rng.choice(3, p=p) for p in probs])
    sharp = C.apply_temperature(probs, 0.5)  # overconfident version
    assert C.fit_temperature(sharp, gold).temperature == pytest.approx(2.0, abs=0.25)


def test_calibration_fit_uses_only_passed_data():
    rng = np.random.default_rng(2)
    p = rng.dirichlet([2, 2, 2], 500)
    y = rng.integers(0, 3, 500)
    full = C.fit_temperature(p, y)
    half = C.fit_temperature(p[:250], y[:250])
    assert full.n_fit == 500 and half.n_fit == 250
    assert half.temperature == C.fit_temperature(p[:250], y[:250]).temperature  # deterministic
    # Changing data outside the fit slice cannot change the fit
    p2, y2 = p.copy(), y.copy()
    p2[250:], y2[250:] = p[:250], (y[:250] + 1) % 3
    assert C.fit_temperature(p2[:250], y2[:250]).temperature == half.temperature


def test_temperature_apply_normalizes_and_preserves_argmax():
    p = np.array([[0.7, 0.2, 0.1], [0.0, 0.5, 0.5]])
    q = C.apply_temperature(p, 3.0)
    assert q.sum(axis=1) == pytest.approx([1.0, 1.0])
    assert q.argmax(axis=1).tolist() == [0, 1]
    # T=1 reproduces the (eps-clipped) input
    assert C.apply_temperature(p, 1.0) == pytest.approx(p, abs=1e-9)


def test_platt_recovers_identity_and_distortion():
    rng = np.random.default_rng(4)
    p = rng.random(20000)
    y = (rng.random(20000) < p).astype(int)
    par = C.fit_platt(p, y)
    assert par.a == pytest.approx(1.0, abs=0.1) and par.b == pytest.approx(0.0, abs=0.1)
    with pytest.raises(ValueError):
        C.fit_platt([0.2, 0.3], [0, 0])
    out = C.apply_platt([0.0, 1.0], par)  # bounded epsilon -> finite, inside (0, 1)
    assert np.all((out > 0) & (out < 1))


def test_abstention_threshold_hand_example():
    conf = [0.9, 0.8, 0.7, 0.6]
    corr = [1, 1, 0, 1]
    # accepting top k: risks 0, 0, 1/3, 1/4 ; target .0 -> top 2, threshold .8
    par = C.fit_abstention_threshold(conf, corr, 0.0)
    assert par.threshold == 0.8 and par.feasible and par.cal_coverage == 0.5
    # target .25 -> all four are feasible (risk .25)
    assert C.fit_abstention_threshold(conf, corr, 0.25).threshold == 0.6
    assert C.apply_abstention([0.85, 0.5], par).tolist() == [True, False]
    # infeasible
    bad = C.fit_abstention_threshold([0.9, 0.8], [0, 0], 0.1)
    assert not bad.feasible and bad.threshold == math.inf


def test_risk_coverage_curve_and_groups():
    rc = C.risk_coverage_curve([0.9, 0.9, 0.5], [1, 0, 1])
    assert rc["coverage"].tolist() == pytest.approx([2 / 3, 1.0])  # tie handled as one step
    assert rc["risk"].tolist() == pytest.approx([0.5, 1 / 3])
    groups = [("m", "en", "choice")] * 2 + [("m", "th", "choice")] * 2
    fit = C.fit_group_abstention(groups, [0.9, 0.5, 0.9, 0.5], [1, 0, 1, 1], 0.0)
    assert fit[("m", "en", "choice")].threshold == 0.9
    assert fit[("m", "th", "choice")].threshold == 0.5
    with pytest.raises(KeyError):
        C.apply_group_abstention(fit, [("x", "en", "choice")], [0.9])


def test_cost_threshold_selection():
    p = [0.1, 0.2, 0.8, 0.9]
    y = [0, 1, 1, 1]
    # false allow costs 10, review costs 1. Options (mean cost over 4):
    # t=.1 review all: 1 unnecessary -> .25 ; t=.2: review 3, 1 FA? y at .1 is 0 -> 0 FA -> 0 cost
    par = C.fit_cost_threshold(p, y, 10.0, 1.0)
    assert par.threshold == 0.2 and par.cal_expected_cost == 0.0
    # if review is very expensive, allowing everything past the risky one can win
    par2 = C.fit_cost_threshold(p, y, 1.0, 100.0)
    assert par2.threshold == 0.2 or par2.threshold == math.inf
    ev = C.evaluate_cost_threshold([0.15, 0.7], [1, 1], par)
    assert ev["false_auto_allow"] == 1 and ev["false_negative_rate_after_gating"] == 0.5


def test_raw_and_calibrated_reported_together():
    rng = np.random.default_rng(5)
    probs = rng.dirichlet([1, 1, 1], 400)
    gold = [LABELS[rng.choice(3, p=p)] for p in probs]
    out = C.raw_and_calibrated_choice(LABELS, probs[:200], gold[:200], probs[200:], gold[200:])
    assert set(out) == {"params", "raw", "calibrated"}
    assert out["params"]["n_fit"] == 200
    yn = (rng.random(400) < 0.5).astype(int)
    out2 = C.raw_and_calibrated_noul(rng.random(200), yn[:200], rng.random(200), yn[200:])
    assert set(out2) == {"params", "raw", "calibrated"}


# ------------------------------------------------------------------ paired statistics

def _paired_data():
    rng = np.random.default_rng(7)
    ids = np.repeat(np.arange(60), 2)  # 2 questions per scenario
    a = rng.random(120) < 0.8
    b = rng.random(120) < 0.6
    return ids, a.astype(float), b.astype(float)


def test_bootstrap_deterministic_and_contains_point():
    ids, a, b = _paired_data()
    run = lambda seed: S.paired_bootstrap_diff(  # noqa: E731
        ids, ids, S.bind(np.mean, a), S.bind(np.mean, b), seed=seed)
    r1, r2, r3 = run(11), run(11), run(12)
    assert r1 == r2 and (r1.ci_low, r1.ci_high) != (r3.ci_low, r3.ci_high)
    assert r1.estimate == pytest.approx(a.mean() - b.mean())
    assert r1.ci_low <= r1.estimate <= r1.ci_high
    assert r1.n_scenarios == 60 and r1.n_resamples >= 2000 and r1.seed == 11
    with pytest.raises(ValueError):
        S.paired_bootstrap_diff(ids, ids, S.bind(np.mean, a), S.bind(np.mean, b), n_resamples=100)


def test_bootstrap_clusters_by_scenario_not_item():
    # Two items per scenario are identical; clustering must give a wider CI than item-level iid.
    rng = np.random.default_rng(9)
    base = (rng.random(50) < 0.5).astype(float)
    a = np.repeat(base, 2)
    ids = np.repeat(np.arange(50), 2)
    clustered = S.bootstrap_ci(ids, S.bind(np.mean, a))
    iid = S.bootstrap_ci(np.arange(100), S.bind(np.mean, a))
    assert clustered.ci_high - clustered.ci_low > iid.ci_high - iid.ci_low


def test_bootstrap_accepts_arbitrary_metric_and_language_gap():
    rng = np.random.default_rng(10)
    labels = ["a", "b"]
    gold = rng.choice(labels, 80)
    pred_en = np.where(rng.random(80) < 0.9, gold, "a")
    pred_th = np.where(rng.random(80) < 0.6, gold, "a")
    ids = np.arange(80)
    f = lambda t, p: M.macro_f1(labels, list(t), list(p))  # noqa: E731
    r = S.language_gap(ids, ids, S.bind(f, gold, pred_th), S.bind(f, gold, pred_en))
    assert r.estimate == pytest.approx(f(gold, pred_th) - f(gold, pred_en))
    assert r.estimate < 0 and r.ci_low <= r.estimate <= r.ci_high
    # unmatched ids are dropped
    r2 = S.language_gap(ids, ids[:40], S.bind(np.mean, np.ones(80)), S.bind(np.mean, np.ones(40)))
    assert r2.n_scenarios == 40


def test_mcnemar_exact_known_table():
    # b=1, c=7 -> d=8 <25 exact: p = 2*P(X<=1 | n=8) = 2*(1+8)/256 = 18/256
    a = [True] * 1 + [False] * 7 + [True] * 10
    b = [False] * 1 + [True] * 7 + [True] * 10
    r = S.mcnemar(a, b)
    assert (r["b"], r["c"], r["method"]) == (1, 7, "exact")
    assert r["p_value"] == pytest.approx(18 / 256)
    assert S.mcnemar([True, False], [True, False])["p_value"] == 1.0
    # balanced discordance caps at 1
    assert S.mcnemar([True, False], [False, True])["p_value"] == 1.0


def test_mcnemar_chi_square_branch():
    # b=30, c=10 -> chi2 = (20-1)^2/40 = 9.025
    a = [True] * 30 + [False] * 10
    b = [False] * 30 + [True] * 10
    r = S.mcnemar(a, b)
    assert r["method"] == "chi2_cc" and r["statistic"] == pytest.approx(9.025)
    from scipy.stats import chi2
    assert r["p_value"] == pytest.approx(chi2.sf(9.025, 1))


def test_holm_known_example():
    # p = [.01, .04, .03, .005], m=4. sorted .005,.01,.03,.04 -> x4,x3,x2,x1 = .02,.03,.06,.04
    # monotone max: .02,.03,.06,.06 ; back to input order [.03,.06,.06,.02]
    h = S.holm_bonferroni([0.01, 0.04, 0.03, 0.005])
    assert h["adjusted"] == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert h["reject"] == [True, False, False, True]
    # .9*2 = 1.8 -> capped 1.0 ; monotone max keeps the next at 1.0
    assert S.holm_bonferroni([0.9, 0.99])["adjusted"] == pytest.approx([1.0, 1.0])


def test_zero_event_bounds():
    assert S.rule_of_three(100) == pytest.approx(0.03)
    assert S.rule_of_three(2) == 1.0  # capped at 1
    # exact: 1 - 0.05^(1/100)
    assert S.clopper_pearson_upper(0, 100) == pytest.approx(1 - 0.05 ** 0.01)
    assert S.clopper_pearson_upper(0, 100) == pytest.approx(0.02951, abs=1e-4)
    assert S.clopper_pearson_upper(3, 3) == 1.0
    lo, hi = S.wilson_interval(0, 100)
    # z=1.959964: upper = z^2/(n+z^2) = 3.8415/103.8415
    assert lo == pytest.approx(0.0, abs=1e-12) and hi == pytest.approx(3.841459 / 103.841459, rel=1e-5)
    z = S.zero_event_bounds(50)
    assert z["rule_of_three"] == pytest.approx(3 / 50) and z["events"] == 0
    # known Wilson value k=5,n=10 symmetric around .5
    l2, h2 = S.wilson_interval(5, 10)
    assert l2 + h2 == pytest.approx(1.0)


# ------------------------------------------------------------------ robustness

def test_robustness_hand_computed():
    assert robustness.choice_flip_rate(["a", "b", "c", None], ["a", "c", "c", "a"]) == pytest.approx(1 / 3)
    # TVD: .5*(|.7-.5| + |.2-.3| + |.1-.2|) = .5*.4 = .2 ; reindexed by key, order irrelevant
    p = {"a": 0.7, "b": 0.2, "c": 0.1}
    q = {"c": 0.2, "a": 0.5, "b": 0.3}
    assert robustness.tvd(p, q) == pytest.approx(0.2)
    assert robustness.mean_tvd([p, p], [q, p]) == pytest.approx(0.1)
    assert robustness.kl_divergence(p, p) == pytest.approx(0.0)
    s = robustness.score_ev_shift([1.0, 2.0], [1.5, 1.5])
    assert s["mean_abs_shift"] == pytest.approx(0.5) and s["mean_signed_shift"] == pytest.approx(0.0)
    assert robustness.noul_abs_shift([0.2, 0.9], [0.4, 0.5]) == pytest.approx(0.3)
    # |P(not X) - (1 - P(X))|: |.3-.2| = .1 ; |.1-.1| = 0
    g = robustness.negation_gap([0.8, 0.9], [0.3, 0.1])
    assert g.tolist() == pytest.approx([0.1, 0.0])
    assert robustness.negation_consistency([0.8, 0.9], [0.3, 0.1])["mean_gap"] == pytest.approx(0.05)


# ------------------------------------------------------------------ latency

def test_percentiles_linear_interpolation():
    # numpy linear: p50 of 1..5 = 3 ; p95 position .95*4 = 3.8 -> 4.8 ; p99 -> 4.96
    p = latency.percentiles([1, 2, 3, 4, 5])
    assert p == pytest.approx({"p50": 3.0, "p95": 4.8, "p99": 4.96})


def test_warm_cold_separation_and_group_summary():
    df = pd.DataFrame({
        "model": ["m"] * 6 + ["n"] * 2,
        "latency_ms": [1000, 10, 20, 30, 999, 15, 5, 7],
        "success": [True, True, True, True, False, True, True, True],
        "is_cold": [True, False, False, False, False, False, False, False],
        "start_s": [0, 1, 2, 3, 4, 5, 0, 1],
        "end_s": [1, 2, 3, 4, 5, 6, 1, 2],
    })
    s = latency.summarize_latency(df, ["model"]).set_index("model")
    m = s.loc["m"]
    # cold 1000 excluded from warm; failed 999 excluded: warm = [10, 20, 30, 15]
    assert m["n_warm"] == 4 and m["n_cold"] == 1
    assert m["warm_p50_ms"] == pytest.approx(17.5)
    assert m["cold_p50_ms"] == 1000
    assert m["success_rate"] == pytest.approx(5 / 6)
    # warm window: start 1 -> end 6 = 5 s, 4 successes
    assert m["throughput_per_s"] == pytest.approx(4 / 5)
    assert s.loc["n", "warm_p50_ms"] == pytest.approx(6.0)
    assert latency.latency_stats([])["success_rate"] != latency.latency_stats([])["success_rate"]  # NaN


# ------------------------------------------------------------------ cost

def test_cost_unit_is_per_million():
    # 2,000,000 tokens at $0.042 per million = $0.084 (NOT per thousand: that would be $84)
    assert cost.remote_cost_usd(2_000_000, 0.042) == pytest.approx(0.084)
    assert cost.remote_cost_usd(1_000, 0.042) == pytest.approx(4.2e-5)


def test_remote_summary_questions_vs_scenarios_and_estimate_flag():
    s = cost.remote_cost_summary(2_000_000, 0.042, n_successful_questions=600, n_successful_scenarios=200)
    # $0.084 / 600 * 1000 = 0.14 ; / 200 * 1000 = 0.42
    assert s.usd_per_1000_questions == pytest.approx(0.14)
    assert s.usd_per_1000_scenario_decisions == pytest.approx(0.42)
    assert s.is_estimate is False
    assert cost.remote_cost_summary(1e6, 0.042, 10, 5, tokens_measured=False).is_estimate is True
    assert cost.remote_cost_summary(1e6, 0.042, 0, 0).usd_per_1000_questions is None


def test_local_cost_and_monthly():
    # $2/h, 4000 decisions/h -> 2 / (4000/1000) = $0.50 per 1000
    assert cost.local_cost_per_1000(2.0, 4000) == pytest.approx(0.5)
    assert cost.local_cost_per_1000(2.0, 0) == math.inf
    s = cost.local_cost_summary(1.5, 4000, questions_per_scenario=2, energy_usd_per_hour=0.5)
    assert s.usd_per_1000_questions == pytest.approx(0.5)
    assert s.usd_per_1000_scenario_decisions == pytest.approx(1.0)
    half = cost.local_cost_summary(2.0, 4000, 1, utilization=0.5)
    assert half.usd_per_1000_questions == pytest.approx(1.0)
    # 0.14 USD/1000 * 5,000,000 / 1000 = 700 ; always-on 2 USD/h * 730 = 1460
    assert cost.monthly_projection(0.14, 5_000_000) == pytest.approx(700.0)
    assert cost.always_on_monthly_usd(2.0) == pytest.approx(1460.0)
