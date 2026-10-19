"""Latency statistics with warm/cold separation (PLAN 11).

Percentiles use ``numpy.percentile`` with its default linear interpolation between order
statistics. Only successful calls enter latency percentiles; the success rate is reported
beside them. Cold samples are never mixed into warm percentiles.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

PERCENTILES = (50, 95, 99)


def percentiles(values: Sequence[float]) -> dict[str, float]:
    v = np.asarray(values, float)
    if len(v) == 0:
        return {f"p{q}": float("nan") for q in PERCENTILES}
    return {f"p{q}": float(np.percentile(v, q)) for q in PERCENTILES}


def throughput(n_successful: int, duration_s: float) -> float:
    """Successful calls per second over the measured wall-clock window."""
    return n_successful / duration_s if duration_s > 0 else float("nan")


def latency_stats(
    latency_ms: Sequence[float], success: Sequence[bool] | None = None, is_cold: Sequence[bool] | None = None,
    duration_s: float | None = None,
) -> dict[str, Any]:
    """Warm and cold stats side by side; ``duration_s`` (warm window) enables throughput."""
    lat = np.asarray(latency_ms, float)
    ok = np.ones(len(lat), bool) if success is None else np.asarray(success, bool)
    cold = np.zeros(len(lat), bool) if is_cold is None else np.asarray(is_cold, bool)
    warm_ok = lat[ok & ~cold]
    cold_ok = lat[ok & cold]
    out: dict[str, Any] = {
        "n_calls": len(lat),
        "n_success": int(ok.sum()),
        "success_rate": float(ok.mean()) if len(lat) else float("nan"),
        "n_warm": int(warm_ok.size),
        "n_cold": int(cold_ok.size),
        **{f"warm_{k}_ms": v for k, v in percentiles(warm_ok).items()},
        **{f"cold_{k}_ms": v for k, v in percentiles(cold_ok).items()},
        "warm_mean_ms": float(warm_ok.mean()) if warm_ok.size else float("nan"),
        "cold_mean_ms": float(cold_ok.mean()) if cold_ok.size else float("nan"),
        "throughput_per_s": throughput(int(warm_ok.size), duration_s) if duration_s else None,
    }
    return out


def summarize_latency(
    df: pd.DataFrame,
    group_keys: Sequence[str],
    latency_col: str = "latency_ms",
    success_col: str = "success",
    cold_col: str = "is_cold",
    start_col: str = "start_s",
    end_col: str = "end_s",
) -> pd.DataFrame:
    """One row per group. If ``start_col``/``end_col`` exist, throughput uses the warm window
    (last end - first start of warm calls); otherwise it is None."""
    has_window = start_col in df.columns and end_col in df.columns
    rows = []
    for key, g in df.groupby(list(group_keys), dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        cold = g[cold_col].astype(bool) if cold_col in g else None
        dur = None
        if has_window:
            w = g[~cold] if cold is not None else g
            if len(w):
                dur = float(w[end_col].max() - w[start_col].min())
        stats = latency_stats(
            g[latency_col].to_numpy(),
            g[success_col].to_numpy() if success_col in g else None,
            cold.to_numpy() if cold is not None else None,
            dur,
        )
        rows.append({**dict(zip(group_keys, key)), **stats})
    return pd.DataFrame(rows)
