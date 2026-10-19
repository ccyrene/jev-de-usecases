"""Turn run predictions into the PLAN section 12 result tables.

Head-to-head metrics are computed on the *attempted* denominator: an invalid answer or provider
failure counts as incorrect for accuracy/macro-F1 and is reported as coverage loss, never dropped.
Probability metrics (Brier, NLL, ECE, AUROC, MAE) use valid answers only and always appear next
to coverage. Mock rows are rejected.

    python -m debench.analyze --runs pilot_laya,pilot_jev --out results/analysis/pilot
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import yaml

from debench.config import CONFIG_DIR, ROOT
from debench.eval import metrics as M
from debench.eval.paired_statistics import bind, bootstrap_ci, mcnemar, paired_bootstrap_diff
from debench.questions import load_question_set
from debench.runner import RUNS_DIR

INVALID = "__invalid__"
HEAD_TO_HEAD = ("laya_routed", "jev_114")
PRIMARY_VARIANTS = ("en_en", "th_th")


class MockRowsError(RuntimeError):
    pass


def load_predictions(run_names: list[str], allow_mock: bool = False) -> pd.DataFrame:
    frames = []
    for name in run_names:
        path = RUNS_DIR / name / "predictions.jsonl"
        if path.exists():
            frames.append(pd.read_json(path, lines=True, dtype=False))
    if not frames:
        raise FileNotFoundError(f"no predictions for runs {run_names}")
    df = pd.concat(frames, ignore_index=True)
    kinds = {t: cfg["provider"] for t, cfg in yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())["tracks"].items()}
    df["provider"] = df["model_track"].map(kinds).fillna(df["provider"])
    mock = df["is_mock"].fillna(False).astype(bool) | (df["provider"] == "mock")
    if mock.any() and not allow_mock:
        raise MockRowsError(f"{int(mock.sum())} mock rows in benchmark aggregation")
    return df


def _rows(sub: pd.DataFrame) -> list[SimpleNamespace]:
    out = []
    for r in sub.itertuples(index=False):
        dist = json.loads(r.distribution_json) if isinstance(r.distribution_json, str) else None
        out.append(
            SimpleNamespace(
                valid=bool(r.valid_answer),
                error_type=r.error_type,
                distribution=dist,
                predicted_label=r.predicted_label,
                predicted_level=None if pd.isna(r.predicted_level) else int(r.predicted_level),
                score_value=None if pd.isna(r.score_value) else float(r.score_value),
                noul_probability=None if pd.isna(r.noul_probability) else float(r.noul_probability),
            )
        )
    return out


def _correct(sub: pd.DataFrame, primitive: str) -> np.ndarray:
    valid = sub["valid_answer"].astype(bool).to_numpy()
    if primitive == "choice":
        hit = (sub["predicted_label"] == sub["gold_label"]).to_numpy()
    elif primitive == "score":
        hit = (sub["predicted_level"] == sub["gold_level"]).to_numpy()
    else:
        hit = ((sub["noul_probability"] >= 0.5) == sub["gold_binary"].astype(bool)).to_numpy()
    return (valid & hit).astype(float)


def _attempted_pred(sub: pd.DataFrame) -> np.ndarray:
    pred = sub["predicted_label"].where(sub["valid_answer"].astype(bool), INVALID)
    return pred.fillna(INVALID).to_numpy(dtype=object)


def _ci(ids: np.ndarray, fn) -> dict[str, float]:
    if len(set(ids)) < 2:
        return {}
    res = bootstrap_ci(list(ids), fn)
    return {"ci_low": res.ci_low, "ci_high": res.ci_high}


def quality_table(df: pd.DataFrame) -> pd.DataFrame:
    """Table 1: model x case x variant x question -> metrics, CI, N, coverage, failure rate."""
    out = []
    keys = ["split", "protocol", "context_level", "model_track", "experiment_id", "variant", "question_id", "primitive"]
    for (split, protocol, context, track, case, variant, qid, primitive), sub in df.groupby(keys, sort=True):
        qset = load_question_set(case)
        q = qset.questions[qid]
        ids = sub["scenario_id"].to_numpy()
        correct = _correct(sub, primitive)
        row: dict[str, Any] = {
            "split": split,
            "protocol": protocol,
            "context_level": context,
            "model_track": track,
            "experiment_id": case,
            "variant": variant,
            "question_id": qid,
            "primitive": primitive,
            "n_attempted": len(sub),
            "coverage": float(sub["valid_answer"].mean()),
            "failure_rate": float((~sub["valid_answer"].astype(bool)).mean()),
            "truncation_rate": float(sub.get("was_truncated", pd.Series(False, index=sub.index)).fillna(False).mean()),
            "effective_accuracy": float(correct.mean()),
        }
        row.update({f"effective_accuracy_{k}": v for k, v in _ci(ids, bind(np.mean, correct)).items()})
        if primitive == "choice":
            labels = q.labels()
            gold, pred = sub["gold_label"].to_numpy(dtype=object), _attempted_pred(sub)
            row["macro_f1"] = M.macro_f1(labels, gold, pred)
            row.update({f"macro_f1_{k}": v for k, v in _ci(ids, bind(lambda g, p: M.macro_f1(labels, g, p), gold, pred)).items()})
            row["balanced_accuracy"] = M.balanced_accuracy(labels, gold, pred)
        has_probs = sub["distribution_json"].notna().any() if "distribution_json" in sub else False
        if has_probs:
            gold_vals = sub[{"choice": "gold_label", "score": "gold_level", "noul": "gold_binary"}[primitive]].tolist()
            summary = M.summarize_primitive(
                primitive,
                _rows(sub),
                gold_vals,
                labels=q.labels() if primitive == "choice" else None,
                n_levels=q.n_levels() if primitive == "score" else None,
            )
            flat = {**summary, **summary.get("metrics", {})}
            for key in ("brier", "nll", "ece_equal_mass", "ece_equal_width", "mae_expected", "rmse_expected", "qwk",
                        "accuracy_argmax", "auroc", "auprc", "log_loss", "prevalence", "answered_only_accuracy"):
                if key in flat and not isinstance(flat[key], dict):
                    row[key] = flat[key]
        out.append(row)
    return pd.DataFrame(out)


def _paired(sub_a: pd.DataFrame, sub_b: pd.DataFrame, primitive: str, labels: list[str] | None) -> list[dict[str, Any]]:
    a = sub_a.sort_values("scenario_id")
    b = sub_b.sort_values("scenario_id")
    common = sorted(set(a["scenario_id"]) & set(b["scenario_id"]))
    a, b = a[a["scenario_id"].isin(common)], b[b["scenario_id"].isin(common)]
    if len(common) < 2:
        return []
    ca, cb = _correct(a, primitive), _correct(b, primitive)
    res = [("effective_accuracy", paired_bootstrap_diff(list(a["scenario_id"]), list(b["scenario_id"]), bind(np.mean, ca), bind(np.mean, cb)))]
    if primitive == "choice" and labels:
        ga, pa = a["gold_label"].to_numpy(dtype=object), _attempted_pred(a)
        gb, pb = b["gold_label"].to_numpy(dtype=object), _attempted_pred(b)
        f1 = lambda g, p: M.macro_f1(labels, g, p)  # noqa: E731
        res.append(("macro_f1", paired_bootstrap_diff(list(a["scenario_id"]), list(b["scenario_id"]), bind(f1, ga, pa), bind(f1, gb, pb))))
    rows = []
    mc = mcnemar(ca.astype(bool), cb.astype(bool))
    for metric, r in res:
        rows.append({"metric": metric, "n_scenarios": r.n_scenarios, "a": r.metric_a, "b": r.metric_b,
                     "delta": r.estimate, "ci_low": r.ci_low, "ci_high": r.ci_high,
                     "n_resamples": r.n_resamples, "seed": r.seed,
                     "mcnemar_p": mc.get("p_value"), "discordant": mc.get("discordant", mc.get("n_discordant"))})
    return rows


def paired_deltas(df: pd.DataFrame, track_a: str = HEAD_TO_HEAD[0], track_b: str = HEAD_TO_HEAD[1]) -> pd.DataFrame:
    """Table 2: per-case paired A - B within each language variant."""
    out = []
    for (split, protocol, context, case, variant, qid, primitive), sub in df.groupby(
        ["split", "protocol", "context_level", "experiment_id", "variant", "question_id", "primitive"]
    ):
        sa, sb = sub[sub["model_track"] == track_a], sub[sub["model_track"] == track_b]
        if sa.empty or sb.empty:
            continue
        labels = load_question_set(case).questions[qid].labels() if primitive == "choice" else None
        for row in _paired(sa, sb, primitive, labels):
            out.append({"split": split, "protocol": protocol, "context_level": context, "experiment_id": case,
                        "variant": variant, "question_id": qid, "primitive": primitive,
                        "track_a": track_a, "track_b": track_b, **row})
    return pd.DataFrame(out)


def language_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Table 3: within-model th_th - en_en, paired by scenario."""
    out = []
    for (split, protocol, context, track, case, qid, primitive), sub in df.groupby(
        ["split", "protocol", "context_level", "model_track", "experiment_id", "question_id", "primitive"]
    ):
        th, en = sub[sub["variant"] == "th_th"], sub[sub["variant"] == "en_en"]
        if th.empty or en.empty:
            continue
        labels = load_question_set(case).questions[qid].labels() if primitive == "choice" else None
        for row in _paired(th, en, primitive, labels):
            out.append({"split": split, "protocol": protocol, "context_level": context, "model_track": track,
                        "experiment_id": case, "question_id": qid, "primitive": primitive, "gap": "th_th - en_en", **row})
    return pd.DataFrame(out)


