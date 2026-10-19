"""Render docs/FINAL_REPORT.md from an analysis directory, run manifests and build reports.

Numbers come only from files written by the pipeline. A head-to-head verdict is issued only when
both primary tracks ran on the same scenarios; a pilot is never reported as a final result.

    python -m debench.report --analysis results/analysis/pilot
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from debench import STUDY_ID
from debench.config import ROOT
from debench.dataset.build import GENERATED_DIR
from debench.runner import RUNS_DIR

CASE_TITLES = {
    "E01": "Pipeline failure triage (OpenSearch)",
    "E02": "Data quality monitoring (ClickHouse)",
    "E03": "Schema drift detection (PostgreSQL)",
    "E04": "Lineage impact analysis (Neo4j)",
    "E05": "PII classification (PostgreSQL + JSONB)",
    "E06": "NL-to-SQL candidate validation (DuckDB)",
}
PRIMARY_QUESTION = {
    "E01": "failure_family",
    "E02": "anomaly_family",
    "E03": "drift_impact",
    "E04": "impact_tier",
    "E05": "data_class",
    "E06": "candidate_verdict",
}


def fmt(v: Any, digits: int = 3) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "–"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def md_table(df: pd.DataFrame, cols: list[str], digits: int = 3) -> str:
    if df.empty:
        return "_No rows._\n"
    cols = [c for c in cols if c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df[cols].iterrows():
        lines.append("| " + " | ".join(fmt(row[c], digits) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _ci(row: pd.Series, metric: str) -> str:
    lo, hi = row.get(f"{metric}_ci_low"), row.get(f"{metric}_ci_high")
    if lo is None or pd.isna(lo):
        return fmt(row.get(metric))
    return f"{fmt(row.get(metric))} [{fmt(lo)}, {fmt(hi)}]"


def verdicts(summary: pd.DataFrame, deltas: pd.DataFrame, tracks: dict[str, Any], pilot: bool) -> list[dict[str, str]]:
    jev = tracks.get("jev_114", {})
    out = []
    for case, title in CASE_TITLES.items():
        q = PRIMARY_QUESTION[case]
        ran = not summary.empty and (summary["experiment_id"] == case).any()
        if not ran:
            out.append({"case": case, "title": title, "verdict": "Not executed", "basis": "no predictions"})
            continue
        if jev.get("status") != "OK":
            reason = jev.get("reason", "jev_114 not run")
            out.append({"case": case, "title": title, "verdict": "Not executed",
                        "basis": f"Jev track {jev.get('status', 'NOT_RUN')}: {reason}"})
            continue
        d = deltas[(deltas["experiment_id"] == case) & (deltas["question_id"] == q) & (deltas["metric"] == "macro_f1")]
        if "track_a" in d:
            d = d[d["track_a"] == "laya_routed"]
        parts, signs = [], set()
        for _, r in d.iterrows():
            parts.append(f"{r['variant']}: Δ={fmt(r['delta'])} [{fmt(r['ci_low'])}, {fmt(r['ci_high'])}], n={r['n_scenarios']}")
            if r["ci_low"] > 0:
                signs.add("laya")
            elif r["ci_high"] < 0:
                signs.add("jev")
            else:
                signs.add("tie")
        if pilot:
            verdict = "Inconclusive (pilot)"
        elif signs == {"laya"}:
            verdict = "Laya wins"
        elif signs == {"jev"}:
            verdict = "Jev wins"
        elif signs == {"tie"}:
            verdict = "Statistical tie"
        else:
            verdict = "Inconclusive"
        out.append({"case": case, "title": title, "verdict": verdict, "basis": f"{q} macro-F1 (Laya routed − Jev 1.14); " + "; ".join(parts)})
    return out


def render(analysis_dir: Path) -> str:
    manifest = json.loads((analysis_dir / "analysis_manifest.json").read_text())
    runs = {name: json.loads((RUNS_DIR / name / "manifest.json").read_text()) for name in manifest["runs"]}
    tracks: dict[str, Any] = {}
    for m in runs.values():
        tracks.update(m.get("tracks", {}))
    pilot = any(m.get("pilot") for m in runs.values())
    hardware = json.loads((ROOT / "results" / "hardware.json").read_text()) if (ROOT / "results" / "hardware.json").exists() else {}
    summary = _read_csv(analysis_dir / "summary.csv")
    deltas = _read_csv(analysis_dir / "paired_deltas.csv")
    gaps = _read_csv(analysis_dir / "language_gaps.csv")
    latency = _read_csv(analysis_dir / "latency.csv")
    cost = _read_csv(analysis_dir / "cost.csv")
    risk = _read_csv(analysis_dir / "high_risk.csv")
    rules = _read_csv(analysis_dir / "rules_comparison.csv")
    robust = _read_csv(analysis_dir / "robustness_p5.csv")
    routing = _read_csv(analysis_dir / "routing_p3.csv")
    ambiguity = _read_csv(analysis_dir / "ambiguity_p9.csv")
    trunc = _read_csv(analysis_dir / "truncation.csv")
    calib = _read_csv(analysis_dir / "calibration_p6.csv")

    def primary(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "protocol" not in df:
            return df
        out = df[df["protocol"] == "primary"]
        return out[out["context_level"] == "common_fit"] if "context_level" in out else out

    out = [f"# Final report — {STUDY_ID}\n"]
    status = "PILOT (dev split; not a final result)" if pilot else "LOCKED EVALUATION"
    out.append(f"**Status:** {status}  \n**Generated:** {datetime.now().astimezone().isoformat(timespec='seconds')}  \n"
               f"**Analysis:** `{analysis_dir.relative_to(ROOT)}` from runs {', '.join(f'`{r}`' for r in manifest['runs'])}\n")

    findings = analysis_dir / "FINDINGS.md"
    if findings.exists():
        out.append(findings.read_text(encoding="utf-8").rstrip() + "\n")

    out.append("## Tracks\n")
    trows = [{"track": t, "status": s.get("status"), "provider": s.get("provider"), "note": s.get("reason", "")} for t, s in sorted(tracks.items())]
    out.append(md_table(pd.DataFrame(trows), ["track", "status", "provider", "note"]))

    gpu = hardware.get("gpu", {})
    out.append("## Environment\n")
    out.append(f"- Host: {hardware.get('cpu', {}).get('model')} ({hardware.get('cpu', {}).get('logical_cores')} threads), "
               f"RAM {hardware.get('memory_gb', {}).get('MemTotal')} GB, {hardware.get('os')}\n"
               f"- GPU: {gpu.get('device_name')} ({gpu.get('vram_gb')} GB), torch CUDA {gpu.get('torch_cuda_version')}\n"
               f"- Docker: {hardware.get('docker') or 'not available (engines run natively/embedded; see manifests)'}\n"
               f"- Packages: " + ", ".join(f"{k} {v}" for k, v in hardware.get("packages", {}).items() if v) + "\n")

    out.append("## Datasets\n")
    drows = []
    for case in CASE_TITLES:
        p = GENERATED_DIR / case.lower() / "common_fit.build.json"
        if p.exists():
            b = json.loads(p.read_text())
            drows.append({"case": case, "engine": f"{b['backend']} {b.get('engine_version') or ''} ({b.get('runtime')})",
                          "records": b["records"], "gold_verified": f"{b['gold_verified']}/300",
                          "pair_failures": len(b["pair_failures"]), "splits": json.dumps(b["split_counts"]),
                          "db_p95_ms": b.get("db_retrieval_ms_p95")})
    out.append(md_table(pd.DataFrame(drows), ["case", "engine", "records", "gold_verified", "pair_failures", "splits", "db_p95_ms"]))

    out.append("## Verdicts\n")
    out.append(md_table(pd.DataFrame(verdicts(primary(summary), primary(deltas), tracks, pilot)), ["case", "title", "verdict", "basis"]))

    out.append("## Table 1 — quality by model, case, language and question\n")
    out.append("Accuracy and macro-F1 use the attempted denominator (invalid = wrong); CIs are 95% scenario-clustered bootstrap (2,000 resamples, seed 20261019). Probability metrics use valid answers only.\n\n")
    if not summary.empty:
        s = primary(summary).copy()
        s["eff_acc [95% CI]"] = s.apply(lambda r: _ci(r, "effective_accuracy"), axis=1)
        s["macro_f1 [95% CI]"] = s.apply(lambda r: _ci(r, "macro_f1") if r["primitive"] == "choice" else "–", axis=1)
        out.append(md_table(s, ["experiment_id", "model_track", "variant", "question_id", "primitive", "n_attempted", "coverage",
                                "truncation_rate", "eff_acc [95% CI]", "macro_f1 [95% CI]", "brier", "ece_equal_mass",
                                "mae_expected", "auroc"]))

    out.append("## Table 2 — paired Laya − Jev deltas\n")
    pdl = primary(deltas)
    if not pdl.empty:
        pdl = pdl[pdl["track_a"] == "laya_routed"]
    out.append("All questions; Δ = Laya routed − Jev 1.14 on identical scenarios and requests. Negative favours Jev.\n\n")
    out.append(md_table(pdl, ["experiment_id", "variant", "question_id", "metric", "n_scenarios", "a", "b", "delta", "ci_low", "ci_high", "mcnemar_p"])
               if not pdl.empty else "_Not executed: no scenarios were answered by both laya_routed and jev_114._\n")
    if not deltas.empty and "track_a" in deltas and (deltas["track_a"] != "laya_routed").any():
        out.append("\n**P3 — each Laya checkpoint vs Jev (primary choice question, macro-F1):**\n\n")
        p3 = primary(deltas)
        p3 = p3[(p3["metric"] == "macro_f1") & p3["question_id"].isin(PRIMARY_QUESTION.values())]
        out.append(md_table(p3, ["experiment_id", "variant", "track_a", "n_scenarios", "a", "b", "delta", "ci_low", "ci_high"]))

    out.append("## Table 3 — language gap within each model (TH − EN, paired)\n")
    g = primary(gaps)
    out.append(md_table(g[g["metric"] == "effective_accuracy"] if not g.empty else g,
                        ["model_track", "experiment_id", "question_id", "n_scenarios", "a", "b", "delta", "ci_low", "ci_high"]))

    out.append("## Table 5 — model latency (warm, per request)\n")
    out.append("Local Laya is in-process inference on this host; it is not comparable to remote HTTPS latency.\n\n")
    out.append(md_table(latency, ["model_track", "device", "variant", "n_requests", "p50_ms", "p95_ms", "p99_ms", "db_retrieval_p50_ms", "topology"], 1))

    out.append("## Table 6 — cost\n")
    out.append(md_table(cost, ["model_track", "basis", "requests", "input_tokens", "usd_total", "sustained_decisions_per_hour",
                               "usd_per_1000_scenario_decisions"], 4))

    out.append("## Table 7 — high-risk failures\n")
    out.append(md_table(risk, ["experiment_id", "question_id", "model_track", "variant", "definition", "n_positive", "misses",
                               "miss_rate", "invalid_on_positive", "upper_95_if_zero"]))

    out.append("## Table 8 — deterministic rules baseline vs models\n")
    out.append(md_table(rules, ["experiment_id", "variant", "question_id", "model_track", "n", "rules_coverage", "rules_acc",
                                "model_acc", "rules_only_correct", "model_only_correct", "model_acc_where_rules_abstain"]))

    out.append("## P3 — Laya router checkpoint choice\n")
    out.append(md_table(primary(routing), ["experiment_id", "variant", "n", "routed_english", "routed_multilingual", "mismatch_vs_state_language"]))

    if not summary.empty and (summary["protocol"] == "P4_cross_language").any():
        out.append("## P4 — cross-language variants (predeclared 50-scenario subset)\n")
        p4 = summary[summary["protocol"] == "P4_cross_language"]
        p4 = p4[p4["question_id"].isin(PRIMARY_QUESTION.values())]
        out.append(md_table(p4, ["experiment_id", "model_track", "variant", "question_id", "n_attempted", "effective_accuracy", "macro_f1", "truncation_rate"]))

    if not robust.empty:
        out.append("## P5 — choice option-order robustness (Laya: 5 permutations; Jev: 2 because of the request cap)\n")
        agg = robust.groupby(["model_track", "experiment_id", "question_id", "language"], as_index=False).agg(
            n=("n", "first"), flip_rate_mean=("flip_rate", "mean"), flip_rate_max=("flip_rate", "max"),
            mean_tvd=("mean_tvd", "mean"), acc_base=("acc_base", "first"), acc_perm_mean=("acc_perm", "mean"))
        out.append(md_table(agg, ["model_track", "experiment_id", "question_id", "language", "n", "flip_rate_mean", "flip_rate_max", "mean_tvd", "acc_base", "acc_perm_mean"]))

    if not calib.empty:
        out.append("## P6 — calibration, raw vs post-hoc (fit on calibration split only)\n")
        out.append(md_table(calib, ["model_track", "experiment_id", "variant", "question_id", "n_cal", "n_test", "temperature",
                                    "raw_brier", "calibrated_brier", "raw_ece_equal_mass", "calibrated_ece_equal_mass",
                                    "test_coverage_at_target", "test_risk_at_target"]))

    if not summary.empty and (summary["context_level"] == "natural_long").any():
        out.append("## P7 — context length (common_fit vs natural_long, primary choice question)\n")
        p7 = summary[(summary["protocol"] == "primary") & summary["question_id"].isin(PRIMARY_QUESTION.values())]
        p7 = p7.pivot_table(index=["experiment_id", "model_track", "variant"], columns="context_level",
                            values=["effective_accuracy", "truncation_rate"]).reset_index()
        p7.columns = ["_".join(c).strip("_") for c in p7.columns]
        out.append(md_table(p7, list(p7.columns)))

    if not ambiguity.empty:
        out.append("## P9 — evidence sufficiency and confident errors (primary choice question)\n")
        a = primary(ambiguity) if "protocol" in ambiguity else ambiguity[ambiguity["context_level"] == "common_fit"]
        a = a[a["question_id"].isin(PRIMARY_QUESTION.values())]
        out.append(md_table(a, ["experiment_id", "model_track", "variant", "evidence_sufficiency", "n", "effective_accuracy",
                                "unknown_recall", "high_conf_error_rate"]))

    lat_files = sorted((ROOT / "results" / "latency").glob("*.json"))
    if lat_files:
        out.append("## P8 — latency harness (warm; concurrency × question count × language)\n")
        out.append("Laya: in-process on this host. Jev: HTTPS from this host to the remote API. "
                   "These are deployment topologies, not intrinsic model speed.\n\n")
        rows = []
        for f in lat_files:
            d = json.loads(f.read_text())
            name = f.stem
            for r in d.get("summary", []):
                rows.append({"run": name, **r})
            rtt = d.get("rtt_baseline_remote_api", {})
            load = (d.get("load") or {}).get("warmup_call") or {}
            rows.append({"run": name, "concurrency": "–", "questions": "load / RTT",
                         "language": f"load_ms={fmt(load.get('load_ms'), 0) if isinstance(load, dict) else '–'}",
                         "p50_ms": rtt.get("p50_ms"), "p95_ms": rtt.get("p95_ms")})
        out.append(md_table(pd.DataFrame(rows), ["run", "concurrency", "questions", "language", "n", "p50_ms", "p95_ms", "p99_ms", "throughput_rps"], 1))
        out.append("\nThe `load / RTT` row gives cold checkpoint load time (Laya) and an unauthenticated HTTPS round trip "
                   "to the API host (all runs), for context.\n")

    out.append("## Context fit (Laya truncation)\n")
    out.append(md_table(trunc, ["split", "protocol", "context_level", "model_track", "experiment_id", "variant", "n_requests", "truncated_share"]))

    out.append("\nSee `docs/STUDY_LIMITATIONS.md`, `docs/FAILURE_CASES.md`, `docs/DATASET_CARD.md` and `results/BLOCKERS.md`.\n")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render docs/FINAL_REPORT.md")
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--out", default="docs/FINAL_REPORT.md")
    args = parser.parse_args(argv)
    text = render(ROOT / args.analysis)
    (ROOT / args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
