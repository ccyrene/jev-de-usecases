"""Cost per 1,000 decisions (PLAN 11.3). Prices are per MILLION input tokens."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

HOURS_PER_MONTH = 730.0


def remote_cost_usd(billed_input_tokens: float, usd_per_million_input_tokens: float) -> float:
    """billed_input_tokens / 1e6 * price_per_million (never per thousand)."""
    return billed_input_tokens / 1e6 * usd_per_million_input_tokens


def local_cost_per_1000(hourly_cost_usd: float, sustained_successful_decisions_per_hour: float) -> float:
    """hourly_cost / (sustained successful decisions per hour / 1000)."""
    if sustained_successful_decisions_per_hour <= 0:
        return float("inf")
    return hourly_cost_usd / (sustained_successful_decisions_per_hour / 1000.0)


@dataclass(frozen=True)
class CostSummary:
    total_cost_usd: float
    n_successful_questions: int
    n_successful_scenarios: int
    usd_per_1000_questions: float | None
    usd_per_1000_scenario_decisions: float | None
    is_estimate: bool  # True when token counts were not measured/billed
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _per_1000(total: float, n: int) -> float | None:
    return total / n * 1000.0 if n > 0 else None


def remote_cost_summary(
    billed_input_tokens: float,
    usd_per_million_input_tokens: float,
    n_successful_questions: int,
    n_successful_scenarios: int,
    tokens_measured: bool = True,
) -> CostSummary:
    """Spend over the whole run (failed/retried calls included in tokens) divided by successes.
    ``tokens_measured=False`` marks the result as an estimate."""
    total = remote_cost_usd(billed_input_tokens, usd_per_million_input_tokens)
    return CostSummary(total, n_successful_questions, n_successful_scenarios,
                       _per_1000(total, n_successful_questions), _per_1000(total, n_successful_scenarios),
                       not tokens_measured,
                       "billed_tokens" if tokens_measured else "estimated_tokens")


def local_cost_summary(
    hourly_cost_usd: float,
    successful_questions_per_hour: float,
    questions_per_scenario: float,
    energy_usd_per_hour: float = 0.0,
    utilization: float = 1.0,
    measured: bool = True,
) -> CostSummary:
    """Local host cost. ``utilization`` < 1 scales sustained throughput down to model idle
    headroom on an always-on instance. ``total_cost_usd`` is one hour; counts are per hour. Scenario decisions = questions / questions_per_scenario."""
    hourly = hourly_cost_usd + energy_usd_per_hour
    q_per_h = successful_questions_per_hour * utilization
    return CostSummary(hourly, int(q_per_h), int(q_per_h / questions_per_scenario),
                       local_cost_per_1000(hourly, q_per_h) if q_per_h > 0 else None,
                       local_cost_per_1000(hourly, q_per_h / questions_per_scenario) if q_per_h > 0 else None,
                       not measured, "sustained_throughput" if measured else "estimated_throughput")


def monthly_projection(usd_per_1000: float, decisions_per_month: float, fixed_monthly_usd: float = 0.0) -> float:
    """Variable cost for a monthly volume plus any fixed monthly cost."""
    return usd_per_1000 * decisions_per_month / 1000.0 + fixed_monthly_usd


def always_on_monthly_usd(hourly_cost_usd: float, energy_usd_per_hour: float = 0.0, hours: float = HOURS_PER_MONTH) -> float:
    return (hourly_cost_usd + energy_usd_per_hour) * hours