def latency_table(df: pd.DataFrame) -> pd.DataFrame:
    """Table 5: per-request model latency (warm, uncached, successful), by track/device/variant."""
    req = df.drop_duplicates(["model_track", "scenario_id", "variant"])
    req = req[(req["provider"] != "rules") & (~req.get("cached", pd.Series(False, index=req.index)).fillna(False).astype(bool))]
    req = req[req["error_type"].isna() | req["valid_answer"].astype(bool)]
    out = []
    for (track, device, variant), sub in req.groupby(["model_track", req["device"].fillna("remote"), "variant"]):
        w = sub["model_wall_ms"].dropna().to_numpy()
        if len(w) == 0:
            continue
        out.append({"model_track": track, "device": device, "variant": variant, "n_requests": len(w),
                    "p50_ms": float(np.percentile(w, 50)), "p95_ms": float(np.percentile(w, 95)),
                    "p99_ms": float(np.percentile(w, 99)), "mean_ms": float(w.mean()),
                    "db_retrieval_p50_ms": float(sub["db_retrieval_ms"].median()) if sub["db_retrieval_ms"].notna().any() else None,
                    "topology": "in_process_local" if device != "remote" else "remote_https_from_this_host"})
    return pd.DataFrame(out)


def cost_table(df: pd.DataFrame, local_hourly_usd: list[float]) -> pd.DataFrame:
    """Table 6: remote cost from measured billed input tokens; local cost only under stated hourly prices."""
    price = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())["jev"]["usd_per_million_input_tokens"]
    req = df.drop_duplicates(["model_track", "scenario_id", "variant"])
    out = []
    for track, sub in req.groupby("model_track"):
        n_req = len(sub)
        n_q = int(df[df["model_track"] == track]["valid_answer"].sum())
        provider = sub["provider"].iloc[0]
        if provider == "jev":
            tokens = sub.loc[~sub.get("cached", False).astype(bool), "input_token_count"].fillna(0).sum()
            usd = tokens / 1e6 * price
            out.append({"model_track": track, "basis": "measured billed input tokens", "requests": n_req,
                        "input_tokens": int(tokens), "usd_total": usd,
                        "usd_per_1000_scenario_decisions": usd / n_req * 1000 if n_req else None,
                        "usd_per_1000_valid_questions": usd / n_q * 1000 if n_q else None,
                        "price_usd_per_million_input_tokens": price})
        elif provider == "laya":
            wall_s = sub["model_wall_ms"].dropna().sum() / 1000.0
            per_hour = n_req / wall_s * 3600 if wall_s else None
            for hourly in local_hourly_usd:
                out.append({"model_track": track, "basis": f"ASSUMED host price {hourly} USD/h, sequential warm throughput",
                            "requests": n_req, "sustained_decisions_per_hour": per_hour,
                            "usd_per_1000_scenario_decisions": hourly / (per_hour / 1000) if per_hour else None})
    return pd.DataFrame(out)


