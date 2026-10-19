"""P8 latency harness for local model tracks (in-process topology).

Measures warm per-request latency at several concurrency levels and question counts, with three
repeats in seeded shuffled order. Checkpoint load (cold) time is reported separately and never
mixed into warm percentiles. Also records an unauthenticated network RTT baseline to the remote
API host, so remote latency can be put in context without sending credentials.

    python -m debench.latency_bench --track laya_routed --device cuda --pairs 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from datetime import datetime
from typing import Any

import httpx
import numpy as np

from debench import SEED
from debench.config import ROOT, load_settings
from debench.dataset.build import load_records
from debench.payload import build_request, payload_bytes
from debench.questions import load_question_set
from debench.runner import select_pairs

OUT_DIR = ROOT / "results" / "latency"
CASES = ("E01", "E02", "E03", "E04", "E05", "E06")


class _NoCache:
    """Never stores or returns responses, so repeated requests always hit the network."""

    def get(self, request_hash: str, model: str) -> None:
        return None

    def put(self, request_hash: str, model: str, response: dict[str, Any]) -> None:
        return None


def rtt_baseline(url: str = "https://api.typesafe.ai/", n: int = 10) -> dict[str, Any]:
    """Unauthenticated GETs: connection + TLS + server round trip, no request body or key."""
    samples, status = [], None
    with httpx.Client(timeout=10) as client:
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                status = client.get(url).status_code
            except httpx.HTTPError as exc:
                status = type(exc).__name__
                continue
            samples.append((time.perf_counter() - t0) * 1000.0)
    return {"url": url, "n": len(samples), "status": status,
            "p50_ms": float(np.percentile(samples, 50)) if samples else None,
            "p95_ms": float(np.percentile(samples, 95)) if samples else None,
            "note": "persistent connection after the first request; first sample includes TLS setup"}


async def bench(track: str, device: str, pairs_per_case: int, split: str, concurrency: list[int],
                question_modes: list[str], repeats: int, warmup: int) -> dict[str, Any]:
    from debench.providers.registry import make_provider

    settings = load_settings()
    provider = make_provider(track, settings, device=device)
    if hasattr(provider, "cache"):
        provider.cache = _NoCache()  # every latency sample is a fresh network request
    t0 = time.perf_counter()
    warm = await provider.warmup(warmup) if hasattr(provider, "warmup") and warmup else None
    load_info = {"warmup_call": warm, "warmup_wall_ms": (time.perf_counter() - t0) * 1000.0}

    requests = []
    for case in CASES:
        qset = load_question_set(case)
        pairs = select_pairs(load_records(case), split, pairs_per_case, SEED)
        for by_lang in pairs.values():
            for lang in ("en", "th"):
                for mode in question_modes:
                    only = [next(iter(qset.questions))] if mode == "1" else None
                    req = build_request(by_lang[lang], qset, only=only)
                    requests.append((case, lang, mode, req))

    samples = []
    for conc in concurrency:
        for rep in range(repeats):
            order = list(range(len(requests)))
            random.Random(SEED + 100 * conc + rep).shuffle(order)
            sem = asyncio.Semaphore(conc)

            async def one(i: int) -> None:
                case, lang, mode, req = requests[i]
                async with sem:
                    t_start = time.perf_counter()
                    resp = await provider.evaluate(req)
                    e2e = (time.perf_counter() - t_start) * 1000.0
                samples.append({"track": track, "device": device, "concurrency": conc, "repeat": rep,
                                "case": case, "language": lang, "questions": mode, "n_questions": len(req.questions),
                                "request_bytes": payload_bytes(req), "model_wall_ms": resp.wall_ms,
                                "client_e2e_ms": e2e, "ok": resp.error is None, "cold": False,
                                "truncated": bool(resp.metadata.get("truncated")),
                                "attempts": resp.metadata.get("attempts", 1), "error": resp.error,
                                "cached": bool(resp.metadata.get("cached", False))})

            t_batch = time.perf_counter()
            await asyncio.gather(*(one(i) for i in order))
            batch_s = time.perf_counter() - t_batch
            samples.append({"track": track, "device": device, "concurrency": conc, "repeat": rep,
                            "batch_requests": len(order), "batch_wall_s": batch_s, "summary_row": True})
    return {"load": load_info, "samples": samples}


def summarize(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows, groups = [], {}
    for s in samples:
        if s.get("summary_row") or not s["ok"] or s.get("cached"):
            continue
        groups.setdefault((s["concurrency"], s["questions"], s["language"]), []).append(s["client_e2e_ms"])
    batches = {}
    for s in samples:
        if s.get("summary_row"):
            batches.setdefault(s["concurrency"], []).append(s["batch_requests"] / s["batch_wall_s"])
    for (conc, mode, lang), vals in sorted(groups.items()):
        rows.append({"concurrency": conc, "questions": mode, "language": lang, "n": len(vals),
                     "p50_ms": float(np.percentile(vals, 50)), "p95_ms": float(np.percentile(vals, 95)),
                     "p99_ms": float(np.percentile(vals, 99)), "mean_ms": statistics.fmean(vals),
                     "throughput_rps": statistics.fmean(batches[conc]) if conc in batches else None})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--track", default="laya_routed")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--pairs", type=int, default=100)
    ap.add_argument("--split", default="test")
    ap.add_argument("--concurrency", default="1,4,8")
    ap.add_argument("--questions", default="1,all")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=30)
    args = ap.parse_args(argv)
    result = asyncio.run(bench(args.track, args.device, args.pairs, args.split,
                               [int(c) for c in args.concurrency.split(",")], args.questions.split(","),
                               args.repeats, args.warmup))
    result["rtt_baseline_remote_api"] = rtt_baseline()
    result["summary"] = summarize(result["samples"])
    result["measured_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    result["topology"] = ("remote_https_from_this_host" if args.track.startswith("jev")
                          else "in_process_local (not comparable to remote HTTPS latency)")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    device = "remote" if args.track.startswith("jev") else args.device
    out = OUT_DIR / f"{args.track}_{device}.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    for row in result["summary"]:
        print(json.dumps(row))
    print(json.dumps(result["rtt_baseline_remote_api"]))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
