"""Build the paired EN/TH dataset for one case.

Every case module ``debench.dataset.eXX`` implements:

    CASE: str                      # "E01"
    BACKEND: str                   # "opensearch"
    generate_specs(seed) -> list[ScenarioSpec]      # 300 specs, gold from injected faults
    db_seed(specs, seed) -> Any                     # payload for EvidenceBackend.load (with distractors)
    make_backend(settings) -> EvidenceBackend
    expected_evidence(spec) -> dict                 # independent pure-Python reference of DB facts
    oracle_check(spec, facts) -> list[str]          # DB facts vs reference/gold; [] = verified
    render_state(spec, facts, lang, context_level) -> dict
    protected_tokens(spec, facts) -> list[str]      # machine tokens that must be byte-identical in EN/TH
    rules_baseline(spec, facts) -> dict[str, Any]   # qid -> label | level | bool | None (abstain)

``evidence_mode="db"`` queries the real engine and sets ``gold_verified`` from ``oracle_check``.
``evidence_mode="expected"`` is an offline fallback: records are marked unverified and must not be
used for final results.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from debench import SEED
from debench.config import ROOT, load_settings
from debench.dataset.paired_generator import assign_splits, p4_subset, validate_pair
from debench.dataset.schema import (
    EVIDENCE_SUFFICIENCY,
    SCENARIOS_PER_CASE,
    ScenarioRecord,
    ScenarioSpec,
    write_jsonl,
)
from debench.payload import build_request
from debench.questions import QuestionSet, load_question_set

GENERATED_DIR = ROOT / "data" / "generated"


def case_module(case: str) -> ModuleType:
    return importlib.import_module(f"debench.dataset.{case.lower()}")


def validate_specs(specs: list[ScenarioSpec], qset: QuestionSet, expected_n: int) -> list[str]:
    problems = []
    if len(specs) != expected_n:
        problems.append(f"expected {expected_n} specs, got {len(specs)}")
    ids = [s.scenario_id for s in specs]
    if len(set(ids)) != len(ids):
        problems.append("duplicate scenario ids")
    for s in specs:
        if set(s.gold) != set(qset.questions):
            problems.append(f"{s.scenario_id}: gold keys {sorted(s.gold)} != questions {sorted(qset.questions)}")
            continue
        if s.evidence_sufficiency not in EVIDENCE_SUFFICIENCY:
            problems.append(f"{s.scenario_id}: bad evidence_sufficiency {s.evidence_sufficiency!r}")
        for qid, q in qset.questions.items():
            g = s.gold[qid]
            if q.primitive == "choice" and g not in q.labels():
                problems.append(f"{s.scenario_id}.{qid}: gold {g!r} not an option")
            elif q.primitive == "score" and not (isinstance(g, int) and 0 <= g < q.n_levels()):
                problems.append(f"{s.scenario_id}.{qid}: gold level {g!r} out of range")
            elif q.primitive == "noul" and not isinstance(g, bool):
                problems.append(f"{s.scenario_id}.{qid}: gold must be bool")
    return problems


@dataclass
class BuildReport:
    case: str
    seed: int
    evidence_mode: str
    context_level: str
    backend: str
    runtime: str | None = None
    engine_version: str | None = None
    questions_version: str = ""
    questions_hash: str = ""
    split_counts: dict[str, int] = field(default_factory=dict)
    sufficiency_counts: dict[str, int] = field(default_factory=dict)
    class_balance: dict[str, dict[str, int]] = field(default_factory=dict)
    spec_problems: list[str] = field(default_factory=list)
    oracle_failures: dict[str, list[str]] = field(default_factory=dict)
    pair_failures: dict[str, list[str]] = field(default_factory=dict)
    gold_verified: int = 0
    records: int = 0
    output: str = ""
    output_sha256: str = ""
    # Hash of the records with run-dependent timings removed; identical across rebuilds of one seed.
    content_sha256: str = ""
    db_retrieval_ms_p50: float | None = None
    db_retrieval_ms_p95: float | None = None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, round(q * (len(vals) - 1))))
    return vals[idx]


def build_case(
    case: str,
    seed: int = SEED,
    evidence_mode: str = "db",
    context_level: str = "common_fit",
    out_dir: Path = GENERATED_DIR,
    expected_n: int = SCENARIOS_PER_CASE,
) -> BuildReport:
    mod = case_module(case)
    qset = load_question_set(case)
    specs = mod.generate_specs(seed)
    report = BuildReport(
        case=mod.CASE,
        seed=seed,
        evidence_mode=evidence_mode,
        context_level=context_level,
        backend=mod.BACKEND,
        questions_version=qset.version,
        questions_hash=qset.content_hash,
    )
    report.spec_problems = validate_specs(specs, qset, expected_n)
    if report.spec_problems:
        raise ValueError(f"{case}: invalid specs: {report.spec_problems[:10]}")
    assign_splits(specs, seed)

    backend = None
    if evidence_mode == "db":
        backend = mod.make_backend(load_settings())
        backend.setup()
        report.runtime, report.engine_version = backend.runtime, backend.engine_version()
        backend.load(mod.db_seed(specs, seed))

    records: list[ScenarioRecord] = []
    sidecar: list[dict[str, Any]] = []
    db_times: list[float] = []
    try:
        for spec in specs:
            if backend is not None:
                ev = backend.retrieve(spec)
                facts, ref, db_ms = ev.facts, ev.ref, ev.db_ms
                problems = mod.oracle_check(spec, facts)
                db_times.append(db_ms)
            else:
                facts = mod.expected_evidence(spec)
                ref, db_ms, problems = {"backend": mod.BACKEND, "mode": "expected"}, None, ["not_db_verified"]
            if problems:
                report.oracle_failures[spec.scenario_id] = problems
            # Evidence facts and the rules baseline are language-invariant and never provider-visible.
            sidecar.append(
                {
                    "scenario_id": spec.scenario_id,
                    "split": spec.split,
                    "facts": facts,
                    "internal": spec.facts.get("_internal", {}),
                    "difficulty_tags": spec.difficulty_tags,
                    "rules_baseline": mod.rules_baseline(spec, facts),
                }
            )
            states = {lang: mod.render_state(spec, facts, lang, context_level) for lang in ("en", "th")}
            tokens = mod.protected_tokens(spec, facts)
            pair_problems = validate_pair(states["en"], states["th"], tokens)
            if pair_problems:
                report.pair_failures[spec.scenario_id] = pair_problems
            for lang, state in states.items():
                records.append(
                    ScenarioRecord(
                        scenario_id=spec.scenario_id,
                        experiment=spec.experiment,
                        split=spec.split or "",
                        seed=spec.seed,
                        language=lang,
                        pair_id=spec.scenario_id,
                        group_id=spec.group_id,
                        state=state,
                        evidence_ref=ref,
                        context_level=context_level,
                        questions_version=qset.version,
                        gold=spec.gold,
                        difficulty_tags=spec.difficulty_tags,
                        evidence_sufficiency=spec.evidence_sufficiency,
                        label_source=spec.label_source,
                        gold_verified=not problems,
                        semantic_pair_validated=not pair_problems,
                        protected_tokens=tokens,
                        db_retrieval_ms=db_ms,
                    )
                )
    finally:
        if backend is not None:
            backend.teardown()

    subset = p4_subset([r.scenario_id for r in records if r.split == "test" and r.language == "en"], seed)
    for rec in records:
        rec.p4_subset = rec.scenario_id in subset
        build_request(rec, qset)  # raises LeakageError on any gold/internal leak

    out = out_dir / mod.CASE.lower() / f"{context_level}.jsonl"
    write_jsonl(out, records)
    with (out.parent / f"{context_level}.evidence.jsonl").open("w", encoding="utf-8") as fh:
        for row in sidecar:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    report.records = len(records)
    report.gold_verified = sum(1 for s in specs if s.scenario_id not in report.oracle_failures)
    report.split_counts = dict(Counter(s.split for s in specs))
    report.sufficiency_counts = dict(Counter(s.evidence_sufficiency for s in specs))
    report.class_balance = {
        qid: dict(Counter(str(s.gold[qid]) for s in specs if s.split == "test")) for qid in qset.questions
    }
    report.output = str(out.relative_to(ROOT))
    report.output_sha256 = hashlib.sha256(out.read_bytes()).hexdigest()
    stable = [json.dumps({**asdict(r), "db_retrieval_ms": None}, ensure_ascii=False, sort_keys=True) for r in records]
    report.content_sha256 = hashlib.sha256("\n".join(stable).encode("utf-8")).hexdigest()
    report.db_retrieval_ms_p50 = _percentile(db_times, 0.5)
    report.db_retrieval_ms_p95 = _percentile(db_times, 0.95)
    (out.parent / f"{context_level}.build.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def load_evidence(case: str, context_level: str = "common_fit") -> dict[str, dict[str, Any]]:
    path = GENERATED_DIR / case.lower() / f"{context_level}.evidence.jsonl"
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {row["scenario_id"]: row for row in rows}


def load_records(case: str, context_level: str = "common_fit") -> list[ScenarioRecord]:
    from debench.dataset.schema import read_jsonl

    return read_jsonl(GENERATED_DIR / case.lower() / f"{context_level}.jsonl")


def summarize(report: BuildReport) -> dict[str, Any]:
    return {
        "case": report.case,
        "mode": report.evidence_mode,
        "runtime": report.runtime,
        "engine": report.engine_version,
        "records": report.records,
        "gold_verified": report.gold_verified,
        "oracle_failures": len(report.oracle_failures),
        "pair_failures": len(report.pair_failures),
        "splits": report.split_counts,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the paired EN/TH dataset for one case")
    parser.add_argument("case", help="E01..E06")
    parser.add_argument("--mode", choices=["db", "expected"], default="db")
    parser.add_argument("--context", default="common_fit")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    report = build_case(args.case.upper(), seed=args.seed, evidence_mode=args.mode, context_level=args.context)
    print(json.dumps(summarize(report), ensure_ascii=False, indent=2))
    return 0 if not report.oracle_failures and not report.pair_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
