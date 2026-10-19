"""Environment audit (Phase 0). Prints booleans for secrets, never their values."""

from __future__ import annotations

import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from debench.config import ROOT, load_settings

PACKAGES = (
    "laya", "torch", "transformers", "httpx", "duckdb", "sqlglot", "chdb", "chdb-core",
    "pixeltable-pgserver", "psycopg", "opensearch-py", "neo4j", "numpy", "pandas", "scipy",
    "scikit-learn", "pyarrow",
)


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if out.returncode == 0 and text else None


def _meminfo() -> dict[str, float]:
    info = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, val = line.split(":", 1)
                if key in ("MemTotal", "MemAvailable", "SwapTotal"):
                    info[key] = round(int(val.split()[0]) / 1024 / 1024, 2)
    except OSError:
        pass
    return info


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def _gpu() -> dict[str, Any]:
    line = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    out: dict[str, Any] = {"nvidia_smi": line}
    try:
        import torch

        out["torch_cuda_available"] = torch.cuda.is_available()
        out["torch_cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            out["device_name"] = torch.cuda.get_device_name(0)
            out["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2)
    except Exception as exc:  # pragma: no cover - depends on local install
        out["torch_error"] = type(exc).__name__
    return out


def _versions() -> dict[str, str | None]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = md.version(name)
        except md.PackageNotFoundError:
            found[name] = None
    return found


def audit() -> dict[str, Any]:
    settings = load_settings()
    disk = shutil.disk_usage(ROOT)
    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "git_commit": _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "os": platform.platform(),
        "kernel": platform.release(),
        "wsl": "microsoft" in platform.release().lower(),
        "python": sys.version.split()[0],
        "cpu": {"model": _cpu_model(), "logical_cores": os.cpu_count()},
        "memory_gb": _meminfo(),
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "gpu": _gpu(),
        "docker": _run(["docker", "--version"]),
        "docker_compose": _run(["docker", "compose", "version"]),
        "java": _run(["java", "-version"]),
        "uv": _run(["uv", "--version"]),
        "packages": _versions(),
        "typesafe_api_key_set": settings.is_set("TYPESAFE_API_KEY"),
        "allow_remote_api": settings.allow_remote_api,
        "remote_enabled": settings.remote_enabled,
        "jev_model": settings.get("JEV_MODEL"),
        "laya_device": settings.get("LAYA_DEVICE", "cpu"),
    }


def main() -> int:
    report = audit()
    out = ROOT / "results" / "hardware.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