def rules_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Table 8: where the deterministic baseline wins, ties or loses (same items)."""
    out = []
    rules = df[df["model_track"] == "rules_only"]
    if rules.empty:
        return pd.DataFrame()
    for (case, variant, qid, primitive), sub in df.groupby(["experiment_id", "variant", "question_id", "primitive"]):
        r = sub[sub["model_track"] == "rules_only"].set_index("scenario_id")
        if r.empty:
            continue
        rc = pd.Series(_correct(r.reset_index(), primitive), index=r.index)
        for track, m in sub[sub["model_track"] != "rules_only"].groupby("model_track"):
            m = m.set_index("scenario_id")
            common = rc.index.intersection(m.index)
            mc = pd.Series(_correct(m.reset_index(), primitive), index=m.index).loc[common]
            rr = rc.loc[common]
            abstain = ~r.loc[common, "valid_answer"].astype(bool)
            out.append({"experiment_id": case, "variant": variant, "question_id": qid, "model_track": track,
                        "n": len(common), "rules_coverage": float(1 - abstain.mean()),
                        "rules_acc": float(rr.mean()), "model_acc": float(mc.mean()),
                        "rules_only_correct": int(((rr == 1) & (mc == 0)).sum()),
                        "model_only_correct": int(((rr == 0) & (mc == 1)).sum()),
                        "model_acc_where_rules_abstain": float(mc[abstain].mean()) if abstain.any() else None})
    return pd.DataFrame(out)


HIGH_RISK = {
    # (case, question) -> (gold condition description, predicate on rows)
    ("E01", "severity"): "severe incident (gold >= 2) predicted < 2",
    ("E03", "drift_impact"): "breaking change predicted non-breaking",
    ("E04", "impact_tier"): "critical downstream impact missed",
    ("E05", "data_class"): "sensitive/direct identifier predicted non-personal",
    ("E06", "candidate_verdict"): "unsafe or wrong SQL accepted as correct",
}


def high_risk_failures(df: pd.DataFrame) -> pd.DataFrame:
    """Table 7: safety-relevant misses with zero-event bounds."""
    from debench.eval.paired_statistics import zero_event_bounds

    out = []
    for (case, qid), desc in HIGH_RISK.items():
        sub = df[(df["experiment_id"] == case) & (df["question_id"] == qid)]
        if sub.empty:
            continue
        for (track, variant), s in sub.groupby(["model_track", "variant"]):
            if qid == "severity":
                pos = s["gold_level"] >= 2
                miss = pos & ~(s["valid_answer"].astype(bool) & (s["predicted_level"] >= 2))
            elif qid == "drift_impact":
                pos = s["gold_label"].isin(["breaking_rename_drop", "type_or_cast_break", "constraint_or_nullability_break"])
                miss = pos & s["predicted_label"].isin(["non_breaking_addition", "no_change_or_unknown"])
            elif qid == "impact_tier":
                pos = s["gold_label"] == "critical_downstream"
                miss = pos & s["predicted_label"].isin(["none", "single_noncritical", "multi_noncritical"])
            elif qid == "data_class":
                pos = s["gold_label"].isin(["sensitive_personal", "direct_identifier"])
                miss = pos & (s["predicted_label"] == "non_personal")
            else:
                pos = s["gold_label"].isin(["unsafe_or_unexecutable", "semantically_wrong"])
                miss = pos & (s["predicted_label"] == "semantically_correct")
            n_pos, k = int(pos.sum()), int(miss.sum())
            row = {"experiment_id": case, "question_id": qid, "model_track": track, "variant": variant,
                   "definition": desc, "n_positive": n_pos, "misses": k,
                   "miss_rate": k / n_pos if n_pos else None,
                   "invalid_on_positive": int((pos & ~s["valid_answer"].astype(bool)).sum())}
            if n_pos and k == 0:
                row["upper_95_if_zero"] = zero_event_bounds(n_pos)["clopper_pearson_upper"]
            out.append(row)
    return pd.DataFrame(out)


UNKNOWN_LABELS = {"other_unknown", "normal_or_unknown", "no_change_or_unknown", "unknown", "insufficient_specification",
                  "unsupported_or_ambiguous"}


def _dist(v: Any) -> dict[str, float] | None:
    return json.loads(v) if isinstance(v, str) else None


def robustness_table(df: pd.DataFrame) -> pd.DataFrame:
    """P5: choice-option permutations vs canonical order (same scenario, same language)."""
    from debench.eval.robustness import choice_flip_rate, mean_tvd

    p5 = df[(df["protocol"] == "P5_order") & (df["primitive"] == "choice")]
    out = []
    for (track, case, qid, lang), sub in p5.groupby(["model_track", "experiment_id", "question_id", "language"]):
        base = sub[sub["variant"] == f"{lang}_{lang}"].set_index("scenario_id")
        for variant, perm in sub[sub["variant"] != f"{lang}_{lang}"].groupby("variant"):
            perm = perm.set_index("scenario_id")
            ids = base.index.intersection(perm.index)
            ok = base.loc[ids, "valid_answer"].astype(bool) & perm.loc[ids, "valid_answer"].astype(bool)
            ids = ids[ok.to_numpy()]
            out.append({"model_track": track, "experiment_id": case, "question_id": qid, "language": lang,
                        "variant": variant, "n": len(ids),
                        "flip_rate": choice_flip_rate(base.loc[ids, "predicted_label"].tolist(), perm.loc[ids, "predicted_label"].tolist()),
                        "mean_tvd": mean_tvd([_dist(x) for x in base.loc[ids, "distribution_json"]],
                                             [_dist(x) for x in perm.loc[ids, "distribution_json"]]),
                        "acc_base": float((base.loc[ids, "predicted_label"] == base.loc[ids, "gold_label"]).mean()),
                        "acc_perm": float((perm.loc[ids, "predicted_label"] == perm.loc[ids, "gold_label"]).mean())})
    return pd.DataFrame(out)


def calibration_table(cal: pd.DataFrame, test: pd.DataFrame, target_risk: float = 0.2) -> pd.DataFrame:
    """P6: fit on the calibration split only, report raw and calibrated metrics on test."""
    from debench.eval import calibration as C

    out = []
    keys = ["model_track", "experiment_id", "variant", "question_id", "primitive"]
    for (track, case, variant, qid, primitive), t in test[test["protocol"] == "primary"].groupby(keys):
        c = cal[(cal["model_track"] == track) & (cal["experiment_id"] == case) & (cal["variant"] == variant)
                & (cal["question_id"] == qid) & cal["valid_answer"].astype(bool)]
        t = t[t["valid_answer"].astype(bool)]
        if c.empty or t.empty or t["distribution_json"].isna().all():
            continue
        q = load_question_set(case).questions[qid]
        row: dict[str, Any] = {"model_track": track, "experiment_id": case, "variant": variant, "question_id": qid,
                               "primitive": primitive, "n_cal": len(c), "n_test": len(t)}
        if primitive in ("choice", "score"):
            labels = q.labels() if primitive == "choice" else [str(i) for i in range(q.n_levels())]
            gcol = "gold_label" if primitive == "choice" else "gold_level"
            cp = np.array([[_dist(x)[k] for k in labels] for x in c["distribution_json"]])
            tp = np.array([[_dist(x)[k] for k in labels] for x in t["distribution_json"]])
            cg = [str(int(g)) if primitive == "score" else g for g in c[gcol]]
            tg = [str(int(g)) if primitive == "score" else g for g in t[gcol]]
            res = C.raw_and_calibrated_choice(labels, cp, cg, tp, tg)
            row["temperature"] = res["params"]["temperature"]
            conf_cal = C.apply_temperature(cp, res["params"]["temperature"]).max(axis=1)
            corr_cal = np.array(labels)[cp.argmax(axis=1)] == np.array(cg)
            conf_test = C.apply_temperature(tp, res["params"]["temperature"]).max(axis=1)
            corr_test = np.array(labels)[tp.argmax(axis=1)] == np.array(tg)
        else:
            cy, ty = c["gold_binary"].astype(int).to_numpy(), t["gold_binary"].astype(int).to_numpy()
            if cy.min() == cy.max():
                continue
            res = C.raw_and_calibrated_noul(c["noul_probability"], cy, t["noul_probability"], ty)
            row.update({f"platt_{k}": v for k, v in res["params"].items() if k in ("a", "b")})
            cp_cal = C.apply_platt(c["noul_probability"], C.fit_platt(c["noul_probability"], cy))
            conf_cal, corr_cal = np.maximum(cp_cal, 1 - cp_cal), (cp_cal >= 0.5) == (cy == 1)
            tp_cal = C.apply_platt(t["noul_probability"], C.fit_platt(c["noul_probability"], cy))
            conf_test, corr_test = np.maximum(tp_cal, 1 - tp_cal), (tp_cal >= 0.5) == (ty == 1)
        for stage in ("raw", "calibrated"):
            m = res[stage]
            for k in ("brier", "nll", "log_loss", "ece_equal_mass", "ece_equal_width", "accuracy"):
                if k in m and m[k] is not None:
                    row[f"{stage}_{k}"] = m[k]
        ab = C.fit_abstention_threshold(conf_cal, corr_cal, target_risk)
        accepted = conf_test >= ab.threshold
        row.update({"target_risk": target_risk, "abstain_threshold": ab.threshold, "abstain_feasible": ab.feasible,
                    "test_coverage_at_target": float(accepted.mean()),
                    "test_risk_at_target": float(1 - corr_test[accepted].mean()) if accepted.any() else None})
        out.append(row)
    return pd.DataFrame(out)


def routing_table(df: pd.DataFrame) -> pd.DataFrame:
    """P3: which checkpoint the router picked, and how often it disagreed with the state language."""
    r = df[(df["model_track"] == "laya_routed")].drop_duplicates(["split", "protocol", "context_level", "scenario_id", "variant"])
    out = []
    for (split, protocol, context, case, variant), sub in r.groupby(["split", "protocol", "context_level", "experiment_id", "variant"]):
        expected = "english" if variant.startswith("en") else "multilingual"
        counts = sub["routing_model"].value_counts().to_dict()
        out.append({"split": split, "protocol": protocol, "context_level": context, "experiment_id": case,
                    "variant": variant, "n": len(sub), "routed_english": counts.get("english", 0),
                    "routed_multilingual": counts.get("multilingual", 0),
                    "mismatch_vs_state_language": float((sub["routing_model"] != expected).mean())})
    return pd.DataFrame(out)


def ambiguity_table(df: pd.DataFrame, high_conf: float = 0.8) -> pd.DataFrame:
    """P9: unknown-class detection and confident errors by evidence sufficiency."""
    ch = df[(df["primitive"] == "choice") & (df["protocol"] == "primary")]
    out = []
    keys = ["split", "context_level", "model_track", "experiment_id", "variant", "question_id", "evidence_sufficiency"]
    for (split, context, track, case, variant, qid, suff), sub in ch.groupby(keys):
        valid = sub["valid_answer"].astype(bool)
        wrong = valid & (sub["predicted_label"] != sub["gold_label"])
        gold_unknown = sub["gold_label"].isin(UNKNOWN_LABELS)
        out.append({"split": split, "context_level": context, "model_track": track, "experiment_id": case,
                    "variant": variant, "question_id": qid, "evidence_sufficiency": suff, "n": len(sub),
                    "effective_accuracy": float((valid & (sub["predicted_label"] == sub["gold_label"])).mean()),
                    "gold_unknown_share": float(gold_unknown.mean()),
                    "unknown_recall": float(sub.loc[gold_unknown, "predicted_label"].isin(UNKNOWN_LABELS).mean()) if gold_unknown.any() else None,
                    "high_conf_error_rate": float((wrong & (sub["max_probability"] >= high_conf)).mean())
                    if "max_probability" in sub and sub["max_probability"].notna().any() else None})
    return pd.DataFrame(out)


def truncation_table(df: pd.DataFrame) -> pd.DataFrame:
    """Context fit: share of requests where Laya reported truncation (common_fit eligibility)."""
    req = df[df["provider"] == "laya"].drop_duplicates(["split", "protocol", "context_level", "model_track", "scenario_id", "variant"])
    out = []
    for keys, sub in req.groupby(["split", "protocol", "context_level", "model_track", "experiment_id", "variant"]):
        out.append(dict(zip(["split", "protocol", "context_level", "model_track", "experiment_id", "variant"], keys))
                   | {"n_requests": len(sub), "truncated_share": float(sub["was_truncated"].fillna(False).astype(bool).mean())})
    return pd.DataFrame(out)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def analyze(
    run_names: list[str],
    out_dir: Path,
    local_hourly_usd: list[float],
    allow_mock: bool = False,
    calibration_runs: list[str] | None = None,
) -> dict[str, Any]:
    df = load_predictions(run_names, allow_mock=allow_mock)
    out_dir.mkdir(parents=True, exist_ok=True)
    pq = df.copy()
    pq["difficulty_tags"] = pq["difficulty_tags"].apply(lambda v: json.dumps(v) if isinstance(v, list) else v)
    pq.to_parquet(out_dir / "predictions.parquet", index=False)
    # P5 permutation rows feed only the robustness table; pooling them elsewhere would double count.
    main = df[df["protocol"] != "P5_order"]
    laya_tracks = [t for t in ("laya_routed", "laya_en_fixed", "laya_multi_fixed") if (main["model_track"] == t).any()]
    deltas = [paired_deltas(main, t, HEAD_TO_HEAD[1]) for t in laya_tracks]
    deltas = [d for d in deltas if not d.empty]
    # Tables without protocol/context keys use the primary common_fit rows only (one row per item).
    core = main[(main["protocol"] == "primary") & (main["context_level"] == "common_fit")]
    tables = {
        "summary": quality_table(main),
        "paired_deltas": pd.concat(deltas, ignore_index=True) if deltas else pd.DataFrame(),
        "language_gaps": language_gaps(main),
        "latency": latency_table(core),
        "cost": cost_table(core, local_hourly_usd),
        "high_risk": high_risk_failures(core),
        "rules_comparison": rules_comparison(core),
        "robustness_p5": robustness_table(df),
        "routing_p3": routing_table(df),
        "ambiguity_p9": ambiguity_table(main),
        "truncation": truncation_table(main),
    }
    if calibration_runs:
        cal = load_predictions(calibration_runs, allow_mock=allow_mock)
        tables["calibration_p6"] = calibration_table(cal, df[df["split"] == "test"])
    for name, table in tables.items():
        table.to_csv(out_dir / f"{name}.csv", index=False)
    tables["summary"].to_parquet(out_dir / "summary.parquet", index=False)
    manifest = {
        "runs": run_names,
        "rows": len(df),
        "tracks": sorted(df["model_track"].unique()),
        "cases": sorted(df["experiment_id"].unique()),
        "variants": sorted(df["variant"].unique()),
        "splits": sorted(df["split"].unique()),
        "tables": {k: len(v) for k, v in tables.items()},
    }
    (out_dir / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2, default=_jsonable))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute result tables from run predictions")
    parser.add_argument("--runs", required=True, help="comma-separated run names under results/runs")
    parser.add_argument("--out", required=True)
    parser.add_argument("--local-hourly-usd", default="", help="comma list of ASSUMED host prices for local cost")
    parser.add_argument("--allow-mock", action="store_true", help="CI only")
    parser.add_argument("--calibration-runs", default="", help="runs on the calibration split (P6 fitting)")
    args = parser.parse_args(argv)
    hourly = [float(x) for x in args.local_hourly_usd.split(",") if x]
    cal_runs = [r for r in args.calibration_runs.split(",") if r]
    print(json.dumps(analyze(args.runs.split(","), ROOT / args.out, hourly, args.allow_mock, cal_runs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
