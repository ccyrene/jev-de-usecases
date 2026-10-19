"""E02 - Data Quality Monitoring on ClickHouse.

Each scenario is one pipeline/dataset daily window (8 batches of 3 hours) with 14 days of history.
ClickHouse (chdb) aggregates the raw synthetic events into per-window facts; the model only sees a
short evidence summary (computed statistics, the static alert that fired, business context). The raw
events are the oracle: ``expected_evidence`` recomputes the same facts with pandas.

Gold comes from the injected perturbation (``anomaly_family``) plus two explicit policies applied to
the validated statistics (policy id ``e02-policy-v1``).

Thresholds (``THR``): type mismatch rate > 0.005, null rate > 0.05, duplicate rate > 0.03, lag >
freshness SLA, seasonal volume ratio < 0.6, |mean shift| > 0.5 baseline sd. Volume is compared with
the same-weekday mean of the last 14 days (all-day mean when fewer than 2 same weekdays), times the
calendar factor when the calendar table knows the day. History needs >= 5 days, otherwise
volume/shift cannot be confirmed and the family is ``normal_or_unknown``.

quality_impact (0..4), anomaly families only, ``normal_or_unknown`` is always 0:
  band 1..3 from the family metric (volume shortfall 1-ratio: <0.5/<0.8/else; null rate <0.10/<0.30;
  duplicate rate <0.08/<0.20; |shift| <1.0/<2.0; lag/SLA <2/<5; mismatch rate <0.02/<0.10)
  +1 if a critical consumer job depends on the dataset
  -1 if at most one batch (partition) is affected
  -1 if the dataset has no dependent job; clamp to 0..4.
investigate_now (bool): impact >= 3; or impact == 2 and (tier1 or SLA breached: freshness lag above
  SLA, seasonal ratio below the completeness SLA); or the family is unconfirmed because history is
  insufficient, the dataset is tier1 and a static alert fired.

``rules_baseline`` applies the same thresholds and policies to the facts alone. It cannot read the
free-text ops note. ``facts["_internal"]["threshold_decidable"]`` is True only when the evidence is
sufficient, no free-text context is needed, and that baseline reproduces all three gold answers.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from debench.config import Settings
from debench.dataset.paired_generator import scenario_id
from debench.dataset.schema import ScenarioSpec
from debench.evidence.base import Evidence
from debench.evidence.clickhouse import BASELINE_DAYS, ClickHouseBackend

CASE = "E02"
BACKEND = "clickhouse"
SEED_OFFSET = 202
POLICY_ID = "e02-policy-v1"

N_BATCH = 8
BLOCK_S = 3 * 3600
AS_OF_S = 24 * 3600 + 30 * 60
MIN_HIST = 5
THR = {"type": 0.005, "null": 0.05, "dup": 0.03, "vol": 0.6, "naive_high": 1.4, "shift": 0.5, "lag": 1.0}
FAMILIES = (
    "volume_drop",
    "null_spike",
    "duplicate_spike",
    "distribution_shift",
    "freshness_lag",
    "type_violation",
    "normal_or_unknown",
)
NORMAL = "normal_or_unknown"
RULE_ORDER = (
    "type_violation",
    "null_spike",
    "duplicate_spike",
    "freshness_lag",
    "volume_drop",
    "distribution_shift",
)

DATASETS = (
    "orders",
    "payments",
    "sessions",
    "shipments",
    "invoices",
    "telemetry",
    "claims",
    "bookings",
    "ledger",
    "tickets",
)
JOBS = (
    "daily_revenue_rollup",
    "fraud_scoring_batch",
    "ops_dashboard_refresh",
    "customer_360_sync",
    "weekly_kpi_report",
    "ml_feature_refresh",
    "inventory_forecast",
    "partner_settlement_feed",
)
CRITICAL_JOBS = ("finance_close_export", "regulatory_report", "payment_reconciliation")
EVENT_TYPES = ("order_created", "order_paid", "order_shipped", "order_refunded")
BAD_AMOUNTS = ("N/A", "null", "12,50", "abc", "1.2.3", "--")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")

# Families that legitimately co-fire with the injected one (quarantined rows / missing tail lower volume).
COUPLED = {"type_violation": {"volume_drop"}, "freshness_lag": {"volume_drop"}}

# (kind, count, injected family)
KIND_PLAN = (
    ("weekly_seasonality", 20, NORMAL),
    ("partition_loss", 24, "volume_drop"),
    ("holiday_dip", 14, NORMAL),
    ("row_collapse", 16, "volume_drop"),
    ("legit_growth", 16, NORMAL),
    ("null_spike", 34, "null_spike"),
    ("contaminated_batch", 16, None),  # type_violation or distribution_shift by variant
    ("delayed_ingestion", 32, "freshness_lag"),
    ("duplicate_replay", 32, "duplicate_spike"),
    ("numeric_shift", 30, "distribution_shift"),
    ("type_violation", 24, "type_violation"),
    ("no_anomaly", 24, NORMAL),
    ("insufficient_history", 18, NORMAL),
)
VARIANTS = {
    "holiday_dip": ["known"] * 8 + ["note"] * 6,
    "row_collapse": ["holiday"] * 8 + ["plain"] * 8,
    "contaminated_batch": ["type"] * 8 + ["dist"] * 8,
    "no_anomaly": ["clean"] * 12 + ["near"] * 12,
    "insufficient_history": ["volume"] * 8 + ["shift"] * 5 + ["plain"] * 5,
}
FAULT_KINDS = {
    "partition_loss",
    "row_collapse",
    "null_spike",
    "contaminated_batch",
    "delayed_ingestion",
    "duplicate_replay",
    "numeric_shift",
    "type_violation",
}


# --------------------------------------------------------------------------------------------------
# Synthetic event simulation
# --------------------------------------------------------------------------------------------------
@dataclass
class Sim:
    events: pd.DataFrame
    raw: pd.DataFrame
    calendar: pd.DataFrame
    catalog: pd.DataFrame


def _weekday_factor(d: date, p: dict[str, Any]) -> float:
    wf = p["weekend_factor"]
    return 1.0 if d.weekday() < 5 else (wf if d.weekday() == 5 else wf * 0.92)


def _day_frame(rng: np.random.Generator, d: date, rate: float, base_mean: float, rec0: int) -> pd.DataFrame:
    counts = rng.poisson(max(rate, 0.0) / N_BATCH, size=N_BATCH)
    batch = np.repeat(np.arange(N_BATCH), counts)
    secs = batch * BLOCK_S + rng.integers(0, BLOCK_S, size=len(batch))
    order = np.argsort(secs, kind="stable")
    batch, secs = batch[order], secs[order]
    n = len(batch)
    amount = np.maximum(np.round(rng.gamma(4.0, base_mean / 4.0, size=n), 2), 0.5)
    cust = np.array([f"cu_{c:06d}" for c in rng.integers(1, 50000, size=n)], dtype=object)
    cust[rng.random(n) < 0.01] = None
    etype = rng.choice(EVENT_TYPES, size=n, p=[0.4, 0.35, 0.15, 0.1])
    return pd.DataFrame(
        {
            "day": d,
            "batch": batch,
            "secs": secs,
            "record_id": rec0 + np.arange(n, dtype=np.uint64),
            "amount": amount,
            "customer_id": cust,
            "event_type": etype,
        }
    )


def _apply_fault(rng: np.random.Generator, df: pd.DataFrame, fault: dict[str, Any]) -> pd.DataFrame:
    """Perturb the window-day frame. Order: delay, partition loss, replay, nulls, values, bad types."""
    kind = fault.get("type")
    df = df.copy()
    if kind == "delay":
        df = df[df["secs"] <= fault["cutoff_s"]]
    elif kind == "loss":
        df = df[~df["batch"].isin(fault["batches"])]
    elif kind == "dup" and len(df):
        order = rng.permutation(N_BATCH)
        chosen, rows = [], 0
        for b in order:
            chosen.append(int(b))
            rows += int((df["batch"] == b).sum())
            if rows >= fault["n_dup"]:
                break
        pool = df[df["batch"].isin(chosen)]
        take = min(fault["n_dup"], len(pool))
        pick = pool.iloc[rng.choice(len(pool), size=take, replace=False)]
        df = pd.concat([df, pick], ignore_index=True)
    elif kind == "null":
        mask = df["batch"].isin(fault["batches"]).to_numpy() & (rng.random(len(df)) < fault["q"])
        df.loc[mask, "customer_id"] = None
    elif kind == "scale":
        df["amount"] = np.round(df["amount"] * fault["factor"], 2).clip(lower=0.01)
    elif kind == "outlier":
        mask = df["batch"].isin(fault["batches"]).to_numpy() & (rng.random(len(df)) < fault["q"])
        df.loc[mask, "amount"] = np.round(df.loc[mask, "amount"] * fault["factor"], 2)
    return df.reset_index(drop=True)


def _build_dataset(
    rng: np.random.Generator,
    p: dict[str, Any],
    dataset: str,
    rate: float,
    rec_base: int,
    fault: dict[str, Any] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Events and raw rows for the 14 history days plus the window day of one dataset."""
    d0 = date.fromisoformat(p["window_day"])
    n_hist = p["n_hist"]
    frames = []
    rec = rec_base
    for k in range(n_hist + 1):
        d = d0 - timedelta(days=n_hist - k)
        trend = 1.0 + (p["growth"] - 1.0) * k / max(n_hist, 1) if p["growth"] else 1.0
        mult = trend
        if k == n_hist:
            mult *= p["window_multiplier"]
        fr = _day_frame(rng, d, rate * _weekday_factor(d, p) * mult, p["base_mean"], rec)
        if k == n_hist and fault:
            fr = _apply_fault(rng, fr, fault)
            if fault["type"] == "dup":
                fr["record_id"] = fr["record_id"].astype("uint64")
        rec += len(fr) + 1000
        frames.append(fr)
    df = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(df["day"].astype(str)) + pd.to_timedelta(df["secs"], unit="s")
    pipeline = p["pipeline_id"]
    batch_id = [f"{pipeline}-{dataset}-{d:%Y%m%d}-b{b}" for d, b in zip(df["day"], df["batch"], strict=True)]
    full = pd.DataFrame(
        {
            "event_time": ts.astype("datetime64[s]"),
            "pipeline_id": pipeline,
            "dataset_id": dataset,
            "batch_id": batch_id,
            "record_id": df["record_id"].astype("uint64"),
            "amount": df["amount"].astype(float),
            "customer_id": df["customer_id"],
            "event_type": df["event_type"],
        }
    )
    raw = full.rename(columns={"amount": "amount_raw"}).copy()
    raw["amount_raw"] = [f"{a:.2f}" for a in full["amount"]]
    if fault and fault["type"] == "badtype":
        is_window = (df["day"] == d0).to_numpy()
        hit = is_window & df["batch"].isin(fault["batches"]).to_numpy() & (rng.random(len(df)) < fault["q"])
        raw.loc[hit, "amount_raw"] = rng.choice(BAD_AMOUNTS, size=int(hit.sum()))
        full = full[~hit].reset_index(drop=True)
    return full, raw


