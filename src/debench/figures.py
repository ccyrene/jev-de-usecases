"""Render every README/report figure into docs/figures/ from the final analysis outputs.

    python -m debench.figures --analysis results/analysis/test

Colours follow the entity in every figure (Laya routed blue, Jev orange, rules aqua, Laya English
checkpoint yellow, Laya multilingual checkpoint magenta) and each entity also has its own marker,
so identity never depends on colour alone. Sequential magnitude uses one blue ramp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from debench.config import ROOT  # noqa: E402
from debench.eval import metrics as M  # noqa: E402
from debench.eval.calibration import risk_coverage_curve  # noqa: E402
from debench.eval.paired_statistics import wilson_interval  # noqa: E402

OUT = ROOT / "docs" / "figures"
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#1f1f1e", "#6b6a63", "#e6e5df"
COLOR = {"laya_routed": "#2a78d6", "jev_114": "#eb6834", "rules_only": "#1baf7a",
         "laya_en_fixed": "#eda100", "laya_multi_fixed": "#e87ba4"}
MARKER = {"laya_routed": "o", "jev_114": "s", "rules_only": "D", "laya_en_fixed": "^", "laya_multi_fixed": "v"}
NAME = {"laya_routed": "Laya (router)", "jev_114": "Jev 1.14.0", "rules_only": "Rules baseline",
        "laya_en_fixed": "Laya English ckpt", "laya_multi_fixed": "Laya multilingual ckpt"}
SEQ = LinearSegmentedColormap.from_list("blue_seq", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
PRIMARY = {"E01": "failure_family", "E02": "anomaly_family", "E03": "drift_impact",
           "E04": "impact_tier", "E05": "data_class", "E06": "candidate_verdict"}
CASE_LABEL = {"E01": "E01 failure triage", "E02": "E02 data quality", "E03": "E03 schema drift",
              "E04": "E04 lineage impact", "E05": "E05 PII class", "E06": "E06 SQL verdict"}
LANGS = (("en_en", "English"), ("th_th", "Thai"))

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": INK, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "text.color": INK,
    "legend.frameon": False,
})


def _style(ax, grid_axis: str = "x") -> None:
    ax.grid(axis=grid_axis, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if grid_axis == "x":
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)


def _dot(ax, x, y, track, label=True, size=60, filled=True):
    ax.scatter(x, y, s=size, marker=MARKER[track], color=COLOR[track] if filled else SURFACE,
               edgecolors=COLOR[track] if not filled else SURFACE, linewidths=2, zorder=3,
               label=NAME[track] if label else None)


def _interval(ax, lo, hi, y, track):
    if pd.notna(lo) and pd.notna(hi):
        ax.plot([lo, hi], [y, y], color=COLOR[track], lw=2, solid_capstyle="round", zorder=2)


def _finish(fig, title: str, name: str, legend_axes=None, ncol: int = 3, top: float = 1.02) -> str:
    ax = legend_axes if legend_axes is not None else fig.axes[0]
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", ncol=ncol, bbox_to_anchor=(0.5, top))
    fig.suptitle(title, x=0.01, y=top + 0.07, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    path = OUT / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path.name


def _primary(df: pd.DataFrame) -> pd.DataFrame:
    df = df[(df["protocol"] == "primary") & (df["context_level"] == "common_fit")]
    return df[df.apply(lambda r: PRIMARY.get(r["experiment_id"]) == r["question_id"], axis=1)]


def _case_axis(ax, cases):
    ax.set_yticks(range(len(cases)), [CASE_LABEL[c] for c in cases])
    for i in range(len(cases)):
        ax.axhline(i, color=GRID, lw=1, zorder=0)


# --------------------------------------------------------------------------------------- figures

def fig_quality(s: pd.DataFrame) -> str:
    s = _primary(s)
    cases = list(PRIMARY)[::-1]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        _case_axis(ax, cases)
        for i, case in enumerate(cases):
            for track in ("rules_only", "laya_routed", "jev_114"):
                r = s[(s.experiment_id == case) & (s.variant == variant) & (s.model_track == track)]
                if r.empty:
                    continue
                r = r.iloc[0]
                if track != "rules_only":
                    _interval(ax, r.get("macro_f1_ci_low"), r.get("macro_f1_ci_high"), i, track)
                _dot(ax, r.macro_f1, i, track, label=i == 0)
        ax.set_xlim(0, 1.03)
        ax.set_title(lang, loc="left", fontsize=11)
        ax.set_xlabel("macro-F1, primary choice question (lines: 95% CI)")
        _style(ax)
    return _finish(fig, "1 · Decision quality by use case (test, n = 200 per case and language)", "01_quality_primary.png")


def fig_head_to_head(d: pd.DataFrame) -> str:
    d = _primary(d)
    d = d[(d.track_a == "laya_routed") & (d.metric == "macro_f1")]
    cases = list(PRIMARY)[::-1]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True, sharex=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        _case_axis(ax, cases)
        ax.axvline(0, color=MUTED, lw=1)
        for i, case in enumerate(cases):
            r = d[(d.experiment_id == case) & (d.variant == variant)]
            if r.empty:
                continue
            r = r.iloc[0]
            ax.plot([r.ci_low, r.ci_high], [i, i], color=INK, lw=2, solid_capstyle="round")
            ax.scatter(r.delta, i, s=60, color=INK, edgecolors=SURFACE, linewidths=2, zorder=3)
        ax.set_xlim(-1, 0.25)
        ax.set_title(lang, loc="left", fontsize=11)
        ax.set_xlabel("Δ macro-F1 = Laya (router) − Jev  (< 0 favours Jev)")
        _style(ax)
    return _finish(fig, "2 · Paired head-to-head difference with 95% scenario-bootstrap CI", "02_head_to_head_delta.png")


def fig_language_gap(g: pd.DataFrame) -> str:
    g = _primary(g)
    g = g[g.metric == "macro_f1"]
    cases = list(PRIMARY)[::-1]
    fig, ax = plt.subplots(figsize=(7.5, 4))
    _case_axis(ax, cases)
    ax.axvline(0, color=MUTED, lw=1)
    for track, off in (("laya_routed", 0.15), ("jev_114", -0.15)):
        for i, case in enumerate(cases):
            r = g[(g.experiment_id == case) & (g.model_track == track)]
            if r.empty:
                continue
            r = r.iloc[0]
            _interval(ax, r.ci_low, r.ci_high, i + off, track)
            _dot(ax, r.delta, i + off, track, label=i == 0)
    ax.set_xlabel("Thai − English macro-F1, same scenarios (< 0: worse in Thai)")
    _style(ax)
    return _finish(fig, "3 · Language gap within each model", "03_language_gap.png", ncol=2)


def fig_score_noul(s: pd.DataFrame) -> str:
    s = s[(s.protocol == "primary") & (s.context_level == "common_fit")]
    score = s[s.primitive == "score"]
    noul = s[s.primitive == "noul"]
    sq = sorted({(r.experiment_id, r.question_id) for r in score.itertuples()}, reverse=True)
    nq = sorted({(r.experiment_id, r.question_id) for r in noul.itertuples()}, reverse=True)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.2), gridspec_kw={"height_ratios": [len(sq), len(nq)]})
    for col, (variant, lang) in enumerate(LANGS):
        for row, (data, qs, metric, xlabel, xlim) in enumerate((
            (score, sq, "mae_expected", "expected-level MAE (lower is better)", (0, 2.2)),
            (noul, nq, "auroc", "AUROC (higher is better; 0.5 = chance)", (0, 1.02)),
        )):
            ax = axes[row, col]
            ax.set_yticks(range(len(qs)), [f"{c} {q}" for c, q in qs])
            for i, (case, qid) in enumerate(qs):
                ax.axhline(i, color=GRID, lw=1, zorder=0)
                for track in ("laya_routed", "jev_114"):
                    r = data[(data.experiment_id == case) & (data.question_id == qid) & (data.variant == variant)
                             & (data.model_track == track)]
                    if not r.empty and pd.notna(r.iloc[0].get(metric)):
                        _dot(ax, r.iloc[0][metric], i, track, label=(i == 0 and row == 0))
            if metric == "auroc":
                ax.axvline(0.5, color=MUTED, lw=1, ls=":")
            ax.set_xlim(*xlim)
            ax.set_xlabel(xlabel)
            ax.set_title(f"{lang} — {'score questions' if row == 0 else 'yes/no (noul) questions'}", loc="left", fontsize=11)
            _style(ax)
    return _finish(fig, "4 · Ordinal and yes/no questions", "04_score_noul.png", legend_axes=axes[0, 0], ncol=2)


def fig_checkpoints(s: pd.DataFrame) -> str:
    s = _primary(s)
    cases = list(PRIMARY)[::-1]
    tracks = ("laya_en_fixed", "laya_multi_fixed", "laya_routed", "jev_114")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        _case_axis(ax, cases)
        # Dodge vertically: the router's value equals the checkpoint it picked, so markers would overlap.
        offsets = dict(zip(tracks, (0.24, 0.08, -0.08, -0.24)))
        for i, case in enumerate(cases):
            for track in tracks:
                r = s[(s.experiment_id == case) & (s.variant == variant) & (s.model_track == track)]
                if not r.empty:
                    size = 75 if MARKER[track] in "^v" else 55
                    _dot(ax, r.iloc[0].macro_f1, i + offsets[track], track, label=i == 0, size=size)
        ax.set_xlim(0, 1.03)
        ax.set_title(lang, loc="left", fontsize=11)
        ax.set_xlabel("macro-F1, primary choice question")
        _style(ax)
    return _finish(fig, "5 · P3 checkpoint ablation: fixed Laya checkpoints vs router vs Jev", "05_checkpoints_p3.png", ncol=4)


def _choice_rows(p: pd.DataFrame) -> pd.DataFrame:
    p = p[(p.protocol == "primary") & (p.context_level == "common_fit") & (p.primitive == "choice")
          & p.model_track.isin(["laya_routed", "jev_114"]) & p.valid_answer.astype(bool)].copy()
    p["correct"] = (p.predicted_label == p.gold_label).astype(float)
    return p


def fig_reliability(p: pd.DataFrame) -> str:
    c = _choice_rows(p)
    edges = np.linspace(0, 1, 11)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        ax.plot([0, 1], [0, 1], color=MUTED, lw=1, ls=":", label="perfect calibration")
        for track in ("laya_routed", "jev_114"):
            sub = c[(c.variant == variant) & (c.model_track == track)]
            conf, corr = sub.max_probability.to_numpy(), sub.correct.to_numpy()
            idx = np.clip(np.digitize(conf, edges) - 1, 0, 9)
            xs, ys = [], []
            for b in range(10):
                m = idx == b
                if m.sum() >= 10:
                    xs.append(conf[m].mean())
                    ys.append(corr[m].mean())
            ece = M.expected_calibration_error(conf, corr, 15, "mass")
            ax.plot(xs, ys, color=COLOR[track], lw=2, marker=MARKER[track], ms=7, mec=SURFACE, mew=1.5,
                    label=f"{NAME[track]} (ECE {ece:.2f})")
        ax.set_xlim(0, 1.02)
        ax.set_ylim(0, 1.02)
        ax.set_aspect("equal")
        ax.set_xlabel("stated confidence (max probability)")
        ax.set_ylabel("observed accuracy")
        ax.set_title(f"{lang}", loc="left", fontsize=11)
        ax.legend(loc="upper left", fontsize=9)
        _style(ax, "both")
    fig.suptitle("6 · Raw calibration, all choice questions pooled (bins with ≥ 10 answers)",
                 x=0.01, y=1.02, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "06_calibration_reliability.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "06_calibration_reliability.png"


def fig_risk_coverage(p: pd.DataFrame) -> str:
    c = _choice_rows(p)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        for track in ("laya_routed", "jev_114"):
            sub = c[(c.variant == variant) & (c.model_track == track)]
            rc = risk_coverage_curve(sub.max_probability.to_numpy(), sub.correct.to_numpy())
            ax.plot(rc["coverage"], rc["risk"], color=COLOR[track], lw=2, label=NAME[track] if variant == "en_en" else None)
        ax.axhline(0.2, color=MUTED, lw=1, ls=":")
        ax.text(0.03, 0.215, "20% risk target", color=MUTED, fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("coverage (share auto-accepted, highest confidence first)")
        ax.set_ylabel("error rate among accepted")
        ax.set_title(lang, loc="left", fontsize=11)
        _style(ax, "y")
    return _finish(fig, "7 · Selective prediction: risk vs coverage (choice questions, raw confidence)",
                   "07_risk_coverage.png", ncol=2)


def fig_order_robustness(r: pd.DataFrame) -> str:
    r = r[r.variant.str.endswith(("perm1", "perm2"))]
    r = r[r.apply(lambda x: PRIMARY.get(x.experiment_id) == x.question_id, axis=1)]
    agg = r.groupby(["model_track", "experiment_id", "language"], as_index=False).flip_rate.mean()
    cases = list(PRIMARY)[::-1]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True, sharex=True)
    for ax, (lang_code, lang) in zip(axes, (("en", "English"), ("th", "Thai"))):
        _case_axis(ax, cases)
        for i, case in enumerate(cases):
            for track in ("laya_routed", "jev_114"):
                v = agg[(agg.model_track == track) & (agg.experiment_id == case) & (agg.language == lang_code)]
                if not v.empty:
                    _dot(ax, v.iloc[0].flip_rate * 100, i, track, label=i == 0)
        ax.set_xlim(0, 45)
        ax.set_xlabel("% of answers that change when only option order changes")
        ax.set_title(lang, loc="left", fontsize=11)
        _style(ax)
    return _finish(fig, "8 · P5 option-order sensitivity (same options, 2 shared permutations)",
                   "08_option_order_p5.png", ncol=2)


def fig_cross_language(s: pd.DataFrame) -> str:
    s = s[(s.protocol == "P4_cross_language")]
    s = s[s.apply(lambda r: PRIMARY.get(r.experiment_id) == r.question_id, axis=1)]
    variants = ["en_en", "en_th", "th_en", "th_th"]
    vlabels = ["EN state\nEN rubric", "EN state\nTH rubric", "TH state\nEN rubric", "TH state\nTH rubric"]
    cases = list(PRIMARY)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, track in zip(axes, ("laya_routed", "jev_114")):
        grid = np.array([[s[(s.model_track == track) & (s.experiment_id == c) & (s.variant == v)].macro_f1.mean()
                          for v in variants] for c in cases])
        im = ax.imshow(grid, cmap=SEQ, vmin=0, vmax=1, aspect="auto")
        for i in range(len(cases)):
            for j in range(len(variants)):
                val = grid[i, j]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=9,
                        color="#ffffff" if val > 0.55 else INK)
        ax.set_xticks(range(len(variants)), vlabels, fontsize=8)
        ax.set_yticks(range(len(cases)), [CASE_LABEL[c] for c in cases])
        ax.set_title(NAME[track], loc="left", fontsize=11)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(length=0)
    cbar = fig.colorbar(im, ax=axes, shrink=0.8, pad=0.02)
    cbar.set_label("macro-F1")
    cbar.outline.set_visible(False)
    fig.suptitle("9 · P4 cross-language prompts (predeclared 50-scenario subset per case)",
                 x=0.01, y=1.0, ha="left", fontsize=12)
    fig.savefig(OUT / "09_cross_language_p4.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "09_cross_language_p4.png"


def fig_context(s: pd.DataFrame) -> str:
    s = s[(s.protocol == "primary")]
    s = s[s.apply(lambda r: PRIMARY.get(r.experiment_id) == r.question_id, axis=1)]
    rows = [(c, v) for c in list(PRIMARY)[::-1] for v in ("th_th", "en_en")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.2), sharey=True)
    for ax, track in zip(axes, ("laya_routed", "jev_114")):
        ax.set_yticks(range(len(rows)), [f"{CASE_LABEL[c]} · {'EN' if v == 'en_en' else 'TH'}" for c, v in rows], fontsize=9)
        for i, (case, variant) in enumerate(rows):
            ax.axhline(i, color=GRID, lw=1, zorder=0)
            vals = {}
            for ctx in ("common_fit", "natural_long"):
                r = s[(s.model_track == track) & (s.experiment_id == case) & (s.variant == variant) & (s.context_level == ctx)]
                if not r.empty:
                    vals[ctx] = r.iloc[0].macro_f1
            if len(vals) == 2:
                ax.plot([vals["common_fit"], vals["natural_long"]], [i, i], color=COLOR[track], lw=2, alpha=0.6)
            if "common_fit" in vals:
                ax.scatter(vals["common_fit"], i, s=55, marker=MARKER[track], color=SURFACE, edgecolors=COLOR[track],
                           linewidths=2, zorder=3)
            if "natural_long" in vals:
                ax.scatter(vals["natural_long"], i, s=55, marker=MARKER[track], color=COLOR[track], edgecolors=SURFACE,
                           linewidths=2, zorder=4)
        ax.set_xlim(0, 1.03)
        ax.set_xlabel("macro-F1, primary choice question")
        ax.set_title(NAME[track], loc="left", fontsize=11)
        _style(ax)
    key = [plt.Line2D([], [], ls="", marker="o", ms=8, mfc=SURFACE, mec=MUTED, mew=2, label="compact evidence (common_fit)"),
           plt.Line2D([], [], ls="", marker="o", ms=8, mfc=MUTED, mec=SURFACE, mew=2, label="long evidence (natural_long)")]
    fig.legend(handles=key, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.03))
    fig.suptitle("10 · P7 context length per model", x=0.01, y=1.09, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "10_context_p7.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "10_context_p7.png"


def fig_sufficiency(a: pd.DataFrame) -> str:
    a = a[(a.context_level == "common_fit") & a.model_track.isin(["laya_routed", "jev_114", "rules_only"])]
    a = a[a.apply(lambda r: PRIMARY.get(r.experiment_id) == r.question_id, axis=1)]
    a = a.assign(correct=a.effective_accuracy * a.n)
    agg = a.groupby(["model_track", "variant", "evidence_sufficiency"], as_index=False)[["correct", "n"]].sum()
    agg["acc"] = agg.correct / agg.n
    levels = ["insufficient", "ambiguous", "sufficient"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        ax.set_yticks(range(len(levels)), [f"{lv} evidence" for lv in levels])
        for i, lv in enumerate(levels):
            ax.axhline(i, color=GRID, lw=1, zorder=0)
            for track in ("rules_only", "laya_routed", "jev_114"):
                v = agg[(agg.model_track == track) & (agg.variant == variant) & (agg.evidence_sufficiency == lv)]
                if not v.empty:
                    _dot(ax, v.iloc[0].acc, i, track, label=i == 0)
        ax.set_xlim(0, 1.03)
        ax.set_xlabel("accuracy, primary choice question (all cases pooled)")
        ax.set_title(lang, loc="left", fontsize=11)
        _style(ax)
    return _finish(fig, "11 · P9 accuracy by evidence sufficiency", "11_sufficiency_p9.png")


def fig_high_risk(h: pd.DataFrame) -> str:
    labels = {"E01": "E01 severe incident rated not blocking", "E03": "E03 breaking change called safe",
              "E04": "E04 critical impact missed", "E05": "E05 identifier called non-personal",
              "E06": "E06 unsafe/wrong SQL accepted"}
    cases = list(labels)[::-1]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8), sharey=True)
    for ax, (variant, lang) in zip(axes, LANGS):
        ax.set_yticks(range(len(cases)), [labels[c] for c in cases])
        for i, case in enumerate(cases):
            ax.axhline(i, color=GRID, lw=1, zorder=0)
            for track, off in (("rules_only", 0.22), ("laya_routed", 0), ("jev_114", -0.22)):
                r = h[(h.experiment_id == case) & (h.model_track == track) & (h.variant == variant)]
                if r.empty or not r.iloc[0].n_positive:
                    continue
                r = r.iloc[0]
                lo, hi = wilson_interval(int(r.misses), int(r.n_positive))
                _interval(ax, lo * 100, hi * 100, i + off, track)
                _dot(ax, r.misses / r.n_positive * 100, i + off, track, label=i == 0, size=45)
        ax.set_xlim(-3, 102)
        ax.set_xlabel("% of high-risk positives missed (Wilson 95% CI)")
        ax.set_title(lang, loc="left", fontsize=11)
        _style(ax)
    return _finish(fig, "12 · Safety-relevant misses (lower is better)", "12_high_risk.png")


def fig_truncation(t: pd.DataFrame) -> str:
    t = t[t.protocol == "primary"]
    groups = [("common_fit", "en_en", "compact · EN"), ("common_fit", "th_th", "compact · TH"),
              ("natural_long", "en_en", "long · EN"), ("natural_long", "th_th", "long · TH")]
    tracks = ("laya_en_fixed", "laya_multi_fixed", "laya_routed")
    fig, ax = plt.subplots(figsize=(8, 3.6))
    width = 0.26
    for k, track in enumerate(tracks):
        vals = []
        for ctx, variant, _ in groups:
            sub = t[(t.context_level == ctx) & (t.variant == variant) & (t.model_track == track)]
            vals.append((sub.truncated_share * sub.n_requests).sum() / sub.n_requests.sum() * 100 if not sub.empty else np.nan)
        x = np.arange(len(groups)) + (k - 1) * (width + 0.02)
        ax.bar(x, vals, width, color=COLOR[track], edgecolor=SURFACE, linewidth=2, label=NAME[track])
        for xi, v in zip(x, vals):
            if v < 2:  # near-invisible bars: say they were measured, not missing
                ax.text(xi, 2.5, "0%" if v == 0 else "<2%", ha="center", va="bottom", fontsize=8, color=MUTED)
    ax.set_xticks(range(len(groups)), [g[2] for g in groups])
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of requests truncated")
    _style(ax, "y")
    return _finish(fig, "13 · Laya context fit: share of requests truncated by the checkpoint", "13_truncation.png")


def fig_latency() -> str:
    lat = {}
    for track, f in (("laya_routed", "laya_routed_cuda"), ("jev_114", "jev_114_remote")):
        path = ROOT / "results" / "latency" / f"{f}.json"
        rows = [r for r in json.loads(path.read_text())["summary"] if r["questions"] == "all" and r["language"] == "en"]
        lat[track] = sorted(rows, key=lambda r: r["concurrency"])
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for track, rows in lat.items():
        c = [r["concurrency"] for r in rows]
        label = NAME[track] + (" — GPU, in-process" if track.startswith("laya") else " — HTTPS from Bangkok")
        axes[0].plot(c, [r["p50_ms"] for r in rows], color=COLOR[track], lw=2, marker=MARKER[track], ms=8,
                     mec=SURFACE, mew=2, label=label)
        axes[1].plot(c, [r["throughput_rps"] for r in rows], color=COLOR[track], lw=2, marker=MARKER[track], ms=8,
                     mec=SURFACE, mew=2)
    axes[0].set_title("p50 latency per request (ms)", loc="left", fontsize=11)
    axes[1].set_title("throughput (requests / s)", loc="left", fontsize=11)
    for ax in axes:
        ax.set_xticks([1, 4, 8])
        ax.set_xlabel("concurrent requests")
        ax.set_ylim(bottom=0)
        _style(ax, "y")
    return _finish(fig, "14 · Deployment latency (English, all questions in one request)", "14_latency.png",
                   legend_axes=axes[0], ncol=2)


def fig_cost(p: pd.DataFrame, cost: pd.DataFrame, price: float) -> str:
    j = p[(p.model_track == "jev_114") & (p.protocol == "primary") & (p.context_level == "common_fit")]
    j = j[~j.cached.fillna(False).astype(bool)].drop_duplicates(["scenario_id", "variant"])
    bars = []
    for variant, lang in LANGS:
        sub = j[j.variant == variant]
        usd = sub.input_token_count.sum() / 1e6 * price
        bars.append((f"Jev · {lang}\n(measured tokens)", usd / len(sub) * 1000, COLOR["jev_114"], None))
    laya = cost[cost.model_track == "laya_routed"].sort_values("usd_per_1000_scenario_decisions")
    for _, r in laya.iterrows():
        hourly = r.basis.split("price ")[1].split(" USD")[0]
        bars.append((f"Laya · host at\n${hourly}/h (assumed)", r.usd_per_1000_scenario_decisions, COLOR["laya_routed"], "//"))
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for k, (label, val, color, hatch) in enumerate(bars):
        ax.bar(k, val, 0.6, color=color if not hatch else SURFACE, edgecolor=color, hatch=hatch, linewidth=2)
        ax.annotate(f"${val:.3f}", (k, val), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=9)
    ax.set_xticks(range(len(bars)), [b[0] for b in bars], fontsize=9)
    ax.set_ylabel("USD per 1,000 decisions")
    _style(ax, "y")
    return _finish(fig, "15 · Cost per 1,000 scenario decisions (hatched = assumed host price)", "15_cost.png")


def render_all(analysis_dir: Path) -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    read = lambda name: pd.read_csv(analysis_dir / name)  # noqa: E731
    s, d, g = read("summary.csv"), read("paired_deltas.csv"), read("language_gaps.csv")
    r, a, h, t, cost = (read(x) for x in ("robustness_p5.csv", "ambiguity_p9.csv", "high_risk.csv", "truncation.csv", "cost.csv"))
    p = pd.read_parquet(analysis_dir / "predictions.parquet")
    price = float(cost[cost.model_track == "jev_114"].price_usd_per_million_input_tokens.iloc[0])
    return [
        fig_quality(s), fig_head_to_head(d), fig_language_gap(g), fig_score_noul(s), fig_checkpoints(s),
        fig_reliability(p), fig_risk_coverage(p), fig_order_robustness(r), fig_cross_language(s), fig_context(s),
        fig_sufficiency(a), fig_high_risk(h), fig_truncation(t), fig_latency(), fig_cost(p, cost, price),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render README figures")
    ap.add_argument("--analysis", default="results/analysis/test")
    args = ap.parse_args(argv)
    for name in render_all(ROOT / args.analysis):
        print(f"docs/figures/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
