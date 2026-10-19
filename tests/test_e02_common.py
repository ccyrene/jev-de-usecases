"""Shared cached objects for the E02 tests (no tests here)."""

from __future__ import annotations

from functools import lru_cache

from debench import SEED
from debench.dataset import e02


@lru_cache(maxsize=1)
def get_specs():
    return e02.generate_specs(SEED)


def make_facts(
    *,
    n_rows: int,
    uniq: int | None = None,
    nulls: int = 0,
    hist_days: int = 14,
    same_days: int = 2,
    same_mean: float | None = 100.0,
    mean_rows: float | None = 100.0,
    amount_mean: float = 100.0,
    base_mean: float = 100.0,
    base_sd: float = 50.0,
    lag_s: int = 2400,
    mismatch: int = 0,
    tier: str = "tier2",
    critical: bool = False,
    jobs: tuple[str, ...] = ("daily_revenue_rollup",),
    sla: int = 180,
    calendar_factor: float | None = None,
    batch_rows: list[int] | None = None,
    batch_nulls: list[int] | None = None,
    batch_uniq: list[int] | None = None,
) -> dict:
    """Minimal raw facts (the shape the backend and the pandas reference both return)."""
    uniq = n_rows if uniq is None else uniq
    rows = batch_rows or [n_rows // 8] * 8
    batches = [
        {
            "batch_id": f"p-d-20260101-b{i}",
            "n_rows": r,
            "uniq_records": (batch_uniq or rows)[i],
            "null_count": (batch_nulls or [0] * 8)[i],
            "amount_mean": amount_mean,
            "raw_rows": r,
            "mismatch_count": 0,
        }
        for i, r in enumerate(rows)
    ]
    return {
        "pipeline_id": "p",
        "dataset_id": "d",
        "window_day": "2026-01-01",
        "as_of": "2026-01-02 00:30:00",
        "window": {
            "n_rows": n_rows,
            "uniq_records": uniq,
            "null_count": nulls,
            "amount_min": 1.0,
            "amount_max": 500.0,
            "amount_mean": amount_mean,
            "amount_sd": base_sd,
            "amount_p95": 200.0,
            "last_event": "2026-01-01 23:50:00",
            "lag_s": lag_s,
            "active_hours": 24,
            "raw_rows": n_rows + mismatch,
            "mismatch_count": mismatch,
        },
        "batches": batches,
        "baseline": {
            "history_days": hist_days,
            "mean_rows": mean_rows,
            "same_weekday_days": same_days,
            "same_weekday_mean_rows": same_mean,
            "null_rate": 0.01,
            "amount_mean": base_mean,
            "amount_sd": base_sd,
            "amount_max": 600.0,
            "amount_p95": 210.0,
        },
        "history_rows": [],
        "catalog": {
            "tier": tier,
            "freshness_sla_min": sla,
            "completeness_sla": 0.8,
            "downstream_critical": critical,
            "dependent_jobs": list(jobs),
            "ops_note": "",
        },
        "calendar": {"is_holiday": True, "expected_factor": calendar_factor} if calendar_factor else None,
    }