def simulate(p: dict[str, Any], with_sibling: bool = True) -> Sim:
    """Deterministic event data for one scenario; the target dataset is generated before the sibling."""
    rng = np.random.default_rng(p["data_seed"])
    events, raw = _build_dataset(rng, p, p["dataset_id"], p["base_rate"], p["record_base"], p["fault"])
    cat_rows = [_catalog_row(p, p["dataset_id"], p["catalog"])]
    if with_sibling and p.get("sibling"):
        s = p["sibling"]
        ev2, raw2 = _build_dataset(
            rng, p, s["dataset_id"], p["base_rate"] * 0.3, p["record_base"] + 500_000, s["fault"]
        )
        events, raw = pd.concat([events, ev2], ignore_index=True), pd.concat([raw, raw2], ignore_index=True)
        cat_rows.append(_catalog_row(p, s["dataset_id"], s["catalog"]))
    calendar = pd.DataFrame(
        [
            {
                "pipeline_id": p["pipeline_id"],
                "day": p["window_day"],
                "is_holiday": 1,
                "expected_factor": p["calendar_factor"],
            }
        ]
        if p.get("calendar_factor")
        else [],
        columns=["pipeline_id", "day", "is_holiday", "expected_factor"],
    )
    return Sim(events=events, raw=raw, calendar=calendar, catalog=pd.DataFrame(cat_rows))


