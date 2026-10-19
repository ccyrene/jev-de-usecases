"""Experiment runner.

Every model track receives the same provider-visible request (state + questions) for a work item;
only the provider envelope differs. One row per (request, question) is appended to
``results/runs/<run_name>/predictions.jsonl``; reruns with the same run name resume and skip rows
already written. A track whose provider is unavailable (e.g. Jev without ALLOW_REMOTE_API=1) is
recorded as BLOCKED in the manifest and produces no rows.

    python -m debench.runner --case E01 --tracks laya_routed,laya_en_fixed,laya_multi_fixed \
        --protocol primary --split dev --n 20 --run-name pilot_laya
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import random
import time
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from debench import SEED, STUDY_ID
from debench.config import CONFIG_DIR, ROOT, Settings, load_settings
from debench.dataset.build import GENERATED_DIR, load_evidence, load_records
from debench.dataset.schema import ScenarioRecord
from debench.normalize import NormalizedAnswer, normalize_response
from debench.payload import build_request, payload_bytes
from debench.providers.base import ERROR_AUTH, ERROR_VERSION, DecisionRequest, DecisionResponse
from debench.questions import QuestionSet, load_question_set

RUNS_DIR = ROOT / "results" / "runs"

# protocol -> [(state_language, question_language)]
LANGUAGE_VARIANTS: dict[str, list[tuple[str, str]]] = {
    "P1_primary_en": [("en", "en")],
    "P2_primary_th": [("th", "th")],
    "primary": [("en", "en"), ("th", "th")],
    "P3_checkpoint": [("en", "en"), ("th", "th")],
    "P4_cross_language": [("en", "en"), ("th", "th"), ("th", "en"), ("en", "th")],
    "P5_order": [("en", "en"), ("th", "th")],
}
P5_PERMUTATION_SEEDS = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class WorkItem:
    record: ScenarioRecord  # record in the state language
    question_language: str
    variant: str
    option_orders: dict[str, list[str]] | None = None


def select_pairs(
    records: list[ScenarioRecord], split: str, n: int | None, seed: int, p4_only: bool = False
) -> dict[str, dict[str, ScenarioRecord]]:
    """Deterministic, track-independent scenario selection: every track sees the same pair ids."""
    pairs: dict[str, dict[str, ScenarioRecord]] = {}
    for rec in records:
        if rec.split == split and (rec.p4_subset or not p4_only):
            pairs.setdefault(rec.pair_id, {})[rec.language] = rec
    ids = sorted(pairs)
    if n is not None and n < len(ids):
        ids = sorted(random.Random(seed).sample(ids, n))
    return {pid: pairs[pid] for pid in ids}


def _permutation(labels: list[str], scenario_id: str, perm_seed: int) -> list[str]:
    order = list(labels)
    random.Random(f"{scenario_id}:{perm_seed}").shuffle(order)
    return order


def work_items(
    protocol: str,
    pairs: dict[str, dict[str, ScenarioRecord]],
    qset: QuestionSet,
    perm_seeds: tuple[int, ...] = P5_PERMUTATION_SEEDS,
) -> list[WorkItem]:
    items = []
    for by_lang in pairs.values():
        for state_lang, question_lang in LANGUAGE_VARIANTS[protocol]:
            rec = by_lang[state_lang]
            items.append(WorkItem(rec, question_lang, f"{state_lang}_{question_lang}"))
            if protocol == "P5_order":
                choice_qids = [qid for qid, q in qset.questions.items() if q.primitive == "choice"]
                for s in perm_seeds:
                    orders = {qid: _permutation(qset.questions[qid].labels(), rec.scenario_id, s) for qid in choice_qids}
                    items.append(WorkItem(rec, question_lang, f"{state_lang}_{question_lang}_perm{s}", orders))
    return items


def _gold_fields(primitive: str, gold: Any) -> dict[str, Any]:
    return {
        "gold_label": gold if primitive == "choice" else None,
        "gold_level": gold if primitive == "score" else None,
        "gold_binary": gold if primitive == "noul" else None,
    }


def _context_bucket(nbytes: int) -> str:
    if nbytes < 2_000:
        return "short"
    if nbytes < 6_000:
        return "medium"
    if nbytes < 20_000:
        return "long"
    return "very_long"


def prediction_rows(
    run_id: str,
    protocol: str,
    track: str,
    item: WorkItem,
    request: DecisionRequest,
    response: DecisionResponse,
    normalized: list[NormalizedAnswer],
) -> list[dict[str, Any]]:
    rec, meta = item.record, response.metadata
    nbytes = payload_bytes(request)
    truncated_qs = set(meta.get("truncated_questions") or [])
    rows = []
    for ans in normalized:
        rows.append(
            {
                "run_id": run_id,
                "protocol": protocol,
                "variant": item.variant,
                "scenario_id": rec.scenario_id,
                "pair_id": rec.pair_id,
                "experiment_id": rec.experiment,
                "split": rec.split,
                "language": rec.language,
                "state_language": rec.language,
                "question_language": item.question_language,
                "model_track": track,
                "provider": _track_config(track)["provider"],
                "provider_name": response.provider,
                "resolved_model": response.resolved_model,
                "checkpoint_revision": meta.get("revision"),
                "routing_model": (meta.get("routing") or {}).get("model"),
                "question_id": ans.question_id,
                "primitive": ans.primitive,
                **_gold_fields(ans.primitive, rec.gold[ans.question_id]),
                "predicted_label": ans.predicted_label,
                "predicted_level": ans.predicted_level,
                "score_value": ans.score_value,
                "noul_probability": ans.noul_probability,
                "distribution_json": json.dumps(ans.distribution) if ans.distribution else None,
                "confidence_vendor": ans.confidence_vendor,
                "max_probability": ans.max_probability,
                "normalized_entropy": ans.normalized_entropy,
                "api_choice_matches_argmax": ans.api_choice_matches_argmax,
                "calibration_version": "raw",
                "input_token_count": response.usage_input_tokens,
                "output_token_count": response.usage_output_tokens,
                "request_bytes": nbytes,
                "request_hash": request.request_hash(),
                "db_retrieval_ms": rec.db_retrieval_ms,
                "model_wall_ms": response.wall_ms,
                # Phases are measured separately; an end-to-end pipeline timing comes only from P8.
                "full_pipeline_ms": None,
                "attempts": meta.get("attempts", 1),
                "cached": bool(meta.get("cached", False)),
                "error_type": ans.error_type,
                "valid_answer": ans.valid,
                "context_level": rec.context_level,
                "context_bucket": _context_bucket(nbytes),
                "was_truncated": bool(meta.get("truncated")) or ans.question_id in truncated_qs,
                "unsupported_context": response.error == "unsupported_context",
                "device": meta.get("device"),
                "evidence_sufficiency": rec.evidence_sufficiency,
                "difficulty_tags": rec.difficulty_tags,
                "p4_subset": rec.p4_subset,
                "is_mock": bool(meta.get("mock", False)),
                "seed": rec.seed,
                "timestamp": datetime.now().astimezone().isoformat(),
            }
        )
    return rows


def rules_rows(
    run_id: str, protocol: str, item: WorkItem, qset: QuestionSet, predictions: dict[str, Any]
) -> list[dict[str, Any]]:
    """Deterministic rules baseline: language-invariant, no probabilities, None = abstain."""
    rec = item.record
    rows = []
    for qid, q in qset.questions.items():
        pred = predictions.get(qid)
        rows.append(
            {
                "run_id": run_id,
                "protocol": protocol,
                "variant": item.variant,
                "scenario_id": rec.scenario_id,
                "pair_id": rec.pair_id,
                "experiment_id": rec.experiment,
                "split": rec.split,
                "language": rec.language,
                "state_language": rec.language,
                "question_language": item.question_language,
                "model_track": "rules_only",
                "provider": "rules",
                "resolved_model": f"rules:{qset.version}",
                "question_id": qid,
                "primitive": q.primitive,
                **_gold_fields(q.primitive, rec.gold[qid]),
                "predicted_label": pred if q.primitive == "choice" else None,
                "predicted_level": pred if q.primitive == "score" else None,
                "score_value": float(pred) if q.primitive == "score" and pred is not None else None,
                "noul_probability": (1.0 if pred else 0.0) if q.primitive == "noul" and pred is not None else None,
                "calibration_version": "raw",
                "error_type": None if pred is not None else "abstain",
                "valid_answer": pred is not None,
                "context_level": rec.context_level,
                "evidence_sufficiency": rec.evidence_sufficiency,
                "difficulty_tags": rec.difficulty_tags,
                "p4_subset": rec.p4_subset,
                "is_mock": False,
                "seed": rec.seed,
            }
        )
    return rows


def _done_keys(path: Path) -> set[tuple[str, str, str]]:
    keys = set()
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                keys.add((row["model_track"], row["scenario_id"], row["variant"]))
    return keys


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@lru_cache(maxsize=None)
def _track_config(track: str) -> dict[str, Any]:
    models = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    if track not in models["tracks"]:
        raise KeyError(f"unknown track {track!r}")
    return models["tracks"][track]


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def run_provider_track(
    track: str,
    provider: Any,
    items: list[WorkItem],
    qset: QuestionSet,
    out: Path,
    run_id: str,
    protocol: str,
    concurrency: int,
) -> dict[str, Any]:
    done = _done_keys(out)
    todo = [it for it in items if (track, it.record.scenario_id, it.variant) not in done]
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counts = {"requests": 0, "errors": 0, "skipped_existing": len(items) - len(todo)}

    async def one(item: WorkItem) -> None:
        request = build_request(item.record, qset, item.question_language, item.option_orders)
        async with sem:
            response = await provider.evaluate(request)
        rows = prediction_rows(run_id, protocol, track, item, request, response, normalize_response(request, response))
        async with lock:
            _append(out, rows)
            counts["requests"] += 1
            counts["errors"] += response.error is not None

    await asyncio.gather(*(one(it) for it in todo))
    return counts


def _provider_blocked(track_cfg: dict[str, Any], settings: Settings) -> str | None:
    if track_cfg["provider"] == "jev" and not settings.remote_enabled:
        reasons = []
        if not settings.allow_remote_api:
            reasons.append("ALLOW_REMOTE_API is not 1")
        if not settings.is_set("TYPESAFE_API_KEY"):
            reasons.append("TYPESAFE_API_KEY is empty")
        return "; ".join(reasons)
    return None


async def run(
    cases: list[str],
    tracks: list[str],
    protocol: str,
    split: str,
    n: int | None,
    run_name: str,
    context_level: str = "common_fit",
    concurrency: int = 1,
    warmup: int = 0,
    seed: int = SEED,
    device: str | None = None,
    perm_seeds: tuple[int, ...] = P5_PERMUTATION_SEEDS,
) -> dict[str, Any]:
    settings = load_settings()
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "predictions.jsonl"
    manifest_path = run_dir / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update(
        {
            "study_id": STUDY_ID,
            "run_id": run_name,
            "protocol": protocol,
            "split": split,
            "n_pairs_per_case": n,
            "context_level": context_level,
            "seed": seed,
            "pilot": n is not None,
            "perm_seeds": list(perm_seeds) if protocol == "P5_order" else None,
            "started": manifest.get("started") or datetime.now().astimezone().isoformat(),
            "settings": settings.redacted(),
        }
    )
    manifest.setdefault("cases", {})
    manifest.setdefault("tracks", {})

    case_inputs = {}
    for case in cases:
        if split == "test":
            from debench.freeze import check_frozen

            check_frozen(case, context_level)
        qset = load_question_set(case)
        records = load_records(case, context_level)
        build_report = json.loads((GENERATED_DIR / case.lower() / f"{context_level}.build.json").read_text())
        pairs = select_pairs(records, split, n, seed, p4_only=protocol == "P4_cross_language")
        items = work_items(protocol, pairs, qset, perm_seeds)
        case_inputs[case] = (qset, items)
        manifest["cases"][case] = {
            "questions_version": qset.version,
            "questions_hash": qset.content_hash,
            "dataset_sha256": build_report["output_sha256"],
            "evidence_mode": build_report["evidence_mode"],
            "engine": {"runtime": build_report["runtime"], "version": build_report["engine_version"]},
            "pair_ids": sorted(pairs),
            "work_items": len(items),
        }

    from debench.providers.registry import make_provider

    for track in tracks:
        cfg = _track_config(track)
        status: dict[str, Any] = {"provider": cfg["provider"]}
        blocked = _provider_blocked(cfg, settings)
        if blocked:
            status.update(status="BLOCKED", reason=blocked)
            manifest["tracks"][track] = status
            continue
        if cfg["provider"] == "rules":
            for case, (qset, items) in case_inputs.items():
                evidence = load_evidence(case, context_level)
                done = _done_keys(out)
                for item in items:
                    if ("rules_only", item.record.scenario_id, item.variant) in done or item.option_orders:
                        continue
                    _append(out, rules_rows(run_name, protocol, item, qset, evidence[item.record.scenario_id]["rules_baseline"]))
            status["status"] = "OK"
            manifest["tracks"][track] = status
            continue
        provider = make_provider(track, settings, device=device)
        if warmup and hasattr(provider, "warmup"):
            t0 = time.perf_counter()
            status["warmup"] = await _maybe_await(provider.warmup(warmup))
            status["warmup_wall_ms"] = (time.perf_counter() - t0) * 1000.0
        if cfg["provider"] == "jev":
            # P0 preflight: one real request; auth or version failure blocks the track before any bulk spend.
            qset0, items0 = next(iter(case_inputs.values()))
            probe = await provider.evaluate(build_request(items0[0].record, qset0, items0[0].question_language))
            status["preflight"] = {"error": probe.error, "resolved_model": probe.resolved_model,
                                   "status_codes": probe.metadata.get("status_codes")}
            if probe.error in (ERROR_AUTH, ERROR_VERSION):
                status.update(status="BLOCKED", reason=f"preflight {probe.error}")
                manifest["tracks"][track] = status
                await provider.aclose()
                continue
        status["per_case"] = {}
        for case, (qset, items) in case_inputs.items():
            counts = await run_provider_track(track, provider, items, qset, out, run_name, protocol, concurrency)
            status["per_case"][case] = counts
        status["status"] = "OK"
        if hasattr(provider, "describe"):
            status["describe"] = provider.describe()
        manifest["tracks"][track] = status
        close = getattr(provider, "aclose", None)
        if close:
            await close()

    manifest["finished"] = datetime.now().astimezone().isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", default="E01", help="comma list, or 'all'")
    parser.add_argument("--tracks", default="laya_routed")
    parser.add_argument("--protocol", default="primary", choices=sorted(LANGUAGE_VARIANTS))
    parser.add_argument("--split", default="dev", choices=["dev", "calibration", "test"])
    parser.add_argument("--n", type=int, default=None, help="pilot size (paired scenarios per case)")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--context", default="common_fit")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--device", default=None, help="Laya device (cpu|cuda); default LAYA_DEVICE")
    parser.add_argument("--perm-seeds", default="1,2,3,4,5", help="P5 option-order permutation seeds")
    args = parser.parse_args(argv)
    cases = ["E01", "E02", "E03", "E04", "E05", "E06"] if args.case == "all" else args.case.upper().split(",")
    manifest = asyncio.run(
        run(
            cases,
            args.tracks.split(","),
            args.protocol,
            args.split,
            args.n,
            args.run_name,
            args.context,
            args.concurrency,
            args.warmup,
            device=args.device,
            perm_seeds=tuple(int(x) for x in args.perm_seeds.split(",")),
        )
    )
    print(json.dumps({k: manifest[k] for k in ("run_id", "protocol", "tracks")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
