"""Local Laya adapter: real in-process inference, no network at call time after the weights are cached.

All three Laya tracks share ONE module-level ``laya.Router`` per (device, revisions), so each
checkpoint is loaded once per process. The Router is built with ``preload=False`` and then
``router.preload(["english", "multilingual"])`` instead of ``Router(preload=True)``: the plan's
``preload=True`` also loads ``typed-decisions``, which this study never auto-routes
(``auto_task_detection=False``), and holding a third ~1.2 GB checkpoint wastes RAM on a small host.

Timing: model load is measured separately (``metadata["load_ms"]``, ``metadata["cold"]``);
``wall_ms`` covers ``Router.predict`` only (routing plus the forward pass).
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import yaml

os.environ.setdefault("USE_TF", "0")

from debench.config import CONFIG_DIR  # noqa: E402
from debench.providers.base import (  # noqa: E402
    ERROR_EXCEPTION,
    ERROR_INVALID_OUTPUT,
    ERROR_VERSION,
    DecisionRequest,
    DecisionResponse,
)

CHECKPOINTS = ("english", "multilingual")
ENV_REVISION = {"english": "LAYA_EN_REVISION", "multilingual": "LAYA_MULTI_REVISION"}
TRACK_MODEL: dict[str, str | None] = {
    "laya_routed": None,
    "laya_en_fixed": "english",
    "laya_multi_fixed": "multilingual",
}

WARMUP_EN = {"state": "The nightly load finished but the row count is lower than yesterday.", "questions": {
    "family": {"type": "choice", "instructions": "Which family fits?", "criteria": {"a": "Data quality", "b": "Other"}},
    "sev": {"type": "score", "instructions": "How severe?", "criteria": ["low", "medium", "high"]},
    "human": {"type": "noul", "instructions": "Does a human need to review this?"},
}}
WARMUP_TH = {
    "state": "การโหลดข้อมูลตอนกลางคืนเสร็จแล้ว แต่จำนวนแถวน้อยกว่าเมื่อวาน",
    "questions": {
        "family": {"type": "choice", "instructions": "ปัญหานี้เข้ากับประเภทใด", "criteria": {"a": "คุณภาพข้อมูล", "b": "อื่น ๆ"}},
        "sev": {"type": "score", "instructions": "รุนแรงเพียงใด", "criteria": ["ต่ำ", "กลาง", "สูง"]},
        "human": {"type": "noul", "instructions": "ต้องให้มนุษย์ตรวจสอบหรือไม่"},
    },
}


class RevisionMismatch(RuntimeError):
    pass


def default_revisions(env: dict[str, str] | None = None) -> dict[str, str]:
    """Per-checkpoint revisions: configs/models.yaml, overridden by non-empty LAYA_*_REVISION."""
    cfg = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["laya"]
    env = os.environ if env is None else env
    return {name: (env.get(ENV_REVISION[name]) or cfg["revision"]) for name in CHECKPOINTS}


def _plain(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str, ensure_ascii=False))


@dataclass
class _Entry:
    router: Any
    load_ms: float
    revisions: dict[str, str]
    actual_revisions: dict[str, str | None]
    config: dict[str, dict[str, Any]]
    versions: dict[str, str]
    calls: int = 0
    claimed_cold: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_ENTRIES: dict[tuple[str, str, str], _Entry] = {}
_BUILD_LOCK = threading.Lock()


def get_entry(device: str, revisions: dict[str, str]) -> _Entry:
    """Build (once) or fetch the shared Router. Blocking: call from a worker thread."""
    key = (device, revisions["english"], revisions["multilingual"])
    with _BUILD_LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None:
            return entry
        import laya
        import torch

        t0 = time.perf_counter()
        router = laya.Router(
            device=device,
            revisions=dict(revisions),
            max_loaded=2,
            auto_task_detection=False,
            preload=False,
        )
        router.preload(list(CHECKPOINTS))
        load_ms = (time.perf_counter() - t0) * 1000.0
        actual = {n: router.load(n).revision for n in CHECKPOINTS}
        for name in CHECKPOINTS:
            if actual[name] is not None and actual[name] != revisions[name]:
                raise RevisionMismatch(f"{name}: loaded revision differs from the pinned revision")
        config = {}
        for name in CHECKPOINTS:
            agent = router.load(name)
            cfg = getattr(agent, "cfg", {}) or {}
            config[name] = {
                "max_len": cfg.get("max_len"),
                "head_max_len": cfg.get("head_max_len"),
                "device": str(agent.device),
            }
        entry = _Entry(
            router=router,
            load_ms=load_ms,
            revisions=dict(revisions),
            actual_revisions=actual,
            config=config,
            versions={"laya": laya.__version__, "torch": torch.__version__},
        )
        _ENTRIES[key] = entry
        return entry


class LayaProvider:
    def __init__(self, track: str, device: str = "cuda", revision: str | dict[str, str] | None = None):
        if track not in TRACK_MODEL:
            raise ValueError(f"unknown Laya track {track!r}")
        self.name = track
        self.model = TRACK_MODEL[track]
        self.device = device
        if revision is None:
            self.revisions = default_revisions()
        elif isinstance(revision, str):
            self.revisions = {name: revision for name in CHECKPOINTS}
        else:
            self.revisions = {name: revision[name] for name in CHECKPOINTS}

    def _entry(self) -> tuple[_Entry, float, bool]:
        """Returns (entry, load_ms spent by THIS call, cold). Load time is never part of wall_ms."""
        key = (self.device, self.revisions["english"], self.revisions["multilingual"])
        built_here = key not in _ENTRIES
        t0 = time.perf_counter()
        entry = get_entry(self.device, self.revisions)
        spent = (time.perf_counter() - t0) * 1000.0
        with entry.lock:
            cold = not entry.claimed_cold
            entry.claimed_cold = True
        return entry, (spent if built_here or cold else 0.0), cold

    def _predict(self, request: DecisionRequest) -> DecisionResponse:
        import torch

        try:
            entry, load_ms, cold = self._entry()
        except RevisionMismatch as exc:
            return self._error(ERROR_VERSION, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - type name only
            return self._error(ERROR_EXCEPTION, type(exc).__name__)
        body = copy.deepcopy(request.shared_body())
        try:
            t0 = time.perf_counter()
            result = entry.router.predict(body["state"], body["questions"], model=self.model)
            if self.device.startswith("cuda"):
                torch.cuda.synchronize()
            wall_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:  # noqa: BLE001 - type name only
            return self._error(ERROR_EXCEPTION, type(exc).__name__, load_ms=load_ms, cold=cold)
        with entry.lock:
            entry.calls += 1
            call_index = entry.calls
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            return self._error(ERROR_INVALID_OUTPUT, "NoAnswers", load_ms=load_ms, cold=cold)
        routing = _plain(result.get("routing") or {})
        usage = result.get("usage") or {}
        checkpoint = routing.get("model") or self.model or "unknown"
        revision = entry.actual_revisions.get(checkpoint) or entry.revisions.get(checkpoint, "unknown")
        metadata = {
            "routing": routing,
            "routed_checkpoint": checkpoint,
            "truncated": usage.get("truncated"),
            "state_tokens": usage.get("state_tokens"),
            "state_tokens_dropped": usage.get("state_tokens_dropped"),
            "truncated_questions": usage.get("truncated_questions"),
            "device": entry.config.get(checkpoint, {}).get("device", self.device),
            "max_len": entry.config.get(checkpoint, {}).get("max_len"),
            "head_max_len": entry.config.get(checkpoint, {}).get("head_max_len"),
            "revision": revision,
            "versions": entry.versions,
            "load_ms": load_ms,
            "router_load_ms": entry.load_ms,
            "cold": cold,
            "call_index": call_index,
        }
        return DecisionResponse(
            provider=self.name,
            resolved_model=f"laya:{checkpoint}@{revision[:12]}",
            answers=answers,
            wall_ms=wall_ms,
            usage_input_tokens=usage.get("input_tokens"),
            usage_output_tokens=usage.get("output_tokens", 0),
            error=None,
            metadata=metadata,
        )

    def _error(self, error: str, type_name: str, load_ms: float = 0.0, cold: bool = False) -> DecisionResponse:
        rev = self.revisions["english" if self.model != "multilingual" else "multilingual"]
        return DecisionResponse(
            provider=self.name,
            resolved_model=f"laya:{self.model or 'routed'}@{rev[:12]}",
            answers={},
            wall_ms=0.0,
            usage_input_tokens=None,
            usage_output_tokens=None,
            error=error,
            metadata={"exception_type": type_name, "load_ms": load_ms, "cold": cold, "device": self.device},
        )

    async def evaluate(self, request: DecisionRequest) -> DecisionResponse:
        return await asyncio.to_thread(self._predict, request)

    async def warmup(self, n: int = 3) -> dict[str, Any]:
        """Load checkpoints (timed apart) then run ``n`` untimed-for-analysis calls per language.

        Returns {"load_ms", "warmup_ms", "calls"}; warmup calls are never measured samples.
        """
        entry, load_ms, cold = await asyncio.to_thread(self._entry)
        t0 = time.perf_counter()
        calls = 0
        for _ in range(n):
            for sample in (WARMUP_EN, WARMUP_TH):
                req = DecisionRequest("warmup", "en", sample["state"], sample["questions"])
                resp = await self.evaluate(req)
                calls += 1
                if resp.error is not None:
                    raise RuntimeError(f"warmup failed: {resp.error}")
        return {"load_ms": load_ms, "cold": cold, "warmup_ms": (time.perf_counter() - t0) * 1000.0, "calls": calls}