def _catalog_row(p: dict[str, Any], dataset: str, c: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline_id": p["pipeline_id"],
        "dataset_id": dataset,
        "tier": c["tier"],
        "freshness_sla_min": c["freshness_sla_min"],
        "completeness_sla": c["completeness_sla"],
        "downstream_critical": int(c["downstream_critical"]),
        "dependent_jobs": list(c["dependent_jobs"]),
        "ops_note": c["ops_note"],
    }


# --------------------------------------------------------------------------------------------------
# Reference (pandas) facts: independent of SQL
# --------------------------------------------------------------------------------------------------
def reference_facts(sim: Sim, core: dict[str, str]) -> dict[str, Any]:
    pid, ds, day = core["pipeline_id"], core["dataset_id"], core["window_day"]
    d0 = pd.Timestamp(day)
    ev = sim.events[(sim.events["pipeline_id"] == pid) & (sim.events["dataset_id"] == ds)]
    raw = sim.raw[(sim.raw["pipeline_id"] == pid) & (sim.raw["dataset_id"] == ds)]
    win = ev[(ev["event_time"] >= d0) & (ev["event_time"] < d0 + pd.Timedelta(days=1))]
    rwin = raw[(raw["event_time"] >= d0) & (raw["event_time"] < d0 + pd.Timedelta(days=1))]
    bad = ~rwin["amount_raw"].map(lambda s: bool(_NUMERIC.match(s)))
    last = win["event_time"].max()
    as_of = pd.Timestamp(core["as_of"])
    window = {
        "n_rows": len(win),
        "uniq_records": int(win["record_id"].nunique()),
        "null_count": int(win["customer_id"].isna().sum()),
        "amount_min": float(win["amount"].min()),
        "amount_max": float(win["amount"].max()),
        "amount_mean": float(win["amount"].mean()),
        "amount_sd": float(win["amount"].std(ddof=0)),
        "amount_p95": float(np.quantile(win["amount"], 0.95)),
        "last_event": last.strftime("%Y-%m-%d %H:%M:%S"),
        "lag_s": int((as_of - last).total_seconds()),
        "active_hours": int(win["event_time"].dt.hour.nunique()),
        "raw_rows": len(rwin),
        "mismatch_count": int(bad.sum()),
    }
    batches = []
    tb = {b: g for b, g in win.groupby("batch_id")}
    rb = {b: g for b, g in rwin.assign(bad=bad.to_numpy()).groupby("batch_id")}
    for bid in sorted(set(tb) | set(rb)):
        t, r = tb.get(bid), rb.get(bid)
        batches.append(
            {
                "batch_id": bid,
                "n_rows": len(t) if t is not None else 0,
                "uniq_records": int(t["record_id"].nunique()) if t is not None else 0,
                "null_count": int(t["customer_id"].isna().sum()) if t is not None else 0,
                "amount_mean": float(t["amount"].mean()) if t is not None else None,
                "raw_rows": len(r) if r is not None else 0,
                "mismatch_count": int(r["bad"].sum()) if r is not None else 0,
            }
        )
    hist = ev[(ev["event_time"] >= d0 - pd.Timedelta(days=BASELINE_DAYS)) & (ev["event_time"] < d0)]
    daily = hist.groupby(hist["event_time"].dt.normalize())["amount"].agg(["count", "sum", "max"])
    sd = hist.groupby(hist["event_time"].dt.normalize())["amount"].std(ddof=0)
    p95 = hist.groupby(hist["event_time"].dt.normalize())["amount"].quantile(0.95)
    nulls = hist.groupby(hist["event_time"].dt.normalize())["customer_id"].apply(
        lambda s: int(s.isna().sum())
    )
    same = np.array([ts.weekday() == d0.weekday() for ts in daily.index], dtype=bool)
    n_hist = len(daily)
    baseline = {
        "history_days": n_hist,
        "mean_rows": float(daily["count"].mean()) if n_hist else None,
        "same_weekday_days": int(same.sum()),
        "same_weekday_mean_rows": float(daily["count"][same].mean()) if same.any() else None,
        "null_rate": float(nulls.sum() / daily["count"].sum()) if n_hist else None,
        "amount_mean": float(daily["sum"].sum() / daily["count"].sum()) if n_hist else None,
        "amount_sd": float(sd.mean()) if n_hist else None,
        "amount_max": float(daily["max"].max()) if n_hist else None,
        "amount_p95": float(p95.mean()) if n_hist else None,
    }
    cat = sim.catalog[(sim.catalog["pipeline_id"] == pid) & (sim.catalog["dataset_id"] == ds)].iloc[0]
    cal = sim.calendar[(sim.calendar["pipeline_id"] == pid) & (sim.calendar["day"] == day)]
    return {
        "pipeline_id": pid,
        "dataset_id": ds,
        "window_day": day,
        "as_of": core["as_of"],
        "window": window,
        "batches": batches,
        "baseline": baseline,
        "history_rows": [[ts.strftime("%Y-%m-%d"), int(c)] for ts, c in daily["count"].items()],
        "catalog": {
            "tier": cat["tier"],
            "freshness_sla_min": int(cat["freshness_sla_min"]),
            "completeness_sla": float(cat["completeness_sla"]),
            "downstream_critical": bool(cat["downstream_critical"]),
            "dependent_jobs": list(cat["dependent_jobs"]),
            "ops_note": cat["ops_note"],
        },
        "calendar": (
            {
                "is_holiday": bool(cal.iloc[0]["is_holiday"]),
                "expected_factor": float(cal.iloc[0]["expected_factor"]),
            }
            if len(cal)
            else None
        ),
    }


# --------------------------------------------------------------------------------------------------
# Derivation, alert rule, rules baseline and policies (shared by DB and reference facts)
# --------------------------------------------------------------------------------------------------
def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd)


