from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ("E01", "E02", "E03", "E04", "E05", "E06")
GENERATED = ROOT / "data" / "generated"


def case_module_or_skip(case: str):
    try:
        return importlib.import_module(f"debench.dataset.{case.lower()}")
    except ModuleNotFoundError:
        pytest.skip(f"{case} module not implemented yet")


def built_records_or_skip(case: str, context_level: str = "common_fit"):
    from debench.dataset.schema import read_jsonl

    path = GENERATED / case.lower() / f"{context_level}.jsonl"
    if not path.exists():
        pytest.skip(f"{case} dataset not built (python -m debench.dataset.build {case})")
    return read_jsonl(path)


def build_report_or_skip(case: str, context_level: str = "common_fit") -> dict:
    path = GENERATED / case.lower() / f"{context_level}.build.json"
    if not path.exists():
        pytest.skip(f"{case} build report missing")
    return json.loads(path.read_text(encoding="utf-8"))
