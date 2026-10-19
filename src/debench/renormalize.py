"""Re-apply the current normalizer to stored Jev answers.

Rows rejected by an earlier normalizer version (e.g. a sum tolerance that ignored Jev's 2-decimal
rounding) are recomputed from the raw answers kept in the resume cache. Only answer fields change;
timing, usage and request metadata stay as measured. Updated rows are tagged ``renormalized``.

    python -m debench.renormalize --runs pilot_jev,test_primary_jev
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from debench.normalize import normalize_answer
from debench.providers.budget import DEFAULT_CACHE
from debench.questions import load_question_set
from debench.runner import RUNS_DIR

NORMALIZATION_ERRORS = {
    "bad_sum", "label_set_mismatch", "level_set_mismatch", "non_finite", "out_of_range",
    "unknown_choice", "empty_distribution", "missing_answer", "type_mismatch",
}
TAG = "v2-reported-precision-tolerance"


def _cache() -> dict[str, dict[str, Any]]:
    out = {}
    with DEFAULT_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out[row["request_hash"]] = row["response"]
    return out


def renormalize_run(run_name: str, cache: dict[str, dict[str, Any]]) -> dict[str, int]:
    path = RUNS_DIR / run_name / "predictions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = {"rows": len(rows), "candidates": 0, "now_valid": 0, "still_invalid": 0, "no_raw": 0}
    for row in rows:
        if row.get("provider") != "jev" or row["valid_answer"] or row["error_type"] not in NORMALIZATION_ERRORS:
            continue
        counts["candidates"] += 1
        raw = cache.get(row["request_hash"])
        if raw is None:
            counts["no_raw"] += 1
            continue
        qdef = load_question_set(row["experiment_id"]).questions[row["question_id"]].render(row["question_language"])
        ans = normalize_answer(row["question_id"], qdef, raw["answers"].get(row["question_id"]))
        a = asdict(ans)
        row.update(
            predicted_label=a["predicted_label"],
            predicted_level=a["predicted_level"],
            score_value=a["score_value"],
            noul_probability=a["noul_probability"],
            distribution_json=json.dumps(a["distribution"]) if a["distribution"] else None,
            confidence_vendor=a["confidence_vendor"],
            max_probability=a["max_probability"],
            normalized_entropy=a["normalized_entropy"],
            api_choice_matches_argmax=a["api_choice_matches_argmax"],
            error_type=a["error_type"],
            valid_answer=a["valid"],
            renormalized=TAG,
        )
        counts["now_valid" if ans.valid else "still_invalid"] += 1
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", required=True)
    args = ap.parse_args(argv)
    cache = _cache()
    for run in args.runs.split(","):
        print(run, renormalize_run(run, cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