def derive(facts: dict[str, Any]) -> dict[str, Any]:
    """Statistics the thresholds work on, computed from the raw aggregates only."""
    w, b, cat, cal = facts["window"], facts["baseline"], facts["catalog"], facts["calendar"]
    n_hist, same = b["history_days"], b["same_weekday_days"]
    if same >= 2:
        base_rows, basis = b["same_weekday_mean_rows"], "same_weekday"
    elif n_hist >= MIN_HIST:
        base_rows, basis = b["mean_rows"], "all_days"
    else:
        base_rows, basis = None, "none"
    factor = cal["expected_factor"] if cal else 1.0
    expected = None if base_rows is None else base_rows * factor
    ratio = None if not expected else w["n_rows"] / expected
    naive = w["n_rows"] / b["mean_rows"] if n_hist >= 2 and b["mean_rows"] else None
    shift = None
    if n_hist >= MIN_HIST and b["amount_sd"]:
        shift = (w["amount_mean"] - b["amount_mean"]) / b["amount_sd"]
    bs = facts["batches"]
    seen = sum(1 for x in bs if x["n_rows"] > 0)
    low = None
    if expected is not None:
        low = N_BATCH - sum(1 for x in bs if x["n_rows"] >= 0.5 * expected / N_BATCH)
    nulls = sum(1 for x in bs if x["null_count"] >= 2 and x["null_count"] / x["n_rows"] > 0.2)
    dups = sum(
        1
        for x in bs
        if x["n_rows"] and x["n_rows"] - x["uniq_records"] >= 2 and 1 - x["uniq_records"] / x["n_rows"] > 0.2
    )
    shifted = None
    if shift is not None:
        shifted = sum(
            1
            for x in bs
            if x["n_rows"] >= 3 and abs(x["amount_mean"] - b["amount_mean"]) / b["amount_sd"] > 1.0
        )
    lag_min = w["lag_s"] // 60
    return {
        "expected_rows": _r(expected, 2),
        "expected_basis": basis,
        "calendar_factor": factor,
        "volume_ratio": _r(ratio),
        "naive_ratio": _r(naive),
        "mean_shift_sd": _r(shift),
        "null_rate": _r(w["null_count"] / w["n_rows"]),
        "dup_rate": _r(1 - w["uniq_records"] / w["n_rows"]),
        "mismatch_rate": _r(w["mismatch_count"] / w["raw_rows"]) if w["raw_rows"] else 0.0,
        "lag_min": lag_min,
        "lag_ratio": _r(lag_min / cat["freshness_sla_min"]),
        "batches_seen": seen,
        "batches_missing": N_BATCH - seen,
        "batches_low_volume": low,
        "batches_null": nulls,
        "batches_dup": dups,
        "batches_shift": shifted,
        "batches_mismatch": sum(1 for x in bs if x["mismatch_count"] >= 1),
    }


def family_metrics(d: dict[str, Any]) -> dict[str, tuple[float, float, str]]:
    """family -> (metric, threshold, direction) for metrics that can be evaluated."""
    m = {
        "type_violation": (d["mismatch_rate"], THR["type"], "hi"),
        "null_spike": (d["null_rate"], THR["null"], "hi"),
        "duplicate_spike": (d["dup_rate"], THR["dup"], "hi"),
        "freshness_lag": (d["lag_ratio"], THR["lag"], "hi"),
    }
    if d["volume_ratio"] is not None:
        m["volume_drop"] = (d["volume_ratio"], THR["vol"], "lo")
    if d["mean_shift_sd"] is not None:
        m["distribution_shift"] = (abs(d["mean_shift_sd"]), THR["shift"], "hi")
    return m


def breached(d: dict[str, Any]) -> list[str]:
    out = []
    for fam in RULE_ORDER:
        if fam in (m := family_metrics(d)):
            val, thr, way = m[fam]
            if (val > thr) if way == "hi" else (val < thr):
                out.append(fam)
    return out


def near_threshold(d: dict[str, Any]) -> set[str]:
    """Families whose metric lies within +-25% of its threshold (either side)."""
    return {f for f, (v, t, _) in family_metrics(d).items() if 0.75 * t <= v <= 1.25 * t}


def rules_family(d: dict[str, Any]) -> str:
    hit = breached(d)
    return hit[0] if hit else NORMAL


