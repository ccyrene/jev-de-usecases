"""Provider smoke test on synthetic examples.

    python -m debench.smoke --provider laya --device cuda
    python -m debench.smoke --provider jev          # reports BLOCKED unless remote is enabled

Real local inference only for Laya. The Jev smoke never enables remote access itself and, even if
the environment enables it, sends nothing unless ``--allow-paid-smoke`` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("USE_TF", "0")

from debench.config import ROOT, load_settings  # noqa: E402
from debench.normalize import normalize_response  # noqa: E402
from debench.providers.base import DecisionRequest  # noqa: E402

SMOKE_DIR = ROOT / "results" / "smoke"
LAYA_TRACKS = ("laya_routed", "laya_en_fixed", "laya_multi_fixed")
JEV_TRACKS = ("jev_114",)


def _qs(family_q: str, family: dict[str, str], sev_q: str, sev: list[str], human_q: str) -> dict[str, Any]:
    return {
        "failure_family": {"type": "choice", "instructions": family_q, "criteria": family},
        "severity": {"type": "score", "instructions": sev_q, "criteria": sev},
        "requires_human": {"type": "noul", "instructions": human_q},
    }


def examples() -> list[DecisionRequest]:
    """Three English and three Thai synthetic examples (PLAN sections 2 and 15)."""
    en_q = _qs(
        "Which failure family best matches the evidence?",
        {
            "schema_mismatch": "A referenced table or column is missing or structurally incompatible",
            "connectivity": "Network, DNS, or database reachability failure",
            "authentication": "Invalid authentication or insufficient permissions",
            "data_error": "Invalid values or input-data integrity problem",
            "resource_exhaustion": "Memory, CPU, quota, or disk exhaustion",
            "other_unknown": "Another cause or not enough evidence",
        },
        "Rate expected operational severity from the visible evidence.",
        [
            "0 = minor, non-blocking",
            "1 = degraded but recoverable",
            "2 = important pipeline blocked",
            "3 = critical multiple downstream consumers blocked",
        ],
        "Does this incident require a human review before changing a production system?",
    )
    th_q = _qs(
        "ปัญหานี้ควรจัดอยู่ในประเภทใด",
        {
            "schema_mismatch": "ตารางหรือคอลัมน์ที่อ้างถึงหายไปหรือโครงสร้างไม่เข้ากัน",
            "connectivity": "ปัญหาเครือข่าย DNS หรือการเข้าถึงฐานข้อมูล",
            "authentication": "การยืนยันตัวตนไม่ถูกต้องหรือสิทธิ์ไม่เพียงพอ",
            "data_error": "ค่าข้อมูลไม่ถูกต้องหรือข้อมูลนำเข้าขาดความสมบูรณ์",
            "resource_exhaustion": "หน่วยความจำ CPU โควตา หรือดิสก์ถูกใช้จนหมด",
            "other_unknown": "สาเหตุอื่นหรือหลักฐานไม่เพียงพอ",
        },
        "ประเมินความรุนแรงเชิงปฏิบัติการจากหลักฐานที่เห็น",
        [
            "0 = เล็กน้อย ไม่ขัดขวางการทำงาน",
            "1 = ประสิทธิภาพลดลงแต่กู้คืนได้",
            "2 = ไปป์ไลน์สำคัญถูกบล็อก",
            "3 = วิกฤต ผู้ใช้ข้อมูลปลายทางหลายรายถูกบล็อก",
        ],
        "เหตุการณ์นี้จำเป็นต้องให้มนุษย์ตรวจสอบก่อนดำเนินการหรือไม่",
    )
    canon_en = {
        "dag": "daily_customer_etl",
        "task": "transform_customer_data",
        "log_excerpt": "psycopg2.errors.UndefinedColumn: column customer_id does not exist",
        "observed": "This task started failing after a deployment.",
    }
    canon_th = dict(canon_en, observed="งานนี้เริ่มล้มเหลวหลังจากมีการปรับใช้ระบบ")
    return [
        DecisionRequest("smoke_canonical", "en", canon_en, en_q),
        DecisionRequest("smoke_e01", "en", "Customer pipeline failed after a database column rename.", en_q),
        DecisionRequest("smoke_e02", "en", "The row count dropped abnormally after the latest batch load.", en_q),
        DecisionRequest("smoke_canonical", "th", canon_th, th_q),
        DecisionRequest("smoke_e01", "th", "งานประมวลผลลูกค้าล้มเหลวหลังจากเปลี่ยนชื่อคอลัมน์ในฐานข้อมูล", th_q),
        DecisionRequest("smoke_e02", "th", "จำนวนแถวลดลงผิดปกติหลังจากการโหลดข้อมูลรอบล่าสุด", th_q),
    ]


def host_rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return float("nan")


def _row(request: DecisionRequest, resp: Any) -> dict[str, Any]:
    norm = normalize_response(request, resp)
    md = resp.metadata
    return {
        "scenario_id": request.scenario_id,
        "language": request.language,
        "track": resp.provider,
        "error": resp.error,
        "resolved_model": resp.resolved_model,
        "wall_ms": round(resp.wall_ms, 3),
        "load_ms": round(md.get("load_ms", 0.0), 1),
        "cold": md.get("cold"),
        "routing": md.get("routing"),
        "truncated": md.get("truncated"),
        "state_tokens": md.get("state_tokens"),
        "state_tokens_dropped": md.get("state_tokens_dropped"),
        "truncated_questions": md.get("truncated_questions"),
        "max_len": md.get("max_len"),
        "head_max_len": md.get("head_max_len"),
        "normalized": [
            {k: v for k, v in n.__dict__.items() if v is not None and k != "distribution"} | {"distribution": n.distribution}
            for n in norm
        ],
    }


async def run_laya(device: str, warmup: int) -> dict[str, Any]:
    import torch

    from debench.providers.registry import make_provider

    settings = load_settings()
    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    rows, warm = [], {}
    for track in LAYA_TRACKS:
        provider = make_provider(track, settings, device=device)
        warm[track] = await provider.warmup(warmup)
        for req in examples():
            rows.append(_row(req, await provider.evaluate(req)))
    import laya

    return {
        "provider": "laya",
        "device": device,
        "gpu": torch.cuda.get_device_name(0) if device.startswith("cuda") else None,
        "peak_gpu_allocated_mb": torch.cuda.max_memory_allocated() / 2**20 if device.startswith("cuda") else None,
        "host_rss_mb": host_rss_mb(),
        "versions": {"laya": laya.__version__, "torch": torch.__version__},
        "warmup": warm,
        "results": rows,
    }


async def run_jev(allow_paid: bool) -> dict[str, Any]:
    from debench.providers.registry import make_provider

    settings = load_settings()
    status = "enabled" if settings.remote_enabled else "BLOCKED"
    out: dict[str, Any] = {"provider": "jev", "remote_status": status, "results": []}
    if settings.remote_enabled and not allow_paid:
        out["remote_status"] = "BLOCKED (remote is enabled, pass --allow-paid-smoke to send paid requests)"
        return out
    for track in JEV_TRACKS:
        provider = make_provider(track, settings)
        for req in examples():
            out["results"].append(_row(req, await provider.evaluate(req)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m debench.smoke")
    ap.add_argument("--provider", choices=("laya", "jev"), required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--allow-paid-smoke", action="store_true")
    args = ap.parse_args(argv)
    if args.provider == "laya":
        report = asyncio.run(run_laya(args.device, args.warmup))
    else:
        report = asyncio.run(run_jev(args.allow_paid_smoke))
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    path = SMOKE_DIR / f"{args.provider}_smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"saved {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