def alert_rule(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Static monitor: first breached rule, volume judged against the plain 14-day mean."""
    d = facts["derived"]
    checks = (
        (
            "freshness_sla",
            d["lag_ratio"] * facts["catalog"]["freshness_sla_min"],
            facts["catalog"]["freshness_sla_min"],
            d["lag_ratio"] > 1.0,
        ),
        ("type_mismatch_rate", d["mismatch_rate"], THR["type"], d["mismatch_rate"] > THR["type"]),
        ("null_rate", d["null_rate"], THR["null"], d["null_rate"] > THR["null"]),
        ("dup_rate", d["dup_rate"], THR["dup"], d["dup_rate"] > THR["dup"]),
        (
            "row_count_vs_mean",
            d["naive_ratio"],
            THR["vol"] if (d["naive_ratio"] or 1) < THR["vol"] else THR["naive_high"],
            d["naive_ratio"] is not None and not THR["vol"] <= d["naive_ratio"] <= THR["naive_high"],
        ),
        (
            "amount_mean_shift",
            None if d["mean_shift_sd"] is None else abs(d["mean_shift_sd"]),
            THR["shift"],
            d["mean_shift_sd"] is not None and abs(d["mean_shift_sd"]) > THR["shift"],
        ),
    )
    for name, observed, thr, fired in checks:
        if fired:
            return {"rule": name, "observed": observed, "threshold": thr}
    return None


def _band(family: str, d: dict[str, Any]) -> int:
    def pick(x: float, cuts: tuple[float, float]) -> int:
        return 1 if x < cuts[0] else 2 if x < cuts[1] else 3

    if family == "volume_drop":
        return pick(1 - d["volume_ratio"], (0.5, 0.8))
    if family == "null_spike":
        return pick(d["null_rate"], (0.10, 0.30))
    if family == "duplicate_spike":
        return pick(d["dup_rate"], (0.08, 0.20))
    if family == "distribution_shift":
        return pick(abs(d["mean_shift_sd"]), (1.0, 2.0))
    if family == "freshness_lag":
        return pick(d["lag_ratio"], (2.0, 5.0))
    return pick(d["mismatch_rate"], (0.02, 0.10))


_AFFECTED = {
    "volume_drop": "batches_low_volume",
    "null_spike": "batches_null",
    "duplicate_spike": "batches_dup",
    "distribution_shift": "batches_shift",
    "freshness_lag": "batches_missing",
    "type_violation": "batches_mismatch",
}


def policy_impact(family: str, facts: dict[str, Any]) -> int:
    if family == NORMAL:
        return 0
    d, cat = facts["derived"], facts["catalog"]
    level = _band(family, d)
    level += 1 if cat["downstream_critical"] else 0
    level -= 1 if d[_AFFECTED[family]] <= 1 else 0
    level -= 1 if not cat["dependent_jobs"] else 0
    return max(0, min(4, level))


def policy_investigate(family: str, impact: int, facts: dict[str, Any]) -> bool:
    d, cat = facts["derived"], facts["catalog"]
    if impact >= 3:
        return True
    if impact == 2:
        sla_breached = (family == "freshness_lag") or (
            family == "volume_drop" and d["volume_ratio"] < cat["completeness_sla"]
        )
        return cat["tier"] == "tier1" or sla_breached
    return (
        family == NORMAL
        and facts["baseline"]["history_days"] < MIN_HIST
        and cat["tier"] == "tier1"
        and facts["alert"] is not None
    )


def needs_free_text(facts: dict[str, Any]) -> bool:
    """The calendar table lacks the day but the ops note explains the dip."""
    return facts["catalog"]["ops_note"] == "regional_holiday" and facts["calendar"] is None


def rules_baseline(spec: ScenarioSpec, facts: dict[str, Any]) -> dict[str, Any]:
    fam = rules_family(facts["derived"])
    impact = policy_impact(fam, facts)
    out = {
        "anomaly_family": fam,
        "quality_impact": impact,
        "investigate_now": policy_investigate(fam, impact, facts),
    }
    decidable = spec.evidence_sufficiency == "sufficient" and not needs_free_text(facts) and out == spec.gold
    facts.setdefault("_internal", {})["threshold_decidable"] = decidable
    return out


def finish_facts(facts: dict[str, Any]) -> dict[str, Any]:
    facts["derived"] = derive(facts)
    facts["alert"] = alert_rule(facts)
    return facts


# --------------------------------------------------------------------------------------------------
# Spec generation
# --------------------------------------------------------------------------------------------------
def _template(rng: random.Random) -> dict[str, Any]:
    tier = rng.choices(["tier1", "tier2", "tier3"], [0.3, 0.4, 0.3])[0]
    n_jobs = {"tier1": rng.randint(2, 4), "tier2": rng.randint(1, 3), "tier3": rng.randint(0, 2)}[tier]
    p_crit = {"tier1": 0.5, "tier2": 0.2, "tier3": 0.05}[tier]
    jobs = rng.sample(JOBS, n_jobs)
    if n_jobs and rng.random() < p_crit:
        jobs[0] = rng.choice(CRITICAL_JOBS)
    return {
        "dataset": rng.choice(DATASETS),
        "tier": tier,
        "jobs": sorted(jobs),
        "critical": any(j in CRITICAL_JOBS for j in jobs),
        "sla": rng.choice([180, 240, 360]),
        "completeness": rng.choice([0.8, 0.9]),
        "base_rate": rng.randint(48, 120),
        "base_mean": rng.randint(60, 160),
    }


def _loguni(rng: random.Random, lo: float, hi: float) -> float:
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def _spread(rng: random.Random, r: float, cap: float = 0.98) -> tuple[list[int], float]:
    """Batches and per-row probability so that about a share ``r`` of all rows is hit."""
    m_min = max(1, math.ceil(r * N_BATCH / cap))
    m = rng.randint(m_min, N_BATCH)
    return sorted(rng.sample(range(N_BATCH), m)), min(cap, r * N_BATCH / m)


def _draw_fault(
    kind: str, variant: str | None, grey: bool, rng: random.Random, tmpl: dict[str, Any], p: dict[str, Any]
) -> None:
    """Fill the fault / window multiplier / calendar fields of ``p`` for one scenario."""
    p["fault"] = None
    p["window_multiplier"] = 1.0
    p["calendar_factor"] = None
    wk = rng.randint(0, 4)  # Mon..Fri default window weekday
    if kind == "weekly_seasonality":
        p["weekend_factor"] = rng.uniform(0.4, 0.5)
        wk = rng.choice([5, 6])
    elif kind == "legit_growth":
        p["growth"] = rng.uniform(1.7, 2.4)
        p["ops_note"] = "campaign" if rng.random() < 0.75 else ""
    elif kind == "holiday_dip":
        f = rng.choice([0.4, 0.5, 0.6]) if variant == "known" else rng.uniform(0.4, 0.55)
        p["window_multiplier"] = f * rng.uniform(0.9, 1.1)
        if variant == "known":
            p["calendar_factor"] = f
        else:
            p["ops_note"] = "regional_holiday"
    elif kind == "row_collapse":
        rho = rng.uniform(0.5, 0.7) if grey else _loguni(rng, 0.05, 0.4)
        f = 1.0
        if variant == "holiday":
            f = rng.choice([0.4, 0.5, 0.6])
            p["calendar_factor"] = f
        p["window_multiplier"] = f * rho
    elif kind == "partition_loss":
        m = 4 if grey else rng.choice([5, 6])
        p["fault"] = {"type": "loss", "batches": sorted(rng.sample(range(1, N_BATCH - 1), m))}
    elif kind == "null_spike":
        r = rng.uniform(0.04, 0.06) if grey else _loguni(rng, 0.08, 0.6)
        b, q = _spread(rng, r)
        p["fault"] = {"type": "null", "batches": b, "q": q}
    elif kind == "contaminated_batch" and variant == "type":
        r = _loguni(rng, 0.01, 0.2)
        b = sorted(rng.sample(range(N_BATCH), rng.randint(1, 2)))
        p["fault"] = {"type": "badtype", "batches": b, "q": rng.uniform(0.6, 1.0)}
    elif kind == "contaminated_batch":
        b = sorted(rng.sample(range(N_BATCH), rng.randint(1, 2)))
        p["fault"] = {
            "type": "outlier",
            "batches": b,
            "q": rng.uniform(0.4, 1.0),
            "factor": rng.uniform(30, 200),
        }
    elif kind == "type_violation":
        r = rng.uniform(0.004, 0.007) if grey else _loguni(rng, 0.01, 0.3)
        b, q = _spread(rng, r)
        p["fault"] = {"type": "badtype", "batches": b, "q": q}
    elif kind == "duplicate_replay":
        r = rng.uniform(0.025, 0.04) if grey else _loguni(rng, 0.06, 0.3)
        p["fault"] = {"type": "dup", "r": r}  # n_dup is fixed once the row count is known
    elif kind == "numeric_shift":
        s = rng.uniform(0.4, 0.6) if grey else _loguni(rng, 0.8, 3.5)
        sign = -1 if (s <= 1.5 and rng.random() < 0.3) else 1
        p["fault"] = {"type": "scale", "factor": 1 + sign * 0.5 * s}
    elif kind == "delayed_ingestion":
        rho = rng.uniform(1.0, 1.25) if grey else rng.uniform(1.5, min(7.0, 1380 / tmpl["sla"]))
        p["fault"] = {"type": "delay", "cutoff_s": int(AS_OF_S - rho * tmpl["sla"] * 60)}
    elif kind == "no_anomaly" and variant == "near":
        which = rng.choice(["null", "dup", "scale", "rho"])
        if which == "null":
            b, q = _spread(rng, rng.uniform(0.03, 0.045))
            p["fault"] = {"type": "null", "batches": b, "q": q}
        elif which == "dup":
            p["fault"] = {"type": "dup", "r": rng.uniform(0.02, 0.027)}
        elif which == "scale":
            p["fault"] = {"type": "scale", "factor": 1 + 0.5 * rng.uniform(0.3, 0.45)}
        else:
            p["window_multiplier"] = rng.uniform(0.62, 0.72)
    elif kind == "insufficient_history":
        p["n_hist"] = 3
        if variant == "volume":
            p["window_multiplier"] = rng.uniform(0.35, 0.6)
        elif variant == "shift":
            p["fault"] = {"type": "scale", "factor": 1 + 0.5 * rng.uniform(0.8, 2.0)}
    d0 = date(2026, 3, 2) + timedelta(days=rng.randint(0, 160))
    d0 += timedelta(days=(wk - d0.weekday()) % 7)
    p["window_day"] = d0.isoformat()


def _sibling(rng: random.Random, tmpl: dict[str, Any]) -> dict[str, Any] | None:
    if rng.random() > 0.4:
        return None
    pick = rng.choice([None, "null", "dup", "scale"])
    fault = {
        None: None,
        "null": {"type": "null", "batches": list(range(N_BATCH)), "q": rng.uniform(0.3, 0.8)},
        "dup": {"type": "dup", "r": rng.uniform(0.1, 0.3)},
        "scale": {"type": "scale", "factor": rng.uniform(1.8, 3.0)},
    }[pick]
    return {
        "dataset_id": f"dim_{tmpl['dataset']}",
        "fault": fault,
        "catalog": {
            "tier": "tier3",
            "freshness_sla_min": tmpl["sla"],
            "completeness_sla": tmpl["completeness"],
            "downstream_critical": False,
            "dependent_jobs": [],
            "ops_note": "",
        },
    }


def _params(
    kind: str, variant: str | None, grey: bool, uid: int, tmpl: dict[str, Any], rng: random.Random
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "kind": kind,
        "variant": variant,
        "uid": uid,
        "data_seed": rng.randrange(2**32),
        "pipeline_id": f"{tmpl['dataset']}_ingest_{uid:03d}",
        "dataset_id": f"{tmpl['dataset']}_clean",
        "record_base": uid * 1_000_000,
        "n_hist": BASELINE_DAYS,
        "weekend_factor": rng.uniform(0.8, 0.9),
        "growth": None,
        "ops_note": "",
        "base_rate": tmpl["base_rate"],
        "base_mean": tmpl["base_mean"],
    }
    _draw_fault(kind, variant, grey, rng, tmpl, p)
    p["catalog"] = {
        "tier": tmpl["tier"],
        "freshness_sla_min": tmpl["sla"],
        "completeness_sla": tmpl["completeness"],
        "downstream_critical": tmpl["critical"],
        "dependent_jobs": tmpl["jobs"],
        "ops_note": p.pop("ops_note"),
    }
    p["sibling"] = _sibling(rng, tmpl)
    p["as_of"] = f"{date.fromisoformat(p['window_day']) + timedelta(days=1)} 00:30:00"
    return p


def _prepare(p: dict[str, Any]) -> None:
    """Dup faults need the window row count; draw it from a dry run without any fault applied."""
    needs = [
        f
        for f in (p["fault"], (p.get("sibling") or {}).get("fault"))
        if f and f["type"] == "dup" and "n_dup" not in f
    ]
    if not needs:
        return
    dry = dict(p, fault=None, sibling=None)
    ev = simulate(dry, with_sibling=False).events
    d0 = pd.Timestamp(p["window_day"])
    n = int(((ev["event_time"] >= d0) & (ev["event_time"] < d0 + pd.Timedelta(days=1))).sum())
    for f in needs:
        f["n_dup"] = max(1, round(f["r"] / (1 - f["r"]) * (n if f is p["fault"] else max(n * 0.3, 4))))


def _core(p: dict[str, Any]) -> dict[str, str]:
    return {
        "pipeline_id": p["pipeline_id"],
        "dataset_id": p["dataset_id"],
        "window_day": p["window_day"],
        "as_of": p["as_of"],
    }


def _facts_for(p: dict[str, Any]) -> dict[str, Any]:
    return finish_facts(reference_facts(simulate(p, with_sibling=False), _core(p)))


def _accept(kind: str, variant: str | None, fam: str, grey: bool, f: dict[str, Any]) -> bool:
    d = f["derived"]
    rf, near = rules_family(d), near_threshold(d)
    if kind == "insufficient_history":
        return d["expected_rows"] is None and rf == NORMAL
    if kind == "holiday_dip" and variant == "note":
        return rf == "volume_drop" and d["volume_ratio"] < 0.55
    if kind == "holiday_dip":
        return rf == NORMAL and 0.7 <= d["volume_ratio"] <= 1.35 and d["naive_ratio"] < THR["vol"]
    if kind == "weekly_seasonality":
        return rf == NORMAL and d["naive_ratio"] < THR["vol"]
    if kind == "legit_growth":
        return rf == NORMAL and d["naive_ratio"] > THR["naive_high"]
    if kind == "no_anomaly" and variant == "clean":
        return rf == NORMAL and f["alert"] is None
    if kind == "no_anomaly":
        return rf == NORMAL and bool(near)
    extra = set(breached(d)) - {fam} - COUPLED.get(fam, set())
    return not extra and (rf == fam or (grey and fam in near))


def _gold(fam: str, f: dict[str, Any]) -> dict[str, Any]:
    impact = policy_impact(fam, f)
    return {
        "anomaly_family": fam,
        "quality_impact": impact,
        "investigate_now": policy_investigate(fam, impact, f),
    }


def _make_member(
    seed: int, kind: str, variant: str | None, fam0: str | None, uid: int, tmpl: dict[str, Any], gid: str
) -> ScenarioSpec:
    fam = fam0 or ("type_violation" if variant == "type" else "distribution_shift")
    for attempt in range(60):
        rng = random.Random(f"{seed}:{uid}:{attempt}")
        grey = kind in FAULT_KINDS and rng.random() < 0.35
        p = _params(kind, variant, grey, uid, tmpl, rng)
        _prepare(p)
        facts = _facts_for(p)
        if _accept(kind, variant, fam, grey, facts):
            break
    else:  # pragma: no cover - guarded by tests on the shipped seed
        raise RuntimeError(f"E02 uid {uid} ({kind}/{variant}): no consistent draw in 60 attempts")
    d = facts["derived"]
    insufficient = kind == "insufficient_history"
    ambiguous = (fam in near_threshold(d)) if fam != NORMAL else bool(near_threshold(d))
    suff = "insufficient" if insufficient else "ambiguous" if ambiguous else "sufficient"
    tags = []
    if fam == NORMAL and (not insufficient or facts["alert"] is not None):
        tags.append("hard_negative")
    if insufficient:
        tags.append("insufficient_evidence")
    if suff == "ambiguous":
        tags.append("ambiguous")
    if p["sibling"]:
        tags.append("distractor")
    tags.append("sql_identifier")
    gold = _gold(fam, facts)
    spec = ScenarioSpec(
        scenario_id="",
        experiment=CASE,
        group_id=gid,
        facts={
            **_core(p),
            "_internal": {
                "params": p,
                "kind": kind,
                "variant": variant,
                "injected_family": fam,
                "policy": POLICY_ID,
            },
        },
        gold=gold,
        difficulty_tags=tags,
        evidence_sufficiency=suff,
        label_source=f"injected_perturbation+{POLICY_ID}",
        seed=seed,
    )
    rules_baseline(spec, facts)
    spec.facts["_internal"]["threshold_decidable"] = facts["_internal"]["threshold_decidable"]
    return spec


def generate_specs(seed: int) -> list[ScenarioSpec]:
    rng = random.Random(seed + SEED_OFFSET)
    specs: list[ScenarioSpec] = []
    uid = gnum = 0
    for kind, n, fam in KIND_PLAN:
        variants = list(VARIANTS.get(kind, [None] * n))
        rng.shuffle(variants)
        left = n
        sizes = []
        while left > 0:
            sizes.append(min(left, 3 if len(sizes) % 2 == 0 else 2))
            left -= sizes[-1]
        vi = 0
        for size in sizes:
            gnum += 1
            tmpl = _template(rng)
            for _ in range(size):
                uid += 1
                specs.append(_make_member(seed, kind, variants[vi], fam, uid, tmpl, f"E02-G{gnum:03d}"))
                vi += 1
    rng.shuffle(specs)
    for i, s in enumerate(specs, start=1):
        s.scenario_id = scenario_id(CASE, i)
    return specs


# --------------------------------------------------------------------------------------------------
# DB seed, backend, oracle
# --------------------------------------------------------------------------------------------------
def db_seed(specs: list[ScenarioSpec], seed: int) -> dict[str, pd.DataFrame]:
    sims = [simulate(s.facts["_internal"]["params"]) for s in specs]
    events = pd.concat([s.events for s in sims], ignore_index=True)
    raw = pd.concat([s.raw for s in sims], ignore_index=True)
    for df in (events, raw):
        df["event_time"] = df["event_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
        df["customer_id"] = df["customer_id"].astype(object).where(df["customer_id"].notna(), None)
    cal = pd.concat([s.calendar for s in sims], ignore_index=True)
    cal["day"] = cal["day"].astype(str)
    return {
        "pipeline_events": events,
        "pipeline_events_raw": raw,
        "dataset_catalog": pd.concat([s.catalog for s in sims], ignore_index=True),
        "business_calendar": cal,
    }


class E02Backend(ClickHouseBackend):
    def retrieve(self, spec: ScenarioSpec) -> Evidence:
        ev = super().retrieve(spec)
        finish_facts(ev.facts)
        return ev


def make_backend(settings: Settings) -> E02Backend:
    return E02Backend("e02")


def expected_evidence(spec: ScenarioSpec) -> dict[str, Any]:
    p = spec.facts["_internal"]["params"]
    facts = reference_facts(simulate(p, with_sibling=False), _core(p))
    return finish_facts(facts)


_TOL = 1e-6


def _diff(path: str, a: Any, b: Any, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k == "_internal":
                continue
            if k not in a or k not in b:
                out.append(f"{path}.{k}: missing on one side")
            else:
                _diff(f"{path}.{k}", a[k], b[k], out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                _diff(f"{path}[{i}]", x, y, out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=_TOL, abs_tol=_TOL):
            out.append(f"{path}: db {a} != reference {b}")
    elif a != b:
        out.append(f"{path}: db {a!r} != reference {b!r}")


def oracle_check(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    """DB aggregates vs the pandas reference, then gold vs the injected family and policy."""
    problems: list[str] = []
    ref = expected_evidence(spec)
    _diff("facts", facts, ref, problems)
    if problems:
        return problems
    inj = spec.facts["_internal"]["injected_family"]
    if spec.gold["anomaly_family"] != inj:
        problems.append(f"gold family {spec.gold['anomaly_family']} != injected {inj}")
    expect = _gold(inj, facts)
    for qid, val in expect.items():
        if spec.gold[qid] != val:
            problems.append(f"gold {qid}={spec.gold[qid]!r} but policy on DB facts gives {val!r}")
    d = facts["derived"]
    if (
        inj != NORMAL
        and spec.evidence_sufficiency != "insufficient"
        and inj not in set(breached(d)) | near_threshold(d)
    ):
        problems.append(f"injected {inj} is neither breached nor near its threshold")
    if (
        inj == NORMAL
        and spec.evidence_sufficiency == "sufficient"
        and not needs_free_text(facts)
        and breached(d)
    ):
        problems.append(f"normal scenario breaches thresholds: {breached(d)}")
    return problems


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------
def _f(x: float | None, nd: int) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _cnt(x: int | None) -> str:
    return "n/a" if x is None else f"{x}/{N_BATCH}"


def machine_lines(facts: dict[str, Any]) -> dict[str, str]:
    w, d, b, cat = facts["window"], facts["derived"], facts["baseline"], facts["catalog"]
    jobs = ",".join(cat["dependent_jobs"]) or "none"
    return {
        "window": f"{facts['pipeline_id']}/{facts['dataset_id']} window={facts['window_day']} as_of={facts['as_of'].replace(' ', 'T')}",
        "volume": (
            f"rows={w['n_rows']} expected={_f(d['expected_rows'], 1)} ratio={_f(d['volume_ratio'], 2)} "
            f"vs_mean={_f(d['naive_ratio'], 2)} lowb={_cnt(d['batches_low_volume'])} seen={_cnt(d['batches_seen'])}"
        ),
        "quality": (
            f"null_rate={d['null_rate']:.3f} nullb={_cnt(d['batches_null'])} dup_rate={d['dup_rate']:.3f} "
            f"dupb={_cnt(d['batches_dup'])} type_mismatch={w['mismatch_count']}/{w['raw_rows']} mismb={_cnt(d['batches_mismatch'])}"
        ),
        "values": (
            f"amount_min={w['amount_min']:.2f} amount_max={w['amount_max']:.2f} amount_mean={w['amount_mean']:.2f} "
            f"baseline_mean={_f(b['amount_mean'], 2)} shift_sd={_f(d['mean_shift_sd'], 2)} shiftb={_cnt(d['batches_shift'])}"
        ),
        "freshness": (
            f"last_event={w['last_event'].replace(' ', 'T')} lag_min={d['lag_min']} "
            f"sla_min={cat['freshness_sla_min']} missingb={_cnt(d['batches_missing'])}"
        ),
        "history": f"days={b['history_days']} same_weekday={b['same_weekday_days']} basis={d['expected_basis']}",
        "catalog": (
            f"tier={cat['tier']} jobs={jobs} critical_consumer={'yes' if cat['downstream_critical'] else 'no'} "
            f"completeness_min={cat['completeness_sla']:.2f}"
        ),
    }


def _alert_machine(a: dict[str, Any]) -> str:
    return f"rule={a['rule']} observed={round(a['observed'], 3):g} threshold={a['threshold']}"


_TEXT = {
    "en": {
        "alert": "Alert fired:",
        "no_alert": "No alert rule breached in this window.",
        "cal_normal": "Calendar: normal business day.",
        "cal_holiday": "Calendar: public holiday, expected volume factor x{f:.2f}.",
        "regional_holiday": "Ops note: regional festival today, lower traffic expected.",
        "campaign": "Ops note: marketing campaign launched this week.",
        "monitor": "Static monitor compares today with mean_14d; expected rows use the same weekday.",
    },
    "th": {
        "alert": "แจ้งเตือนที่ทำงาน:",
        "no_alert": "ไม่มีกฎแจ้งเตือนใดถูกละเมิดในช่วงเวลานี้",
        "cal_normal": "ปฏิทิน: วันทำการปกติ",
        "cal_holiday": "ปฏิทิน: วันหยุดนักขัตฤกษ์ ปริมาณที่คาดไว้ลดลงเป็น x{f:.2f}",
        "regional_holiday": "บันทึกจากทีมปฏิบัติการ: วันนี้เป็นเทศกาลท้องถิ่น คาดว่าทราฟฟิกลดลง",
        "campaign": "บันทึกจากทีมปฏิบัติการ: เริ่มแคมเปญการตลาดในสัปดาห์นี้",
        "monitor": "monitor แบบ static เทียบวันนี้กับ mean_14d ส่วนจำนวนแถวที่คาดไว้ใช้วันในสัปดาห์เดียวกัน",
    },
}


def render_state(spec: ScenarioSpec, facts: dict[str, Any], lang: str, context_level: str) -> dict[str, Any]:
    t = _TEXT[lang]
    lines = machine_lines(facts)
    a = facts["alert"]
    cal, cat = facts["calendar"], facts["catalog"]
    sentences = [t["cal_holiday"].format(f=cal["expected_factor"]) if cal else t["cal_normal"]]
    if cat["ops_note"]:
        sentences.append(t[cat["ops_note"]])
    state: dict[str, Any] = {"alert": f"{t['alert']} {_alert_machine(a)}" if a else t["no_alert"]}
    state.update(
        {k: lines[k] for k in ("window", "volume", "quality", "values", "freshness", "history", "catalog")}
    )
    state["context"] = " ".join(sentences)
    if context_level == "natural_long":
        state["batch_rows"] = " ".join(
            f"b{x['batch_id'].rsplit('-b', 1)[1]}={x['n_rows']}" for x in facts["batches"]
        )
        state["history_rows"] = " ".join(f"{day}={n}" for day, n in facts["history_rows"])
        state["monitor_notes"] = t["monitor"]
    return state


def protected_tokens(spec: ScenarioSpec, facts: dict[str, Any]) -> list[str]:
    lines = machine_lines(facts)
    toks = [facts["pipeline_id"], facts["dataset_id"], facts["window_day"]] + list(lines.values())
    if facts["alert"]:
        toks.append(_alert_machine(facts["alert"]))
    toks.extend(facts["catalog"]["dependent_jobs"])
    return list(dict.fromkeys(toks))
